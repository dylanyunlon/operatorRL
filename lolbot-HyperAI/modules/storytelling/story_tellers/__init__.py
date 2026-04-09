"""
storytelling/story_tellers — Apollo story_tellers/ parity.

Apollo reference:
    modules/storytelling/story_tellers/base_teller.h
    modules/storytelling/story_tellers/close_to_junction_teller.cc

Each teller is responsible for one category of game events,
producing narration segments when relevant events occur.
"""

from modules.storytelling.story_tellers.base_teller import BaseTeller
from modules.storytelling.story_tellers.teamfight_teller import TeamfightTeller
from modules.storytelling.story_tellers.objective_teller import ObjectiveTeller

__all__ = ["BaseTeller", "TeamfightTeller", "ObjectiveTeller"]
