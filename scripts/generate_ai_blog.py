#!/usr/bin/env python3
"""Generate stock sentiment blog posts from current market headlines.

Supports two modes:
- Real mode: fetches Finnhub news/quotes and uses OpenAI to draft article copy.
- Mock mode: no network/API keys required, creates deterministic sample posts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BLOG_DIR = ROOT / "blog"
INDEX_PATH = BLOG_DIR / "index.html"
MANIFEST_PATH = ROOT / "data" / "blog-manifest.json"

OPENAI_API_URL = "https://api.openai.com/v1/responses"
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
DEFAULT_MAX_POSTS = 6

STOPWORDS = {
    "A",
    "AN",
    "THE",
    "AND",
    "OR",
    "FOR",
    "WITH",
    "FROM",
    "BY",
    "ON",
    "IN",
    "TO",
    "OF",
    "US",
    "USA",
    "ETF",
    "ETFS",
    "CEO",
    "CFO",
    "IPO",
    "SEC",
    "GDP",
    "CPI",
    "FED",
    "NYSE",
    "NASDAQ",
    "NEWS",
    "TODAY",
    "WEEK",
    "MONTH",
    "Q1",
    "Q2",
    "Q3",
    "Q4",
}

POSITIVE_WORDS = {
    "beat",
    "beats",
    "surge",
    "surges",
    "strong",
    "growth",
    "record",
    "profit",
    "profits",
    "upgrade",
    "upgrades",
    "win",
    "wins",
    "bullish",
    "rebound",
    "gains",
}

NEGATIVE_WORDS = {
    "miss",
    "misses",
    "drop",
    "drops",
    "fall",
    "falls",
    "slump",
    "warning",
    "warnings",
    "downgrade",
    "downgrades",
    "lawsuit",
    "probe",
    "bearish",
    "risk",
    "risks",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def ymd(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def date_label(dt: datetime) -> str:
    return dt.strftime("%B %d, %Y")


def safe_get_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 30) -> Any:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        return json.loads(response.read().decode("utf-8"))


def finnhub_get(endpoint: str, params: dict[str, Any], api_key: str) -> Any:
    qs = urllib.parse.urlencode({**params, "token": api_key})
    url = f"{FINNHUB_BASE_URL}/{endpoint}?{qs}"
    return safe_get_json(url)


def load_manifest() -> dict[str, Any]:
    if MANIFEST_PATH.exists():
        try:
            payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload.setdefault("posts", [])
                return payload
        except json.JSONDecodeError:
            pass
    return {"generated_at": None, "posts": []}


def save_manifest(manifest: dict[str, Any]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def extract_ticker_candidates(news_items: list[dict[str, Any]]) -> Counter:
    counter: Counter = Counter()
    for item in news_items:
        text = f"{item.get('headline', '')} {item.get('summary', '')}".upper()
        for token in re.findall(r"\b[A-Z]{1,5}\b", text):
            if token in STOPWORDS:
                continue
            counter[token] += 1
    return counter


def score_sentiment(text: str) -> int:
    words = re.findall(r"[a-zA-Z]+", text.lower())
    pos = sum(1 for word in words if word in POSITIVE_WORDS)
    neg = sum(1 for word in words if word in NEGATIVE_WORDS)
    return pos - neg


def classify_sentiment(score: int) -> str:
    if score >= 2:
        return "Bullish"
    if score <= -2:
        return "Bearish"
    return "Neutral"


def build_article_fallback(stock: dict[str, Any], generated_at: datetime) -> dict[str, str]:
    ticker = stock["ticker"]
    company = stock.get("company_name") or ticker
    sentiment = stock["sentiment"].lower()
    score = stock["sentiment_score"]
    date = date_label(generated_at)

    return {
        "title": f"{ticker} News Impact Report ({date})",
        "excerpt": f"A quick view of {company}'s latest headline flow and why traders are reading it as {sentiment}.",
        "body_html": "".join(
            [
                f"<p>{escape(company)} ({escape(ticker)}) is showing a <strong>{escape(stock['sentiment'])}</strong> tone in headline coverage as of {escape(date)}.</p>",
                f"<p>Our heuristic sentiment score is <strong>{score}</strong>, based on recent positive/negative signal words in market coverage.</p>",
                "<p>Use this report as a directional summary and pair it with valuation, risk, and macro context before taking action.</p>",
            ]
        ),
    }


def generate_article_openai(stock: dict[str, Any], generated_at: datetime, openai_api_key: str, model: str) -> dict[str, str]:
    prompt = {
        "ticker": stock["ticker"],
        "company_name": stock.get("company_name"),
        "sentiment": stock["sentiment"],
        "sentiment_score": stock["sentiment_score"],
        "headlines": [
            {
                "headline": item.get("headline"),
                "source": item.get("source"),
                "datetime": item.get("datetime"),
                "url": item.get("url"),
            }
            for item in stock.get("mentions", [])[:8]
        ],
        "generated_at": iso(generated_at),
    }

    body = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": (
                    "You write concise financial blog posts. Return strict JSON with keys: "
                    "title, excerpt, body_html. body_html must contain 2-4 short <p> paragraphs."
                ),
            },
            {"role": "user", "content": json.dumps(prompt)},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "blog_post",
                "schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "excerpt": {"type": "string"},
                        "body_html": {"type": "string"},
                    },
                    "required": ["title", "excerpt", "body_html"],
                    "additionalProperties": False,
                },
                "strict": True,
            }
        },
    }

    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        OPENAI_API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {openai_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as res:  # nosec B310
        parsed = json.loads(res.read().decode("utf-8"))

    text = parsed.get("output_text", "").strip()
    if not text:
        raise RuntimeError("Empty model output")
    article = json.loads(text)
    return {
        "title": article["title"].strip(),
        "excerpt": article["excerpt"].strip(),
        "body_html": article["body_html"].strip(),
    }


def render_post_html(stock: dict[str, Any], article: dict[str, str], generated_at: datetime, slug: str) -> str:
    title = article["title"]
    description = article["excerpt"]
    company = stock.get("company_name") or stock["ticker"]
    sentiment = stock["sentiment"]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{escape(title)} | Stock Sentiment Score</title>
  <meta name="description" content="{escape(description)}" />
  <link rel="stylesheet" href="/style.css" />
</head>
<body>
  <div id="site-header"></div>
  <main class="container content-page blog-post-page">
    <article class="content-card">
      <p class="eyebrow">AI Sentiment Report</p>
      <h1>{escape(title)}</h1>
      <p><strong>Ticker:</strong> {escape(stock['ticker'])} · <strong>Company:</strong> {escape(company)} · <strong>Sentiment:</strong> {escape(sentiment)}</p>
      <p><strong>Published:</strong> {escape(date_label(generated_at))}</p>
      {article['body_html']}
      <hr />
      <p><a href="/blog/index.html">← Back to blog index</a></p>
    </article>
  </main>
  <div id="site-footer"></div>
  <script src="/js/include-header.js"></script>
  <script src="/js/include-footer.js"></script>
</body>
</html>
"""


