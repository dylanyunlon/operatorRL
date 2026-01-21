---
name: FUN Report Agent
version: 0.1.0
description: Centralized reporting for LSI/SFI metrics; eliminates manual report creation for AX&E Engineering.
category: analyst
maturity: beta
owner: AX&E Engineering
last-validated: 2026-01-21
---

# FUN Report Agent

> Centralized reporting for LSI/SFI metrics; eliminates manual report creation for AX&E Engineering.

| Property | Value |
|----------|-------|
| **Version** | 0.1.0 |
| **Category** | analyst |
| **Maturity** | 🟡 beta |
| **Owner** | AX&E Engineering |
| **Orchestration Role** | worker |

## Related Agents

- [SFI Agent](sfi-agent.md)
- [Planning Agent](planning-agent.md)

---

## Capabilities

### Tools
| Tool | Description |
|------|-------------|
| `power_bi` | Power BI dashboard creation |
| `dataset_connector` | Connect to data sources |
| `scheduler` | Schedule report generation |

### Integrations
- Power BI
- SharePoint

### Context Files
- `report-definitions.md`

---

## Risk Assessment

| Risk Factor | Level |
|-------------|-------|
| **Autonomy Level** | guided |
| **Blast Radius** | external-system |
| **Reversibility** | fully |
| **Data Sensitivity** | internal-only |
| **Cost Profile** | moderate |

### Human Checkpoints
> Points where human approval is required before proceeding.

- [ ] Before publishing org dashboards
- [ ] Before refreshing underlying datasets

### Failure Modes
> Known ways this agent can fail.

- Stale datasets
- Misaligned definitions across teams

---

## Workflow Integration

### Trigger Scenarios
> When to invoke this agent.

- End-of-week reporting
- Monthly business review

### Input Contract

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `datasets` | files | ✅ | Data sources / semantic models |

### Output Contract

| Name | Type | Location | Description |
|------|------|----------|-------------|
| `fun_dashboard` | url | stdout | Published dashboard URL |

### Agent Flow

```
┌────────────────┐     ┌──────────────────┐     ┌───────────────────┐
│ SFI Agent      │ ──▶ │ FUN Report Agent │ ──▶ │ Leadership Review │
│ Planning Agent │     └──────────────────┘     └───────────────────┘
└────────────────┘
```

**Persona:** Clear, concise reporter

---

## Evaluation & Adoption

### Success Metrics
- ✅ 100% automation of weekly report
- ✅ Report preparation time < 10 minutes

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | Same day once datasets wired |
| **Learning Curve** | minimal |

### Prerequisites
- Power BI workspace and dataset refresh permissions

---

## Governance

| Field | Value |
|-------|-------|
| **Owner** | AX&E Engineering |
| **Last Validated** | 2026-01-21 |
| **Deprecation Policy** | Replace with Org Analytics when ready |

### Changelog
| Version | Notes |
|---------|-------|
| 0.1.0 | Initial |
