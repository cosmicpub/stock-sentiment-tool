import json
import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "market-data.json"
BLOG_DIR = ROOT / "blog"
INDEX_PATH = BLOG_DIR / "index.html"
MANIFEST_PATH = ROOT / "data" / "blog-manifest.json"

OPENAI_API_URL = "https://api.openai.com/v1/responses"
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

# behavior controls
BLOG_RUN_MODE = os.getenv("BLOG_RUN_MODE", "morning").strip().lower()  # morning | intraday
MORNING_POSTS = int(os.getenv("MORNING_POSTS", "6"))
INTRADAY_POSTS = int(os.getenv("INTRADAY_POSTS", "2"))
MAX_ARCHIVE_POSTS = int(os.getenv("MAX_ARCHIVE_POSTS", "120"))
USE_MOCK = os.getenv("BLOG_MOCK", "false").lower() == "true"


def now_utc():
    return datetime.now(timezone.utc)


def now_iso():
    return now_utc().isoformat()


def date_label(iso_ts: str) -> str:
    if not iso_ts:
        return "Unknown"
    return datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).strftime("%B %d, %Y")


def ymd(iso_ts: str) -> str:
    return datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).strftime("%Y-%m-%d")


def hhmm(iso_ts: str) -> str:
    return datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).strftime("%H%M")


def load_market_data():
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    updated_at = payload.get("updated_at") or now_iso()
    stocks = payload.get("stocks", [])
    return updated_at, stocks


def load_manifest():
    if MANIFEST_PATH.exists():
        try:
            data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            posts = data.get("posts", [])
            if isinstance(posts, list):
                return data
        except Exception:
            pass
    return {"generated_at": None, "posts": []}


def save_manifest(manifest):
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def pick_stocks(stocks, count, existing_posts, today):
    # avoid duplicating same ticker too often in same day for intraday runs
    posted_today = {}
    for p in existing_posts:
        if p.get("published_date") == today:
            t = str(p.get("ticker", "")).upper()
            posted_today[t] = posted_today.get(t, 0) + 1

    def rank_key(s):
        sentiment_abs = abs(int(s.get("sentiment_score", 0)))
        confidence = str(s.get("confidence", "Low")).lower()
        conf_weight = {"high": 3, "moderate": 2, "low": 1}.get(confidence, 1)
        news_count = len(s.get("news", []) or [])
        return (sentiment_abs, conf_weight, news_count)

    ranked = sorted(stocks, key=rank_key, reverse=True)

    selected = []
    for s in ranked:
        t = str(s.get("ticker", "")).upper().strip()
        if not t:
            continue

        if BLOG_RUN_MODE == "intraday" and posted_today.get(t, 0) >= 2:
            # avoid flooding same ticker throughout day
            continue

        selected.append(s)
        if len(selected) >= count:
            break

    return selected


