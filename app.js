const tickerInput = document.getElementById("tickerInput");
const analyzeBtn = document.getElementById("analyzeBtn");
const results = document.getElementById("results");
const newsList = document.getElementById("newsList");
const statusMsg = document.getElementById("statusMsg");

const WORKER_URL = "https://sparkling-bar-4dab.cosmicpublicationsinc.workers.dev";

function formatMoney(value) {
  if (typeof value !== "number" || Number.isNaN(value)) return "N/A";
  return `$${value.toFixed(2)}`;
}

function formatPercent(value) {
  if (typeof value !== "number" || Number.isNaN(value)) return "N/A";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function sentimentClass(label) {
  if (label === "Bullish") return "bullish";
  if (label === "Bearish") return "bearish";
  return "mixed";
}

function niceLabel(text) {
  if (!text) return "";
  return text.replace(/_/g, " ").replace(/\b\w/g, char => char.toUpperCase());
}

function renderTopDrivers(drivers) {
  if (!drivers || drivers.length === 0) {
    return `
      <div class="drivers-wrap">
        <span class="drivers-title">Top Drivers</span>
        <p class="muted">Not available</p>
      </div>
    `;
  }

  return `
    <div class="drivers-wrap">
      <span class="drivers-title">Top Drivers</span>
      <div class="drivers-list">
        ${drivers.map(driver => `<span class="driver-pill">${niceLabel(driver)}</span>`).join("")}
      </div>
    </div>
  `;
}

function renderResultCard(data) {
  const className = sentimentClass(data.sentiment);

  results.innerHTML = `
    <h2>${data.ticker}</h2>
    <div class="result ${className}">
      ${data.sentiment} (Score: ${data.sentiment_score})
    </div>

    <div class="metrics-grid">
      <div class="metric-card">
        <div class="metric-label">Company</div>
        <div class="metric-value">${data.company_name || data.ticker}</div>
      </div>

      <div class="metric-card">
        <div class="metric-label">Industry</div>
        <div class="metric-value">${data.industry || "N/A"}</div>
      </div>

      <div class="metric-card">
        <div class="metric-label">Price</div>
        <div class="metric-value">${formatMoney(data.price)}</div>
      </div>

      <div class="metric-card">
        <div class="metric-label">Daily Change</div>
        <div class="metric-value">${formatMoney(data.change)} (${formatPercent(data.percent_change)})</div>
      </div>

      <div class="metric-card">
        <div class="metric-label">Confidence</div>
        <div class="metric-value">${data.confidence || "N/A"}</div>
      </div>
    </div>

    ${renderTopDrivers(data.top_drivers)}
  `;
}

function renderHeadlines(news) {
  if (!news || news.length === 0) {
    newsList.innerHTML = `<li class="headline-item">No relevant recent headlines found.</li>`;
    return;
  }

  newsList.innerHTML = news.map(item => {
    const signalClass = sentimentClass(item.signal);
    const source = item.source || "Source";
    const impactType = item.impact_type ? niceLabel(item.impact_type) : "";

    const driverTags = item.driver_tags && item.driver_tags.length
      ? `
        <div class="headline-tags">
          ${item.driver_tags.map(tag => `<span class="tag-pill">${niceLabel(tag)}</span>`).join("")}
        </div>
      `
      : "";

    const headline = item.url
      ? `<a href="${item.url}" target="_blank" rel="noopener noreferrer">${item.headline}</a>`
      : item.headline;

    return `
      <li class="headline-item">
        <div class="headline-top">
          <div class="headline-source">${source}</div>
          <div class="headline-meta">
            ${impactType ? `<span class="headline-impact">${impactType}</span>` : ""}
            ${item.signal ? `<span class="headline-signal ${signalClass}">${item.signal}</span>` : ""}
          </div>
        </div>

        <div class="headline-title">${headline}</div>
        ${driverTags}
      </li>
    `;
  }).join("");
}

function setLoadingState(isLoading, ticker = "") {
  analyzeBtn.disabled = isLoading;
  analyzeBtn.textContent = isLoading ? "Loading..." : "Analyze";
  statusMsg.textContent = isLoading ? `Loading live sentiment for ${ticker}...` : "";
}

async function runAnalysis() {
  const ticker = tickerInput.value.trim().toUpperCase();

  if (!ticker) {
    statusMsg.textContent = "Please enter a ticker.";
    results.innerHTML = `
      <div class="empty-state">
        <h3>Missing ticker</h3>
        <p>Enter a stock symbol like AAPL or TSLA to continue.</p>
      </div>
    `;
    newsList.innerHTML = "";
    return;
  }

  setLoadingState(true, ticker);
  results.innerHTML = `
    <div class="empty-state">
      <h3>Loading...</h3>
      <p>Pulling sentiment, confidence, and relevant headlines.</p>
    </div>
  `;
  newsList.innerHTML = "";

  try {
    const response = await fetch(`${WORKER_URL}?ticker=${encodeURIComponent(ticker)}`);
    const data = await response.json();

    if (!response.ok || data.error) {
      results.innerHTML = `
        <div class="empty-state">
          <h3>Unable to load data</h3>
          <p>${data.error || "Something went wrong while loading market data."}</p>
        </div>
      `;
      newsList.innerHTML = "";
      statusMsg.textContent = "";
      setLoadingState(false);
      return;
    }

    renderResultCard(data);
    renderHeadlines(data.news);
    statusMsg.textContent = `Loaded live sentiment for ${data.ticker}.`;
  } catch (error) {
    console.error(error);
    results.innerHTML = `
      <div class="empty-state">
        <h3>Error loading live market data</h3>
        <p>Please try again in a moment.</p>
      </div>
    `;
    newsList.innerHTML = "";
    statusMsg.textContent = "";
  } finally {
    setLoadingState(false);
  }
}

analyzeBtn.addEventListener("click", runAnalysis);

tickerInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    runAnalysis();
  }
});
