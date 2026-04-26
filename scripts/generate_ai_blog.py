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
ARCHIVE_PATH = BLOG_DIR / "archive.html"
MANIFEST_PATH = ROOT / "data" / "blog-manifest.json"

OPENAI_API_URL = "https://api.openai.com/v1/responses"
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
SITE_URL = os.getenv("SITE_URL", "https://stocksentimentscore.com").rstrip("/")

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
DEFAULT_MAX_POSTS = int(os.getenv("MAX_POSTS", "6"))
FRONT_PAGE_POST_LIMIT = 24

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

def fallback_image_for_ticker(ticker: str) -> str:
    # deterministic fallback image by ticker initial
    options = [
        "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?auto=format&fit=crop&w=1400&q=80",  # markets
        "https://images.unsplash.com/photo-1520607162513-77705c0f0d4a?auto=format&fit=crop&w=1400&q=80",  # trading floor
        "https://images.unsplash.com/photo-1460472178825-e5240623afd5?auto=format&fit=crop&w=1400&q=80",  # finance desk
        "https://images.unsplash.com/photo-1642543492481-44e81e3914a7?auto=format&fit=crop&w=1400&q=80",  # chart screen
    ]
    idx = sum(ord(c) for c in ticker) % len(options)
    return options[idx]

def fallback_image_for_ticker(ticker: str) -> str:
    # deterministic fallback image so cards never render blank
    options = [
        "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?auto=format&fit=crop&w=1400&q=80",
        "https://images.unsplash.com/photo-1520607162513-77705c0f0d4a?auto=format&fit=crop&w=1400&q=80",
        "https://images.unsplash.com/photo-1460472178825-e5240623afd5?auto=format&fit=crop&w=1400&q=80",
        "https://images.unsplash.com/photo-1642543492481-44e81e3914a7?auto=format&fit=crop&w=1400&q=80",
    ]
    idx = sum(ord(c) for c in ticker) % len(options)
    return options[idx]


def pick_best_image(mentions: list[dict[str, Any]]) -> str:
    def looks_like_logo(url: str) -> bool:
        lowered = url.lower()
        noisy_tokens = (
            "logo",
            "icon",
            "avatar",
            "wordmark",
            "placeholder",
            "default-image",
            "default.jpg",
            "spacer",
            "sprite",
            "brand-assets",
        )
        reuters_logo_patterns = ("reuters.com/pf/resources", "/resources_v2/images/", "reuters-graphics")
        return any(token in lowered for token in noisy_tokens) or any(token in lowered for token in reuters_logo_patterns)

    for item in mentions:
        for key in ("image", "image_url", "urlToImage", "thumbnail"):
            v = item.get(key)
            if isinstance(v, str) and v.startswith(("http://", "https://")) and not looks_like_logo(v):
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

    prompt = {
        "ticker": ticker,
        "company_name": company,
        "industry": stock.get("industry", "Unknown"),
        "price": stock.get("price"),
        "sentiment": stock.get("sentiment", "Neutral"),
        "sentiment_score": stock.get("sentiment_score", 0),
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
                    "You are a senior financial SEO content strategist writing high-quality stock analysis articles.\n"
                    "Return STRICT JSON with keys: title, excerpt, body_html.\n\n"

                    "Follow this framework, but introduce natural variation in execution:\n"
                    "1) Always include:\n"
                    "   - SEO-optimized title\n"
                    "   - Intro hook\n"
                    "   - Quick Verdict\n"
                    "   - Stock Snapshot\n"
                    "   - At least 3-5 core analysis sections\n"
                    "   - FAQ section\n"
                    "   - Last Updated timestamp\n\n"

                    "2) Choose ONE primary framing per article:\n"
                    "   - Is this stock a buy?\n"
                    "   - Why this stock is moving\n"
                    "   - Bull vs Bear breakdown\n"
                    "   - Biggest risks investors should watch\n"
                    "   - Short-term vs long-term outlook\n\n"

                    "3) Vary section order naturally (do not always use the same sequence).\n\n"

                    "4) Dynamically include/exclude optional sections based on context:\n"
                    "   - What Smart Investors Are Thinking\n"
                    "   - Hidden Opportunity\n"
                    "   - Market Overreaction?\n"
                    "   - Competitor Comparison\n"
                    "   - Valuation Insight\n\n"

                    "5) Rewrite all sections with unique phrasing each time:\n"
                    "   - avoid repeated sentence structures\n"
                    "   - avoid predictable transitions\n"
                    "   - use natural, human-like tone\n\n"

                    "6) Add light opinionated phrasing:\n"
                    "   - highlight what matters most\n"
                    "   - call out risks/opportunities clearly\n\n"

                    "7) Generate a unique FAQ section each time based on this stock and current setup.\n"
                    "8) Keep paragraphs short and readable.\n"
                    "9) Avoid generic AI-sounding language.\n\n"

                    "IMPORTANT content requirements:\n"
                    "- title: 65-100 chars, include high-intent SEO terms such as "
                    "\"[Ticker] stock analysis\", \"[Company] stock forecast\", or \"Is [Ticker] a buy\".\n"
                    "- excerpt: 140-200 chars, compelling and specific.\n"
                    "- body_html: 900-1600 words, valid HTML using <h2>, <h3>, <p>, <ul>, <li>.\n"
                    "- Include a clear educational-use disclaimer in the generated body near the end:\n"
                    "  \"This content is for educational and informational purposes only and is not financial advice.\"\n"
                    "- Include a visible \"Last Updated\" line in body_html using provided date.\n"
                    "- No markdown fences. Output only JSON."
                ),
            },
            {"role": "user", "content": json.dumps(prompt)},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "seo_blog_post_v3",
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


