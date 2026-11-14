"""Normalise licence URLs/names and decide allow / flag / deny.

Not legal advice. The rules encode the mechanical consequences for sampling:
ND (no derivatives) is denied because sampling is a derivative; anything without a
recognisable licence is denied (unless a source opts in to keeping it flagged); NC and SA
are kept but flagged.

Policy entries without a version (``CC-BY``) match every version of that licence;
versioned entries (``CC-BY-4.0``) match only that version.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .config import LicencePolicy

Decision = Literal["allow", "flag", "deny"]

_CC = re.compile(
    r"creativecommons\.org/(?P<kind>licenses|publicdomain)/(?P<code>[a-z+\-]+)/?(?P<ver>[\d.]+)?",
    re.I,
)
_CC_SHORT = re.compile(r"^cc[\s-]?(by(?:[\s-](?:nc|sa|nd))*)(?:[\s-]+v?([\d.]+))?", re.I)
_CC0 = re.compile(r"^(cc0|cc[\s-]zero)(?:[\s-]+v?1\.0)?$", re.I)
_VERSION = re.compile(r"-\d[\d.]*$")

_NAMES = {
    "creative commons 0": "CC0-1.0",
    "attribution": "CC-BY-4.0",
    "attribution 3.0": "CC-BY-3.0",
    "attribution 4.0": "CC-BY-4.0",
    "attribution noncommercial": "CC-BY-NC-4.0",
    "attribution noncommercial 3.0": "CC-BY-NC-3.0",
    "attribution noncommercial 4.0": "CC-BY-NC-4.0",
    "sampling+": "Sampling+",
    "nc-sampling+": "NC-Sampling+",
    "noncommercial sampling+": "NC-Sampling+",
    "public domain": "PDM",
    "public domain mark": "PDM",
    "pd": "PDM",
    "pdm": "PDM",
    "nasa-media": "NASA-Media",
}

# Reasons shown for specific ids instead of the generic allow/flag text.
_REASONS = {
    "NASA-Media": "US government work, usually public domain; check for third-party content",
}


@dataclass(frozen=True)
class LicenceInfo:
    id: str | None
    url: str | None
    decision: Decision
    reason: str


def _cc_code(code: str) -> str | None:
    if "sampling+" in code:
        return "NC-Sampling+" if code.startswith("nc") else "Sampling+"
    return None


def normalise(raw: str | None) -> str | None:
    """Map a licence URL or short name (``CC BY-SA 4.0``, ``cc0``) to an SPDX-like id."""
    if not raw:
        return None
    s = raw.strip()
    m = _CC.search(s)
    if m:
        kind, code, ver = m.group("kind").lower(), m.group("code").lower(), m.group("ver")
        if kind == "publicdomain":
            if code == "zero":
                return "CC0-1.0"
            if code == "mark":
                return "PDM"
            return None
        if code == "publicdomain":  # the retired /licenses/publicdomain/ dedication
            return "PDM"
        return _cc_code(code) or f"CC-{code.upper()}-{ver or '4.0'}"
    low = re.sub(r"\s+", " ", s.lower())
    if low in _NAMES:
        return _NAMES[low]
    if _CC0.match(low):
        return "CC0-1.0"
    m = _CC_SHORT.match(low)
    if m:
        code = re.sub(r"[\s-]+", "-", m.group(1)).upper()
        return f"CC-{code}-{m.group(2) or '4.0'}"
    return None


def _matches(lid: str, entries: tuple[str, ...]) -> bool:
    return lid in entries or _VERSION.sub("", lid) in entries


def decide(raw: str | None, policy: LicencePolicy) -> LicenceInfo:
    lid = normalise(raw)
    url = raw if raw and raw.startswith("http") else None
    if lid is None:
        return LicenceInfo(None, url, "deny", "no recognisable licence")
    if "-ND" in lid:
        return LicenceInfo(lid, url, "deny", "no-derivatives licence forbids sampling")
    if _matches(lid, policy.allow):
        return LicenceInfo(lid, url, "allow", _REASONS.get(lid, "allowed by policy"))
    if _matches(lid, policy.flag):
        reason = _REASONS.get(lid, "kept but flagged (check before release)")
        return LicenceInfo(lid, url, "flag", reason)
    return LicenceInfo(lid, url, "deny", "not in allow or flag list")


def attribution(title: str | None, author: str | None, url: str | None, lid: str | None) -> str:
    parts = [f'"{title}"' if title else "Untitled"]
    if author:
        parts.append(f"by {author}")
    if url:
        parts.append(f"({url})")
    if lid and not lid.startswith("local") and not lid.endswith("-unknown"):
        parts.append(f"licensed {lid}")
    return " ".join(parts)
