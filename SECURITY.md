# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.x.x   | :white_check_mark: |

Once Agent OS reaches 1.0, we will maintain security updates for the latest minor version.

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

If you discover a security vulnerability in Agent OS, please report it responsibly:

### 1. Email (Preferred)

Send an email to: **security@agent-os.dev**

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fixes (optional)

### 2. GitHub Security Advisories

You can also report via [GitHub Security Advisories](https://github.com/imran-siddique/agent-os/security/advisories/new).

### 3. Encrypted Communication

For sensitive reports, you may encrypt your message using our PGP key:

```
-----BEGIN PGP PUBLIC KEY BLOCK-----
[PGP key to be added]
-----END PGP PUBLIC KEY BLOCK-----
```

Key fingerprint: `[To be added]`

## Response Timeline

| Stage | Timeline |
|-------|----------|
| Initial acknowledgment | Within 48 hours |
| Initial assessment | Within 1 week |
| Fix development | Varies by severity |
| Public disclosure | Coordinated with reporter |

## Severity Classification

We use the following severity levels:

| Severity | Description | Response Time |
|----------|-------------|---------------|
| **Critical** | Remote code execution, complete bypass of safety policies | 24-48 hours |
| **High** | Partial bypass of safety policies, data exposure | 1 week |
| **Medium** | Denial of service, information leakage | 2 weeks |
| **Low** | Minor issues, hardening opportunities | Next release |

## Security Model

### What Agent OS Protects Against

Agent OS provides **application-level policy enforcement**:

- ✅ Deterministic policy checks on agent actions
- ✅ SQL injection prevention (via policy rules)
- ✅ File system access restrictions (configurable paths)
- ✅ Rate limiting and resource controls
- ✅ Action logging and audit trails (Flight Recorder)

### What Agent OS Does NOT Protect Against

Agent OS is middleware, not a security sandbox:

- ❌ Memory corruption or process isolation (use containers)
- ❌ Compromised LLM providers
- ❌ Network-level attacks
- ❌ Supply chain attacks on dependencies

**For production deployments, we recommend:**
- Running agents in isolated containers
- Using network policies to restrict agent communication
- Monitoring the Flight Recorder for anomalies
- Regular dependency audits

## Security Best Practices

### For Users

1. **Keep Agent OS updated** - Always run the latest version
2. **Use restrictive policies** - Start with minimal permissions, expand as needed
3. **Enable Flight Recorder** - Log all agent actions for audit
4. **Review policies regularly** - Ensure policies match current requirements
5. **Run in containers** - Isolate agents for defense in depth

### For Contributors

1. **Sign commits** - Use `git commit -s` (DCO) and GPG signing
2. **Review dependencies** - Check for known vulnerabilities before adding
3. **Write secure code** - Follow OWASP guidelines
4. **Add tests** - Include security-relevant test cases
5. **Document security implications** - Note any security considerations in PRs

## Dependency Management

We monitor dependencies for known vulnerabilities using:
- GitHub Dependabot
- Regular security audits

## Disclosure Policy

We follow coordinated disclosure:

1. Reporter notifies us privately
2. We confirm and assess the vulnerability
3. We develop and test a fix
4. We coordinate disclosure timing with the reporter
5. Fix is released with security advisory
6. Credit is given to the reporter (unless they prefer anonymity)

## Security Advisories

Published security advisories are available at:
[github.com/imran-siddique/agent-os/security/advisories](https://github.com/imran-siddique/agent-os/security/advisories)

## Hall of Fame

We recognize security researchers who responsibly disclose vulnerabilities:

*No submissions yet - be the first!*

## Contact

- Security issues: security@agent-os.dev
- General questions: [GitHub Discussions](https://github.com/imran-siddique/agent-os/discussions)

---

*Last updated: February 2026*