def render_index(posts: list[dict[str, Any]], generated_at: datetime) -> str:
    cards = []
    for post in posts[:60]:
        cards.append(
            f"""
        <article class="blog-report-card">
          <div class="blog-report-tag">{escape(post.get('ticker', 'N/A'))} · {escape(post.get('sentiment', 'Neutral'))}</div>
          <h3><a href="{escape(post['href'])}">{escape(post['title'])}</a></h3>
          <p>{escape(post.get('excerpt', ''))}</p>
          <a class="blog-report-link" href="{escape(post['href'])}">Read report →</a>
        </article>
        """.strip()
        )

    cards_html = "\n".join(cards) if cards else "<p>No reports available yet.</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Stock Sentiment Blog | Stock Sentiment Score</title>
  <meta name="description" content="Recent AI-generated stock sentiment reports and headline impact summaries." />
  <link rel="stylesheet" href="/style.css" />
</head>
<body>
  <div id="site-header"></div>
  <header class="hero hero-small blog-hero">
    <div class="hero-inner">
      <p class="eyebrow">Market Insight Blog</p>
      <h1>Stock Sentiment Blog</h1>
      <p class="hero-text">Auto-generated from recent headlines. Last updated {escape(date_label(generated_at))}.</p>
    </div>
  </header>
  <main class="container content-page blog-page">
    <section class="content-card blog-featured-card">
      <div class="blog-section-top">
        <div>
          <h2>Latest Reports</h2>
          <p>Short-form AI summaries of news momentum by ticker.</p>
        </div>
      </div>
      <div class="blog-report-grid">
        {cards_html}
      </div>
    </section>
  </main>
  <div id="site-footer"></div>
  <script src="/js/include-header.js"></script>
  <script src="/js/include-footer.js"></script>
