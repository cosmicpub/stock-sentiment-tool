(async function () {
  const root = document.getElementById("homeLatestNews");
  if (!root) return;

  try {
    const res = await fetch("/data/blog-manifest.json", { cache: "no-store" });
    if (!res.ok) throw new Error("Failed to load manifest");
    const manifest = await res.json();
    const posts = Array.isArray(manifest.posts) ? manifest.posts.slice(0, 6) : [];

    if (!posts.length) {
      root.innerHTML = `<p class="muted">No posts yet. Check back soon.</p>`;
      return;
    }

    root.innerHTML = posts.map(p => {
      const title = escapeHtml(p.title || "Untitled");
      const href = escapeHtml(p.href || "#");
      const excerpt = escapeHtml(p.excerpt || "");
      const tag = escapeHtml(`${p.ticker || "N/A"} ${p.sentiment ? `• ${p.sentiment}` : ""}`);
      const date = escapeHtml(p.published_date || p.date || "");
      const image = p.image_url ? `<img src="${escapeHtml(p.image_url)}" alt="${title}" loading="lazy" class="news-thumb" />` : "";

      return `
        <article class="blog-report-card">
          ${image}
          <div class="blog-report-tag">${tag}</div>
          <h3><a href="${href}">${title}</a></h3>
          <p>${excerpt}</p>
          <p><small>${date}</small></p>
          <a class="blog-report-link" href="${href}">Read report →</a>
        </article>
      `;
    }).join("");
  } catch (err) {
    root.innerHTML = `<p class="muted">Unable to load latest posts right now.</p>`;
    console.error(err);
  }

  function escapeHtml(str) {
    return String(str)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }
})();
