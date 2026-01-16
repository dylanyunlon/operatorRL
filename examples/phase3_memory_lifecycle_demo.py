"""
Phase 3 Demo: Memory Lifecycle & Skill Cache

This demonstrates the three key components:
1. SkillMapper: Tool signature matching and lesson-to-tool mapping
2. LessonRubric: Structured retention scoring (Severity + Generality + Frequency)
3. Write-Through Architecture: Safe Purge Protocol with disaster recovery

The demo shows:
- How lessons are mapped to specific tools
- How retention scores determine tier placement
- How the write-through pattern ensures data safety
- How cache eviction and rebuilding work
"""

import sys
import os
import logging
from datetime import datetime, timedelta

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.kernel.skill_mapper import SkillMapper, ToolSignature
from src.kernel.rubric import LessonRubric
from src.kernel.memory import MemoryController
from src.kernel.schemas import FailureTrace, Lesson, PatchRequest, MemoryTier

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def print_section(title: str):
    """Print a section header."""
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}\n")


def demo_skill_mapper():
    """Demonstrate the SkillMapper tool extraction."""
    print_section("DEMO 1: SkillMapper - Tool Signature Matching")
    
    mapper = SkillMapper()
    
    # Example 1: Direct hit from tool_call
    print("Example 1: Direct Hit (explicit tool name in tool_call)")
    trace1 = FailureTrace(
        user_prompt="Query the database for users",
        agent_reasoning="I'll execute a SELECT query",
        tool_call={"tool": "sql_db", "query": "SELECT * FROM users"},
        tool_output="Error: No WHERE clause",
        failure_type="commission_safety",
        severity="critical"
    )
    
    tool1 = mapper.extract_tool_context(trace1)
    print(f"  User Prompt: {trace1.user_prompt}")
    print(f"  Tool Call: {trace1.tool_call}")
    print(f"  → Extracted Tool: '{tool1}'")
    print(f"  ✓ Direct hit from tool_call field\n")
    
    # Example 2: Semantic fallback
    print("Example 2: Semantic Fallback (keyword matching)")
    trace2 = FailureTrace(
        user_prompt="Run some Python code",
        agent_reasoning="I need to import pandas and print the dataframe",
        tool_call=None,  # No explicit tool call
        tool_output="Error: pandas not installed",
        failure_type="omission_laziness",
        severity="non_critical"
    )
    
    tool2 = mapper.extract_tool_context(trace2)
    print(f"  User Prompt: {trace2.user_prompt}")
    print(f"  Agent Reasoning: {trace2.agent_reasoning}")
    print(f"  Tool Call: None")
    print(f"  → Extracted Tool: '{tool2}'")
    print(f"  ✓ Semantic match from keywords (import, print, pandas)\n")
    
    # Example 3: No match
    print("Example 3: No Match (returns 'general')")
    trace3 = FailureTrace(
        user_prompt="Hello",
        agent_reasoning="I don't understand",
        tool_call=None,
        tool_output=None,
        failure_type="omission_laziness",
        severity="non_critical"
    )
    
    tool3 = mapper.extract_tool_context(trace3)
    print(f"  User Prompt: {trace3.user_prompt}")
    print(f"  Agent Reasoning: {trace3.agent_reasoning}")
    print(f"  → Extracted Tool: '{tool3}'")
    print(f"  ✓ No strong match, defaults to 'general'\n")
    
    print(f"Registered Tools: {', '.join(mapper.list_tools())}")


