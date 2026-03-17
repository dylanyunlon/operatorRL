# Copyright (c) Microsoft. All rights reserved.

# type: ignore

import os
import logging
from copy import deepcopy

import ray
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from verl.workers.rollout.vllm_rollout.vllm_async_server import AsyncvLLMServer
from vllm.entrypoints.openai.protocol import ChatCompletionRequest, ErrorResponse

from agentlightning.instrumentation.vllm import ChatCompletionResponsePatched, instrument_vllm

logger = logging.getLogger(__name__)


def _unwrap_ray_remote(cls):
    if hasattr(cls, "__ray_actor_class__"):
        cls = cls.__ray_actor_class__
    return cls


# === M24: 设备检测辅助函数 (命题3: HTTP身体 → Neuron硬件) ===
def _detect_device_type() -> str:
    """
    检测当前运行环境的设备类型。
    
    Returns:
        "neuron": AWS Trainium/Inferentia (检测到 /dev/neuron* 或 torch_neuronx)
        "cuda": NVIDIA GPU
        "cpu": 无加速器
    """
    # 检测 Neuron Runtime（AWS Trainium/Inferentia）
    if os.path.exists("/dev/neuron0") or os.environ.get("NEURON_RT_VISIBLE_CORES"):
        try:
            import torch_neuronx  # noqa: F401
            return "neuron"
        except ImportError:
            # /dev/neuron 存在但 torch_neuronx 未安装
            logger.warning("Neuron device detected but torch_neuronx not installed")
            return "neuron"
    
    # 检测 CUDA
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    
    return "cpu"


@ray.remote(num_cpus=1)
class PatchedvLLMServer(_unwrap_ray_remote(AsyncvLLMServer)):

    def __init__(self, *args, **kwargs):
        instrument_vllm()
        
        # === M24: 设备类型检测和配置 ===
        self._device_type = _detect_device_type()
        logger.info(f"PatchedvLLMServer initialized with device type: {self._device_type}")
        
        # 如果是 Neuron 设备，设置相关环境变量
        if self._device_type == "neuron":
            os.environ.setdefault("XLA_USE_BF16", "1")
            os.environ.setdefault("NEURON_CC_FLAGS", "--auto-cast=matmul --auto-cast-type=bf16")
            logger.info("Neuron device detected, XLA_USE_BF16 enabled")
        
        super().__init__(*args, **kwargs)

        self.config = deepcopy(self.config)
        self.config.rollout.multi_turn.tool_config_path = "/dev/null"

    async def chat_completion(self, raw_request: Request):
        """OpenAI-compatible HTTP endpoint.

        API reference: [OpenAI-compatible server documentation](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html)
        """
        request_json = await raw_request.json()
        request = ChatCompletionRequest(**request_json)
        generator = await self.openai_serving_chat.create_chat_completion(request, raw_request)

        if isinstance(generator, ErrorResponse):
            return JSONResponse(content=generator.model_dump(), status_code=generator.code)
        if request.stream:
            return StreamingResponse(content=generator, media_type="text/event-stream")
        else:
            return JSONResponse(content=generator.model_dump())
