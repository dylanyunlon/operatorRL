---
name: Onboarding Agent
version: 0.1.0
description: Reduces onboarding time by generating key engineering artifacts from existing wiki/SharePoint/code and auto-creating initial ADO items.
category: capture
maturity: experimental
owner: AX&E Engineering
last-validated: 2026-01-21
---

# Onboarding Agent

> Reduces onboarding time by generating key engineering artifacts from existing wiki/SharePoint/code and auto-creating initial ADO items.

| Property | Value |
|----------|-------|
| **Version** | 0.1.0 |
| **Category** | capture |
| **Maturity** | 🧪 experimental |
| **Owner** | AX&E Engineering |
| **Orchestration Role** | worker |

## Related Agents

- [Planning Agent](planning-agent.md)
- [Design Review Agent](design-review-agent.md)

---

## Capabilities

### Tools
| Tool | Description |
|------|-------------|
| `sharepoint_reader` | Read SharePoint content |
| `repo_reader` | Read repository contents |
| `ado_api` | Azure DevOps API integration |
| `doc_summarizer` | Summarize documentation |

### Integrations
- SharePoint
- GitHub/ADO Repos
- Azure DevOps

### Context Files
- `onboarding-template.md`
- `team-handbook.md`

---

## Risk Assessment

| Risk Factor | Level |
|-------------|-------|
| **Autonomy Level** | guided |
| **Blast Radius** | external-system |
| **Reversibility** | fully |
| **Data Sensitivity** | internal-only |
| **Cost Profile** | minimal |

### Human Checkpoints
> Points where human approval is required before proceeding.

- [ ] Before publishing onboarding guide
- [ ] Before creating ADO onboarding items

### Failure Modes
> Known ways this agent can fail.

- Outdated source materials
- Non-standard team practices

---

## Workflow Integration

### Trigger Scenarios
> When to invoke this agent.

- New hire joins
- Team rotation

### Input Contract

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `team_sources` | string[] | ✅ | Links to wikis/repos |

### Output Contract

| Name | Type | Location | Description |
|------|------|----------|-------------|
| `onboarding_guide` | markdown | stdout | Role-specific starter guide |

### Agent Flow

```
┌────────────────┐     ┌──────────────────┐     ┌───────────┐
│ Planning Agent │ ──▶ │ Onboarding Agent │ ──▶ │ New Hire  │
└────────────────┘     └──────────────────┘     │ Manager   │
                                                 └───────────┘
```

**Persona:** Supportive coach with curated, minimal path

---

## Evaluation & Adoption

### Success Metrics
- ✅ Time-to-first-PR
- ✅ Time-to-environment-setup

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | Same day |
| **Learning Curve** | minimal |

### Prerequisites
- Access to team repositories and wikis

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
