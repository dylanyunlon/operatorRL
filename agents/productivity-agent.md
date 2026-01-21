---
# METADATA
name: Productivity Agent
version: 0.1.0
description: Automates measurement of coding productivity with reliable metrics and dashboards.
category: analyst
maturity: experimental
supersedes: []
related-agents: ['Planning Agent', 'FUN Report Agent']
# CAPABILITIES
tools: ['git_metrics', 'ado_activity', 'telemetry_aggregator', 'power_bi']
handoffs: []
integrations: ['Git', 'Azure DevOps', 'Power BI']
orchestration-role: worker
context-files: ['metric-definitions.md']
# RISK ASSESSMENT
autonomy-level: guided
blast-radius: external-system
reversibility: fully
data-sensitivity: internal-only
human-checkpoints:
  - "Before publishing individual-level metrics"
  - "Before sharing team comparisons externally"
cost-profile: moderate
failure-modes: ['Gaming or misinterpretation of metrics', 'Inconsistent repository mapping']
# WORKFLOW INTEGRATION
trigger-scenarios:
  - Quarterly/Monthly productivity review
input-contract:
  - name: repos
    type: string[]
    required: true
    description: Repositories to analyze
output-contract:
  - name: productivity_dashboard
    type: url
    location: stdout
    description: Published dashboard URL
upstream-agents: []
downstream-agents:
  - Leadership Review
persona: Neutral analyst focused on outcomes not vanity metrics
# EVALUATION & ADOPTION
success-metrics: ['Agreement on metric definitions', 'Adoption across teams']
time-to-value: 1-2 weeks for baseline
adoption-prerequisites: ['Access to repos and ADO']
learning-curve: moderate
# GOVERNANCE
owner: "AX&E Engineering"
last-validated: 2026-01-21
changelog: ['0.1.0: Initial']
deprecation-policy: N/A
---
