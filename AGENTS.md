# SDLC Agents Status Dashboard

> A comprehensive overview of all AI agents being developed for the Software Development Lifecycle.

---

## 📊 Summary

| Status | Icon | Count | Description |
|--------|------|-------|-------------|
| Experimental | 🧪 | 3 | Early exploration, expect breaking changes |
| Beta | 🟡 | 7 | Functional but still being refined |
| Stable | 🟢 | 0 | Production-ready |
| Deprecated | ⛔ | 1 | No longer maintained or used |
| **Total** | | **11** | |

---

## 🗂️ Quick Reference

| Agent | Category | Maturity | Orchestration |
|-------|----------|----------|---------------|
| [Planning Agent](agents/planning-agent.md) | orchestrator | 🟡 beta | coordinator |
| [Onboarding Agent](agents/onboarding-agent.md) | capture | 🧪 experimental | worker |
| [Design Review Agent](agents/design-review-agent.md) | hybrid | 🟡 beta | standalone |
| [Accessibility Agent](agents/accessibility-agent.md) | hybrid | 🟡 beta | worker |
| [Productivity Agent](agents/productivity-agent.md) | analyst | 🧪 experimental | worker |
| [Unit & Scenario Testing Agent](agents/unit-and-scenario-testing-agent.md) | analyst | 🟡 beta | worker |
| [SFI Agent](agents/sfi-agent.md) | hybrid | 🟡 beta | coordinator |
| [Release Freshness Agent](agents/release-freshness-agent.md) | analyst | 🟡 beta | worker |
| [Zero Production Touch](agents/zero-production-touch.md) | orchestrator | ⛔ deprecated | coordinator |
| [SRE Agent](agents/sre-agent.md) | orchestrator | 🧪 experimental | coordinator |
| [FUN Report Agent](agents/fun-report-agent.md) | analyst | 🟡 beta | worker |

---

## 📋 Planning & Requirements

<table>
<tr>
<td width="50%">

### [Planning Agent](agents/planning-agent.md)
🟡 **Beta** · orchestrator · coordinator

Summarizes sprint plans, creates and updates Azure DevOps items, and maintains hygiene; provides dashboards for alignment and status.

**Tools:** `ado_api` `power_bi` `sharepoint_reader` `teams_notifier`

</td>
<td width="50%">

### [Onboarding Agent](agents/onboarding-agent.md)
🧪 **Experimental** · capture · worker

Reduces onboarding time by generating key engineering artifacts from existing wiki/SharePoint/code and auto-creating initial ADO items.

**Tools:** `sharepoint_reader` `repo_reader` `ado_api` `doc_summarizer`

</td>
</tr>
</table>

---

## 🏗️ Design & Architecture

<table>
<tr>
<td>

### [Design Review Agent](agents/design-review-agent.md)
🟡 **Beta** · hybrid · standalone

Provides early feedback on design, architecture, and security using historical data so designs improve before peer review.

**Tools:** `repo_reader` `threat_model_rules` `static_analysis` `office365_search` `doc_reviewer`

</td>
</tr>
</table>

---

## 💻 Development & Coding

<table>
<tr>
<td width="50%">

### [Accessibility Agent](agents/accessibility-agent.md)
🟡 **Beta** · hybrid · worker

Automates accessibility checks and bug fixing: analyzes ADO WITs, reproduces issues, identifies problems, and proposes code fixes.

**Tools:** `playwright_browser` `axe_core_scan` `ado_api` `git_pr_creator`

</td>
<td width="50%">

### [Productivity Agent](agents/productivity-agent.md)
🧪 **Experimental** · analyst · worker

Automates measurement of coding productivity with reliable metrics and dashboards.

**Tools:** `git_metrics` `ado_activity` `telemetry_aggregator` `power_bi`

</td>
</tr>
</table>

---

## 🧪 Testing & Quality Assurance

<table>
<tr>
<td width="50%">

### [Unit & Scenario Testing Agent](agents/unit-and-scenario-testing-agent.md)
🟡 **Beta** · analyst · worker

Generates AI-assisted unit and scenario tests and integrates with pipelines to increase coverage and defect detection.

**Tools:** `test_generator` `playwright` `coverage_analyzer` `pipeline_integration`

