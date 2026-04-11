import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import urllib.request
import urllib.error
import urllib.parse

ROOT = Path(__file__).resolve().parent.parent
BLOG_DIR = ROOT / "blog"
INDEX_PATH = BLOG_DIR / "index.html"
MANIFEST_PATH = ROOT / "data" / "blog-manifest.json"

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

try:
    MAX_POSTS = int(os.getenv("MAX_POSTS", "6"))
except ValueError:
    MAX_POSTS = 6

OPENAI_API_URL = "https://api.openai.com/v1/responses"
FINNHUB_BASE = "https://finnhub.io/api/v1"

STOPWORDS = {
    "A", "AN", "THE", "AND", "OR", "FOR", "WITH", "FROM", "BY", "ON", "IN",
    "TO", "OF", "US", "USA", "ETF", "ETFS", "CEO", "CFO", "AI", "IPO", "SEC",
    "GDP", "CPI", "FED", "FOMC", "SP", "S&P", "DJIA", "NYSE", "NASDAQ", "RALLY",
    "MARKET", "STOCK", "STOCKS", "SHARES", "NEWS", "TODAY", "WEEK", "MONTH"
}

POSITIVE_WORDS = {
    "beat", "beats", "surge", "surges", "strong", "growth", "record", "profit",
    "profits", "upgrade", "upgrades", "wins", "win", "bullish", "rebound", "gains"
}
NEGATIVE_WORDS = {
    "miss", "misses", "drop", "drops", "fall", "falls", "slump", "warning",
    "warnings", "downgrade", "downgrades", "lawsuit", "probe", "bearish", "risk", "risks"
}


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def date_label(dt_or_iso) -> str:
    dt = datetime.fromisoformat(dt_or_iso.replace("Z", "+00:00")) if isinstance(dt_or_iso, str) else dt_or_iso
    return dt.strftime("%B %d, %Y")


def ymd(dt_or_iso) -> str:
    dt = datetime.fromisoformat(dt_or_iso.replace("Z", "+00:00")) if isinstance(dt_or_iso, str) else dt_or_iso
    return dt.strftime("%Y-%m-%d")


def http_get_json(url: str):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def finnhub_get(path: str, params: dict):
    encoded = urllib.parse.urlencode(params)
    url = f"{FINNHUB_BASE}/{path}?{encoded}&token={FINNHUB_API_KEY}"
    return http_get_json(url)


def load_manifest():
    if MANIFEST_PATH.exists():
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        posts = data.get("posts", [])
        # Backfill legacy entries
        for p in posts:
            if "published_date" not in p:
                p["published_date"] = p.get("date", "Unknown")
        data["posts"] = posts
        return data
    return {"generated_at": None, "posts": []}


def save_manifest(manifest):
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def extract_ticker_candidates(news_items):
    counter = Counter()
    for item in news_items:
        text = f"{item.get('headline', '')} {item.get('summary', '')}"
        matches = re.findall(r"\b[A-Z]{1,5}\b", text.upper())
        for m in matches:
            if m in STOPWORDS or len(m) <= 1:
                continue
            counter[m] += 1
    return counter


def validate_ticker(symbol: str):
    try:
        q = finnhub_get("quote", {"symbol": symbol})
        price = q.get("c")
        if not (isinstance(price, (int, float)) and price > 0):
            return None

        p = finnhub_get("stock/profile2", {"symbol": symbol})
        return {
            "ticker": symbol,
            "company_name": p.get("name") or symbol,
            "industry": p.get("finnhubIndustry") or "N/A",
            "price": q.get("c"),
            "change": q.get("d"),
            "percent_change": q.get("dp"),
        }
    except Exception:
        return None


def score_news_for_ticker(symbol: str, company_name: str, news_items):
    comp = (company_name or "").lower()
    sym = symbol.lower()
    mentions = []
    score = 0

    for item in news_items:
        h = item.get("headline") or ""
        s = item.get("summary") or ""
        text = f"{h} {s}".lower()

        if sym in text or (comp and comp in text):
            mentions.append(item)
            words = set(re.findall(r"[a-z]+", text))
            score += len(words.intersection(POSITIVE_WORDS))
            score -= len(words.intersection(NEGATIVE_WORDS))

    sentiment = "Neutral"
    if score >= 2:
        sentiment = "Bullish"
    elif score <= -2:
        sentiment = "Bearish"

    confidence = "Low"
    if len(mentions) >= 6:
        confidence = "High"
    elif len(mentions) >= 3:
        confidence = "Moderate"

    return {
        "mentions": mentions[:10],
        "sentiment_score": score,
        "sentiment": sentiment,
        "confidence": confidence,
    }


