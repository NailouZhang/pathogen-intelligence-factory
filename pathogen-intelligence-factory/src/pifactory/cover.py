from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import random
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from .config import Settings, load_seed
from .utils import dump_json, load_json, sha256_text, utc_now_iso

STYLE_VERSION = "cover-v1-navy-green-red"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _profile_fingerprint(settings: Settings, profile: dict[str, Any]) -> str:
    seed = load_seed(settings.project_root, settings.profile_id)
    override = settings.project_root / "profiles" / settings.profile_id / "cover_override.jpg"
    payload = {
        "style": STYLE_VERSION,
        "profile_id": settings.profile_id,
        "seed": seed,
        "profile": {
            "display_name_en": profile.get("display_name_en"),
            "display_name_zh": profile.get("display_name_zh"),
            "taxonomy": profile.get("taxonomy"),
            "virus_names": profile.get("virus_names"),
            "genes_proteins": profile.get("genes_proteins"),
            "hosts": profile.get("hosts"),
            "disease_names_en": profile.get("disease_names_en"),
        },
        "override_sha256": _file_sha256(override) if override.is_file() else None,
        "image_mode": settings.cover_image_mode,
        "image_model": settings.cover_image_model,
    }
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _deterministic_pathogen_art(profile: dict[str, Any], size: tuple[int, int]) -> Image.Image:
    width, height = size
    seed_text = json.dumps(profile, ensure_ascii=False, sort_keys=True)
    rng = random.Random(int(hashlib.sha256(seed_text.encode()).hexdigest()[:16], 16))
    canvas = Image.new("RGB", size, (22, 43, 61))
    draw = ImageDraw.Draw(canvas, "RGBA")
    # soft layered background
    for index in range(22):
        x = rng.randint(-120, width + 80)
        y = rng.randint(-120, height + 80)
        r = rng.randint(60, 190)
        color = rng.choice([(39, 174, 96, 28), (197, 48, 48, 22), (44, 62, 80, 35)])
        draw.ellipse((x-r, y-r, x+r, y+r), fill=color)
    # stylized scientific viral particles; deliberately non-diagnostic
    for index in range(12):
        cx = rng.randint(width // 2, width - 30)
        cy = rng.randint(20, height - 20)
        radius = rng.randint(24, 72)
        fill = rng.choice([(39, 174, 96, 125), (210, 70, 70, 105), (120, 170, 190, 100)])
        draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), fill=fill, outline=(255,255,255,80), width=2)
        spikes = rng.randint(10, 22)
        for s in range(spikes):
            angle = (2 * math.pi * s / spikes) + rng.random() * 0.12
            inner = radius * 0.86
            outer = radius * 1.22
            x1, y1 = cx + math.cos(angle)*inner, cy + math.sin(angle)*inner
            x2, y2 = cx + math.cos(angle)*outer, cy + math.sin(angle)*outer
            draw.line((x1,y1,x2,y2), fill=(225,240,245,100), width=2)
            draw.ellipse((x2-3,y2-3,x2+3,y2+3), fill=(225,240,245,115))
    return canvas.filter(ImageFilter.GaussianBlur(0.4))


def _gemini_art(settings: Settings, profile: dict[str, Any], output: Path) -> tuple[bool, str]:
    key = settings.secrets.get("GEMINI_API_KEY", "")
    if not key:
        return False, "missing_gemini_key"
    try:
        from google import genai
    except Exception:
        return False, "google_genai_not_installed"

    names = ", ".join((profile.get("virus_names") or profile.get("english_terms") or [])[:8])
    taxonomy = json.dumps(profile.get("taxonomy") or {}, ensure_ascii=False)
    prompt = f"""Create a scientifically restrained editorial illustration for a pathogen intelligence report.
Pathogen: {profile.get('display_name_en') or settings.profile_id}.
Supported names: {names}.
Taxonomy context: {taxonomy}.
Style: modern biomedical microscopy-inspired illustration, dark navy background, emerald green and restrained crimson accents, subtle depth, clean negative space on the left for later typography, no text, no labels, no logos, no watermark-like lettering, no human patient, no gore. Do not claim exact virion morphology when uncertain; use an abstract scientifically plausible pathogen motif. Landscape 16:9."""
    try:
        client = genai.Client(api_key=key)
        interaction = client.interactions.create(
            model=settings.cover_image_model,
            input=prompt,
            response_format={"type": "image", "aspect_ratio": "16:9", "image_size": "1K"},
        )
        data = base64.b64decode(interaction.output_image.data)
        image = Image.open(io.BytesIO(data)).convert("RGB")
        image.save(output, "JPEG", quality=92)
        return True, f"gemini:{settings.cover_image_model}"
    except Exception as exc:
        return False, f"gemini_failed:{type(exc).__name__}"


