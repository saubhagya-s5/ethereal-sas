"""Resolve Gemini grounding redirect URLs to the real source URL."""
from __future__ import annotations

import asyncio
import re
import urllib.request


def _resolve(uri: str, title: str) -> str:
    if "vertexaisearch.cloud.google.com" not in uri:
        return uri
    try:
        req = urllib.request.Request(uri, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.geturl()
    except Exception:
        # The chunk title is usually the source domain (e.g. "fabhotels.com").
        if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", title.strip().lower()):
            return f"https://{title.strip().lower()}/"
        return uri


async def resolve_redirects(chunks: list[tuple[str, str]]) -> list[str]:
    return list(await asyncio.gather(*(asyncio.to_thread(_resolve, u, t) for u, t in chunks)))