def generate_ai_article(stock_payload, generated_at, market_data_as_of):
    prompt = {
        "generated_at": generated_at,
        "market_data_as_of": market_data_as_of,
        "ticker": stock_payload["ticker"],
        "company_name": stock_payload["company_name"],
        "industry": stock_payload["industry"],
        "price": stock_payload.get("price"),
        "change": stock_payload.get("change"),
        "percent_change": stock_payload.get("percent_change"),
        "sentiment": stock_payload.get("sentiment"),
        "sentiment_score": stock_payload.get("sentiment_score"),
        "confidence": stock_payload.get("confidence"),
        "news": stock_payload.get("news", [])[:8],
    }

    body = {
        "model": OPENAI_MODEL,
        "input": [
            {
                "role": "system",
                "content": (
                    "Write a high-quality SEO stock sentiment post using ONLY provided data. "
                    "Return strict JSON keys: title, meta_description, excerpt, body_html, faq. "
                    "body_html may use only <h2>, <p>, <ul>, <li>. "
                    "faq must have 3 objects with q and a. No investment advice."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "text": {"format": {"type": "json_object"}},
    }

    req = urllib.request.Request(
        OPENAI_API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    out = (data.get("output_text") or "").strip()
    if out:
        return json.loads(out)

    for item in data.get("output", []):
        for c in item.get("content", []):
            if c.get("type") == "output_text" and c.get("text"):
                return json.loads(c["text"])

    raise RuntimeError("OpenAI response missing output_text")


def render_post_html(stock, ai, generated_at, market_data_as_of, slug):
    generated_label = date_label(generated_at)
    market_label = date_label(market_data_as_of)

    ticker = escape(stock["ticker"])
    company = escape(stock["company_name"])
    industry = escape(stock["industry"])
    sentiment = escape(stock["sentiment"])
    score = int(stock["sentiment_score"])
    confidence = escape(stock["confidence"])
    price = stock.get("price")
    change = stock.get("change")
    pct = stock.get("percent_change")

    price_text = f"${price:.2f}" if isinstance(price, (int, float)) else "N/A"
    change_text = f"{change:+.2f}" if isinstance(change, (int, float)) else "N/A"
    pct_text = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "N/A"

    title = escape(ai.get("title", f"{ticker} Stock Sentiment Report"))
    desc = escape(ai.get("meta_description", f"{ticker} sentiment analysis"))
    excerpt = escape(ai.get("excerpt", ""))
    body_html = ai.get("body_html", "<p>No body generated.</p>")
    faq = ai.get("faq", [])

    faq_html = ""
    faq_schema_items = []
    for item in faq[:3]:
        q = str(item.get("q", "")).strip()
        a = str(item.get("a", "")).strip()
        if not q or not a:
            continue
        faq_html += f"<h3>{escape(q)}</h3><p>{escape(a)}</p>"
        faq_schema_items.append({
            "@type": "Question",
            "name": q,
            "acceptedAnswer": {"@type": "Answer", "text": a},
        })

    article_schema = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": f"{ai.get('title', ticker)} ({generated_label})",
        "description": ai.get("meta_description", ""),
        "datePublished": generated_at,
        "dateModified": generated_at,
        "author": {"@type": "Organization", "name": "Stock Sentiment Score"},
        "mainEntityOfPage": f"https://www.stocksentimentscore.com/blog/{slug}.html",
    }

    faq_schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": faq_schema_items,
    }

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title} ({generated_label}) | Stock Sentiment Score</title>
  <meta name="description" content="{desc}" />
  <link rel="stylesheet" href="/style.css" />
  <script type="application/ld+json">{json.dumps(article_schema)}</script>
  <script type="application/ld+json">{json.dumps(faq_schema)}</script>
</head>
<body>
  <div id="site-header"></div>
  <header class="hero hero-small blog-hero">
    <div class="hero-inner">
      <p class="eyebrow">AI Sentiment Report</p>
      <h1>{title} ({generated_label})</h1>
      <p class="hero-text"><strong>Generated:</strong> {generated_label} | <strong>Market data as of:</strong> {market_label}</p>
    </div>
  </header>

  <main class="container content-page blog-article-page">
    <article class="content-card blog-article-card">
      <div class="blog-article-meta">
        <span class="blog-article-pill">{ticker}</span>
        <span class="blog-article-pill">{company}</span>
        <span class="blog-article-pill">{industry}</span>
      </div>
      <p>{excerpt}</p>
      <p><strong>Sentiment:</strong> {sentiment} ({score:+d}) | <strong>Confidence:</strong> {confidence}</p>
      <p><strong>Price:</strong> {price_text} | <strong>Daily Change:</strong> {change_text} ({pct_text})</p>
      {body_html}
      <h2>FAQ</h2>
      {faq_html}
      <div class="blog-article-note"><strong>Important:</strong> Educational only. Not financial advice.</div>
    </article>
  </main>

  <div id="site-footer"></div>
  <script src="/js/include-header.js"></script>
  <script src="/js/include-footer.js"></script>
</body>
</html>
"""


def render_index(posts, generated_at):
    generated_label = date_label(generated_at)
    cards = []
    for p in posts[:80]:
        cards.append(
            f"""
            <article class="blog-report-card">
              <div class="blog-report-tag">{escape(p.get('sentiment', 'Neutral'))} ({int(p.get('score', 0)):+d})</div>
              <h3><a href="{escape(p.get('href', '#'))}">{escape(p.get('title', 'Untitled'))}</a></h3>
              <p>{escape(p.get('excerpt', ''))}</p>
              <p><small>Published: {escape(str(p.get('published_date', 'Unknown')))}</small></p>
              <a class="blog-report-link" href="{escape(p.get('href', '#'))}">Read report →</a>
            </article>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Stock Sentiment Blog | Stock Sentiment Score</title>
  <meta name="description" content="News-driven AI stock sentiment posts generated from current events." />
  <link rel="stylesheet" href="/style.css" />
</head>
<body>
  <div id="site-header"></div>
  <header class="hero hero-small blog-hero">
    <div class="hero-inner">
      <p class="eyebrow">News-Driven AI Blog</p>
      <h1>Stock Sentiment Blog</h1>
      <p class="hero-text">Latest generation: {generated_label}</p>
    </div>
  </header>
  <main class="container content-page blog-page">
    <section class="content-card blog-featured-card">
      <div class="blog-section-top"><h2>Latest Relevant News Posts</h2></div>
      <div class="blog-report-grid">{''.join(cards)}</div>
    </section>
  </main>
  <div id="site-footer"></div>
  <script src="/js/include-header.js"></script>
  <script src="/js/include-footer.js"></script>
</body>
</html>
"""


def main():
    if not FINNHUB_API_KEY:
        raise RuntimeError("Missing FINNHUB_API_KEY")
    if not OPENAI_API_KEY:
        raise RuntimeError("Missing OPENAI_API_KEY")

    generated_at = iso(now_utc())
    market_data_as_of = generated_at
    day = ymd(generated_at)

    manifest = load_manifest()
    posts = manifest.get("posts", [])

    general_news = finnhub_get("news", {"category": "general"})
    if not isinstance(general_news, list):
        raise RuntimeError("Finnhub general news response invalid")

    counts = extract_ticker_candidates(general_news)

    candidates = []
    for symbol, mention_count in counts.most_common(60):
        validated = validate_ticker(symbol)
        if not validated:
            continue

        ticker_news = score_news_for_ticker(symbol, validated["company_name"], general_news)
        if len(ticker_news["mentions"]) == 0:
            continue

        impact_score = mention_count * 2 + abs(ticker_news["sentiment_score"]) + len(ticker_news["mentions"])
        candidate = {**validated, **ticker_news, "impact_score": impact_score}
        candidates.append(candidate)

    candidates.sort(key=lambda x: x["impact_score"], reverse=True)
    selected = candidates[:MAX_POSTS]

    BLOG_DIR.mkdir(exist_ok=True)

    for stock in selected:
        ticker = stock["ticker"]

        already_today = any(
            p.get("ticker") == ticker and p.get("published_date") == day
            for p in posts
        )
        if already_today:
            continue

        ai = generate_ai_article(
            {
                "ticker": stock["ticker"],
                "company_name": stock["company_name"],
                "industry": stock["industry"],
                "price": stock.get("price"),
                "change": stock.get("change"),
                "percent_change": stock.get("percent_change"),
                "sentiment": stock["sentiment"],
                "sentiment_score": stock["sentiment_score"],
                "confidence": stock["confidence"],
                "news": stock["mentions"],
            },
            generated_at=generated_at,
            market_data_as_of=market_data_as_of,
        )

        archive_slug = f"{ticker.lower()}-news-impact-{day}"
        evergreen_slug = f"{ticker.lower()}-sentiment"

        archive_html = render_post_html(stock, ai, generated_at, market_data_as_of, archive_slug)
        evergreen_html = render_post_html(stock, ai, generated_at, market_data_as_of, evergreen_slug)

        (BLOG_DIR / f"{archive_slug}.html").write_text(archive_html, encoding="utf-8")
        (BLOG_DIR / f"{evergreen_slug}.html").write_text(evergreen_html, encoding="utf-8")

        posts.append({
            "ticker": ticker,
            "title": ai.get("title", f"{ticker} News Impact Sentiment"),
            "excerpt": ai.get("excerpt", ""),
            "sentiment": stock["sentiment"],
            "score": stock["sentiment_score"],
            "href": f"/blog/{archive_slug}.html",
            "published_date": day,
            "generated_at": generated_at,
        })

    posts = sorted(posts, key=lambda x: x.get("generated_at", ""), reverse=True)
    INDEX_PATH.write_text(render_index(posts, generated_at), encoding="utf-8")

    manifest["generated_at"] = generated_at
    manifest["market_data_as_of"] = market_data_as_of
    manifest["model"] = OPENAI_MODEL
    manifest["posts"] = posts
    save_manifest(manifest)

    print(f"Done. Selected {len(selected)} candidates from current news.")


if __name__ == "__main__":
    main()
