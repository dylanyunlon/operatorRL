# Agent OS Governance

This document describes the governance model for Agent OS, an open source project for governing autonomous AI agents.

## Overview

Agent OS is committed to building an open, inclusive, and vendor-neutral project. We follow transparent governance practices to ensure the project serves its community and mission.

## Project Mission

To provide a kernel architecture for governing autonomous AI agents through deterministic policy enforcement, enabling safe and trustworthy agent deployment at scale.

## Governance Structure

### Roles and Responsibilities

#### Users

Anyone who uses Agent OS. Users are encouraged to participate by:
- Filing issues and feature requests
- Participating in discussions
- Providing feedback on releases

#### Contributors

Anyone who contributes to the project (code, documentation, tests, reviews). Contributors:
- Submit pull requests
- Review code
- Help triage issues
- Participate in community discussions

#### Maintainers

Maintainers have write access to the repository and are responsible for:
- Reviewing and merging pull requests
- Triaging issues
- Ensuring code quality and test coverage
- Making release decisions
- Enforcing the Code of Conduct

**Current Maintainers:**

| Name | GitHub | Focus Area |
|------|--------|------------|
| Imran Siddique | [@imran-siddique](https://github.com/imran-siddique) | Project Lead, Architecture |

#### Technical Steering Committee (TSC)

The TSC provides technical oversight and strategic direction. Responsibilities:
- Setting technical direction and roadmap
- Resolving technical disputes
- Approving significant architectural changes
- Managing the release process
- Reviewing and accepting new maintainers

**TSC Members:**

| Name | Affiliation | Term |
|------|-------------|------|
| Imran Siddique | Independent | Founding Member |

*Additional TSC members will be elected as the project grows.*

### Becoming a Maintainer

New maintainers are nominated by existing maintainers based on:
- Sustained, high-quality contributions over 6+ months
- Demonstrated understanding of the project's architecture and goals
- Positive interactions with the community
- Commitment to the project's Code of Conduct

The nomination process:
1. Existing maintainer nominates candidate
2. TSC reviews contribution history and community feedback
3. TSC votes (requires 2/3 majority)
4. Candidate accepts responsibilities

### Stepping Down

Maintainers may step down at any time by notifying the TSC. Maintainers inactive for 12 months may be moved to emeritus status after outreach.

## Decision Making

### Consensus-Based Decisions

Most decisions are made through lazy consensus:
1. A proposal is made (issue, PR, or discussion)
2. Community members provide feedback
3. If no objections after a reasonable period (typically 72 hours for minor changes, 1 week for significant changes), the proposal is accepted

### Voting

When consensus cannot be reached, decisions are made by vote:
- **Maintainers:** Simple majority for code/documentation changes
- **TSC:** 2/3 majority for governance, architectural, or strategic decisions

Voting is conducted openly via GitHub issues or discussions.

### Appeal Process

Any contributor may appeal a decision by:
1. Opening an issue with `[APPEAL]` prefix
2. Clearly stating the decision being appealed and rationale
3. TSC reviews and makes final decision within 2 weeks

## Code of Conduct

All participants must adhere to our [Code of Conduct](CODE_OF_CONDUCT.md). The TSC is responsible for enforcement.

Reports can be made to: **conduct@agent-os.dev** (or via GitHub issues for non-sensitive matters)

## Intellectual Property

### License

Agent OS is licensed under the [MIT License](LICENSE).

### Contributions

By contributing to Agent OS, you agree that:
- Your contributions are your original work (or you have the right to submit them)
- You license your contributions under the project's MIT License
- You have read and agree to the [Developer Certificate of Origin (DCO)](https://developercertificate.org/)

All commits should be signed off (`git commit -s`) to indicate DCO agreement.

### Trademark

The "Agent OS" name and logo are trademarks of the project. Usage guidelines will be published separately.

## Project Resources

| Resource | Location |
|----------|----------|
| Source Code | [github.com/imran-siddique/agent-os](https://github.com/imran-siddique/agent-os) |
| Documentation | [imran-siddique.github.io/agent-os-docs](https://imran-siddique.github.io/agent-os-docs/) |
| Issue Tracker | [GitHub Issues](https://github.com/imran-siddique/agent-os/issues) |
| Discussions | [GitHub Discussions](https://github.com/imran-siddique/agent-os/discussions) |
| Security Reports | See [SECURITY.md](SECURITY.md) |

## Releases

### Release Process

1. Maintainer proposes release (version, changelog, date)
2. Release candidate is tagged and tested
3. Community has 72 hours to report blocking issues
4. Final release is tagged and published to PyPI
5. Release notes are published on GitHub

### Versioning

Agent OS follows [Semantic Versioning](https://semver.org/):
- **MAJOR:** Breaking changes to public APIs
- **MINOR:** New features, backward compatible
- **PATCH:** Bug fixes, backward compatible

## Amendments

This governance document may be amended by:
1. Opening a PR with proposed changes
2. Discussion period of at least 2 weeks
3. TSC vote (2/3 majority required)

## Acknowledgments

This governance model is inspired by:
- [Apache Software Foundation](https://www.apache.org/foundation/governance/)
- [Cloud Native Computing Foundation](https://github.com/cncf/foundation/blob/main/charter.md)
- [Kubernetes Governance](https://github.com/kubernetes/community/blob/master/governance.md)

---

*Last updated: February 2026*
