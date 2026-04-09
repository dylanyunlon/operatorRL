"""
StorytellingFlags — Apollo storytelling_gflags.cc equivalent.
===============================================================
lolbot-HyperAI · modules/storytelling/common

Apollo reference:
    modules/storytelling/common/storytelling_gflags.cc
    modules/storytelling/common/storytelling_gflags.h

Centralizes all storytelling configuration flags, matching Apollo's
pattern of gflags-based configuration for module behavior.

位置: lolbot-HyperAI/modules/storytelling/common/storytelling_gflags.py
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StorytellingFlags:
    """Configuration flags for the storytelling module.

    Apollo equivalent: DEFINE_double / DEFINE_bool / DEFINE_string
    macros in storytelling_gflags.cc.
    """

    # ── Frame timing ─────────────────────────────────────────────────
    # How often the storytelling component processes events (ms)
    storytelling_interval_ms: float = 1000.0  # 1Hz

    # ── Narration behavior ───────────────────────────────────────────
    # Minimum gap between narrations of the same event type (seconds)
    same_type_cooldown_s: float = 10.0

    # Maximum narration queue depth before dropping low-priority items
    max_queue_size: int = 50

    # Recent hash window for duplicate detection
    recent_hash_window: int = 30

    # ── Tone adaptation ──────────────────────────────────────────────
    # Gold diff threshold for switching to "tense" tone
    tense_gold_diff_threshold: float = 3000.0

    # Win probability threshold for "warning" tone
    warning_win_prob_threshold: float = 0.35

    # Win probability threshold for "celebrating" tone
    celebrating_win_prob_threshold: float = 0.75

    # ── TTS integration ──────────────────────────────────────────────
    # Maximum text length for a single narration segment
    max_narration_length: int = 200

    # Speech rate hint (words per minute)
    default_speech_rate_wpm: int = 180

    # ── Story teller registration ────────────────────────────────────
    # Enable/disable individual story tellers
    enable_teamfight_teller: bool = True
    enable_objective_teller: bool = True
    enable_death_teller: bool = True
    enable_item_teller: bool = True
    enable_vision_teller: bool = True

    # ── Logging ──────────────────────────────────────────────────────
    storytelling_log_level: str = "INFO"
    enable_narration_logging: bool = True
