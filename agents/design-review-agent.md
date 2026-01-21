---
# METADATA
name: Design Review Agent
version: 0.1.0
description: Provides early feedback on design, architecture, and security using historical data so designs improve before peer review.
category: hybrid
maturity: beta
supersedes: []
related-agents: ['Planning Agent', 'Unit & Scenario Testing Agent']
# CAPABILITIES
tools: ['repo_reader', 'threat_model_rules', 'static_analysis', 'office365_search', 'doc_reviewer']
handoffs: []
integrations: ['GitHub', 'Azure DevOps Repos', 'SharePoint', 'Teams']
orchestration-role: standalone
context-files: ['design-checklist.md', 'security-baselines.md']
# RISK ASSESSMENT
autonomy-level: guided
blast-radius: workspace
reversibility: fully
data-sensitivity: internal-only
human-checkpoints:
  - "Before filing blocking issues"
  - "Before recommending architectural changes"
cost-profile: moderate
failure-modes: ['Out-of-context recommendations', 'Overly generic guidance', 'Missed historical precedent']
# WORKFLOW INTEGRATION
trigger-scenarios:
  - Draft design doc ready
  - Pre-PR design gate
input-contract:
  - name: design_doc
    type: file
    required: true
    description: Design document (.md/.docx/.pptx)
  - name: repo_links
    type: string[]
    required: false
    description: Relevant code locations
output-contract:
  - name: review_findings
    type: markdown
    location: stdout
    description: Actionable strengths, risks, and questions
upstream-agents:
  - Planning Agent
downstream-agents:
  - Implementation Agent
  - Security Review
persona: Thoughtful architect citing prior art and risks
# EVALUATION & ADOPTION
success-metrics: ['Actionable findings accepted by team', 'Reduced rework during implementation']
time-to-value: 5-15 minutes
adoption-prerequisites: ['Access to design repo/wiki', 'Security baseline documents']
learning-curve: moderate
# GOVERNANCE
owner: "AX&E Engineering"
last-validated: 2026-01-21
changelog: ['0.1.0: Initial spec from deck']
deprecation-policy: Superseded by Org Design LLM when available
---
