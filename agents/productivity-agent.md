---
name: Productivity Agent
version: 0.1.0
description: Automates measurement of coding productivity with reliable metrics and dashboards.
category: analyst
maturity: experimental
owner: AX&E Engineering
last-validated: 2026-01-21
---

# Productivity Agent

> Automates measurement of coding productivity with reliable metrics and dashboards.

| Property | Value |
|----------|-------|
| **Version** | 0.1.0 |
| **Category** | analyst |
| **Maturity** | 🧪 experimental |
| **Owner** | AX&E Engineering |
| **Orchestration Role** | worker |

## Related Agents

- [Planning Agent](planning-agent.md)
- [FUN Report Agent](fun-report-agent.md)

---

## Capabilities

### Tools
| Tool | Description |
|------|-------------|
| `git_metrics` | Git repository metrics |
| `ado_activity` | Azure DevOps activity tracking |
| `telemetry_aggregator` | Aggregate telemetry data |
| `power_bi` | Power BI dashboard creation |

### Integrations
- Git
- Azure DevOps
- Power BI

### Context Files
- `metric-definitions.md`

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

- [ ] Before publishing individual-level metrics
- [ ] Before sharing team comparisons externally

### Failure Modes
> Known ways this agent can fail.

- Gaming or misinterpretation of metrics
- Inconsistent repository mapping

---

## Workflow Integration

### Trigger Scenarios
> When to invoke this agent.

- Quarterly/Monthly productivity review

### Input Contract

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `repos` | string[] | ✅ | Repositories to analyze |

### Output Contract

| Name | Type | Location | Description |
|------|------|----------|-------------|
| `productivity_dashboard` | url | stdout | Published dashboard URL |

### Agent Flow

```
┌──────────────────────┐     ┌───────────────────┐
│ Productivity Agent   │ ──▶ │ Leadership Review │
└──────────────────────┘     └───────────────────┘
```

**Persona:** Neutral analyst focused on outcomes not vanity metrics

---

## Evaluation & Adoption

### Success Metrics
- ✅ Agreement on metric definitions
- ✅ Adoption across teams

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | 1-2 weeks for baseline |
| **Learning Curve** | moderate |

### Prerequisites
- Access to repos and ADO

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