def render_report_cards(posts: list[dict[str, Any]]) -> str:
    cards = []
    for post in posts:
        image_html = ""
        if post.get("image_url"):
            image_html = (
                f'<a href="{escape(post["href"])}" aria-label="{escape(post["title"])}">'
                f'<img src="{escape(post["image_url"])}" alt="{escape(post.get("ticker", "Stock"))} report cover" '
                'loading="lazy" style="width:100%;height:140px;object-fit:cover;border-radius:12px;margin:0 0 10px;" />'
                f'</a>'
            )

        cards.append(
            f"""
        <article class="blog-report-card">
          {image_html}
          <div class="blog-report-tag">{escape(post.get('ticker', 'N/A'))} • {escape(post.get('sentiment', 'Neutral')).upper()}</div>
          <h3><a href="{escape(post['href'])}">{escape(post['title'])}</a></h3>
          <p>{escape(post.get('excerpt', ''))}</p>
          <p style="margin:0 0 10px;font-size:13px;color:#64748b;">{escape(post.get('published_date', ''))}</p>
          <a class="blog-report-link" href="{escape(post['href'])}">Read report →</a>
        </article>
        """.strip()
        )
    return "\n".join(cards) if cards else "<p>No reports available yet.</p>"


def render_index(posts: list[dict[str, Any]], generated_at: datetime) -> str:
    visible_posts = posts[:FRONT_PAGE_POST_LIMIT]
    archived_count = max(0, len(posts) - FRONT_PAGE_POST_LIMIT)

    lead = visible_posts[0] if visible_posts else None
    rest = visible_posts[1:] if len(visible_posts) > 1 else []

    lead_html = ""
    if lead:
        lead_image = (
            f'<img src="{escape(lead["image_url"])}" alt="{escape(lead.get("ticker", "Stock"))} lead report" loading="lazy" />'
            if lead.get("image_url")
            else ""
        )
        lead_html = f"""
        <article class="news-lead-card">
          <a class="news-lead-image" href="{escape(lead['href'])}">{lead_image}</a>
          <div class="news-lead-body">
            <div class="blog-report-tag">{escape(lead.get('ticker', 'N/A'))} • {escape(lead.get('sentiment', 'Neutral')).upper()}</div>
            <h2><a href="{escape(lead['href'])}">{escape(lead['title'])}</a></h2>
            <p>{escape(lead.get('excerpt', ''))}</p>
            <p class="news-meta">{escape(lead.get('published_date', ''))}</p>
            <a class="blog-report-link" href="{escape(lead['href'])}">Read lead report →</a>
          </div>
        </article>
        """.strip()

    cards_html = render_report_cards(rest)

    # Sidebar: top 8 recent tickers (excluding lead)
    ticker_items = []
    for p in rest[:8]:
        ticker_items.append(
            f'<li><a href="{escape(p["href"])}"><strong>{escape(p.get("ticker", "N/A"))}</strong> — {escape(p["title"])}</a></li>'
        )
    sidebar_list_html = "".join(ticker_items) if ticker_items else "<li>No additional reports yet.</li>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AI Stock Sentiment Reports | Stock Sentiment Score Blog</title>
  <meta name="description" content="Daily AI stock sentiment reports with headline impact analysis, catalysts, risks, and investor-focused summaries." />
  <link rel="canonical" href="{SITE_URL}/blog/index.html" />
  <link rel="stylesheet" href="/style.css" />
