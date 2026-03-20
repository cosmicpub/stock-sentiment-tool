const tickerInput = document.getElementById("tickerInput");
const results = document.getElementById("results");
const newsList = document.getElementById("newsList");

let marketData = null;

async function loadMarketData() {
  const response = await fetch("data/market-data.json?ts=" + Date.now(), { cache: "no-store" });
  if (!response.ok) {
    throw new Error("Could not load market data.");
  }
  marketData = await response.json();
}

function formatMoney(value) {
  if (typeof value !== "number") return "N/A";
  return `$${value.toFixed(2)}`;
}

function formatPercent(value) {
  if (typeof value !== "number") return "N/A";
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
    if (!marketData) {
      await loadMarketData();
    }

    const stock = (marketData.stocks || []).find(
      item => item.ticker.toUpperCase() === ticker
    );

    if (!stock) {
      results.innerHTML = `<p>No stored data found for <strong>${ticker}</strong>.</p>`;
      newsList.innerHTML = "";
      return;
    }

    const className = sentimentClass(stock.sentiment);

    results.innerHTML = `
      <h2>${stock.ticker}</h2>
      <div class="result ${className}">
        ${stock.sentiment} (Score: ${stock.sentiment_score})
      </div>
      <p><strong>Price:</strong> ${formatMoney(stock.price)}</p>
      <p><strong>Daily Change:</strong> ${formatMoney(stock.change)} (${formatPercent(stock.percent_change)})</p>
      <p><strong>Confidence:</strong> ${stock.confidence}</p>
      <p><strong>Reason:</strong> ${stock.reason}</p>
      <p><strong>Last Updated:</strong> ${marketData.updated_at || "Not available yet"}</p>
    `;

    if (!stock.news || stock.news.length === 0) {
      newsList.innerHTML = "<li>No recent headlines found.</li>";
      return;
    }

    newsList.innerHTML = stock.news.map(item => {
      const source = item.source ? `<strong>${item.source}</strong>: ` : "";
      const signal = item.signal ? ` <em>(${item.signal})</em>` : "";
      const linkOpen = item.url ? `<a href="${item.url}" target="_blank" rel="noopener noreferrer">` : "";
      const linkClose = item.url ? `</a>` : "";

      return `<li>${source}${linkOpen}${item.headline}${linkClose}${signal}</li>`;
    }).join("");
  } catch (error) {
    console.error(error);
    results.innerHTML = "<p>Error loading market data.</p>";
    newsList.innerHTML = "";
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    await loadMarketData();
  } catch (error) {
    console.error("Initial data load failed:", error);
  }
});

window.runAnalysis = runAnalysis;
