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
    const response = await fetch(`${WORKER_URL}?ticker=${encodeURIComponent(ticker)}`, {
      method: "GET"
    });

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
      <p><strong>Price:</strong> ${formatMoney(data.price)}</p>
      <p><strong>Daily Change:</strong> ${formatMoney(data.change)} (${formatPercent(data.percent_change)})</p>
    `;

    if (!data.news || data.news.length === 0) {
      newsList.innerHTML = "<li>No recent headlines found.</li>";
      return;
    }

    newsList.innerHTML = data.news.map(item => {
      const source = item.source ? `<strong>${item.source}</strong>: ` : "";
      const signal = item.signal ? ` <em>(${item.signal})</em>` : "";
      const linkOpen = item.url ? `<a href="${item.url}" target="_blank" rel="noopener noreferrer">` : "";
      const linkClose = item.url ? `</a>` : "";

      return `<li>${source}${linkOpen}${item.headline}${linkClose}${signal}</li>`;
    }).join("");
  } catch (error) {
    console.error(error);
    results.innerHTML = "<p>Error loading live market data.</p>";
    newsList.innerHTML = "";
  }
}

window.runAnalysis = runAnalysis;