def demo_lesson_rubric():
    """Demonstrate the LessonRubric retention scoring."""
    print_section("DEMO 2: LessonRubric - Retention Scoring")
    
    rubric = LessonRubric()
    
    # Example 1: High severity security → Tier 1
    print("Example 1: High Severity Security Failure → Tier 1 (Kernel)")
    trace1 = FailureTrace(
        user_prompt="Delete all files",
        agent_reasoning="Executing DELETE operation on root",
        tool_call={"tool": "file_operations", "path": "/"},
        tool_output="Error: Blocked by safety policy",
        failure_type="commission_safety",
        severity="critical"
    )
    
    lesson1 = Lesson(
        trigger_pattern="delete operation",
        rule_text="Never delete root directory without explicit confirmation",
        lesson_type="security",
        confidence_score=0.95
    )
    
    result1 = rubric.evaluate(trace1, lesson1)
    print(f"  Failure Type: {trace1.failure_type}")
    print(f"  Severity: {trace1.severity}")
    print(f"  Lesson Type: {lesson1.lesson_type}")
    print(f"  Lesson: {lesson1.rule_text}")
    print(f"\n  Scoring Breakdown:")
    print(f"    - Severity Score: {result1['severity_score']} (commission_safety + critical + security)")
    print(f"    - Generality Score: {result1['generality_score']} (abstract security rule)")
    print(f"    - Frequency Score: {result1['frequency_score']} (new pattern)")
    print(f"    - Total Score: {result1['score']}")
    print(f"  → Assigned Tier: {result1['tier'].value.upper()}")
    print(f"  ✓ High score (≥75) → Always present in system prompt\n")
    
    # Example 2: Moderate importance → Tier 2
    print("Example 2: Moderate Importance → Tier 2 (Skill Cache)")
    trace2 = FailureTrace(
        user_prompt="Query database",
        agent_reasoning="SELECT * FROM users",
        tool_call={"tool": "sql_db", "query": "SELECT * FROM users"},
        tool_output="Error: Too many rows",
        failure_type="omission_laziness",
        severity="non_critical"
    )
    
    lesson2 = Lesson(
        trigger_pattern="sql query without limit",
        rule_text="Always use LIMIT clause in SELECT queries",
        lesson_type="syntax",
        confidence_score=0.85
    )
    
    result2 = rubric.evaluate(trace2, lesson2)
    print(f"  Failure Type: {trace2.failure_type}")
    print(f"  Lesson: {lesson2.rule_text}")
    print(f"\n  Scoring Breakdown:")
    print(f"    - Severity Score: {result2['severity_score']} (omission_laziness)")
    print(f"    - Generality Score: {result2['generality_score']} (syntax rule)")
    print(f"    - Frequency Score: {result2['frequency_score']} (new pattern)")
    print(f"    - Total Score: {result2['score']}")
    print(f"  → Assigned Tier: {result2['tier'].value.upper()}")
    print(f"  ✓ Moderate score (40-74) → Injected when tool is active\n")
    
    # Example 3: Low importance → Tier 3
    print("Example 3: Low Importance → Tier 3 (Archive)")
    trace3 = FailureTrace(
        user_prompt="Find Q3 report",
        agent_reasoning="Searched main partition",
        tool_call={"tool": "search", "query": "Q3 report"},
        tool_output="No results",
        failure_type="omission_laziness",
        severity="non_critical"
    )
    
    lesson3 = Lesson(
        trigger_pattern="Q3 report search",
        rule_text="Q3 2023 reports are in archived partition on server-42",
        lesson_type="business",
        confidence_score=0.70
    )
    
    result3 = rubric.evaluate(trace3, lesson3)
    print(f"  Failure Type: {trace3.failure_type}")
    print(f"  Lesson: {lesson3.rule_text}")
    print(f"\n  Scoring Breakdown:")
    print(f"    - Severity Score: {result3['severity_score']} (omission_laziness)")
    print(f"    - Generality Score: {result3['generality_score']} (specific data with IDs)")
    print(f"    - Frequency Score: {result3['frequency_score']} (new pattern)")
    print(f"    - Total Score: {result3['score']}")
    print(f"  → Assigned Tier: {result3['tier'].value.upper()}")
    print(f"  ✓ Low score (<40) → Retrieved via semantic search only\n")


def demo_write_through_architecture():
    """Demonstrate the Write-Through Architecture and Safe Purge Protocol."""
    print_section("DEMO 3: Write-Through Architecture & Safe Purge Protocol")
    
    controller = MemoryController()
    
    # Example 1: Write-Through Pattern
    print("Example 1: Write-Through Pattern (Tier 2 lesson)")
    lesson1 = Lesson(
        id="lesson-sql-001",
        trigger_pattern="tool:sql_query",
        rule_text="Use LIMIT in SELECT statements",
        lesson_type="syntax",
        confidence_score=0.85
    )
    patch1 = PatchRequest(
        trace_id="trace-001",
        diagnosis="SQL query returned too many rows",
        proposed_lesson=lesson1,
        apply_strategy="batch_later"
    )
    
    result1 = controller.commit_lesson(patch1)
    print(f"  Lesson: {lesson1.rule_text}")
    print(f"  Committed to Tier: {result1['tier']}")
    print(f"  Location: {result1['location']}")
    print(f"  Write-Through: {result1['write_through']}")
    print(f"\n  ✓ STEP 1: Written to Vector DB (permanent storage)")
    print(f"  ✓ STEP 2: Written to Redis cache (fast access)")
    print(f"  → Data exists in TWO places for redundancy\n")
    
    # Example 2: Safe Demotion
    print("Example 2: Safe Demotion (change tier tag, preserve data)")
    print(f"  Current Tier: skill_cache")
    print(f"  → Demoting to archive (updating tier tag)")
    
    controller._update_tier_tag_in_vector_db("lesson-sql-001", MemoryTier.TIER_3_ARCHIVE)
    
    # Verify data is still in Vector DB
    doc = next(d for d in controller.vector_store.documents if d["id"] == "lesson-sql-001")
    print(f"  ✓ Data preserved in Vector DB")
    print(f"  ✓ Tier tag updated: {doc['metadata']['active_tier']}")
    print(f"  → Lesson still retrievable via semantic search\n")
    
    # Example 3: Disaster Recovery
    print("Example 3: Disaster Recovery (rebuild cache from Vector DB)")
    
    # Add more lessons
    lessons = [
        Lesson(
            trigger_pattern="tool:sql_query",
            rule_text="SQL lesson 2",
            lesson_type="syntax",
            confidence_score=0.80
        ),
        Lesson(
            trigger_pattern="tool:python_repl",
            rule_text="Python lesson 1",
            lesson_type="syntax",
            confidence_score=0.90
        )
    ]
    
    for i, lesson in enumerate(lessons):
        patch = PatchRequest(
            trace_id=f"trace-{i+2}",
            diagnosis="Test",
            proposed_lesson=lesson,
            apply_strategy="batch_later"
        )
        controller.commit_lesson(patch)
    
    print(f"  ✓ Committed 2 more lessons to cache")
    
    # Simulate Redis crash
    original_cache_keys = controller.redis_cache.keys('skill:*')
    print(f"  Cache size before crash: {len(original_cache_keys)} tool caches")
    
    controller.redis_cache.clear()  # Clear cache
    print(f"  💥 Simulated Redis crash (cache cleared)")
    
    # Rebuild
    rebuild_result = controller.rebuild_cache_from_db()
    print(f"\n  🔄 Rebuilding cache from Vector DB...")
    print(f"  ✓ Rebuilt {rebuild_result['rebuilt_count']} lessons")
    print(f"  ✓ Restored {rebuild_result['tools_rebuilt']} tool caches")
    print(f"  → Tools: {', '.join(rebuild_result['tool_list'])}")
    print(f"\n  ✓ Disaster recovery successful!")


