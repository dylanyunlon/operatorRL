"""
AbstractVehicleFactory — Apollo-style data source factory interface.
=====================================================================

Apollo reference: ``modules/canbus/vehicle/abstract_vehicle_factory.h``

In Apollo, AbstractVehicleFactory defines the interface that all
vehicle implementations (Lincoln, GE3, etc.) must implement. The
canbus_component.cc creates a factory via ClassLoader and delegates
all CAN bus operations to it.

In lolbot-HyperAI, the "vehicle" is the LoL game client. We have
multiple data sources (LCU HTTP, Fiddler MCP, mock, replay) that
all implement this same interface.

Claude27: New file. Provides the abstract base that LCUClient,
FiddlerClient, and SimulatedReplay already implement via
DataSourceFactory (Claude16). This formalizes the interface
following Apollo's pattern.

Location: lolbot-HyperAI/modules/canbus/vehicle/abstract_vehicle_factory.py

NOTE: This does NOT replace DataSourceFactory (Claude16). It sits
*above* it as the Apollo-aligned abstract base. DataSourceFactory
acts as the ClassLoader equivalent.
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class VehicleStats:
    """Runtime statistics for a vehicle factory instance.

    Apollo equivalent: collected internally by vehicle_controller.
    """
    total_polls: int = 0
    successful_polls: int = 0
    failed_polls: int = 0
    last_success_time: float = 0.0
    last_failure_time: float = 0.0
    total_bytes_received: int = 0
    avg_latency_ms: float = 0.0
    _latency_sum: float = 0.0
    _latency_count: int = 0

    def record_poll(self, success: bool, latency_ms: float = 0.0,
                    bytes_received: int = 0) -> None:
        """Record a single poll result."""
        self.total_polls += 1
        if success:
            self.successful_polls += 1
            self.last_success_time = time.time()
            self.total_bytes_received += bytes_received
        else:
            self.failed_polls += 1
            self.last_failure_time = time.time()
        if latency_ms > 0:
            self._latency_sum += latency_ms
            self._latency_count += 1
            self.avg_latency_ms = self._latency_sum / self._latency_count

    def to_dict(self) -> dict:
        return {
            "total_polls": self.total_polls,
            "successful_polls": self.successful_polls,
            "failed_polls": self.failed_polls,
            "last_success_time": self.last_success_time,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "total_bytes_received": self.total_bytes_received,
        }


class AbstractVehicleFactory(abc.ABC):
    """Abstract interface for game data acquisition.

    Apollo equivalent: ``AbstractVehicleFactory``

    Subclasses:
        - LCUClient: polls localhost:2999 HTTP
        - FiddlerMCPClient: reads network captures via MCP bridge
        - SimulatedReplay: replays recorded JSONL sessions
        - MockDataSource: generates synthetic data for testing

    Lifecycle:
        1. ``Init(config)`` — configure the data source
        2. ``Start()`` — begin data acquisition (connect, open file, etc.)
        3. ``publish_chassis()`` — called each Proc() tick, returns data
        4. ``Stop()`` — graceful shutdown
    """

    def __init__(self) -> None:
        self._started = False
        self._stats = VehicleStats()

    @property
    def stats(self) -> VehicleStats:
        return self._stats

    @abc.abstractmethod
    def Init(self, config: Dict[str, Any]) -> bool:
        """Initialize the data source with config.

        Apollo equivalent: ``AbstractVehicleFactory::Init(const CanbusConf&)``

        Returns True on success.
        """
        ...

    @abc.abstractmethod
    def Start(self) -> bool:
        """Start data acquisition.

        Apollo equivalent: ``AbstractVehicleFactory::Start()``

        Returns True on success.
        """
        ...

    @abc.abstractmethod
    def Stop(self) -> None:
        """Stop data acquisition and release resources.

        Apollo equivalent: ``AbstractVehicleFactory::Stop()``
        """
        ...

    @abc.abstractmethod
    def publish_chassis(self) -> Optional[Dict[str, Any]]:
        """Poll and return the latest game data.

        Apollo equivalent: ``AbstractVehicleFactory::publish_chassis()``

        Returns:
            The allgamedata dict, or None if no data available.
        """
        ...

    def CheckChassisCommunicationFault(self) -> bool:
        """Check if communication with data source has failed.

        Apollo equivalent: ``AbstractVehicleFactory::CheckChassisCommunicationFault()``

        Default: check if last successful poll is too old.
        """
        if self._stats.last_success_time <= 0:
            return False
        age = time.time() - self._stats.last_success_time
        return age > 10.0  # 10s threshold

    def UpdateHeartbeat(self) -> None:
        """Update internal heartbeat / keep-alive.

        Apollo equivalent: ``AbstractVehicleFactory::UpdateHeartbeat()``

        Default: no-op. Override for sources that need keep-alive.
        """
        pass

    def PublishChassisDetail(self) -> Optional[Dict[str, Any]]:
        """Publish optional detailed data (Fiddler captures, etc.).

        Apollo equivalent: ``vehicle_object_->PublishChassisDetail()``

        Default: returns None (no detail available).
        """
        return None

    @property
    def is_started(self) -> bool:
        return self._started

    def Name(self) -> str:
        """Return the data source name."""
        return type(self).__name__

    def snapshot(self) -> dict:
        return {
            "name": self.Name(),
            "started": self._started,
            "stats": self._stats.to_dict(),
        }
