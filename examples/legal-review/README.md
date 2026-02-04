# Legal Contract Review Agent

A governed AI agent for reviewing legal contracts with confidentiality and privilege protection.

## Use Case

Law firms and legal departments need AI assistance for contract review while maintaining:
- Attorney-client privilege
- Document confidentiality
- Audit trails for compliance
- Conflict of interest checks

## Governance Features

| Feature | Implementation |
|---------|----------------|
| **Privilege Protection** | Never expose privileged communications |
| **Confidentiality** | Document content stays within secure boundary |
| **Conflict Check** | Verify no conflicts before accessing matter |
| **Audit Trail** | Log all document access and recommendations |
| **Redaction** | Auto-redact PII in outputs |

## Quick Start

```bash
pip install agent-os-kernel[full]
python main.py
```

## Policy Configuration

```yaml
# policy.yaml
governance:
  name: legal-review-agent
  framework: attorney-privilege
  
permissions:
  document_access:
    - action: read_contract
      requires: [matter_authorization, conflict_cleared]
    - action: generate_summary
      redact: [ssn, account_numbers, signatures]
      
  external_communication:
    - action: send_email
      allowed: false  # No external comms without human approval
    - action: api_call
      allowed: false
      
audit:
  level: comprehensive
  retention_days: 2555  # 7 years for legal
  include:
    - document_id
    - user_id
    - action_type
    - timestamp
    - matter_id
```

## Architecture

```
┌─────────────────────────────────────────────────┐
│              Legal Review Agent                  │
├─────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐              │
│  │  Contract   │  │  Clause     │              │
│  │  Parser     │  │  Analyzer   │              │
│  └──────┬──────┘  └──────┬──────┘              │
│         │                │                      │
│         ▼                ▼                      │
│  ┌─────────────────────────────────┐           │
│  │     Agent OS Governance Layer   │           │
│  │  • Privilege gate               │           │
│  │  • Conflict checker             │           │
│  │  • Redaction filter             │           │
│  │  • Audit logger                 │           │
│  └─────────────────────────────────┘           │
│                    │                            │
│                    ▼                            │
│  ┌─────────────────────────────────┐           │
│  │      Document Management        │           │
│  │   (NetDocuments, iManage)       │           │
│  └─────────────────────────────────┘           │
└─────────────────────────────────────────────────┘
```

## Sample Output

```
Contract Review Summary
=======================
Document: Service Agreement - [REDACTED] Corp
Matter: 2024-M-001234
Reviewer: AI-Legal-Agent-v1

Key Findings:
1. ⚠️  Indemnification clause (§7.2) is broader than standard
2. ⚠️  Limitation of liability excludes gross negligence  
3. ✅ Payment terms align with client requirements
4. ⚠️  IP assignment (§12) needs client review

Recommendations:
- Negotiate cap on indemnification
- Add carve-out for gross negligence
- Flag §12 for partner review

[Audit ID: LR-2024-02-04-0892]
```

## Compliance

- **ABA Model Rules**: Rule 1.6 (Confidentiality)
- **GDPR**: Data minimization in outputs
- **State Bar Requirements**: Varies by jurisdiction
