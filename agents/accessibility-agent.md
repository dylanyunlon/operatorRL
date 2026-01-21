---
name: Accessibility Agent
version: 0.2.0
description: Automates accessibility checks and bug fixing at PR time and in ADO workflows; catches a11y issues before merge and proposes fixes.
category: hybrid
maturity: beta
owner: AX&E Engineering
last-validated: 2026-01-21
---

# Accessibility Agent

> Automates accessibility checks and bug fixing at PR time and in ADO workflows; catches a11y issues before merge and proposes fixes.

## 🎯 Vision

**Shift-left accessibility** — Integrate directly into the PR workflow so a11y issues are caught and fixed *before* code merges, not discovered later in production or via bug reports.

### Roadmap
- ✅ ADO WIT analysis and reproduction
- ✅ Axe-core scanning and fix suggestions
- 🔄 PR integration (GitHub/ADO) — catch issues at review time
- 🔜 Auto-suggest fixes as PR comments
- 🔜 Expand rollout beyond Ecosystems Engineering

| Property | Value |
|----------|-------|
| **Version** | 0.1.0 |
| **Category** | hybrid |
| **Maturity** | 🟡 beta |
| **Owner** | AX&E Engineering |
| **Orchestration Role** | worker |

## Related Agents

| Agent | Relationship |
|-------|-------------|
| [Unit & Scenario Testing Agent](unit-and-scenario-testing-agent.md) | 🔍 **Exploring integration** — a11y checks as part of test suite |
| [S360 Agent](s360-agent.md) | Upstream — S360 triages a11y work items |

---

## Capabilities

### Tools
| Tool | Description |
|------|-------------|
| `playwright_browser` | Browser automation with Playwright |
| `axe_core_scan` | Axe-core accessibility scanning |
| `ado_api` | Azure DevOps API integration |
| `git_pr_creator` | Create pull requests |
| `pr_reviewer` | 🔜 Review PRs for a11y issues |

### Integrations
- Azure DevOps (WITs, PRs)
- GitHub (PRs, Actions)
- Browser Automation (Playwright)

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

- **PR opened/updated** — scan for a11y issues before merge *(target state)*
- New a11y bug created in ADO
- Regression detected in CI

### Input Contract

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `work_item` | json | ✅ | ADO bug with repro info |

### Output Contract

| Name | Type | Location | Description |
|------|------|----------|-------------|
| `fix_suggestion` | markdown | stdout | Proposed code diff and rationale |
| `pull_request` | url | stdout | Optional auto-created PR |
| `pr_comments` | json | PR | 🔜 Inline suggestions on PR files |

### Agent Flow

```
┌───────────┐     ┌─────────────────────┐     ┌───────────┐
│ S360 Agent│ ──▶ │ Accessibility Agent │ ──▶ │ SWE Agent │
└───────────┘     └─────────────────────┘     │ QA        │
                                               └───────────┘
```

**Persona:** Helpful fixer focused on standards compliance

---

## Evaluation & Adoption

### Success Metrics
- ✅ Cycle time from bug to PR
- ✅ A11y defect escape rate
- 🔜 A11y issues caught at PR time (vs. post-merge)

### Current Adoption
- ✅ **Ecosystems Engineering** — actively using
- 🔜 **Expanding to other teams** — rollout planned

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | Hours for first fix |
| **Learning Curve** | moderate |

### Prerequisites
- Repo write permissions
- Test environment
- PR webhook access (for PR integration)

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
| 0.2.0 | Added PR integration vision, expansion roadmap |
| 0.1.0 | Initial |
