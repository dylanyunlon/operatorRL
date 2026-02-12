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
from .langchain_adapter import PolicyViolationError


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
        self._original_methods: dict[str, dict[str, Any]] = {}
        self._stopped: dict[str, bool] = {}
    
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
            self._stopped[agent_id] = False
            
            # Store original methods before wrapping
            self._original_methods[agent_id] = {}
            for method_name in ('initiate_chat', 'generate_reply', 'receive'):
                if hasattr(agent, method_name):
                    self._original_methods[agent_id][method_name] = getattr(agent, method_name)
            
            # Wrap key methods
            self._wrap_initiate_chat(agent, ctx, agent_id)
            self._wrap_generate_reply(agent, ctx, agent_id)
            self._wrap_receive(agent, ctx, agent_id)
            
            governed.append(agent)
        
        return governed
    
    def _wrap_initiate_chat(self, agent: Any, ctx: ExecutionContext, agent_id: str):
        """Wrap initiate_chat method"""
        if not hasattr(agent, 'initiate_chat'):
            return
        
        original = agent.initiate_chat
        kernel = self
        
        def governed_initiate_chat(recipient, message=None, **kwargs):
            if kernel._stopped.get(agent_id):
                raise PolicyViolationError(f"Agent '{agent_id}' is stopped (SIGSTOP)")
            
            allowed, reason = kernel.pre_execute(ctx, {"recipient": str(recipient), "message": message})
            if not allowed:
                raise PolicyViolationError(reason)
            
            result = original(recipient, message=message, **kwargs)
            
            kernel.post_execute(ctx, result)
            return result
        
        agent.initiate_chat = governed_initiate_chat
    
    def _wrap_generate_reply(self, agent: Any, ctx: ExecutionContext, agent_id: str):
        """Wrap generate_reply method"""
        if not hasattr(agent, 'generate_reply'):
            return
        
        original = agent.generate_reply
        kernel = self
        
        def governed_generate_reply(messages=None, sender=None, **kwargs):
            if kernel._stopped.get(agent_id):
                return f"[BLOCKED: Agent '{agent_id}' is stopped (SIGSTOP)]"
            
            allowed, reason = kernel.pre_execute(ctx, {"messages": messages, "sender": str(sender)})
            if not allowed:
                return f"[BLOCKED: {reason}]"
            
            result = original(messages=messages, sender=sender, **kwargs)
            
            valid, reason = kernel.post_execute(ctx, result)
            if not valid:
                return f"[BLOCKED: {reason}]"
            
            return result
        
        agent.generate_reply = governed_generate_reply
    
    def _wrap_receive(self, agent: Any, ctx: ExecutionContext, agent_id: str):
        """Wrap receive method"""
        if not hasattr(agent, 'receive'):
            return
        
        original = agent.receive
        kernel = self
        
        def governed_receive(message, sender, **kwargs):
            if kernel._stopped.get(agent_id):
                raise PolicyViolationError(f"Agent '{agent_id}' is stopped (SIGSTOP)")
            
            allowed, reason = kernel.pre_execute(ctx, {"message": message, "sender": str(sender)})
            if not allowed:
                raise PolicyViolationError(reason)
            
            result = original(message, sender, **kwargs)
            
            kernel.post_execute(ctx, result)
            return result
        
        agent.receive = governed_receive
    
    def unwrap(self, governed_agent: Any) -> Any:
        """Restore original methods on a governed AutoGen agent"""
        agent_id = getattr(governed_agent, 'name', f"autogen-{id(governed_agent)}")
        originals = self._original_methods.get(agent_id, {})
        
        for method_name, original_method in originals.items():
            setattr(governed_agent, method_name, original_method)
        
        self._governed_agents.pop(agent_id, None)
        self._original_methods.pop(agent_id, None)
        self._stopped.pop(agent_id, None)
        
        return governed_agent
    
    def signal(self, agent_id: str, signal: str):
        """Send signal to a governed agent"""
        if signal == "SIGSTOP":
            self._stopped[agent_id] = True
        elif signal == "SIGCONT":
            self._stopped[agent_id] = False
        elif signal == "SIGKILL":
            if agent_id in self._governed_agents:
                agent = self._governed_agents[agent_id]
                self.unwrap(agent)
        
        super().signal(agent_id, signal)


# Convenience function
def govern(*agents: Any, policy: Optional[GovernancePolicy] = None) -> List[Any]:
    """Quick governance for AutoGen agents"""
    return AutoGenKernel(policy).govern(*agents)
