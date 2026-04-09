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
    p = argparse.ArgumentParser(description="Generate AI sentiment blog posts.")
    p.add_argument("--mock", action="store_true", help="No API call; generate deterministic content.")
    p.add_argument("--limit", type=int, default=0, help="Limit number of tickers")
    return p.parse_args()


def read_market_data():
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return payload.get("updated_at"), payload.get("stocks", [])


def to_date_label(iso_ts: str) -> str:
    if not iso_ts:
        return "Unknown"
    return datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).strftime("%B %d, %Y")


def call_openai(stock: dict, updated_at: str, api_key: str) -> dict:
    prompt_data = {
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

    payload = {
        "model": MODEL,
        "input": [
            {
                "role": "system",
                "content": (
                    "You are an SEO financial content writer. "
                    "Use ONLY provided data. No invented facts. "
                    "Return strict JSON keys: "
                    "title, meta_description, excerpt, key_takeaways, body_sections, faq. "
                    "key_takeaways: array of 3-5 bullets. "
                    "body_sections: array of {heading, paragraphs, bullets}. "
                    "faq: array of {q, a} with 3 items. "
                    "Tone: educational, clear, non-hype, not financial advice."
                ),
            },
            {"role": "user", "content": json.dumps(prompt_data, ensure_ascii=False)},
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
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {detail}") from exc

    # Primary path
    output_text = (data.get("output_text") or "").strip()
    if output_text:
        return json.loads(output_text)

    # Fallback path
    for item in data.get("output", []):
        for c in item.get("content", []):
            if c.get("type") == "output_text" and c.get("text"):
                return json.loads(c["text"])

    raise RuntimeError(f"OpenAI response missing text. Raw head: {json.dumps(data)[:500]}")


def mock_content(stock: dict) -> dict:
    ticker = stock.get("ticker", "UNKNOWN")
    sentiment = stock.get("sentiment", "Neutral")
    score = int(stock.get("sentiment_score", 0))
    drivers = stock.get("top_drivers", [])[:4]

    return {
        "title": f"{ticker} Sentiment Today: {sentiment} ({score:+d})",
        "meta_description": f"{ticker} sentiment report with score, confidence, top drivers, and recent headlines.",
        "excerpt": f"{ticker} currently reads {sentiment} at {score:+d} from recent headline pressure.",
        "key_takeaways": [
            f"{ticker} sentiment is {sentiment} ({score:+d}).",
            "Headline pressure can shift quickly as new stories arrive.",
            "Use sentiment as context, not certainty."
        ],
        "body_sections": [
            {
                "heading": "Quick Take",
                "paragraphs": [
                    f"{ticker} shows a {sentiment} reading at {score:+d} based on current analyzed headlines."
                ],
                "bullets": []
            },
            {
                "heading": "What Is Driving Sentiment",
                "paragraphs": [
                    "The score reflects weighted headline tone and relevance."
                ],
                "bullets": drivers
            },
            {
                "heading": "How To Interpret This Signal",
                "paragraphs": [
                    "Combine sentiment with earnings quality, valuation, and price action."
                ],
                "bullets": []
            },
            {
                "heading": "What To Watch Next",
                "paragraphs": [
                    "Watch upcoming catalysts, trend shifts, and broader market tone."
                ],
                "bullets": []
            },
        ],
        "faq": [
            {"q": "Is this financial advice?", "a": "No. This content is educational only."},
            {"q": "How often does this update?", "a": "On each workflow run."},
            {"q": "Can sentiment be wrong?", "a": "Yes, use multiple signals when making decisions."},
        ],
    }


def normalize_ai_payload(ai: dict, stock: dict) -> dict:
    ticker = stock.get("ticker", "UNKNOWN")
    sentiment = stock.get("sentiment", "Neutral")
    score = int(stock.get("sentiment_score", 0))

    if not isinstance(ai, dict):
        ai = {}

    title = ai.get("title") or f"{ticker} Sentiment Today: {sentiment} ({score:+d})"
    meta_description = ai.get("meta_description") or f"{ticker} sentiment report with score and context."
    excerpt = ai.get("excerpt") or f"{ticker} currently reads {sentiment} at {score:+d}."

    key_takeaways = ai.get("key_takeaways") or [
        f"{ticker} sentiment is {sentiment} ({score:+d}).",
        "Headline pressure changes quickly.",
        "Use sentiment with other analysis."
    ]

    body_sections = ai.get("body_sections") or []
    faq = ai.get("faq") or [
        {"q": "Is this financial advice?", "a": "No. Educational only."},
        {"q": "How often does this update?", "a": "On each generator run."},
        {"q": "Can sentiment be wrong?", "a": "Yes. It should be combined with other signals."},
    ]

    return {
        "title": title,
        "meta_description": meta_description,
        "excerpt": excerpt,
        "key_takeaways": key_takeaways,
        "body_sections": body_sections,
        "faq": faq,
    }


def render_post(stock: dict, ai: dict, updated_at: str) -> str:
    ai = normalize_ai_payload(ai, stock)

    ticker = escape(str(stock.get("ticker", "UNKNOWN")))
    company = escape(str(stock.get("company_name") or stock.get("ticker") or ticker))
    industry = escape(str(stock.get("industry") or "N/A"))
    sentiment = escape(str(stock.get("sentiment", "Neutral")))
    score = int(stock.get("sentiment_score", 0))
    confidence = escape(str(stock.get("confidence", "Low")))
    price = stock.get("price")
    change = stock.get("change")
    pct = stock.get("percent_change")

    price_text = f"${price:.2f}" if isinstance(price, (int, float)) else "N/A"
    change_text = f"{change:+.2f}" if isinstance(change, (int, float)) else "N/A"
    pct_text = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "N/A"

    title = escape(ai["title"])
    desc = escape(ai["meta_description"])
    date_label = to_date_label(updated_at)

    takeaway_html = "".join(f"<li>{escape(str(x))}</li>" for x in ai["key_takeaways"])

    section_html = ""
    for s in ai["body_sections"]:
        heading = escape(str(s.get("heading", "Section")))
        paragraphs = "".join(f"<p>{escape(str(p))}</p>" for p in s.get("paragraphs", []))
        bullets = s.get("bullets", [])
        bullets_html = ""
        if bullets:
            bullets_html = "<ul>" + "".join(
                f"<li>{escape(str(b).replace('_', ' ').title())}</li>" for b in bullets
            ) + "</ul>"
        section_html += f"<h2>{heading}</h2>{paragraphs}{bullets_html}"

    faq_html = ""
    faq_schema_entities = []
    for f in ai["faq"]:
        q = escape(str(f.get("q", "")))
        a = escape(str(f.get("a", "")))
        faq_html += f"<h3>{q}</h3><p>{a}</p>"
        faq_schema_entities.append({
            "@type": "Question",
            "name": f.get("q", ""),
            "acceptedAnswer": {"@type": "Answer", "text": f.get("a", "")}
        })

    article_schema = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": ai["title"],
        "description": ai["meta_description"],
        "author": {"@type": "Organization", "name": "Stock Sentiment Score"},
        "dateModified": updated_at,
        "mainEntityOfPage": f"https://www.stocksentimentscore.com/blog/{ticker.lower()}-sentiment.html",
    }

    faq_schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": faq_schema_entities
    }

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title} | Stock Sentiment Score</title>
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
      <h1>{title}</h1>
      <p class="hero-text">Updated {date_label}</p>
    </div>
  </header>

  <main class="container content-page blog-article-page">
    <article class="content-card blog-article-card">
      <div class="blog-article-meta">
        <span class="blog-article-pill">{ticker}</span>
        <span class="blog-article-pill">{company}</span>
        <span class="blog-article-pill">{industry}</span>
      </div>

      <p><strong>Sentiment:</strong> {sentiment} ({score:+d}) | <strong>Confidence:</strong> {confidence}</p>
      <p><strong>Price:</strong> {price_text} | <strong>Daily Change:</strong> {change_text} ({pct_text})</p>

      <h2>Key Takeaways</h2>
      <ul>{takeaway_html}</ul>

      {section_html}

      <h2>FAQ</h2>
      {faq_html}

      <div class="blog-article-note">
        <strong>Important:</strong> Educational only. Not financial advice.
      </div>
    </article>
  </main>

  <div id="site-footer"></div>
  <script src="/js/include-header.js"></script>
  <script src="/js/include-footer.js"></script>
</body>
</html>
"""


def render_index(posts: list, updated_at: str) -> str:
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
      <p class="hero-text">Last updated {to_date_label(updated_at)}</p>
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
    updated_at, stocks = read_market_data()
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

        ai_n = normalize_ai_payload(ai, stock)
        posts.append(
            {
                "ticker": ticker,
                "href": f"/blog/{ticker.lower()}-sentiment.html",
                "title": ai_n["title"],
                "excerpt": ai_n["excerpt"],
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
