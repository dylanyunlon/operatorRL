---
# METADATA
name: FUN Report Agent
version: 0.1.0
description: Centralized reporting for LSI/SFI metrics; eliminates manual report creation for AX&E Engineering.
category: analyst
maturity: beta
supersedes: []
related-agents:
  - SFI Agent
  - Planning Agent
# CAPABILITIES
tools:
  - power_bi
  - dataset_connector
  - scheduler
handoffs: []
integrations:
  - Power BI
  - SharePoint
orchestration-role: worker
context-files:
  - report-definitions.md
# RISK ASSESSMENT
autonomy-level: guided
blast-radius: external-system
reversibility: fully
data-sensitivity: internal-only
human-checkpoints:
  - "Before publishing org dashboards"
  - "Before refreshing underlying datasets"
cost-profile: moderate
failure-modes:
  - Stale datasets
  - Misaligned definitions across teams
# WORKFLOW INTEGRATION
trigger-scenarios:
  - End-of-week reporting
  - Monthly business review
input-contract:
  - name: datasets
    type: files
    required: true
    description: Data sources / semantic models
output-contract:
  - name: fun_dashboard
    type: url
    location: stdout
    description: Published dashboard URL
upstream-agents:
  - SFI Agent
  - Planning Agent
downstream-agents:
  - Leadership Review
persona: Clear, concise reporter
# EVALUATION & ADOPTION
success-metrics:
  - 100% automation of weekly report
  - "Report preparation time < 10 minutes"
time-to-value: Same day once datasets wired
adoption-prerequisites:
  - Power BI workspace and dataset refresh permissions
learning-curve: minimal
# GOVERNANCE
owner: "AX&E Engineering"
last-validated: 2026-01-21
changelog:
  - "0.1.0: Initial"
deprecation-policy: Replace with Org Analytics when ready
---
