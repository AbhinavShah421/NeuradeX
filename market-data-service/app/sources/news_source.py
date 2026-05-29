"""NewsAPI data source — fetches financial news for symbols and the broad market."""

import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Optional

_executor = ThreadPoolExecutor(max_workers=2)

MARKET_QUERIES = [
    "NSE stock market India",
    "BSE Sensex Nifty",
    "RBI interest rate India",
    "FII DII stock market",
]


def _fetch_articles_sync(api_key: str, query: str, page_size: int = 20) -> list[dict]:
    try:
        import requests
        from_date = (datetime.now(tz=timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q": query,
                "from": from_date,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": page_size,
                "apiKey": api_key,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        articles = []
        for a in data.get("articles", []):
            title = a.get("title", "") or ""
            desc = a.get("description", "") or ""
            raw_text = f"{title}. {desc}".strip()
            pub_at = a.get("publishedAt", "")
            try:
                published = datetime.fromisoformat(pub_at.replace("Z", "+00:00"))
            except Exception:
                published = datetime.now(tz=timezone.utc)
            article_id = hashlib.md5(a.get("url", raw_text).encode()).hexdigest()
            articles.append({
                "article_id": article_id,
                "title": title,
                "description": desc,
                "url": a.get("url", ""),
                "source": a.get("source", {}).get("name", "unknown"),
                "published_at": published,
                "raw_text": raw_text,
            })
        return articles
    except Exception:
        return []


class NewsSource:
    def __init__(self, api_key: str):
        self._api_key = api_key

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def fetch_for_symbol(self, symbol: str) -> list[dict]:
        query = f"{symbol} stock NSE India"
        articles = await asyncio.get_event_loop().run_in_executor(
            _executor, _fetch_articles_sync, self._api_key, query, 10
        )
        for a in articles:
            a["symbol"] = symbol
        return articles

    async def fetch_market_news(self) -> list[dict]:
        tasks = [
            asyncio.get_event_loop().run_in_executor(
                _executor, _fetch_articles_sync, self._api_key, q, 20
            )
            for q in MARKET_QUERIES
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        articles = []
        for r in results:
            if isinstance(r, list):
                articles.extend(r)
        seen = set()
        unique = []
        for a in articles:
            if a["article_id"] not in seen:
                seen.add(a["article_id"])
                unique.append(a)
        return unique