</body>
</html>
"""


def select_real_candidates(news: list[dict[str, Any]], finnhub_api_key: str) -> list[dict[str, Any]]:
    ticker_counts = extract_ticker_candidates(news)
    candidates: list[dict[str, Any]] = []

    for ticker, mentions in ticker_counts.most_common(120):
        if mentions < 2:
            continue

        try:
            profile = finnhub_get("stock/profile2", {"symbol": ticker}, finnhub_api_key)
            quote = finnhub_get("quote", {"symbol": ticker}, finnhub_api_key)
        except urllib.error.URLError:
            continue

        if not isinstance(profile, dict) or not profile.get("ticker"):
            continue
        if not isinstance(quote, dict) or not quote.get("c"):
            continue

        related = []
        combined_text = []
        for item in news:
            text = f"{item.get('headline', '')} {item.get('summary', '')}".upper()
            if re.search(rf"\b{re.escape(ticker)}\b", text):
                related.append(item)
                combined_text.append(f"{item.get('headline', '')} {item.get('summary', '')}")

        if len(related) < 2:
            continue

        sentiment_score = score_sentiment(" ".join(combined_text))
        candidates.append(
            {
                "ticker": ticker,
                "company_name": profile.get("name") or ticker,
                "industry": profile.get("finnhubIndustry") or "Unknown",
                "price": quote.get("c"),
                "sentiment_score": sentiment_score,
                "sentiment": classify_sentiment(sentiment_score),
                "mentions": related,
                "impact_score": mentions * 2 + abs(sentiment_score),
            }
        )

    candidates.sort(key=lambda item: item["impact_score"], reverse=True)
    return candidates


def mock_candidates() -> list[dict[str, Any]]:
    return [
        {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "industry": "Consumer Electronics",
            "price": 198.11,
            "sentiment_score": 3,
            "sentiment": "Bullish",
            "impact_score": 10,
            "mentions": [{"headline": "Apple supplier checks point to strong iPhone demand", "source": "MockWire"}],
        },
        {
            "ticker": "NVDA",
            "company_name": "NVIDIA Corporation",
            "industry": "Semiconductors",
            "price": 1120.44,
            "sentiment_score": 2,
            "sentiment": "Bullish",
            "impact_score": 9,
            "mentions": [{"headline": "NVIDIA AI server backlog remains elevated", "source": "MockWire"}],
        },
        {
            "ticker": "TSLA",
            "company_name": "Tesla, Inc.",
            "industry": "Automobiles",
            "price": 173.05,
            "sentiment_score": -2,
            "sentiment": "Bearish",
            "impact_score": 8,
            "mentions": [{"headline": "Tesla faces margin pressure amid renewed EV incentives", "source": "MockWire"}],
        },
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate AI stock sentiment blog posts.")
    parser.add_argument("--mock", action="store_true", help="Run without external APIs and generate deterministic sample posts.")
    parser.add_argument("--limit", type=int, default=DEFAULT_MAX_POSTS, help=f"Maximum number of posts to generate (default: {DEFAULT_MAX_POSTS}).")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"OpenAI model name (default: {DEFAULT_MODEL}).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    limit = max(1, args.limit)
    generated_at = now_utc()

    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    finnhub_api_key = os.getenv("FINNHUB_API_KEY", "").strip()

    if not args.mock:
        if not finnhub_api_key:
            raise RuntimeError("Missing FINNHUB_API_KEY (or run with --mock)")
        if not openai_api_key:
            raise RuntimeError("Missing OPENAI_API_KEY (or run with --mock)")

    if args.mock:
        candidates = mock_candidates()
    else:
        news = finnhub_get("news", {"category": "general"}, finnhub_api_key)
        if not isinstance(news, list):
            raise RuntimeError("Finnhub general news response was not a list")
        candidates = select_real_candidates(news, finnhub_api_key)

    selected = candidates[:limit]

    BLOG_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    posts = manifest.get("posts", [])

    for stock in selected:
        slug = f"{stock['ticker'].lower()}-news-impact-{ymd(generated_at)}"

        if args.mock:
            article = build_article_fallback(stock, generated_at)
        else:
            try:
                article = generate_article_openai(stock, generated_at, openai_api_key, args.model)
            except Exception:
                article = build_article_fallback(stock, generated_at)

        html = render_post_html(stock, article, generated_at, slug)
        (BLOG_DIR / f"{slug}.html").write_text(html, encoding="utf-8")

        posts.append(
            {
                "ticker": stock["ticker"],
                "title": article["title"],
                "excerpt": article["excerpt"],
                "sentiment": stock["sentiment"],
                "score": stock["sentiment_score"],
                "impact_score": stock["impact_score"],
                "href": f"/blog/{slug}.html",
                "published_date": ymd(generated_at),
                "generated_at": iso(generated_at),
            }
        )

    posts = sorted(posts, key=lambda item: item.get("generated_at", ""), reverse=True)

    INDEX_PATH.write_text(render_index(posts, generated_at), encoding="utf-8")
    manifest.update(
        {
            "generated_at": iso(generated_at),
            "market_data_as_of": iso(generated_at),
            "model": args.model,
            "posts": posts,
        }
    )
    save_manifest(manifest)

    print(f"Done. Generated {len(selected)} post(s). mock={args.mock}")


if __name__ == "__main__":
    main()
