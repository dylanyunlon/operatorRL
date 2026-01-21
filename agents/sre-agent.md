---
name: SRE Agent
version: 0.1.0
description: Self-serve live site incident assistant providing 24×7 monitoring with proactive alerts; closes alerting gaps with recommendations.
category: orchestrator
maturity: experimental
owner: AX&E Engineering
last-validated: 2026-01-21
---

# SRE Agent

> Self-serve live site incident assistant providing 24×7 monitoring with proactive alerts; closes alerting gaps with recommendations.

| Property | Value |
|----------|-------|
| **Version** | 0.1.0 |
| **Category** | orchestrator |
| **Maturity** | 🧪 experimental |
| **Owner** | AX&E Engineering |
| **Orchestration Role** | coordinator |

## Related Agents

- [Release Freshness Agent](release-freshness-agent.md)
- [Zero Production Touch](zero-production-touch.md)

---

## Capabilities

### Tools
| Tool | Description |
|------|-------------|
| `icm_api` | IcM incident management API |
| `geneva_metrics` | Azure Monitor (Geneva) metrics |
| `kusto_query` | Kusto query execution |
| `runbook_executor` | Execute runbooks |
| `teams_notifier` | Send Teams notifications |

### Integrations
- IcM Agent Studio
- SRE Portal
- Azure Monitor (Geneva)

### Context Files
- `sev-definitions.md`
- `oncall-rotations.md`

---

## Risk Assessment

| Risk Factor | Level |
|-------------|-------|
| **Autonomy Level** | semi-autonomous |
| **Blast Radius** | external-system |
| **Reversibility** | partially |
| **Data Sensitivity** | internal-only |
| **Cost Profile** | variable |

### Human Checkpoints
> Points where human approval is required before proceeding.

- [ ] Before auto-escalation
- [ ] Before incident closure

### Failure Modes
> Known ways this agent can fail.

- False positives/alert fatigue
- Missed correlated signals
- Improper escalation routing

---

## Workflow Integration

### Trigger Scenarios
> When to invoke this agent.

- Anomalies detected
- New IcM created
- Runbook recommendation needed

### Input Contract

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `signal` | json | ✅ | Alert payload or IcM event |

### Output Contract

| Name | Type | Location | Description |
|------|------|----------|-------------|
| `triage_summary` | markdown | stdout | What happened, impact, hypothesis, next steps |
| `actions` | json | file | Suggested/run runbooks with parameters |

### Agent Flow

```
┌─────────────────────────┐     ┌───────────┐     ┌─────────────────────┐
│ Release Freshness Agent │ ──▶ │ SRE Agent │ ──▶ │ On-call Engineer    │
└─────────────────────────┘     └───────────┘     │ Incident Postmortem │
                                                   └─────────────────────┘
```

**Persona:** Calm, evidence-driven responder

---

## Evaluation & Adoption

### Success Metrics
- ✅ MTTA/MTTR reduction
- ✅ Lower page volume with same coverage

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | Immediate on alert |
| **Learning Curve** | moderate |

### Prerequisites
- IcM access
- Geneva/Kusto query permissions

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
| 0.1.0 | Seed spec |
