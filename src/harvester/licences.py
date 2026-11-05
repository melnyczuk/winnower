"""Normalise licence URLs/names and decide allow / flag / deny.

Not legal advice. The rules encode the mechanical consequences for sampling:
ND (no derivatives) is denied because sampling is a derivative; anything without a
recognisable licence is denied; NC and SA are kept but flagged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .config import LicencePolicy

Decision = Literal["allow", "flag", "deny"]

_CC = re.compile(
    r"creativecommons\.org/(?P<kind>licenses|publicdomain)/(?P<code>[a-z+\-]+)/(?P<ver>[\d.]+)?",
    re.I,
)


@dataclass(frozen=True)
class LicenceInfo:
    id: str | None
    url: str | None
    decision: Decision
    reason: str


def normalise(raw: str | None) -> str | None:
    """Map a licence URL or Freesound-style name to an SPDX-like id."""
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
        if code == "sampling+":
            return "Sampling+"
        return f"CC-{code.upper()}-{ver or '4.0'}"
    low = s.lower()
    names = {
        "creative commons 0": "CC0-1.0",
        "cc0": "CC0-1.0",
        "attribution": "CC-BY-4.0",
        "attribution 3.0": "CC-BY-3.0",
        "attribution 4.0": "CC-BY-4.0",
        "attribution noncommercial": "CC-BY-NC-4.0",
        "attribution noncommercial 3.0": "CC-BY-NC-3.0",
        "attribution noncommercial 4.0": "CC-BY-NC-4.0",
        "sampling+": "Sampling+",
        "public domain": "PDM",
    }
    return names.get(low)


def decide(raw: str | None, policy: LicencePolicy) -> LicenceInfo:
    lid = normalise(raw)
    url = raw if raw and raw.startswith("http") else None
    if lid is None:
        return LicenceInfo(None, url, "deny", "no recognisable licence")
    if "-ND" in lid:
        return LicenceInfo(lid, url, "deny", "no-derivatives licence forbids sampling")
    if lid in policy.allow:
        return LicenceInfo(lid, url, "allow", "allowed by policy")
    if lid in policy.flag:
        return LicenceInfo(lid, url, "flag", "kept but flagged (check before release)")
    return LicenceInfo(lid, url, "deny", "not in allow or flag list")


def attribution(title: str | None, author: str | None, url: str | None, lid: str | None) -> str:
    parts = [f'"{title}"' if title else "Untitled"]
    if author:
        parts.append(f"by {author}")
    if url:
        parts.append(f"({url})")
    if lid and not lid.startswith("local"):
        parts.append(f"licensed {lid}")
    return " ".join(parts)
