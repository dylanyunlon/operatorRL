"""
Memory - Adaptive Memory Hierarchy & Semantic Purge.

This module implements the "Adaptive Memory Hierarchy" with deterministic three-tier
storage, replacing probabilistic RAG-based memory with systematic routing based on
criticality and frequency.

The Three Tiers:
1. Tier 1 (Kernel): Safety-critical rules in system_prompt (zero latency)
2. Tier 2 (Skill Cache): Tool-specific rules injected conditionally (low latency)
3. Tier 3 (Archive): Long-tail wisdom retrieved via semantic search (high latency)

Key Components:
1. MemoryController: Routes lessons to appropriate tier and constructs dynamic context
2. Promotion/Demotion: Hot Tier 3 lessons promoted to Tier 2, cold Tier 1 demoted
3. Patch Classifier: Type A vs Type B classification for Semantic Purge
4. Semantic Purge: Model upgrade triggered cleanup ("Scale by Subtraction")

Promotion Logic:
- Tier 3 → Tier 2: Retrieved > 5 times in 24 hours (hot path optimization)

Demotion Logic:
- Tier 1 → Tier 2: No triggers in 30 days (keep kernel lean)
"""

import logging
from enum import Enum
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from collections import Counter
import json
import re

logger = logging.getLogger(__name__)

# Import models from agent_kernel for backward compatibility
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from agent_kernel.models import (
    CorrectionPatch, ClassifiedPatch, PatchDecayType,
    CognitiveGlitch, CompletenessAudit
)

# Import from local schemas
from src.kernel.schemas import Lesson, PatchRequest, MemoryTier


class MockRedisCache:
    """
    Mock Redis cache for Tier 2 (Skill Cache).
    In production, replace with actual Redis client.
    """
    
    def __init__(self):
        self.store: Dict[str, List[str]] = {}
    
    def rpush(self, key: str, value: str) -> None:
        """Append value to list."""
        if key not in self.store:
            self.store[key] = []
        self.store[key].append(value)
    
    def lrange(self, key: str, start: int, end: int) -> List[Dict]:
        """Get list range."""
        if key not in self.store:
            return []
        items = self.store[key][start:end+1] if end >= 0 else self.store[key][start:]
        return [json.loads(item) for item in items]
    
    def incr(self, key: str) -> int:
        """Increment counter."""
        if key not in self.store:
            self.store[key] = ["0"]
        count = int(self.store[key][0]) + 1
        self.store[key] = [str(count)]
        return count
    
    def get(self, key: str) -> Optional[str]:
        """Get value."""
        if key not in self.store:
            return None
        return self.store[key][0] if self.store[key] else None
    
    def delete(self, key: str) -> None:
        """Delete key."""
        if key in self.store:
            del self.store[key]


class MockVectorStore:
    """
    Mock Vector DB for Tier 3 (Archive).
    In production, replace with Chroma/Pinecone/etc.
    """
    
    def __init__(self):
        self.documents: List[Dict] = []
    
    def add(self, documents: List[str], metadatas: List[Dict], ids: List[str]) -> None:
        """Add documents to vector store."""
        for doc, meta, doc_id in zip(documents, metadatas, ids):
            self.documents.append({
                "id": doc_id,
                "text": doc,
                "metadata": meta
            })
    
    def similarity_search(self, query: str, k: int = 2) -> List[Dict]:
        """
        Mock similarity search using simple keyword matching.
        In production, this would use embeddings and vector similarity.
        """
        query_lower = query.lower()
        scores = []
        
        for doc in self.documents:
            # Simple scoring: count matching words
            doc_text = doc["text"].lower()
            query_words = set(query_lower.split())
            doc_words = set(doc_text.split())
            score = len(query_words & doc_words)
            
            if score > 0:
                scores.append((score, doc))
        
        # Sort by score and return top k
        scores.sort(reverse=True, key=lambda x: x[0])
        return [{"page_content": doc["text"], "metadata": doc["metadata"]} 
                for _, doc in scores[:k]]


