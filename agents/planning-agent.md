---
name: Planning Agent
version: 0.2.0
description: Helps engineers stay productive by automating ADO hygiene, Power Operating Model compliance, and day-to-day tracking tasks.
category: orchestrator
maturity: beta
owner: AX&E Engineering
last-validated: 2026-01-21
---

# Planning Agent

> Helps engineers stay productive by automating ADO hygiene, Power Operating Model compliance, and day-to-day tracking tasks.

## 🎯 Vision

**Engineer velocity, not overhead** — Automate the tracking busywork so engineers can focus on building. This agent handles the "hundreds of things" that need attention: dates, assignments, goals, hygiene, and compliance.

> 📝 **Naming consideration:** Is "Planning Agent" the right name? This is really about **engineering productivity** and **tracking**, not product planning. Consider: *Tracking Agent*, *Velocity Agent*, *Hygiene Agent*?

### What This Agent Helps With

| Area | Examples |
|------|----------|
| **Power Operating Model** | Start dates, end dates, project goals, work assignments |
| **ADO Hygiene** | Missing fields, stale items, incorrect states, orphaned work |
| **Sprint Tracking** | Summaries, status, blockers, carryover |
| **Compliance** | Required fields, area paths, iteration alignment |

### Current State

| What's Working | What's Coming |
|----------------|---------------|
| ✅ Core hygiene checks | 🔜 More Power Operating Model rules |
| ✅ Sprint summaries | 🔜 Proactive notifications |
| ✅ ADO updates | 🔜 Cross-team visibility |
| ✅ Basic dashboards | 🔜 Advanced analytics |

| Property | Value |
|----------|-------|
| **Version** | 0.2.0 |
| **Category** | orchestrator |
| **Maturity** | 🟡 beta |
| **Owner** | AX&E Engineering |
| **Orchestration Role** | coordinator |

## Related Agents

- [DRI Report Agent](dri-report-agent.md)
- [S360 Agent](s360-agent.md)
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
- `power-operating-model-rules.md` — Required fields and compliance rules
- `hygiene-rules.md` — ADO hygiene standards
- `ado-templates.md` — Work item templates

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

- Daily/weekly hygiene checks
- Sprint planning and kickoff
- Mid-sprint health check
- Power Operating Model compliance review
- Before leadership sync (quick status)

### Input Contract

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `ado_query` | string | ✅ | ADO query or area path to analyze |
| `check_type` | enum | ❌ | `hygiene` \| `compliance` \| `summary` \| `all` |

### Output Contract

| Name | Type | Location | Description |
|------|------|----------|-------------|
| `hygiene_report` | markdown | stdout | Issues found and recommended fixes |
| `compliance_report` | markdown | stdout | Power Operating Model gaps |
| `ado_changes` | json | file | Created/updated work items with links |

### Agent Flow

```
┌─────────────┐     ┌────────────────┐     ┌─────────────────┐
│ DRI Report │ ──▶ │ Planning Agent │ ──▶ │ DRI Report      │
└─────────────┘     └────────────────┘     │ S360 Agent      │
                                           └─────────────────┘
```

**Persona:** Efficient engineering assistant focused on reducing busywork

---

## Evaluation & Adoption

### Success Metrics
- ✅ < 10 minutes to produce a sprint summary
- ✅ Hygiene score improves week-over-week
- ✅ Reduction in manual ADO edits
- 🔜 Time saved per engineer per week
- 🔜 Power Operating Model compliance rate

### Adoption Status

| Team | Status |
|------|--------|
| **Localization** | ✅ Onboarded |
| **Learn** | 🔄 Onboarding |
| Other teams | 🔜 After initial feedback |

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
| 0.2.0 | Clarified focus on engineering productivity; added Power Operating Model; adoption status |
| 0.1.0 | Initial spec import from SDLC deck |
