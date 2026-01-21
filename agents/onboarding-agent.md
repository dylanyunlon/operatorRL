---
# METADATA
name: Onboarding Agent
version: 0.1.0
description: Reduces onboarding time by generating key engineering artifacts from existing wiki/SharePoint/code and auto-creating initial ADO items.
category: capture
maturity: experimental
supersedes: []
related-agents: ['Planning Agent', 'Design Review Agent']
# CAPABILITIES
tools: ['sharepoint_reader', 'repo_reader', 'ado_api', 'doc_summarizer']
handoffs: []
integrations: ['SharePoint', 'GitHub/ADO Repos', 'Azure DevOps']
orchestration-role: worker
context-files: ['onboarding-template.md', 'team-handbook.md']
# RISK ASSESSMENT
autonomy-level: guided
blast-radius: external-system
reversibility: fully
data-sensitivity: internal-only
human-checkpoints:
  - "Before publishing onboarding guide"
  - "Before creating ADO onboarding items"
cost-profile: minimal
failure-modes: ['Outdated source materials', 'Non-standard team practices']
# WORKFLOW INTEGRATION
trigger-scenarios:
  - New hire joins
  - Team rotation
input-contract:
  - name: team_sources
    type: string[]
    required: true
    description: Links to wikis/repos
output-contract:
  - name: onboarding_guide
    type: markdown
    location: stdout
    description: Role-specific starter guide
upstream-agents:
  - Planning Agent
downstream-agents:
  - New Hire
  - Manager
persona: Supportive coach with curated, minimal path
# EVALUATION & ADOPTION
success-metrics: ['Time-to-first-PR', 'Time-to-environment-setup']
time-to-value: Same day
adoption-prerequisites: ['Access to team repositories and wikis']
learning-curve: minimal
# GOVERNANCE
owner: "AX&E Engineering"
last-validated: 2026-01-21
changelog: ['0.1.0: Initial']
deprecation-policy: N/A
---
