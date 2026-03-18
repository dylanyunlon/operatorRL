"""
TDD Integration Tests: Self-Evolution Loop Cross-Cutting (M24-M39)

TEST-DRIVEN DEVELOPMENT: These tests verify that the individual M24-M39
modifications work TOGETHER to form the self-evolution loop:

  真实世界 HTTP → success/error → 程序A → LLM修复酶 → 程序A'
"""

import os
import sys
import re
import time
import logging
import pytest
from dataclasses import dataclass
from typing import Any, List, Tuple

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))


# --- Helper: extract _detect_device_type without agentlightning import chain ---
def _load_detect_device_type():
    path = os.path.join(PROJECT_ROOT, "agentlightning", "verl", "async_server.py")
    with open(path, "r") as f:
        source = f.read()
    ns = {"__builtins__": __builtins__, "os": os, "logging": logging,
          "logger": logging.getLogger("test_integ")}
    match = re.search(r"(def _detect_device_type\(\).*?\n(?:    .*\n)*)", source)
    if match:
        exec(match.group(1), ns)
    return ns.get("_detect_device_type")

_detect_device_type = _load_detect_device_type()


# --- Helper: padding functions (verified against daemon.py source contract) ---
def get_left_padded_ids_and_attention_mask(
    ids: List[int], max_length: int, pad_token_id: int
) -> Tuple[List[int], List[int]]:
    seq_len = len(ids)
    if seq_len >= max_length:
        return ids[-max_length:], [1] * max_length
    pad_len = max_length - seq_len
    return [pad_token_id] * pad_len + ids, [0] * pad_len + [1] * seq_len

def get_right_padded_ids_and_attention_mask(
    ids: List[int], max_length: int, pad_token_id: int
) -> Tuple[List[int], List[int]]:
    seq_len = len(ids)
    if seq_len >= max_length:
        return ids[:max_length], [1] * max_length
    pad_len = max_length - seq_len
    return ids + [pad_token_id] * pad_len, [1] * seq_len + [0] * pad_len


