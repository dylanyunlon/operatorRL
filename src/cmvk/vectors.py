"""
Verification Vectors

Data structures for claim and observation vectors used in verification.
"""

from dataclasses import dataclass
from typing import List, Optional
import numpy as np


@dataclass
class ClaimVector:
    """
    Vector representation of a carbon credit claim.
    
    Contains the claimed values from the Project Design Document
    that will be verified against satellite observations.
    """
    project_id: str
    ndvi: float  # Claimed NDVI (vegetation index)
    carbon_stock: float  # Claimed carbon stock (tonnes/hectare)
    year: int
    polygon: Optional[str] = None
    
    def to_array(self) -> np.ndarray:
        """Convert to numpy array for mathematical operations."""
        return np.array([self.ndvi, self.carbon_stock / 1000.0])  # Normalize carbon
    
    def __repr__(self) -> str:
        return f"ClaimVector(project={self.project_id}, ndvi={self.ndvi}, carbon={self.carbon_stock})"


@dataclass
class ObservationVector:
    """
    Vector representation of satellite observations.
    
    Contains the actual measured values from satellite imagery
    that will be compared against claims.
    """
    project_id: str
    ndvi_mean: float  # Observed mean NDVI
    ndvi_std: float  # Standard deviation
    cloud_cover: float  # Fraction of cloud cover
    vegetation_coverage: float  # Fraction of area with vegetation
    deforestation_indicator: float  # Fraction showing signs of deforestation
    
    @property
    def estimated_carbon_stock(self) -> float:
        """
        Estimate carbon stock from NDVI.
        
        This is a simplified model. In production, use biomass models
        calibrated for the specific forest type.
        
        Tropical forest relationship (approximate):
        Carbon = 250 * NDVI^2 (tonnes/hectare)
        """
        return 250 * (self.ndvi_mean ** 2)
    
    def to_array(self) -> np.ndarray:
        """Convert to numpy array for mathematical operations."""
        return np.array([
            self.ndvi_mean,
            self.estimated_carbon_stock / 1000.0  # Normalize carbon
        ])
    
    @property
    def confidence(self) -> float:
        """
        Calculate observation confidence score.
        
        Lower cloud cover and lower variance = higher confidence.
        """
        cloud_penalty = 1.0 - self.cloud_cover
        variance_penalty = 1.0 - min(self.ndvi_std / 0.3, 1.0)
        return cloud_penalty * variance_penalty
    
    def __repr__(self) -> str:
        return (
            f"ObservationVector(project={self.project_id}, "
            f"ndvi={self.ndvi_mean:.3f}, "
            f"est_carbon={self.estimated_carbon_stock:.1f})"
        )
