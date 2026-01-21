---
name: Accessibility Agent
version: 0.1.0
description: Automates accessibility checks and bug fixing: analyzes ADO WITs, reproduces issues, identifies problems, and proposes code fixes; long term auto-PR via SWE Agent.
category: hybrid
maturity: beta
owner: AX&E Engineering
last-validated: 2026-01-21
---

# Accessibility Agent

> Automates accessibility checks and bug fixing: analyzes ADO WITs, reproduces issues, identifies problems, and proposes code fixes; long term auto-PR via SWE Agent.

| Property | Value |
|----------|-------|
| **Version** | 0.1.0 |
| **Category** | hybrid |
| **Maturity** | 🟡 beta |
| **Owner** | AX&E Engineering |
| **Orchestration Role** | worker |

## Related Agents

- [SFI Agent](sfi-agent.md)
- [Unit & Scenario Testing Agent](unit-and-scenario-testing-agent.md)

---

## Capabilities

### Tools
| Tool | Description |
|------|-------------|
| `playwright_browser` | Browser automation with Playwright |
| `axe_core_scan` | Axe-core accessibility scanning |
| `ado_api` | Azure DevOps API integration |
| `git_pr_creator` | Create pull requests |

### Integrations
- Azure DevOps
- GitHub
- Browser Automation

### Context Files
- `a11y-standards.md`
- `browser-selectors.md`

---

## Risk Assessment

| Risk Factor | Level |
|-------------|-------|
| **Autonomy Level** | guided |
| **Blast Radius** | workspace |
| **Reversibility** | partially |
| **Data Sensitivity** | internal-only |
| **Cost Profile** | moderate |

### Human Checkpoints
> Points where human approval is required before proceeding.

- [ ] Before committing fixes
- [ ] Before creating PR

### Failure Modes
> Known ways this agent can fail.

- Flaky reproduction steps
- False positives from scanners
- Incorrect selectors

---

## Workflow Integration

### Trigger Scenarios
> When to invoke this agent.

- New a11y bug created
- Regression detected

### Input Contract

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `work_item` | json | ✅ | ADO bug with repro info |

### Output Contract

| Name | Type | Location | Description |
|------|------|----------|-------------|
| `fix_suggestion` | markdown | stdout | Proposed code diff and rationale |
| `pull_request` | url | stdout | Optional auto-created PR |

### Agent Flow

```
┌───────────┐     ┌─────────────────────┐     ┌───────────┐
│ SFI Agent │ ──▶ │ Accessibility Agent │ ──▶ │ SWE Agent │
└───────────┘     └─────────────────────┘     │ QA        │
                                               └───────────┘
```

**Persona:** Helpful fixer focused on standards compliance

---

## Evaluation & Adoption

### Success Metrics
- ✅ Cycle time from bug to PR
- ✅ A11y defect escape rate

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | Hours for first fix |
| **Learning Curve** | moderate |

### Prerequisites
- Repo write permissions
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
| 0.1.0 | Initial |
