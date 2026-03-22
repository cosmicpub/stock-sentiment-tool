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
      explanation: "Recent relevant headlines are leaning clearly positive for this stock."
    };
  }

  if (score >= 3) {
    return {
      band: "Moderate Bullish Pressure",
      explanation: "Recent news is mostly positive, but not overwhelmingly so."
    };
  }

  if (score >= 1) {
    return {
      band: "Slight Bullish Lean",
      explanation: "News flow is mildly positive, but the signal is not especially strong."
    };
  }

  if (score === 0) {
    return {
      band: "Balanced / Mixed",
      explanation: "The recent news flow is mixed or neutral overall."
    };
  }

  if (score <= -6) {
    return {
      band: "Strong Bearish Pressure",
      explanation: "Recent relevant headlines are leaning clearly negative for this stock."
    };
  }

  if (score <= -3) {
    return {
      band: "Moderate Bearish Pressure",
      explanation: "Recent news is mostly negative, but not overwhelmingly so."
    };
  }

  return {
    band: "Slight Bearish Lean",
    explanation: "News flow is mildly negative, but the signal is not especially strong."
  };
}

function getConfidenceMeaning(confidence) {
  if (!confidence) {
    return "Confidence is not available for this search.";
  }

  if (confidence === "High") {
    return "High confidence means the tool found several relevant headlines pointing in a similar direction.";
  }

  if (confidence === "Moderate") {
    return "Moderate confidence means there is a usable signal, but some headlines may be mixed or less decisive.";
  }

  return "Low confidence means the signal is weaker, mixed, or based on less convincing headline evidence.";
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

function renderScoreGuide(score, confidence) {
  const scoreMeaning = getScoreMeaning(score);
  const confidenceMeaning = getConfidenceMeaning(confidence);

  return `
    <div class="score-guide">
      <h3>How to Read This Result</h3>

      <div class="score-guide-grid">
        <div class="score-guide-card">
          <div class="score-guide-label">Score Meaning</div>
          <div class="score-guide-value">${scoreMeaning.band}</div>
          <p>${scoreMeaning.explanation}</p>
        </div>

        <div class="score-guide-card">
          <div class="score-guide-label">Confidence Meaning</div>
          <div class="score-guide-value">${confidence || "N/A"}</div>
          <p>${confidenceMeaning}</p>
        </div>
      </div>

      <div class="score-scale">
        <div><strong>Score Scale:</strong></div>
        <div class="scale-line">
          <span>-6 or lower = Strong Bearish</span>
          <span>-3 to -5 = Moderate Bearish</span>
          <span>-1 to -2 = Slight Bearish</span>
          <span>0 = Mixed</span>
          <span>1 to 2 = Slight Bullish</span>
          <span>3 to 5 = Moderate Bullish</span>
          <span>6 or higher = Strong Bullish</span>
        </div>
      </div>

      <p class="score-note">
        This score reflects <strong>news sentiment pressure</strong>, not a guaranteed price prediction.
        It helps summarize whether recent relevant headlines are leaning positive, negative, or mixed.
      </p>
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
    ${renderScoreGuide(data.sentiment_score, data.confidence)}
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
