---
name: DRI Report Agent
version: 1.0.0
description: Automates DRI report generation; saves hours of manual reporting for DRIs across AX&E Engineering.
category: analyst
maturity: stable
owner: AX&E Engineering
last-validated: 2026-01-21
---

# DRI Report Agent

> Automates DRI report generation; saves hours of manual reporting for DRIs across AX&E Engineering.

## 🎯 Vision

**Zero manual DRI reports** — DRIs complete their routine, invoke the agent, and get a ready-to-share report. No more hours spent formatting and compiling data.

### Value Proposition

| Metric | Before | After |
|--------|--------|-------|
| Time per DRI report | Hours | Minutes |
| DRIs across AX&E | Many | All automated |
| Report consistency | Variable | Standardized |

| Property | Value |
|----------|-------|
| **Version** | 1.0.0 |
| **Category** | analyst |
| **Maturity** | 🟢 stable |
| **Owner** | AX&E Engineering |
| **Orchestration Role** | worker |

## Related Agents

- [S360 Agent](s360-agent.md)
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
| `dri_report` | markdown | stdout | Generated DRI report ready to share |
| `dashboard_url` | url | stdout | Published dashboard URL |

### Agent Flow

```
┌─────────────────┐     ┌──────────────────┐     ┌───────────────────┐
│ DRI completes   │ ──▶ │ DRI Report Agent │ ──▶ │ Report ready to   │
│ routine/tasks   │     └──────────────────┘     │ share with team   │
└─────────────────┘                              └───────────────────┘
```

**Persona:** Efficient, accurate report generator

---

## Evaluation & Adoption

### Success Metrics
- ✅ 100% automation of DRI reports
- ✅ Report preparation time < 10 minutes
- ✅ Hours saved per DRI per week

### Current Rollout Status

| Phase | Status | Details |
|-------|--------|--------|
| Knowledge sharing | ✅ Done | Sessions completed with multiple teams |
| Ecosystems Engineering | 🔄 Onboarding | Currently adopting |
| Feedback collection | 🔄 Active | Gathering input from early adopters |
| AX&E Engineering rollout | 🔜 Next | Full rollout after feedback incorporated |

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | Same day once datasets wired |
| **Learning Curve** | minimal |

### Prerequisites
- Power BI workspace and dataset refresh permissions
- DRI routine data sources configured

---

## Governance

| Field | Value |
|-------|-------|
| **Owner** | AX&E Engineering |
| **Last Validated** | 2026-01-21 |
| **Deprecation Policy** | N/A — actively maintained |

### Changelog
| Version | Notes |
|---------|-------|
| 1.0.0 | Renamed from FUN Report Agent to DRI Report Agent; stable release |
| 0.1.0 | Initial (as FUN Report Agent) |
