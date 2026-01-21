---
# METADATA
name: Release Freshness Agent
version: 0.1.0
description: Tracks production freshness and automatically follows up on delayed deployments and pending changes across services.
category: analyst
maturity: beta
supersedes: []
related-agents: ['SRE Agent', 'Zero Production Touch', 'Planning Agent']
# CAPABILITIES
tools: ['ado_release_api', 'git_diff_checker', 'power_bi', 'notifier']
handoffs: []
integrations: ['ADO Pipelines', 'Git', 'Power BI']
orchestration-role: worker
context-files: ['service-catalog.md', 'release-policies.md']
# RISK ASSESSMENT
autonomy-level: semi-autonomous
blast-radius: external-system
reversibility: fully
data-sensitivity: internal-only
human-checkpoints:
  - "Before posting broad follow-ups"
  - "Before escalating stale deployments to leadership"
cost-profile: moderate
failure-modes: ['False staleness due to hotfix branches', 'Missing service mapping']
# WORKFLOW INTEGRATION
trigger-scenarios:
  - Daily freshness scan
  - Missed SLA window
input-contract:
  - name: service_map
    type: file
    required: true
    description: List of repos/pipelines to monitor
output-contract:
  - name: freshness_report
    type: markdown
    location: stdout
    description: Services behind, suggested follow-ups
upstream-agents:
  - Planning Agent
downstream-agents:
  - SRE Agent
  - Service Owners
persona: Data-first release analyst
# EVALUATION & ADOPTION
success-metrics: ['Reduction in stale deployments', 'Time-to-follow-up < 24h']
time-to-value: Within first scan cycle
adoption-prerequisites: ['Access to repos and pipeline metadata', 'Power BI workspace']
learning-curve: minimal
# GOVERNANCE
owner: "AX&E Engineering"
last-validated: 2026-01-21
changelog: ['0.1.0: Initial']
deprecation-policy: N/A
---
