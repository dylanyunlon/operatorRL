---
name: SRE Agent
version: 0.2.0
description: Self-serve live site incident assistant for Sev3s — automatically triage, run TSGs, and close incidents without DRI involvement.
category: orchestrator
maturity: experimental
owner: AX&E Engineering
last-validated: 2026-01-21
---

# SRE Agent

> Self-serve live site incident assistant for Sev3s — automatically triage, run TSGs, and close incidents without DRI involvement.

## 🎯 Vision

**Zero DRI hours on Sev3s** — When a Sev3 fires, the agent handles it end-to-end. DRIs focus on Sev1/Sev2 and feature work.

> 📝 **Naming consideration:** This is really about **Live Site Management**. Consider renaming to *Live Site Management Agent* or *Incident Management Agent*.

### The Goal

```
┌────────────────┐     ┌─────────────────────────────────────────────────────┐
│ Sev3 fires     │     │                    SRE Agent                         │
│                │ ──▶ ├─────────────────────────────────────────────────────┤
└────────────────┘     │  False alarm? ──▶ Investigate ──▶ Close incident     │
                        │  Real issue?  ──▶ Run TSG ──▶ Mitigate ──▶ Close  │
                        └─────────────────────────────────────────────────────┘
                                              │
                                              ▼
                                    ┌───────────────────────┐
                                    │ Zero DRI hours spent │
                                    └───────────────────────┘
```

### Tool Options Being Explored

| Option | Description | Status |
|--------|-------------|--------|
| **SRE Agent** (current) | Custom agent with IcM/Geneva/Kusto integration | 🧪 Demo ready |
| **IcM Agent Studio** | Microsoft's built-in incident management AI | 🧪 Evaluating |

We're close to achieving initial goals — exploring which tool/approach is the right fit.

| Property | Value |
|----------|-------|
| **Version** | 0.2.0 |
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
| Tool | Status | Description |
|------|--------|-------------|
| `icm_api` | 🧪 Evaluating | IcM incident management API |
| `geneva_metrics` | 🧪 Evaluating | Azure Monitor (Geneva) metrics |
| `kusto_query` | 🧪 Evaluating | Kusto query execution |
| `tsg_executor` | 🧪 Evaluating | Execute troubleshooting guides |
| `incident_closer` | 🧪 Evaluating | Auto-close incidents with reason |

### Integrations (Evaluating)
- IcM Agent Studio — Microsoft's incident AI
- SRE Portal
- Azure Monitor (Geneva)
- TSG repository

### Context Files
- `sev-definitions.md` — What constitutes Sev1/2/3
- `tsg-catalog.md` — Available troubleshooting guides
- `false-alarm-patterns.md` — Known false positive signatures

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

- [ ] **Sev1/Sev2** — Always involve DRI (agent assists only)
- [ ] Before auto-closing if confidence is low
- [ ] When TSG requires destructive action

### Failure Modes
> Known ways this agent can fail.

- False positives/alert fatigue
- Missed correlated signals
- Improper escalation routing

---

## Workflow Integration

### Trigger Scenarios
> When to invoke this agent.

- **Sev3 incident created** — primary use case
- Anomaly detected that may become incident
- TSG recommendation needed

### Input Contract

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `signal` | json | ✅ | Alert payload or IcM event |

### Output Contract

| Name | Type | Location | Description |
|------|------|----------|-------------|
| `triage_result` | enum | stdout | `false_alarm` \| `mitigated` \| `escalate` |
| `triage_summary` | markdown | stdout | What happened, root cause, actions taken |
| `incident_update` | json | IcM | Updated incident with resolution |

### Agent Flow

```
┌─────────────┐     ┌─────────────────────────────────────────┐
│ Sev3 fires  │ ──▶ │               SRE Agent                │
└─────────────┘     ├─────────────┬─────────────┬─────────────┤
                        │ Investigate   │ Run TSG     │ Close       │
                        └─────────────┴─────────────┴─────────────┘
                                              │
                        ┌────────────────────────────┴────────────┐
                        ▼                                            ▼
              ┌────────────────────┐               ┌────────────────────┐
              │ DRI not involved │               │ Escalate to DRI    │
              │ (goal state)     │               │ (if needed)        │
              └────────────────────┘               └────────────────────┘
```

**Persona:** Calm, methodical incident responder

---

## Evaluation & Adoption

### Success Metrics
- 🔜 **DRI hours on Sev3s → Zero** (primary goal)
- 🔜 Sev3 auto-resolution rate
- 🔜 False alarm detection accuracy
- 🔜 MTTA/MTTR for Sev3s

### Current Status

| Milestone | Status |
|-----------|--------|
| Demos working | ✅ Complete |
| Tool evaluation (SRE Agent vs IcM Agent Studio) | 🔄 In progress |
| Initial Sev3 handling | 🔜 Close to achieving |
| Full self-serve Sev3 | 🔜 Target state |

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | TBD — evaluating tools |
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
| 0.2.0 | Refocused on Sev3 self-service; added tool evaluation status |
| 0.1.0 | Seed spec |