</head>
<body>
  <div id="site-header"></div>

  <header class="hero hero-small blog-hero">
    <div class="hero-inner">
      <p class="eyebrow">Market Insight Blog</p>
      <h1>AI Stock Sentiment Reports</h1>
      <p class="hero-text">Fresh headline-driven analysis with SEO-focused titles, market context, and company-level risk/catalyst framing. Updated {escape(date_label(generated_at))}.</p>
      <p class="blog-disclaimer-subtle">Educational content only — not financial advice.</p>
    </div>
  </header>

  <main class="container content-page blog-page">
    <section class="content-card blog-news-shell">
      <div class="blog-news-main">
        <div class="blog-section-top">
          <div>
            <h2>Latest Reports</h2>
            <p>Front page shows the latest {FRONT_PAGE_POST_LIMIT} reports.</p>
          </div>
        </div>
        {lead_html}
        <div class="blog-report-grid blog-report-grid-large">{cards_html}</div>
      </div>

      <aside class="blog-news-sidebar">
        <div class="blog-sidebar-card">
          <h3>Top Stories</h3>
          <ul class="sidebar-story-list">
            {sidebar_list_html}
          </ul>
        </div>

        <div class="blog-sidebar-card">
          <h3>Feed Status</h3>
          <p><strong>Visible now:</strong> {len(visible_posts)} reports</p>
          <p><strong>Archived:</strong> {archived_count} reports</p>
          <a class="blog-report-link" href="/blog/archive.html">Open full archive →</a>
        </div>
      </aside>
    </section>
  </main>

  <div id="site-footer"></div>
  <script src="/js/include-header.js"></script>
  <script src="/js/include-footer.js"></script>
</body>
</html>
"""


def render_archive(posts: list[dict[str, Any]], generated_at: datetime) -> str:
    cards_html = render_report_cards(posts)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Stock Sentiment Report Archive | Stock Sentiment Score</title>
  <meta name="description" content="Full archive of AI stock sentiment reports published by Stock Sentiment Score." />
  <link rel="canonical" href="{SITE_URL}/blog/archive.html" />
  <link rel="stylesheet" href="/style.css" />
</head>
<body>
  <div id="site-header"></div>
  <header class="hero hero-small blog-hero">
    <div class="hero-inner">
      <p class="eyebrow">Market Insight Blog</p>
      <h1>Report Archive</h1>
      <p class="hero-text">Complete history of generated stock sentiment reports. Last updated {escape(date_label(generated_at))}.</p>
    </div>
  </header>
  <main class="container content-page blog-page">
    <section class="content-card blog-featured-card">
      <div class="blog-section-top">
        <div>
          <h2>All Reports</h2>
          <p>{len(posts)} total reports. <a href="/blog/index.html">Back to latest feed</a>.</p>
        </div>
      </div>
      <div class="blog-report-grid blog-report-grid-compact">{cards_html}</div>
    </section>
  </main>
  <div id="site-footer"></div>
  <script src="/js/include-header.js"></script>
  <script src="/js/include-footer.js"></script>
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

    BLOG_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    posts = manifest.get("posts", [])

    # Encourage variety: avoid recently used tickers first
    recent_tickers = {
        p.get("ticker")
        for p in posts[:30]
        if isinstance(p, dict) and p.get("ticker")
    }

    fresh = [c for c in candidates if c.get("ticker") not in recent_tickers]
    selected = (fresh[:limit] if len(fresh) >= limit else (fresh + candidates)[:limit])

    used_images: set[str] = set()

    for stock in selected:
        # Include time so intraday runs don't overwrite same-day ticker pages
        slug = f"{stock['ticker'].lower()}-news-impact-{generated_at.strftime('%Y-%m-%d-%H%M')}"

        article = fallback_article(stock, generated_at)
        if not args.mock:
            try:
                article = generate_openai_article(stock, openai_api_key, args.model, generated_at)
            except Exception:
                pass

        image_url = (
            stock.get("image_url")
            or pick_best_image(stock.get("mentions", []))
            or fallback_image_for_ticker(stock["ticker"])
        )

        if image_url in used_images:
            image_url = fallback_image_for_ticker(stock["ticker"])
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
    ARCHIVE_PATH.write_text(render_archive(posts, generated_at), encoding="utf-8")
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
