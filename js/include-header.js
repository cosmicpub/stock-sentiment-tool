document.addEventListener("DOMContentLoaded", () => {
  const target = document.getElementById("site-header");
  if (!target) return;

  fetch("/components/header.html")
    .then(res => res.text())
    .then(html => {
      target.innerHTML = html;
    })
    .catch(err => {
      console.error("Header load failed:", err);
    });
});
