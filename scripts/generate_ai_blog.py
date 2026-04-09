import json
import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "market-data.json"
BLOG_DIR = ROOT / "blog"
INDEX_PATH = BLOG_DIR / "index.html"
MANIFEST_PATH = ROOT / "data" / "blog-manifest.json"

OPENAI_API_URL = "https://api.openai.com/v1/responses"
MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
API_KEY = os.getenv("OPENAI_API_KEY", "").strip()


def call_openai(stock, updated_at):
    payload = {
        "model": MODEL,
        "input": [
            {
                "role": "system",
                "content": (
                    "Write SEO-friendly stock sentiment content. "
                    "Use only provided data. Return JSON with keys: "
                    "title, meta_description, excerpt, body_html. "
                    "body_html must use only <h2>, <p>, <ul>, <li>."
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
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    output_text = data.get("output_text", "").strip()
    if not output_text:
        raise RuntimeError("OpenAI returned empty output_text")
    return json.loads(output_text)


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

  <header class="hero hero-small blog-hero">
    <div class="hero-inner">
      <p class="eyebrow">AI Sentiment Report</p>
      <h1>{title}</h1>
      <p class="hero-text">Updated {date_label}</p>
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


def render_index(posts, updated_at):
    date_label = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).strftime("%B %d, %Y")
    cards = []
    for p in posts:
        cards.append(
            f"""
            <article class="blog-report-card">
              <div class="blog-report-tag">{escape(p['sentiment'])} ({p['score']:+d})</div>
              <h3><a href="{escape(p['href'])}">{escape(p['title'])}</a></h3>
              <p>{escape(p['excerpt'])}</p>
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
      <p class="hero-text">Last updated {date_label}</p>
    </div>
  </header>
  <main class="container content-page blog-page">
    <section class="content-card blog-featured-card">
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
    if not API_KEY:
        raise RuntimeError("Missing OPENAI_API_KEY")

    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    updated_at = payload.get("updated_at")
    stocks = payload.get("stocks", [])

    BLOG_DIR.mkdir(exist_ok=True)
    posts = []

    for stock in stocks:
        ticker = str(stock.get("ticker", "")).upper().strip()
        if not ticker:
            continue

        ai = call_openai(stock, updated_at)
        out_file = BLOG_DIR / f"{ticker.lower()}-sentiment.html"
        out_file.write_text(render_post(stock, ai, updated_at), encoding="utf-8")

        posts.append(
            {
                "ticker": ticker,
                "href": f"/blog/{ticker.lower()}-sentiment.html",
                "title": ai.get("title", f"{ticker} Stock Sentiment Analysis"),
                "excerpt": ai.get("excerpt", f"{ticker} sentiment report"),
                "sentiment": stock.get("sentiment", "Neutral"),
                "score": int(stock.get("sentiment_score", 0)),
            }
        )

    INDEX_PATH.write_text(render_index(posts, updated_at), encoding="utf-8")
    MANIFEST_PATH.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "source_data_updated_at": updated_at,
                "model": MODEL,
                "posts": posts,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print("Generated AI blog pages successfully.")


if __name__ == "__main__":
    main()
