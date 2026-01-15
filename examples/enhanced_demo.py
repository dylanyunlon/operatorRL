"""
Enhanced demonstration of the Self-Correcting Agent Kernel.

This example showcases the new features:
1. Failure Queue with full trace capture
2. Cognitive Glitch Detection (Hallucination, Logic Error, Context Gap)
3. Shadow Agent counterfactual simulation (MCTS-based)
4. Smart patching with System Prompt updates and RAG memory injection
"""

import logging
from agent_kernel import SelfCorrectingAgentKernel

# Setup logging to see the kernel in action
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def example_sql_hallucination():
    """
    Example: Agent hallucinates a table name that doesn't exist.
    
    This demonstrates:
    - Full trace capture with chain of thought
    - Cognitive glitch detection (HALLUCINATION)
    - Shadow Agent verification
    - RAG memory injection (hard fix)
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 1: SQL Query Hallucination")
    print("=" * 80 + "\n")
    
    kernel = SelfCorrectingAgentKernel()
    
    # Simulate an agent failure with full trace
    agent_id = "sql-agent-001"
    error_message = "Action blocked by control plane: Dangerous SQL query - table 'recent_users' does not exist"
    
    # Full trace: User Prompt + Chain of Thought + Failed Action
    user_prompt = "Delete recent user records"
    chain_of_thought = [
        "User wants to delete records",
        "I need to identify which records are 'recent'",
        "I'll query the recent_users table",  # HALLUCINATION: table doesn't exist
        "I'll construct a DELETE query"
    ]
    failed_action = {
        "action": "execute_sql",
        "query": "DELETE FROM recent_users WHERE created_at > '2024-01-01'"
    }
    context = {
        "action": "execute_sql",
        "resource": "database"
    }
    
    # The kernel wakes up with full context
    result = kernel.handle_failure(
        agent_id=agent_id,
        error_message=error_message,
        context=context,
        user_prompt=user_prompt,
        chain_of_thought=chain_of_thought,
        failed_action=failed_action
    )
    
    # Display results
    print("\n" + "-" * 80)
    print("RESULTS:")
    print("-" * 80)
    print(f"Success: {result['success']}")
    print(f"Patch Applied: {result['patch_applied']}")
    
    if result['diagnosis']:
        print(f"\nCOGNITIVE DIAGNOSIS:")
        print(f"  Glitch Type: {result['diagnosis'].cognitive_glitch}")
        print(f"  Deep Problem: {result['diagnosis'].deep_problem}")
        print(f"  Confidence: {result['diagnosis'].confidence:.2%}")
        print(f"  Evidence: {len(result['diagnosis'].evidence)} pieces")
        print(f"  Hint Generated: {result['diagnosis'].hint[:80]}...")
    
    if result['shadow_result']:
        print(f"\nSHADOW AGENT SIMULATION:")
        print(f"  Execution Success: {result['shadow_result'].execution_success}")
        print(f"  Verified Fix: {result['shadow_result'].verified}")
        print(f"  Output: {result['shadow_result'].output}")
    
    print(f"\nPATCH DETAILS:")
    print(f"  Patch ID: {result['patch'].patch_id}")
    print(f"  Patch Type: {result['patch'].patch_type}")
    print(f"  Strategy: {'RAG Memory (Hard Fix)' if result['patch'].patch_type == 'rag_memory' else 'System Prompt (Easy Fix)'}")
    
    # Show what was patched
    if result['patch'].patch_type == 'rag_memory':
        print(f"\n  RAG Memory Injected:")
        print(f"    Context: {result['patch'].patch_content.get('failure_context', '')[:100]}...")
        print(f"    Verified by Shadow: {result['patch'].patch_content.get('verified_by_shadow', False)}")
    
    print("-" * 80 + "\n")


def example_context_gap():
    """
    Example: Agent lacks necessary schema information.
    
    This demonstrates:
    - Context gap detection
    - System prompt update (easy fix)
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 2: Context Gap - Missing Schema Info")
    print("=" * 80 + "\n")
    
    kernel = SelfCorrectingAgentKernel()
    
    agent_id = "api-agent-002"
    error_message = "Action blocked: Missing required permissions"
    
    user_prompt = "Update the user's email address"
    chain_of_thought = [
        "User wants to update email",
        # Very short chain - indicating context gap
    ]
    failed_action = {
        "action": "update_user",
        "params": {"email": "new@email.com"}
    }
    
    result = kernel.handle_failure(
        agent_id=agent_id,
        error_message=error_message,
        user_prompt=user_prompt,
        chain_of_thought=chain_of_thought,
        failed_action=failed_action
    )
    
    print("\n" + "-" * 80)
    print("RESULTS:")
    print("-" * 80)
    
    if result['diagnosis']:
        print(f"Cognitive Glitch: {result['diagnosis'].cognitive_glitch}")
        print(f"Deep Problem: {result['diagnosis'].deep_problem}")
    
    print(f"\nPatch Type: {result['patch'].patch_type}")
    if result['patch'].patch_type == 'system_prompt':
        print(f"System Prompt Rule Added:")
        print(f"  {result['patch'].patch_content.get('rule', 'N/A')}")
    
    print("-" * 80 + "\n")


