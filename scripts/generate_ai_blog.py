import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
BLOG_DIR = ROOT / "blog"
INDEX_PATH = BLOG_DIR / "index.html"
MANIFEST_PATH = ROOT / "data" / "blog-manifest.json"

FINNHUB_API_KEY = (os.getenv("FINNHUB_API_KEY") or "").strip()
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_MODEL = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()

BLOG_RUN_MODE = (os.getenv("BLOG_RUN_MODE") or "morning").strip().lower()  # morning | intraday
MORNING_POSTS = int(os.getenv("MORNING_POSTS", "6"))
INTRADAY_POSTS = int(os.getenv("INTRADAY_POSTS", "2"))
MAX_ARCHIVE_POSTS = int(os.getenv("MAX_ARCHIVE_POSTS", "120"))
BLOG_MOCK = (os.getenv("BLOG_MOCK", "false").lower() == "true")

OPENAI_API_URL = "https://api.openai.com/v1/responses"
FINNHUB_BASE = "https://finnhub.io/api/v1"

STOPWORDS = {
    "A", "AN", "THE", "AND", "OR", "FOR", "WITH", "FROM", "BY", "ON", "IN",
    "TO", "OF", "US", "USA", "ETF", "ETFS", "CEO", "CFO", "AI", "IPO", "SEC",
    "GDP", "CPI", "FED", "FOMC", "SP", "DJIA", "NYSE", "NASDAQ", "RALLY",
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


def now_iso():
    return now_utc().isoformat()


def to_dt(v):
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    return now_utc()


def date_label(dt_or_iso) -> str:
    return to_dt(dt_or_iso).strftime("%B %d, %Y")


def ymd(dt_or_iso) -> str:
    return to_dt(dt_or_iso).strftime("%Y-%m-%d")


def hhmm(dt_or_iso) -> str:
    return to_dt(dt_or_iso).strftime("%H%M")


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
        try:
            data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            posts = data.get("posts", [])
            if isinstance(posts, list):
                for p in posts:
                    if "published_date" not in p:
                        p["published_date"] = p.get("date", "Unknown")
                    if "generated_at" not in p:
                        p["generated_at"] = data.get("generated_at") or now_iso()
                    if "image_url" not in p:
                        p["image_url"] = ""
                data["posts"] = posts
                return data
        except Exception:
            pass
    return {"generated_at": None, "posts": []}


def save_manifest(manifest):
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def is_valid_symbol(sym: str) -> bool:
    if not sym:
        return False
    if sym in STOPWORDS:
        return False
    if len(sym) < 1 or len(sym) > 5:
        return False
    if not sym.isalpha():
        return False
    return True


def extract_ticker_candidates(news_items):
    """
    Prefer Finnhub's `related` field (comma-separated symbols), fallback to regex.
    """
    counter = Counter()

    for item in news_items:
        related = (item.get("related") or "").upper().strip()
        if related:
            for raw in related.split(","):
                sym = raw.strip().upper()
                if is_valid_symbol(sym):
                    counter[sym] += 2

        text = f"{item.get('headline', '')} {item.get('summary', '')}".upper()
        for m in re.findall(r"\b[A-Z]{1,5}\b", text):
            if is_valid_symbol(m):
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
        headline = item.get("headline") or ""
        summary = item.get("summary") or ""
        text = f"{headline} {summary}".lower()
        related = (item.get("related") or "").lower()

        if sym in text or (comp and comp in text) or sym in related:
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
        "mentions": mentions[:12],
        "sentiment_score": score,
        "sentiment": sentiment,
        "confidence": confidence,
    }


