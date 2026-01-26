"""
Geo Agent (Agent B) - "The Eye"

Satellite interface that fetches imagery and calculates vegetation indices.
Listens for Claims and publishes Observations.
"""

from typing import Any, Dict, List, Optional

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amb import Message, MessageType
from amb.topics import Topics
from atr import SentinelAPITool, NDVICalculatorTool, ToolRegistry
from .base import Agent, MessageBus


class GeoAgent(Agent):
    """
    The Geo Agent - "The Eye"
    
    Role: Satellite interface that:
        - Listens for Claim messages with coordinates
        - Fetches satellite imagery for the specified polygon
        - Calculates NDVI (vegetation index)
        - Publishes Observation objects with actual values
    
    Tooling: sentinel_api, ndvi_calculator
    
    Subscribes to: CLAIMS topic
    Publishes: Observation objects to the OBSERVATIONS topic
    """

    def __init__(
        self,
        agent_id: str,
        bus: MessageBus,
        simulate_deforestation: bool = False
    ):
        """
        Initialize the Geo Agent.
        
        Args:
            agent_id: Unique identifier
            bus: Message bus reference
            simulate_deforestation: If True, generate data showing deforestation
        """
        super().__init__(agent_id, bus, name="geo-agent")
        
        self._simulate_deforestation = simulate_deforestation
        
        # Initialize tools
        self._sentinel_api = SentinelAPITool()
        self._ndvi_calculator = NDVICalculatorTool()
        
        # Register tools
        self._registry = ToolRegistry()
        self._registry.register(self._sentinel_api)
        self._registry.register(self._ndvi_calculator)

    @property
    def subscribed_topics(self) -> List[str]:
        """Subscribe to claims to trigger satellite lookups."""
        return [Topics.CLAIMS]

    def handle_message(self, message: Message) -> None:
        """
        Handle incoming claim messages.
        
        When a claim arrives, fetch satellite data and calculate actual values.
        """
        if message.type == MessageType.CLAIM:
            self._log(f"Received claim for project: {message.payload.get('project_id')}")
            self.process_claim(message.payload, message.correlation_id)

    def process_claim(
        self,
        claim: Dict[str, Any],
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process a claim by fetching satellite data and calculating NDVI.
        
        Args:
            claim: The claim data with polygon and time range
            correlation_id: Optional tracking ID
            
        Returns:
            The observation data
        """
        project_id = claim.get("project_id", "UNKNOWN")
        polygon = claim.get("polygon", "[]")
        year = claim.get("year", 2024)
        
        self._log(f"Fetching satellite data for {project_id}, year {year}")
        
        # Step 1: Fetch satellite imagery
        satellite_result = self._sentinel_api.execute(
            polygon=polygon,
            start_date=f"{year}-01-01",
            end_date=f"{year}-12-31",
        )
        
        if not satellite_result.success:
            self._log(f"Failed to fetch satellite data: {satellite_result.error}", level="ERROR")
            return {"error": satellite_result.error}
        
        cloud_cover = satellite_result.data.get("cloud_cover_percentage", 0) / 100.0
        
        self._log(f"Retrieved {satellite_result.data['product_type']} imagery, "
                  f"cloud cover: {cloud_cover:.1%}")
        
        # Step 2: Calculate NDVI
        ndvi_result = self._ndvi_calculator.execute(
            red_band=satellite_result.data["bands"]["B04_RED"],
            nir_band=satellite_result.data["bands"]["B08_NIR"],
            simulate_deforestation=self._simulate_deforestation,
        )
        
        if not ndvi_result.success:
            self._log(f"Failed to calculate NDVI: {ndvi_result.error}", level="ERROR")
            return {"error": ndvi_result.error}
        
        self._log(f"Calculated NDVI: mean={ndvi_result.data['ndvi_mean']:.3f}, "
                  f"vegetation coverage: {ndvi_result.data['vegetation_coverage']:.1%}")
        
        # Step 3: Build observation object
        observation = self._build_observation(
            project_id=project_id,
            ndvi_data=ndvi_result.data,
            cloud_cover=cloud_cover,
            satellite_provenance=satellite_result.provenance,
            ndvi_provenance=ndvi_result.provenance,
        )
        
        # Step 4: Publish to bus
        self.publish(
            topic=Topics.OBSERVATIONS,
            payload=observation,
            message_type=MessageType.OBSERVATION,
            correlation_id=correlation_id,
        )
        
        return observation

    def _build_observation(
        self,
        project_id: str,
        ndvi_data: Dict[str, Any],
        cloud_cover: float,
        satellite_provenance: Any,
        ndvi_provenance: Any,
    ) -> Dict[str, Any]:
        """
        Build a standardized observation object.
        """
        observation = {
            "project_id": project_id,
            "observed_ndvi_mean": ndvi_data["ndvi_mean"],
            "observed_ndvi_std": ndvi_data["ndvi_std"],
            "observed_ndvi_min": ndvi_data["ndvi_min"],
            "observed_ndvi_max": ndvi_data["ndvi_max"],
            "cloud_cover": cloud_cover,
            "vegetation_coverage": ndvi_data["vegetation_coverage"],
            "deforestation_indicator": ndvi_data["deforestation_indicator"],
            "pixel_count": ndvi_data["pixel_count"],
        }
        
        # Add provenance metadata (the "Cryptographic Oracle" feature)
        if satellite_provenance:
            observation["_satellite_provenance"] = {
                "signature": satellite_provenance.signature,
                "source": satellite_provenance.source,
                "timestamp": satellite_provenance.timestamp.isoformat(),
            }
        
        if ndvi_provenance:
            observation["_ndvi_provenance"] = {
                "signature": ndvi_provenance.signature,
                "source": ndvi_provenance.source,
                "timestamp": ndvi_provenance.timestamp.isoformat(),
            }
        
        return observation

    def get_tools(self) -> List[str]:
        """List available tools."""
        return self._registry.list_tools()

    def set_simulation_mode(self, simulate_deforestation: bool) -> None:
        """
        Set whether to simulate deforestation in NDVI calculations.
        
        Args:
            simulate_deforestation: If True, generate low NDVI values
        """
        self._simulate_deforestation = simulate_deforestation
        mode = "DEFORESTATION" if simulate_deforestation else "HEALTHY"
        self._log(f"Simulation mode set to: {mode}")
