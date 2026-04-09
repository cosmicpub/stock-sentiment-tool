import argparse
import json
import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "market-data.json"
BLOG_DIR = ROOT / "blog"
INDEX_PATH = BLOG_DIR / "index.html"
MANIFEST_PATH = ROOT / "data" / "blog-manifest.json"

OPENAI_API_URL = "https://api.openai.com/v1/responses"
MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--mock", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    return p.parse_args()


def call_openai(stock, updated_at, api_key):
    payload = {
        "model": MODEL,
        "input": [
            {
                "role": "system",
                "content": (
                    "Write SEO-friendly stock sentiment content. Use only provided data. "
                    "Return strict JSON with keys: title, meta_description, excerpt, body_html. "
                    "body_html can use only <h2>, <p>, <ul>, <li>."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "updated_at": updated_at,
                        "ticker": stock.get("ticker"),
                        "sentiment": stock.get("sentiment"),
                        "sentiment_score": stock.get("sentiment_score"),
                        "confidence": stock.get("confidence"),
                        "price": stock.get("price"),
                        "change": stock.get("change"),
                        "percent_change": stock.get("percent_change"),
                        "top_drivers": stock.get("top_drivers", []),
                        "news": stock.get("news", [])[:8],
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "text": {"format": {"type": "json_object"}},
    }

    req = urllib.request.Request(
        OPENAI_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {body}") from exc

    # Primary path
    txt = (data.get("output_text") or "").strip()
    if txt:
        return json.loads(txt)

    # Fallback path for structured output blocks
    for item in data.get("output", []):
        for c in item.get("content", []):
            if c.get("type") == "output_text" and c.get("text"):
                return json.loads(c["text"])

    raise RuntimeError(f"OpenAI response missing usable text: {json.dumps(data)[:500]}")


def mock_content(stock):
    ticker = stock.get("ticker", "UNKNOWN")
    sentiment = stock.get("sentiment", "Neutral")
    score = int(stock.get("sentiment_score", 0))
    return {
        "title": f"{ticker} Sentiment Today: {sentiment} ({score:+d})",
        "meta_description": f"{ticker} sentiment report with score, confidence, key drivers, and headline context.",
        "excerpt": f"{ticker} currently reads {sentiment} at {score:+d} based on recent headline pressure.",
        "body_html": (
            f"<h2>Quick Take</h2><p>{escape(ticker)} shows a {escape(sentiment)} reading at {score:+d}.</p>"
            "<h2>Current Reading</h2><p>Use this as a context signal, not a guarantee.</p>"
            "<h2>Drivers</h2><ul><li>Based on current dataset headlines and sentiment tags.</li></ul>"
            "<h2>What to Watch</h2><p>Watch earnings, macro events, and trend changes.</p>"
            "<h2>FAQ</h2><ul><li><strong>Financial advice?</strong> No.</li></ul>"
        ),
    }


def render_post(stock, ai, updated_at):
    ticker = escape(str(stock.get("ticker", "UNKNOWN")))
    sentiment = escape(str(stock.get("sentiment", "Neutral")))
    score = int(stock.get("sentiment_score", 0))
    confidence = escape(str(stock.get("confidence", "Low")))
    price = stock.get("price")
    change = stock.get("change")
    pct = stock.get("percent_change")

    price_text = f"${price:.2f}" if isinstance(price, (int, float)) else "N/A"
    change_text = f"{change:+.2f}" if isinstance(change, (int, float)) else "N/A"
    pct_text = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "N/A"

    title = escape(ai.get("title", f"{ticker} Stock Sentiment Analysis"))
    desc = escape(ai.get("meta_description", f"{ticker} sentiment report"))
    body_html = ai.get("body_html", "<p>No content generated.</p>")

    date_label = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).strftime("%B %d, %Y")

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
  <header class="hero hero-small blog-hero"><div class="hero-inner">
    <p class="eyebrow">AI Sentiment Report</p><h1>{title}</h1><p class="hero-text">Updated {date_label}</p>
  </div></header>
  <main class="container content-page blog-article-page">
    <article class="content-card blog-article-card">
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
</html>"""


def render_index(posts, updated_at):
    date_label = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).strftime("%B %d, %Y")
    cards = []
    for p in posts:
        cards.append(
            f"""<article class="blog-report-card">
<div class="blog-report-tag">{escape(p['sentiment'])} ({p['score']:+d})</div>
<h3><a href="{escape(p['href'])}">{escape(p['title'])}</a></h3>
<p>{escape(p['excerpt'])}</p>
<a class="blog-report-link" href="{escape(p['href'])}">Read report →</a>
</article>"""
        )

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Stock Sentiment Blog | Stock Sentiment Score</title>
<meta name="description" content="AI-generated stock sentiment reports and analysis." />
<link rel="stylesheet" href="/style.css" /></head>
<body><div id="site-header"></div>
<header class="hero hero-small blog-hero"><div class="hero-inner">
<p class="eyebrow">AI Market Insight Blog</p><h1>Stock Sentiment Blog</h1><p class="hero-text">Last updated {date_label}</p>
</div></header>
<main class="container content-page blog-page"><section class="content-card blog-featured-card">
<div class="blog-report-grid">{''.join(cards)}</div>
</section></main>
<div id="site-footer"></div><script src="/js/include-header.js"></script><script src="/js/include-footer.js"></script>
</body></html>"""


def main():
    args = parse_args()
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    updated_at = payload.get("updated_at")
    stocks = payload.get("stocks", [])
    if args.limit > 0:
        stocks = stocks[: args.limit]

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not args.mock and not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY")

    BLOG_DIR.mkdir(exist_ok=True)
    posts = []

    for stock in stocks:
        ticker = str(stock.get("ticker", "")).upper().strip()
        if not ticker:
            continue

        ai = mock_content(stock) if args.mock else call_openai(stock, updated_at, api_key)

        out_file = BLOG_DIR / f"{ticker.lower()}-sentiment.html"
        out_file.write_text(render_post(stock, ai, updated_at), encoding="utf-8")

        posts.append({
            "ticker": ticker,
            "href": f"/blog/{ticker.lower()}-sentiment.html",
            "title": ai.get("title", f"{ticker} Stock Sentiment Analysis"),
            "excerpt": ai.get("excerpt", f"{ticker} sentiment report"),
            "sentiment": stock.get("sentiment", "Neutral"),
            "score": int(stock.get("sentiment_score", 0)),
        })

    INDEX_PATH.write_text(render_index(posts, updated_at), encoding="utf-8")
    MANIFEST_PATH.write_text(json.dumps({
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source_data_updated_at": updated_at,
        "model": MODEL,
        "posts": posts
    }, indent=2), encoding="utf-8")

    print("Generated AI blog pages successfully.")


if __name__ == "__main__":
    main()
