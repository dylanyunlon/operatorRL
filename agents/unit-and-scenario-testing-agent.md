---
# METADATA
name: Unit & Scenario Testing Agent (AX&E Unit Testing)
version: 0.1.0
description: Generates AI-assisted unit and scenario tests and integrates with pipelines to increase coverage and defect detection.
category: analyst
maturity: beta
supersedes: []
related-agents: ['Accessibility Agent', 'Design Review Agent']
# CAPABILITIES
tools: ['test_generator', 'playwright', 'coverage_analyzer', 'pipeline_integration']
handoffs: []
integrations: ['ADO Pipelines', 'Playwright', 'Git']
orchestration-role: worker
context-files: ['test-guidelines.md', 'critical-scenarios.md']
# RISK ASSESSMENT
autonomy-level: guided
blast-radius: workspace
reversibility: fully
data-sensitivity: internal-only
human-checkpoints:
  - "Before committing generated tests"
  - "Before enabling tests in CI gate"
cost-profile: moderate
failure-modes: ['Flaky tests', 'Overfitting to current UI state']
# WORKFLOW INTEGRATION
trigger-scenarios:
  - New feature PR
  - Regression bug postmortem
input-contract:
  - name: target_module
    type: string
    required: true
    description: Module or feature name
output-contract:
  - name: tests
    type: files
    location: file
    description: Generated test files
  - name: coverage_report
    type: markdown
    location: stdout
    description: Diff in coverage and risk areas
upstream-agents: []
downstream-agents:
  - CI/CD
persona: Diligent test engineer
# EVALUATION & ADOPTION
success-metrics: ['Coverage delta per PR', 'Defects caught before release']
time-to-value: Per PR within minutes
adoption-prerequisites: ['Pipeline permissions', 'Test environment']
learning-curve: moderate
# GOVERNANCE
owner: "AX&E Engineering"
last-validated: 2026-01-21
changelog: ['0.1.0: Initial']
deprecation-policy: N/A
---
