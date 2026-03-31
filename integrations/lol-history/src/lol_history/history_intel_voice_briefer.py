"""
HistoryIntelVoiceBriefer — Generates voice-ready text briefings from historical intel.

Architecture (拿来主义):
  history_driven_voice_briefer.py（M602）— voice briefing generation
  pregame_voice_briefer.py（M598）— pregame voice output
  voice_narration_engine.py — TTS bridge

Location: integrations/lol-history/src/lol_history/history_intel_voice_briefer.py

Design Notes (Knuth-level critique):
  User:
    - Hands-free intel: spoken briefings during champ select and loading screen.
    - Concise: each briefing ≤30 seconds spoken time (~75 words).
    - Prioritized: threats first, then opportunities, then neutral observations.
  System:
    - Text generation is template-based, not LLM-dependent (zero latency).
    - Templates support variable substitution for player names, stats, champions.
    - Voice briefer is stateless: each call produces self-contained output.
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.history_intel_voice_briefer.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

_TEMPLATES = {
    "threat_high_rank": "注意，{name}是{tier}段位选手，{champion}胜率{winrate}%，是主要威胁。",
    "threat_one_trick": "{name}是{champion}的绝活哥，{games}局经验，胜率{winrate}%。建议Ban。",
    "opportunity_low_wr": "{name}最近{champion}表现不佳，{games}局只有{winrate}%胜率。可以针对。",
    "opportunity_autofill": "{name}可能是被自动填充到{role}位。历史数据显示主玩{main_role}。",
    "smurf_warning": "小心，{name}的{champion}数据异常，可能是代练或小号。",
    "neutral_info": "{name}使用{champion}，{tier}段位，{winrate}%胜率。",
    "team_comp": "敌方阵容偏向{archetype}风格。注意{key_point}。",
    "pregame_summary": "赛前简报：{threats}个主要威胁，{opportunities}个可利用的弱点。",
}

_TIER_CN = {
    "IRON": "黑铁", "BRONZE": "青铜", "SILVER": "白银", "GOLD": "黄金",
    "PLATINUM": "铂金", "EMERALD": "翡翠", "DIAMOND": "钻石",
    "MASTER": "大师", "GRANDMASTER": "宗师", "CHALLENGER": "王者",
}


class HistoryIntelVoiceBriefer:
    """Generates voice-ready text briefings from historical intelligence.

    Public API: generate_pregame_briefing, generate_threat_alert,
                generate_opportunity_alert, generate_comp_analysis,
                format_for_tts, get_stats
    """
    def __init__(self, max_words_per_briefing: int = 75) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._briefing_count = 0
        self._max_words = max_words_per_briefing
        self._custom_templates: Dict[str, str] = {}

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _get_template(self, key: str) -> str:
        return self._custom_templates.get(key, _TEMPLATES.get(key, ""))

    def _translate_tier(self, tier: str) -> str:
        return _TIER_CN.get(tier.upper(), tier)

    def set_template(self, key: str, template: str) -> Dict[str, Any]:
        """Override a briefing template."""
        self._op_count += 1
        self._custom_templates[key] = template
        return {"status": "ok", "key": key}

    def generate_pregame_briefing(self, briefing_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate full pregame voice briefing from aggregated intel."""
        self._op_count += 1
        self._briefing_count += 1
        segments = []
        threats = briefing_data.get("threats", [])
        opportunities = briefing_data.get("opportunities", [])
        # Summary opener
        summary = self._get_template("pregame_summary").format(
            threats=len(threats), opportunities=len(opportunities))
        segments.append(summary)
        # Top threats (max 2)
        for threat in threats[:2]:
            seg = self._generate_player_segment(threat, "threat")
            if seg:
                segments.append(seg)
        # Top opportunities (max 1)
        for opp in opportunities[:1]:
            seg = self._generate_player_segment(opp, "opportunity")
            if seg:
                segments.append(seg)
        full_text = " ".join(segments)
        # Trim to word budget
        words = full_text.split()
        if len(words) > self._max_words:
            full_text = " ".join(words[:self._max_words]) + "。"
        self._fire("briefing_generated", {"segments": len(segments),
                                           "words": len(full_text.split())})
        return {"status": "ok", "text": full_text, "segments": segments,
                "word_count": len(full_text.split()),
                "estimated_duration_seconds": round(len(full_text.split()) / 2.5, 1)}

    def _generate_player_segment(self, player: Dict[str, Any],
                                   category: str) -> str:
        """Generate a single player briefing segment."""
        name = player.get("game_name", player.get("summoner_name", "未知"))
        champion = player.get("champion_name", f"英雄{player.get('champion_id', '?')}")
        tier = self._translate_tier(player.get("tier", ""))
        winrate = player.get("winrate", 0)
        games = player.get("games", 0)
        role = player.get("role", "")
        if category == "threat":
            threat_score = player.get("threat_score", 0.5)
            if player.get("is_one_trick"):
                return self._get_template("threat_one_trick").format(
                    name=name, champion=champion, games=games, winrate=winrate)
            if tier:
                return self._get_template("threat_high_rank").format(
                    name=name, tier=tier, champion=champion, winrate=winrate)
        elif category == "opportunity":
            if winrate and winrate < 45:
                return self._get_template("opportunity_low_wr").format(
                    name=name, champion=champion, games=games, winrate=winrate)
        return self._get_template("neutral_info").format(
            name=name, champion=champion, tier=tier or "未知", winrate=winrate)

    def generate_threat_alert(self, player_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a single threat alert."""
        self._op_count += 1
        text = self._generate_player_segment(player_data, "threat")
        return {"status": "ok", "text": text, "priority": "high"}

    def generate_opportunity_alert(self, player_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a single opportunity alert."""
        self._op_count += 1
        text = self._generate_player_segment(player_data, "opportunity")
        return {"status": "ok", "text": text, "priority": "low"}

    def generate_comp_analysis(self, team_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate team composition analysis briefing."""
        self._op_count += 1
        archetype = team_data.get("archetype", "未知")
        key_point = team_data.get("key_point", "注意团战站位")
        text = self._get_template("team_comp").format(
            archetype=archetype, key_point=key_point)
        return {"status": "ok", "text": text}

    def format_for_tts(self, text: str) -> Dict[str, Any]:
        """Format text for TTS engine consumption (add pauses, emphasis)."""
        self._op_count += 1
        # Add SSML-like pauses after sentences
        formatted = text.replace("。", "。<break/>").replace("，", "，")
        formatted = formatted.rstrip("<break/>")
        return {"status": "ok", "original": text, "tts_formatted": formatted,
                "word_count": len(text.split()),
                "estimated_duration_seconds": round(len(text.split()) / 2.5, 1)}

    def get_stats(self) -> Dict[str, Any]:
        return {"briefing_count": self._briefing_count,
                "custom_templates": len(self._custom_templates),
                "total_ops": self._op_count}