def _compose_cover(background: Image.Image, profile: dict[str, Any], issue_date: str) -> Image.Image:
    target = (900, 383)
    bg = background.convert("RGB")
    ratio = max(target[0] / bg.width, target[1] / bg.height)
    resized = bg.resize((int(bg.width * ratio), int(bg.height * ratio)), Image.Resampling.LANCZOS)
    left = max(0, (resized.width - target[0]) // 2)
    top = max(0, (resized.height - target[1]) // 2)
    image = resized.crop((left, top, left + target[0], top + target[1]))
    image = ImageEnhance.Contrast(image).enhance(1.06)

    overlay = Image.new("RGBA", target, (0,0,0,0))
    draw = ImageDraw.Draw(overlay)
    for x in range(560):
        alpha = int(225 * (1 - x / 620))
        draw.line((x, 0, x, target[1]), fill=(17, 34, 49, max(20, alpha)))
    draw.rectangle((0, 0, 14, target[1]), fill=(39, 174, 96, 255))
    draw.rectangle((14, 0, 22, target[1]), fill=(197, 48, 48, 220))
    image = Image.alpha_composite(image.convert("RGBA"), overlay)

    draw = ImageDraw.Draw(image)
    zh = str(profile.get("display_name_zh") or profile.get("display_name_en") or profile.get("profile_id"))
    en = str(profile.get("display_name_en") or profile.get("profile_id"))
    draw.text((58, 54), "全球病原每日情报", font=_font(25, True), fill=(245,248,250,255))
    draw.text((58, 108), zh[:20], font=_font(52, True), fill=(255,255,255,255))
    draw.text((60, 183), en[:42], font=_font(25, False), fill=(201,222,231,255))
    draw.line((60, 235, 418, 235), fill=(39,174,96,255), width=4)
    draw.text((60, 258), "文献 · 疫情 · 公共卫生 · 证据审计", font=_font(21, False), fill=(244,226,196,255))
    draw.text((60, 320), issue_date, font=_font(19, False), fill=(205,216,222,255))
    return image.convert("RGB")


def ensure_profile_cover(settings: Settings, profile: dict[str, Any], issue_date: str) -> dict[str, Any]:
    profile_dir = settings.output_dir / "data" / "profiles" / settings.profile_id
    profile_dir.mkdir(parents=True, exist_ok=True)
    state_file = profile_dir / "cover_meta.json"
    persistent_cover = profile_dir / "cover.jpg"
    fingerprint = _profile_fingerprint(settings, profile)
    previous = load_json(state_file, default={}) or {}
    override = settings.project_root / "profiles" / settings.profile_id / "cover_override.jpg"

    changed = previous.get("profile_fingerprint") != fingerprint or not persistent_cover.is_file()
    generator = previous.get("generator") or "cached"
    if changed:
        if override.is_file():
            background = Image.open(override).convert("RGB")
            generator = "profile_override"
        else:
            raw = profile_dir / "cover_raw.jpg"
            used_gemini = False
            reason = ""
            if settings.cover_image_mode in {"auto", "gemini"}:
                used_gemini, reason = _gemini_art(settings, profile, raw)
            if used_gemini:
                background = Image.open(raw).convert("RGB")
                generator = reason
            else:
                background = _deterministic_pathogen_art(profile, (1200, 675))
                generator = f"deterministic:{reason or settings.cover_image_mode}"
        final = _compose_cover(background, profile, issue_date)
        final.save(persistent_cover, "JPEG", quality=93, optimize=True)
        dump_json(
            state_file,
            {
                "schema_version": 1,
                "profile_id": settings.profile_id,
                "profile_fingerprint": fingerprint,
                "cover_sha256": _file_sha256(persistent_cover),
                "generator": generator,
                "generated_at": utc_now_iso(),
                "style_version": STYLE_VERSION,
            },
        )

    site_assets = settings.output_dir / "site" / "assets"
    package_dir = settings.output_dir / "wechat-package"
    site_assets.mkdir(parents=True, exist_ok=True)
    package_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(persistent_cover, site_assets / "cover.jpg")
    shutil.copy2(persistent_cover, package_dir / "cover.jpg")
    meta = load_json(state_file, default={}) or {}
    meta["cover_file"] = "cover.jpg"
    meta["cover_sha256"] = _file_sha256(persistent_cover)
    meta["changed_this_run"] = changed
    return meta