class MemoryController:
    """
    The systematic router for the Adaptive Memory Hierarchy.
    
    This implements deterministic tiering based on criticality and frequency,
    replacing probabilistic RAG-based approaches with systematic rules.
    
    Architecture:
    - Tier 1 (Kernel): Safety-critical rules always in system_prompt
    - Tier 2 (Skill Cache): Tool-specific rules injected conditionally  
    - Tier 3 (Archive): Long-tail wisdom retrieved on-demand
    
    Responsibilities:
    1. Route new lessons to appropriate tier
    2. Inject context dynamically based on active tools
    3. Track retrieval frequency for promotion
    4. Track usage for demotion (keeping Kernel lean)
    """
    
    def __init__(self, vector_store=None, redis_cache=None):
        """
        Initialize MemoryController with storage backends.
        
        Args:
            vector_store: Vector DB for Tier 3 (defaults to mock)
            redis_cache: Redis cache for Tier 2 (defaults to mock)
        """
        self.vector_store = vector_store or MockVectorStore()
        self.redis_cache = redis_cache or MockRedisCache()
        
        # Tier 1 is loaded from config/code at startup
        self.kernel_rules: List[Lesson] = []
        
        # Tracking for promotion/demotion
        self.retrieval_window = timedelta(hours=24)
        self.promotion_threshold = 5  # 5 retrievals in 24h
        self.demotion_window = timedelta(days=30)
        
        logger.info("MemoryController initialized with 3-tier architecture")
    
    def route_lesson(self, patch: PatchRequest) -> MemoryTier:
        """
        The systematic logic to decide where a new lesson goes.
        
        This is the core of "Deterministic Tiering" - no guesswork, just rules:
        1. Security/Safety → Tier 1 (Kernel)
        2. Tool-specific syntax → Tier 2 (Skill Cache)
        3. Business logic/edge cases → Tier 3 (Archive)
        
        Args:
            patch: The patch request containing the lesson to route
            
        Returns:
            MemoryTier: The tier where this lesson should be stored
        """
        lesson = patch.proposed_lesson
        
        # 1. Critical Safety → Tier 1 (Kernel)
        # These must ALWAYS be active (e.g., "Never delete root")
        if lesson.lesson_type == "security":
            logger.info(f"Routing lesson to Tier 1 (security): {lesson.rule_text[:50]}")
            return MemoryTier.TIER_1_KERNEL
        
        if patch.diagnosis and "critical" in patch.diagnosis.lower():
            logger.info(f"Routing lesson to Tier 1 (critical failure): {lesson.rule_text[:50]}")
            return MemoryTier.TIER_1_KERNEL
        
        # 2. Tool-Specific Syntax → Tier 2 (Skill Cache)
        # Pattern: "tool:sql_query" or "when using <tool>"
        if self._is_tool_specific(lesson):
            tool_name = self._extract_tool_name(lesson.trigger_pattern)
            logger.info(f"Routing lesson to Tier 2 (tool: {tool_name}): {lesson.rule_text[:50]}")
            return MemoryTier.TIER_2_SKILL_CACHE
        
        # 3. Everything else (Business logic, edge cases) → Tier 3 (Archive)
        logger.info(f"Routing lesson to Tier 3 (archive): {lesson.rule_text[:50]}")
        return MemoryTier.TIER_3_ARCHIVE
    
    def _is_tool_specific(self, lesson: Lesson) -> bool:
        """Check if lesson is tool-specific."""
        trigger = lesson.trigger_pattern.lower()
        rule = lesson.rule_text.lower()
        
        # Explicit tool prefix
        if "tool:" in trigger:
            return True
        
        # Common tool-related patterns
        tool_indicators = [
            "when using", "with tool", "for tool",
            "sql", "query", "database", "api call",
            "file operation", "http request"
        ]
        
        return any(indicator in trigger or indicator in rule 
                  for indicator in tool_indicators)
    
    def _extract_tool_name(self, trigger_pattern: str) -> str:
        """
        Extract tool name from trigger pattern.
        
        Examples:
        - "tool:sql_query" → "sql_query"
        - "when using file_reader" → "file_reader"
        """
        # Check for explicit "tool:" prefix
        match = re.search(r'tool:(\w+)', trigger_pattern.lower())
        if match:
            return match.group(1)
        
        # Check for "when using <tool>" pattern
        match = re.search(r'when using (\w+)', trigger_pattern.lower())
        if match:
            return match.group(1)
        
        # Check for common tool names
        common_tools = ["sql", "api", "file", "http", "database"]
        for tool in common_tools:
            if tool in trigger_pattern.lower():
                return tool
        
        return "general"
    
    def commit_lesson(self, patch: PatchRequest) -> Dict[str, str]:
        """
        Commit a lesson to its determined tier.
        
        Args:
            patch: The patch request containing the lesson
            
        Returns:
            dict: Status information about the commit
        """
        tier = self.route_lesson(patch)
        lesson = patch.proposed_lesson
        
        # Update lesson metadata
        lesson.tier = tier
        
        logger.info(f"💾 Committing Lesson '{lesson.rule_text[:50]}...' to {tier.value}")
        
        if tier == MemoryTier.TIER_1_KERNEL:
            # Append to kernel rules (in production, write to system_prompt file)
            self.kernel_rules.append(lesson)
            return {"status": "committed", "tier": tier.value, "location": "kernel"}
        
        elif tier == MemoryTier.TIER_2_SKILL_CACHE:
            # Key is the tool name, Value is the serialized lesson
            tool_name = self._extract_tool_name(lesson.trigger_pattern)
            self.redis_cache.rpush(f"skill:{tool_name}", lesson.model_dump_json())
            return {"status": "committed", "tier": tier.value, "tool": tool_name}
        
        elif tier == MemoryTier.TIER_3_ARCHIVE:
            # Store in vector DB for semantic search
            self.vector_store.add(
                documents=[lesson.rule_text],
                metadatas=[lesson.model_dump()],
                ids=[lesson.id]
            )
            return {"status": "committed", "tier": tier.value, "location": "vector_db"}
        
        return {"status": "error", "message": "Unknown tier"}
    
    def retrieve_context(
        self, 
        current_task: str, 
        active_tools: List[str]
    ) -> str:
        """
        Constructs the Dynamic System Prompt using the three-tier hierarchy.
        
        This is the "injection efficiency" mechanism that keeps context minimal:
        - Always includes Tier 1 (Kernel) rules
        - Conditionally injects Tier 2 rules (only for active tools)
        - Retrieves Tier 3 rules on-demand (semantic search)
        
        Args:
            current_task: The current user task/prompt
            active_tools: List of tools the agent has access to
            
        Returns:
            str: The constructed context block to inject into system prompt
        """
        context_block = []
        
        # 1. Always add Kernel Rules (Tier 1)
        # These are safety-critical and must always be active
        if self.kernel_rules:
            context_block.append("=== CRITICAL SAFETY RULES ===")
            for rule in self.kernel_rules:
                context_block.append(f"- {rule.rule_text}")
            context_block.append("")
        
        # 2. Inject Skill Rules (Tier 2) - ONLY for active tools
        # This is the key optimization: SQL rules only when SQL is available
        for tool in active_tools:
            cached_lessons = self.redis_cache.lrange(f"skill:{tool}", 0, -1)
            if cached_lessons:
                context_block.append(f"=== Guidelines for {tool.upper()} ===")
                for lesson_dict in cached_lessons:
                    context_block.append(f"- {lesson_dict['rule_text']}")
                context_block.append("")
        
        # 3. RAG Search (Tier 3) - Only if task is complex
        # Only retrieve if the prompt suggests complexity or ambiguity
        if len(current_task.split()) > 5:  # Simple heuristic
            relevant_docs = self.vector_store.similarity_search(current_task, k=2)
            if relevant_docs:
                context_block.append("=== Relevant Past Lessons ===")
                for doc in relevant_docs:
                    context_block.append(f"- {doc['page_content']}")
                    # Track retrieval for promotion logic
                    self._track_retrieval(doc['metadata']['id'])
                context_block.append("")
        
        return "\n".join(context_block)
    
    def _track_retrieval(self, lesson_id: str) -> None:
        """
        Track retrieval count for promotion logic.
        
        If a Tier 3 lesson is retrieved > 5 times in 24 hours,
        it should be promoted to Tier 2 (hot path optimization).
        """
        key = f"retrieval:{lesson_id}"
        count = self.redis_cache.incr(key)
        
        logger.debug(f"Tracked retrieval for lesson {lesson_id}: {count} times")
        
        # Check if promotion threshold reached
        if count >= self.promotion_threshold:
            logger.info(f"🔥 Lesson {lesson_id} is HOT ({count} retrievals) - candidate for promotion")
    
    def promote_hot_lessons(self) -> Dict[str, int]:
        """
        Promote frequently retrieved Tier 3 lessons to Tier 2.
        
        This is the "hot path optimization" - lessons that are retrieved
        often enough should be moved to the skill cache for faster access.
        
        Returns:
            dict: Statistics about promotions
        """
        # In production, this would scan retrieval counters
        # For now, this is a placeholder
        logger.info("🚀 Scanning for hot Tier 3 lessons to promote...")
        return {"promoted_count": 0}
    
    def demote_cold_kernel_rules(self) -> Dict[str, int]:
        """
        Demote Tier 1 rules that haven't triggered in 30 days.
        
        This keeps the Kernel lean - only truly critical rules stay in Tier 1.
        Rules that aren't being used can be demoted to Tier 2.
        
        Returns:
            dict: Statistics about demotions
        """
        now = datetime.now()
        demoted = 0
        
        for rule in self.kernel_rules[:]:  # Copy list to avoid modification during iteration
            # Check if rule has triggered recently
            if rule.last_triggered_at is None:
                # Never triggered - candidate for demotion
                if (now - rule.created_at) > self.demotion_window:
                    logger.info(f"❄️  Demoting cold Tier 1 rule: {rule.rule_text[:50]}")
                    self.kernel_rules.remove(rule)
                    demoted += 1
            elif (now - rule.last_triggered_at) > self.demotion_window:
                logger.info(f"❄️  Demoting cold Tier 1 rule: {rule.rule_text[:50]}")
                self.kernel_rules.remove(rule)
                demoted += 1
        
        logger.info(f"Demoted {demoted} cold Tier 1 rules")
        return {"demoted_count": demoted}


