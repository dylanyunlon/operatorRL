"""
ComponentConf — Component configuration protocol (Apollo proto parity).
=========================================================================

Apollo reference: ``cyber/proto/component_conf.proto``

In Apollo, component configuration is defined in protobuf.
In Python, we use dataclasses with the same field names to maintain
structural parity.

Claude27: New file.
Location: lolbot-HyperAI/modules/common/proto/component_conf.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class TimerComponentConf:
    """Configuration for a TimerComponent.

    Apollo equivalent: ``TimerComponentConfig`` in component_conf.proto.

    Fields map 1:1 to Apollo's protobuf definition:
        - name: component name (required)
        - interval: timer interval in ms (required)
        - config_file_path: path to component-specific config
        - flag_file_path: path to gflags file
    """
    name: str = ""
    interval: int = 100  # milliseconds
    config_file_path: str = ""
    flag_file_path: str = ""


@dataclass
class DAGModuleConf:
    """Configuration for a DAG module (component + its config).

    Apollo equivalent: ``ModuleConfig`` in dag_conf.proto.
    """
    module_library: str = ""  # Apollo: .so path; ours: Python module path
    timer_components: List[TimerComponentConf] = field(default_factory=list)
    components: List[ComponentConf] = field(default_factory=list)


@dataclass
class ComponentConf:
    """Configuration for a message-triggered component.

    Apollo equivalent: ``ComponentConfig`` in component_conf.proto.
    """
    name: str = ""
    config_file_path: str = ""
    flag_file_path: str = ""
    readers: List[ReaderOption] = field(default_factory=list)


@dataclass
class ReaderOption:
    """Reader configuration within a component.

    Apollo equivalent: ``ReaderOption`` in component_conf.proto.
    """
    channel: str = ""
    pending_queue_size: int = 10
    qos_profile: str = ""


@dataclass
class DAGConf:
    """Top-level DAG configuration.

    Apollo equivalent: ``DAGConfig`` in dag_conf.proto.

    A DAG config file lists all modules and their components
    that should be loaded by mainboard.
    """
    module_config: List[DAGModuleConf] = field(default_factory=list)

    def component_count(self) -> int:
        """Total number of components across all modules."""
        count = 0
        for mod in self.module_config:
            count += len(mod.timer_components)
            count += len(mod.components)
        return count

    def component_names(self) -> List[str]:
        """List all component names."""
        names: List[str] = []
        for mod in self.module_config:
            for tc in mod.timer_components:
                names.append(tc.name)
            for c in mod.components:
                names.append(c.name)
        return names


def parse_dag_yaml(path: str) -> DAGConf:
    """Parse a YAML DAG config file into a DAGConf.

    This is our Python equivalent of Apollo's protobuf DAG parsing.
    Compatible with the YAML format used by conf/dag/*.yaml files.
    """
    import yaml
    from pathlib import Path

    dag_path = Path(path)
    if not dag_path.exists():
        raise FileNotFoundError(f"DAG config not found: {path}")

    with open(dag_path) as f:
        raw = yaml.safe_load(f)

    if raw is None:
        return DAGConf()

    dag = DAGConf()

    # Parse components list (our YAML format)
    components_raw = raw.get("components", [])
    if components_raw:
        mod = DAGModuleConf()
        for comp_raw in components_raw:
            tc = TimerComponentConf(
                name=comp_raw.get("name", ""),
                interval=comp_raw.get("interval_ms", 100),
                config_file_path=comp_raw.get("config_file", ""),
            )
            mod.timer_components.append(tc)
        dag.module_config.append(mod)

    return dag
