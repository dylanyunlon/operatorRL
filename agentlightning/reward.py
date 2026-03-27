# Copyright (c) Microsoft. All rights reserved.

import warnings

from .emitter.reward import *  # noqa: F401,F403

warnings.warn("agentlightning.reward is deprecated. Please use agentlightning.emitter instead.")

# --- AgentRL self-evolution reward constants (M112) ---
_REWARD_EVOLUTION_SIGNAL: str = "agentrl.reward.evolution.signal"
_REWARD_COMPUTE_BACKEND: str = "auto"
_REWARD_MATURITY_WEIGHT: float = 0.15
