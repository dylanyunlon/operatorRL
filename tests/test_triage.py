"""
Unit tests for the Failure Triage Engine.
"""

import unittest
from agent_kernel.triage import FailureTriage, FixStrategy


class TestFailureTriage(unittest.TestCase):
    """Tests for FailureTriage decision engine."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.triage = FailureTriage()
    
    def test_critical_tool_sync_jit(self):
        """Test that critical tools trigger SYNC_JIT strategy."""
        # Test with critical tool name
        strategy = self.triage.decide_strategy(
            prompt="Delete the user records",
            tool_name="delete_resource"
        )
        self.assertEqual(strategy, FixStrategy.SYNC_JIT)
        
        # Test with execute_payment
        strategy = self.triage.decide_strategy(
            prompt="Process refund for customer",
            tool_name="execute_payment"
        )
        self.assertEqual(strategy, FixStrategy.SYNC_JIT)
        
        # Test with drop_table
        strategy = self.triage.decide_strategy(
            prompt="Clean up old data",
            tool_name="drop_table"
        )
        self.assertEqual(strategy, FixStrategy.SYNC_JIT)
    
    def test_critical_action_in_context(self):
        """Test that critical actions in context trigger SYNC_JIT."""
        strategy = self.triage.decide_strategy(
            prompt="Delete some files",
            context={"action": "delete_file", "path": "/important/data"}
        )
        self.assertEqual(strategy, FixStrategy.SYNC_JIT)
        
        strategy = self.triage.decide_strategy(
            prompt="Update database",
            context={"action": "update_db", "table": "users"}
        )
        self.assertEqual(strategy, FixStrategy.SYNC_JIT)
    
    def test_high_effort_prompt_sync_jit(self):
        """Test that high-effort prompts trigger SYNC_JIT strategy."""
        # Test with "carefully"
        strategy = self.triage.decide_strategy(
            prompt="Please carefully analyze the security logs",
            tool_name="read_logs"
        )
        self.assertEqual(strategy, FixStrategy.SYNC_JIT)
        
        # Test with "critical"
        strategy = self.triage.decide_strategy(
            prompt="This is a critical operation for production",
            tool_name="query_db"
        )
        self.assertEqual(strategy, FixStrategy.SYNC_JIT)
        
        # Test with "important"
        strategy = self.triage.decide_strategy(
            prompt="Important: Check all user permissions",
            tool_name="check_permissions"
        )
        self.assertEqual(strategy, FixStrategy.SYNC_JIT)
        
        # Test with "urgent"
        strategy = self.triage.decide_strategy(
            prompt="Urgent request from customer",
            tool_name="fetch_data"
        )
        self.assertEqual(strategy, FixStrategy.SYNC_JIT)
        
        # Test with "must"
        strategy = self.triage.decide_strategy(
            prompt="You must verify all entries before proceeding",
            tool_name="verify"
        )
        self.assertEqual(strategy, FixStrategy.SYNC_JIT)
    
    def test_vip_user_sync_jit(self):
        """Test that VIP users trigger SYNC_JIT strategy."""
        strategy = self.triage.decide_strategy(
            prompt="Fetch my account details",
            tool_name="read_account",
            user_metadata={"is_vip": True}
        )
        self.assertEqual(strategy, FixStrategy.SYNC_JIT)
        
        strategy = self.triage.decide_strategy(
            prompt="Search for logs",
            tool_name="search_logs",
            user_metadata={"is_vip": True, "tier": "platinum"}
        )
        self.assertEqual(strategy, FixStrategy.SYNC_JIT)
    
    def test_read_operations_async_batch(self):
        """Test that read/query operations default to ASYNC_BATCH."""
        # Simple read operation
        strategy = self.triage.decide_strategy(
            prompt="Get the latest logs",
            tool_name="read_logs"
        )
        self.assertEqual(strategy, FixStrategy.ASYNC_BATCH)
        
        # Query operation
        strategy = self.triage.decide_strategy(
            prompt="Find user with email test@example.com",
            tool_name="query_users"
        )
        self.assertEqual(strategy, FixStrategy.ASYNC_BATCH)
        
        # Fetch operation
        strategy = self.triage.decide_strategy(
            prompt="Fetch recent data",
            tool_name="fetch_data"
        )
        self.assertEqual(strategy, FixStrategy.ASYNC_BATCH)
    
    def test_default_to_async_batch(self):
        """Test that non-critical operations default to ASYNC_BATCH."""
        strategy = self.triage.decide_strategy(
            prompt="Show me the dashboard",
            tool_name="render_dashboard"
        )
        self.assertEqual(strategy, FixStrategy.ASYNC_BATCH)
        
        strategy = self.triage.decide_strategy(
            prompt="List all available options",
            tool_name="list_options"
        )
        self.assertEqual(strategy, FixStrategy.ASYNC_BATCH)
    
    def test_is_critical_convenience_method(self):
        """Test the is_critical convenience method."""
        # Critical tool should return True
        self.assertTrue(
            self.triage.is_critical(
                prompt="Delete records",
                tool_name="delete_resource"
            )
        )
        
        # High effort prompt should return True
        self.assertTrue(
            self.triage.is_critical(
                prompt="This is critical operation",
                tool_name="some_tool"
            )
        )
        
        # Non-critical should return False
        self.assertFalse(
            self.triage.is_critical(
                prompt="Show me data",
                tool_name="read_data"
            )
        )
    
    def test_custom_critical_tools(self):
        """Test custom critical tools configuration."""
        custom_triage = FailureTriage(config={
            "critical_tools": ["custom_delete", "custom_update"]
        })
        
        # Custom critical tool
        strategy = custom_triage.decide_strategy(
            prompt="Do something",
            tool_name="custom_delete"
        )
        self.assertEqual(strategy, FixStrategy.SYNC_JIT)
        
        # Default critical tool not in custom list
        strategy = custom_triage.decide_strategy(
            prompt="Delete resource",
            tool_name="delete_resource"
        )
        self.assertEqual(strategy, FixStrategy.ASYNC_BATCH)
    
    def test_custom_high_effort_keywords(self):
        """Test custom high effort keywords configuration."""
        custom_triage = FailureTriage(config={
            "high_effort_keywords": ["immediate", "priority"]
        })
        
        # Custom keyword
        strategy = custom_triage.decide_strategy(
            prompt="This needs immediate attention",
            tool_name="some_tool"
        )
        self.assertEqual(strategy, FixStrategy.SYNC_JIT)
        
        # Default keyword not in custom list
        strategy = custom_triage.decide_strategy(
            prompt="This is critical",
            tool_name="some_tool"
        )
        self.assertEqual(strategy, FixStrategy.ASYNC_BATCH)
    
    def test_case_insensitive_keyword_matching(self):
        """Test that keyword matching is case-insensitive."""
        # Uppercase keyword
        strategy = self.triage.decide_strategy(
            prompt="CRITICAL operation needed",
            tool_name="some_tool"
        )
        self.assertEqual(strategy, FixStrategy.SYNC_JIT)
        
        # Mixed case
        strategy = self.triage.decide_strategy(
            prompt="Please handle this CareFully",
            tool_name="some_tool"
        )
        self.assertEqual(strategy, FixStrategy.SYNC_JIT)
    
    def test_priority_order(self):
        """Test that rules are applied in correct priority order."""
        # Critical tool overrides default async
        strategy = self.triage.decide_strategy(
            prompt="Just delete this",
            tool_name="delete_resource"
        )
        self.assertEqual(strategy, FixStrategy.SYNC_JIT)
        
        # High effort keyword overrides default async
        strategy = self.triage.decide_strategy(
            prompt="Critical: fetch this data",
            tool_name="fetch_data"
        )
        self.assertEqual(strategy, FixStrategy.SYNC_JIT)
        
        # VIP user overrides default async
        strategy = self.triage.decide_strategy(
            prompt="Get my data",
            tool_name="fetch_data",
            user_metadata={"is_vip": True}
        )
        self.assertEqual(strategy, FixStrategy.SYNC_JIT)


class TestTriageIntegration(unittest.TestCase):
    """Integration tests for triage with kernel."""
    
    def test_triage_import(self):
        """Test that triage components can be imported."""
        from agent_kernel import FailureTriage, FixStrategy
        
        triage = FailureTriage()
        self.assertIsNotNone(triage)
        
        # Test enum values - these are part of the API contract
        # and returned to users in result dictionaries
        self.assertEqual(FixStrategy.SYNC_JIT.value, "jit_retry")
        self.assertEqual(FixStrategy.ASYNC_BATCH.value, "async_patch")
    
    def test_kernel_has_triage(self):
        """Test that kernel initializes with triage engine."""
        from agent_kernel import SelfCorrectingAgentKernel
        
        kernel = SelfCorrectingAgentKernel()
        self.assertIsNotNone(kernel.triage)
        self.assertIsInstance(kernel.triage, FailureTriage)
    
    def test_kernel_triage_stats(self):
        """Test that kernel provides triage stats."""
        from agent_kernel import SelfCorrectingAgentKernel
        
        kernel = SelfCorrectingAgentKernel()
        stats = kernel.get_triage_stats()
        
        self.assertIn("async_queue_size", stats)
        self.assertIn("critical_tools", stats)
        self.assertIn("high_effort_keywords", stats)
        self.assertEqual(stats["async_queue_size"], 0)  # Initially empty


if __name__ == "__main__":
    unittest.main()