def example_logic_error():
    """
    Example: Agent misunderstands "delete recent" instruction.
    
    This demonstrates:
    - Logic error detection
    - MCTS-based hint optimization
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 3: Logic Error - Misunderstanding 'Recent'")
    print("=" * 80 + "\n")
    
    kernel = SelfCorrectingAgentKernel()
    
    agent_id = "cleanup-agent-003"
    error_message = "Action blocked: Attempting to delete too many records"
    
    user_prompt = "Delete recent failed login attempts"
    chain_of_thought = [
        "User wants to delete failed logins",
        "I assume 'recent' means all records",  # LOGIC ERROR
        "I'll delete all failed_login records"
    ]
    failed_action = {
        "action": "delete_records",
        "table": "failed_logins",
        "condition": "1=1"  # Delete all!
    }
    
    result = kernel.handle_failure(
        agent_id=agent_id,
        error_message=error_message,
        user_prompt=user_prompt,
        chain_of_thought=chain_of_thought,
        failed_action=failed_action
    )
    
    print("\n" + "-" * 80)
    print("RESULTS:")
    print("-" * 80)
    
    if result['diagnosis']:
        print(f"Cognitive Glitch: {result['diagnosis'].cognitive_glitch}")
        print(f"Hint: {result['diagnosis'].hint}")
        print(f"Expected Fix: {result['diagnosis'].expected_fix}")
    
    if result['shadow_result']:
        print(f"\nShadow Agent:")
        print(f"  Reasoning Steps: {len(result['shadow_result'].reasoning_chain)}")
        for i, step in enumerate(result['shadow_result'].reasoning_chain, 1):
            print(f"    {i}. {step}")
    
    print("-" * 80 + "\n")


def example_failure_queue():
    """
    Example: Demonstrate the Failure Queue with multiple failures.
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 4: Failure Queue")
    print("=" * 80 + "\n")
    
    kernel = SelfCorrectingAgentKernel()
    
    # Generate multiple failures
    for i in range(3):
        kernel.handle_failure(
            agent_id=f"agent-{i}",
            error_message=f"Error {i}",
            user_prompt=f"User request {i}",
            chain_of_thought=[f"Thought {i}"],
            failed_action={"action": f"action_{i}"}
        )
    
    # Check queue size
    queue_size = kernel.detector.failure_queue.size()
    print(f"Failure Queue Size: {queue_size}")
    print(f"Failures with full trace captured: {queue_size}")
    
    # Peek at oldest failure
    oldest = kernel.detector.failure_queue.peek()
    if oldest:
        print(f"\nOldest Failure:")
        print(f"  Agent: {oldest.agent_id}")
        print(f"  Has Trace: {oldest.failure_trace is not None}")
        if oldest.failure_trace:
            print(f"  User Prompt: {oldest.failure_trace.user_prompt}")
    
    print("-" * 80 + "\n")


if __name__ == "__main__":
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 15 + "ENHANCED SELF-CORRECTING AGENT KERNEL" + " " * 25 + "║")
    print("║" + " " * 22 + "Advanced Cognitive Diagnosis" + " " * 28 + "║")
    print("╚" + "=" * 78 + "╝")
    
    # Run examples
    example_sql_hallucination()
    example_context_gap()
    example_logic_error()
    example_failure_queue()
    
    print("\n" + "=" * 80)
    print("All enhanced examples completed successfully!")
    print("=" * 80 + "\n")
    print("\nKEY FEATURES DEMONSTRATED:")
    print("✓ Failure Queue with full trace capture (User Prompt + Chain of Thought + Action)")
    print("✓ Cognitive Glitch Detection (Hallucination, Logic Error, Context Gap)")
    print("✓ DiagnosisJSON structured output")
    print("✓ Shadow Agent counterfactual simulation (MCTS-based)")
    print("✓ Smart Patching:")
    print("  - Easy Fix: System Prompt updates")
    print("  - Hard Fix: RAG Memory injection")
    print()
