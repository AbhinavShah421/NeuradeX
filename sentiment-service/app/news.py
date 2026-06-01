"""Free news fetcher — Google News RSS (no API key required).

Returns recent headlines for a stock, which the LLM then judges for short-term
sentiment. Google News RSS is a simple XML feed; we parse it with the stdlib so
there's no extra dependency.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger("sentiment-service")

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_BASE = "https://news.google.com/rss/search"


async def fetch_headlines(symbol: str, name: str, limit: int = 8) -> list[dict]:
    """Recent headlines for the stock, newest first."""
    query = f'"{name}" stock NSE' if name else f"{symbol} NSE share price"
    url = f"{_BASE}?q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=12.0) as c:
            r = await c.get(url, headers=_UA)
            r.raise_for_status()
            root = ET.fromstring(r.content)
    except Exception as exc:
        logger.debug("news fetch %s failed: %s", symbol, exc)
        return []

    items: list[dict] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        pub = (item.findtext("pubDate") or "").strip()
        source_el = item.find("{http://news.google.com}source")
        source = (source_el.text if source_el is not None else "") or ""
        items.append({"title": title, "source": source, "published": pub})
        if len(items) >= limit:
            break
    return items


def headlines_block(items: list[dict]) -> str:
    return "\n".join(f"{i+1}. {h['title']}" for i, h in enumerate(items))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
