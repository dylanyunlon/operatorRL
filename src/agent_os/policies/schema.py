"""
Declarative policy schema for Agent-OS governance.

Defines PolicyDocument and related models that represent policies as
pure data (JSON/YAML) rather than coupling structure with evaluation logic.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class PolicyOperator(str, Enum):
    """Comparison operators for policy conditions."""

    EQ = "eq"
    NE = "ne"
    GT = "gt"
    LT = "lt"
    GTE = "gte"
    LTE = "lte"
    IN = "in"
    MATCHES = "matches"
    CONTAINS = "contains"


class PolicyAction(str, Enum):
    """Actions a policy rule can prescribe."""

    ALLOW = "allow"
    DENY = "deny"
    AUDIT = "audit"
    BLOCK = "block"


class PolicyCondition(BaseModel):
    """A single condition evaluated against execution context."""

    field: str = Field(..., description="Context field, e.g. 'tool_name', 'token_count'")
    operator: PolicyOperator = Field(..., description="Comparison operator")
    value: Any = Field(..., description="Value to compare against")


# === M33: 成长阶段门控 (命题7: 小学到大学) ===
class MaturityGate(BaseModel):
    """定义在特定maturity_level下的policy行为变化。
    
    例如: maturity_level >= 4 时，某些non-critical规则可以放宽。
    成长阶段: 0=婴儿期, 1=幼儿期, 2=小学, 3=初中, 4=高中, 5=大学, 6=研究生
    """
    min_level: int = Field(default=0, ge=0, le=6, description="最小成长阶段级别 (0-6)")
    max_level: int = Field(default=6, ge=0, le=6, description="最大成长阶段级别 (0-6)")
    action_override: PolicyAction | None = Field(
        default=None, 
        description="在此阶段范围内覆盖默认action"
    )
    skip_rule: bool = Field(
        default=False, 
        description="在此阶段范围内跳过此规则"
    )
    message_override: str = Field(
        default="", 
        description="在此阶段范围内使用的替代消息"
    )


class PolicyRule(BaseModel):
    """A single governance rule within a policy document."""

    name: str
    condition: PolicyCondition
    action: PolicyAction
    priority: int = Field(default=0, description="Higher priority rules are evaluated first")
    message: str = Field(default="", description="Human-readable explanation")
    # === M33: 成长阶段门控 ===
    maturity_gates: list[MaturityGate] = Field(
        default_factory=list,
        description="按成长阶段调整规则行为的门控列表"
    )
    is_critical: bool = Field(
        default=False, 
        description="关键规则不受maturity_level影响，始终严格执行"
    )


class PolicyDefaults(BaseModel):
    """Default settings applied when no rule matches."""

    action: PolicyAction = PolicyAction.ALLOW
    max_tokens: int = 4096
    max_tool_calls: int = 10
    confidence_threshold: float = 0.8


class PolicyDocument(BaseModel):
    """Top-level declarative policy document."""

    version: str = "1.0"
    name: str = "unnamed"
    description: str = ""
    rules: list[PolicyRule] = Field(default_factory=list)
    defaults: PolicyDefaults = Field(default_factory=PolicyDefaults)

    @classmethod
    def from_yaml(cls, path: str | Path) -> PolicyDocument:
        """Load a PolicyDocument from a YAML file."""
        try:
            import yaml
        except ImportError as exc:
            raise ImportError("pyyaml is required: pip install pyyaml") from exc

        path = Path(path)
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data)

    def to_yaml(self, path: str | Path) -> None:
        """Serialize this PolicyDocument to a YAML file."""
        try:
            import yaml
        except ImportError as exc:
            raise ImportError("pyyaml is required: pip install pyyaml") from exc

        path = Path(path)
        with open(path, "w") as f:
            yaml.dump(self.model_dump(mode="json"), f, default_flow_style=False, sort_keys=False)

    @classmethod
    def from_json(cls, path: str | Path) -> PolicyDocument:
        """Load a PolicyDocument from a JSON file."""
        path = Path(path)
        with open(path) as f:
            data = json.load(f)
        return cls.model_validate(data)

    def to_json(self, path: str | Path) -> None:
        """Serialize this PolicyDocument to a JSON file."""
        path = Path(path)
        with open(path, "w") as f:
            json.dump(self.model_dump(mode="json"), f, indent=2)
