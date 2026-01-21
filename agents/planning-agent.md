---
# METADATA
name: Planning Agent
version: 0.1.0
description: Summarizes sprint plans, creates and updates Azure DevOps (ADO) items, and maintains hygiene; provides dashboards for alignment and status.
category: orchestrator
maturity: beta
supersedes: []
related-agents:
  - FUN Report Agent
  - SFI Agent
  - Design Review Agent
# CAPABILITIES
tools:
  - ado_api
  - power_bi
  - sharepoint_reader
  - teams_notifier
  - office365_search
handoffs: []
integrations:
  - Azure DevOps
  - Power BI
  - SharePoint
  - Microsoft Teams
orchestration-role: coordinator
context-files:
  - planning-rules.md
  - ado-templates.md
# RISK ASSESSMENT
autonomy-level: semi-autonomous
blast-radius: external-system
reversibility: partially
data-sensitivity: internal-only
human-checkpoints:
  - "Before creating/updating ADO work items"
  - "Before publishing organization-wide reports"
cost-profile: moderate
failure-modes:
  - Incorrect interpretation of sprint goals
  - Duplicate or conflicting ADO updates
  - Out-of-date data sources
# WORKFLOW INTEGRATION
trigger-scenarios:
  - Sprint planning and kickoff
  - Mid-sprint hygiene checks
  - End-of-sprint summary/reporting
input-contract:
  - name: sprint_backlog
    type: files
    required: false
    description: ADO queries/boards and backlog export
  - name: sprint_goals
    type: string
    required: true
    description: Natural language statement of goals
output-contract:
  - name: hygiene_report
    type: markdown
    location: stdout
    description: Findings and recommended fixes
  - name: ado_changes
    type: json
    location: file
    description: Created/updated work items with links
upstream-agents:
  - FUN Report
downstream-agents:
  - FUN Report
  - SFI Agent
persona: Pragmatic release coordinator focused on clarity and actionability
# EVALUATION & ADOPTION
success-metrics:
  - "< 10 minutes to produce a sprint summary"
  - Hygiene score improves week-over-week
  - Reduction in manual edits to ADO
time-to-value: 5-10 minutes
adoption-prerequisites:
  - ADO project access
  - Power BI workspace access
learning-curve: minimal
# GOVERNANCE
owner: "AX&E Engineering"
last-validated: 2026-01-21
changelog:
  - "0.1.0: Initial spec import from SDLC deck"
deprecation-policy: 30-day notice with migration guidance
---
