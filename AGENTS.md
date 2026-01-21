# SDLC Agents Status Dashboard

This document provides a comprehensive overview of all agents being developed for the SDLC process.

## Summary

| Status | Count |
|--------|-------|
| Experimental | 3 |
| Beta | 7 |
| Stable | 0 |
| Deprecated | 1 |
| **Total** | **11** |

---

## Planning & Requirements Agents

### [Planning Agent](agents/planning-agent.md)

**Status:** 🟡 Beta  
**Owner:** AX&E Engineering  
**Description:** Summarizes sprint plans, creates and updates Azure DevOps items, and maintains hygiene; provides dashboards for alignment and status.  
**Last Updated:** 2026-01-21

### [Onboarding Agent](agents/onboarding-agent.md)

**Status:** 🧪 Experimental  
**Owner:** AX&E Engineering  
**Description:** Reduces onboarding time by generating key engineering artifacts from existing wiki/SharePoint/code and auto-creating initial ADO items.  
**Last Updated:** 2026-01-21

---

## Design & Architecture Agents

### [Design Review Agent](agents/design-review-agent.md)

**Status:** 🟡 Beta  
**Owner:** AX&E Engineering  
**Description:** Provides early feedback on design, architecture, and security using historical data so designs improve before peer review.  
**Last Updated:** 2026-01-21

---

## Development & Coding Agents

### [Accessibility Agent](agents/accessibility-agent.md)

**Status:** 🟡 Beta  
**Owner:** AX&E Engineering  
**Description:** Automates accessibility checks and bug fixing: analyzes ADO WITs, reproduces issues, identifies problems, and proposes code fixes.  
**Last Updated:** 2026-01-21

### [Productivity Agent](agents/productivity-agent.md)

**Status:** 🧪 Experimental  
**Owner:** AX&E Engineering  
**Description:** Automates measurement of coding productivity with reliable metrics and dashboards.  
**Last Updated:** 2026-01-21

---

## Testing & Quality Assurance Agents

### [Unit & Scenario Testing Agent](agents/unit-and-scenario-testing-agent.md)

**Status:** 🟡 Beta  
**Owner:** AX&E Engineering  
**Description:** Generates AI-assisted unit and scenario tests and integrates with pipelines to increase coverage and defect detection.  
**Last Updated:** 2026-01-21

### [SFI Agent](agents/sfi-agent.md)

**Status:** 🟡 Beta  
**Owner:** AX&E Engineering  
**Description:** Manages SFI work with two specialized agents integrated into the SWE agent; tracks ~6 KPIs and automates ADO WIT creation.  
**Last Updated:** 2026-01-21

---

## Deployment & Operations Agents

### [Release Freshness Agent](agents/release-freshness-agent.md)

**Status:** 🟡 Beta  
**Owner:** AX&E Engineering  
**Description:** Tracks production freshness and automatically follows up on delayed deployments and pending changes across services.  
**Last Updated:** 2026-01-21

### [Zero Production Touch](agents/zero-production-touch.md)

**Status:** ⛔ Deprecated  
**Owner:** AX&E Engineering  
**Description:** Automated safety dashboard to replace manual reviews of unsafe production changes. (On hold)  
**Last Updated:** 2026-01-21

---

## Monitoring & Maintenance Agents

### [SRE Agent](agents/sre-agent.md)

**Status:** 🧪 Experimental  
**Owner:** AX&E Engineering  
**Description:** Self-serve live site incident assistant providing 24×7 monitoring with proactive alerts; closes alerting gaps with recommendations.  
**Last Updated:** 2026-01-21

---

## Reporting Agents

### [FUN Report Agent](agents/fun-report-agent.md)

**Status:** 🟡 Beta  
**Owner:** AX&E Engineering  
**Description:** Centralized reporting for LSI/SFI metrics; eliminates manual report creation for AX&E Engineering.  
**Last Updated:** 2026-01-21

---

## How to Update This Document

When adding a new agent:

1. Create agent documentation in the `agents/` directory following the [Agent Specification](agent-specification.md)
2. Add an entry in the relevant category section above
3. Update the summary count table
4. Follow this format for agent entries:

```markdown
### [Agent Name](agents/agent-name.md)

**Status:** [Status]  
**Owner:** [Team/Person]  
**Description:** Brief description of what the agent does  
**Last Updated:** YYYY-MM-DD
```

## Status Legend

Use these status indicators when documenting agents:

- 🧪 **Experimental** - Early exploration, expect breaking changes
- 🟡 **Beta** - Functional but still being refined
- 🟢 **Stable** - Production-ready
- ⛔ **Deprecated** - No longer maintained or used
