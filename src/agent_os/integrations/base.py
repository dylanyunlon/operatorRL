"""
Base Integration Interface

All framework adapters inherit from this base class.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from datetime import datetime


@dataclass
class GovernancePolicy:
    """Policy configuration for governed agents"""
    max_tokens: int = 4096
    max_tool_calls: int = 10
    allowed_tools: list[str] = field(default_factory=list)
    blocked_patterns: list[str] = field(default_factory=list)
    require_human_approval: bool = False
    timeout_seconds: int = 300
    
    # Safety thresholds
    confidence_threshold: float = 0.8
    drift_threshold: float = 0.15
    
    # Audit settings
    log_all_calls: bool = True
    checkpoint_frequency: int = 5  # Every N calls


@dataclass
class ExecutionContext:
    """Context passed through the governance layer"""
    agent_id: str
    session_id: str
    policy: GovernancePolicy
    start_time: datetime = field(default_factory=datetime.now)
    call_count: int = 0
    tool_calls: list[dict] = field(default_factory=list)
    checkpoints: list[str] = field(default_factory=list)


class BaseIntegration(ABC):
    """
    Base class for framework integrations.
    
    Wraps any agent framework with Agent OS governance:
    - Pre-execution policy checks
    - Post-execution validation
    - Flight recording
    - Signal handling
    """
    
    def __init__(self, policy: Optional[GovernancePolicy] = None):
        self.policy = policy or GovernancePolicy()
        self.contexts: dict[str, ExecutionContext] = {}
        self._signal_handlers: dict[str, Callable] = {}
    
    @abstractmethod
    def wrap(self, agent: Any) -> Any:
        """
        Wrap an agent with governance.
        
        Returns a governed version of the agent that:
        - Enforces policy on all operations
        - Records execution to flight recorder
        - Responds to signals (SIGSTOP, SIGKILL, etc.)
        """
        pass
    
    @abstractmethod
    def unwrap(self, governed_agent: Any) -> Any:
        """Remove governance wrapper and return original agent"""
        pass
    
    def create_context(self, agent_id: str) -> ExecutionContext:
        """Create execution context for an agent"""
        from uuid import uuid4
        ctx = ExecutionContext(
            agent_id=agent_id,
            session_id=str(uuid4())[:8],
            policy=self.policy
        )
        self.contexts[agent_id] = ctx
        return ctx
    
    def pre_execute(self, ctx: ExecutionContext, input_data: Any) -> tuple[bool, Optional[str]]:
        """
        Pre-execution policy check.
        
        Returns (allowed, reason) tuple.
        """
        # Check call count
        if ctx.call_count >= self.policy.max_tool_calls:
            return False, f"Max tool calls exceeded ({self.policy.max_tool_calls})"
        
        # Check timeout
        elapsed = (datetime.now() - ctx.start_time).total_seconds()
        if elapsed > self.policy.timeout_seconds:
            return False, f"Timeout exceeded ({self.policy.timeout_seconds}s)"
        
        # Check blocked patterns
        input_str = str(input_data)
        for pattern in self.policy.blocked_patterns:
            if pattern.lower() in input_str.lower():
                return False, f"Blocked pattern detected: {pattern}"
        
        return True, None
    
    def post_execute(self, ctx: ExecutionContext, output_data: Any) -> tuple[bool, Optional[str]]:
        """
        Post-execution validation.
        
        Returns (valid, reason) tuple.
        """
        ctx.call_count += 1
        
        # Checkpoint if needed
        if ctx.call_count % self.policy.checkpoint_frequency == 0:
            checkpoint_id = f"checkpoint-{ctx.call_count}"
            ctx.checkpoints.append(checkpoint_id)
        
        return True, None
    
    def on_signal(self, signal: str, handler: Callable):
        """Register a signal handler"""
        self._signal_handlers[signal] = handler
    
    def signal(self, agent_id: str, signal: str):
        """Send signal to agent"""
        if signal in self._signal_handlers:
            self._signal_handlers[signal](agent_id)
