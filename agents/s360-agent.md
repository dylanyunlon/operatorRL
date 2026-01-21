---
name: S360 Agent
version: 0.2.0
description: Suite of tools and agents for S360/SFI work — understanding items, creating work items for Copilot, and auto-generating fixes.
category: hybrid
maturity: beta
owner: AX&E Engineering
last-validated: 2026-01-21
---

# S360 Agent

> Suite of tools and agents for S360/SFI work — understanding items, creating work items for Copilot, and auto-generating fixes.

## 🎯 Vision

**From S360 item to PR automatically** — Reduce the manual effort of understanding, triaging, and fixing S360 items across AX&E Engineering.

### Three Parallel Workstreams

| # | Workstream | Status | Description |
|---|------------|--------|-------------|
| 1️⃣ | **S360 Explainer** | ✅ Adopted | Tool that helps you understand what an S360 item is about |
| 2️⃣ | **Work Item Creator** | ✅ Heavily adopted | Creates ADO work items → assigns to GitHub Copilot → work done |
| 3️⃣ | **Auto-Fix Generator** | 🧪 Exploring | Generate fixes for addressable items; PRs show up alongside S360 items |

### Workstream Details

#### 1️⃣ S360 Explainer Tool
```
┌────────────────┐     ┌──────────────────┐     ┌───────────────────┐
│ S360 Item      │ ──▶ │ S360 Explainer   │ ──▶ │ Clear explanation │
│ (confusing)    │     │ Tool             │     │ of what to do     │
└────────────────┘     └──────────────────┘     └───────────────────┘
```
**Status:** ✅ Adopted by multiple teams

#### 2️⃣ Work Item Creator → GitHub Copilot
```
┌────────────────┐     ┌──────────────────┐     ┌───────────────────┐     ┌────────────┐
│ S360 Item      │ ──▶ │ Create ADO       │ ──▶ │ GitHub Copilot    │ ──▶ │ Work done! │
│                │     │ Work Item        │     │ picks it up      │     └────────────┘
└────────────────┘     └──────────────────┘     └───────────────────┘
```
**Status:** ✅ Heavily adopted — improves velocity and productivity

#### 3️⃣ Auto-Fix Generator (Exploring)
```
┌──────────────────┐     ┌────────────────────┐     ┌──────────────────────────────┐
│ Addressable    │     │ Auto-Fix         │     │ S360 item + PR with fix    │
│ S360 Items     │ ──▶ │ Generator        │ ──▶ │ show up together           │
└──────────────────┘     └────────────────────┘     └──────────────────────────────┘
```
**Status:** 🧪 Exploring with Microsoft Commerce and S360 Breeze teams
**Impact:** Saves time across all of AX&E Engineering (not just Ecosystems)

| Property | Value |
|----------|-------|
| **Version** | 0.2.0 |
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
| Tool | Workstream | Description |
|------|------------|-------------|
| `s360_explainer` | 1️⃣ | Explain S360 items in plain language |
| `ado_wit_creator` | 2️⃣ | Create work items from S360 items |
| `copilot_assigner` | 2️⃣ | Assign work items to GitHub Copilot |
| `fix_generator` | 3️⃣ | Generate fixes for addressable items |
| `pr_creator` | 3️⃣ | Create PRs with generated fixes |

### Integrations
- Azure DevOps
- GitHub / GitHub Copilot
- S360 / SFI systems
- Microsoft Commerce team systems
- S360 Breeze

### Context Files
- `s360-item-types.md` — Types of S360/SFI items and how to address them
- `ado-wit-templates.md` — Work item templates for Copilot
- `addressable-patterns.md` — Patterns that can be auto-fixed

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

- Misclassified S360 item type
- Generated fix doesn't compile
- Work item lacks sufficient context for Copilot

---

## Workflow Integration

### Trigger Scenarios
> When to invoke this agent.

- New S360/SFI item assigned to team
- Batch processing of addressable items
- Engineer needs help understanding an S360 item

### Input Contract

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `s360_item` | json | ✅ | S360/SFI item details |
| `action` | enum | ✅ | `explain` \| `create_wit` \| `generate_fix` |

### Output Contract

| Name | Type | Location | Description |
|------|------|----------|-------------|
| `explanation` | markdown | stdout | Plain-language explanation of item |
| `ado_wit` | url | stdout | Created work item link |
| `pull_request` | url | stdout | PR with generated fix |

### Agent Flow

```
                        ┌───────────────────────────────────────────┐
                        │              S360 Agent                   │
                        ├───────────────────────────────────────────┤
┌─────────────┐     │  1️⃣ Explainer  │ 2️⃣ WIT+Copilot │ 3️⃣ Auto-Fix │
│ S360 Item   │ ──▶ │     ✅         │      ✅         │    🧪     │
└─────────────┘     └───────────────────────────────────────────┘
                              │                  │              │
                              ▼                  ▼              ▼
                        ┌────────────┐   ┌────────────┐   ┌──────────┐
                        │ Explanation│   │ Work done  │   │ PR ready │
                        └────────────┘   └────────────┘   └──────────┘
```

**Persona:** Efficient S360 processing assistant

---

## Evaluation & Adoption

### Success Metrics

| Workstream | Metric |
|------------|--------|
| 1️⃣ Explainer | Time saved understanding items |
| 2️⃣ WIT Creator | Work items created → completed via Copilot |
| 3️⃣ Auto-Fix | PRs generated with fixes |

### Adoption Status

| Workstream | Adoption |
|------------|----------|
| 1️⃣ S360 Explainer | ✅ Adopted by multiple teams |
| 2️⃣ Work Item Creator | ✅ **Heavily adopted** — improving velocity |
| 3️⃣ Auto-Fix Generator | 🧪 Exploring with Commerce & S360 Breeze |

### Collaboration

Working with centralized Microsoft teams:
- **Commerce team** — identifying addressable patterns
- **S360 Breeze team** — integrating fix generation

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | Immediate for explainer/WIT; TBD for auto-fix |
| **Learning Curve** | minimal |

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
| 0.2.0 | Renamed from SFI Agent to S360 Agent; documented three workstreams |
| 0.1.0 | First draft (as SFI Agent) |
