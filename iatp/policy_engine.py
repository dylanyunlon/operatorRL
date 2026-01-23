"""
Policy Engine Integration with agent-control-plane.

This module wraps the agent-control-plane PolicyEngine to provide
policy validation for IATP capability manifests.
"""
from typing import Dict, Any, Tuple, Optional, List
from agent_control_plane import PolicyEngine, PolicyRule
from iatp.models import CapabilityManifest, RetentionPolicy, ReversibilityLevel


class IATPPolicyEngine:
    """
    Wrapper around agent-control-plane PolicyEngine for IATP.
    
    This integrates the agent-control-plane's policy validation
    capabilities into IATP's handshake and request validation flow.
    
    Note: This uses agent-control-plane's PolicyEngine for governance
    and extends it with IATP-specific policy logic.
    """
    
    def __init__(self):
        """Initialize the IATP Policy Engine."""
        self.engine = PolicyEngine()
        self.custom_rules: List[Dict[str, Any]] = []
        self._setup_default_policies()
    
    def _setup_default_policies(self):
        """Setup default security policies for IATP."""
        # Define IATP-specific policy rules
        # These are custom rules that work with our manifest validation
        self.custom_rules = [
            {
                "name": "StrictPrivacyRetention",
                "description": "Block agents with permanent data retention",
                "action": "deny",
                "conditions": {
                    "retention_policy": ["permanent", "forever"]
                }
            },
            {
                "name": "RequireReversibility",
                "description": "Warn when agents don't support reversibility",
                "action": "warn",
                "conditions": {
                    "reversibility": ["none"]
                }
            },
            {
                "name": "AllowEphemeral",
                "description": "Allow agents with ephemeral data retention",
                "action": "allow",
                "conditions": {
                    "retention_policy": ["ephemeral"]
                }
            }
        ]
    
    def add_custom_rule(self, rule: Dict[str, Any]):
        """
        Add a custom policy rule.
        
        Args:
            rule: Dictionary defining the policy rule with keys:
                - name: Rule name
                - description: Rule description
                - action: "allow", "warn", or "deny"
                - conditions: Dictionary of conditions to match
        """
        # Insert at the beginning so custom rules take precedence
        self.custom_rules.insert(0, rule)
    
    def validate_manifest(
        self,
        manifest: CapabilityManifest
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Validate a capability manifest against policies.
        
        This is the main integration point that validates incoming
        agent manifests against the configured policy rules.
        
        Args:
            manifest: The capability manifest to validate
        
        Returns:
            Tuple of (is_allowed, error_message, warning_message)
            - is_allowed: True if request should proceed
            - error_message: Blocking error if is_allowed is False
            - warning_message: Warning for user if there are concerns
        """
        # Convert manifest to policy context
        context = self._manifest_to_context(manifest)
        
        # Evaluate against custom policy rules
        permission_action = self._evaluate_rules(context)
        
        # Generate appropriate response
        if permission_action == "deny":
            return False, self._generate_deny_message(manifest, context), None
        elif permission_action == "warn":
            return True, None, self._generate_warn_message(manifest, context)
        else:  # allow
            return True, None, None
    
    def _evaluate_rules(self, context: Dict[str, Any]) -> str:
        """
        Evaluate custom rules against context.
        
        Args:
            context: Policy evaluation context
        
        Returns:
            Action string: "allow", "warn", or "deny"
        """
        # Check rules in order of severity: deny, warn, allow
        for rule in self.custom_rules:
            if self._rule_matches(rule, context):
                return rule["action"]
        
        # Default to allow if no rules match
        return "allow"
    
    def _rule_matches(self, rule: Dict[str, Any], context: Dict[str, Any]) -> bool:
        """
        Check if a rule matches the context.
        
        Args:
            rule: Rule definition
            context: Context to check
        
        Returns:
            True if rule matches
        """
        conditions = rule.get("conditions", {})
        
        for key, values in conditions.items():
            if key in context:
                if context[key] in values:
                    return True
        
        return False
    
    def _manifest_to_context(self, manifest: CapabilityManifest) -> Dict[str, Any]:
        """
        Convert a capability manifest to a policy evaluation context.
        
        Args:
            manifest: The capability manifest
        
        Returns:
            Dictionary context for policy evaluation
        """
        return {
            "agent_id": manifest.agent_id,
            "trust_level": manifest.trust_level.value,
            "retention_policy": manifest.privacy_contract.retention.value,
            "reversibility": manifest.capabilities.reversibility.value,
            "idempotency": manifest.capabilities.idempotency,
            "human_review": manifest.privacy_contract.human_review,
            "encryption_at_rest": manifest.privacy_contract.encryption_at_rest,
            "encryption_in_transit": manifest.privacy_contract.encryption_in_transit,
        }
    
    def _generate_deny_message(
        self,
        manifest: CapabilityManifest,
        context: Dict[str, Any]
    ) -> str:
        """
        Generate a denial message for blocked requests.
        
        Args:
            manifest: The capability manifest
            context: Policy context
        
        Returns:
            User-friendly error message
        """
        reasons = []
        
        if context["retention_policy"] in ["permanent", "forever"]:
            reasons.append(
                f"Agent '{manifest.agent_id}' stores data permanently, "
                "which violates privacy policies"
            )
        
        if context["trust_level"] == "untrusted":
            reasons.append(
                f"Agent '{manifest.agent_id}' is marked as untrusted"
            )
        
        if reasons:
            return "Policy Violation: " + "; ".join(reasons)
        
        return f"Policy Violation: Agent '{manifest.agent_id}' failed policy validation"
    
    def _generate_warn_message(
        self,
        manifest: CapabilityManifest,
        context: Dict[str, Any]
    ) -> str:
        """
        Generate a warning message for risky requests.
        
        Args:
            manifest: The capability manifest
            context: Policy context
        
        Returns:
            User-friendly warning message
        """
        warnings = []
        
        if context["reversibility"] == "none":
            warnings.append(
                f"Agent '{manifest.agent_id}' does not support transaction reversal"
            )
        
        if not context["idempotency"]:
            warnings.append(
                f"Agent '{manifest.agent_id}' may not handle duplicate requests safely"
            )
        
        if context["human_review"]:
            warnings.append(
                f"Agent '{manifest.agent_id}' may have humans review your data"
            )
        
        if warnings:
            return "⚠️  Policy Warning:\n" + "\n".join(f"  • {w}" for w in warnings)
        
        return None
    
    def validate_handshake(
        self,
        manifest: CapabilityManifest,
        required_capabilities: Optional[List[str]] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate handshake compatibility between agents.
        
        This checks if the remote agent's capabilities meet the
        local agent's requirements.
        
        Args:
            manifest: Remote agent's capability manifest
            required_capabilities: List of required capability keys
        
        Returns:
            Tuple of (is_compatible, error_message)
        """
        if not required_capabilities:
            # Default requirements
            required_capabilities = []
        
        # Always validate against base policies first
        is_allowed, error_msg, _ = self.validate_manifest(manifest)
        if not is_allowed:
            return False, error_msg
        
        # Check specific capability requirements
        missing = []
        for capability in required_capabilities:
            if capability == "reversibility" and \
               manifest.capabilities.reversibility == ReversibilityLevel.NONE:
                missing.append("reversibility support")
            elif capability == "idempotency" and \
                 not manifest.capabilities.idempotency:
                missing.append("idempotency support")
        
        if missing:
            return False, f"Agent missing required capabilities: {', '.join(missing)}"
        
        return True, None
