# SDLC Agents

> A collection of AI agents designed to automate and enhance the Software Development Lifecycle (SDLC).

## Overview

This repository serves as a central hub for tracking, documenting, and maintaining the status of various AI agents designed to support and automate different phases of the software development lifecycle. Each agent follows a standardized specification format to ensure consistency, evaluability, and safe adoption.

## 📋 Agent Specification

All agents in this repository follow the [Agent Specification v1.0](agent-specification.md), which defines:

- **Metadata** – Identity, versioning, and categorization
- **Capabilities** – Tools, handoffs, and integrations
- **Risk Assessment** – Autonomy level, blast radius, and failure modes
- **Workflow Integration** – Triggers, inputs, outputs, and agent relationships
- **Evaluation & Adoption** – Success metrics and prerequisites
- **Governance** – Ownership and lifecycle management

## Repository Structure

```
sdlc_agents/
├── README.md                    # This file
├── agent-specification.md       # Formal taxonomy for agent specs
└── agents/
    ├── accessibility-agent.md
    ├── design-review-agent.md
    ├── fun-report-agent.md
    ├── onboarding-agent.md
    ├── planning-agent.md
    ├── productivity-agent.md
    ├── release-freshness-agent.md
    ├── sfi-agent.md
    ├── sre-agent.md
    ├── unit-and-scenario-testing-agent.md
    └── zero-production-touch.md
```

## 🤖 Available Agents

| Agent | Category | Maturity | Description |
|-------|----------|----------|-------------|
| [Planning Agent](agents/planning-agent.md) | orchestrator | 🟡 beta | Summarizes sprint plans, creates and updates ADO items, and maintains hygiene |
| [Design Review Agent](agents/design-review-agent.md) | hybrid | 🟡 beta | Provides early feedback on design, architecture, and security |
| [Accessibility Agent](agents/accessibility-agent.md) | hybrid | 🟡 beta | Automates accessibility checks and bug fixing with code fix proposals |
| [Unit & Scenario Testing Agent](agents/unit-and-scenario-testing-agent.md) | analyst | 🟡 beta | Generates AI-assisted unit and scenario tests for increased coverage |
| [SFI Agent](agents/sfi-agent.md) | hybrid | 🟡 beta | Manages SFI work, tracks KPIs, and automates ADO WIT creation |
| [FUN Report Agent](agents/fun-report-agent.md) | analyst | 🟡 beta | Centralized reporting for LSI/SFI metrics |
| [Release Freshness Agent](agents/release-freshness-agent.md) | analyst | 🟡 beta | Tracks production freshness and follows up on delayed deployments |
| [SRE Agent](agents/sre-agent.md) | orchestrator | 🧪 experimental | Self-serve live site incident assistant with 24×7 monitoring |
| [Onboarding Agent](agents/onboarding-agent.md) | capture | 🧪 experimental | Reduces onboarding time by generating engineering artifacts |
| [Productivity Agent](agents/productivity-agent.md) | analyst | 🧪 experimental | Automates measurement of coding productivity with dashboards |
| [Zero Production Touch](agents/zero-production-touch.md) | orchestrator | ⛔ deprecated | Automated safety dashboard for production changes (on hold) |

### Maturity Legend

| Icon | Level | Description |
|------|-------|-------------|
| 🧪 | `experimental` | Early exploration, expect breaking changes |
| 🟡 | `beta` | Functional but still being refined |
| 🟢 | `stable` | Production-ready |
| ⛔ | `deprecated` | No longer actively maintained |

## Agent Categories

Our SDLC agents are organized using the [Agent Specification](agent-specification.md) categories:

| Category | Description |
|----------|-------------|
| **capture** | Agents that gather and structure information |
| **coach** | Agents that guide and teach users |
| **analyst** | Agents that research, analyze, and report |
| **orchestrator** | Agents that coordinate workflows and other agents |
| **hybrid** | Agents that combine multiple capabilities |

## 🚀 Getting Started

1. **Browse agents** – Review the agent specifications in the [`agents/`](agents/) directory
2. **Understand the spec** – Read the [Agent Specification](agent-specification.md) to understand the taxonomy
3. **Evaluate fit** – Use the risk assessment and adoption prerequisites to determine if an agent suits your needs
4. **Integrate** – Follow the workflow integration section to connect agents to your processes

## 🤝 Contributing

To add a new agent:

1. Create a new `.md` file in the `agents/` directory
2. Follow the [Agent Specification v1.0](agent-specification.md) format
3. Include all required fields for each section
4. Set appropriate maturity level (start with `experimental`)
5. Define clear human checkpoints for any risky operations

## Status Definitions

| Maturity | Description |
|----------|-------------|
| `experimental` | Early exploration, expect breaking changes |
| `beta` | Functional but still being refined |
| `stable` | Production-ready |
| `deprecated` | No longer actively maintained |

## License

Internal use only – AX&E Engineering
