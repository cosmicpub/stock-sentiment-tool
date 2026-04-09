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


def fmt_date(iso_value: str) -> str:
    if not iso_value:
        return "Unknown"
    return datetime.fromisoformat(iso_value.replace("Z", "+00:00")).strftime("%B %d, %Y")


def read_market_data():
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return payload.get("updated_at"), payload.get("stocks", [])


def call_openai(stock, data_updated_at, run_at, api_key):
    payload = {
        "model": MODEL,
        "input": [
            {
                "role": "system",
                "content": (
                    "Write a clear SEO stock sentiment report using ONLY provided data. "
                    "Return strict JSON with keys: title, meta_description, excerpt, body_html. "
                    "body_html must only use <h2>, <p>, <ul>, <li>. "
                    "No hype, no financial advice, no invented facts."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "run_generated_at": run_at,
                        "market_data_updated_at": data_updated_at,
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

    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    out = (data.get("output_text") or "").strip()
    if out:
        return json.loads(out)

    for item in data.get("output", []):
        for c in item.get("content", []):
            if c.get("type") == "output_text" and c.get("text"):
                return json.loads(c["text"])

    raise RuntimeError("OpenAI response did not include usable output text")


def mock_content(stock):
    ticker = stock.get("ticker", "UNKNOWN")
    sentiment = stock.get("sentiment", "Neutral")
    score = int(stock.get("sentiment_score", 0))
    return {
        "title": f"{ticker} Sentiment Today: {sentiment} ({score:+d})",
        "meta_description": f"{ticker} sentiment report with score, confidence, and headline context.",
        "excerpt": f"{ticker} currently reads {sentiment} at {score:+d}.",
        "body_html": (
            f"<h2>Quick Take</h2><p>{escape(ticker)} currently shows {escape(sentiment)} sentiment at {score:+d}.</p>"
            "<h2>What This Means</h2><p>Sentiment is a context signal, not a guarantee.</p>"
            "<h2>Key Drivers</h2><ul><li>Recent headline tone and relevance.</li></ul>"
            "<h2>What To Watch</h2><p>Monitor upcoming catalysts and sentiment shifts.</p>"
        ),
    }


def render_post(stock, ai, run_at, data_updated_at):
    ticker = escape(str(stock.get("ticker", "UNKNOWN")))
    sentiment = escape(str(stock.get("sentiment", "Neutral")))
    score = int(stock.get("sentiment_score", 0))
    confidence = escape(str(stock.get("confidence", "Low")))
    price = stock.get("price")
    change = stock.get("change")
    pct = stock.get("percent_change")

    title_raw = ai.get("title", f"{ticker} Stock Sentiment Analysis")
    title = escape(title_raw)
    desc = escape(ai.get("meta_description", f"{ticker} sentiment report"))
    body_html = ai.get("body_html", "<p>No content generated.</p>")

    run_label = fmt_date(run_at)
    data_label = fmt_date(data_updated_at)

    price_text = f"${price:.2f}" if isinstance(price, (int, float)) else "N/A"
    change_text = f"{change:+.2f}" if isinstance(change, (int, float)) else "N/A"
    pct_text = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "N/A"

    article_schema = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": f"{title_raw} ({run_label})",
        "description": ai.get("meta_description", ""),
        "datePublished": run_at,
        "dateModified": run_at,
        "author": {"@type": "Organization", "name": "Stock Sentiment Score"},
    }

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title} ({run_label}) | Stock Sentiment Score</title>
  <meta name="description" content="{desc}" />
  <link rel="stylesheet" href="/style.css" />
  <script type="application/ld+json">{json.dumps(article_schema)}</script>
</head>
<body>
  <div id="site-header"></div>

  <header class="hero hero-small blog-hero">
    <div class="hero-inner">
      <p class="eyebrow">AI Sentiment Report</p>
      <h1>{title} ({run_label})</h1>
      <p class="hero-text"><strong>Generated:</strong> {run_label} | <strong>Market data as of:</strong> {data_label}</p>
    </div>
  </header>

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
</html>
"""


def render_index(posts, run_at):
    run_label = fmt_date(run_at)
    cards = []
    for p in posts:
        cards.append(
            f"""
            <article class="blog-report-card">
              <div class="blog-report-tag">{escape(p['sentiment'])} ({p['score']:+d})</div>
              <h3><a href="{escape(p['href'])}">{escape(p['title'])}</a></h3>
              <p>{escape(p['excerpt'])}</p>
              <p><small>Generated: {escape(p['run_label'])}</small></p>
              <a class="blog-report-link" href="{escape(p['href'])}">Read report →</a>
            </article>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Stock Sentiment Blog | Stock Sentiment Score</title>
  <meta name="description" content="AI-generated stock sentiment reports and analysis." />
  <link rel="stylesheet" href="/style.css" />
</head>
<body>
  <div id="site-header"></div>
  <header class="hero hero-small blog-hero">
    <div class="hero-inner">
      <p class="eyebrow">AI Market Insight Blog</p>
      <h1>Stock Sentiment Blog</h1>
      <p class="hero-text">Generated on {run_label}</p>
    </div>
  </header>
  <main class="container content-page blog-page">
    <section class="content-card blog-featured-card">
      <div class="blog-section-top"><h2>Latest AI Reports</h2></div>
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
    data_updated_at, stocks = read_market_data()

    if args.limit > 0:
        stocks = stocks[:args.limit]

    run_at = datetime.now(timezone.utc).isoformat()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()

    if not args.mock and not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY")

    BLOG_DIR.mkdir(exist_ok=True)
    posts = []

    for stock in stocks:
        ticker = str(stock.get("ticker", "")).strip().upper()
        if not ticker:
            continue

        ai = mock_content(stock) if args.mock else call_openai(stock, data_updated_at, run_at, api_key)
        html = render_post(stock, ai, run_at, data_updated_at)

        out_file = BLOG_DIR / f"{ticker.lower()}-sentiment.html"
        out_file.write_text(html, encoding="utf-8")

        posts.append({
            "ticker": ticker,
            "href": f"/blog/{ticker.lower()}-sentiment.html",
            "title": ai.get("title", f"{ticker} Stock Sentiment Analysis"),
            "excerpt": ai.get("excerpt", f"{ticker} sentiment report"),
            "sentiment": stock.get("sentiment", "Neutral"),
            "score": int(stock.get("sentiment_score", 0)),
            "run_label": fmt_date(run_at),
        })

    INDEX_PATH.write_text(render_index(posts, run_at), encoding="utf-8")
    MANIFEST_PATH.write_text(json.dumps({
        "generated_at": run_at,
        "market_data_updated_at": data_updated_at,
        "model": MODEL,
        "posts": posts,
    }, indent=2), encoding="utf-8")

    print("Generated AI blog pages successfully.")


if __name__ == "__main__":
    main()