def call_openai(stock_payload, generated_at):
    prompt = {
        "generated_at": generated_at,
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
                    "Write a concise stock sentiment report for retail readers. "
                    "Use ONLY provided data. No invented facts. No financial advice. "
                    "Return strict JSON keys: title, meta_description, excerpt, body_html. "
                    "body_html must use only <h2>, <p>, <ul>, <li>."
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

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenAI API HTTP {exc.code}: {detail}") from exc

    out = (data.get("output_text") or "").strip()
    if out:
        return json.loads(out)

    for item in data.get("output", []):
        for c in item.get("content", []):
            if c.get("type") == "output_text" and c.get("text"):
                return json.loads(c["text"])

    raise RuntimeError("OpenAI response missing output text")


def mock_ai(stock):
    ticker = stock.get("ticker", "UNKNOWN")
    sentiment = stock.get("sentiment", "Neutral")
    score = int(stock.get("sentiment_score", 0))
    return {
        "title": f"{ticker} Sentiment Update: {sentiment} ({score:+d})",
        "meta_description": f"{ticker} sentiment update with score, confidence, and key context.",
        "excerpt": f"{ticker} currently reads {sentiment} at {score:+d} based on current news flow.",
        "body_html": (
            f"<h2>What happened</h2><p>{escape(ticker)} is currently reading {escape(sentiment)} at {score:+d}.</p>"
            "<h2>Why it matters</h2><p>Sentiment helps with context and should be paired with fundamentals and risk management.</p>"
            "<h2>What to watch next</h2><ul>"
            "<li>Upcoming earnings and guidance</li>"
            "<li>Sector and macro headlines</li>"
            "<li>Regulatory or demand shifts</li>"
            "</ul>"
        ),
    }


def pick_best_image(mentions):
    """
    Prefer non-generic story images; skip wire/logo placeholders.
    """
    if not mentions:
        return ""

    bad_patterns = [
        "reuters",
        "logo",
        "placeholder",
        "default",
        "no-image",
        "icon",
        "brand",
    ]

    for item in mentions:
        img = (item.get("image") or "").strip()
        if not img:
            continue

        low = img.lower()
        if any(p in low for p in bad_patterns):
            continue

        return img

    return ""


def render_post(stock, ai, generated_at):
    ticker = escape(stock.get("ticker", "UNKNOWN"))
    company = escape(stock.get("company_name", ticker))
    industry = escape(stock.get("industry", "N/A"))
    sentiment = escape(stock.get("sentiment", "Neutral"))
    score = int(stock.get("sentiment_score", 0))
    confidence = escape(stock.get("confidence", "Low"))
    image_url = (stock.get("image_url") or "").strip()

    price = stock.get("price")
    change = stock.get("change")
    pct = stock.get("percent_change")

    price_text = f"${price:.2f}" if isinstance(price, (int, float)) else "N/A"
    change_text = f"{change:+.2f}" if isinstance(change, (int, float)) else "N/A"
    pct_text = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "N/A"

    title = escape(ai.get("title", f"{ticker} Stock Sentiment Report"))
    description = escape(ai.get("meta_description", f"{ticker} stock sentiment report"))
    excerpt = escape(ai.get("excerpt", ""))
    body_html = ai.get("body_html", "<p>No content generated.</p>")

    image_html = ""
    if image_url:
        image_html = f'<img src="{escape(image_url)}" alt="{ticker} market image" class="news-card-img" loading="lazy" />'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title} | Stock Sentiment Score</title>
  <meta name="description" content="{description}" />
  <link rel="stylesheet" href="/style.css" />