class LessonType(Enum):
    """Types of lessons for lifecycle management."""
    SYNTAX = "syntax"         # Expire on model upgrade (e.g. "Output JSON")
    BUSINESS = "business"     # Never expire (e.g. "Fiscal year starts Oct")
    ONE_OFF = "one_off"       # Delete immediately (Transient error)


class MemoryManager:
    """
    Lesson lifecycle manager implementing Semantic Purge.
    
    This is the simplified reference implementation showing the core concept
    of tagging lessons by type so syntax lessons can be deleted on model upgrades.
    """
    
    def __init__(self):
        self.vector_store = []  # Simplified in-memory storage
        
    def add_lesson(self, lesson_text: str, lesson_type: LessonType, model_version: str = "gpt-4-0125"):
        """
        Add a lesson with lifecycle metadata.
        
        Args:
            lesson_text: The lesson content
            lesson_type: Type of lesson (SYNTAX, BUSINESS, or ONE_OFF)
            model_version: Model version when lesson was created
        """
        entry = {
            "text": lesson_text,
            "type": lesson_type,
            "model_version": model_version,
            "created_at": datetime.now()
        }
        self.vector_store.append(entry)

    def run_upgrade_purge(self, new_model_version: str) -> dict:
        """
        Called when you switch from GPT-4 to GPT-5.
        Deletes all 'SYNTAX' lessons (Type A patches).
        
        This is "Scale by Subtraction" - removing complexity, not adding it.
        
        Args:
            new_model_version: The new model version
            
        Returns:
            dict: Statistics about the purge
        """
        # Filter out SYNTAX lessons
        original_count = len(self.vector_store)
        self.vector_store = [
            entry for entry in self.vector_store 
            if entry["type"] != LessonType.SYNTAX
        ]
        purged_count = original_count - len(self.vector_store)
        
        logger.info(f"🗑️  Semantic Purge: {purged_count} Type A lessons removed on upgrade to {new_model_version}")
        
        return {
            "purged_count": purged_count,
            "retained_count": len(self.vector_store),
            "new_model_version": new_model_version,
            "reduction_percentage": (purged_count / original_count * 100) if original_count > 0 else 0
        }
    
    def get_lessons_by_type(self, lesson_type: LessonType) -> List[dict]:
        """
        Get all lessons of a specific type.
        
        Args:
            lesson_type: The type of lessons to retrieve
            
        Returns:
            list: Lessons matching the type
        """
        return [
            entry for entry in self.vector_store
            if entry["type"] == lesson_type
        ]
    
    def get_lesson_count(self) -> dict:
        """Get count of lessons by type."""
        type_counts = Counter(entry["type"] for entry in self.vector_store)
        return dict(type_counts)


