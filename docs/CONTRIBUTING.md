# Contributing Guidelines

Thank you for contributing to the SDLC Agents repository! This document provides guidelines for contributing to this project.

## Table of Contents

- [Getting Started](#getting-started)
- [How to Contribute](#how-to-contribute)
- [Adding a New Agent](#adding-a-new-agent)
- [Updating an Agent](#updating-an-agent)
- [Documentation Standards](#documentation-standards)
- [Pull Request Process](#pull-request-process)

---

## Getting Started

1. Clone the repository
2. Read the [Agent Specification v1.0](../agent-specification.md) to understand the taxonomy
3. Review existing agents in the `agents/` directory for examples
4. Make your changes
5. Submit a pull request

---

## How to Contribute

| Contribution Type | Description |
|-------------------|-------------|
| **Add a new agent** | Document a new SDLC agent following the spec |
| **Update an agent** | Keep agent status and documentation current |
| **Improve documentation** | Enhance existing documentation |
| **Fix issues** | Address any issues or inconsistencies |

---

## Adding a New Agent

### Step 1: Create Agent File

Create a new file in the `agents/` directory:

```
agents/[agent-name].md
```

Use the template structure from existing agents. Key sections:

1. **YAML frontmatter** — name, version, description, category, maturity, owner, last-validated
2. **Vision** — What problem does this solve?
3. **Capabilities** — Tools, integrations, context files
4. **Risk Assessment** — Autonomy, blast radius, checkpoints, failure modes
5. **Workflow Integration** — Triggers, inputs, outputs, flow diagram
6. **Evaluation & Adoption** — Metrics, status, prerequisites
7. **Governance** — Owner, changelog

### Step 2: Update README.md

Add your agent to the [README.md](../README.md):

1. Add to the **Quick Reference** table
2. Add to the appropriate **SDLC Phase** section
3. Update the **Status Summary** counts if needed

### Step 3: Submit Pull Request

- Use a clear title: "Add [Agent Name] documentation"
- Describe what the agent does and why it's needed
- Start with `experimental` maturity

---

## Updating an Agent

When updating existing agent information:

1. **Edit the agent file** in `agents/`
2. **Bump the version** if making significant changes
3. **Update `last-validated`** date
4. **Add changelog entry** explaining what changed
5. **Update README.md** if status or description changed

---

## Documentation Standards

### File Naming

- Use lowercase with hyphens: `agent-name.md`
- Be descriptive but concise

### Required Sections

All agent documentation must include:

| Section | Required |
|---------|----------|
| YAML frontmatter | ✅ |
| Vision / Description | ✅ |
| Capabilities (tools, integrations) | ✅ |
| Risk Assessment | ✅ |
| Workflow Integration | ✅ |
| Evaluation & Adoption | ✅ |
| Governance (owner, changelog) | ✅ |

### Maturity Levels

| Level | When to Use |
|-------|-------------|
| `experimental` | New agents, early exploration |
| `beta` | Functional, being refined, some adoption |
| `stable` | Production-ready, widely adopted |
| `deprecated` | No longer maintained, on hold |

---

## Pull Request Process

1. **Create a descriptive PR title**
   - ✅ Good: "Add Code Review Agent documentation"
   - ❌ Bad: "Update files"

2. **Provide context**
   - What are you adding/changing?
   - Why is this change needed?

3. **Keep changes focused**
   - One agent per PR when adding new agents

4. **Self-review**
   - Check for typos and formatting
   - Ensure all links work
   - Verify README counts are correct

---

## Questions?

If you have questions:
- Open an issue for discussion
- Check existing documentation first
- Review similar agents for examples

Thank you for helping improve SDLC Agents! 🚀