# experiments/paper_data_generator.py
"""
Paper Data Generator - Generates experimental data for research paper.

This script compares:
1. Baseline: Single agent (OpenAI) without verification
2. CMVK: Cross-Model Verification Kernel (OpenAI + Gemini + Trace)

The generated traces can be used for charts and supplementary material.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.simple_kernel import SimpleVerificationKernel
from src.agents.generator_openai import OpenAIGenerator


# 1. The Dataset (Subtle bugs that standard models miss)
problems = [
    {
        "id": "prob_001",
        "query": "Write a Python function to merge two sorted arrays into one sorted array WITHOUT using 'sorted()' or 'sort()'. Optimize for O(n)."
    },
    {
        "id": "prob_002", 
        "query": "Write a regex to validate an email address that DOES NOT allow uppercase letters."
    }
]


def run_baseline_agent(problem):
    """
    Control Group: Just OpenAI, no Verification Loop.
    
    This represents the traditional approach where a single LLM
    generates code without adversarial verification.
    
    Args:
        problem: Problem dictionary with 'id' and 'query' keys
    """
    print(f"\n{'='*80}")
    print(f"--- Running Baseline for {problem['id']} ---")
    print(f"{'='*80}")
    
    agent = OpenAIGenerator()
    code = agent.generate_solution(problem['query'])
    
    # In a real paper, we'd run automated testing on this code.
    # For now, we just save it.
    os.makedirs("logs", exist_ok=True)
    with open(f"logs/baseline_{problem['id']}.py", "w") as f:
        f.write(code)
    
    print(f"💾 Baseline solution saved to: logs/baseline_{problem['id']}.py")


def run_our_kernel(problem):
    """
    Experimental Group: CMVK (OpenAI + Gemini + Trace).
    
    This represents our approach with adversarial verification
    and full traceability.
    
    Args:
        problem: Problem dictionary with 'id' and 'query' keys
    """
    print(f"\n{'='*80}")
    print(f"--- Running CMVK for {problem['id']} ---")
    print(f"{'='*80}")
    
    kernel = SimpleVerificationKernel()
    solution = kernel.solve(problem['query'], run_id=f"cmvk_{problem['id']}")
    
    if solution:
        print(f"✅ CMVK found verified solution for {problem['id']}")
    else:
        print(f"❌ CMVK failed to find solution for {problem['id']}")


if __name__ == "__main__":
    print("="*80)
    print("PAPER DATA GENERATOR")
    print("Comparing Baseline (Single Agent) vs CMVK (Adversarial Verification)")
    print("="*80)
    
    for p in problems:
        # 1. Run Baseline
        run_baseline_agent(p)
        
        # 2. Run Our Kernel
        run_our_kernel(p)
    
    print("\n" + "="*80)
    print("EXPERIMENT COMPLETE")
    print("="*80)
    print("\n📊 Results saved to:")
    print("   - Baseline solutions: logs/baseline_*.py")
    print("   - CMVK traces: logs/traces/cmvk_*.json")
    print("\n💡 Next Steps:")
    print("   1. Visualize the Traces: Replay the debate")
    print("   2. Scale the Experiment: Use HumanEval dataset")
    print("   3. Draft the Paper: Write the Methodology section")
