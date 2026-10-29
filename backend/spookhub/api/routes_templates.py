from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlmodel import col, select

from spookhub import db
from spookhub.models import CueTemplate, TemplateIn, TemplateOut

router = APIRouter(prefix="/api/templates", tags=["templates"])


def _out(t: CueTemplate) -> TemplateOut:
    return TemplateOut(id=t.id or 0, name=t.name, cues=t.cues, created_at=t.created_at)  # type: ignore[arg-type]


@router.get("")
def list_templates() -> list[TemplateOut]:
    with db.session() as s:
        rows = s.exec(select(CueTemplate).order_by(col(CueTemplate.id))).all()
        return [_out(t) for t in rows]


@router.post("")
def create_template(body: TemplateIn) -> TemplateOut:
    with db.session() as s:
        t = CueTemplate(name=body.name, cues=[c.model_dump(mode="json") for c in body.cues])
        s.add(t)
        s.commit()
        s.refresh(t)
        return _out(t)


@router.delete("/{tid}")
def delete_template(tid: int) -> dict[str, bool]:
    with db.session() as s:
        t = s.get(CueTemplate, tid)
        if t is None:
            raise HTTPException(404, "template not found")
        s.delete(t)
        s.commit()
    return {"ok": True}
