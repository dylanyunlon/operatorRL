---
name: Release Freshness Agent
version: 0.1.0
description: Tracks production freshness and automatically follows up on delayed deployments and pending changes across services.
category: analyst
maturity: beta
owner: AX&E Engineering
last-validated: 2026-01-21
---

# Release Freshness Agent

> Tracks production freshness and automatically follows up on delayed deployments and pending changes across services.

| Property | Value |
|----------|-------|
| **Version** | 0.1.0 |
| **Category** | analyst |
| **Maturity** | 🟡 beta |
| **Owner** | AX&E Engineering |
| **Orchestration Role** | worker |

## Related Agents

- [SRE Agent](sre-agent.md)
- [Zero Production Touch](zero-production-touch.md)
- [Planning Agent](planning-agent.md)

---

## Capabilities

### Tools
| Tool | Description |
|------|-------------|
| `ado_release_api` | ADO release pipeline API |
| `git_diff_checker` | Check git diffs |
| `power_bi` | Power BI dashboard creation |
| `notifier` | Send notifications |

### Integrations
- ADO Pipelines
- Git
- Power BI

### Context Files
- `service-catalog.md`
- `release-policies.md`

---

## Risk Assessment

| Risk Factor | Level |
|-------------|-------|
| **Autonomy Level** | semi-autonomous |
| **Blast Radius** | external-system |
| **Reversibility** | fully |
| **Data Sensitivity** | internal-only |
| **Cost Profile** | moderate |

### Human Checkpoints
> Points where human approval is required before proceeding.

- [ ] Before posting broad follow-ups
- [ ] Before escalating stale deployments to leadership

### Failure Modes
> Known ways this agent can fail.

- False staleness due to hotfix branches
- Missing service mapping

---

## Workflow Integration

### Trigger Scenarios
> When to invoke this agent.

- Daily freshness scan
- Missed SLA window

### Input Contract

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `service_map` | file | ✅ | List of repos/pipelines to monitor |

### Output Contract

| Name | Type | Location | Description |
|------|------|----------|-------------|
| `freshness_report` | markdown | stdout | Services behind, suggested follow-ups |

### Agent Flow

```
┌────────────────┐     ┌─────────────────────────┐     ┌────────────────┐
│ Planning Agent │ ──▶ │ Release Freshness Agent │ ──▶ │ SRE Agent      │
└────────────────┘     └─────────────────────────┘     │ Service Owners │
                                                        └────────────────┘
```

**Persona:** Data-first release analyst

---

## Evaluation & Adoption

### Success Metrics
- ✅ Reduction in stale deployments
- ✅ Time-to-follow-up < 24h

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | Within first scan cycle |
| **Learning Curve** | minimal |

### Prerequisites
- Access to repos and pipeline metadata
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
| 0.1.0 | Initial |
