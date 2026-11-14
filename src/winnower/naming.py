"""Folders and file names that say what a sample is: key, tempo, length and a description.

Samples are grouped by Camelot key, so neighbouring folders mix harmonically::

    samples/8A A minor/Am_96bpm_4bar_10.0s_pulsing-dark-samba-de-fato-1932_29424a.flac
    samples/no key/nokey_108bpm_4bar_8.9s_crackling-airy-rain-on-tin-roof_8f31c0.flac
    samples/no key/nokey_120bpm_2bar_4.0s_rising-gesture-bowed-metal_0b7c2e.flac

The ``.json`` sidecar sits next to its FLAC with the same name.
"""

from __future__ import annotations

import re
import unicodedata

NO_KEY = "no key"
TITLE_WORDS = 4


def folder(key: dict | None) -> str:
    return f"{key['camelot']} {key['name']}" if key else NO_KEY


def slug(text: str | None, words: int = TITLE_WORDS) -> str:
    """Lowercase ASCII words joined by hyphens: safe in any filesystem or bucket."""
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return "-".join(re.findall(r"[a-z0-9]+", ascii_text.lower())[:words])


def adjectives(sidecar: dict, limit: int = 2) -> list[str]:
    """A couple of plain words for how the sample sounds, most telling first."""
    desc, scores = sidecar.get("descriptors") or {}, sidecar.get("scores") or {}
    parts = scores.get("interest_parts") or {}
    region, key = sidecar["region"], sidecar.get("key")
    centroid = desc.get("centroid_hz") or {}
    brightness = centroid.get("mean", 1500.0)
    flatness = (desc.get("flatness") or {}).get("mean", 0.1)
    events, modulation = parts.get("micro_events") or 0, parts.get("modulation") or 0
    gesture = region["type"] == "gesture"
    pulsed = region.get("tempo_source") == "detected" or (scores.get("rhythmicity") or 0) >= 0.5
    rules = [
        ("rising", gesture and centroid.get("slope", 0) > 0),
        ("falling", gesture and centroid.get("slope", 0) < 0),
        ("pulsing", pulsed),
        ("crackling", not pulsed and events >= 0.9 and flatness > 0.05),
        ("drifting", not pulsed and modulation >= 0.6 and events < 0.5),
        ("tonal", bool(key) and key.get("confidence", 0) >= 0.8),
        ("dark", brightness < 700),
        ("bright", brightness > 3500),
        ("airy", flatness > 0.25),
    ]
    return [word for word, applies in rules if applies][:limit]


def describe(sidecar: dict) -> str:
    words = adjectives(sidecar)
    if sidecar["region"]["type"] != "loop":
        words.append(sidecar["region"]["type"])
    title = slug((sidecar.get("source") or {}).get("title"))
    return "-".join(words + ([title] if title else []))


def file_name(sidecar: dict) -> str:
    """``Am_96bpm_4bar_10.0s_<description>_<id6>`` (no extension)."""
    key = sidecar.get("key")
    region = sidecar["region"]
    return "_".join(
        [
            key["short"] if key else "nokey",
            f"{region['bpm']}bpm",
            f"{region['bars']}bar",
            f"{sidecar['audio']['duration_s']:.1f}s",
            describe(sidecar),
            sidecar["id"][:6],
        ]
    )


def sample_path(sidecar: dict, ext: str) -> str:
    return f"samples/{folder(sidecar.get('key'))}/{file_name(sidecar)}.{ext}"