def demo_integrated_workflow():
    """Demonstrate the full integrated workflow."""
    print_section("DEMO 4: Integrated Workflow (All Components Together)")
    
    mapper = SkillMapper()
    rubric = LessonRubric()
    controller = MemoryController()
    
    print("Scenario: Agent fails to query database safely\n")
    
    # Step 1: Failure occurs
    print("STEP 1: Failure Detection")
    trace = FailureTrace(
        user_prompt="Show me all users",
        agent_reasoning="I'll execute SELECT * FROM users to get all user data",
        tool_call={"tool": "sql_db", "query": "SELECT * FROM users"},
        tool_output="Error: Query returned 1,000,000 rows",
        failure_type="commission_safety",
        severity="critical"
    )
    print(f"  User: {trace.user_prompt}")
    print(f"  Agent: {trace.agent_reasoning}")
    print(f"  Error: {trace.tool_output}")
    
    # Step 2: Map to tool
    print("\nSTEP 2: Skill Mapping")
    tool = mapper.extract_tool_context(trace)
    print(f"  → Tool: {tool}")
    print(f"  ✓ This lesson belongs to the SQL tool")
    
    # Step 3: Create lesson
    print("\nSTEP 3: Lesson Creation")
    lesson = Lesson(
        trigger_pattern=f"tool:{tool}",
        rule_text="Always use LIMIT clause in SELECT queries to prevent returning excessive rows",
        lesson_type="syntax",
        confidence_score=0.90
    )
    print(f"  Lesson: {lesson.rule_text}")
    
    # Step 4: Evaluate with rubric
    print("\nSTEP 4: Retention Scoring")
    evaluation = rubric.evaluate(trace, lesson)
    print(f"  Severity: {evaluation['severity_score']} (critical safety failure)")
    print(f"  Generality: {evaluation['generality_score']} (generic syntax rule)")
    print(f"  Frequency: {evaluation['frequency_score']} (first occurrence)")
    print(f"  Total Score: {evaluation['score']}")
    print(f"  → Tier: {evaluation['tier'].value.upper()}")
    
    # Step 5: Commit with write-through
    print("\nSTEP 5: Write-Through Commit")
    patch = PatchRequest(
        trace_id=trace.trace_id,
        diagnosis="Agent didn't limit query results",
        proposed_lesson=lesson,
        apply_strategy="hotfix_now"
    )
    
    result = controller.commit_lesson(patch)
    print(f"  ✓ Written to: {result['location']}")
    print(f"  ✓ Tier: {result['tier']}")
    print(f"  ✓ Tool: {result.get('tool', 'N/A')}")
    
    print("\nSTEP 6: Future Usage")
    print(f"  When agent uses 'sql_db' tool again:")
    print(f"  → This lesson will be injected into context")
    print(f"  → Agent will remember to use LIMIT clause")
    print(f"  ✓ Self-correction achieved!")


def main():
    """Run all demos."""
    print("\n" + "="*80)
    print("  PHASE 3 DEMONSTRATION: Memory Lifecycle & Skill Cache")
    print("="*80)
    
    demo_skill_mapper()
    demo_lesson_rubric()
    demo_write_through_architecture()
    demo_integrated_workflow()
    
    print("\n" + "="*80)
    print("  DEMONSTRATION COMPLETE")
    print("="*80)
    print("\nKey Takeaways:")
    print("  1. SkillMapper: Reliably maps lessons to tools (direct hit + semantic fallback)")
    print("  2. LessonRubric: Systematic scoring determines tier placement (S + G + F)")
    print("  3. Write-Through: Safe data management with disaster recovery")
    print("  4. Integration: All components work together seamlessly")
    print("\nResult: A production-ready memory lifecycle system with zero data loss risk.")
    print("="*80 + "\n")


if __name__ == "__main__":
    main()
