---
# METADATA
name: SFI Agent
version: 0.1.0
description: Manages SFI work with two specialized agents integrated into the SWE agent; tracks ~6 KPIs and automates ADO WIT creation.
category: hybrid
maturity: beta
supersedes: []
related-agents: ['FUN Report Agent', 'Planning Agent', 'Accessibility Agent']
# CAPABILITIES
tools: ['ado_api', 'kpi_calculator', 'power_bi', 'swe_agent_bridge']
handoffs: []
integrations: ['Azure DevOps', 'Power BI']
orchestration-role: coordinator
context-files: ['sfi-kpis.md', 'ado-wit-templates.md']
# RISK ASSESSMENT
autonomy-level: semi-autonomous
blast-radius: external-system
reversibility: partially
data-sensitivity: internal-only
human-checkpoints:
  - "Before mass WIT creation"
  - "Before KPI publication"
cost-profile: moderate
failure-modes: ['Incorrect KPI mapping', 'Duplicate work items', 'Misclassified SFI types']
# WORKFLOW INTEGRATION
trigger-scenarios:
  - Weekly/Monthly SFI review
  - SFI intake events
input-contract:
  - name: sfi_sources
    type: files
    required: true
    description: Data sources / exports
output-contract:
  - name: kpi_report
    type: markdown
    location: stdout
    description: Trend, goals, and deltas
  - name: ado_wits
    type: json
    location: file
    description: Created/updated ADO items
upstream-agents:
  - FUN Report
downstream-agents:
  - SWE Agent
persona: Operationally minded program manager
# EVALUATION & ADOPTION
success-metrics: ['KPI freshness within 24h', 'Reduction in manual SFI processing']
time-to-value: 1-2 days initial, then hours
adoption-prerequisites: ['ADO area path permissions', 'Power BI workspace']
learning-curve: moderate
# GOVERNANCE
owner: "AX&E Engineering"
last-validated: 2026-01-21
changelog: ['0.1.0: First draft']
deprecation-policy: N/A
---
