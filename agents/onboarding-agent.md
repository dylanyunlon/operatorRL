---
name: Onboarding Agent
version: 0.1.0
description: Generates blessed artifacts (C4 diagrams, dataflow diagrams, etc.) from existing sources to help coding agents and engineers understand repositories.
category: capture
maturity: experimental
owner: AX&E Engineering
last-validated: 2026-01-21
---

# Onboarding Agent

> Generates blessed artifacts (C4 diagrams, dataflow diagrams, etc.) from existing sources to help coding agents and engineers understand repositories.

## 🎯 Vision

**Blessed artifacts in every repo** — Generate canonical documentation artifacts from scattered sources (SharePoint, OneDrive, wikis), get expert approval, and commit them to the repository where they help both AI agents and humans.

### The Idea

```
┌───────────────────┐     ┌──────────────────┐     ┌─────────────────┐     ┌───────────────────┐
│ Scattered Sources   │     │ Onboarding Agent │     │ Expert Review   │     │ Artifacts in Repo │
│ ───────────────── │ ──▶ │ ──────────────── │ ──▶ │ ─────────────── │ ──▶ │ ───────────────── │
│ • SharePoint         │     │ Generates:       │     │ Approves/blesses│     │ Helps:            │
│ • OneDrive           │     │ • C4 diagrams    │     │ artifacts       │     │ • GitHub Copilot  │
│ • Wikis              │     │ • Dataflow       │     └─────────────────┘     │ • Coding agents   │
│ • Existing docs      │     │ • Architecture   │                           │ • New engineers   │
└───────────────────┘     └──────────────────┘                           └───────────────────┘
```

### Why This Matters

| Beneficiary | How Blessed Artifacts Help |
|-------------|---------------------------|
| **GitHub Copilot** | Better context → better code suggestions |
| **Other coding agents** | Understand architecture before making changes |
| **New engineers** | Open repo, ask questions, get accurate answers |
| **Existing team** | Single source of truth, always up-to-date |

### Artifacts to Generate

| Artifact | Description | Format |
|----------|-------------|--------|
| C4 Context Diagram | System context and external dependencies | Mermaid/PlantUML |
| C4 Container Diagram | High-level technical building blocks | Mermaid/PlantUML |
| Dataflow Diagrams | How data moves through the system | Mermaid |
| Architecture Decision Records | Key decisions and rationale | Markdown |
| Component Overview | What each major component does | Markdown |

### Current Status: 🧪 Just Kicking Off

We know the vision. Now exploring how to execute it.

| Property | Value |
|----------|-------|
| **Version** | 0.1.0 |
| **Category** | capture |
| **Maturity** | 🧪 experimental |
| **Owner** | AX&E Engineering |
| **Orchestration Role** | worker |

## Related Agents

- [Planning Agent](planning-agent.md)
- [Design Review Agent](design-review-agent.md)

---

## Capabilities

### Tools
| Tool | Description |
|------|-------------|
| `sharepoint_reader` | Read SharePoint content |
| `onedrive_reader` | Read OneDrive documents |
| `repo_reader` | Read repository contents |
| `diagram_generator` | Generate C4/dataflow diagrams |
| `doc_summarizer` | Summarize existing documentation |

### Integrations
- SharePoint
- OneDrive
- GitHub/ADO Repos
- Mermaid/PlantUML rendering

### Context Files
- `artifact-templates/` — Templates for each artifact type
- `c4-model-guide.md` — C4 diagram standards

---

## Risk Assessment

| Risk Factor | Level |
|-------------|-------|
| **Autonomy Level** | guided |
| **Blast Radius** | external-system |
| **Reversibility** | fully |
| **Data Sensitivity** | internal-only |
| **Cost Profile** | minimal |

### Human Checkpoints
> Points where human approval is required before proceeding.

- [ ] **Expert review of generated artifacts** — critical before committing
- [ ] Before committing artifacts to repository
- [ ] Periodic review to ensure artifacts stay current

### Failure Modes
> Known ways this agent can fail.

- Outdated source materials
- Non-standard team practices

---

## Workflow Integration

### Trigger Scenarios
> When to invoke this agent.

- Repository lacks architectural documentation
- New project setup
- Major architecture changes requiring doc refresh
- New engineer joining (artifacts should already exist)

### Input Contract

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `source_links` | string[] | ✅ | Links to SharePoint, OneDrive, wikis |
| `repo_url` | string | ✅ | Target repository for artifacts |

### Output Contract

| Name | Type | Location | Description |
|------|------|----------|-------------|
| `c4_diagrams` | files | repo `/docs` | Context and container diagrams |
| `dataflow_diagrams` | files | repo `/docs` | Data flow documentation |
| `architecture_overview` | markdown | repo `/docs` | Component overview and decisions |

### Agent Flow

```
┌──────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│ Onboarding Agent │ ──▶ │ Expert Approval  │ ──▶ │ Artifacts in Repo   │
└──────────────────┘     └──────────────────┘     └─────────────────────┘
                                                            │
                         ┌──────────────────────────────────┘
                         ▼
         ┌───────────────────────────────────────────────────┐
         │  Blessed artifacts help:                          │
         │  • GitHub Copilot gives better suggestions         │
         │  • New engineers ask questions, get good answers   │
         │  • Coding agents understand before changing        │
         └───────────────────────────────────────────────────┘
```

**Persona:** Diligent documentation curator

---

## Evaluation & Adoption

### Success Metrics
- 🔜 Repos with blessed artifacts vs. without
- 🔜 Copilot suggestion quality in repos with artifacts
- 🔜 Time-to-productivity for new engineers
- 🔜 Questions answered accurately from repo context

### Current Status: Exploration Phase

| What We Know | What We're Figuring Out |
|--------------|------------------------|
| ✅ The vision is clear | 🤔 Best artifact formats |
| ✅ The value proposition | 🤔 Source discovery automation |
| ✅ Expert approval is critical | 🤔 Keeping artifacts fresh |

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | TBD — exploring |
| **Learning Curve** | TBD |

### Prerequisites
- Access to source documentation (SharePoint, OneDrive)
- Target repository write access
- Domain expert available for review

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
| 0.1.0 | Initial — vision defined, exploration starting |
