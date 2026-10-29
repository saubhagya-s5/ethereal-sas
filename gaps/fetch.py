"""Polite HTTP: robots.txt check, fixed delay between requests, one shared client."""
import time
import urllib.robotparser
from urllib.parse import urlparse

import httpx

from gaps.config import REQUEST_DELAY_S, USER_AGENT

_client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True)
_robots = {}
_last = 0.0


def _allowed(url):
    host = urlparse(url)
    base = f"{host.scheme}://{host.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        r = _client.get(base + "/robots.txt")
        rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        _robots[base] = rp
    return _robots[base].can_fetch(USER_AGENT, url)


def get(url):
    """Return response text, or None if robots.txt disallows it or the request fails."""
    global _last
    if not _allowed(url):
        print(f"  robots.txt disallows {url}, skipping")
        return None
    wait = REQUEST_DELAY_S - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    try:
        r = _client.get(url)
    except httpx.HTTPError as e:
        print(f"  request failed {url}: {e}")
        return None
    if r.status_code != 200:
        print(f"  HTTP {r.status_code} {url}")
        return None
    return r.text
