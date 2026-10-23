from app.consumer import _build_signal


def test_no_news_does_not_create_a_synthetic_hold_vote():
    aggregated = {
        "net_sentiment": 0.0,
        "article_count": 0,
        "effective_articles": 0.0,
    }
    assert _build_signal("RELIANCE", aggregated) is None


def test_news_still_creates_a_directional_vote():
    aggregated = {
        "net_sentiment": 0.4,
        "article_count": 2,
        "effective_articles": 2.0,
    }
    signal = _build_signal("RELIANCE", aggregated)
    assert signal is not None
    assert signal["payload"]["signal"] == "BUY"