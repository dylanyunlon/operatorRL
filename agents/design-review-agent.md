---
name: Design Review Agent
version: 0.2.0
description: Merges document structure analysis with deep architecture review to provide actionable feedback before peer design reviews.
category: hybrid
maturity: beta
owner: AX&E Engineering
last-validated: 2026-01-21
---

# Design Review Agent

> Merges document structure analysis with deep architecture review to provide actionable feedback before peer design reviews.

## 🎯 Vision

**Shift-left design quality** — Catch structural and architectural issues *before* the design review meeting, so discussions focus on strategic decisions rather than surface-level gaps.

### Current State: Merging Two Prototypes

| Prototype | Focus | Status |
|-----------|-------|--------|
| **Structure Analyzer** | Document completeness, sections, formatting | ✅ Prototype complete |
| **Architecture Reviewer** | Deep analysis inside Word docs — key design issues, how to address them | ✅ Prototype complete |
| **Merged Agent** | Combined capabilities | 🔄 In progress |

### Value Differentiator

> *"Why not just use Researcher?"*

Researcher is very powerful and broad, and it can help with many kinds of analysis and exploration. However, it is not particularly specialized for design reviews. What we are proposing is different in that we can encode our own design-review–specific requirements (for example, explicit rules and heuristics derived from past design discussions), and inject very targeted context that Researcher would not naturally have. This allows our own agent to perform design reviews with stronger domain knowledge and more consistent judgment, rather than being a general-purpose reviewer.

| Capability | Researcher | Design Review Agent |
|------------|------------|--------------------|
| General Q&A on design topics | ✅ | ✅ |
| **Team-specific design patterns** | ❌ | ✅ Learns from your team's past reviews |
| **Historical precedent lookup** | ❌ | ✅ "We solved this in Project X" |
| **Security baseline enforcement** | ❌ | ✅ Applies org security rules |
| **Continuous learning loop** | ❌ | ✅ Improves from each review |
| **Pre-meeting preparation** | ❌ | ✅ Structured checklist for reviewers |

### Roadmap
- ✅ Prototype 1: Document structure analysis
- ✅ Prototype 2: Architecture issue detection
- 🔄 Merge prototypes into unified agent
- 🔜 Learning loop from live design discussions
- 🔜 Pilot with Learn, Localization, Startups teams
- 🔜 Broader rollout based on feedback

| Property | Value |
|----------|-------|
| **Version** | 0.2.0 |
| **Category** | hybrid |
| **Maturity** | 🟡 beta |
| **Owner** | AX&E Engineering |
| **Orchestration Role** | standalone |

## Related Agents

- [Planning Agent](planning-agent.md)
- [Unit & Scenario Testing Agent](unit-and-scenario-testing-agent.md)

---

## Capabilities

### Tools
| Tool | Description |
|------|-------------|
| `doc_structure_analyzer` | Analyze document completeness and structure |
| `word_doc_reader` | Deep analysis inside Word/PPTX documents |
| `repo_reader` | Read repository contents for context |
| `threat_model_rules` | Apply threat modeling rules |
| `office365_search` | Search historical designs and precedents |
| `learning_rules_engine` | 🔜 Incorporate rules from past reviews |

### Integrations
- GitHub
- Azure DevOps Repos
- SharePoint
- Teams

### Context Files
- `design-checklist.md` — Required sections, common gaps
- `security-baselines.md` — Org security requirements
- `learned-patterns.md` — 🔜 Rules extracted from past reviews
- `team-precedents.md` — 🔜 "We solved this before" examples

---

## Risk Assessment

| Risk Factor | Level |
|-------------|-------|
| **Autonomy Level** | guided |
| **Blast Radius** | workspace |
| **Reversibility** | fully |
| **Data Sensitivity** | internal-only |
| **Cost Profile** | moderate |

### Human Checkpoints
> Points where human approval is required before proceeding.

- [ ] Before filing blocking issues
- [ ] Before recommending architectural changes

### Failure Modes
> Known ways this agent can fail.

- Out-of-context recommendations
- Overly generic guidance
- Missed historical precedent

---

## Workflow Integration

### Trigger Scenarios
> When to invoke this agent.

- Draft design doc ready for pre-review
- Before scheduling design review meeting
- When seeking historical precedent for a pattern

### Input Contract

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `design_doc` | file | ✅ | Design document (.md/.docx/.pptx) |
| `repo_links` | string[] | ❌ | Relevant code locations |

### Output Contract

| Name | Type | Location | Description |
|------|------|----------|-------------|
| `structure_report` | markdown | stdout | Document completeness gaps |
| `architecture_findings` | markdown | stdout | Key design issues with suggested fixes |
| `precedent_links` | json | stdout | Similar past designs for reference |
| `reviewer_prep` | markdown | stdout | Pre-meeting checklist for reviewers |

### Agent Flow

```
┌─────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│ Planning Agent  │ ──▶ │ Design Review Agent │ ──▶ │ Implementation Agent│
└─────────────────┘     └─────────────────────┘     │ Security Review     │
                                                     └─────────────────────┘
```

**Persona:** Thoughtful architect citing prior art and risks

---

## Evaluation & Adoption

### Success Metrics
- ✅ Actionable findings accepted by team
- ✅ Reduced rework during implementation
- 🔜 Design review meetings shorter/more focused
- 🔜 Fewer "we should have caught this" moments

### Pilot Rollout Plan

| Phase | Teams | Goal |
|-------|-------|------|
| **Phase 1** | Learn, Localization, Startups | Early feedback, refine value prop |
| **Phase 2** | Broader Ecosystems Engineering | Validate at scale |
| **Phase 3** | Cross-org | Full rollout |

### Adoption Info

| Factor | Value |
|--------|-------|
| **Time to Value** | 5-15 minutes |
| **Learning Curve** | moderate |

### Prerequisites
- Access to design repo/wiki
- Security baseline documents
- Historical design docs (for precedent lookup)

---

## Governance

| Field | Value |
|-------|-------|
| **Owner** | AX&E Engineering |
| **Last Validated** | 2026-01-21 |
| **Deprecation Policy** | Superseded by Org Design LLM when available |

### Changelog
| Version | Notes |
|---------|-------|
| 0.2.0 | Merged two prototypes, added value differentiator, pilot plan |
| 0.1.0 | Initial spec from deck |
