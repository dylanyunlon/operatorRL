"""
Example demonstrating the Failure Triage Engine.

This shows how the kernel routes failures to sync (JIT) or async (batch)
correction strategies based on criticality analysis.
"""

import logging
from agent_kernel import SelfCorrectingAgentKernel, FixStrategy

# Setup logging to see the triage decisions
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


def example_critical_operation_sync():
    """
    Example: Critical write operation triggers SYNC_JIT (user waits).
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 1: Critical Operation (SYNC_JIT)")
    print("=" * 80 + "\n")
    
    kernel = SelfCorrectingAgentKernel()
    
    # Simulate a failure with a critical operation
    result = kernel.handle_failure(
        agent_id="payment-agent-001",
        error_message="Action blocked: Invalid payment gateway configuration",
        user_prompt="Process refund for order #12345",
        context={
            "action": "execute_payment",
            "operation": "refund",
            "amount": 99.99
        }
    )
    
    print("\n" + "-" * 80)
    print("RESULT:")
    print("-" * 80)
    if result.get("strategy"):
        print(f"Triage Decision: {result['strategy'].value}")
    if result.get("queued"):
        print("Status: Queued for async processing")
    else:
        print(f"Status: Fixed synchronously")
        print(f"Success: {result.get('success')}")
        print(f"Patch Applied: {result.get('patch_applied')}")
    print()


def example_high_effort_prompt_sync():
    """
    Example: High-effort prompt triggers SYNC_JIT (deep thinking required).
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 2: High-Effort Prompt (SYNC_JIT)")
    print("=" * 80 + "\n")
    
    kernel = SelfCorrectingAgentKernel()
    
    result = kernel.handle_failure(
        agent_id="analysis-agent-002",
        error_message="Query timeout after 30 seconds",
        user_prompt="This is critical: carefully analyze all security logs from the past week",
        context={
            "action": "query_logs",
            "time_range": "7d"
        }
    )
    
    print("\n" + "-" * 80)
    print("RESULT:")
    print("-" * 80)
    if result.get("strategy"):
        print(f"Triage Decision: {result['strategy'].value}")
    if result.get("queued"):
        print("Status: Queued for async processing")
    else:
        print(f"Status: Fixed synchronously")
        print(f"Success: {result.get('success')}")
    print()


def example_vip_user_sync():
    """
    Example: VIP user triggers SYNC_JIT (priority treatment).
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 3: VIP User Request (SYNC_JIT)")
    print("=" * 80 + "\n")
    
    kernel = SelfCorrectingAgentKernel()
    
    result = kernel.handle_failure(
        agent_id="support-agent-003",
        error_message="Database connection failed",
        user_prompt="Show me my recent orders",
        user_metadata={"is_vip": True, "tier": "platinum"},
        context={
            "action": "query_db",
            "table": "orders"
        }
    )
    
    print("\n" + "-" * 80)
    print("RESULT:")
    print("-" * 80)
    if result.get("strategy"):
        print(f"Triage Decision: {result['strategy'].value}")
    if result.get("queued"):
        print("Status: Queued for async processing")
    else:
        print(f"Status: Fixed synchronously (VIP priority)")
        print(f"Success: {result.get('success')}")
    print()


def example_read_operation_async():
    """
    Example: Simple read operation triggers ASYNC_BATCH (fast response).
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 4: Read Operation (ASYNC_BATCH)")
    print("=" * 80 + "\n")
    
    kernel = SelfCorrectingAgentKernel()
    
    result = kernel.handle_failure(
        agent_id="query-agent-004",
        error_message="No results found in cache",
        user_prompt="Get the latest blog posts",
        context={
            "action": "fetch_data",
            "resource": "blog_posts"
        }
    )
    
    print("\n" + "-" * 80)
    print("RESULT:")
    print("-" * 80)
    if result.get("strategy"):
        print(f"Triage Decision: {result['strategy'].value}")
    if result.get("queued"):
        print("Status: Queued for async processing (user gets fast response)")
        print(f"Error returned to user: {result.get('error')}")
        print("Fix will happen in background/nightly batch")
    print()
    
    # Show async queue stats
    stats = kernel.get_triage_stats()
    print(f"\nAsync Queue Size: {stats['async_queue_size']}")


