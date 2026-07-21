from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.users import AvatarFields


InsightsPeriodKey = Literal["all_time", "this_year"]
ConfidenceTier = Literal["empty", "basic", "emerging", "established"]


class InsightsPeriodOut(BaseModel):
    key: InsightsPeriodKey
    label: str
    starts_at: datetime | None = None
    ends_at: datetime


class InsightsAvailabilityOut(BaseModel):
    sample_size: int
    confidence_tier: ConfidenceTier
    personality_available: bool
    member_highlights_available: bool
    reason_unavailable: str | None = None
    next_tier_at: int | None = None


class InsightsActivityOut(BaseModel):
    completed_nights: int
    confirmed_watched_nights: int
    total_watch_minutes: int
    average_watched_runtime_minutes: int | None = None
    unique_winners: int
    unique_genres_explored: int


class InsightsRecordOut(BaseModel):
    key: str
    label: str
    value: str
    detail: str | None = None
    session_id: UUID | None = None


class InsightsDecisionOut(BaseModel):
    average_seconds: int | None = None
    median_seconds: int | None = None
    average_candidate_count: float | None = None
    unanimous_rate: float | None = None
    unanimous_sample_size: int = 0


class RankedInsightOut(BaseModel):
    key: str
    label: str
    count: int
    percentage: float


class InsightsTasteOut(BaseModel):
    genres: list[RankedInsightOut] = Field(default_factory=list)
    moods: list[RankedInsightOut] = Field(default_factory=list)
    runtime_bands: list[RankedInsightOut] = Field(default_factory=list)


class PersonalityDimensionOut(BaseModel):
    key: str
    label: str
    value: float
    interpretation: str


class GroupPersonalityOut(BaseModel):
    title: str
    description: str
    supporting_facts: list[str]
    dimensions: list[PersonalityDimensionOut]
    sample_size: int
    confidence_tier: ConfidenceTier


class MemberHighlightOut(AvatarFields):
    user_id: UUID
    display_name: str
    title: str
    explanation: str


class InsightsDataQualityOut(BaseModel):
    watched_runtimes_known: int
    watched_runtimes_missing: int
    decisions_timed: int
    unanimity_known: int
    sessions_with_vote_snapshots: int
    notes: list[str] = Field(default_factory=list)


class GroupInsightsOut(BaseModel):
    group_id: UUID
    group_name: str
    calculation_version: str
    period: InsightsPeriodOut
    availability: InsightsAvailabilityOut
    activity: InsightsActivityOut
    decision: InsightsDecisionOut
    taste: InsightsTasteOut
    records: list[InsightsRecordOut]
    personality: GroupPersonalityOut | None = None
    member_highlights: list[MemberHighlightOut] = Field(default_factory=list)
    data_quality: InsightsDataQualityOut
