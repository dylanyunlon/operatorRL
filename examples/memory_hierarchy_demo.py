"""
Simple example demonstrating the Adaptive Memory Hierarchy.

This shows how to use the MemoryController to manage lessons across
three tiers for optimal context efficiency.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.kernel.memory import MemoryController
from src.kernel.schemas import Lesson, PatchRequest


def main():
    print("=" * 70)
    print("ADAPTIVE MEMORY HIERARCHY - SIMPLE EXAMPLE")
    print("=" * 70)
    print()
    
    # Initialize the controller
    controller = MemoryController()
    print("✅ MemoryController initialized\n")
    
    # Example 1: Add a security lesson (goes to Tier 1 - Kernel)
    print("1️⃣  Adding a SECURITY lesson...")
    security_lesson = Lesson(
        trigger_pattern="authentication check",
        rule_text="Always validate JWT tokens before processing requests",
        lesson_type="security",
        confidence_score=0.95
    )
    security_patch = PatchRequest(
        trace_id="trace-001",
        diagnosis="Missing authentication check in API endpoint",
        proposed_lesson=security_lesson,
        apply_strategy="hotfix_now"
    )
    
    result = controller.commit_lesson(security_patch)
    print(f"   → Committed to: {result['tier']}")
    print(f"   → Location: {result.get('location', 'N/A')}\n")
    
    # Example 2: Add a tool-specific lesson (goes to Tier 2 - Skill Cache)
    print("2️⃣  Adding a TOOL-SPECIFIC lesson...")
    sql_lesson = Lesson(
        trigger_pattern="tool:sql_query",
        rule_text="When querying large tables, always use LIMIT to prevent memory issues",
        lesson_type="syntax",
        confidence_score=0.88
    )
    sql_patch = PatchRequest(
        trace_id="trace-002",
        diagnosis="SQL query returned too many rows causing timeout",
        proposed_lesson=sql_lesson,
        apply_strategy="batch_later"
    )
    
    result = controller.commit_lesson(sql_patch)
    print(f"   → Committed to: {result['tier']}")
    print(f"   → Tool: {result.get('tool', 'N/A')}\n")
    
    # Example 3: Add a business lesson (goes to Tier 3 - Archive)
    print("3️⃣  Adding a BUSINESS LOGIC lesson...")
    business_lesson = Lesson(
        trigger_pattern="fiscal year reporting",
        rule_text="Fiscal year starts in July, not January, for all financial reports",
        lesson_type="business",
        confidence_score=0.85
    )
    business_patch = PatchRequest(
        trace_id="trace-003",
        diagnosis="Agent used calendar year instead of fiscal year",
        proposed_lesson=business_lesson,
        apply_strategy="batch_later"
    )
    
    result = controller.commit_lesson(business_patch)
    print(f"   → Committed to: {result['tier']}")
    print(f"   → Location: {result.get('location', 'N/A')}\n")
    
    # Example 4: Retrieve context for different scenarios
    print("=" * 70)
    print("CONTEXT RETRIEVAL EXAMPLES")
    print("=" * 70)
    print()
    
    # Scenario A: Simple task with no tools
    print("📝 Scenario A: Simple greeting (no tools active)")
    context = controller.retrieve_context(
        current_task="Hello",
        active_tools=[]
    )
    lines = [l for l in context.split('\n') if l.strip()]
    print(f"   → Context size: {len(lines)} lines")
    print(f"   → Includes security rules: {'CRITICAL SAFETY RULES' in context}")
    print(f"   → Includes SQL rules: {'SQL' in context}")
    print()
    
    # Scenario B: SQL query with SQL tool active
    print("📝 Scenario B: SQL query (SQL tool active)")
    context = controller.retrieve_context(
        current_task="Query the users table for active accounts",
        active_tools=["sql_query"]
    )
    lines = [l for l in context.split('\n') if l.strip()]
    print(f"   → Context size: {len(lines)} lines")
    print(f"   → Includes security rules: {'CRITICAL SAFETY RULES' in context}")
    print(f"   → Includes SQL rules: {'sql_query' in context.lower()}")
    print()
    
    # Scenario C: Complex task that triggers semantic search
    print("📝 Scenario C: Complex business query")
    context = controller.retrieve_context(
        current_task="Generate the fiscal year 2024 financial report",
        active_tools=[]
    )
    lines = [l for l in context.split('\n') if l.strip()]
    print(f"   → Context size: {len(lines)} lines")
    print(f"   → Includes security rules: {'CRITICAL SAFETY RULES' in context}")
    print(f"   → Includes relevant lessons: {'Relevant Past Lessons' in context}")
    print(f"   → Retrieved fiscal year info: {'fiscal year' in context.lower()}")
    print()
    
    # Summary
    print("=" * 70)
    print("KEY TAKEAWAYS")
    print("=" * 70)
    print()
    print("✅ Deterministic Routing:")
    print("   - Security lessons → Tier 1 (always active)")
    print("   - Tool lessons → Tier 2 (injected when tool is used)")
    print("   - Business lessons → Tier 3 (retrieved on-demand)")
    print()
    print("✅ Context Efficiency:")
    print("   - Simple tasks: minimal context (only Tier 1)")
    print("   - Tool tasks: targeted context (Tier 1 + relevant Tier 2)")
    print("   - Complex tasks: full context (Tier 1 + Tier 2 + Tier 3)")
    print()
    print("✅ Scale by Subtraction:")
    print("   - Only load what's needed")
    print("   - Promote hot lessons (Tier 3 → Tier 2)")
    print("   - Demote cold lessons (Tier 1 → Tier 2)")
    print()


if __name__ == "__main__":
    main()
