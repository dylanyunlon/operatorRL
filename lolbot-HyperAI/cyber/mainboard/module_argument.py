#!/usr/bin/env python3
"""
cyber/mainboard/module_argument.py — Module Arguments
=======================================================

从 Apollo `cyber/mainboard/module_argument.cc` 这个好例子开始。然后, 遵循
该模式实现一个新的 `ModuleArgument`, 让系统可以解析和管理模块启动参数。

Apollo reference:
    cyber/mainboard/module_argument.cc   — ModuleArgument class
    cyber/mainboard/module_argument.h

位置: lolbot-HyperAI/cyber/mainboard/module_argument.py
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class DAGConfig:
    """Configuration for a single DAG file."""
    module_name: str = ""
    dag_path: str = ""
    process_name: str = ""
    sched_name: str = ""
    
    def __post_init__(self):
        if not self.process_name:
            self.process_name = self.module_name


@dataclass
class ModuleArgument:
    """
    Module startup arguments.
    
    Apollo equivalent: cyber/mainboard/module_argument.cc
    
    Parses command-line arguments for module startup:
    - DAG file paths
    - Process configuration
    - Scheduler selection
    
    Usage::
    
        args = ModuleArgument.from_args()
        
        for dag in args.dag_configs:
            print(f"Module: {dag.module_name}")
            print(f"DAG: {dag.dag_path}")
    """
    
    # Process configuration
    process_name: str = "mainboard"
    process_group: str = "default"
    
    # DAG configurations
    dag_configs: List[DAGConfig] = field(default_factory=list)
    
    # Scheduler configuration
    sched_name: str = "classic"
    
    # Flags
    enable_perf: bool = False
    log_level: str = "INFO"
    
    # Additional options
    options: Dict[str, str] = field(default_factory=dict)
    
    @classmethod
    def from_args(cls, args: Optional[List[str]] = None) -> ModuleArgument:
        """Parse arguments from command line.
        
        Apollo equivalent: ModuleArgument::ParseArgument()
        
        Args:
            args: Command line arguments (default: sys.argv[1:])
        
        Returns:
            Parsed ModuleArgument
        """
        parser = argparse.ArgumentParser(
            description="Apollo-style module launcher",
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        
        parser.add_argument(
            "-d", "--dag",
            action="append",
            dest="dag_files",
            default=[],
            help="DAG file path(s)",
        )
        
        parser.add_argument(
            "-p", "--process",
            dest="process_name",
            default="mainboard",
            help="Process name",
        )
        
        parser.add_argument(
            "-g", "--group",
            dest="process_group",
            default="default",
            help="Process group",
        )
        
        parser.add_argument(
            "-s", "--sched",
            dest="sched_name",
            default="classic",
            choices=["classic", "choreography"],
            help="Scheduler type",
        )
        
        parser.add_argument(
            "--perf",
            action="store_true",
            dest="enable_perf",
            help="Enable performance profiling",
        )
        
        parser.add_argument(
            "-l", "--log-level",
            dest="log_level",
            default="INFO",
            choices=["DEBUG", "INFO", "WARNING", "ERROR"],
            help="Logging level",
        )
        
        parser.add_argument(
            "-o", "--option",
            action="append",
            dest="extra_options",
            default=[],
            help="Extra options in key=value format",
        )
        
        parsed = parser.parse_args(args)
        
        # Parse DAG configs
        dag_configs = []
        for dag_path in parsed.dag_files:
            path = Path(dag_path)
            dag_configs.append(DAGConfig(
                module_name=path.stem,
                dag_path=str(path),
                process_name=parsed.process_name,
                sched_name=parsed.sched_name,
            ))
        
        # Parse extra options
        options = {}
        for opt in parsed.extra_options:
            if '=' in opt:
                key, value = opt.split('=', 1)
                options[key] = value
        
        return cls(
            process_name=parsed.process_name,
            process_group=parsed.process_group,
            dag_configs=dag_configs,
            sched_name=parsed.sched_name,
            enable_perf=parsed.enable_perf,
            log_level=parsed.log_level,
            options=options,
        )
    
    @classmethod
    def from_dict(cls, data: Dict) -> ModuleArgument:
        """Create from dictionary (e.g., from config file)."""
        dag_configs = [
            DAGConfig(**d) if isinstance(d, dict) else d
            for d in data.get("dag_configs", [])
        ]
        
        return cls(
            process_name=data.get("process_name", "mainboard"),
            process_group=data.get("process_group", "default"),
            dag_configs=dag_configs,
            sched_name=data.get("sched_name", "classic"),
            enable_perf=data.get("enable_perf", False),
            log_level=data.get("log_level", "INFO"),
            options=data.get("options", {}),
        )
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "process_name": self.process_name,
            "process_group": self.process_group,
            "dag_configs": [
                {
                    "module_name": d.module_name,
                    "dag_path": d.dag_path,
                    "process_name": d.process_name,
                    "sched_name": d.sched_name,
                }
                for d in self.dag_configs
            ],
            "sched_name": self.sched_name,
            "enable_perf": self.enable_perf,
            "log_level": self.log_level,
            "options": self.options,
        }
    
    def get_option(self, key: str, default: str = "") -> str:
        """Get an option value."""
        return self.options.get(key, default)
    
    def has_option(self, key: str) -> bool:
        """Check if an option exists."""
        return key in self.options
