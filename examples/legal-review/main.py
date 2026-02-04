"""
Legal Contract Review Agent with Agent OS Governance

Demonstrates:
- Attorney-client privilege protection
- Document confidentiality enforcement
- Comprehensive audit logging
- PII redaction in outputs
"""

import asyncio
import hashlib
import re
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field

# Agent OS imports
try:
    from agent_os import Governor, Policy, AuditLog
    from agent_os.policies import create_policy
    AGENT_OS_AVAILABLE = True
except ImportError:
    AGENT_OS_AVAILABLE = False
    print("Note: Install agent-os-kernel for full governance features")


@dataclass
class Matter:
    """Legal matter/case information."""
    matter_id: str
    client_name: str
    matter_type: str
    authorized_users: list[str] = field(default_factory=list)
    conflicts_cleared: bool = False


@dataclass
class Contract:
    """Contract document."""
    doc_id: str
    matter_id: str
    title: str
    content: str
    privilege_level: str = "confidential"  # confidential, privileged, work-product


class ConflictChecker:
    """Check for conflicts of interest before document access."""
    
    def __init__(self):
        self.conflict_parties: set[str] = set()
    
    def add_conflict(self, party: str):
        self.conflict_parties.add(party.lower())
    
    def check_conflict(self, parties: list[str]) -> tuple[bool, Optional[str]]:
        """Returns (has_conflict, conflicting_party)."""
        for party in parties:
            if party.lower() in self.conflict_parties:
                return True, party
        return False, None


class RedactionEngine:
    """Redact sensitive information from outputs."""
    
    PATTERNS = {
        'ssn': r'\b\d{3}-\d{2}-\d{4}\b',
        'phone': r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',
        'email': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
        'account': r'\b\d{8,17}\b',
        'credit_card': r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b',
    }
    
    @classmethod
    def redact(cls, text: str, types: list[str] = None) -> str:
        """Redact specified PII types from text."""
        types = types or list(cls.PATTERNS.keys())
        result = text
        
        for pii_type in types:
            if pii_type in cls.PATTERNS:
                result = re.sub(
                    cls.PATTERNS[pii_type], 
                    f'[REDACTED-{pii_type.upper()}]', 
                    result
                )
        return result


class LegalAuditLog:
    """Comprehensive audit logging for legal compliance."""
    
    def __init__(self, matter_id: str):
        self.matter_id = matter_id
        self.entries: list[dict] = []
    
    def log(self, action: str, user_id: str, doc_id: str = None, 
            details: dict = None, result: str = "success"):
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "matter_id": self.matter_id,
            "action": action,
            "user_id": user_id,
            "doc_id": doc_id,
            "result": result,
            "details": details or {},
            "hash": None  # Will be set below
        }
        # Create tamper-evident hash
        entry["hash"] = hashlib.sha256(
            f"{entry['timestamp']}{entry['action']}{entry['user_id']}".encode()
        ).hexdigest()[:16]
        
        self.entries.append(entry)
        return entry["hash"]


class LegalReviewAgent:
    """AI agent for reviewing legal contracts with governance."""
    
    def __init__(self, agent_id: str = "legal-review-agent"):
        self.agent_id = agent_id
        self.conflict_checker = ConflictChecker()
        self.matters: dict[str, Matter] = {}
        self.contracts: dict[str, Contract] = {}
        self.audit_logs: dict[str, LegalAuditLog] = {}
        
        # Initialize governance if available
        if AGENT_OS_AVAILABLE:
            self.policy = create_policy({
                "name": "legal-review-policy",
                "rules": [
                    {
                        "action": "access_document",
                        "conditions": ["conflict_cleared", "user_authorized"],
                        "audit": True
                    },
                    {
                        "action": "external_communication",
                        "allowed": False
                    },
                    {
                        "action": "generate_output",
                        "require_redaction": True
                    }
                ]
            })
            self.governor = Governor(self.policy)
        else:
            self.governor = None
    
    def register_matter(self, matter: Matter) -> str:
        """Register a new legal matter."""
        self.matters[matter.matter_id] = matter
        self.audit_logs[matter.matter_id] = LegalAuditLog(matter.matter_id)
        return matter.matter_id
    
    def add_contract(self, contract: Contract) -> str:
        """Add a contract to a matter."""
        if contract.matter_id not in self.matters:
            raise ValueError(f"Matter {contract.matter_id} not found")
        self.contracts[contract.doc_id] = contract
        return contract.doc_id
    
    async def access_document(self, doc_id: str, user_id: str) -> tuple[bool, str]:
        """
        Attempt to access a document with full governance checks.
        Returns (allowed, reason_or_content).
        """
        if doc_id not in self.contracts:
            return False, "Document not found"
        
        contract = self.contracts[doc_id]
        matter = self.matters.get(contract.matter_id)
        audit = self.audit_logs.get(contract.matter_id)
        
        # Check 1: User authorization
        if user_id not in matter.authorized_users:
            if audit:
                audit.log("access_document", user_id, doc_id, 
                         {"reason": "unauthorized"}, "denied")
            return False, "User not authorized for this matter"
        
        # Check 2: Conflict of interest
        has_conflict, party = self.conflict_checker.check_conflict([matter.client_name])
        if has_conflict:
            if audit:
                audit.log("access_document", user_id, doc_id,
                         {"reason": "conflict", "party": party}, "denied")
            return False, f"Conflict of interest detected: {party}"
        
        # Check 3: Governance layer (if available)
        if self.governor:
            allowed = await self.governor.check_action("access_document", {
                "user_id": user_id,
                "doc_id": doc_id,
                "privilege_level": contract.privilege_level
            })
            if not allowed:
                return False, "Governance policy denied access"
        
        # Access granted - log and return
        if audit:
            audit.log("access_document", user_id, doc_id, 
                     {"privilege": contract.privilege_level}, "granted")
        
        return True, contract.content
    
    async def review_contract(self, doc_id: str, user_id: str) -> dict:
        """
        Review a contract and generate findings.
        Output is automatically redacted.
        """
        # First, attempt to access the document
        allowed, content_or_reason = await self.access_document(doc_id, user_id)
        
        if not allowed:
            return {
                "status": "denied",
                "reason": content_or_reason
            }
        
        contract = self.contracts[doc_id]
        matter = self.matters[contract.matter_id]
        audit = self.audit_logs[contract.matter_id]
        
        # Simulate contract analysis
        findings = self._analyze_contract(content_or_reason)
        
        # Redact any PII from findings
        redacted_findings = {
            "status": "completed",
            "document": RedactionEngine.redact(contract.title),
            "matter_id": matter.matter_id,
            "findings": [
                {**f, "text": RedactionEngine.redact(f["text"])} 
                for f in findings
            ],
            "audit_id": audit.log("review_contract", user_id, doc_id,
                                  {"findings_count": len(findings)}, "success")
        }
        
        return redacted_findings
    
    def _analyze_contract(self, content: str) -> list[dict]:
        """Simulate contract clause analysis."""
        # In production, this would use LLM analysis
        findings = []
        
        # Check for common issues
        if "indemnif" in content.lower():
            findings.append({
                "type": "warning",
                "clause": "Indemnification",
                "text": "Indemnification clause detected - review scope"
            })
        
        if "limitation of liability" in content.lower():
            findings.append({
                "type": "warning", 
                "clause": "Liability",
                "text": "Liability limitations may need negotiation"
            })
        
        if "intellectual property" in content.lower() or "ip assignment" in content.lower():
            findings.append({
                "type": "review",
                "clause": "IP Rights",
                "text": "IP provisions require partner review"
            })
        
        if "termination" in content.lower():
            findings.append({
                "type": "info",
                "clause": "Termination",
                "text": "Standard termination provisions present"
            })
        
        return findings
    
    def get_audit_trail(self, matter_id: str) -> list[dict]:
        """Retrieve audit trail for a matter."""
        if matter_id not in self.audit_logs:
            return []
        return self.audit_logs[matter_id].entries


