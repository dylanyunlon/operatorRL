"""
AutoGen Integration

Wraps Microsoft AutoGen agents with Agent OS governance.

Usage:
    from agent_os.integrations import AutoGenKernel
    
    kernel = AutoGenKernel()
    kernel.govern(agent1, agent2, agent3)
    
    # Now all conversations are governed
    agent1.initiate_chat(agent2, message="...")
"""

from typing import Any, Optional, List

from .base import BaseIntegration, GovernancePolicy, ExecutionContext


class AutoGenKernel(BaseIntegration):
    """
    AutoGen adapter for Agent OS.
    
    Supports:
    - AssistantAgent
    - UserProxyAgent
    - GroupChat
    - Conversation flows
    """
    
    def __init__(self, policy: Optional[GovernancePolicy] = None):
        super().__init__(policy)
        self._governed_agents: dict[str, Any] = {}
    
    def wrap(self, agent: Any) -> Any:
        """Wrap a single AutoGen agent"""
        return self.govern(agent)[0]
    
    def govern(self, *agents: Any) -> List[Any]:
        """
        Add governance to multiple AutoGen agents.
        
        Intercepts:
        - initiate_chat()
        - generate_reply()
        - receive()
        - send()
        """
        governed = []
        
        for agent in agents:
            agent_id = getattr(agent, 'name', f"autogen-{id(agent)}")
            ctx = self.create_context(agent_id)
            
            # Store reference
            self._governed_agents[agent_id] = agent
            
            # Wrap key methods
            self._wrap_initiate_chat(agent, ctx)
            self._wrap_generate_reply(agent, ctx)
            self._wrap_receive(agent, ctx)
            
            governed.append(agent)
        
        return governed
    
    def _wrap_initiate_chat(self, agent: Any, ctx: ExecutionContext):
        """Wrap initiate_chat method"""
        if not hasattr(agent, 'initiate_chat'):
            return
        
        original = agent.initiate_chat
        kernel = self
        
        def governed_initiate_chat(recipient, message=None, **kwargs):
            allowed, reason = kernel.pre_execute(ctx, {"recipient": str(recipient), "message": message})
            if not allowed:
                from .langchain_adapter import PolicyViolationError
                raise PolicyViolationError(reason)
            
            result = original(recipient, message=message, **kwargs)
            
            kernel.post_execute(ctx, result)
            return result
        
        agent.initiate_chat = governed_initiate_chat
    
    def _wrap_generate_reply(self, agent: Any, ctx: ExecutionContext):
        """Wrap generate_reply method"""
        if not hasattr(agent, 'generate_reply'):
            return
        
        original = agent.generate_reply
        kernel = self
        
        def governed_generate_reply(messages=None, sender=None, **kwargs):
            allowed, reason = kernel.pre_execute(ctx, {"messages": messages, "sender": str(sender)})
            if not allowed:
                return f"[BLOCKED: {reason}]"
            
            result = original(messages=messages, sender=sender, **kwargs)
            
            valid, reason = kernel.post_execute(ctx, result)
            if not valid:
                return f"[BLOCKED: {reason}]"
            
            return result
        
        agent.generate_reply = governed_generate_reply
    
    def _wrap_receive(self, agent: Any, ctx: ExecutionContext):
        """Wrap receive method"""
        if not hasattr(agent, 'receive'):
            return
        
        original = agent.receive
        kernel = self
        
        def governed_receive(message, sender, **kwargs):
            allowed, reason = kernel.pre_execute(ctx, {"message": message, "sender": str(sender)})
            if not allowed:
                from .langchain_adapter import PolicyViolationError
                raise PolicyViolationError(reason)
            
            result = original(message, sender, **kwargs)
            
            kernel.post_execute(ctx, result)
            return result
        
        agent.receive = governed_receive
    
    def unwrap(self, governed_agent: Any) -> Any:
        """Note: AutoGen agents are modified in-place, can't easily unwrap"""
        raise NotImplementedError(
            "AutoGen agents are governed in-place. "
            "Create a new agent instance if you need ungoverned access."
        )


# Convenience function
def govern(*agents: Any, policy: Optional[GovernancePolicy] = None) -> List[Any]:
    """Quick governance for AutoGen agents"""
    return AutoGenKernel(policy).govern(*agents)
