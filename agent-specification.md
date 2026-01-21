# Agent Specification v1.0

> A formal taxonomy for describing AI agents—enabling humans to evaluate usefulness, detect duplication, assess adoption fit, understand risk, and determine workflow integration.

---

## 1. Metadata

Core identity and discoverability fields.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | ✅ | Short identifier for the agent |
| `version` | string | ✅ | Semantic version (e.g., `1.0.0`) |
| `description` | string | ✅ | One-line purpose statement |
| `category` | enum | ✅ | `capture` · `coach` · `analyst` · `orchestrator` · `hybrid` |
| `maturity` | enum | ✅ | `experimental` · `beta` · `stable` · `deprecated` |
| `supersedes` | string[] | | Agents this replaces |
| `related-agents` | string[] | | Similar or complementary agents |

### Example

```yaml
name: PlanningAgent
version: 1.0.0
description: Researches and outlines multi-step plans
category: analyst
maturity: stable
supersedes: [LegacyPlannerV1]
related-agents: [ResearchAgent, ExecutionAgent]
```

---

## 2. Capabilities

What the agent can do and how it connects to other systems.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tools` | string[] | ✅ | Explicit list of tools agent can invoke |
| `handoffs` | object[] | | Exit points to other agents/modes |
| `integrations` | string[] | | External systems (MCP tools, APIs, IDEs) |
| `orchestration-role` | enum | ✅ | `standalone` · `coordinator` · `worker` |
| `context-files` | string[] | | Config files agent loads for context |

### Handoff Schema

```yaml
handoffs:
  - label: Start Implementation    # Button/action label
    agent: ImplementationAgent     # Target agent
    prompt: "Begin coding"         # Prompt passed to target
    send: true                     # Auto-send or pause for edit
```

### Example

```yaml
tools: [search, read_file, runSubagent, grep_search]
handoffs:
  - label: Start Implementation
    agent: agent
    prompt: Start implementation
integrations: [MCP get_current_time, GitHub API]
orchestration-role: coordinator
context-files: [background.md, attention_charter.md]
```

---

## 3. Risk Assessment

Understand what can go wrong and how to mitigate.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `autonomy-level` | enum | ✅ | `guided` · `semi-autonomous` · `fully-autonomous` |
| `blast-radius` | enum | ✅ | `local-file` · `workspace` · `external-system` · `irreversible` |
| `reversibility` | enum | ✅ | `fully` · `partially` · `not-reversible` |
| `data-sensitivity` | enum | ✅ | `none` · `internal-only` · `PII` · `credentials` |
| `human-checkpoints` | string[] | | Points where human approval is required |
| `cost-profile` | enum | | `minimal` · `moderate` · `high` · `variable` |
| `failure-modes` | string[] | | Known ways the agent can fail |

### Autonomy Level Definitions

| Level | Description |
|-------|-------------|
| **guided** | Step-by-step with human at each decision |
| **semi-autonomous** | Runs independently but pauses at key checkpoints |
| **fully-autonomous** | Executes end-to-end without intervention |

### Blast Radius Definitions

| Level | Description |
|-------|-------------|
| **local-file** | Only affects a single file |
| **workspace** | Can modify multiple files in workspace |
| **external-system** | Interacts with APIs, databases, or external services |
| **irreversible** | Actions cannot be undone (e.g., sending emails, deleting cloud resources) |

### Example

```yaml
autonomy-level: semi-autonomous
blast-radius: workspace
reversibility: fully
data-sensitivity: internal-only
human-checkpoints:
  - Before file writes
  - Before external API calls
cost-profile: moderate
failure-modes:
  - Incomplete context leading to shallow plans
  - Hallucinated file paths
  - Over-scoped recommendations
```

---

## 4. Workflow Integration

How the agent fits into broader processes and other agents.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `trigger-scenarios` | string[] | ✅ | When to invoke this agent |
| `input-contract` | object[] | ✅ | Required inputs and their formats |
| `output-contract` | object[] | ✅ | What agent produces and format |
| `upstream-agents` | string[] | | Agents that feed into this one |
| `downstream-agents` | string[] | | Agents that consume this output |
| `persona` | string | | Identity and tone of the agent |

### Contract Schema

```yaml
input-contract:
  - name: task_description
    type: string
    required: true
    description: Natural language description of the goal

output-contract:
  - name: plan
    type: markdown
    location: stdout | file
    description: Structured plan with steps and considerations
```

### Example

```yaml
trigger-scenarios:
  - Starting a new feature with unclear requirements
  - Breaking down a complex task
  - When implementation approach is uncertain
input-contract:
  - name: task_description
    type: string
    required: true
  - name: codebase_context
    type: files
    required: false
output-contract:
  - name: plan
    type: markdown
    location: stdout
