---
name: Design Review Agent
version: 0.1.0
description: Provides early feedback on design, architecture, and security using historical data so designs improve before peer review.
category: hybrid
maturity: beta
owner: AX&E Engineering
last-validated: 2026-01-21
---

# Design Review Agent

> Provides early feedback on design, architecture, and security using historical data so designs improve before peer review.

| Property | Value |
|----------|-------|
| **Version** | 0.1.0 |
| **Category** | hybrid |
| **Maturity** | 🟡 beta |
| **Owner** | AX&E Engineering |
| **Orchestration Role** | standalone |

## Related Agents

- [Planning Agent](planning-agent.md)
- [Unit & Scenario Testing Agent](unit-and-scenario-testing-agent.md)

---

## Capabilities

### Tools
| Tool | Description |
|------|-------------|
| `repo_reader` | Read repository contents |
| `threat_model_rules` | Apply threat modeling rules |
| `static_analysis` | Run static code analysis |
| `office365_search` | Search Office 365 content |
| `doc_reviewer` | Review documentation |

### Integrations
- GitHub
- Azure DevOps Repos
- SharePoint
- Teams

### Context Files
- `design-checklist.md`
- `security-baselines.md`

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

- [ ] Before filing blocking issues
- [ ] Before recommending architectural changes

### Failure Modes
> Known ways this agent can fail.

- Out-of-context recommendations
- Overly generic guidance
- Missed historical precedent

---

## Workflow Integration

### Trigger Scenarios
> When to invoke this agent.

- Draft design doc ready
- Pre-PR design gate

### Input Contract

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `design_doc` | file | ✅ | Design document (.md/.docx/.pptx) |
| `repo_links` | string[] | ❌ | Relevant code locations |

### Output Contract

| Name | Type | Location | Description |
|------|------|----------|-------------|
| `review_findings` | markdown | stdout | Actionable strengths, risks, and questions |

### Agent Flow

```
┌─────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│ Planning Agent  │ ──▶ │ Design Review Agent │ ──▶ │ Implementation Agent│
└─────────────────┘     └─────────────────────┘     │ Security Review     │
                                                     └─────────────────────┘
```

**Persona:** Thoughtful architect citing prior art and risks

---

## Evaluation & Adoption

### Success Metrics
- ✅ Actionable findings accepted by team
- ✅ Reduced rework during implementation

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | 5-15 minutes |
| **Learning Curve** | moderate |

### Prerequisites
- Access to design repo/wiki
- Security baseline documents

---

## Governance

| Field | Value |
|-------|-------|
| **Owner** | AX&E Engineering |
| **Last Validated** | 2026-01-21 |
| **Deprecation Policy** | Superseded by Org Design LLM when available |

### Changelog
| Version | Notes |
|---------|-------|
| 0.1.0 | Initial spec from deck |