</head>
<body>
  <div id="site-header"></div>

  <header class="hero hero-small blog-hero">
    <div class="hero-inner">
      <p class="eyebrow">Market Desk Report</p>
      <h1>{title}</h1>
      <p class="hero-text">Published {date_label(generated_at)}</p>
    </div>
  </header>

  <main class="container content-page blog-article-page">
    <article class="content-card blog-article-card">
      {image_html}
      <div class="blog-article-meta">
        <span class="blog-article-pill">{ticker}</span>
        <span class="blog-article-pill">{company}</span>
        <span class="blog-article-pill">{industry}</span>
      </div>
      <p>{excerpt}</p>
      <p><strong>Sentiment:</strong> {sentiment} ({score:+d}) | <strong>Confidence:</strong> {confidence}</p>
      <p><strong>Price:</strong> {price_text} | <strong>Daily Change:</strong> {change_text} ({pct_text})</p>
      {body_html}
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
    # display de-dupe by ticker (keep newest per ticker)
    deduped = []
    seen_tickers = set()
    for p in posts:
        t = str(p.get("ticker", "")).upper()
        if t in seen_tickers:
            continue
        seen_tickers.add(t)
        deduped.append(p)

    lead = deduped[0] if deduped else None
    rest = deduped[1:13] if len(deduped) > 1 else []

    lead_html = ""
    if lead:
        lead_html = f"""
        <section class="news-lead">
          <div class="news-lead-text">
            <div class="news-kicker">TOP STORY</div>
            <h2><a href="{escape(lead.get('href', '#'))}">{escape(lead.get('title', 'Untitled'))}</a></h2>
            <p>{escape(lead.get('excerpt', ''))}</p>
          </div>
        </section>
        """

    cards = []
    for p in rest:
        img_html = ""
        if p.get("image_url"):
            img_html = f'<img src="{escape(p["image_url"])}" alt="{escape(p.get("title","news image"))}" class="news-card-img" loading="lazy" />'

        cards.append(
            f"""
            <article class="news-card">
              {img_html}
              <div class="news-card-kicker">{escape(p.get('ticker', 'NEWS'))}</div>
              <h3><a href="{escape(p.get('href', '#'))}">{escape(p.get('title', 'Untitled'))}</a></h3>
              <p>{escape(p.get('excerpt', ''))}</p>
              <a class="blog-report-link" href="{escape(p.get('href', '#'))}">Read report →</a>
            </article>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Market Desk | Stock Sentiment Score</title>
  <meta name="description" content="News-style stock sentiment desk with frequent market updates." />
  <link rel="stylesheet" href="/style.css" />
</head>
<body>
  <div id="site-header"></div>

  <div class="news-topbar">
    <div class="news-topbar-inner">
      <div class="news-brand">Market Desk</div>
      <div class="news-trending">Trending: AI • Earnings • Rates • Regulation • Macro | Updated {date_label(generated_at)}</div>
    </div>
  </div>

  <main class="news-shell">
    {lead_html}
    <section class="news-grid">{''.join(cards)}</section>
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
    if not BLOG_MOCK and not OPENAI_API_KEY:
        raise RuntimeError("Missing OPENAI_API_KEY (or set BLOG_MOCK=true)")

    generated_at = now_iso()
    today = ymd(generated_at)

    manifest = load_manifest()
    existing_posts = manifest.get("posts", [])

    run_count = MORNING_POSTS if BLOG_RUN_MODE == "morning" else INTRADAY_POSTS

    general_news = finnhub_get("news", {"category": "general"})
    if not isinstance(general_news, list):
        raise RuntimeError("Finnhub general news response invalid")

    counts = extract_ticker_candidates(general_news)

    candidates = []
    for symbol, mention_count in counts.most_common(150):
        validated = validate_ticker(symbol)
        if not validated:
            continue

        ticker_news = score_news_for_ticker(symbol, validated["company_name"], general_news)
        if not ticker_news["mentions"]:
            continue

        impact_score = mention_count * 2 + abs(ticker_news["sentiment_score"]) + len(ticker_news["mentions"])
        item = {**validated, **ticker_news, "impact_score": impact_score}
        candidates.append(item)

    # sort by impact
    candidates.sort(key=lambda x: x["impact_score"], reverse=True)

    # hard de-dupe ticker selection per run
    selected = []
    seen_tickers = set()
    for c in candidates:
        t = c["ticker"]
        if t in seen_tickers:
            continue

        # intraday: avoid flooding same ticker if already posted twice today
        if BLOG_RUN_MODE == "intraday":
            already_today = sum(
                1 for p in existing_posts
                if p.get("ticker") == t and p.get("published_date") == today
            )
            if already_today >= 2:
                continue

        selected.append(c)
        seen_tickers.add(t)

        if len(selected) >= run_count:
            break

    BLOG_DIR.mkdir(exist_ok=True)

    new_posts = []
    for stock in selected:
        ticker = stock["ticker"]

        ai = mock_ai(stock) if BLOG_MOCK else call_openai(
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
        )

        stamp = f"{today}-{hhmm(now_iso())}"
        file_name = f"{ticker.lower()}-sentiment-{stamp}.html"
        href = f"/blog/{file_name}"

        image_url = pick_best_image(stock.get("mentions", []))
        stock["image_url"] = image_url

        html = render_post(stock, ai, generated_at)
        (BLOG_DIR / file_name).write_text(html, encoding="utf-8")
        print(f"Generated blog/{file_name}")

        new_posts.append({
            "ticker": ticker,
            "href": href,
            "title": ai.get("title", f"{ticker} Stock Sentiment Report"),
            "excerpt": ai.get("excerpt", ""),
            "sentiment": stock.get("sentiment", "Neutral"),
            "score": int(stock.get("sentiment_score", 0)),
            "published_date": today,
            "generated_at": now_iso(),
            "image_url": image_url,
        })

    all_posts = sorted(new_posts + existing_posts, key=lambda x: x.get("generated_at", ""), reverse=True)
    all_posts = all_posts[:MAX_ARCHIVE_POSTS]

    INDEX_PATH.write_text(render_index(all_posts, generated_at), encoding="utf-8")

    manifest["generated_at"] = generated_at
    manifest["source"] = "finnhub_general_news"
    manifest["model"] = OPENAI_MODEL
    manifest["mode"] = BLOG_RUN_MODE
    manifest["posts"] = all_posts
    save_manifest(manifest)

    print(
        f"[ai-blog] mode={BLOG_RUN_MODE} candidates={len(candidates)} "
        f"selected={len(selected)} new_posts={len(new_posts)} total_posts={len(all_posts)}"
    )
    print("Generated blog/index.html")
    print("Generated data/blog-manifest.json")


if __name__ == "__main__":
    main()
