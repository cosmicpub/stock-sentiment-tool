#!/usr/bin/env python3
"""Generate high-quality stock sentiment blog posts from market headlines."""

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
SITE_URL = os.getenv("SITE_URL", "https://stocksentimentscore.com").rstrip("/")

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
DEFAULT_MAX_POSTS = int(os.getenv("MAX_POSTS", "6"))

STOPWORDS = {
    "THE", "AND", "FOR", "WITH", "FROM", "NEWS", "TODAY", "WEEK", "MONTH", "INC", "CO", "PLC", "ETF",
    "CEO", "CFO", "IPO", "SEC", "GDP", "CPI", "FED", "NYSE", "NASDAQ", "US", "USA", "Q1", "Q2", "Q3", "Q4"
}

COMMON_FALSE_TICKERS = {
    "OPEN", "GROW", "UNIT", "WELL", "LOVE", "FREE", "TRUE", "GOOD", "BEST", "TOP", "LOW", "HIGH",
    "UP", "DOWN", "NOW", "NEW", "FAST", "GAIN", "LOSS", "RISK", "RATE", "SALE", "PLAN", "MOVE"
}

TRUSTED_SOURCES = {
    "Reuters", "Bloomberg", "CNBC", "MarketWatch", "WSJ", "Barrons", "Associated Press", "AP", "The Wall Street Journal"
}

