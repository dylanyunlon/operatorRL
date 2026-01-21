---
# METADATA
name: SRE Agent
version: 0.1.0
description: Self-serve live site incident assistant providing 24×7 monitoring with proactive alerts; closes alerting gaps with recommendations.
category: orchestrator
maturity: experimental
supersedes: []
related-agents:
  - Release Freshness Agent
  - Zero Production Touch
# CAPABILITIES
tools:
  - icm_api
  - geneva_metrics
  - kusto_query
  - runbook_executor
  - teams_notifier
handoffs: []
integrations:
  - IcM Agent Studio
  - SRE Portal
  - Azure Monitor (Geneva)
orchestration-role: coordinator
context-files:
  - sev-definitions.md
  - oncall-rotations.md
# RISK ASSESSMENT
autonomy-level: semi-autonomous
blast-radius: external-system
reversibility: partially
data-sensitivity: internal-only
human-checkpoints:
  - "Before auto-escalation"
  - "Before incident closure"
cost-profile: variable
failure-modes:
  - False positives/alert fatigue
  - Missed correlated signals
  - Improper escalation routing
# WORKFLOW INTEGRATION
trigger-scenarios:
  - Anomalies detected
  - New IcM created
  - Runbook recommendation needed
input-contract:
  - name: signal
    type: json
    required: true
    description: Alert payload or IcM event
output-contract:
  - name: triage_summary
    type: markdown
    location: stdout
    description: What happened, impact, hypothesis, next steps
  - name: actions
    type: json
    location: file
    description: Suggested/run runbooks with parameters
upstream-agents:
  - Release Freshness Agent
downstream-agents:
  - On-call Engineer
  - Incident Postmortem
persona: Calm, evidence-driven responder
# EVALUATION & ADOPTION
success-metrics:
  - MTTA/MTTR reduction
  - Lower page volume with same coverage
time-to-value: Immediate on alert
adoption-prerequisites:
  - IcM access
  - Geneva/Kusto query permissions
learning-curve: moderate
# GOVERNANCE
owner: "AX&E Engineering"
last-validated: 2026-01-21
changelog:
  - "0.1.0: Seed spec"
deprecation-policy: N/A
---
