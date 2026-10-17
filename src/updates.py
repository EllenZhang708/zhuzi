"""A quiet check for a newer release on GitHub."""
from __future__ import annotations

import json
import logging
import re

import core

log = logging.getLogger("zhuzi")

# The GitHub repository releases come from. Empty: never check.
REPO = "EllenZhang708/zhuzi"
RELEASES = f"https://github.com/{REPO}/releases/latest" if REPO else ""


def _version(tag: str) -> tuple[int, ...]:
    """(major, minor, patch, 1 for a release or 0 for a pre-release such as
    1.1.0-beta.1), so a beta's users still hear about the release it led to."""
    return tuple(int(n) for n in re.findall(r"\d+", tag)[:3]) + (0 if "-" in tag else 1,)


def newer_release() -> str | None:
    """The newer version's tag (like "v1.1.0"), or None if current or unknown."""
    if not REPO:
        return None
    try:
        data = json.loads(
            core.http_get(
                f"https://api.github.com/repos/{REPO}/releases/latest",
                timeout=8,
                attempts=1,
                headers={"Accept": "application/vnd.github+json"},
            )
        )
        tag = data.get("tag_name", "")
        return tag if _version(tag) > _version(core.APP_VERSION) else None
    except (OSError, ValueError):
        log.info("update check failed", exc_info=True)
        return None
