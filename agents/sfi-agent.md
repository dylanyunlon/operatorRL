---
name: SFI Agent
version: 0.1.0
description: Manages SFI work with two specialized agents integrated into the SWE agent; tracks ~6 KPIs and automates ADO WIT creation.
category: hybrid
maturity: beta
owner: AX&E Engineering
last-validated: 2026-01-21
---

# SFI Agent

> Manages SFI work with two specialized agents integrated into the SWE agent; tracks ~6 KPIs and automates ADO WIT creation.

| Property | Value |
|----------|-------|
| **Version** | 0.1.0 |
| **Category** | hybrid |
| **Maturity** | 🟡 beta |
| **Owner** | AX&E Engineering |
| **Orchestration Role** | coordinator |

## Related Agents

- [DRI Report Agent](dri-report-agent.md)
- [Planning Agent](planning-agent.md)
- [Accessibility Agent](accessibility-agent.md)

---

## Capabilities

### Tools
| Tool | Description |
|------|-------------|
| `ado_api` | Azure DevOps API integration |
| `kpi_calculator` | Calculate KPI metrics |
| `power_bi` | Power BI dashboard creation |
| `swe_agent_bridge` | Bridge to SWE Agent |

### Integrations
- Azure DevOps
- Power BI

### Context Files
- `sfi-kpis.md`
- `ado-wit-templates.md`

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

- [ ] Before mass WIT creation
- [ ] Before KPI publication

### Failure Modes
> Known ways this agent can fail.

- Incorrect KPI mapping
- Duplicate work items
- Misclassified SFI types

---

## Workflow Integration

### Trigger Scenarios
> When to invoke this agent.

- Weekly/Monthly SFI review
- SFI intake events

### Input Contract

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `sfi_sources` | files | ✅ | Data sources / exports |

### Output Contract

| Name | Type | Location | Description |
|------|------|----------|-------------|
| `kpi_report` | markdown | stdout | Trend, goals, and deltas |
| `ado_wits` | json | file | Created/updated ADO items |

### Agent Flow

```
┌─────────────┐     ┌───────────┐     ┌───────────┐
│ DRI Report  │ ──▶ │ SFI Agent │ ──▶ │ SWE Agent │
└─────────────┘     └───────────┘     └───────────┘
```

**Persona:** Operationally minded program manager

---

## Evaluation & Adoption

### Success Metrics
- ✅ KPI freshness within 24h
- ✅ Reduction in manual SFI processing

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | 1-2 days initial, then hours |
| **Learning Curve** | moderate |

### Prerequisites
- ADO area path permissions
- Power BI workspace

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
| 0.1.0 | First draft |
