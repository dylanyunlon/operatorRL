"""
Topic Definitions

Standard topic names for the Carbon Auditor message bus.
"""


class Topics:
    """Standard topic names for agent communication."""
    
    # Claims Agent publishes extracted claims here
    CLAIMS = "vcm.claims"
    
    # Geo Agent publishes satellite observations here
    OBSERVATIONS = "vcm.observations"
    
    # Auditor Agent publishes verification results here
    VERIFICATION_RESULTS = "vcm.verification"
    
    # Critical alerts (fraud detection, etc.)
    ALERTS = "vcm.alerts"
    
    # System messages (agent status, errors, etc.)
    SYSTEM = "vcm.system"