POSITIVE_WORDS = {"beat", "beats", "surge", "surges", "strong", "growth", "record", "profit", "profits", "upgrade", "upgrades", "bullish", "rebound", "gains", "outperform"}
NEGATIVE_WORDS = {"miss", "misses", "drop", "drops", "fall", "falls", "slump", "warning", "warnings", "downgrade", "downgrades", "lawsuit", "probe", "bearish", "risk", "risks", "weak"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def ymd(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def date_label(dt: datetime) -> str:
    return dt.strftime("%B %d, %Y")


def http_json_get(url: str, *, timeout: int = 30, headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        return json.loads(resp.read().decode("utf-8"))


def http_json_post(url: str, payload: dict[str, Any], *, timeout: int = 60, headers: dict[str, str] | None = None) -> Any:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers or {"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        return json.loads(resp.read().decode("utf-8"))


def finnhub_get(endpoint: str, params: dict[str, Any], api_key: str) -> Any:
    query = urllib.parse.urlencode({**params, "token": api_key})
    return http_json_get(f"{FINNHUB_BASE_URL}/{endpoint}?{query}")


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


def is_reasonable_ticker(token: str) -> bool:
    if not re.fullmatch(r"[A-Z]{2,5}", token):
        return False
    if token in STOPWORDS or token in COMMON_FALSE_TICKERS:
        return False
    return True


def extract_ticker_candidates(news_items: list[dict[str, Any]]) -> Counter:
    counter: Counter = Counter()
    for item in news_items:
        headline = str(item.get("headline", ""))
        summary = str(item.get("summary", ""))
        related = str(item.get("related", ""))

        for token in re.findall(r"\$([A-Z]{1,5})\b", headline):
            if is_reasonable_ticker(token):
                counter[token] += 4

        for token in [t.strip().upper() for t in related.split(",") if t.strip()]:
            if is_reasonable_ticker(token):
                counter[token] += 6

        text = f"{headline} {summary}".upper()
        for token in re.findall(r"\b[A-Z]{2,5}\b", text):
            if is_reasonable_ticker(token):
                counter[token] += 1
    return counter


def score_sentiment(text: str) -> int:
    words = re.findall(r"[a-zA-Z]+", text.lower())
    pos = sum(1 for w in words if w in POSITIVE_WORDS)
    neg = sum(1 for w in words if w in NEGATIVE_WORDS)
    return pos - neg


def classify_sentiment(score: int) -> str:
    if score >= 2:
        return "Bullish"
    if score <= -2:
        return "Bearish"
    return "Neutral"


def pick_best_image(mentions: list[dict[str, Any]], used_urls: set[str] | None = None) -> str:
    used_urls = used_urls or set()

    def is_bad_image(url: str) -> bool:
        u = url.lower()
        bad_tokens = [
            "logo", "wordmark", "icon", "avatar", "placeholder", "default",
            "reuters.com/pf/resources", "/resources_v2/images/", "reuters-graphics",
            "sprite", "brand-assets"
        ]
        return any(t in u for t in bad_tokens)

    # Prefer first valid, non-duplicate, non-logo image
    for item in mentions:
        for key in ("image", "image_url", "urlToImage", "thumbnail"):
            v = item.get(key)
            if not isinstance(v, str):
                continue
            if not v.startswith(("http://", "https://")):
                continue
            if v in used_urls:
                continue
            if is_bad_image(v):
                continue
            return v

    return ""


def fallback_article(stock: dict[str, Any], generated_at: datetime) -> dict[str, str]:
    ticker = stock["ticker"]
    company = stock.get("company_name", ticker)
    industry = stock.get("industry", "Unknown")
    sentiment = stock.get("sentiment", "Neutral")
    score = stock.get("sentiment_score", 0)
    price = stock.get("price")
    date_text = date_label(generated_at)

    price_text = f"${price:.2f}" if isinstance(price, (int, float)) else "N/A"
    risk_level = "High" if abs(score) >= 4 else ("Medium" if abs(score) >= 2 else "Low")
    short_outlook = "Bullish bias" if score > 1 else ("Bearish bias" if score < -1 else "Sideways/Neutral")
    long_outlook = "Constructive if execution holds" if score >= 0 else "Cautious until trend improves"
    key_theme = "Momentum and narrative shift" if abs(score) >= 2 else "Mixed signals / wait-and-see"

    mentions = stock.get("mentions", [])[:6]
    drivers = []
    for item in mentions:
        h = str(item.get("headline", "")).strip()
        s = str(item.get("source", "")).strip() or "Source"
        if h:
            drivers.append(f"<li><strong>{escape(s)}:</strong> {escape(h)}</li>")

    faq_items = [
        (f"Is {ticker} stock a buy right now?", f"{ticker} currently has a {sentiment.lower()} setup. Consider valuation, earnings quality, and risk tolerance before deciding."),
        (f"What is driving {ticker} stock today?", "Headline flow, sentiment momentum, and forward guidance expectations are the key near-term drivers."),
        (f"What are the biggest risks for {ticker}?", "Estimate cuts, margin pressure, macro shocks, and execution misses are the core risks to monitor."),
        (f"What should investors watch next for {ticker}?", "Watch earnings commentary, analyst revisions, and whether headline tone confirms or reverses current momentum."),
    ]

    title = f"{ticker} Stock Analysis: {company} Stock Forecast & Outlook — Is {ticker} a Buy?"
    excerpt = (
        f"{company} ({ticker}) sentiment update with bull/bear scenarios, risk analysis, "
        f"and the key signals investors should watch next."
    )

    body_html = f"""
    <p><strong>{escape(company)} ({escape(ticker)})</strong> is trading around <strong>{escape(price_text)}</strong> and has a current sentiment read of <strong>{escape(sentiment)}</strong>. 
    The main market driver right now is headline momentum and expectation-reset risk across its sector.</p>
    <p>Investors are asking a simple question: <strong>is {escape(ticker)} a buy here, or is this setup a trap?</strong> 
    The answer depends on whether upcoming catalysts confirm the current narrative or break it.</p>

    <h2>Quick Verdict</h2>
    <ul>
      <li><strong>Sentiment:</strong> {escape(sentiment)}</li>
      <li><strong>Short-Term Outlook:</strong> {escape(short_outlook)}</li>
      <li><strong>Long-Term Outlook:</strong> {escape(long_outlook)}</li>
      <li><strong>Risk Level:</strong> {escape(risk_level)}</li>
    </ul>

    <h2>Stock Snapshot</h2>
    <ul>
      <li><strong>Price:</strong> {escape(price_text)}</li>
      <li><strong>Industry:</strong> {escape(industry)}</li>
      <li><strong>Sentiment Score:</strong> {escape(str(score))}</li>
      <li><strong>Key Theme:</strong> {escape(key_theme)}</li>
    </ul>

    <h2>Why {escape(ticker)} Is Moving Today</h2>
    {("<ul>" + "".join(drivers) + "</ul>") if drivers else "<p>Price action appears driven by a mix of company updates, sector tone, and positioning ahead of catalysts.</p>"}

    <h2>Bull Case for {escape(ticker)}</h2>
    <p>If execution remains strong, the upside case includes improved guidance credibility, stronger demand signals, and multiple expansion as uncertainty fades.</p>

    <h2>Bear Case for {escape(ticker)}</h2>
    <p>The downside case is centered on softer demand, earnings misses, estimate cuts, and valuation compression if macro or rates move against risk assets.</p>

    <h2>Key Risks for {escape(ticker)}</h2>
    <p>Primary risks include guidance disappointment, margin pressure, regulatory or competitive shocks, and sudden narrative reversals after earnings commentary.</p>

    <h2>What Investors Should Watch Next</h2>
    <p>Track analyst revision trends, management tone, and whether new headlines reinforce or contradict the current sentiment structure.</p>

    <h2>FAQ: {escape(ticker)} Stock Analysis</h2>
    {"".join([f"<h3>{escape(q)}</h3><p>{escape(a)}</p>" for q, a in faq_items])}

    <p><strong>Last Updated:</strong> {escape(date_text)} (UTC)</p>
    """

    return {
        "title": title,
        "excerpt": excerpt,
        "body_html": body_html.strip(),
    }


def generate_openai_article(stock: dict[str, Any], openai_api_key: str, model: str, generated_at: datetime) -> dict[str, str]:
    ticker = stock["ticker"]
    company = stock.get("company_name", ticker)
    industry = stock.get("industry", "Unknown")
    price = stock.get("price")
    sentiment = stock.get("sentiment", "Neutral")
    sentiment_score = stock.get("sentiment_score", 0)

    prompt = {
        "ticker": ticker,
        "company_name": company,
        "industry": industry,
        "price": price,
        "sentiment": sentiment,
        "sentiment_score": sentiment_score,
        "headlines": [
            {
                "headline": n.get("headline", ""),
                "summary": n.get("summary", ""),
                "source": n.get("source", ""),
                "datetime": n.get("datetime", ""),
                "url": n.get("url", ""),
            }
            for n in stock.get("mentions", [])[:10]
        ],
        "generated_at": iso(generated_at),
        "date_label": date_label(generated_at),
    }

    body = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": (
                    "You are a senior financial SEO writer and equity-content editor.\n"
                    "Return STRICT JSON with keys: title, excerpt, body_html.\n\n"

                    "Transform content with these rules:\n"
                    "1) Create a strong SEO title using patterns like:\n"
                    '   - "[Ticker] stock analysis"\n'
                    '   - "[Company] stock forecast"\n'
                    '   - "Is [Ticker] a buy"\n'
                    "2) Write a compelling 2-3 sentence intro that includes company+ticker, current price context, "
                    "main market driver, and a question hook for investors.\n"
                    "3) Add a 'Quick Verdict' section near the top including: sentiment, short-term outlook, "
                    "long-term outlook, risk level.\n"
                    "4) Add a 'Stock Snapshot' section including: price, industry, sentiment score, key theme.\n"
                    "5) Use these SEO-friendly section headers exactly:\n"
                    "   - Why [Stock] Is Moving Today\n"
                    "   - Bull Case for [Stock]\n"
                    "   - Bear Case for [Stock]\n"
                    "   - Key Risks for [Stock]\n"
                    "   - What Investors Should Watch Next\n"
                    "6) Improve readability: short paragraphs, occasional bold emphasis, light conversational tone.\n"
                    "7) Add an FAQ section at the bottom with 3-5 investor questions.\n"
                    "8) Add a visible 'Last Updated' timestamp.\n"
                    "9) Keep it informative but slightly opinionated and engaging.\n"
                    "10) Avoid generic phrasing; make the copy feel unique and human.\n\n"

                    "Hard output requirements:\n"
                    "- title: 65-100 chars, include ticker and high-intent keyword phrase.\n"
                    "- excerpt: 140-200 chars.\n"
                    "- body_html: 1,000-1,800 words, valid HTML using <h2>, <h3>, <p>, <ul>, <li>.\n"
                    "- Must include exactly one FAQ section with 3-5 Q&A items.\n"
                    "- Do NOT include markdown fences.\n"
                ),
            },
            {"role": "user", "content": json.dumps(prompt)},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "seo_blog_post_v2",
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

    parsed = http_json_post(
        OPENAI_API_URL,
        body,
        headers={
            "Authorization": f"Bearer {openai_api_key}",
            "Content-Type": "application/json",
        },
    )

    out = str(parsed.get("output_text", "")).strip()
    if not out and isinstance(parsed.get("output"), list):
        chunks = []
        for item in parsed["output"]:
            for content in item.get("content", []):
                txt = content.get("text")
                if isinstance(txt, str):
                    chunks.append(txt)
        out = "\n".join(chunks).strip()

    if not out:
        raise RuntimeError("Model returned empty output text")

    article = json.loads(out)
    return {
        "title": article["title"].strip(),
        "excerpt": article["excerpt"].strip(),
        "body_html": article["body_html"].strip(),
    }


def render_post_html(stock: dict[str, Any], article: dict[str, str], generated_at: datetime, slug: str, image_url: str) -> str:
    title = article["title"]
    excerpt = article["excerpt"]
    ticker = stock["ticker"]
    company = stock.get("company_name", ticker)
    sentiment = stock.get("sentiment", "Neutral")
    canonical = f"{SITE_URL}/blog/{slug}.html"

    image_meta = f'<meta property="og:image" content="{escape(image_url)}" />' if image_url else ""
    hero_image = f'<img src="{escape(image_url)}" alt="{escape(ticker)} market sentiment chart" loading="lazy" style="width:100%;max-height:420px;object-fit:cover;border-radius:14px;margin:0 0 20px;" />' if image_url else ""

    ld_json = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title,
        "description": excerpt,
        "datePublished": iso(generated_at),
        "dateModified": iso(generated_at),
        "author": {"@type": "Organization", "name": "Stock Sentiment Score"},
        "publisher": {"@type": "Organization", "name": "Stock Sentiment Score"},
        "mainEntityOfPage": canonical,
        "url": canonical,
    }
    if image_url:
        ld_json["image"] = [image_url]

    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>{escape(title)} | Stock Sentiment Score</title>
  <meta name=\"description\" content=\"{escape(excerpt)}\" />
  <link rel=\"canonical\" href=\"{escape(canonical)}\" />
  <meta property=\"og:title\" content=\"{escape(title)}\" />
  <meta property=\"og:description\" content=\"{escape(excerpt)}\" />
  <meta property=\"og:type\" content=\"article\" />
  <meta property=\"og:url\" content=\"{escape(canonical)}\" />
  {image_meta}
  <link rel=\"stylesheet\" href=\"/style.css\" />
  <script type=\"application/ld+json\">{json.dumps(ld_json)}</script>
</head>
<body>
  <div id=\"site-header\"></div>
  <main class=\"container content-page blog-article-page\">
    <article class=\"content-card blog-article-card\">
      <p class=\"eyebrow\">AI Stock Sentiment Report</p>
      <h1>{escape(title)}</h1>
      <p><strong>Ticker:</strong> {escape(ticker)} · <strong>Company:</strong> {escape(company)} · <strong>Sentiment:</strong> {escape(sentiment)}</p>
      <p><strong>Published:</strong> {escape(date_label(generated_at))}</p>
      {hero_image}
      {article['body_html']}
        <div style="margin-top:24px;padding:14px 16px;border:1px solid #fcd34d;background:#fffbeb;border-radius:10px;">
        <strong>Educational Use Only — Not Financial Advice.</strong>
        <p style="margin:8px 0 0;">
          This content is generated for educational and informational purposes only and should not be considered
          investment, financial, tax, or legal advice. Always do your own research and consult a licensed advisor.
        </p>
        </div>
      <hr />
      <p><a href=\"/blog/index.html\">← Back to blog index</a></p>
    </article>
  </main>
  <div id=\"site-footer\"></div>
  <script src=\"/js/include-header.js\"></script>
  <script src=\"/js/include-footer.js\"></script>
</body>
</html>
"""


def render_index(posts: list[dict[str, Any]], generated_at: datetime) -> str:
    cards = []
    for post in posts[:60]:
        image_html = ""
        if post.get("image_url"):
            image_html = (
                f'<a href="{escape(post["href"])}" aria-label="{escape(post["title"])}">'
                f'<img src="{escape(post["image_url"])}" alt="{escape(post.get("ticker", "Stock"))} report cover" '
                'loading="lazy" style="width:100%;height:180px;object-fit:cover;border-radius:14px;margin:0 0 14px;" />'
                f'</a>'
            )

        cards.append(
            f"""
        <article class=\"blog-report-card\">
          {image_html}
          <div class=\"blog-report-tag\">{escape(post.get('ticker', 'N/A'))} • {escape(post.get('sentiment', 'Neutral')).upper()}</div>
          <h3><a href=\"{escape(post['href'])}\">{escape(post['title'])}</a></h3>
          <p>{escape(post.get('excerpt', ''))}</p>
          <p style=\"margin:0 0 14px;font-size:14px;color:#64748b;\">{escape(post.get('published_date', ''))}</p>
          <a class=\"blog-report-link\" href=\"{escape(post['href'])}\">Read report →</a>
        </article>
        """.strip()
        )

    cards_html = "\n".join(cards) if cards else "<p>No reports available yet.</p>"

    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>AI Stock Sentiment Reports | Stock Sentiment Score Blog</title>
  <meta name=\"description\" content=\"Daily AI stock sentiment reports with headline impact analysis, catalysts, risks, and investor-focused summaries.\" />
  <link rel=\"canonical\" href=\"{SITE_URL}/blog/index.html\" />
  <link rel=\"stylesheet\" href=\"/style.css\" />
</head>
<body>
  <div id=\"site-header\"></div>
  <header class=\"hero hero-small blog-hero\">
    <div class=\"hero-inner\">
      <p class=\"eyebrow\">Market Insight Blog</p>
      <h1>AI Stock Sentiment Reports</h1>
      <p class=\"hero-text\">Fresh headline-driven analysis with SEO-focused titles, market context, and company-level risk/catalyst framing. Updated {escape(date_label(generated_at))}.</p>
    </div>
  </header>
  <main class=\"container content-page blog-page\">
    <section class=\"content-card blog-featured-card\">
      <div class=\"blog-section-top\">
        <div>
          <h2>Latest Reports</h2>
          <p>Browse sentiment, catalysts, and risk summaries by ticker.</p>
        </div>
      </div>
      <div class=\"blog-report-grid\">{cards_html}</div>
    </section>
  </main>
  <div id=\"site-footer\"></div>
  <script src=\"/js/include-header.js\"></script>
  <script src=\"/js/include-footer.js\"></script>
</body>
</html>
"""


def select_real_candidates(news: list[dict[str, Any]], api_key: str, limit_hint: int) -> list[dict[str, Any]]:
    counts = extract_ticker_candidates(news)
    candidates: list[dict[str, Any]] = []

    for ticker, mention_weight in counts.most_common(max(60, limit_hint * 20)):
        if mention_weight < 5:
            continue

        try:
            profile = finnhub_get("stock/profile2", {"symbol": ticker}, api_key)
            quote = finnhub_get("quote", {"symbol": ticker}, api_key)
        except urllib.error.URLError:
            continue

        if not isinstance(profile, dict) or not profile.get("ticker"):
            continue
        if not isinstance(quote, dict) or not quote.get("c"):
            continue

        mentions: list[dict[str, Any]] = []
        text_blobs = []
        trust_bonus = 0

        for item in news:
            headline = str(item.get("headline", ""))
            summary = str(item.get("summary", ""))
            related = str(item.get("related", "")).upper()
            full = f"{headline} {summary}".upper()

            hit = (
                ticker in {s.strip() for s in related.split(",") if s.strip()}
                or bool(re.search(rf"\${re.escape(ticker)}\b", headline.upper()))
                or bool(re.search(rf"\b{re.escape(ticker)}\b", full))
            )
            if not hit:
                continue

            mentions.append(item)
            text_blobs.append(f"{headline} {summary}")
            if str(item.get("source", "")).strip() in TRUSTED_SOURCES:
                trust_bonus += 1

        if len(mentions) < 2:
            continue

        sentiment_score = score_sentiment(" ".join(text_blobs))
        impact_score = mention_weight + len(mentions) * 2 + abs(sentiment_score) + trust_bonus

        candidates.append(
            {
                "ticker": ticker,
                "company_name": profile.get("name") or ticker,
                "industry": profile.get("finnhubIndustry") or "Unknown",
                "price": quote.get("c"),
                "sentiment": classify_sentiment(sentiment_score),
                "sentiment_score": sentiment_score,
                "impact_score": impact_score,
                "mentions": mentions,
                "image_url": pick_best_image(mentions),
            }
        )

    candidates.sort(key=lambda x: x["impact_score"], reverse=True)
    return candidates


def mock_candidates() -> list[dict[str, Any]]:
    return [
        {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "industry": "Consumer Electronics",
            "price": 198.11,
            "sentiment": "Bullish",
            "sentiment_score": 3,
            "impact_score": 18,
            "image_url": "https://images.unsplash.com/photo-1611186871348-b1ce696e52c9?auto=format&fit=crop&w=1200&q=80",
            "mentions": [{"headline": "Apple supplier checks point to stronger-than-expected iPhone demand", "source": "Reuters"}],
        },
        {
            "ticker": "NVDA",
            "company_name": "NVIDIA Corporation",
            "industry": "Semiconductors",
            "price": 1120.44,
            "sentiment": "Bullish",
            "sentiment_score": 2,
            "impact_score": 16,
            "image_url": "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=1200&q=80",
            "mentions": [{"headline": "NVIDIA AI infrastructure demand remains elevated across hyperscalers", "source": "Bloomberg"}],
        },
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate AI stock sentiment blog posts.")
    parser.add_argument("--mock", action="store_true", help="Generate deterministic sample content without API calls.")
    parser.add_argument("--limit", type=int, default=DEFAULT_MAX_POSTS, help="Maximum number of posts to generate.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI model for article generation.")
    return parser.parse_args()
def ensure_seo_title(title: str, ticker: str, company: str) -> str:
    t = (title or "").strip()

    # Detect weak/generic title patterns
    weak_patterns = [
        "news impact report",
        "sentiment report",
        "stock update",
        "market update",
        "daily update",
    ]
    is_weak = (len(t) < 45) or any(p in t.lower() for p in weak_patterns)

    if is_weak:
        return f"{ticker} Stock Analysis & Forecast: Is {ticker} a Buy Right Now?"

    # Ensure high-intent keywords are present
    lower_t = t.lower()
    if "stock analysis" not in lower_t and "stock forecast" not in lower_t and "is " + ticker.lower() + " a buy" not in lower_t:
        t = f"{t} | {ticker} Stock Analysis"

    # Keep reasonable SEO length
    if len(t) > 100:
        t = t[:97].rstrip() + "..."

    return t


def ensure_excerpt_quality(excerpt: str, ticker: str, company: str) -> str:
    e = (excerpt or "").strip()
    if len(e) < 120:
        e = (
            f"{company} ({ticker}) sentiment outlook with bull and bear scenarios, "
            f"key risks, and the next catalysts investors should watch."
        )
    if len(e) > 200:
        e = e[:197].rstrip() + "..."
    return e

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

    candidates = mock_candidates() if args.mock else select_real_candidates(
        finnhub_get("news", {"category": "general"}, finnhub_api_key),
        finnhub_api_key,
        limit,
    )

    selected = candidates[:limit]

    BLOG_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    posts = manifest.get("posts", [])

    used_images: set[str] = set()
    
    for stock in selected:
        slug = f"{stock['ticker'].lower()}-news-impact-{ymd(generated_at)}"
        article = fallback_article(stock, generated_at)
        if not args.mock:
            try:
                article = generate_openai_article(stock, openai_api_key, args.model, generated_at)
            except Exception:
                pass
                
        article["title"] = ensure_seo_title(article.get("title", ""), stock["ticker"], stock.get("company_name", stock["ticker"]))
        article["excerpt"] = ensure_excerpt_quality(article.get("excerpt", ""), stock["ticker"], stock.get("company_name", stock["ticker"]))
        
        image_url = stock.get("image_url") or pick_best_image(stock.get("mentions", []), used_images)
        if image_url:
            used_images.add(image_url)
        html = render_post_html(stock, article, generated_at, slug, image_url)
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
                "image_url": image_url,
            }
        )

    posts = sorted(posts, key=lambda x: x.get("generated_at", ""), reverse=True)

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
