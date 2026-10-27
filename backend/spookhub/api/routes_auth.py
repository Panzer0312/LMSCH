from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from spookhub.api.deps import get_hub
from spookhub.auth import COOKIE
from spookhub.hub import Hub

router = APIRouter(prefix="/api/auth", tags=["auth"])
HubDep = Annotated[Hub, Depends(get_hub)]
YEAR = 365 * 24 * 3600


class PinIn(BaseModel):
    pin: str = Field(min_length=1, max_length=64)


class SetPinIn(BaseModel):
    pin: str | None = Field(default=None, max_length=64)


class TokenIn(BaseModel):
    token: str | None = Field(default=None, max_length=128)


@router.get("/status")
def status(hub: HubDep, response: Response) -> dict[str, Any]:
    return {"pin_enabled": hub.auth.pin_enabled}


@router.post("/login")
async def login(body: PinIn, hub: HubDep, response: Response) -> dict[str, bool]:
    if not hub.auth.check_pin(body.pin):
        await asyncio.sleep(1.0)  # slow down guessing
        raise HTTPException(401, "wrong PIN")
    response.set_cookie(COOKIE, hub.auth.session_token(), max_age=YEAR, httponly=True,
                        samesite="lax")  # fmt: skip
    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.put("/pin")
def set_pin(body: SetPinIn, hub: HubDep, response: Response) -> dict[str, bool]:
    """Set or clear the UI PIN (requires a valid session when a PIN is already set)."""
    hub.auth.set_pin(body.pin or None)
    if hub.auth.pin_enabled:
        # Keep the person who just set it logged in.
        response.set_cookie(COOKIE, hub.auth.session_token(), max_age=YEAR, httponly=True,
                            samesite="lax")  # fmt: skip
    return {"pin_enabled": hub.auth.pin_enabled}


@router.get("/trigger_token")
def get_trigger_token(hub: HubDep) -> dict[str, str | None]:
    return {"token": hub.auth.trigger_token}


@router.put("/trigger_token")
def set_trigger_token(body: TokenIn, hub: HubDep) -> dict[str, str | None]:
    hub.auth.set_trigger_token((body.token or "").strip() or None)
    return {"token": hub.auth.trigger_token}