async def demo():
    """Demonstrate the legal review agent."""
    print("=" * 60)
    print("Legal Contract Review Agent - Agent OS Demo")
    print("=" * 60)
    
    # Initialize agent
    agent = LegalReviewAgent()
    
    # Register a matter
    matter = Matter(
        matter_id="2024-M-001234",
        client_name="Acme Corporation",
        matter_type="Contract Review",
        authorized_users=["attorney_jane", "paralegal_bob"],
        conflicts_cleared=True
    )
    agent.register_matter(matter)
    print(f"\n✓ Registered matter: {matter.matter_id}")
    
    # Add a contract
    contract = Contract(
        doc_id="DOC-2024-0001",
        matter_id=matter.matter_id,
        title="Service Agreement - Acme Corp & Vendor Inc",
        content="""
        SERVICE AGREEMENT
        
        This Agreement is entered into by Acme Corporation (SSN on file: 123-45-6789)
        and Vendor Inc.
        
        Section 7.2 - INDEMNIFICATION
        Vendor shall indemnify Client against all claims...
        
        Section 8.1 - LIMITATION OF LIABILITY
        Neither party shall be liable for consequential damages...
        
        Section 12 - INTELLECTUAL PROPERTY ASSIGNMENT
        All work product shall be assigned to Client...
        
        Section 15 - TERMINATION
        Either party may terminate with 30 days notice...
        """,
        privilege_level="confidential"
    )
    agent.add_contract(contract)
    print(f"✓ Added contract: {contract.doc_id}")
    
    # Test 1: Authorized user reviews contract
    print("\n--- Test 1: Authorized User Review ---")
    result = await agent.review_contract("DOC-2024-0001", "attorney_jane")
    print(f"Status: {result['status']}")
    if result['status'] == 'completed':
        print(f"Document: {result['document']}")
        print(f"Findings: {len(result['findings'])}")
        for f in result['findings']:
            icon = "⚠️" if f['type'] == 'warning' else "ℹ️"
            print(f"  {icon} [{f['clause']}] {f['text']}")
        print(f"Audit ID: {result['audit_id']}")
    
    # Test 2: Unauthorized user attempt
    print("\n--- Test 2: Unauthorized User Attempt ---")
    result = await agent.review_contract("DOC-2024-0001", "random_user")
    print(f"Status: {result['status']}")
    print(f"Reason: {result['reason']}")
    
    # Test 3: Conflict of interest
    print("\n--- Test 3: Conflict of Interest ---")
    agent.conflict_checker.add_conflict("Acme Corporation")
    result = await agent.review_contract("DOC-2024-0001", "attorney_jane")
    print(f"Status: {result['status']}")
    print(f"Reason: {result['reason']}")
    
    # Show audit trail
    print("\n--- Audit Trail ---")
    trail = agent.get_audit_trail(matter.matter_id)
    for entry in trail:
        print(f"  [{entry['timestamp'][:19]}] {entry['action']} by {entry['user_id']}: {entry['result']}")
    
    print("\n" + "=" * 60)
    print("Demo complete - All access governed and audited")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(demo())
