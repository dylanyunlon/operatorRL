"""
Enhanced Features Demo

Demonstrates the new enhancements to the Self-Correcting Agent Kernel:
1. Tool Execution Telemetry - False positive prevention
2. Semantic Analysis - Beyond regex detection
3. Nudge Mechanism - Automatic retry logic
4. Value Delivery Metrics - Competence focus
"""

from agent_kernel import (
    SelfCorrectingAgentKernel,
    ToolExecutionTelemetry,
    ToolExecutionStatus
)


def print_section(title: str):
    """Print a section header."""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80 + "\n")


def demo_false_positive_prevention():
    """Demonstrate false positive prevention with tool telemetry."""
    print_section("DEMO 1: False Positive Prevention (Valid Empty Sets)")
    
    kernel = SelfCorrectingAgentKernel(config={
        "use_semantic_analysis": True,
        "teacher_model": "o1-preview"
    })
    
    print("Scenario: User asks for logs from 1990 (legitimately don't exist)")
    print()
    
    # Simulate tool execution that legitimately returns empty
    telemetry = [
        ToolExecutionTelemetry(
            tool_name="search_logs",
            tool_status=ToolExecutionStatus.EMPTY_RESULT,
            tool_result=[],
            execution_time_ms=150.5
        ),
        ToolExecutionTelemetry(
            tool_name="search_archive",
            tool_status=ToolExecutionStatus.EMPTY_RESULT,
            tool_result=[],
            execution_time_ms=89.2
        )
    ]
    
    result = kernel.handle_outcome(
        agent_id="log-agent",
        user_prompt="Find logs from 1990",
        agent_response="No data found for logs from 1990.",
        tool_telemetry=telemetry,
        auto_nudge=False
    )
    
    print(f"✓ Agent Response: {result['outcome'].agent_response}")
    print(f"✓ Give-Up Signal Detected: {result['outcome'].give_up_signal.value if result['outcome'].give_up_signal else 'None'}")
    print(f"✓ Tools Called: {len(result['outcome'].tool_telemetry)}")
    print(f"✓ Tool Status: All returned EMPTY_RESULT")
    print(f"✓ Outcome Type: {result['outcome'].outcome_type.value}")
    print()
    print("🎯 RESULT: Despite 'no data found' signal, this is SUCCESS (not GIVE_UP)")
    print("   Reason: Tools were called and legitimately returned empty results")
    print("   This is a VALID empty set, not laziness!")
    print()
    
    # Now show laziness case - no tools called
    print("Contrast: Agent says 'no data' without calling tools (LAZINESS)")
    print()
    
    result2 = kernel.handle_outcome(
        agent_id="lazy-agent",
        user_prompt="Find logs from last week",
        agent_response="No logs found.",
        tool_telemetry=[],  # No tools called!
        auto_nudge=False
    )
    
    print(f"✗ Agent Response: {result2['outcome'].agent_response}")
    print(f"✗ Tools Called: {len(result2['outcome'].tool_telemetry)}")
    print(f"✗ Outcome Type: {result2['outcome'].outcome_type.value}")
    print()
    print("⚠️  RESULT: This is GIVE_UP (flagged for audit)")
    print("   Reason: No tools were called - clear laziness!")


def demo_semantic_analysis():
    """Demonstrate semantic analysis beyond regex."""
    print_section("DEMO 2: Semantic Analysis (Beyond Regex)")
    
    kernel = SelfCorrectingAgentKernel(config={
        "use_semantic_analysis": True
    })
    
    print("Scenario: Agent uses subtle refusal language not in regex patterns")
    print()
    
    subtle_refusals = [
        "I'm afraid those records are elusive at the moment.",
        "The information seems to be unavailable right now.",
        "It appears there's nothing to show for this query."
    ]
    
    for i, response in enumerate(subtle_refusals, 1):
        print(f"Example {i}: {response}")
        
        result = kernel.handle_outcome(
            agent_id="subtle-agent",
            user_prompt="Find user records",
            agent_response=response,
            auto_nudge=False
        )
        
        if result['outcome'].semantic_analysis:
            sa = result['outcome'].semantic_analysis
            print(f"  ✓ Semantic Analysis: {sa.semantic_category}")
            print(f"  ✓ Is Refusal: {sa.is_refusal}")
            print(f"  ✓ Confidence: {sa.refusal_confidence:.2f}")
            print(f"  ✓ Reasoning: {sa.reasoning[:100]}...")
        print()
    
    print("🎯 RESULT: Semantic analysis catches subtle refusals that regex misses")
    print("   These phrases don't match regex patterns but indicate giving up")


