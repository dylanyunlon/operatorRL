"""
Deployment Manifest — Declarative deployment specification.

Defines what models, configs, and resources are needed for a deployment.
Supports validation, diffing between manifests, and serialization.

Location: agentlightning/deployment/deployment_manifest.py

Reference (拿来主义):
  查看 agentos/governance/model_versioner.py 上现有 ModelVersioner 的
  版本元信息管理方式, 理解其模式, 特别是 version→weights→saved_at
  的结构化存储如何与操作(save/load/diff)分离。
  从 agentos/governance/model_versioner.py 这个好例子开始 — 它的
  diff方法展示了两个版本间的差异比较。
  遵循该模式实现 DeploymentManifest, 让所有部署操作都基于声明式清单,
  并能通过 diff 精确识别两次部署之间的变更.
"""

from __future__ import annotations

import copy
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.deployment.deployment_manifest.v1"


class DeploymentManifest:
    """Declarative deployment specification.

    Attributes:
        name: Deployment name.
        version: Manifest version.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self, name: str, version: str = "1.0") -> None:
        self.name = name
        self.version = version
        self.created_at = time.time()
        self._models: Dict[str, Dict[str, Any]] = {}
        self._configs: Dict[str, Any] = {}
        self._resources: Dict[str, Any] = {}
        self._dependencies: List[str] = []
        self._metadata: Dict[str, Any] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def add_model(
        self, name: str, version: str,
        game: str = "any", config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._models[name] = {
            "version": version, "game": game,
            "config": config or {},
        }

    def remove_model(self, name: str) -> bool:
        if name in self._models:
            del self._models[name]
            return True
        return False

    def set_config(self, key: str, value: Any) -> None:
        self._configs[key] = value

    def get_config(self, key: str, default: Any = None) -> Any:
        return self._configs.get(key, default)

    def set_resource(self, key: str, value: Any) -> None:
        self._resources[key] = value

    def add_dependency(self, dep: str) -> None:
        if dep not in self._dependencies:
            self._dependencies.append(dep)

    def set_metadata(self, key: str, value: Any) -> None:
        self._metadata[key] = value

    def validate(self) -> Dict[str, Any]:
        """Validate the manifest.

        Returns:
            Dict with valid bool and any issues.
        """
        issues: List[str] = []
        if not self._models:
            issues.append("No models specified")
        for name, spec in self._models.items():
            if not spec.get("version"):
                issues.append(f"Model '{name}' missing version")
        return {"valid": len(issues) == 0, "issues": issues}

    def diff(self, other: "DeploymentManifest") -> Dict[str, Any]:
        """Compute diff between two manifests.

        Args:
            other: Other manifest to compare against.

        Returns:
            Dict describing changes.
        """
        changes: Dict[str, Any] = {"models": {}, "configs": {}, "resources": {}}

        all_model_keys = set(self._models.keys()) | set(other._models.keys())
        for k in all_model_keys:
            a = self._models.get(k)
            b = other._models.get(k)
            if a is None:
                changes["models"][k] = {"change": "removed"}
            elif b is None:
                changes["models"][k] = {"change": "added", "spec": a}
            elif a != b:
                changes["models"][k] = {"change": "modified", "old": b, "new": a}

        all_config_keys = set(self._configs.keys()) | set(other._configs.keys())
        for k in all_config_keys:
            a = self._configs.get(k)
            b = other._configs.get(k)
            if a != b:
                changes["configs"][k] = {"old": b, "new": a}

        return changes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "version": self.version,
            "created_at": self.created_at,
            "models": copy.deepcopy(self._models),
            "configs": copy.deepcopy(self._configs),
            "resources": copy.deepcopy(self._resources),
            "dependencies": list(self._dependencies),
            "metadata": copy.deepcopy(self._metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeploymentManifest":
        m = cls(name=data["name"], version=data.get("version", "1.0"))
        m.created_at = data.get("created_at", time.time())
        m._models = data.get("models", {})
        m._configs = data.get("configs", {})
        m._resources = data.get("resources", {})
        m._dependencies = data.get("dependencies", [])
        m._metadata = data.get("metadata", {})
        return m

    def model_count(self) -> int:
        return len(self._models)

    def list_models(self) -> List[Dict[str, Any]]:
        return [{"name": k, **v} for k, v in self._models.items()]
