const tickerInput = document.getElementById("tickerInput");
const results = document.getElementById("results");
const newsList = document.getElementById("newsList");

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
  return text
    .replace(/_/g, " ")
    .replace(/\b\w/g, char => char.toUpperCase());
}

function renderTopDrivers(drivers) {
  if (!drivers || drivers.length === 0) {
    return "<p><strong>Top Drivers:</strong> Not available</p>";
  }

  const items = drivers.map(driver => {
    return `<span class="driver-pill">${niceLabel(driver)}</span>`;
  }).join(" ");

  return `
    <div class="drivers-wrap">
      <strong>Top Drivers:</strong>
      <div class="drivers-list">${items}</div>
    </div>
  `;
}

async function runAnalysis() {
  const ticker = tickerInput.value.trim().toUpperCase();

  if (!ticker) {
    results.innerHTML = "<p>Please enter a ticker.</p>";
    newsList.innerHTML = "";
    return;
  }

  results.innerHTML = "<p>Loading...</p>";
  newsList.innerHTML = "";

  try {
    const response = await fetch(`${WORKER_URL}?ticker=${encodeURIComponent(ticker)}`);
    const data = await response.json();

    if (!response.ok || data.error) {
      results.innerHTML = `<p>${data.error || "Unable to load stock data."}</p>`;
      newsList.innerHTML = "";
      return;
    }

    const className = sentimentClass(data.sentiment);

    results.innerHTML = `
      <h2>${data.ticker}</h2>
      <div class="result ${className}">
        ${data.sentiment} (Score: ${data.sentiment_score})
      </div>
      <p><strong>Company:</strong> ${data.company_name || data.ticker}</p>
      <p><strong>Industry:</strong> ${data.industry || "N/A"}</p>
      <p><strong>Price:</strong> ${formatMoney(data.price)}</p>
      <p><strong>Daily Change:</strong> ${formatMoney(data.change)} (${formatPercent(data.percent_change)})</p>
      <p><strong>Confidence:</strong> ${data.confidence || "N/A"}</p>
      ${renderTopDrivers(data.top_drivers)}
    `;

    if (!data.news || data.news.length === 0) {
      newsList.innerHTML = "<li>No relevant recent headlines found.</li>";
      return;
    }

    newsList.innerHTML = data.news.map(item => {
      const source = item.source ? `<strong>${item.source}</strong>` : "Source";
      const signalClass = sentimentClass(item.signal);
      const signal = item.signal ? `<span class="headline-signal ${signalClass}">${item.signal}</span>` : "";
      const impactType = item.impact_type ? `<span class="headline-impact">${niceLabel(item.impact_type)}</span>` : "";
      const driverTags = item.driver_tags && item.driver_tags.length
        ? `<div class="headline-tags">${item.driver_tags.map(tag => `<span class="tag-pill">${niceLabel(tag)}</span>`).join(" ")}</div>`
        : "";

      const linkedHeadline = item.url
        ? `<a href="${item.url}" target="_blank" rel="noopener noreferrer">${item.headline}</a>`
        : item.headline;

      return `
        <li class="headline-item">
          <div class="headline-top">
            ${source}
            <div class="headline-meta">
              ${impactType}
              ${signal}
            </div>
          </div>
          <div class="headline-title">${linkedHeadline}</div>
          ${driverTags}
        </li>
      `;
    }).join("");
  } catch (error) {
    console.error(error);
    results.innerHTML = "<p>Error loading live market data.</p>";
    newsList.innerHTML = "";
  }
}

window.runAnalysis = runAnalysis;
