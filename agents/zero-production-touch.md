---
name: Zero Production Touch
version: 0.1.0
description: Automated safety dashboard to replace manual reviews of unsafe production changes; keep work in Learn platform, maintain only (on hold).
category: orchestrator
maturity: deprecated
owner: AX&E Engineering
last-validated: 2026-01-21
---

# Zero Production Touch

> ⛔ **DEPRECATED** — This agent is on hold until capacity constraints are resolved.

Automated safety dashboard to replace manual reviews of unsafe production changes; keep work in Learn platform, maintain only.

| Property | Value |
|----------|-------|
| **Version** | 0.1.0 |
| **Category** | orchestrator |
| **Maturity** | ⛔ deprecated |
| **Owner** | AX&E Engineering |
| **Orchestration Role** | coordinator |

## Related Agents

- [Release Freshness Agent](release-freshness-agent.md)
- [SRE Agent](sre-agent.md)

---

## Capabilities

### Tools
| Tool | Description |
|------|-------------|
| `policy_checker` | Check release policies |
| `release_diff` | Compare release differences |
| `alerting` | Send alerts |

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
- ✅ Reduction in unsafe pushes
- ✅ Reduced manual bi-weekly review time

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | Per release cycle |
| **Learning Curve** | minimal |

### Prerequisites
- Access to pipeline and repo policies

---

## Governance

| Field | Value |
|-------|-------|
| **Owner** | AX&E Engineering |
| **Last Validated** | 2026-01-21 |
| **Deprecation Policy** | Paused until capacity constraints resolved |

### Changelog
| Version | Notes |
|---------|-------|
| 0.1.0 | Initial |
| 0.1.1 | Status set to On Hold |
