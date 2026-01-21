---
name: Release Freshness Agent
version: 0.1.0
description: Dashboard for tracking production freshness; future agent will automate follow-ups with service owners.
category: analyst
maturity: experimental
owner: AX&E Engineering
last-validated: 2026-01-21
---

# Release Freshness Agent

> Dashboard for tracking production freshness; future agent will automate follow-ups with service owners.

## 🎯 Vision

**From manual follow-up to automated nudges** — Today we have dashboards. Tomorrow an agent will do the follow-up work automatically.

### Tool vs Agent: Where We Are

| Component | Status | Description |
|-----------|--------|-------------|
| **Dashboard (Tool)** | ✅ Live | Shows freshness delta for each production pipeline |
| **Agent (AI)** | 🔜 Not started | Automates follow-up with owners |

### Current Manual Process

```
┌──────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│ Dashboard shows   │     │ Human looks at it   │     │ Human follows up    │
│ freshness delta   │ ──▶ │ identifies issues   │ ──▶ │ with service owners │
└──────────────────┘     └─────────────────────┘     └─────────────────────┘
         ✅                      ❌ Manual                    ❌ Manual
```

### Future State (Agent)

```
┌──────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│ Dashboard shows   │     │ Agent detects       │     │ Agent notifies      │
│ freshness delta   │ ──▶ │ large deltas        │ ──▶ │ owners, tracks      │
└──────────────────┘     └─────────────────────┘     │ resolution          │
         ✅                      🔜 AI                   └─────────────────────┘
                                                              🔜 AI
```

### What the Agent Will Do

| Capability | Description |
|------------|-------------|
| **Detect** | Identify pipelines with large commit-to-production delta |
| **Investigate** | Check if there's a valid reason (hotfix branch, planned hold) |
| **Notify** | Message service owners with specific details |
| **Follow up** | Track acknowledgment, remind if no action |
| **Escalate** | Alert leadership if SLA exceeded |

| Property | Value |
|----------|-------|
| **Version** | 0.1.0 |
| **Category** | analyst |
| **Maturity** | 🧪 experimental (dashboard live, agent not started) |
| **Owner** | AX&E Engineering |
| **Orchestration Role** | worker |

## Related Agents

- [SRE Agent](sre-agent.md)
- [Zero Production Touch](zero-production-touch.md)
- [Planning Agent](planning-agent.md)

---

## Capabilities

### Tools
| Tool | Status | Description |
|------|--------|-------------|
| `ado_release_api` | ✅ In use | ADO release pipeline API |
| `git_diff_checker` | ✅ In use | Check git diffs for freshness |
| `power_bi` | ✅ In use | Freshness dashboard |
| `notifier` | 🔜 Planned | Send notifications to owners |
| `owner_lookup` | 🔜 Planned | Find service owner from pipeline |

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

**Today (Dashboard):**
- On-demand freshness check
- Weekly freshness review

**Future (Agent):**
- Daily automated scan
- Threshold exceeded alert
- SLA window approaching

### Input Contract

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `service_map` | file | ✅ | List of repos/pipelines to monitor |

### Output Contract

| Name | Type | Status | Description |
|------|------|--------|-------------|
| `freshness_dashboard` | url | ✅ Live | Power BI dashboard showing all pipelines |
| `freshness_report` | markdown | 🔜 Planned | Services behind, suggested follow-ups |
| `owner_notifications` | messages | 🔜 Planned | Automated messages to service owners |

### Agent Flow

```
┌──────────────────────────────────────────────────────────────────────────┐
│  TODAY: Dashboard → Human review → Manual follow-up            │
│  FUTURE: Dashboard → Agent detects → Auto-notify → Track       │
└──────────────────────────────────────────────────────────────────────────┘
```

**Persona:** Persistent release tracker that doesn't let stale deployments slip

---

## Evaluation & Adoption

### Success Metrics

**Dashboard (Today):**
- ✅ Visibility into all pipeline freshness
- ✅ Single source of truth for deployment status

**Agent (Future):**
- 🔜 Reduction in stale deployments
- 🔜 Time-to-follow-up < 24h (automated)
- 🔜 Human hours saved on manual follow-up

### Current Status

| Component | Status |
|-----------|--------|
| Freshness dashboard | ✅ Live and working |
| Pipeline-to-commit delta calculation | ✅ Working |
| Service owner mapping | 🔜 Needed for automation |
| Notification automation | 🔜 Not started |
| Follow-up tracking | 🔜 Not started |

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | Dashboard: immediate \| Agent: TBD |
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
