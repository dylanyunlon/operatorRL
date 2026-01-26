"""
Verification Kernel

Mathematical verification engine for comparing claims against observations.
This is the core "decision engine" that uses deterministic math, not LLM inference.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional
import numpy as np

from .vectors import ClaimVector, ObservationVector


class DriftMetric(Enum):
    """
    Available metrics for calculating drift between claim and observation.
    """
    EUCLIDEAN = "euclidean"  # Standard distance
    COSINE = "cosine"  # Directional similarity
    MANHATTAN = "manhattan"  # Absolute differences
    RELATIVE = "relative"  # Percentage difference


class VerificationStatus(Enum):
    """
    Verification outcome status.
    """
    VERIFIED = "VERIFIED"  # Claim matches observation within tolerance
    FLAGGED = "FLAGGED"  # Minor discrepancy, requires review
    FRAUD = "FRAUD"  # Significant discrepancy, likely fraudulent claim


@dataclass
class VerificationResult:
    """
    Result of a verification calculation.
    
    Contains all the mathematical details for audit trail.
    """
    project_id: str
    status: VerificationStatus
    drift_score: float
    threshold: float
    confidence: float
    claim_vector: np.ndarray
    observation_vector: np.ndarray
    metric: DriftMetric
    timestamp: datetime
    details: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for reporting."""
        return {
            "project_id": self.project_id,
            "status": self.status.value,
            "drift_score": round(self.drift_score, 4),
            "threshold": self.threshold,
            "confidence": round(self.confidence, 4),
            "claim_vector": self.claim_vector.tolist(),
            "observation_vector": self.observation_vector.tolist(),
            "metric": self.metric.value,
            "timestamp": self.timestamp.isoformat(),
            "details": self.details,
        }
    
    def __repr__(self) -> str:
        return (
            f"VerificationResult(\n"
            f"  status={self.status.value},\n"
            f"  drift_score={self.drift_score:.4f},\n"
            f"  threshold={self.threshold},\n"
            f"  confidence={self.confidence:.2%}\n"
            f")"
        )