def example_async_queue_processing():
    """
    Example: Processing the async queue in background.
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 5: Async Queue Processing (Background/Nightly)")
    print("=" * 80 + "\n")
    
    kernel = SelfCorrectingAgentKernel()
    
    # Queue up several non-critical failures
    print("Queuing non-critical failures...")
    for i in range(5):
        kernel.handle_failure(
            agent_id=f"read-agent-{i}",
            error_message=f"Cache miss for resource {i}",
            user_prompt=f"Fetch data item {i}",
            context={"action": "read_data", "item": i}
        )
    
    print(f"\nQueued {kernel.get_triage_stats()['async_queue_size']} failures")
    
    # Process the async queue (simulating background/nightly processing)
    print("\nProcessing async queue in background...")
    result = kernel.process_async_queue(batch_size=3)
    
    print("\n" + "-" * 80)
    print("ASYNC PROCESSING RESULT:")
    print("-" * 80)
    print(f"Processed: {result['processed']}")
    print(f"Succeeded: {result['succeeded']}")
    print(f"Failed: {result['failed']}")
    print(f"Remaining in queue: {result['remaining']}")
    print()


def example_triage_decision_directly():
    """
    Example: Using triage engine directly for decision-making.
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 6: Direct Triage Decisions")
    print("=" * 80 + "\n")
    
    kernel = SelfCorrectingAgentKernel()
    triage = kernel.triage
    
    # Test various scenarios
    scenarios = [
        {
            "name": "Critical Delete",
            "prompt": "Remove user account",
            "tool": "delete_resource"
        },
        {
            "name": "High Effort Analysis",
            "prompt": "Carefully review all transactions",
            "tool": "query_db"
        },
        {
            "name": "Simple Read",
            "prompt": "Show me the dashboard",
            "tool": "fetch_dashboard"
        },
        {
            "name": "VIP Request",
            "prompt": "Get my profile",
            "tool": "read_profile",
            "metadata": {"is_vip": True}
        }
    ]
    
    print("Triage Decisions for Various Scenarios:\n")
    for scenario in scenarios:
        strategy = triage.decide_strategy(
            prompt=scenario["prompt"],
            tool_name=scenario["tool"],
            user_metadata=scenario.get("metadata")
        )
        
        icon = "⚡" if strategy == FixStrategy.SYNC_JIT else "⏰"
        print(f"{icon} {scenario['name']}:")
        print(f"   Prompt: '{scenario['prompt']}'")
        print(f"   Tool: {scenario['tool']}")
        print(f"   Decision: {strategy.value}")
        print(f"   Strategy: {'SYNC (user waits)' if strategy == FixStrategy.SYNC_JIT else 'ASYNC (fast response)'}")
        print()


if __name__ == "__main__":
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 22 + "FAILURE TRIAGE ENGINE" + " " * 35 + "║")
    print("║" + " " * 20 + "Sync (JIT) vs Async (Batch)" + " " * 31 + "║")
    print("╚" + "=" * 78 + "╝")
    
    # Run examples
    example_triage_decision_directly()
    example_critical_operation_sync()
    example_high_effort_prompt_sync()
    example_vip_user_sync()
    example_read_operation_async()
    example_async_queue_processing()
    
    print("\n" + "=" * 80)
    print("All triage examples completed!")
    print("=" * 80)
    print("\nKey Takeaways:")
    print("  • Critical operations (write/delete) → SYNC_JIT (user waits, high reliability)")
    print("  • High-effort prompts (careful/critical) → SYNC_JIT (deep thinking)")
    print("  • VIP users → SYNC_JIT (priority treatment)")
    print("  • Read/query operations → ASYNC_BATCH (fast response, fix later)")
    print("  • Async queue processed in background/nightly batches")
    print("=" * 80 + "\n")
