from __future__ import annotations

from fastapi import Request

from spookhub.hub import Hub


def get_hub(request: Request) -> Hub:
    return request.app.state.hub
