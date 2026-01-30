"""
My First Governed Agent

This agent is protected by Agent OS with kernel-level safety guarantees.
"""

from agent_os import KernelSpace

# Create kernel with strict policy
kernel = KernelSpace(policy="strict")

@kernel.register
async def my_first_agent(task: str):
    """A simple agent that processes tasks safely."""
    # Your agent code here
    # All operations are checked against policies
    result = f"Processed: {task}"
    return result

if __name__ == "__main__":
    import asyncio
    result = asyncio.run(kernel.execute(my_first_agent, "Hello Agent OS!"))
    print(result)