class PatchClassifier:
    """
    Classifies patches into Type A (Syntax) vs Type B (Business).
    
    This is the "Taxonomy of Lessons" that determines lifecycle.
    Production-grade classifier with sophisticated heuristics.
    """
    
    def __init__(self):
        self.syntax_indicators = [
            "output json", "format", "syntax", "parse", "validation error",
            "type mismatch", "parameter type", "limit 10", "use uuid",
            "tool definition", "schema injection", "parameter checking",
            "encoding", "serialization", "casting"
        ]
        
        self.business_indicators = [
            "fiscal year", "project", "entity", "business rule", "policy",
            "archived", "deprecated", "does not exist", "negative constraint",
            "company", "organization", "domain", "customer", "workflow",
            "regulation", "compliance", "privacy"
        ]
    
    def classify_patch(
        self,
        patch: CorrectionPatch,
        current_model_version: str
    ) -> ClassifiedPatch:
        """
        Classify a patch as Type A or Type B.
        
        Args:
            patch: The correction patch to classify
            current_model_version: Current model version (e.g., "gpt-4o", "gpt-5")
            
        Returns:
            ClassifiedPatch with decay type and metadata
        """
        logger.info(f"Classifying patch {patch.patch_id}")
        
        # Analyze patch content to determine type
        decay_type = self._determine_decay_type(patch)
        
        # Determine if should purge on upgrade
        should_purge = (decay_type == PatchDecayType.SYNTAX_CAPABILITY)
        
        # Build metadata
        metadata = self._build_decay_metadata(patch, decay_type)
        
        classified = ClassifiedPatch(
            base_patch=patch,
            decay_type=decay_type,
            created_at_model_version=current_model_version,
            decay_metadata=metadata,
            should_purge_on_upgrade=should_purge
        )
        
        logger.info(f"Classified as {decay_type.value} (purge on upgrade: {should_purge})")
        
        return classified
    
    def _determine_decay_type(self, patch: CorrectionPatch) -> PatchDecayType:
        """
        Determine if patch is Type A (Syntax) or Type B (Business).
        
        Type A - Syntax/Capability (HIGH DECAY):
        - Model-specific issues (JSON formatting, type errors)
        - Tool usage errors (wrong parameter types)
        - Syntax errors, validation issues
        - These are likely fixed in newer model versions
        
        Type B - Business/Context (ZERO DECAY):
        - Company-specific rules ("Fiscal year starts in July")
        - Entity existence ("Project_Alpha is deprecated")
        - Policy violations (medical advice restrictions)
        - These are world truths that models can't learn
        """
        # Check diagnosis first (most reliable indicator)
        if patch.diagnosis:
            glitch = patch.diagnosis.cognitive_glitch
            
            # Tool misuse is almost always Type A
            if glitch == CognitiveGlitch.TOOL_MISUSE:
                return PatchDecayType.SYNTAX_CAPABILITY
            
            # Policy violations are Type B
            if glitch == CognitiveGlitch.POLICY_VIOLATION:
                return PatchDecayType.BUSINESS_CONTEXT
            
            # Hallucinations about entities are Type B
            if glitch == CognitiveGlitch.HALLUCINATION:
                return PatchDecayType.BUSINESS_CONTEXT
        
        # Analyze patch content
        content_str = str(patch.patch_content).lower()
        
        # Count indicators
        syntax_score = sum(1 for ind in self.syntax_indicators if ind in content_str)
        business_score = sum(1 for ind in self.business_indicators if ind in content_str)
        
        # Decide based on scores
        if business_score > syntax_score:
            return PatchDecayType.BUSINESS_CONTEXT
        elif syntax_score > 0:
            return PatchDecayType.SYNTAX_CAPABILITY
        else:
            # Default to business context if uncertain
            return PatchDecayType.BUSINESS_CONTEXT
    
    def _build_decay_metadata(self, patch: CorrectionPatch, decay_type: PatchDecayType) -> dict:
        """Build metadata for patch lifecycle tracking."""
        return {
            "decay_type": decay_type.value,
            "created_at": datetime.now().isoformat(),
            "failure_type": patch.failure_type.value if hasattr(patch.failure_type, 'value') else str(patch.failure_type),
            "cognitive_glitch": patch.diagnosis.cognitive_glitch.value if patch.diagnosis else None,
            "estimated_tokens": len(str(patch.patch_content).split()) * 1.3  # Rough estimate
        }


