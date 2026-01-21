---
# METADATA
name: Accessibility Agent
version: 0.1.0
description: Automates accessibility checks and bug fixing: analyzes ADO WITs, reproduces issues, identifies problems, and proposes code fixes; long term auto-PR via SWE Agent.
category: hybrid
maturity: beta
supersedes: []
related-agents:
  - SFI Agent
  - Unit & Scenario Testing Agent
# CAPABILITIES
tools:
  - playwright_browser
  - axe_core_scan
  - ado_api
  - git_pr_creator
handoffs: []
integrations:
  - Azure DevOps
  - GitHub
  - Browser Automation
orchestration-role: worker
context-files:
  - a11y-standards.md
  - browser-selectors.md
# RISK ASSESSMENT
autonomy-level: guided
blast-radius: workspace
reversibility: partially
data-sensitivity: internal-only
human-checkpoints:
  - "Before committing fixes"
  - "Before creating PR"
cost-profile: moderate
failure-modes:
  - Flaky reproduction steps
  - False positives from scanners
  - Incorrect selectors
# WORKFLOW INTEGRATION
trigger-scenarios:
  - New a11y bug created
  - Regression detected
input-contract:
  - name: work_item
    type: json
    required: true
    description: ADO bug with repro info
output-contract:
  - name: fix_suggestion
    type: markdown
    location: stdout
    description: Proposed code diff and rationale
  - name: pull_request
    type: url
    location: stdout
    description: Optional auto-created PR
upstream-agents:
  - SFI Agent
downstream-agents:
  - SWE Agent
  - QA
persona: Helpful fixer focused on standards compliance
# EVALUATION & ADOPTION
success-metrics:
  - Cycle time from bug to PR
  - A11y defect escape rate
time-to-value: Hours for first fix
adoption-prerequisites:
  - Repo write permissions
  - Test environment
learning-curve: moderate
# GOVERNANCE
owner: "AX&E Engineering"
last-validated: 2026-01-21
changelog:
  - "0.1.0: Initial"
deprecation-policy: N/A
---
