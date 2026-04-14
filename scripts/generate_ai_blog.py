import argparse
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
MAX_POSTS = int(os.getenv("MAX_POSTS", "6"))


def parse_args():
    p = argparse.ArgumentParser(description="Generate blog pages from market data.")
    p.add_argument("--mock", action="store_true", help="Do not call OpenAI; use deterministic content.")
    p.add_argument("--limit", type=int, default=0, help="Limit number of posts generated.")
    return p.parse_args()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def date_label(iso_ts: str) -> str:
    if not iso_ts:
        return "Unknown"
    return datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).strftime("%B %d, %Y")


def load_market_data():
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    updated_at = payload.get("updated_at") or now_iso()
    stocks = payload.get("stocks", [])
    return updated_at, stocks


def pick_stocks(stocks, n):
    def score_key(s):
        return abs(int(s.get("sentiment_score", 0)))
    ranked = sorted(stocks, key=score_key, reverse=True)
    return ranked[:n]


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
                    "You write concise stock sentiment blog reports. "
                    "Use only provided data. No financial advice. "
                    "Return strict JSON with keys: title, meta_description, excerpt, body_html. "
                    "body_html may only use <h2>, <p>, <ul>, <li>."
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
      <p class="eyebrow">AI Sentiment Report</p>
      <h1>{title}</h1>
      <p class="hero-text">Generated {date_label(updated_at)}</p>
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
    cards = []
    for p in posts:
        cards.append(
            f"""
            <article class="blog-report-card">
              <div class="blog-report-tag">{escape(p.get('sentiment', 'Neutral'))} ({int(p.get('score', 0)):+d})</div>
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
  <title>Stock Sentiment Blog | Stock Sentiment Score</title>
  <meta name="description" content="AI-generated stock sentiment reports." />
  <link rel="stylesheet" href="/style.css" />
</head>
<body>
  <div id="site-header"></div>
  <header class="hero hero-small blog-hero">
    <div class="hero-inner">
      <p class="eyebrow">Auto AI Blog</p>
      <h1>Stock Sentiment Blog</h1>
      <p class="hero-text">Latest update: {date_label(updated_at)}</p>
    </div>
  </header>

  <main class="container content-page blog-page">
    <section class="content-card blog-featured-card">
      <div class="blog-section-top"><h2>Latest Reports</h2></div>
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
    args = parse_args()
    updated_at, stocks = load_market_data()

    if args.limit and args.limit > 0:
        count = args.limit
    else:
        count = MAX_POSTS

    selected = pick_stocks(stocks, count)

    if not args.mock and not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is required unless using --mock")

    BLOG_DIR.mkdir(exist_ok=True)
    posts = []

    for stock in selected:
        ticker = (stock.get("ticker") or "").strip().upper()
        if not ticker:
            continue

        ai = mock_ai(stock) if args.mock else call_openai(stock, updated_at)

        file_name = f"{ticker.lower()}-sentiment.html"
        href = f"/blog/{file_name}"
        html = render_post(stock, ai, updated_at)
        (BLOG_DIR / file_name).write_text(html, encoding="utf-8")

        posts.append({
            "ticker": ticker,
            "href": href,
            "title": ai.get("title", f"{ticker} Stock Sentiment Report"),
            "excerpt": ai.get("excerpt", ""),
            "sentiment": stock.get("sentiment", "Neutral"),
            "score": int(stock.get("sentiment_score", 0)),
        })

        print(f"Generated blog/{file_name}")

    INDEX_PATH.write_text(render_index(posts, updated_at), encoding="utf-8")

    manifest = {
        "generated_at": now_iso(),
        "source_data_updated_at": updated_at,
        "model": OPENAI_MODEL,
        "posts": posts,
    }
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("Generated blog/index.html")
    print("Generated data/blog-manifest.json")
    print(f"Done. Generated {len(posts)} posts.")


if __name__ == "__main__":
    main()
