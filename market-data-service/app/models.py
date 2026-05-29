from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class OHLCVCandle(BaseModel):
    time: datetime
    symbol: str
    exchange: str = "NSE"
    interval: str          # '1m','5m','15m','1h','1d'
    open: float
    high: float
    low: float
    close: float
    volume: int
    oi: Optional[int] = None
    source: str = "unknown"


class Tick(BaseModel):
    symbol: str
    exchange: str = "NSE"
    ltp: float             # last traded price
    open: float
    high: float
    low: float
    close: float
    volume: int
    timestamp: datetime
    source: str = "unknown"


class NewsArticle(BaseModel):
    article_id: str
    symbol: Optional[str] = None
    title: str
    description: Optional[str] = None
    url: str
    source: str
    published_at: datetime
    raw_text: str
    sentiment_score: Optional[float] = None   # filled by sentiment-agent


class MarketDataEvent(BaseModel):
    event_id: str
    timestamp: str
    service: str = "market-data-service"
    version: str = "1.0"
    payload: dict
