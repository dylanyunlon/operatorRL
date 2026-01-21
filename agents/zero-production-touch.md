---
name: Zero Production Touch
version: 0.1.0
description: Dashboard tracking production touches with manual follow-up process; AI automation paused due to lack of sponsorship.
category: orchestrator
maturity: deprecated
owner: AX&E Engineering
last-validated: 2026-01-21
---

# Zero Production Touch

> ⛔ **ON HOLD** — AI automation paused due to lack of sponsorship. Dashboard and manual process continue.

## Current State

### Tool vs Agent: Similar to Release Freshness

| Component | Status | Description |
|-----------|--------|-------------|
| **Dashboard (Tool)** | ✅ Live & working well | Shows production touch metrics — we're doing awesome |
| **Manual Process** | ✅ Active | Weekly follow-up with developers on production touches |
| **Agent (AI)** | ⛔ **On hold** | No sponsorship — unknown when we'll resume |

### Current Manual Process

```
┌──────────────────┐     ┌───────────────────────────────────────────────────────┐
│ Dashboard shows  │     │                    Manual Process                     │
│ production       │ ──▶ ├───────────────────────────────────────────────────────┤
│ touches          │     │  1. Review weekly production touches                     │
└──────────────────┘     │  2. Follow up with developers — why did they do it?      │
         ✅               │  3. False alarm? → Work with dashboard/service owners   │
                         │  4. Real issue? → Note it, track resolution             │
                         └───────────────────────────────────────────────────────┘
                                              ❌ Manual effort
```

### What AI Automation Would Do (If Sponsored)

| Step | Current | With Agent |
|------|---------|------------|
| Review production touches | Manual | Automated |
| Follow up with developers | Manual | Auto-notify |
| Classify false alarm vs real | Manual | AI-assisted |
| Work with service owners | Manual | Auto-route |
| Track and note issues | Manual | Auto-log |

### Why On Hold

| Factor | Status |
|--------|--------|
| Dashboard | ✅ Working great |
| Manual process | ✅ Working (but time-consuming) |
| Sponsorship for AI automation | ❌ **Not available** |
| Resume date | ❓ Unknown |

| Property | Value |
|----------|-------|
| **Version** | 0.1.0 |
| **Category** | orchestrator |
| **Maturity** | ⛔ on hold (no sponsorship) |
| **Owner** | AX&E Engineering |
| **Orchestration Role** | coordinator |

## Related Agents

- [Release Freshness Agent](release-freshness-agent.md)
- [SRE Agent](sre-agent.md)

---

## Capabilities

### Tools
| Tool | Status | Description |
|------|--------|-------------|
| `zpt_dashboard` | ✅ Live | Production touch tracking dashboard |
| `policy_checker` | 🔜 Planned | Check release policies |
| `auto_followup` | 🔜 Planned | Automated developer follow-up |
| `false_alarm_classifier` | 🔜 Planned | AI classification of touches |

### Integrations
- Learn Platform
- ADO Pipelines

### Context Files
- `prod-safety-rules.md`

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

- [ ] Before flagging a release as unsafe
- [ ] Before blocking a release pipeline

### Failure Modes
> Known ways this agent can fail.

- False positives blocking release

---

## Workflow Integration

### Trigger Scenarios
> When to invoke this agent.

- Pre-deploy safety review

### Input Contract

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `release_candidate` | string | ✅ | Release branch/build identifier |

### Output Contract

| Name | Type | Location | Description |
|------|------|----------|-------------|
| `safety_report` | markdown | stdout | Findings and required mitigations |

### Agent Flow

```
┌─────────────────────────┐     ┌──────────────────────┐     ┌──────────────────┐
│ Release Freshness Agent │ ──▶ │ Zero Production Touch│ ──▶ │ Release Managers │
└─────────────────────────┘     └──────────────────────┘     └──────────────────┘
```

**Persona:** Strict but fair release guardian

---

## Evaluation & Adoption

### Success Metrics

**Dashboard (Working):**
- ✅ Visibility into all production touches
- ✅ Metrics trending well — "we're doing awesome"

**Agent (If Resumed):**
- 🔜 Reduction in manual follow-up time
- 🔜 Faster false alarm resolution

### Current Status

| Component | Status |
|-----------|--------|
| Dashboard | ✅ Live and showing great results |
| Manual weekly process | ✅ Active |
| AI automation | ⛔ On hold — no sponsorship |

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | Dashboard: immediate \| Agent: TBD |
| **Learning Curve** | minimal |

### Prerequisites
- Access to pipeline and repo policies

---

## Governance

| Field | Value |
|-------|-------|
| **Owner** | AX&E Engineering |
| **Last Validated** | 2026-01-21 |
| **Status** | On hold — no sponsorship for AI automation |
| **Resume** | Unknown |

### Changelog
| Version | Notes |
|---------|-------|
| 0.1.1 | Clarified tool vs agent status; on hold due to no sponsorship |
| 0.1.0 | Initial |