def demo_nudge_mechanism():
    """Demonstrate automatic nudge/retry logic."""
    print_section("DEMO 3: Nudge Mechanism (Automatic Retry Logic)")
    
    kernel = SelfCorrectingAgentKernel(config={
        "use_semantic_analysis": True
    })
    
    print("Scenario: Agent gives up, system automatically generates nudge")
    print()
    
    result = kernel.handle_outcome(
        agent_id="nudge-agent",
        user_prompt="Find error logs for error 500 from last week",
        agent_response="No logs found for error 500.",
        auto_nudge=True  # Enable auto-nudge
    )
    
    print(f"Agent's Initial Response: {result['outcome'].agent_response}")
    print(f"Outcome Type: {result['outcome'].outcome_type.value}")
    print()
    
    if "nudge_prompt" in result:
        print("🔔 AUTOMATIC NUDGE GENERATED:")
        print("-" * 80)
        print(result['nudge_prompt'])
        print("-" * 80)
        print()
        print("✓ The nudge asks the agent to:")
        print("  1. Confirm tools were executed with correct parameters")
        print("  2. Check all relevant data sources including archives")
        print("  3. Use appropriate time ranges and filters")
        print()
        print("🎯 RESULT: Agent would be re-invoked with this nudge")
        print("   Industry standard: Often fixes laziness without human intervention")
    
    # Show nudge stats
    stats = kernel.get_alignment_stats()
    print()
    print("Nudge Statistics:")
    print(f"  Total Nudges: {stats['nudge_mechanism']['total_nudges']}")
    print(f"  Success Rate: {stats['nudge_mechanism']['success_rate']:.2%}")


def demo_value_delivery_metrics():
    """Demonstrate competence/value delivery metrics."""
    print_section("DEMO 4: Value Delivery Metrics (Competence Focus)")
    
    kernel = SelfCorrectingAgentKernel(config={
        "use_semantic_analysis": True,
        "teacher_model": "o1-preview"
    })
    
    print("Scenario: Track competence and value delivery over time")
    print()
    
    # Simulate various outcomes
    outcomes = [
        ("Find logs for error 500", "Found 247 log entries in /var/log/app.log", True),
        ("Get user data", "Retrieved 1,523 user records successfully", True),
        ("Find archived data", "No data found.", False),
        ("Search database", "Query returned 0 results.", False),
        ("Get metrics", "Collected metrics from 5 sources successfully", True),
    ]
    
    for prompt, response, has_data in outcomes:
        telemetry = None
        if has_data:
            telemetry = [
                ToolExecutionTelemetry(
                    tool_name="search_tool",
                    tool_status=ToolExecutionStatus.SUCCESS,
                    tool_result={"count": 100}
                )
            ]
        
        kernel.handle_outcome(
            agent_id="production-agent",
            user_prompt=prompt,
            agent_response=response,
            tool_telemetry=telemetry,
            auto_nudge=False
        )
    
    # Get value delivery metrics
    stats = kernel.get_alignment_stats()
    value_delivery = stats["value_delivery"]
    
    print("VALUE DELIVERY METRICS:")
    print("-" * 80)
    print(f"  Competence Score: {value_delivery['competence_score']:.2f}/100")
    print(f"  Give-Up Rate: {value_delivery['give_up_rate']:.2%}")
    print(f"  Laziness Detection Rate: {value_delivery['laziness_detection_rate']:.2%}")
    print(f"  Total Audits: {value_delivery['total_audits']}")
    print(f"  Laziness Caught: {value_delivery['laziness_caught']}")
    print()
    print(f"  Focus: {value_delivery['focus']}")
    print("-" * 80)
    print()
    print("🎯 DIFFERENTIATION FROM STANDARD GOVERNANCE TOOLS:")
    print("   • Most control planes focus on SAFETY (Loop 1):")
    print("     - Did it violate policy?")
    print("     - Was the action blocked?")
    print()
    print("   • This system focuses on COMPETENCE (Loop 2):")
    print("     - Is the agent delivering value?")
    print("     - Is it giving up too easily?")
    print("     - What's the 'Give-Up Rate'?")
    print()
    print("   Microsoft/Forrester Context:")
    print("   Recent reports focus on Cost & Identity (billing, consumption)")
    print("   but less on Competence/Quality. This system fills that gap!")


