---
name: Unit & Scenario Testing Agent
version: 1.0.0
description: AI-assisted unit and scenario test generation to improve coverage across AX&E Engineering.
category: analyst
maturity: beta
owner: AX&E Engineering
last-validated: 2026-01-21
---

# Unit & Scenario Testing Agent

> AI-assisted unit and scenario test generation to improve coverage across AX&E Engineering.

## ⭐ Sponsored Initiative

| | |
|---|---|
| **Sponsor** | AX&E Engineering |
| **Status** | 🚀 **Rolling out org-wide** |
| **Baselines** | ✅ Established |
| **Plan** | ✅ In place |

This agent is **blessed by AX&E Engineering** and actively rolling out across all engineering teams.

| Property | Value |
|----------|-------|
| **Version** | 1.0.0 |
| **Category** | analyst |
| **Maturity** | 🟡 beta (org-wide rollout in progress) |
| **Owner** | AX&E Engineering (Sponsor) |
| **Orchestration Role** | worker |

## Related Agents

- [Accessibility Agent](accessibility-agent.md)
- [Design Review Agent](design-review-agent.md)

---

## Capabilities

### Tools
| Tool | Description |
|------|-------------|
| `test_generator` | Generate test cases |
| `playwright` | Playwright browser automation |
| `coverage_analyzer` | Analyze code coverage |
| `pipeline_integration` | Integrate with CI/CD pipelines |

### Integrations
- ADO Pipelines
- Playwright
- Git

### Context Files
- `test-guidelines.md`
- `critical-scenarios.md`

---

## Risk Assessment

| Risk Factor | Level |
|-------------|-------|
| **Autonomy Level** | guided |
| **Blast Radius** | workspace |
| **Reversibility** | fully |
| **Data Sensitivity** | internal-only |
| **Cost Profile** | moderate |

### Human Checkpoints
> Points where human approval is required before proceeding.

- [ ] Before committing generated tests
- [ ] Before enabling tests in CI gate

### Failure Modes
> Known ways this agent can fail.

- Flaky tests
- Overfitting to current UI state

---

## Workflow Integration

### Trigger Scenarios
> When to invoke this agent.

- New feature PR
- Regression bug postmortem

### Input Contract

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `target_module` | string | ✅ | Module or feature name |

### Output Contract

| Name | Type | Location | Description |
|------|------|----------|-------------|
| `tests` | files | file | Generated test files |
| `coverage_report` | markdown | stdout | Diff in coverage and risk areas |

### Agent Flow

```
┌─────────────────────────────────┐     ┌─────────┐
│ Unit & Scenario Testing Agent   │ ──▶ │ CI/CD   │
└─────────────────────────────────┘     └─────────┘
```

**Persona:** Diligent test engineer

---

## Evaluation & Adoption

### Success Metrics
- ✅ Coverage delta per PR
- ✅ Defects caught before release
- ✅ Baseline coverage established
- 🔄 Coverage improvement trending up

### Rollout Status

| Scope | Status |
|-------|--------|
| **Baselines** | ✅ Established |
| **Rollout plan** | ✅ In place |
| **Org-wide adoption** | 🔄 In progress — going well |
| **All engineers** | 🔜 Target |

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | Per PR within minutes |
| **Learning Curve** | moderate |

### Prerequisites
- Pipeline permissions
- Test environment

---

## Governance

| Field | Value |
|-------|-------|
| **Owner** | AX&E Engineering |
| **Last Validated** | 2026-01-21 |
| **Deprecation Policy** | N/A |

### Changelog
| Version | Notes |
|---------|-------|
| 1.0.0 | Org-wide rollout; blessed by AX&E Engineering |
| 0.1.0 | Initial |
