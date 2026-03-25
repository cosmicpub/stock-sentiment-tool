document.addEventListener("DOMContentLoaded", () => {
  const target = document.getElementById("site-footer");
  if (!target) return;

  fetch("/stock-sentiment-tool/components/footer.html")
    .then(res => res.text())
    .then(html => {
      target.innerHTML = html;
    })
    .catch(err => {
      console.error("Footer load failed:", err);
    });
});
