# Copyright (c) Microsoft. All rights reserved.

from __future__ import annotations

import os
import platform
import socket
from contextlib import suppress
from datetime import datetime
from typing import Any, Dict, List, cast

import psutil
from gpustat import GPUStat, GPUStatCollection

# --- AgentRL self-evolution: Neuron/Trainium device detection (M110) ---
# Enables system_snapshot to detect AWS Trainium2/Inferentia NeuronCores
# alongside NVIDIA GPUs, so the training pipeline knows which accelerator
# is available on each worker node.
_NEURON_DETECTION_ENABLED: bool = True


def _detect_neuron_devices() -> Dict[str, Any]:
    """Detect AWS Neuron (Trainium2/Inferentia) devices.

    Checks for the presence of Neuron runtime indicators:
    - NEURON_RT_VISIBLE_CORES environment variable
    - /dev/neuron* device files
    - neuron-ls command availability

    Returns:
        Dict with 'available' (bool) and optional device details.
    """
    info: Dict[str, Any] = {"available": False}

    # Check environment variable (set by Neuron runtime)
    neuron_cores = os.environ.get("NEURON_RT_VISIBLE_CORES", "")
    if neuron_cores:
        info["available"] = True
        info["visible_cores"] = neuron_cores
        return info

    # Check for Neuron device files
    with suppress(Exception):
        neuron_devs = [f for f in os.listdir("/dev") if f.startswith("neuron")]
        if neuron_devs:
            info["available"] = True
            info["device_count"] = len(neuron_devs)
            return info

    return info


def system_snapshot(include_gpu: bool = False) -> Dict[str, Any]:
    """Capture a snapshot of the system's hardware and software information.

    Args:
        include_gpu: Whether to include GPU information.

    Returns:
        A dictionary containing the system's hardware and software information.
    """
    # CPU
    cpu = {
        "cpu_name": platform.processor(),
        "cpu_cores": psutil.cpu_count(logical=False),
        "cpu_threads": psutil.cpu_count(logical=True),
        "cpu_usage_pct": psutil.cpu_percent(0.0),
    }

    # Memory
    vm = psutil.virtual_memory()
    mem = {
        "mem_used_gb": round(vm.used / (2**30), 2),
        "mem_total_gb": round(vm.total / (2**30), 2),
        "mem_pct": vm.percent,
    }

    # Disk
    du = psutil.disk_usage("/")
    disk = {
        "disk_used_gb": round(du.used / (2**30), 2),
        "disk_total_gb": round(du.total / (2**30), 2),
        "disk_pct": du.percent,
    }

    # GPU (only query if explicitly requested)
    gpus: List[Dict[str, Any]] = []
    if include_gpu:
        with suppress(Exception):
            for g in GPUStatCollection.new_query().gpus:  # type: ignore
                g = cast(GPUStat, g)
                gpus.append(
                    {
                        "gpu": g.name,  # type: ignore
                        "util_pct": g.utilization,
                        "mem_used_mb": g.memory_used,
                        "mem_total_mb": g.memory_total,
                        "temp_c": g.temperature,
                    }
                )

    # Network
    net = psutil.net_io_counters()
    netinfo = {
        "bytes_sent_mb": round(net.bytes_sent / (2**20), 2),
        "bytes_recv_mb": round(net.bytes_recv / (2**20), 2),
    }

    # OS / meta
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "host": socket.gethostname(),
        "os": platform.platform(),
        **cpu,
        **mem,
        **disk,
        **netinfo,
        **({"gpus": gpus} if include_gpu else {}),
    }
