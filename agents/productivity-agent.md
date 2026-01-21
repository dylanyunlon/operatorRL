---
name: Productivity Agent
version: 0.1.0
description: IDE-integrated monitoring to measure engineer productivity and velocity before/after using AI agents.
category: analyst
maturity: experimental
owner: AX&E Engineering
last-validated: 2026-01-21
---

# Productivity Agent

> IDE-integrated monitoring to measure engineer productivity and velocity before/after using AI agents.

## 🎯 Vision

**Measure the impact of AI agents** — Attach to existing IDEs (VS Code, etc.), monitor activity, and show engineers how productive they've been with and without AI assistance.

### The Idea

```
┌──────────────────────────────────────────────────────────────────────┐
│                        VS Code / IDE                                 │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  Productivity Agent (monitoring)                                 │  │
│  │  ────────────────────────────────────────────────────────────  │  │
│  │  • Tracks activity BEFORE using AI agents                       │  │
│  │  • Tracks activity AFTER using AI agents                        │  │
│  │  • Shows: "Here's how productive you've been"                   │  │
│  │  • Shows: "Here's your velocity improvement"                    │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

### Target Audiences (Phased)

| Phase | Audience | Status |
|-------|----------|--------|
| **Phase 1** | Engineers (individual insights) | 🔄 Current focus |
| **Phase 2** | Managers (team insights) | 🔜 Future |

### Build Approach

| Aspect | Approach |
|--------|----------|
| **Building from scratch?** | ❌ No |
| **Partnership** | Working with a team who has already built this |
| **Our role** | Leverage their work, potentially fork to customize |

| Property | Value |
|----------|-------|
| **Version** | 0.1.0 |
| **Category** | analyst |
| **Maturity** | 🧪 experimental |
| **Owner** | AX&E Engineering |
| **Orchestration Role** | worker |

## Related Agents

- [Planning Agent](planning-agent.md)
- [DRI Report Agent](dri-report-agent.md)

---

## Capabilities

### Tools
| Tool | Status | Description |
|------|--------|-------------|
| `ide_activity_monitor` | 🔄 Exploring | VS Code activity tracking |
| `agent_usage_tracker` | 🔄 Exploring | Track before/after agent usage |
| `velocity_calculator` | 🔄 Exploring | Calculate productivity metrics |
| `insights_dashboard` | 🔜 Planned | Personal productivity dashboard |

### Integrations
- VS Code (primary)
- Other IDEs (future)
- AI agents (to measure impact)

### Context Files
- `metric-definitions.md` — What we measure and why
- `privacy-guidelines.md` — What data is collected and how it's used

---

## Risk Assessment

| Risk Factor | Level |
|-------------|-------|
| **Autonomy Level** | guided |
| **Blast Radius** | local-file |
| **Reversibility** | fully |
| **Data Sensitivity** | internal-only (engineer activity data) |
| **Cost Profile** | minimal |

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

- **Continuous** — passively monitors IDE activity
- Engineer wants to see their productivity insights
- Before/after comparison when adopting new AI tools

### Input Contract

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `activity_data` | telemetry | ✅ | IDE activity (automatic collection) |
| `agent_sessions` | json | ❌ | AI agent usage sessions |

### Output Contract

| Name | Type | Location | Description |
|------|------|----------|-------------|
| `productivity_insights` | markdown | IDE panel | Personal productivity summary |
| `velocity_comparison` | chart | IDE panel | Before/after AI agent adoption |

### Agent Flow

```
┌──────────────────┐     ┌────────────────────┐     ┌────────────────────────┐
│ IDE Activity     │ ──▶ │ Productivity Agent │ ──▶ │ Engineer sees:         │
│ (continuous)     │     └────────────────────┘     │ • Productivity score    │
└──────────────────┘                              │ • Velocity metrics      │
                                                  │ • AI impact comparison  │
                                                  └────────────────────────┘
```

**Persona:** Non-judgmental productivity companion

---

## Evaluation & Adoption

### Success Metrics
- 🔜 Engineers find insights valuable
- 🔜 Measurable productivity improvement with AI agents
- 🔜 Adoption rate among engineers

### Current Status: 🧪 Very Experimental

| Aspect | Status |
|--------|--------|
| Partnership with existing team | 🔄 In progress |
| Exploring their solution | 🔄 In progress |
| Potential fork for customization | 🔜 If needed |
| Engineer-facing insights | 🔜 Phase 1 |
| Manager-facing insights | 🔜 Phase 2 (future) |

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | TBD — exploring |
| **Learning Curve** | minimal (passive monitoring) |

### Prerequisites
- VS Code or supported IDE
- Opt-in to activity monitoring

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
| 0.1.0 | Initial — exploring partnership approach |