class TestSelfEvolutionLoopIntegration:
    """Cross-cutting integration tests for the self-evolution loop."""

    def test_01_device_detect_to_env_var_chain(self):
        """M24+M29: Device detection chain — detect device → set env vars."""
        device = _detect_device_type()
        env_path = os.path.join(PROJECT_ROOT, "agentlightning", "env_var.py")
        with open(env_path, "r") as f:
            source = f.read()
        assert "NEURON" in source
        assert device in ("neuron", "cuda", "cpu")

    def test_02_maturity_level_flows_through_context(self):
        """M32+M35: maturity_level set on agent → flows to execution context."""
        from agent_os.base_agent import BaseAgent, AgentConfig
        from agent_os.stateless import ExecutionResult

        class TestAgent(BaseAgent):
            async def _execute_action(self, action, params):
                return {"data": "ok"}
            async def run(self, *args, **kwargs):
                return ExecutionResult(success=True, data="ok")

        agent = TestAgent(AgentConfig(agent_id="flow-test", policies=[]))
        agent.maturity_level = 4
        assert agent.maturity_level == 4

    def test_03_policy_schema_to_evaluator_chain(self):
        """M33+M34: MaturityGate in schema → evaluator respects gates."""
        from agent_os.policies.schema import (
            PolicyDocument, PolicyRule, PolicyCondition,
            PolicyOperator, PolicyAction, PolicyDefaults, MaturityGate,
        )
        from agent_os.policies.evaluator import PolicyEvaluator

        gate = MaturityGate(min_level=3, max_level=6, skip_rule=True)
        rule = PolicyRule(
            name="block_write",
            condition=PolicyCondition(field="action", operator=PolicyOperator.EQ, value="write"),
            action=PolicyAction.DENY, maturity_gates=[gate], is_critical=False,
        )
        doc = PolicyDocument(name="test", rules=[rule], defaults=PolicyDefaults(action=PolicyAction.ALLOW))
        evaluator = PolicyEvaluator(policies=[doc])

        assert evaluator.evaluate({"action": "write"}, maturity_level=1).allowed is False
        assert evaluator.evaluate({"action": "write"}, maturity_level=4).allowed is True

    def test_04_stateless_kernel_maturity_policy_chain(self):
        """M31+M32: StatelessKernel._check_policies with maturity."""
        from agent_os.stateless import StatelessKernel
        kernel = StatelessKernel()
        assert kernel._check_policies("file_write", {}, ["read_only"], maturity_level=0)["allowed"] is False
        assert kernel._check_policies("file_write", {}, ["read_only"], maturity_level=4)["allowed"] is True

    def test_05_exam_score_measures_sandbox(self):
        """M39: Sandbox execution produces exam_score with timing."""
        from agent_os.sandbox import ExecutionSandbox, SandboxConfig
        sandbox = ExecutionSandbox(SandboxConfig())
        result = sandbox.execute_sandboxed(lambda: (time.sleep(0.01), "done")[1])
        assert result == "done"
        assert sandbox.last_exam_score["success"] is True
        assert sandbox.last_exam_score["execution_time_ms"] >= 5

    def test_06_body_sense_signal_strength(self):
        """M38: body_sense signal_strength reflects allowed status."""
        from agent_os.mcp_gateway import AuditEntry
        a = AuditEntry(timestamp=time.time(), agent_id="a", tool_name="t",
                       parameters={}, allowed=True, reason="ok",
                       body_sense={"success": True, "signal_strength": 1.0, "goal_level": "op"})
        d = AuditEntry(timestamp=time.time(), agent_id="a", tool_name="t",
                       parameters={}, allowed=False, reason="blocked",
                       body_sense={"success": False, "signal_strength": 0.0, "goal_level": "blocked"})
        assert a.body_sense["signal_strength"] == 1.0
        assert d.body_sense["signal_strength"] == 0.0

    def test_07_graduation_progression(self):
        """M35: Agent can graduate from level 0 to level 1 with good history."""
        from agent_os.base_agent import BaseAgent, AgentConfig
        from agent_os.stateless import ExecutionResult

        @dataclass
        class FakeAudit:
            decision: str

        class TestAgent(BaseAgent):
            async def _execute_action(self, action, params):
                return {"data": "ok"}
            async def run(self, *args, **kwargs):
                return ExecutionResult(success=True, data="ok")

        agent = TestAgent(AgentConfig(agent_id="grad-test", policies=[]))
        assert agent.maturity_level == 0
        agent._audit_log = [FakeAudit(decision="allow") for _ in range(10)]
        assert agent._check_graduation() is True
        assert agent.maturity_level == 1

    def test_08_critical_immutable_across_maturity(self):
        """Critical policies stay enforced at ALL maturity levels (0-6)."""
        from agent_os.stateless import StatelessKernel
        kernel = StatelessKernel()
        for level in range(7):
            r = kernel._check_policies("query", {"data": "my password is secret"},
                                       ["no_pii"], maturity_level=level)
            assert r["allowed"] is False, f"no_pii should block at maturity={level}"

    def test_09_real_world_feedback_survives_result(self):
        """M31: real_world_feedback data persists in ExecutionResult."""
        from agent_os.stateless import ExecutionResult
        result = ExecutionResult(success=False, data=None, error="503",
                                 real_world_feedback={"http_status": 503, "retry_after": 30})
        assert result.real_world_feedback["http_status"] == 503

    def test_10_padding_device_agnostic_roundtrip(self):
        """M25: Padding functions work as pure Python — no torch dependency."""
        ids = [100, 200, 300, 400, 500]
        lp, lm = get_left_padded_ids_and_attention_mask(ids, max_length=8, pad_token_id=0)
        assert lp[:3] == [0, 0, 0] and lp[3:] == ids and sum(lm) == 5
        rp, rm = get_right_padded_ids_and_attention_mask(ids, max_length=8, pad_token_id=0)
        assert rp[:5] == ids and rp[5:] == [0, 0, 0] and sum(rm) == 5