upstream-agents: [RequirementsGatherer, ContextLoader]
downstream-agents: [ImplementationAgent, TestAgent]
persona: Thoughtful technical architect who asks clarifying questions
```

---

## 5. Evaluation & Adoption

How to measure success and what's needed to adopt.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `success-metrics` | string[] | | How to measure good output |
| `time-to-value` | string | | How long until useful output |
| `adoption-prerequisites` | string[] | | Skills, tools, or context needed |
| `learning-curve` | enum | | `minimal` · `moderate` · `steep` |

### Example

```yaml
success-metrics:
  - Plan has 3-6 actionable steps
  - User approves plan without major revision
  - Implementation succeeds following plan
time-to-value: 2-5 minutes
adoption-prerequisites:
  - VS Code with GitHub Copilot
  - Familiarity with codebase structure
  - Basic understanding of task scope
learning-curve: minimal
```

---

## 6. Governance

Ownership, versioning, and lifecycle management.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `owner` | string | | Person or team responsible |
| `last-validated` | date | | Date spec was last tested |
| `changelog` | string[] | | Version history summary |
| `deprecation-policy` | string | | How end-of-life is handled |

### Example

```yaml
owner: "@seanrich"
last-validated: 2026-01-21
changelog:
  - "1.0.0: Initial stable release"
  - "0.9.0: Added risk assessment section"
  - "0.8.0: Beta with core workflow"
deprecation-policy: 30-day notice with migration guide to successor
```

---

## Full Example: Planning Agent

```yaml
---
# METADATA
name: PlanningAgent
version: 1.0.0
description: Researches codebase and outlines multi-step implementation plans
category: analyst
maturity: stable
supersedes: []
related-agents: [ResearchAgent, ImplementationAgent]

# CAPABILITIES
tools: [search, read_file, runSubagent, grep_search, semantic_search]
handoffs:
  - label: Start Implementation
    agent: ImplementationAgent
    prompt: Start implementation
  - label: Open in Editor
    agent: agent
    prompt: Create plan as untitled file
integrations: [VS Code, GitHub]
orchestration-role: coordinator
context-files: []

# RISK ASSESSMENT
autonomy-level: semi-autonomous
blast-radius: local-file
reversibility: fully
data-sensitivity: internal-only
human-checkpoints:
  - Plan review before handoff
cost-profile: moderate
failure-modes:
  - Incomplete context
  - Over-scoped plans
  - Missing edge cases

# WORKFLOW INTEGRATION
trigger-scenarios:
  - New feature development
  - Complex refactoring
  - Unclear requirements
input-contract:
  - name: task_description
    type: string
    required: true
output-contract:
  - name: plan
    type: markdown
    location: stdout
upstream-agents: []
downstream-agents: [ImplementationAgent, TestAgent]
persona: Thoughtful technical architect

# EVALUATION & ADOPTION
success-metrics:
  - Plan has 3-6 actionable steps
  - User approves without major revision
time-to-value: 2-5 minutes
adoption-prerequisites:
  - VS Code with Copilot
learning-curve: minimal

# GOVERNANCE
owner: "@copilot"
last-validated: 2026-01-21
changelog:
  - "1.0.0: Initial release"
deprecation-policy: N/A
---
```

---

## Appendix: Enum Reference

### category
| Value | Description |
|-------|-------------|
| `capture` | Prompts user and saves structured data |
| `coach` | Multi-turn conversational guidance |
| `analyst` | Reads inputs, processes, generates output |
| `orchestrator` | Coordinates multiple agents |
| `hybrid` | Combines multiple patterns |

### maturity
| Value | Description |
|-------|-------------|
| `experimental` | Early testing, expect breaking changes |
| `beta` | Functional but still evolving |
| `stable` | Production-ready, versioned |
| `deprecated` | Scheduled for removal |

### autonomy-level
| Value | Description |
|-------|-------------|
| `guided` | Human at each decision point |
| `semi-autonomous` | Pauses at key checkpoints |
| `fully-autonomous` | Runs end-to-end independently |

### blast-radius
| Value | Description |
|-------|-------------|
| `local-file` | Single file impact |
| `workspace` | Multiple workspace files |
| `external-system` | APIs, databases, external services |
| `irreversible` | Cannot be undone |

### reversibility
| Value | Description |
|-------|-------------|
| `fully` | All actions can be undone |
| `partially` | Some actions permanent |
| `not-reversible` | Actions cannot be undone |

### data-sensitivity
| Value | Description |
|-------|-------------|
| `none` | No data access |
| `internal-only` | Workspace/project data only |
| `PII` | Personally identifiable information |
| `credentials` | Secrets, tokens, passwords |

### learning-curve
| Value | Description |
|-------|-------------|
| `minimal` | Use immediately with little training |
| `moderate` | Some learning required |
| `steep` | Significant expertise needed |
