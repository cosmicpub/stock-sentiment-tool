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

function getScoreMeaning(score) {
  if (typeof score !== "number" || Number.isNaN(score)) {
    return {
      band: "Unknown",
      explanation: "The tool could not determine a usable sentiment score."
    };
  }

  if (score >= 6) {
    return {
      band: "Strong Bullish Pressure",
      explanation: "Recent relevant headlines are leaning clearly positive."
    };
  }

  if (score >= 3) {
    return {
      band: "Moderate Bullish Pressure",
      explanation: "Recent relevant headlines are mostly positive."
    };
  }

  if (score >= 1) {
    return {
      band: "Slight Bullish Lean",
      explanation: "News flow is mildly positive but not strong."
    };
  }

  if (score === 0) {
    return {
      band: "Mixed / Balanced",
      explanation: "Recent headlines are mixed or neutral overall."
    };
  }

  if (score <= -6) {
    return {
      band: "Strong Bearish Pressure",
      explanation: "Recent relevant headlines are leaning clearly negative."
    };
  }

  if (score <= -3) {
    return {
      band: "Moderate Bearish Pressure",
      explanation: "Recent relevant headlines are mostly negative."
    };
  }

  return {
    band: "Slight Bearish Lean",
    explanation: "News flow is mildly negative but not strong."
  };
}

function getConfidenceMeaning(confidence) {
  if (!confidence) return "Confidence is not available.";
  if (confidence === "High") return "Several relevant headlines are pointing in a similar direction.";
  if (confidence === "Moderate") return "There is a usable signal, but the headlines are less decisive.";
  return "The signal is weaker, mixed, or based on less convincing headline evidence.";
}

function getMeterPosition(score) {
  if (typeof score !== "number" || Number.isNaN(score)) return 50;
  const min = -6;
  const max = 6;
  const clamped = Math.max(min, Math.min(max, score));
  return ((clamped - min) / (max - min)) * 100;
}

function renderTopDrivers(drivers) {
  if (!drivers || drivers.length === 0) {
    return `
      <div class="drivers-wrap">
        <div class="section-mini-title">Top Drivers</div>
        <p class="muted">Not available</p>
      </div>
    `;
  }

  return `
    <div class="drivers-wrap">
      <div class="section-mini-title">Top Drivers</div>
      <div class="drivers-list">
        ${drivers.map(driver => `<span class="driver-pill">${niceLabel(driver)}</span>`).join("")}
      </div>
    </div>
  `;
}

function renderScoreBlock(score, sentiment, confidence) {
  const scoreMeaning = getScoreMeaning(score);
  const scorePos = getMeterPosition(score);
  const badgeClass = sentimentClass(sentiment);

  return `
    <div class="score-panel">
      <div class="score-panel-left">
        <div class="score-topline">
          <span class="score-badge ${badgeClass}">${sentiment}</span>
          <span class="score-band-label">${scoreMeaning.band}</span>
        </div>

        <div class="score-main-row">
          <div class="score-number-card">
            <div class="score-number-label">Sentiment Score</div>
            <div class="score-number">${score}</div>
          </div>

          <div class="score-explainer-card">
            <div class="score-explainer-title">What this means</div>
            <p>${scoreMeaning.explanation}</p>
            <div class="confidence-inline">
              <strong>Confidence:</strong> ${confidence || "N/A"}
            </div>
            <p class="confidence-copy">${getConfidenceMeaning(confidence)}</p>
          </div>
        </div>
      </div>

      <div class="score-scale-card">
        <div class="score-scale-title">Score Scale</div>

        <div class="sentiment-meter">
          <div class="meter-track"></div>
          <div class="meter-pointer" style="left:${scorePos}%"></div>
        </div>

        <div class="meter-label-row">
          <span>-6</span>
          <span>-3</span>
          <span>0</span>
          <span>3</span>
          <span>6</span>
        </div>

        <div class="meter-band-row">
          <span>Bearish</span>
          <span>Mixed</span>
          <span>Bullish</span>
        </div>

        <p class="score-note">
          This score reflects <strong>news sentiment pressure</strong>, not a guaranteed price prediction.
        </p>
      </div>
    </div>
  `;
}

function renderResultCard(data) {
  results.innerHTML = `
    <h2>${data.ticker}</h2>

    ${renderScoreBlock(data.sentiment_score, data.sentiment, data.confidence)}

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