def call_openai(stock, updated_at):
    prompt = {
        "updated_at": updated_at,
        "ticker": stock.get("ticker"),
        "company_name": stock.get("company_name"),
        "industry": stock.get("industry"),
        "price": stock.get("price"),
        "change": stock.get("change"),
        "percent_change": stock.get("percent_change"),
        "sentiment": stock.get("sentiment"),
        "sentiment_score": stock.get("sentiment_score"),
        "confidence": stock.get("confidence"),
        "top_drivers": stock.get("top_drivers", []),
        "news": stock.get("news", [])[:8],
    }

    body = {
        "model": OPENAI_MODEL,
        "input": [
            {
                "role": "system",
                "content": (
                    "Write a concise stock sentiment report for retail readers. "
                    "Use only provided data. No financial advice. "
                    "Return strict JSON keys: title, meta_description, excerpt, body_html. "
                    "body_html must only use <h2>, <p>, <ul>, <li>."
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
    t = stock.get("ticker", "UNKNOWN")
    s = stock.get("sentiment", "Neutral")
    sc = int(stock.get("sentiment_score", 0))
    return {
        "title": f"{t} Sentiment Update: {s} ({sc:+d})",
        "meta_description": f"{t} sentiment update with score, confidence, and key context.",
        "excerpt": f"{t} currently reads {s} at {sc:+d} based on recent market context.",
        "body_html": (
            f"<h2>What happened</h2><p>{escape(t)} is currently reading {escape(s)} at {sc:+d}.</p>"
            "<h2>Why it matters</h2><p>Sentiment is a context signal and should be used with fundamentals and price action.</p>"
            "<h2>What to watch next</h2><ul>"
            "<li>Earnings and guidance updates</li>"
            "<li>Sector-wide headlines</li>"
            "<li>Macro and rate expectations</li>"
            "</ul>"
        ),
    }


def render_post(stock, ai, updated_at):
    ticker = escape(stock.get("ticker", "UNKNOWN"))
    company = escape(stock.get("company_name", ticker))
    industry = escape(stock.get("industry", "N/A"))
    sentiment = escape(stock.get("sentiment", "Neutral"))
    score = int(stock.get("sentiment_score", 0))
    confidence = escape(stock.get("confidence", "Low"))

    price = stock.get("price")
    change = stock.get("change")
    pct = stock.get("percent_change")

    price_text = f"${price:.2f}" if isinstance(price, (int, float)) else "N/A"
    change_text = f"{change:+.2f}" if isinstance(change, (int, float)) else "N/A"
    pct_text = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "N/A"

    title = escape(ai.get("title", f"{ticker} Stock Sentiment Report"))
    desc = escape(ai.get("meta_description", f"{ticker} stock sentiment report"))
    excerpt = escape(ai.get("excerpt", ""))
    body_html = ai.get("body_html", "<p>No content generated.</p>")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title} | Stock Sentiment Score</title>
  <meta name="description" content="{desc}" />
  <link rel="stylesheet" href="/style.css" />
</head>
<body>
  <div id="site-header"></div>

  <header class="hero hero-small blog-hero">
    <div class="hero-inner">
      <p class="eyebrow">Market Desk</p>
      <h1>{title}</h1>
      <p class="hero-text">Published {date_label(updated_at)}</p>
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
      <div class="blog-article-note"><strong>Important:</strong> Educational only. Not financial advice.</div>
    </article>
  </main>

  <div id="site-footer"></div>
  <script src="/js/include-header.js"></script>
  <script src="/js/include-footer.js"></script>
</body>
</html>
"""


def render_index(posts, updated_at):
    lead = posts[0] if posts else None
    rest = posts[1:13] if len(posts) > 1 else []

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
        cards.append(
            f"""
            <article class="news-card">
              <div class="news-card-kicker">{escape(p.get('ticker', 'NEWS'))}</div>
              <h3><a href="{escape(p.get('href', '#'))}">{escape(p.get('title', 'Untitled'))}</a></h3>
              <p>{escape(p.get('excerpt', ''))}</p>
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
  <style>
    .news-topbar {{ background:#041b46; color:#fff; border-top:4px solid #cf2027; }}
    .news-topbar-inner {{
      max-width:1200px; margin:0 auto; padding:12px 16px;
      display:flex; gap:16px; align-items:center; justify-content:space-between;
    }}
    .news-brand {{ font-weight:800; letter-spacing:.04em; text-transform:uppercase; }}
    .news-trending {{ font-size:.9rem; opacity:.95; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}

    .news-shell {{ max-width:1200px; margin:22px auto; padding:0 16px; }}
    .news-lead {{
      background:#f5f7fb; border:1px solid #dde3ef; border-left:6px solid #cf2027; margin-bottom:18px;
    }}
    .news-lead-text {{ padding:18px; }}
    .news-kicker {{ color:#cf2027; font-weight:800; margin-bottom:8px; font-size:.85rem; letter-spacing:.04em; }}
    .news-lead h2 {{ margin:0 0 8px; font-size:2.2rem; line-height:1.05; }}
    .news-lead h2 a {{ color:#0a2a5e; text-decoration:none; }}
    .news-lead p {{ margin:0; color:#2a3c5c; }}

    .news-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; }}
    .news-card {{ border:1px solid #dbe1ec; background:#fff; padding:14px; min-height:180px; }}
    .news-card-kicker {{
      display:inline-block; background:#143d78; color:#fff; font-size:.72rem; font-weight:800;
      padding:4px 8px; border-radius:2px; margin-bottom:10px;
    }}
    .news-card h3 {{ margin:0 0 8px; font-size:1.45rem; line-height:1.1; }}
    .news-card h3 a {{ color:#0b2f66; text-decoration:none; }}
    .news-card p {{ margin:0; color:#3b4f71; }}

    @media (max-width:980px) {{ .news-grid {{ grid-template-columns:1fr 1fr; }} .news-lead h2 {{ font-size:1.8rem; }} }}
    @media (max-width:680px) {{ .news-grid {{ grid-template-columns:1fr; }} .news-lead h2 {{ font-size:1.55rem; }} }}
  </style>
</head>
<body>
  <div id="site-header"></div>

  <div class="news-topbar">
    <div class="news-topbar-inner">
      <div class="news-brand">Market Desk</div>
      <div class="news-trending">Trending: AI • Earnings • Rates • Regulation • Macro | Updated {date_label(updated_at)}</div>
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
    updated_at, stocks = load_market_data()
    manifest = load_manifest()
    existing_posts = manifest.get("posts", [])

    today = ymd(updated_at)
    run_count = MORNING_POSTS if BLOG_RUN_MODE == "morning" else INTRADAY_POSTS

    selected = pick_stocks(stocks, run_count, existing_posts, today)

    if not USE_MOCK and not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is required unless BLOG_MOCK=true")

    BLOG_DIR.mkdir(exist_ok=True)

    new_posts = []
    for stock in selected:
        ticker = (stock.get("ticker") or "").strip().upper()
        if not ticker:
            continue

        ai = mock_ai(stock) if USE_MOCK else call_openai(stock, updated_at)

        # archive-like filename so morning+intraday can add more each day
        stamp = f"{today}-{hhmm(now_iso())}"
        file_name = f"{ticker.lower()}-sentiment-{stamp}.html"
        href = f"/blog/{file_name}"

        html = render_post(stock, ai, updated_at)
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
        })

    # append, do not delete existing
    all_posts = sorted(new_posts + existing_posts, key=lambda x: x.get("generated_at", ""), reverse=True)
    all_posts = all_posts[:MAX_ARCHIVE_POSTS]

    INDEX_PATH.write_text(render_index(all_posts, updated_at), encoding="utf-8")

    manifest["generated_at"] = now_iso()
    manifest["source_data_updated_at"] = updated_at
    manifest["model"] = OPENAI_MODEL
    manifest["mode"] = BLOG_RUN_MODE
    manifest["posts"] = all_posts
    save_manifest(manifest)

    print(f"[ai-blog] mode={BLOG_RUN_MODE} selected={len(selected)} new_posts={len(new_posts)} total_posts={len(all_posts)}")
    print("Generated blog/index.html")
    print("Generated data/blog-manifest.json")


if __name__ == "__main__":
    main()
