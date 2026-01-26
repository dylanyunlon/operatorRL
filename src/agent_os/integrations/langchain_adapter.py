"""
LangChain Integration

Wraps LangChain agents/chains with Agent OS governance.

Usage:
    from agent_os.integrations import LangChainKernel
    
    kernel = LangChainKernel()
    governed_chain = kernel.wrap(my_langchain_chain)
    
    # Now all invocations go through Agent OS
    result = governed_chain.invoke({"input": "..."})
"""

from typing import Any, Optional
from functools import wraps

from .base import BaseIntegration, GovernancePolicy, ExecutionContext


class LangChainKernel(BaseIntegration):
    """
    LangChain adapter for Agent OS.
    
    Supports:
    - Chains (invoke, ainvoke)
    - Agents (run, arun)
    - Runnables (invoke, batch, stream)
    """
    
    def __init__(self, policy: Optional[GovernancePolicy] = None):
        super().__init__(policy)
        self._wrapped_agents: dict[int, Any] = {}  # id(wrapped) -> original
    
    def wrap(self, agent: Any) -> Any:
        """
        Wrap a LangChain agent/chain with governance.
        
        Intercepts:
        - invoke() / ainvoke()
        - run() / arun()
        - batch() / abatch()
        - stream() / astream()
        """
        # Get agent ID from the object
        agent_id = getattr(agent, 'name', None) or f"langchain-{id(agent)}"
        ctx = self.create_context(agent_id)
        
        # Store original
        self._wrapped_agents[id(agent)] = agent
        
        # Create wrapper class
        original = agent
        kernel = self
        
        class GovernedLangChainAgent:
            """LangChain agent wrapped with Agent OS governance"""
            
            def __init__(self):
                self._original = original
                self._ctx = ctx
                self._kernel = kernel
            
            def invoke(self, input_data: Any, **kwargs) -> Any:
                """Governed invoke"""
                # Pre-check
                allowed, reason = self._kernel.pre_execute(self._ctx, input_data)
                if not allowed:
                    raise PolicyViolationError(reason)
                
                # Execute
                result = self._original.invoke(input_data, **kwargs)
                
                # Post-check
                valid, reason = self._kernel.post_execute(self._ctx, result)
                if not valid:
                    raise PolicyViolationError(reason)
                
                return result
            
            async def ainvoke(self, input_data: Any, **kwargs) -> Any:
                """Governed async invoke"""
                allowed, reason = self._kernel.pre_execute(self._ctx, input_data)
                if not allowed:
                    raise PolicyViolationError(reason)
                
                result = await self._original.ainvoke(input_data, **kwargs)
                
                valid, reason = self._kernel.post_execute(self._ctx, result)
                if not valid:
                    raise PolicyViolationError(reason)
                
                return result
            
            def run(self, *args, **kwargs) -> Any:
                """Governed run (for agents)"""
                input_data = args[0] if args else kwargs
                allowed, reason = self._kernel.pre_execute(self._ctx, input_data)
                if not allowed:
                    raise PolicyViolationError(reason)
                
                result = self._original.run(*args, **kwargs)
                
                valid, reason = self._kernel.post_execute(self._ctx, result)
                if not valid:
                    raise PolicyViolationError(reason)
                
                return result
            
            def batch(self, inputs: list, **kwargs) -> list:
                """Governed batch"""
                for inp in inputs:
                    allowed, reason = self._kernel.pre_execute(self._ctx, inp)
                    if not allowed:
                        raise PolicyViolationError(reason)
                
                results = self._original.batch(inputs, **kwargs)
                
                for result in results:
                    valid, reason = self._kernel.post_execute(self._ctx, result)
                    if not valid:
                        raise PolicyViolationError(reason)
                
                return results
            
            def stream(self, input_data: Any, **kwargs):
                """Governed stream"""
                allowed, reason = self._kernel.pre_execute(self._ctx, input_data)
                if not allowed:
                    raise PolicyViolationError(reason)
                
                for chunk in self._original.stream(input_data, **kwargs):
                    yield chunk
                
                self._kernel.post_execute(self._ctx, None)
            
            # Passthrough for non-execution methods
            def __getattr__(self, name):
                return getattr(self._original, name)
        
        return GovernedLangChainAgent()
    
    def unwrap(self, governed_agent: Any) -> Any:
        """Get original agent from wrapped version"""
        return governed_agent._original


class PolicyViolationError(Exception):
    """Raised when an agent violates governance policy"""
    pass


# Convenience function
def wrap(agent: Any, policy: Optional[GovernancePolicy] = None) -> Any:
    """Quick wrapper for LangChain agents"""
    return LangChainKernel(policy).wrap(agent)
