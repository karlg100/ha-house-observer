"""Persistent property knowledge: owner notes, confirmed patterns, continuity."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any

from .models import KnowledgeItem, ObserverSummary, parse_timestamp

MAX_KNOWLEDGE_ITEMS = 100
MAX_NOTE_LENGTH = 1000
PROMPT_KNOWLEDGE_ITEMS = 40
PREVIOUS_SUMMARIES = 3
CANDIDATE_MEMORY_SUMMARIES = 10
CANDIDATE_MEMORY_LIMIT = 15


def new_note(
    text: str,
    category: str,
    now: datetime,
    *,
    expires_in_days: float | None = None,
) -> KnowledgeItem:
    """Create a bounded knowledge item."""
    cleaned = " ".join(str(text).split())
    if len(cleaned) > MAX_NOTE_LENGTH:
        cleaned = f"{cleaned[: MAX_NOTE_LENGTH - 1]}…"
    expires = (
        (now + timedelta(days=float(expires_in_days))).isoformat()
        if expires_in_days
        else None
    )
    return KnowledgeItem(
        note_id=secrets.token_hex(4),
        created=now.isoformat(),
        category=category,
        text=cleaned,
        expires=expires,
    )


def prune_knowledge(items: list[KnowledgeItem], now: datetime) -> list[KnowledgeItem]:
    """Drop expired items and keep the newest bounded set."""
    kept = [
        item
        for item in items
        if (expires := parse_timestamp(item.expires)) is None or expires > now
    ]
    return kept[-MAX_KNOWLEDGE_ITEMS:]


def knowledge_context(items: list[KnowledgeItem]) -> list[dict[str, Any]]:
    """Return the newest knowledge items in a compact, model-readable form."""
    return [
        {"recorded": item.created[:10], "category": item.category, "note": item.text}
        for item in items[-PROMPT_KNOWLEDGE_ITEMS:]
    ]


def previous_summaries(
    summaries: list[ObserverSummary], limit: int = PREVIOUS_SUMMARIES
) -> list[dict[str, Any]]:
    """Return recent summary headlines so analysis has continuity."""
    output: list[dict[str, Any]] = []
    for summary in summaries[-limit:]:
        text = summary.summary
        if len(text) > 400:
            text = f"{text[:399]}…"
        output.append(
            {
                "generated": summary.timestamp,
                "reason": summary.reason,
                "severity": summary.severity,
                "summary": text,
            }
        )
    return output


def recurring_candidate_memories(
    summaries: list[ObserverSummary],
    *,
    exclude: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Aggregate unconfirmed pattern candidates across recent summaries."""
    excluded = {_normalize(text) for text in exclude or set()}
    counts: dict[str, tuple[str, int]] = {}
    for summary in summaries[-CANDIDATE_MEMORY_SUMMARIES:]:
        seen_here: set[str] = set()
        for raw in summary.candidate_memories.splitlines():
            text = raw.strip().lstrip("-•* ").strip()
            key = _normalize(text)
            if not key or key in excluded or key in seen_here:
                continue
            seen_here.add(key)
            _, count = counts.get(key, (text, 0))
            counts[key] = (text, count + 1)
    ranked = sorted(counts.values(), key=lambda pair: pair[1], reverse=True)
    return [
        {"pattern": text, "seen_in_summaries": count}
        for text, count in ranked[:CANDIDATE_MEMORY_LIMIT]
    ]


def _normalize(text: str) -> str:
    return " ".join(text.lower().split()).rstrip(".")
