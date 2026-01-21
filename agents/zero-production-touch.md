---
# METADATA
name: Zero Production Touch
version: 0.1.0
description: Automated safety dashboard to replace manual reviews of unsafe production changes; keep work in Learn platform, maintain only (on hold).
category: orchestrator
maturity: deprecated
supersedes: []
related-agents: ['Release Freshness Agent', 'SRE Agent']
# CAPABILITIES
tools: ['policy_checker', 'release_diff', 'alerting']
handoffs: []
integrations: ['Learn Platform', 'ADO Pipelines']
orchestration-role: coordinator
context-files: ['prod-safety-rules.md']
# RISK ASSESSMENT
autonomy-level: guided
blast-radius: external-system
reversibility: fully
data-sensitivity: internal-only
human-checkpoints:
  - "Before flagging a release as unsafe"
  - "Before blocking a release pipeline"
cost-profile: moderate
failure-modes: ['False positives blocking release']
# WORKFLOW INTEGRATION
trigger-scenarios:
  - Pre-deploy safety review
input-contract:
  - name: release_candidate
    type: string
    required: true
    description: Release branch/build identifier
output-contract:
  - name: safety_report
    type: markdown
    location: stdout
    description: Findings and required mitigations
upstream-agents:
  - Release Freshness Agent
downstream-agents:
  - Release Managers
persona: Strict but fair release guardian
# EVALUATION & ADOPTION
success-metrics: ['Reduction in unsafe pushes', 'Reduced manual bi-weekly review time']
time-to-value: Per release cycle
adoption-prerequisites: ['Access to pipeline and repo policies']
learning-curve: minimal
# GOVERNANCE
owner: "AX&E Engineering"
last-validated: 2026-01-21
changelog: ['0.1.0: Initial', '0.1.1: Status set to On Hold']
deprecation-policy: Paused until capacity constraints resolved
---
