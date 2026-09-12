#!/usr/bin/env python3
"""Generate a 360x360 media artwork state file for Magic Dial AOD.

Dependencies:
  pip install pillow
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import struct
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image


ART_SIZE = 360
RGB565_BYTES = ART_SIZE * ART_SIZE * 2


def stable_color(seed: str) -> tuple[int, int, int]:
    digest = hashlib.sha256(seed.encode('utf-8')).digest()
    return (48 + digest[0] % 128, 48 + digest[1] % 128, 48 + digest[2] % 128)


def brighten(color: tuple[int, int, int], amount: int) -> tuple[int, int, int]:
    return tuple(min(255, c + amount) for c in color)


def search_itunes_artwork(title: str, subtitle: str) -> str:
    term = ' '.join([x for x in [title, subtitle] if is_non_empty(x)])
    if not term:
        return ''
    query = urllib.parse.urlencode({'term': term, 'media': 'music', 'entity': 'song', 'limit': 3})
    url = f'https://itunes.apple.com/search?{query}'
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
        for item in payload.get('results', []):
            artwork = item.get('artworkUrl100') or item.get('artworkUrl60')
            if isinstance(artwork, str) and artwork:
                return artwork.replace('100x100bb', '600x600bb').replace('60x60bb', '600x600bb')
    except Exception:
        return ''
    return ''


def generated_fallback_art(media: ActiveMedia) -> Image.Image:
    base = stable_color(media.art_signature or f'{media.kind}:{media.title}:{media.subtitle}')
    accent = brighten(base, 60)
    img = Image.new('RGB', (ART_SIZE, ART_SIZE), base)
    px = img.load()
    for y in range(ART_SIZE):
        ratio = y / float(max(1, ART_SIZE - 1))
        row = tuple(int(base[i] * (1 - ratio) + accent[i] * ratio) for i in range(3))
        for x in range(ART_SIZE):
            px[x, y] = row
    return img


def is_non_empty(value: Any) -> bool:
    return isinstance(value, str) and value.strip() not in {"", "unknown", "unavailable", "None"}


def first_non_empty(*values: Any) -> str:
    for value in values:
        if is_non_empty(value):
            return str(value).strip()
    return ""


def safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def join_non_empty(parts: list[str], sep: str = " · ") -> str:
    return sep.join([p for p in parts if is_non_empty(p)])


def slugify(text: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", text.strip(), flags=re.UNICODE)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "unknown"


def build_cache_key(kind: str, title: str, subtitle: str, *identity: str) -> str:
    base = "::".join((kind, title, subtitle, *identity))
    digest = hashlib.sha1(base.encode('utf-8')).hexdigest()[:12]
    stem = slugify(f"{kind}_{title}_{subtitle}")[:48]
    return f"{stem}_{digest}"


def cache_file_path(output_dir: Path, cache_key: str) -> Path:
    return output_dir / 'cache' / f"{cache_key}.rgb565"


@dataclass
class ActiveMedia:
    source_name: str
    kind: str
    title: str
    subtitle: str
    lyric_hint: str
    art_source: str
    has_progress: bool
    progress_pct: float
    art_signature: str
    cache_key: str


class HAClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self._auth = f"Bearer {token}" if token else ""

    def _request(self, url: str) -> bytes:
        headers = {"Authorization": self._auth} if self._auth else {}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()

    def fetch_state(self, entity_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/api/states/{entity_id}"
        return json.loads(self._request(url).decode("utf-8"))

    def fetch_bytes(self, source: str) -> bytes:
        if source.startswith("/"):
            url = self.base_url + source
        elif source.startswith("http://") or source.startswith("https://"):
            url = source
        else:
            raise ValueError(f"Unsupported art source: {source}")
        return self._request(url)


def extract_progress(payload: dict[str, Any]) -> tuple[bool, float]:
    position = safe_float(payload.get("media_position"))
    duration = safe_float(payload.get("media_duration"))
    if position is None or duration is None or duration <= 0:
        return False, 0.0
    pct = max(0.0, min(100.0, position / duration * 100.0))
    return True, round(pct, 1)


def homepod_candidate(payload: dict[str, Any]) -> ActiveMedia | None:
    if (payload.get("state") or "").strip() != "playing":
        return None
    title = first_non_empty(payload.get("media_title"))
    art_source = first_non_empty(payload.get("entity_picture_local"), payload.get("entity_picture"))
    if not title and not art_source:
        return None
    stable_secondary = first_non_empty(payload.get("media_album_name"), payload.get("app_name"))
    subtitle = stable_secondary or "HomePod"
    lyric_hint = first_non_empty(payload.get("media_artist"), payload.get("media_series_title"))
    has_progress, progress_pct = extract_progress(payload)
    # AirPlay may expose rolling lyrics as media_artist and rotates proxy
    # token/cache query parameters. Neither belongs in the immutable track key.
    sig = f"music::{title}::{subtitle}"
    cache_key = build_cache_key("music", title or "HomePod", subtitle)
    return ActiveMedia("homepod", "music", title or "HomePod", subtitle, lyric_hint, art_source, has_progress, progress_pct, sig, cache_key)


def ps4_candidate(payload: dict[str, Any], overrides: dict[str, str]) -> ActiveMedia | None:
    if (payload.get("state") or "").strip() != "playing":
        return None
    title = first_non_empty(payload.get("media_title"), payload.get("source"))
    art_source = first_non_empty(payload.get("entity_picture_local"), payload.get("entity_picture"))
    if not art_source:
        art_source = overrides.get(f"game:{title}", "")
    if not title and not art_source:
        return None
    sig = f"game::{title}::PlayStation 4::{art_source}"
    cache_key = build_cache_key("game", title or "PlayStation 4", "PlayStation 4", art_source)
    return ActiveMedia("ps4", "game", title or "PlayStation 4", "PlayStation 4", "", art_source, False, 0.0, sig, cache_key)


def sony_tv_candidate(payload: dict[str, Any], source_sensor: str, overrides: dict[str, str]) -> ActiveMedia | None:
    media_title = first_non_empty(payload.get("media_title"))
    app_name = first_non_empty(payload.get("app_name"))
    source = first_non_empty(payload.get("source"), source_sensor)
    art_source = first_non_empty(payload.get("entity_picture_local"), payload.get("entity_picture"))
    if media_title == "Smart TV" and not app_name and not source and not art_source:
        return None
    title = first_non_empty(media_title if media_title != "Smart TV" else "", app_name, source)
    if not title and not art_source:
        return None
    subtitle = first_non_empty(app_name, source, "Sony TV")
    if not art_source:
        art_source = overrides.get(f"tv_app:{app_name}", "") or overrides.get(f"tv_source:{source}", "")
    has_progress, progress_pct = extract_progress(payload)
    sig = f"tv::{title}::{subtitle}::{art_source}"
    cache_key = build_cache_key("tv", title or "Sony TV", subtitle, art_source)
    return ActiveMedia("tv", "tv", title or "Sony TV", subtitle, "", art_source, has_progress and bool(art_source), progress_pct, sig, cache_key)


def load_overrides(path: Path | None) -> dict[str, str]:
    if not path or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def resolve_local_override(path_value: str, output_dir: Path) -> Path:
    if path_value.startswith("/local/"):
        return output_dir.parent / path_value[len("/local/"):]
    return Path(path_value)


def square_cover_from_bytes(blob: bytes) -> Image.Image:
    from io import BytesIO
    with Image.open(BytesIO(blob)) as img:
        return square_cover(img)


def square_cover(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    width, height = img.size
    if width <= 0 or height <= 0:
        raise ValueError("invalid image size")
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    img = img.crop((left, top, left + side, top + side))
    if img.size != (ART_SIZE, ART_SIZE):
        img = img.resize((ART_SIZE, ART_SIZE), Image.LANCZOS)
    return img


def encode_rgb565_le(image: Image.Image) -> bytes:
    """Encode an exact 360x360 RGB image as little-endian RGB565 pixels."""
    rgb = square_cover(image).tobytes()
    encoded = bytearray(RGB565_BYTES)
    for source, target in zip(range(0, len(rgb), 3), range(0, RGB565_BYTES, 2)):
        red, green, blue = rgb[source:source + 3]
        pixel = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
        struct.pack_into("<H", encoded, target, pixel)
    return bytes(encoded)


def is_valid_cached_art(path: Path) -> bool:
    return path.is_file() and path.stat().st_size == RGB565_BYTES


def write_rgb565_atomic(image: Image.Image, path: Path) -> None:
    tmp_fd, tmp_name = tempfile.mkstemp(prefix="magic_dial_cache_", suffix=".rgb565", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(tmp_fd, "wb") as output:
            output.write(encode_rgb565_le(image))
            output.flush()
            os.fsync(output.fileno())
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def discover_openai_config_entry(output_dir: Path) -> str:
    store_path = output_dir.parent.parent / '.storage' / 'core.config_entries'
    try:
        payload = json.loads(store_path.read_text(encoding='utf-8'))
    except Exception:
        return ''
    for entry in payload.get('data', {}).get('entries', []):
        if entry.get('domain') == 'openai_conversation':
            return str(entry.get('entry_id') or '')
    return ''


def build_music_ai_prompt(media: ActiveMedia) -> str:
    lyric = media.lyric_hint.strip() if is_non_empty(media.lyric_hint) else ''
    prompt = [
        'Create a stylized square album cover inspired by the song.',
        f'Title: {media.title}.',
    ]
    if is_non_empty(media.subtitle) and media.subtitle != 'HomePod':
        prompt.append(f'Context: {media.subtitle}.')
    if lyric:
        prompt.append(f'Lyric mood reference: {lyric}.')
    prompt.extend([
        'Focus on the emotional atmosphere and cinematic symbolism suggested by the title and lyric reference.',
        'Premium, artistic, evocative, album-cover composition.',
        'Stylized artistic typography is allowed if it feels naturally integrated into the artwork.',
        'Do not add explanatory text, metadata blocks, subtitles, lyric captions, logos, UI, borders, stickers, or watermarks.',
        'Avoid literal poster layouts or information-card compositions.'
    ])
    return ' '.join(prompt)


def generate_ai_music_art(media: ActiveMedia, client: HAClient, output_dir: Path) -> Image.Image | None:
    entry_id = discover_openai_config_entry(output_dir)
    if not entry_id:
        return None
    payload = {
        'config_entry': entry_id,
        'prompt': build_music_ai_prompt(media),
        'size': '1024x1024',
        'quality': 'standard',
        'style': 'vivid',
    }
    body = json.dumps(payload).encode('utf-8')
    url = f"{client.base_url}/api/services/openai_conversation/generate_image?return_response"
    headers = {
        'Authorization': client._auth,
        'Content-Type': 'application/json',
    } if client._auth else {'Content-Type': 'application/json'}
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            response_payload = json.loads(resp.read().decode('utf-8'))
        service_response = response_payload.get('service_response') or {}
        image_url = service_response.get('url') or service_response.get('image_url')
        if not is_non_empty(image_url):
            return None
        blob = client.fetch_bytes(str(image_url))
        return square_cover_from_bytes(blob)
    except Exception:
        return None


def materialize_art(media: ActiveMedia, client: HAClient, output_dir: Path, previous: dict[str, Any]) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = output_dir / 'cache'
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_file_path(output_dir, media.cache_key)
    previous_sig = str(previous.get("art_signature") or "")

    if media.art_signature == previous_sig and is_valid_cached_art(cache_path):
        return build_public_url(output_dir, cache_path)

    if is_valid_cached_art(cache_path):
        return build_public_url(output_dir, cache_path)

    image = None
    sources_to_try = []
    if is_non_empty(media.art_source):
        sources_to_try.append(media.art_source)
    if media.kind == 'music':
        itunes_art = search_itunes_artwork(media.title, media.subtitle)
        if is_non_empty(itunes_art) and itunes_art not in sources_to_try:
            sources_to_try.append(itunes_art)

    for source in sources_to_try:
        try:
            if source.startswith("http://") or source.startswith("https://") or source.startswith("/"):
                blob = client.fetch_bytes(source)
                image = square_cover_from_bytes(blob)
            else:
                source_path = resolve_local_override(source, output_dir)
                if not source_path.exists():
                    raise FileNotFoundError(source_path)
                with Image.open(source_path) as img:
                    image = square_cover(img)
            media.art_source = source
            break
        except Exception:
            pass

    if image is None and media.kind == 'music':
        image = generate_ai_music_art(media, client, output_dir)

    if image is None:
        image = square_cover(generated_fallback_art(media))

    write_rgb565_atomic(image, cache_path)
    return build_public_url(output_dir, cache_path)


def build_public_url(output_dir: Path, cache_path: Path) -> str:
    filename = urllib.parse.quote(cache_path.name)
    return f"__EXTERNAL_BASE__/local/{output_dir.name}/cache/{filename}"


def empty_result() -> dict[str, Any]:
    return {
        "updated_at": int(time.time()),
        "active_kind": "",
        "has_art": False,
        "artwork_url": "",
        "title": "",
        "subtitle": "",
        "has_progress": False,
        "progress_pct": -1,
        "debug_source": "",
        "art_signature": "",
        "cache_key": "",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ha-url", required=True)
    parser.add_argument("--external-base-url", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--overrides")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "current.json"
    previous = {}
    if state_path.exists():
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            previous = {}

    try:
        token = Path(args.token_file).read_text(encoding="utf-8").strip()
        client = HAClient(args.ha_url, token)
        overrides = load_overrides(Path(args.overrides) if args.overrides else None)

        homepod_state = client.fetch_state("media_player.ke_ting_ke_ting")
        ps4_state = client.fetch_state("media_player.ps4_183_playstation_4")
        tv_state = client.fetch_state("media_player.sony_k_55xr50")
        tv_source_state = client.fetch_state("sensor.sony_tv_source")

        homepod = {**homepod_state.get("attributes", {}), "state": homepod_state.get("state")}
        ps4 = {**ps4_state.get("attributes", {}), "state": ps4_state.get("state")}
        tv = {**tv_state.get("attributes", {}), "state": tv_state.get("state")}
        tv_source = str(tv_source_state.get("state") or "")

        media = homepod_candidate(homepod) or ps4_candidate(ps4, overrides) or sony_tv_candidate(tv, tv_source, overrides)
        if media is None:
            result = empty_result()
        else:
            artwork_url = materialize_art(media, client, output_dir, previous).replace("__EXTERNAL_BASE__", args.external_base_url.rstrip("/"))
            result = {
                "updated_at": int(time.time()),
                "active_kind": media.kind,
                "has_art": bool(artwork_url),
                "artwork_url": artwork_url,
                "title": media.title,
                "subtitle": media.subtitle,
                "has_progress": media.has_progress and bool(artwork_url),
                "progress_pct": media.progress_pct if media.has_progress and bool(artwork_url) else -1,
                "debug_source": media.source_name,
                "art_signature": media.art_signature,
                "cache_key": media.cache_key,
            }
    except Exception as exc:
        result = empty_result()
        result["debug_source"] = f"error:{type(exc).__name__}"

    tmp_state = state_path.with_suffix(".json.tmp")
    tmp_state.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_state.replace(state_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
