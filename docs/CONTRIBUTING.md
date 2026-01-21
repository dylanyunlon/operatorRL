# Contributing Guidelines

Thank you for contributing to the SDLC Agents repository! This document provides guidelines for contributing to this project.

## Table of Contents

- [Getting Started](#getting-started)
- [How to Contribute](#how-to-contribute)
- [Adding a New Agent](#adding-a-new-agent)
- [Updating Agent Information](#updating-agent-information)
- [Documentation Standards](#documentation-standards)
- [Pull Request Process](#pull-request-process)

## Getting Started

1. Fork the repository
2. Clone your fork locally
3. Create a new branch for your changes
4. Make your changes
5. Submit a pull request

## How to Contribute

There are several ways to contribute to this repository:

1. **Add a new agent** - Document a new SDLC agent
2. **Update agent information** - Keep agent status and documentation current
3. **Improve documentation** - Enhance existing documentation
4. **Fix issues** - Address any issues or inconsistencies

## Adding a New Agent

To add a new agent to the repository:

### Step 1: Create Agent Documentation

1. Navigate to the appropriate category directory under `agents/`
2. Copy the template from `agents/templates/AGENT_TEMPLATE.md`
3. Create a new file named `[agent-name].md` in the category directory
4. Fill out all sections of the template with relevant information

Example:
```bash
cp agents/templates/AGENT_TEMPLATE.md agents/development/code-review-agent.md
```

### Step 2: Update AGENTS.md

Add an entry for your agent in the [AGENTS.md](../AGENTS.md) file:

1. Find the appropriate category section
2. Add your agent following this format:

```markdown
### [Agent Name](agents/category/agent-name.md)

**Status:** Status  
**Owner:** Your Name/Team  
**Description:** Brief description  
**Last Updated:** YYYY-MM-DD
```

3. Update the summary count table at the top of the file

### Step 3: Create a Pull Request

Submit a pull request with your changes. Make sure to:
- Use a clear and descriptive title
- Describe what agent you're adding and why
- Link to any relevant issues or discussions

## Updating Agent Information

When updating existing agent information:

1. Edit the agent's documentation file in the `agents/` directory
2. Update the **Last Updated** date
3. If the status changes, update both the agent doc and AGENTS.md
4. Update the summary count in AGENTS.md if status changed
5. Submit a pull request with your changes

## Documentation Standards

### File Naming

- Use lowercase with hyphens for file names: `agent-name.md`
- Be descriptive but concise
- Avoid special characters

### Markdown Style

- Use proper markdown formatting
- Include code blocks with language specifications
- Use tables for structured data
- Keep line length reasonable (80-120 characters)

### Required Information

All agent documentation must include:
- Clear overview and description
- Current status
- Owner/responsible party
- Last updated date
- Technical details
- Usage instructions

### Status Updates

When updating agent status:
- Always update the **Last Updated** date
- Add a changelog entry explaining what changed
- Update the AGENTS.md summary counts

## Pull Request Process

1. **Create a descriptive PR title**
   - Good: "Add Code Review Agent documentation"
   - Bad: "Update files"

2. **Provide context in PR description**
   - What are you adding/changing?
   - Why is this change needed?
   - Link to related issues

3. **Keep changes focused**
   - One agent per PR when adding new agents
   - Group related updates together

4. **Review your own PR**
   - Check for typos and formatting
   - Ensure all links work
   - Verify status counts are correct

5. **Respond to feedback**
   - Address reviewer comments
   - Make requested changes promptly
   - Ask questions if unclear

## Questions?

If you have questions about contributing, please:
- Open an issue for discussion
- Reach out to the repository maintainers
- Check existing documentation and issues first

Thank you for helping make SDLC Agents better! 🚀