class VerificationKernel:
    """
    The Mathematical Verification Kernel.
    
    This is the core engine that performs deterministic verification
    of carbon credit claims against satellite observations.
    
    KEY PRINCIPLE: No LLM inference. Pure mathematics.
    
    "The AI didn't decide; the Math decided. The AI just managed the workflow."
    
    Thresholds:
        - drift_score < 0.10: VERIFIED (claims match observations)
        - drift_score 0.10 - 0.15: FLAGGED (minor discrepancy)
        - drift_score > 0.15: FRAUD (significant discrepancy)
    """
    
    DEFAULT_THRESHOLD = 0.15
    FLAG_THRESHOLD = 0.10
    
    def __init__(self, threshold: float = DEFAULT_THRESHOLD):
        """
        Initialize the verification kernel.
        
        Args:
            threshold: Drift score threshold for fraud detection
        """
        self.threshold = threshold
        self._verification_count = 0

    def verify(
        self,
        target: ClaimVector,
        actual: ObservationVector,
        metric: DriftMetric = DriftMetric.EUCLIDEAN,
    ) -> VerificationResult:
        """
        Perform mathematical verification of a claim against observation.
        
        Args:
            target: The claimed values (from Project Design Document)
            actual: The observed values (from satellite data)
            metric: The distance metric to use
            
        Returns:
            VerificationResult with status and audit details
        """
        self._verification_count += 1
        
        # Convert to vectors
        claim_vec = target.to_array()
        obs_vec = actual.to_array()
        
        # Calculate drift score
        drift_score = self._calculate_drift(claim_vec, obs_vec, metric)
        
        # Determine status
        if drift_score > self.threshold:
            status = VerificationStatus.FRAUD
        elif drift_score > self.FLAG_THRESHOLD:
            status = VerificationStatus.FLAGGED
        else:
            status = VerificationStatus.VERIFIED
        
        # Calculate additional details
        details = self._calculate_details(target, actual, drift_score)
        
        return VerificationResult(
            project_id=target.project_id,
            status=status,
            drift_score=drift_score,
            threshold=self.threshold,
            confidence=actual.confidence,
            claim_vector=claim_vec,
            observation_vector=obs_vec,
            metric=metric,
            timestamp=datetime.utcnow(),
            details=details,
        )

    def _calculate_drift(
        self,
        claim: np.ndarray,
        observation: np.ndarray,
        metric: DriftMetric,
    ) -> float:
        """
        Calculate the drift score between two vectors.
        
        Args:
            claim: Claimed values as numpy array
            observation: Observed values as numpy array
            metric: Distance metric to use
            
        Returns:
            Drift score (0 = perfect match, higher = more discrepancy)
        """
        if metric == DriftMetric.EUCLIDEAN:
            return float(np.linalg.norm(claim - observation))
        
        elif metric == DriftMetric.COSINE:
            # Cosine distance (1 - similarity)
            dot = np.dot(claim, observation)
            norm = np.linalg.norm(claim) * np.linalg.norm(observation)
            if norm == 0:
                return 1.0
            return 1.0 - (dot / norm)
        
        elif metric == DriftMetric.MANHATTAN:
            return float(np.sum(np.abs(claim - observation)))
        
        elif metric == DriftMetric.RELATIVE:
            # Average relative error
            with np.errstate(divide='ignore', invalid='ignore'):
                relative = np.abs((claim - observation) / claim)
                relative = np.nan_to_num(relative, nan=1.0, posinf=1.0)
            return float(np.mean(relative))
        
        else:
            raise ValueError(f"Unknown metric: {metric}")

    def _calculate_details(
        self,
        claim: ClaimVector,
        observation: ObservationVector,
        drift_score: float,
    ) -> Dict[str, Any]:
        """
        Calculate detailed breakdown for audit trail.
        """
        ndvi_diff = claim.ndvi - observation.ndvi_mean
        ndvi_pct_diff = abs(ndvi_diff) / claim.ndvi * 100 if claim.ndvi else 0
        
        carbon_diff = claim.carbon_stock - observation.estimated_carbon_stock
        carbon_pct_diff = abs(carbon_diff) / claim.carbon_stock * 100 if claim.carbon_stock else 0
        
        return {
            "ndvi_claimed": claim.ndvi,
            "ndvi_observed": observation.ndvi_mean,
            "ndvi_difference": round(ndvi_diff, 4),
            "ndvi_percent_difference": round(ndvi_pct_diff, 2),
            "carbon_claimed": claim.carbon_stock,
            "carbon_observed": round(observation.estimated_carbon_stock, 2),
            "carbon_difference": round(carbon_diff, 2),
            "carbon_percent_difference": round(carbon_pct_diff, 2),
            "observation_confidence": round(observation.confidence, 4),
            "deforestation_indicator": observation.deforestation_indicator,
            "vegetation_coverage": observation.vegetation_coverage,
            "audit_note": self._generate_audit_note(drift_score, ndvi_pct_diff, carbon_pct_diff),
        }

    def _generate_audit_note(
        self,
        drift_score: float,
        ndvi_pct_diff: float,
        carbon_pct_diff: float,
    ) -> str:
        """
        Generate human-readable audit note.
        """
        if drift_score > self.threshold:
            return (
                f"CRITICAL: Mathematical verification failed. "
                f"NDVI discrepancy: {ndvi_pct_diff:.1f}%, "
                f"Carbon stock discrepancy: {carbon_pct_diff:.1f}%. "
                f"Drift score ({drift_score:.4f}) exceeds threshold ({self.threshold}). "
                f"Recommend investigation for potential fraud."
            )
        elif drift_score > self.FLAG_THRESHOLD:
            return (
                f"WARNING: Minor discrepancies detected. "
                f"NDVI discrepancy: {ndvi_pct_diff:.1f}%, "
                f"Carbon stock discrepancy: {carbon_pct_diff:.1f}%. "
                f"Recommend manual review."
            )
        else:
            return (
                f"VERIFIED: Claim aligns with satellite observations. "
                f"NDVI discrepancy: {ndvi_pct_diff:.1f}%, "
                f"Carbon stock discrepancy: {carbon_pct_diff:.1f}%. "
                f"Within acceptable tolerance."
            )

    @property
    def stats(self) -> Dict[str, Any]:
        """Get kernel statistics."""
        return {
            "verification_count": self._verification_count,
            "threshold": self.threshold,
            "flag_threshold": self.FLAG_THRESHOLD,
        }
