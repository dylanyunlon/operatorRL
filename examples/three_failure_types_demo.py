"""
Demonstration of the three specific failure types from the problem statement:
1. Tool Misuse - Agent called delete_user(id) with a name instead of UUID
2. Hallucination - Agent referenced Project_Alpha which doesn't exist  
3. Policy Violation - Agent tried to advise on medical issues

Each scenario shows:
- Automatic detection of the cognitive glitch
- Application of the correct patch strategy
- Tool Misuse → Schema Injection (system prompt update)
- Hallucination → RAG Patch (negative constraint)
- Policy Violation → Constitutional Update (refusal rule)
"""

import logging
from agent_kernel import SelfCorrectingAgentKernel

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s'
)

def print_scenario_header(title):
    print("\n" + "=" * 80)
    print(f" {title}")
    print("=" * 80 + "\n")

def print_results(result):
    """Print the results of failure handling."""
    print(f"\n✅ SUCCESS: {result['success']}")
    print(f"📋 Failure Type: {result['failure'].failure_type.value}")
    
    if result.get('diagnosis'):
        print(f"\n🔍 Diagnosis:")
        print(f"   Cognitive Glitch: {result['diagnosis'].cognitive_glitch.value}")
        print(f"   Deep Problem: {result['diagnosis'].deep_problem}")
        print(f"   Confidence: {result['diagnosis'].confidence:.1%}")
    
    if result.get('analysis'):
        print(f"\n🧠 Analysis:")
        print(f"   Root Cause: {result['analysis'].root_cause}")
        print(f"   Confidence: {result['analysis'].confidence_score:.1%}")
    
    if result['patch_applied'] and result.get('patch'):
        print(f"\n🔧 Patch Applied:")
        print(f"   Patch ID: {result['patch'].patch_id}")
        print(f"   Patch Type: {result['patch'].patch_type}")
        
        # Show patch content
        if result['patch'].patch_content.get('rule'):
            print(f"   Rule: {result['patch'].patch_content['rule'][:100]}...")
        elif result['patch'].patch_content.get('negative_constraint'):
            print(f"   Negative Constraint: {result['patch'].patch_content['negative_constraint']}")
    
    print("\n" + "-" * 80)


def scenario_1_tool_misuse():
    """Scenario 1: Tool Misuse - Using name instead of UUID."""
    print_scenario_header("SCENARIO 1: Tool Misuse")
    
    print("Agent attempts to delete user by name instead of UUID:")
    print("  delete_user(id='john_doe')  ❌ Wrong! Should use UUID\n")
    
    kernel = SelfCorrectingAgentKernel()
    
    result = kernel.handle_failure(
        agent_id="user-management-agent",
        error_message="Expected UUID format for parameter 'id', got string 'john_doe'",
        user_prompt="Delete the user john_doe",
        chain_of_thought=[
            "User wants to delete a user",
            "I'll call delete_user with the username",
            "Calling delete_user(id='john_doe')"
        ],
        failed_action={
            "action": "delete_user",
            "params": {"id": "john_doe"}
        },
        auto_patch=True
    )
    
    print_results(result)
    
    # Verify it's the correct fix strategy
    if result.get('diagnosis'):
        assert result['diagnosis'].cognitive_glitch.value == "tool_misuse"
        assert result['patch'].patch_type == "system_prompt"
        assert "SCHEMA INJECTION" in result['patch'].patch_content['rule']
        print("\n✓ Confirmed: Tool Misuse → Schema Injection (system_prompt)")


def scenario_2_hallucination():
    """Scenario 2: Hallucination - Referencing non-existent project."""
    print_scenario_header("SCENARIO 2: Hallucination")
    
    print("Agent references Project_Alpha which doesn't exist:")
    print("  get_project('Project_Alpha')  ❌ Project doesn't exist\n")
    
    kernel = SelfCorrectingAgentKernel()
    
    result = kernel.handle_failure(
        agent_id="project-management-agent",
        error_message="Project 'Project_Alpha' does not exist",
        user_prompt="Show me the status of Project_Alpha",
        chain_of_thought=[
            "User wants project status",
            "I'll query Project_Alpha",
            "Fetching data for Project_Alpha"
        ],
        failed_action={
            "action": "get_project_status",
            "params": {"project_name": "Project_Alpha"}
        },
        auto_patch=True
    )
    
    print_results(result)
    
    # Verify it's the correct fix strategy
    if result.get('diagnosis'):
        assert result['diagnosis'].cognitive_glitch.value == "hallucination"
        assert result['patch'].patch_type == "rag_memory"
        assert result['patch'].patch_content.get('negative_constraint') is not None
        print("\n✓ Confirmed: Hallucination → RAG Patch with negative constraint")
        print(f"   Negative constraint added: '{result['patch'].patch_content['negative_constraint']}'")


def scenario_3_policy_violation():
    """Scenario 3: Policy Violation - Attempting to give medical advice."""
    print_scenario_header("SCENARIO 3: Policy Violation")
    
    print("Agent tries to give medical advice:")
    print("  provide_medical_advice('Take aspirin...')  ❌ Policy violation\n")
    
    kernel = SelfCorrectingAgentKernel()
    
    result = kernel.handle_failure(
        agent_id="assistant-agent",
        error_message="Policy violation: Cannot advise on medical issues",
        user_prompt="What medication should I take for my headache?",
        chain_of_thought=[
            "User has a headache",
            "I should recommend some medication",
            "Common headache medications include aspirin"
        ],
        failed_action={
            "action": "provide_advice",
            "domain": "medical",
            "response": "You should take aspirin..."
        },
        auto_patch=True
    )
    
    print_results(result)
    
    # Verify it's the correct fix strategy
    if result.get('diagnosis'):
        assert result['diagnosis'].cognitive_glitch.value == "policy_violation"
        assert result['patch'].patch_type == "system_prompt"
        assert "CONSTITUTIONAL REFUSAL RULE" in result['patch'].patch_content['rule']
        print("\n✓ Confirmed: Policy Violation → Constitutional Update (system_prompt)")


def main():
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 15 + "SELF-CORRECTING AGENT KERNEL DEMO" + " " * 30 + "║")
    print("║" + " " * 20 + "Three Failure Type Examples" + " " * 31 + "║")
    print("╚" + "=" * 78 + "╝")
    
    # Run all three scenarios
    scenario_1_tool_misuse()
    scenario_2_hallucination()
    scenario_3_policy_violation()
    
    print("\n" + "=" * 80)
    print("✅ All scenarios completed successfully!")
    print("=" * 80)
    
    print("\nSummary of Automated Fix Strategies:")
    print("  1. Tool Misuse       → Schema Injection (stricter tool definitions)")
    print("  2. Hallucination     → RAG Patch (negative constraints)")
    print("  3. Policy Violation  → Constitutional Update (refusal rules)")
    print()


if __name__ == "__main__":
    main()
