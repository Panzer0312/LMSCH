"""DisplayGeometry -> mpv runtime properties and warp shader parameters. Pure, no I/O."""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

from spookhub.models import CropRect, DisplayGeometry

WARP_SHADER = Path(__file__).resolve().parent / "shaders" / "warp.glsl"

# mpv's own defaults; identity geometry maps to exactly these.
DEFAULT_PROPS: dict[str, Any] = {
    "video-zoom": 0.0,
    "video-pan-x": 0.0,
    "video-pan-y": 0.0,
    "video-scale-x": 1.0,
    "video-scale-y": 1.0,
    "video-align-x": 0.0,
    "video-align-y": 0.0,
    "video-rotate": 0,
    "video-aspect-override": -1.0,
    "video-crop": "",
    "brightness": 0,
    "contrast": 0,
    "gamma": 0,
    "saturation": 0,
}
SHADER_PARAMS = (
    "c0x c0y c1x c1y c2x c2y c3x c3y mask_t mask_r mask_b mask_l feather flip_h flip_v".split()
)
DEFAULT_SHADER_OPTS: dict[str, str] = {k: "0" for k in SHADER_PARAMS}


class SourceFrame(NamedTuple):
    """Coded frame size as ffprobe reports it, plus the container rotation (cw degrees).

    mpv crops the coded frame and then rotates by container rotation + video-rotate."""

    w: int
    h: int
    rotate: int = 0


def _fmt(v: float) -> str:
    # Fixed precision keeps diffs stable against float noise from the UI sliders.
    s = f"{v:.5f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def crop_to_source(c: CropRect, rotate: int) -> tuple[float, float, float, float]:
    """Displayed-frame crop (what the user sees after rotation) -> source-frame fractions.

    mpv applies video-crop to the coded frame before any rotation (container rotation plus
    video-rotate, clockwise). Verified on mpv 0.41 / vo=gpu-next with a quadrant test clip,
    pinned by test_crop_matches_mpv_crop_then_rotate."""
    x, y, w, h = c.x, c.y, c.w, c.h
    match rotate:
        case 90:
            return y, 1 - (x + w), h, w
        case 180:
            return 1 - (x + w), 1 - (y + h), w, h
        case 270:
            return 1 - (y + h), x, h, w
        case _:
            return x, y, w, h


def crop_px(c: CropRect, rotate: int, src_w: int, src_h: int) -> str:
    """mpv video-crop string; "" = no crop (also when the source size is unknown)."""
    if (c.x, c.y, c.w, c.h) == (0.0, 0.0, 1.0, 1.0) or src_w <= 0 or src_h <= 0:
        return ""
    sx, sy, sw, sh = crop_to_source(c, rotate)
    x = max(0, min(src_w - 1, round(sx * src_w)))
    y = max(0, min(src_h - 1, round(sy * src_h)))
    w = max(1, min(src_w - x, round(sw * src_w)))
    h = max(1, min(src_h - y, round(sh * src_h)))
    if (x, y, w, h) == (0, 0, src_w, src_h):
        return ""
    return f"{w}x{h}+{x}+{y}"


def needs_warp(g: DisplayGeometry) -> bool:
    """True when the geometry uses shader-only features (keystone, mask, flip)."""
    m = g.mask
    return (
        any(v != 0 for c in g.corners for v in c)
        or any(v > 0 for v in (m.top, m.right, m.bottom, m.left))
        or g.flip_h
        or g.flip_v
    )


def to_mpv(
    g: DisplayGeometry, src_w: int, src_h: int, src_rotate: int = 0
) -> tuple[dict[str, Any], dict[str, str]]:
    """`src_w`/`src_h` are coded dims; `src_rotate` the file's own cw rotation (metadata)."""
    total_rotate = (src_rotate + int(g.rotate)) % 360
    props: dict[str, Any] = {
        "video-zoom": float(g.zoom),
        "video-pan-x": float(g.pan_x),
        "video-pan-y": float(g.pan_y),
        "video-scale-x": float(g.scale_x),
        "video-scale-y": float(g.scale_y),
        "video-align-x": float(g.align_x),
        "video-align-y": float(g.align_y),
        "video-rotate": int(g.rotate),
        "video-aspect-override": -1.0 if g.aspect is None else float(g.aspect),
        "video-crop": crop_px(g.crop, total_rotate, src_w, src_h),
        "brightness": int(g.brightness),
        "contrast": int(g.contrast),
        "gamma": int(g.gamma),
        "saturation": int(g.saturation),
    }
    m = g.mask
    vals = [v for corner in g.corners for v in corner] + [
        m.top,
        m.right,
        m.bottom,
        m.left,
        m.feather,
        1.0 if g.flip_h else 0.0,
        1.0 if g.flip_v else 0.0,
    ]
    shader = {k: _fmt(float(v)) for k, v in zip(SHADER_PARAMS, vals, strict=True)}
    return props, shader
