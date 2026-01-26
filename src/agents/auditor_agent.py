"""
Auditor Agent (Agent C) - "The Judge"

The decision maker that uses cmvk (Verification Kernel) to detect fraud.
Compares claims against observations using mathematical verification.
"""

from typing import Any, Dict, List, Optional

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amb import Message, MessageType
from amb.topics import Topics
from cmvk import VerificationKernel, DriftMetric, ClaimVector, ObservationVector
from cmvk.kernel import VerificationStatus
from .base import Agent, MessageBus


class AuditorAgent(Agent):
    """
    The Auditor Agent - "The Judge"
    
    Role: Decision maker that:
        - Subscribes to both Claim and Observation messages
        - Uses cmvk (Verification Kernel) to calculate drift scores
        - Issues verification results: VERIFIED, FLAGGED, or FRAUD
    
    KEY PRINCIPLE: "The AI didn't decide; the Math decided."
    
    The agent doesn't use LLM inference for the verification decision.
    It uses deterministic mathematical calculations via cmvk.
    
    Dependencies: cmvk (Carbon Market Verification Kernel)
    
    Subscribes to: CLAIMS, OBSERVATIONS
    Publishes: Verification results and ALERTS
    """

    def __init__(
        self,
        agent_id: str,
        bus: MessageBus,
        threshold: float = 0.15
    ):
        """
        Initialize the Auditor Agent.
        
        Args:
            agent_id: Unique identifier
            bus: Message bus reference
            threshold: Drift score threshold for fraud detection
        """
        super().__init__(agent_id, bus, name="auditor-agent")
        
        # Initialize the Verification Kernel
        self._kernel = VerificationKernel(threshold=threshold)
        
        # Store pending claims and observations for matching
        self._pending_claims: Dict[str, Dict[str, Any]] = {}
        self._pending_observations: Dict[str, Dict[str, Any]] = {}
        
        # Verification results
        self._results: List[Dict[str, Any]] = []

    @property
    def subscribed_topics(self) -> List[str]:
        """Subscribe to both claims and observations."""
        return [Topics.CLAIMS, Topics.OBSERVATIONS]

    def handle_message(self, message: Message) -> None:
        """
        Handle incoming claim or observation messages.
        
        When both a claim and observation are received for the same project,
        perform verification.
        """
        project_id = message.payload.get("project_id")
        
        if message.type == MessageType.CLAIM:
            self._log(f"Received claim for project: {project_id}")
            self._pending_claims[project_id] = message.payload
            
        elif message.type == MessageType.OBSERVATION:
            self._log(f"Received observation for project: {project_id}")
            self._pending_observations[project_id] = message.payload
        
        # Check if we can perform verification
        if project_id in self._pending_claims and project_id in self._pending_observations:
            self._perform_verification(
                project_id,
                self._pending_claims[project_id],
                self._pending_observations[project_id],
                message.correlation_id,
            )
            
            # Clean up
            del self._pending_claims[project_id]
            del self._pending_observations[project_id]

    def _perform_verification(
        self,
        project_id: str,
        claim_data: Dict[str, Any],
        observation_data: Dict[str, Any],
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Perform mathematical verification using cmvk.
        
        This is where "the Math decides, not the AI."
        """
        self._log(f"Performing verification for project: {project_id}")
        self._log("="*60)
        
        # Build vectors
        claim_vector = ClaimVector(
            project_id=project_id,
            ndvi=claim_data.get("claimed_ndvi", 0.0),
            carbon_stock=claim_data.get("claimed_carbon_stock", 0.0),
            year=claim_data.get("year", 2024),
            polygon=claim_data.get("polygon"),
        )
        
        observation_vector = ObservationVector(
            project_id=project_id,
            ndvi_mean=observation_data.get("observed_ndvi_mean", 0.0),
            ndvi_std=observation_data.get("observed_ndvi_std", 0.0),
            cloud_cover=observation_data.get("cloud_cover", 0.0),
            vegetation_coverage=observation_data.get("vegetation_coverage", 0.0),
            deforestation_indicator=observation_data.get("deforestation_indicator", 0.0),
        )
        
        self._log(f"Claim Vector: {claim_vector}")
        self._log(f"Observation Vector: {observation_vector}")
        
        # Run verification through cmvk
        # This is MATHEMATICAL, not LLM-based
        result = self._kernel.verify(
            target=claim_vector,
            actual=observation_vector,
            metric=DriftMetric.EUCLIDEAN,
        )
        
        self._log(f"Verification Result: {result}")
        
        # Store result
        result_dict = result.to_dict()
        self._results.append(result_dict)
        
        # Publish verification result
        self.publish(
            topic=Topics.VERIFICATION_RESULTS,
            payload=result_dict,
            message_type=MessageType.VERIFICATION_RESULT,
            correlation_id=correlation_id,
        )
        
        # If fraud detected, publish alert
        if result.status == VerificationStatus.FRAUD:
            self._issue_alert(result, correlation_id)
        
        return result_dict

    def _issue_alert(
        self,
        result: Any,
        correlation_id: Optional[str] = None,
    ) -> None:
        """
        Issue a CRITICAL alert for detected fraud.
        """
        alert = {
            "level": "CRITICAL",
            "type": "FRAUD_DETECTED",
            "project_id": result.project_id,
            "drift_score": result.drift_score,
            "threshold": result.threshold,
            "message": (
                f"FRAUD ALERT: Project {result.project_id} shows significant "
                f"discrepancy between claimed and observed values. "
                f"Drift score ({result.drift_score:.4f}) exceeds "
                f"threshold ({result.threshold})."
            ),
            "details": result.details,
            "recommendation": "Immediate investigation required. Suspend credit issuance pending review.",
        }
        
        self._log("!"*60, level="ALERT")
        self._log(f"FRAUD DETECTED: {result.project_id}", level="ALERT")
        self._log(f"Drift Score: {result.drift_score:.4f} (threshold: {result.threshold})", level="ALERT")
        self._log("!"*60, level="ALERT")
        
        self.publish(
            topic=Topics.ALERTS,
            payload=alert,
            message_type=MessageType.ALERT,
            correlation_id=correlation_id,
        )

    def verify_project(
        self,
        claim_vector: ClaimVector,
        observation_vector: ObservationVector,
    ) -> Dict[str, Any]:
        """
        Direct verification API (for programmatic use).
        
        Args:
            claim_vector: The claimed values
            observation_vector: The observed values
            
        Returns:
            Verification result dictionary
        """
        result = self._kernel.verify(
            target=claim_vector,
            actual=observation_vector,
            metric=DriftMetric.EUCLIDEAN,
        )
        
        result_dict = result.to_dict()
        self._results.append(result_dict)
        
        return result_dict

    def get_results(self) -> List[Dict[str, Any]]:
        """Get all verification results."""
        return self._results

    def get_kernel_stats(self) -> Dict[str, Any]:
        """Get verification kernel statistics."""
        return self._kernel.stats
