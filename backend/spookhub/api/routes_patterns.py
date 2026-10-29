"""Alignment pattern catalogue; mounted next to `routes_display` under the same prefix."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from spookhub.show.patterns import PATTERNS, Kind

router = APIRouter(prefix="/api/display", tags=["display"])


class PatternOut(BaseModel):
    id: str
    label: str
    kind: Kind


@router.get("/patterns")
def patterns() -> list[PatternOut]:
    return [PatternOut(id=p.id, label=p.label, kind=p.kind) for p in PATTERNS]
