---
name: Planning Agent
version: 0.1.0
description: Summarizes sprint plans, creates and updates Azure DevOps (ADO) items, and maintains hygiene; provides dashboards for alignment and status.
category: orchestrator
maturity: beta
owner: AX&E Engineering
last-validated: 2026-01-21
---

# Planning Agent

> Summarizes sprint plans, creates and updates Azure DevOps (ADO) items, and maintains hygiene; provides dashboards for alignment and status.

| Property | Value |
|----------|-------|
| **Version** | 0.1.0 |
| **Category** | orchestrator |
| **Maturity** | 🟡 beta |
| **Owner** | AX&E Engineering |
| **Orchestration Role** | coordinator |

## Related Agents

- [FUN Report Agent](fun-report-agent.md)
- [SFI Agent](sfi-agent.md)
- [Design Review Agent](design-review-agent.md)

---

## Capabilities

### Tools
| Tool | Description |
|------|-------------|
| `ado_api` | Azure DevOps API integration |
| `power_bi` | Power BI dashboard creation |
| `sharepoint_reader` | Read SharePoint content |
| `teams_notifier` | Send Teams notifications |
| `office365_search` | Search Office 365 content |

### Integrations
- Azure DevOps
- Power BI
- SharePoint
- Microsoft Teams

### Context Files
- `planning-rules.md`
- `ado-templates.md`

---

## Risk Assessment

| Risk Factor | Level |
|-------------|-------|
| **Autonomy Level** | semi-autonomous |
| **Blast Radius** | external-system |
| **Reversibility** | partially |
| **Data Sensitivity** | internal-only |
| **Cost Profile** | moderate |

### Human Checkpoints
> Points where human approval is required before proceeding.

- [ ] Before creating/updating ADO work items
- [ ] Before publishing organization-wide reports

### Failure Modes
> Known ways this agent can fail.

- Incorrect interpretation of sprint goals
- Duplicate or conflicting ADO updates
- Out-of-date data sources

---

## Workflow Integration

### Trigger Scenarios
> When to invoke this agent.

- Sprint planning and kickoff
- Mid-sprint hygiene checks
- End-of-sprint summary/reporting

### Input Contract

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `sprint_backlog` | files | ❌ | ADO queries/boards and backlog export |
| `sprint_goals` | string | ✅ | Natural language statement of goals |

### Output Contract

| Name | Type | Location | Description |
|------|------|----------|-------------|
| `hygiene_report` | markdown | stdout | Findings and recommended fixes |
| `ado_changes` | json | file | Created/updated work items with links |

### Agent Flow

```
┌─────────────┐     ┌────────────────┐     ┌─────────────────┐
│ FUN Report  │ ──▶ │ Planning Agent │ ──▶ │ FUN Report      │
└─────────────┘     └────────────────┘     │ SFI Agent       │
                                           └─────────────────┘
```

**Persona:** Pragmatic release coordinator focused on clarity and actionability

---

## Evaluation & Adoption

### Success Metrics
- ✅ < 10 minutes to produce a sprint summary
- ✅ Hygiene score improves week-over-week
- ✅ Reduction in manual edits to ADO

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | 5-10 minutes |
| **Learning Curve** | minimal |

### Prerequisites
- ADO project access
- Power BI workspace access

---

## Governance

| Field | Value |
|-------|-------|
| **Owner** | AX&E Engineering |
| **Last Validated** | 2026-01-21 |
| **Deprecation Policy** | 30-day notice with migration guidance |

### Changelog
| Version | Notes |
|---------|-------|
| 0.1.0 | Initial spec import from SDLC deck |