def demo_all_together():
    """Demonstrate all features working together."""
    print_section("DEMO 5: All Features Working Together")
    
    kernel = SelfCorrectingAgentKernel(config={
        "use_semantic_analysis": True,
        "teacher_model": "o1-preview",
        "auto_patch": True
    })
    
    print("Complex Scenario: Multi-layered detection and intervention")
    print()
    
    # Case 1: Regex miss, semantic catch
    print("Case 1: Subtle refusal (regex miss, semantic catch)")
    result1 = kernel.handle_outcome(
        agent_id="agent-1",
        user_prompt="Find customer records",
        agent_response="Those records appear to be elusive.",
        auto_nudge=True
    )
    print(f"  Regex Signal: {result1['outcome'].give_up_signal}")
    print(f"  Semantic Detection: {result1['outcome'].semantic_analysis.is_refusal if result1['outcome'].semantic_analysis else 'N/A'}")
    print(f"  Outcome: {result1['outcome'].outcome_type.value}")
    print(f"  Nudge Generated: {'Yes' if 'nudge_prompt' in result1 else 'No'}")
    print()
    
    # Case 2: Valid empty with telemetry
    print("Case 2: Valid empty result (tools called)")
    telemetry = [
        ToolExecutionTelemetry(
            tool_name="db_query",
            tool_status=ToolExecutionStatus.EMPTY_RESULT,
            tool_result=[]
        )
    ]
    result2 = kernel.handle_outcome(
        agent_id="agent-2",
        user_prompt="Find records from 1800",
        agent_response="No records found from 1800.",
        tool_telemetry=telemetry,
        auto_nudge=True
    )
    print(f"  Give-Up Signal Present: Yes")
    print(f"  Tools Called: {len(telemetry)}")
    print(f"  Tool Status: EMPTY_RESULT")
    print(f"  Outcome: {result2['outcome'].outcome_type.value}")
    print(f"  Audit Triggered: {'Yes' if result2['audit'] else 'No'}")
    print()
    
    # Case 3: Clear laziness
    print("Case 3: Clear laziness (no tools, give-up signal)")
    result3 = kernel.handle_outcome(
        agent_id="agent-3",
        user_prompt="Find recent logs",
        agent_response="Cannot find logs.",
        tool_telemetry=[],
        auto_nudge=True
    )
    print(f"  Tools Called: 0")
    print(f"  Outcome: {result3['outcome'].outcome_type.value}")
    print(f"  Audit Triggered: {'Yes' if result3['audit'] else 'No'}")
    print(f"  Nudge Generated: {'Yes' if 'nudge_prompt' in result3 else 'No'}")
    print()
    
    # Show comprehensive stats
    stats = kernel.get_alignment_stats()
    print("COMPREHENSIVE STATS:")
    print(f"  Total Outcomes: {stats['outcome_analyzer']['total_outcomes']}")
    print(f"  Give-Up Rate: {stats['outcome_analyzer']['give_up_rate']:.2%}")
    print(f"  Audits Performed: {stats['completeness_auditor']['total_audits']}")
    print(f"  Nudges Generated: {stats['nudge_mechanism']['total_nudges']}")
    print(f"  Competence Score: {stats['value_delivery']['competence_score']:.2f}/100")


def main():
    """Run all demos."""
    print("\n" + "=" * 80)
    print("  SELF-CORRECTING AGENT KERNEL - ENHANCED FEATURES DEMO")
    print("=" * 80)
    
    demo_false_positive_prevention()
    input("\nPress Enter to continue to next demo...")
    
    demo_semantic_analysis()
    input("\nPress Enter to continue to next demo...")
    
    demo_nudge_mechanism()
    input("\nPress Enter to continue to next demo...")
    
    demo_value_delivery_metrics()
    input("\nPress Enter to continue to final demo...")
    
    demo_all_together()
    
    print("\n" + "=" * 80)
    print("  DEMO COMPLETE")
    print("=" * 80)
    print()
    print("Summary of Enhancements:")
    print("1. ✓ Tool Execution Telemetry - Prevents false positives")
    print("2. ✓ Semantic Analysis - Catches subtle refusals")
    print("3. ✓ Nudge Mechanism - Automatic retry logic")
    print("4. ✓ Value Delivery Metrics - Focus on competence")
    print()
    print("These enhancements address the blind spots in regex-based approaches")
    print("and differentiate the system by focusing on competence/value delivery")
    print("rather than just safety/compliance.")
    print()


if __name__ == "__main__":
    main()
