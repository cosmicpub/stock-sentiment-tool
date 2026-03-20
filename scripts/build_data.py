import json
import os
from datetime import datetime, timedelta, timezone

import requests


API_KEY = os.getenv("MARKET_API_KEY", "").strip()
BASE_URL = "https://finnhub.io/api/v1"

# Starter tickers. Keep this small at first.
TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META"]

POSITIVE_WORDS = [
    "beat", "beats", "growth", "strong", "surge", "surges", "record",
    "profit", "profits", "upgrade", "upgrades", "expands", "expansion",
    "partnership", "partnerships", "wins", "raised", "raises", "raise",
    "outperform", "outperforms", "bullish", "demand", "rebound", "rebounds",
    "gain", "gains", "positive", "momentum"
]

NEGATIVE_WORDS = [
    "miss", "misses", "lawsuit", "lawsuits", "downgrade", "downgrades",
    "cuts", "cut", "decline", "declines", "weak", "investigation",
    "investigations", "layoffs", "drop", "drops", "warning", "warnings",
    "bearish", "risk", "risks", "slump", "falls", "fall", "negative",
    "recall", "recalls", "fraud", "probe"
]


def require_api_key() -> None:
    if not API_KEY:
        raise RuntimeError("Missing MARKET_API_KEY environment variable.")


def get_json(url: str, params: dict) -> dict | list:
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def score_headline(headline: str) -> int:
    text = headline.lower()
    score = 0

    for word in POSITIVE_WORDS:
        if word in text:
            score += 1

    for word in NEGATIVE_WORDS:
        if word in text:
            score -= 1

    return score


def label_from_score(score: int) -> str:
    if score >= 3:
        return "Bullish"
    if score <= -3:
        return "Bearish"
    return "Mixed"


def confidence_from_score(score: int, headline_count: int) -> str:
    abs_score = abs(score)

    if headline_count == 0:
        return "Low"
    if abs_score >= 6:
        return "High"
    if abs_score >= 3:
        return "Moderate"
    return "Low"


def build_reason(score: int, headline_count: int, price_change_percent: float | None) -> str:
    direction = "mixed"
    if score > 0:
        direction = "more positive"
    elif score < 0:
        direction = "more negative"

    if price_change_percent is None:
        return (
            f"Based on {headline_count} recent headlines with {direction} language."
        )

    price_text = f"{price_change_percent:+.2f}% today"
    return (
        f"Based on {headline_count} recent headlines with {direction} language "
        f"and a stock move of {price_text}."
    )


def get_quote(ticker: str) -> dict:
    return get_json(
        f"{BASE_URL}/quote",
        {"symbol": ticker, "token": API_KEY}
    )


def get_company_news(ticker: str) -> list:
    today = datetime.now(timezone.utc).date()
    week_ago = today - timedelta(days=7)

    news = get_json(
        f"{BASE_URL}/company-news",
        {
            "symbol": ticker,
            "from": week_ago.isoformat(),
            "to": today.isoformat(),
            "token": API_KEY
        }
    )

    if not isinstance(news, list):
        return []

    filtered = []
    seen = set()

    for item in news:
        headline = (item.get("headline") or "").strip()
        summary = (item.get("summary") or "").strip()
        url = (item.get("url") or "").strip()
        source = (item.get("source") or "").strip()

        if not headline:
            continue
        if headline in seen:
            continue

        seen.add(headline)
        filtered.append({
            "headline": headline,
            "summary": summary,
            "source": source,
            "url": url
        })

        if len(filtered) >= 8:
            break

    return filtered


def build_stock_record(ticker: str) -> dict:
    quote = get_quote(ticker)
    news_items = get_company_news(ticker)

    current_price = quote.get("c")
    change = quote.get("d")
    percent_change = quote.get("dp")
    high = quote.get("h")
    low = quote.get("l")
    open_price = quote.get("o")
    prev_close = quote.get("pc")

    headline_scores = [score_headline(item["headline"]) for item in news_items]
    total_score = sum(headline_scores)
    sentiment = label_from_score(total_score)
    confidence = confidence_from_score(total_score, len(news_items))
    reason = build_reason(total_score, len(news_items), percent_change)

    enriched_news = []
    for item, headline_score in zip(news_items, headline_scores):
        entry = dict(item)
        entry["score"] = headline_score
        entry["signal"] = "Bullish" if headline_score > 0 else "Bearish" if headline_score < 0 else "Neutral"
        enriched_news.append(entry)

    return {
        "ticker": ticker,
        "price": current_price,
        "change": change,
        "percent_change": percent_change,
        "high": high,
        "low": low,
        "open": open_price,
        "previous_close": prev_close,
        "sentiment": sentiment,
        "sentiment_score": total_score,
        "confidence": confidence,
        "reason": reason,
        "news_count": len(enriched_news),
        "news": enriched_news
    }


def main() -> None:
    require_api_key()

    stocks = []
    errors = []

    for ticker in TICKERS:
        try:
            stocks.append(build_stock_record(ticker))
        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "stocks": stocks,
        "errors": errors
    }

    os.makedirs("data", exist_ok=True)
    with open("data/market-data.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


if __name__ == "__main__":
    main()