class SemanticPurge:
    """
    Orchestrates the Semantic Purge process on model upgrades.
    
    This is the "Scale by Subtraction" engine that prevents unbounded growth.
    """
    
    def __init__(self):
        self.classifier = PatchClassifier()
        self.memory_manager = MemoryManager()
        self.purge_history: List[dict] = []
    
    def execute_purge(
        self,
        patches: List[CorrectionPatch],
        old_model_version: str,
        new_model_version: str
    ) -> dict:
        """
        Execute semantic purge on model upgrade.
        
        Args:
            patches: List of patches to evaluate
            old_model_version: Current model version
            new_model_version: Upgraded model version
            
        Returns:
            dict: Purge statistics
        """
        logger.info(f"🔄 Starting Semantic Purge: {old_model_version} → {new_model_version}")
        
        # Classify all patches
        classified_patches = [
            self.classifier.classify_patch(p, old_model_version)
            for p in patches
        ]
        
        # Separate purgeable (Type A) from permanent (Type B)
        purgeable = [p for p in classified_patches if p.should_purge_on_upgrade]
        permanent = [p for p in classified_patches if not p.should_purge_on_upgrade]
        
        # Calculate token savings
        tokens_reclaimed = sum(
            p.decay_metadata.get("estimated_tokens", 0)
            for p in purgeable
        )
        
        stats = {
            "old_model_version": old_model_version,
            "new_model_version": new_model_version,
            "total_patches": len(patches),
            "purged_count": len(purgeable),
            "retained_count": len(permanent),
            "tokens_reclaimed": int(tokens_reclaimed),
            "reduction_percentage": (len(purgeable) / len(patches) * 100) if patches else 0
        }
        
        self.purge_history.append({
            "timestamp": datetime.now(),
            "stats": stats
        })
        
        logger.info(f"✨ Purge complete: {stats['purged_count']} Type A patches removed ({stats['reduction_percentage']:.1f}%)")
        logger.info(f"💾 Tokens reclaimed: {stats['tokens_reclaimed']}")
        
        return stats
    
    def get_purge_history(self) -> List[dict]:
        """Get history of purge operations."""
        return self.purge_history