</td>
<td width="50%">

### [SFI Agent](agents/sfi-agent.md)
🟡 **Beta** · hybrid · coordinator

Manages SFI work with two specialized agents integrated into the SWE agent; tracks ~6 KPIs and automates ADO WIT creation.

**Tools:** `ado_api` `kpi_calculator` `power_bi` `swe_agent_bridge`

</td>
</tr>
</table>

---

## 🚀 Deployment & Operations

<table>
<tr>
<td width="50%">

### [Release Freshness Agent](agents/release-freshness-agent.md)
🟡 **Beta** · analyst · worker

Tracks production freshness and automatically follows up on delayed deployments and pending changes across services.

**Tools:** `ado_release_api` `git_diff_checker` `power_bi` `notifier`

</td>
<td width="50%">

### [Zero Production Touch](agents/zero-production-touch.md)
⛔ **Deprecated** · orchestrator · coordinator

Automated safety dashboard to replace manual reviews of unsafe production changes. *(On hold)*

**Tools:** `policy_checker` `release_diff` `alerting`

</td>
</tr>
</table>

---

## 📡 Monitoring & Maintenance

<table>
<tr>
<td>

### [SRE Agent](agents/sre-agent.md)
🧪 **Experimental** · orchestrator · coordinator

Self-serve live site incident assistant providing 24×7 monitoring with proactive alerts; closes alerting gaps with recommendations.

**Tools:** `icm_api` `geneva_metrics` `kusto_query` `runbook_executor` `teams_notifier`

</td>
</tr>
</table>

---

## 📈 Reporting

<table>
<tr>
<td>

### [FUN Report Agent](agents/fun-report-agent.md)
🟡 **Beta** · analyst · worker

Centralized reporting for LSI/SFI metrics; eliminates manual report creation for AX&E Engineering.

**Tools:** `power_bi` `dataset_connector` `scheduler`

</td>
</tr>
</table>

---

## 🔗 Agent Relationships

```
                                    ┌─────────────────────┐
                                    │   Planning Agent    │
                                    │   (coordinator)     │
                                    └──────────┬──────────┘
                                               │
                    ┌──────────────────────────┼──────────────────────────┐
                    │                          │                          │
                    ▼                          ▼                          ▼
         ┌──────────────────┐       ┌──────────────────┐       ┌──────────────────┐
         │ Design Review    │       │ FUN Report Agent │       │ Onboarding Agent │
         │ Agent            │       │                  │       │                  │
         └────────┬─────────┘       └────────┬─────────┘       └──────────────────┘
                  │                          │
                  ▼                          ▼
         ┌──────────────────┐       ┌──────────────────┐
         │ Unit & Scenario  │       │    SFI Agent     │◀─────────────────┐
         │ Testing Agent    │       │   (coordinator)  │                  │
         └──────────────────┘       └────────┬─────────┘                  │
                                             │                            │
                                             ▼                            │
                                    ┌──────────────────┐       ┌──────────────────┐
                                    │ Accessibility    │       │ Release Freshness│
                                    │ Agent            │       │ Agent            │
                                    └──────────────────┘       └────────┬─────────┘
                                                                        │
                                                                        ▼
                                                               ┌──────────────────┐
                                                               │    SRE Agent     │
                                                               │   (coordinator)  │
                                                               └──────────────────┘
```

---

## 📝 How to Add a New Agent

1. **Create the agent file** in `agents/` following the [Agent Specification](agent-specification.md)
2. **Use the standard format** with YAML frontmatter + human-readable markdown
3. **Add to this dashboard** in the appropriate category section
4. **Update the summary counts** at the top

### Agent File Template

```markdown
---
name: Agent Name
version: 0.1.0
description: One-line description
category: analyst | capture | coach | orchestrator | hybrid
maturity: experimental | beta | stable | deprecated
owner: AX&E Engineering
last-validated: YYYY-MM-DD
---

# Agent Name

> One-line description

(... rest of human-readable documentation ...)
```

---

## 📚 Resources

- [Agent Specification v1.0](agent-specification.md) — Formal taxonomy for agent definitions
- [README](README.md) — Repository overview and getting started

---

*Last updated: 2026-01-21 · Owner: AX&E Engineering*
