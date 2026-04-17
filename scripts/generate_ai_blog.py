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

BLOG_RUN_MODE = (os.getenv("BLOG_RUN_MODE") or "morning").strip().lower()
MORNING_POSTS = int(os.getenv("MORNING_POSTS", "6"))
INTRADAY_POSTS = int(os.getenv("INTRADAY_POSTS", "2"))
MAX_ARCHIVE_POSTS = int(os.getenv("MAX_ARCHIVE_POSTS", "120"))
BLOG_MOCK = (os.getenv("BLOG_MOCK", "false").lower() == "true")

# quality filters
MIN_PRICE = float(os.getenv("MIN_PRICE", "25"))
MIN_MENTIONS = int(os.getenv("MIN_MENTIONS", "2"))
BLOCKED_TICKERS = {"S", "WAR", "AI", "IONQ"}

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
        "news": stock_payload.get("news", [])[:12],
    }

    body = {
        "model": OPENAI_MODEL,
        "input": [
            {
                "role": "system",
                "content": (
                    "You are writing a high-quality financial news analysis article for retail investors. "
                    "Use ONLY the provided data/news. Do not invent facts. No financial advice. "
                    "Return strict JSON keys: title, meta_description, excerpt, body_html. "
                    "body_html must use only <h2>, <p>, <ul>, <li>. "
                    "Minimum 900 words in body_html. "
                    "Include these sections in order: "
                    "1) What happened today, "
                    "2) Why this matters for investors, "
                    "3) Bull case, "
                    "4) Bear case, "
                    "5) Key headlines and what they imply, "
                    "6) Industry and macro context, "
                    "7) What to watch next (earnings window, guidance risks, catalysts), "
                    "8) Bottom line summary. "
                    "Use concrete numbers from payload whenever available. "
                    "Write clearly and factually."
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
    Choose best non-generic image from mention list.
    Returns "" if only generic/wire/logo images are available.
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
        "static",
    ]

    for item in mentions:
        img = (item.get("image") or "").strip()
        if not img:
            continue

        low = img.lower()

        # skip obvious generic/wire art
        if any(p in low for p in bad_patterns):
            continue

        # skip non-http(s)
        if not (low.startswith("http://") or low.startswith("https://")):
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
    # de-dupe display by ticker (newest only)
    deduped = []
    seen_tickers = set()
    for p in posts:
        t = str(p.get("ticker", "")).upper()
        if not t or t in seen_tickers:
            continue
        seen_tickers.add(t)
        deduped.append(p)

    # de-dupe repeated image URLs across displayed cards
    used_images = set()
    normalized = []
    for p in deduped:
        item = dict(p)
        img = (item.get("image_url") or "").strip()
        if img:
            if img in used_images:
                item["image_url"] = ""  # remove repeated image, keep card
            else:
                used_images.add(img)
        normalized.append(item)

    lead = normalized[0] if normalized else None
    rest = normalized[1:13] if len(normalized) > 1 else []

    lead_html = ""
    if lead:
        lead_img = ""
        if lead.get("image_url"):
            lead_img = f'<img src="{escape(lead["image_url"])}" alt="{escape(lead.get("title","Top story image"))}" class="md-lead-img" loading="lazy" />'

        lead_html = f"""
        <section class="md-lead">
          <div class="md-kicker">TOP STORY</div>
          <h2><a href="{escape(lead.get('href', '#'))}">{escape(lead.get('title', 'Untitled'))}</a></h2>
          <p>{escape(lead.get('excerpt', ''))}</p>
          {lead_img}
        </section>
        """

    cards_html = []
    for p in rest:
        img_html = ""
        if p.get("image_url"):
            img_html = f'<img src="{escape(p["image_url"])}" alt="{escape(p.get("title","News image"))}" class="md-card-img" loading="lazy" />'

        cards_html.append(f"""
        <article class="md-card">
          {img_html}
          <div class="md-pill">{escape(p.get('ticker', 'NEWS'))} • {escape(p.get('sentiment', 'Neutral'))}</div>
          <h3><a href="{escape(p.get('href', '#'))}">{escape(p.get('title', 'Untitled'))}</a></h3>
          <p>{escape(p.get('excerpt', ''))}</p>
          <div class="md-date">{escape(str(p.get('published_date', '')))}</div>
          <a class="md-btn" href="{escape(p.get('href', '#'))}">Read report →</a>
        </article>
        """)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Market Desk | Stock Sentiment Score</title>
  <meta name="description" content="Fresh ticker-impact stories generated from current market news." />
  <link rel="stylesheet" href="/style.css" />
  <style>
    .md-wrap {{ max-width: 1280px; margin: 0 auto; padding: 18px 20px 40px; }}
    .md-top {{
      border-top: 3px solid #cf2027;
      background: linear-gradient(90deg,#07265a,#1b2f7f);
      color: #fff;
      padding: 10px 14px;
      margin-bottom: 14px;
      border-radius: 6px;
      font-weight: 700;
      display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap;
    }}
    .md-lead {{
      background:#f4f7ff; border:1px solid #d9e2f3; border-left:6px solid #cf2027;
      border-radius:10px; padding:16px; margin-bottom:18px; color:#102445;
    }}
    .md-kicker {{ color:#cf2027; font-weight:800; letter-spacing:.04em; margin-bottom:6px; font-size:.82rem; }}
    .md-lead h2 {{ margin:0 0 8px; font-size:2.2rem; line-height:1.05; }}
    .md-lead h2 a {{ color:#173b7a; text-decoration:none; }}
    .md-lead p {{ margin:0 0 12px; color:#334968; font-size:1.12rem; }}
    .md-lead-img {{ width:100%; height:320px; object-fit:cover; border-radius:8px; }}
    .md-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; }}
    .md-card {{ background:#f4f7ff; border:1px solid #d9e2f3; border-radius:12px; padding:14px; color:#1f3558; }}
    .md-card-img {{ width:100%; height:170px; object-fit:cover; border-radius:8px; margin-bottom:10px; }}
    .md-pill {{ display:inline-block; padding:6px 12px; border-radius:999px; background:#e7eefc; border:1px solid #bfd0f2; color:#365ac8; font-weight:800; margin-bottom:8px; }}
    .md-card h3 {{ margin:0 0 8px; font-size:2rem; line-height:1.05; }}
    .md-card h3 a {{ color:#142b58; text-decoration:none; }}
    .md-card h3 a:visited {{ color:#142b58; }}
    .md-card p {{ margin:0 0 10px; color:#445a7b; font-size:1.02rem; }}
    .md-date {{ color:#6a7f9d; font-weight:700; margin-bottom:10px; }}
    .md-btn {{
      display:inline-flex; align-items:center; justify-content:center;
      min-height:44px; padding:10px 18px; border-radius:999px;
      background:#d91f2a; color:#fff !important; font-weight:800; text-decoration:none;
    }}
    .md-btn:hover {{ background:#b81720; }}
    @media (max-width:1000px) {{ .md-grid{{grid-template-columns:1fr 1fr;}} .md-lead h2{{font-size:1.7rem;}} }}
    @media (max-width:680px) {{ .md-grid{{grid-template-columns:1fr;}} .md-lead h2{{font-size:1.4rem;}} .md-lead-img{{height:220px;}} }}
  </style>
</head>
<body>
  <div id="site-header"></div>

  <div class="md-wrap">
    <div class="md-top">
      <div>Market Desk</div>
      <div>Trending: AI • Earnings • Rates • Regulation • Macro | Updated {date_label(generated_at)}</div>
    </div>

    {lead_html}

    <section class="md-grid">
      {''.join(cards_html)}
    </section>
  </div>

  <div id="site-footer"></div>
  <script src="/js/include-header.js"></script>
  <script src="/js/include-footer.js"></script>
</body>
</html>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Market Desk | Stock Sentiment Score</title>
  <meta name="description" content="Fresh ticker-impact stories generated from current market news." />
  <link rel="stylesheet" href="/style.css" />
  <style>
    .md-wrap {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 18px 20px 40px;
    }}
    .md-top {{
      border-top: 3px solid #cf2027;
      background: linear-gradient(90deg,#07265a,#1b2f7f);
      color: #fff;
      padding: 10px 14px;
      margin-bottom: 14px;
      border-radius: 6px;
      font-weight: 700;
      display:flex;
      justify-content:space-between;
      gap: 12px;
      flex-wrap:wrap;
    }}
    .md-lead {{
      background:#f4f7ff;
      border:1px solid #d9e2f3;
      border-left:6px solid #cf2027;
      border-radius:10px;
      padding:16px;
      margin-bottom:18px;
      color:#102445;
    }}
    .md-kicker {{
      color:#cf2027;
      font-weight:800;
      letter-spacing:.04em;
      margin-bottom:6px;
      font-size:.82rem;
    }}
    .md-lead h2 {{
      margin:0 0 8px;
      font-size:2.2rem;
      line-height:1.05;
    }}
    .md-lead h2 a {{
      color:#173b7a;
      text-decoration:none;
    }}
    .md-lead p {{
      margin:0 0 12px;
      color:#334968;
      font-size:1.12rem;
    }}
    .md-lead-img {{
      width:100%;
      height:320px;
      object-fit:cover;
      border-radius:8px;
    }}
    .md-grid {{
      display:grid;
      grid-template-columns:repeat(3,minmax(0,1fr));
      gap:14px;
    }}
    .md-card {{
      background:#f4f7ff;
      border:1px solid #d9e2f3;
      border-radius:12px;
      padding:14px;
      color:#1f3558;
    }}
    .md-card-img {{
      width:100%;
      height:170px;
      object-fit:cover;
      border-radius:8px;
      margin-bottom:10px;
    }}
    .md-pill {{
      display:inline-block;
      padding:6px 12px;
      border-radius:999px;
      background:#e7eefc;
      border:1px solid #bfd0f2;
      color:#365ac8;
      font-weight:800;
      margin-bottom:8px;
    }}
    .md-card h3 {{
      margin:0 0 8px;
      font-size:2rem;
      line-height:1.05;
    }}
    .md-card h3 a {{
      color:#142b58;
      text-decoration:none;
    }}
    .md-card p {{
      margin:0 0 10px;
      color:#445a7b;
      font-size:1.02rem;
    }}
    .md-date {{
      color:#6a7f9d;
      font-weight:700;
      margin-bottom:10px;
    }}
    .md-btn {{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      min-height:44px;
      padding:10px 18px;
      border-radius:999px;
      background:#d91f2a;
      color:#fff !important;
      font-weight:800;
      text-decoration:none;
    }}
    .md-btn:hover {{ background:#b81720; }}
    @media (max-width: 1000px) {{
      .md-grid {{ grid-template-columns:1fr 1fr; }}
      .md-lead h2 {{ font-size:1.7rem; }}
    }}
    @media (max-width: 680px) {{
      .md-grid {{ grid-template-columns:1fr; }}
      .md-lead h2 {{ font-size:1.4rem; }}
      .md-lead-img {{ height:220px; }}
    }}
  </style>
</head>
<body>
  <div id="site-header"></div>

  <div class="md-wrap">
    <div class="md-top">
      <div>Market Desk</div>
      <div>Trending: AI • Earnings • Rates • Regulation • Macro | Updated {date_label(generated_at)}</div>
    </div>

    {lead_html}

    <section class="md-grid">
      {''.join(cards_html)}
    </section>
  </div>

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

        ticker = validated["ticker"].upper()

        # hard blocklist for noisy/irrelevant symbols
        if ticker in BLOCKED_TICKERS:
            continue

        # price floor to avoid low-quality tiny names
        if float(validated.get("price") or 0) < MIN_PRICE:
            continue

        ticker_news = score_news_for_ticker(symbol, validated["company_name"], general_news)

        # require minimum relevance depth
        if len(ticker_news["mentions"]) < MIN_MENTIONS:
            continue

        impact_score = (
            mention_count * 2
            + abs(ticker_news["sentiment_score"])
            + len(ticker_news["mentions"])
        )
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
