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
API_KEY = os.getenv("OPENAI_API_KEY", "").strip()


def now_utc_iso():
    return datetime.now(timezone.utc).isoformat()


def iso_to_date_label(iso_value: str) -> str:
    if not iso_value:
        return "Unknown"
    return datetime.fromisoformat(iso_value.replace("Z", "+00:00")).strftime("%B %d, %Y")


def iso_to_ymd(iso_value: str) -> str:
    if not iso_value:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return datetime.fromisoformat(iso_value.replace("Z", "+00:00")).strftime("%Y-%m-%d")


def load_market_data():
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return payload.get("updated_at"), payload.get("stocks", [])


def load_manifest():
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {"generated_at": None, "posts": []}


def save_manifest(manifest):
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def call_openai(stock, market_data_updated_at, generated_at):
    prompt_data = {
        "generated_at": generated_at,
        "market_data_updated_at": market_data_updated_at,
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
                    "You are writing a high-quality SEO stock sentiment article. "
                    "Use only provided data. Do not invent facts or numbers. "
                    "Return strict JSON with keys: "
                    "title, meta_description, excerpt, key_takeaways, body_sections, faq. "
                    "key_takeaways = array(3-5). "
                    "body_sections = array of objects with keys {heading, paragraphs, bullets}. "
                    "faq = array of exactly 3 objects {q, a}. "
                    "Tone: clear, specific, educational, non-hype. Not financial advice."
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
            "Authorization": f"Bearer {API_KEY}",
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

    out = (data.get("output_text") or "").strip()
    if out:
        return json.loads(out)

    for item in data.get("output", []):
        for c in item.get("content", []):
            if c.get("type") == "output_text" and c.get("text"):
                return json.loads(c["text"])

    raise RuntimeError("OpenAI response missing usable JSON output")


def fallback_ai_payload(stock):
    t = stock.get("ticker", "UNKNOWN")
    s = stock.get("sentiment", "Neutral")
    score = int(stock.get("sentiment_score", 0))
    drivers = stock.get("top_drivers", [])[:4]
    return {
        "title": f"{t} Sentiment Today: {s} ({score:+d})",
        "meta_description": f"{t} sentiment report with score, confidence, key drivers, and recent headlines.",
        "excerpt": f"{t} currently reads {s} at {score:+d} based on current headline pressure.",
        "key_takeaways": [
            f"{t} sentiment is {s} ({score:+d}).",
            "Headline pressure can change rapidly.",
            "Use sentiment as context, not certainty.",
        ],
        "body_sections": [
            {
                "heading": "Quick Take",
                "paragraphs": [f"{t} shows {s} sentiment at {score:+d} from current data."],
                "bullets": []
            },
            {
                "heading": "What Is Driving Sentiment",
                "paragraphs": ["The score reflects weighted headline tone and relevance."],
                "bullets": drivers
            },
            {
                "heading": "How To Interpret This Signal",
                "paragraphs": ["Combine sentiment with fundamentals and price action."],
                "bullets": []
            },
            {
                "heading": "What To Watch Next",
                "paragraphs": ["Watch earnings, macro data, and narrative changes."],
                "bullets": []
            },
        ],
        "faq": [
            {"q": "Is this financial advice?", "a": "No. This content is educational only."},
            {"q": "How often does this update?", "a": "Daily via automation."},
            {"q": "Can sentiment be wrong?", "a": "Yes. Use multiple signals before decisions."},
        ],
    }


def normalize_ai(ai, stock):
    if not isinstance(ai, dict):
        ai = {}

    fb = fallback_ai_payload(stock)

    return {
        "title": ai.get("title") or fb["title"],
        "meta_description": ai.get("meta_description") or fb["meta_description"],
        "excerpt": ai.get("excerpt") or fb["excerpt"],
        "key_takeaways": ai.get("key_takeaways") or fb["key_takeaways"],
        "body_sections": ai.get("body_sections") or fb["body_sections"],
        "faq": ai.get("faq") or fb["faq"],
    }


def render_sections(body_sections):
    out = []
    for sec in body_sections:
        heading = escape(str(sec.get("heading", "Section")))
        paragraphs = "".join(f"<p>{escape(str(p))}</p>" for p in sec.get("paragraphs", []))
        bullets = sec.get("bullets", [])
        bullets_html = ""
        if bullets:
            bullets_html = "<ul>" + "".join(
                f"<li>{escape(str(b).replace('_', ' ').title())}</li>" for b in bullets
            ) + "</ul>"
        out.append(f"<h2>{heading}</h2>{paragraphs}{bullets_html}")
    return "".join(out)


def render_faq(faq_items):
    html = []
    schema_items = []
    for item in faq_items[:3]:
        q = str(item.get("q", "")).strip()
        a = str(item.get("a", "")).strip()
        if not q or not a:
            continue
        html.append(f"<h3>{escape(q)}</h3><p>{escape(a)}</p>")
        schema_items.append({
            "@type": "Question",
            "name": q,
            "acceptedAnswer": {"@type": "Answer", "text": a}
        })
    return "".join(html), schema_items


def render_post_html(stock, ai, generated_at, market_data_updated_at, dated_filename):
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
    excerpt = escape(ai["excerpt"])

    generated_label = iso_to_date_label(generated_at)
    data_label = iso_to_date_label(market_data_updated_at)

    takeaways_html = "".join(f"<li>{escape(str(t))}</li>" for t in ai["key_takeaways"][:5])
    sections_html = render_sections(ai["body_sections"])
    faq_html, faq_schema_items = render_faq(ai["faq"])

    article_schema = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": f"{ai['title']} ({generated_label})",
        "description": ai["meta_description"],
        "datePublished": generated_at,
        "dateModified": generated_at,
        "author": {"@type": "Organization", "name": "Stock Sentiment Score"},
        "mainEntityOfPage": f"https://www.stocksentimentscore.com/blog/{dated_filename}",
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
      <p class="hero-text"><strong>Generated:</strong> {generated_label} | <strong>Market data as of:</strong> {data_label}</p>
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

      <h2>Key Takeaways</h2>
      <ul>{takeaways_html}</ul>

      {sections_html}

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


def render_index_html(posts, generated_at):
    generated_label = iso_to_date_label(generated_at)
    cards = []
    for p in posts[:60]:
        cards.append(
            f"""
            <article class="blog-report-card">
              <div class="blog-report-tag">{escape(p['sentiment'])} ({p['score']:+d})</div>
              <h3><a href="{escape(p['href'])}">{escape(p['title'])}</a></h3>
              <p>{escape(p['excerpt'])}</p>
              <p><small>Published: {escape(p['date'])}</small></p>
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
  <meta name="description" content="Daily AI-generated stock sentiment reports and analysis." />
  <link rel="stylesheet" href="/style.css" />
</head>
<body>
  <div id="site-header"></div>

  <header class="hero hero-small blog-hero">
    <div class="hero-inner">
      <p class="eyebrow">AI Market Insight Blog</p>
      <h1>Stock Sentiment Blog</h1>
      <p class="hero-text">Latest publish run: {generated_label}</p>
    </div>
  </header>

  <main class="container content-page blog-page">
    <section class="content-card blog-featured-card">
      <div class="blog-section-top"><h2>Latest Daily Reports</h2></div>
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

    market_data_updated_at, stocks = load_market_data()
    generated_at = now_utc_iso()
    day_slug = iso_to_ymd(generated_at)

    manifest = load_manifest()
    posts = manifest.get("posts", [])

    BLOG_DIR.mkdir(exist_ok=True)

    # Create one NEW archived post per ticker per day, plus overwrite evergreen
    for stock in stocks:
        ticker = str(stock.get("ticker", "")).strip().upper()
        if not ticker:
            continue

        dated_filename = f"{ticker.lower()}-sentiment-{day_slug}.html"
        evergreen_filename = f"{ticker.lower()}-sentiment.html"

        # Skip creating duplicate archive entry if already exists for today/ticker
        exists_today = any(
            p.get("ticker") == ticker and p.get("date") == day_slug
            for p in posts
        )

        ai_raw = call_openai(stock, market_data_updated_at, generated_at)
        ai = normalize_ai(ai_raw, stock)

        # Always refresh evergreen page (latest snapshot)
        evergreen_html = render_post_html(stock, ai, generated_at, market_data_updated_at, evergreen_filename)
        (BLOG_DIR / evergreen_filename).write_text(evergreen_html, encoding="utf-8")

        # Create daily archive page only if not already created today
        if not exists_today:
            archive_html = render_post_html(stock, ai, generated_at, market_data_updated_at, dated_filename)
            (BLOG_DIR / dated_filename).write_text(archive_html, encoding="utf-8")

            posts.append({
                "ticker": ticker,
                "date": day_slug,
                "href": f"/blog/{dated_filename}",
                "title": ai["title"],
                "excerpt": ai["excerpt"],
                "sentiment": stock.get("sentiment", "Neutral"),
                "score": int(stock.get("sentiment_score", 0)),
            })

    # newest first
    posts = sorted(posts, key=lambda x: x.get("date", ""), reverse=True)

    INDEX_PATH.write_text(render_index_html(posts, generated_at), encoding="utf-8")

    manifest["generated_at"] = generated_at
    manifest["market_data_updated_at"] = market_data_updated_at
    manifest["model"] = MODEL
    manifest["posts"] = posts
    save_manifest(manifest)

    print("Generated daily archive posts + evergreen posts successfully.")


if __name__ == "__main__":
    main()
