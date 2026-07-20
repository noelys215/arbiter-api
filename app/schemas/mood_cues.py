from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


MoodCueCategory = Literal["energy", "effect", "occasion", "taste"]


class MoodCueOut(BaseModel):
    id: str
    label: str
    description: str
    category: MoodCueCategory
    display_order: int


MOOD_CUES: tuple[MoodCueOut, ...] = (
    MoodCueOut(id="easygoing", label="Easygoing", description="Light, warm, and easy to settle into.", category="energy", display_order=10),
    MoodCueOut(id="high-energy", label="High energy", description="Fast-moving and full of momentum.", category="energy", display_order=20),
    MoodCueOut(id="slow-burn", label="Slow burn", description="Patient, atmospheric, and rewarding.", category="energy", display_order=30),
    MoodCueOut(id="edge-of-our-seats", label="Edge of our seats", description="Tense enough to hold the room.", category="energy", display_order=40),
    MoodCueOut(id="make-us-laugh", label="Make us laugh", description="Something genuinely funny together.", category="effect", display_order=50),
    MoodCueOut(id="make-us-cry", label="Make us cry", description="Emotional, heartfelt, and affecting.", category="effect", display_order=60),
    MoodCueOut(id="comfort-watch", label="Comfort watch", description="Familiar-feeling and reassuring.", category="effect", display_order=70),
    MoodCueOut(id="something-hopeful", label="Something hopeful", description="Leave the night feeling lighter.", category="effect", display_order=80),
    MoodCueOut(id="something-unsettling", label="Something unsettling", description="Strange, dark, or quietly unnerving.", category="effect", display_order=90),
    MoodCueOut(id="date-night", label="Date night", description="Romantic without taking over the evening.", category="occasion", display_order=100),
    MoodCueOut(id="rainy-evening", label="Rainy evening", description="Immersive company for staying in.", category="occasion", display_order=110),
    MoodCueOut(id="late-night-watch", label="Late-night watch", description="A compelling choice for after dark.", category="occasion", display_order=120),
    MoodCueOut(id="friends-over", label="Friends over", description="Easy for a room to enjoy together.", category="occasion", display_order=130),
    MoodCueOut(id="mind-bending", label="Mind-bending", description="Clever, strange, and worth discussing.", category="taste", display_order=140),
    MoodCueOut(id="nostalgic", label="Nostalgic", description="A little familiar and transportive.", category="taste", display_order=150),
    MoodCueOut(id="epic-night", label="Epic night", description="A sweeping choice for making an occasion of it.", category="taste", display_order=160),
)

MOOD_CUE_IDS = frozenset(cue.id for cue in MOOD_CUES)

# Existing title-taxonomy keys used by the current deterministic matcher.
MOOD_CUE_MATCH_KEYS: dict[str, str] = {
    "easygoing": "cozy",
    "high-energy": "high energy",
    "slow-burn": "slow burn",
    "edge-of-our-seats": "thrilling",
    "make-us-laugh": "comedy",
    "make-us-cry": "heartfelt",
    "comfort-watch": "cozy",
    "something-hopeful": "feel-good",
    "something-unsettling": "scary",
    "date-night": "romantic",
    "rainy-evening": "cozy",
    "late-night-watch": "thrilling",
    "friends-over": "comedy",
    "mind-bending": "mind-bender",
    "nostalgic": "nostalgic",
    "epic-night": "epic",
}
