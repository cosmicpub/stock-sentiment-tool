fetch('/components/footer.html')
  .then(response => {
    if (!response.ok) throw new Error('Failed to load footer');
    return response.text();
  })
  .then(data => {
    document.getElementById('site-footer').innerHTML = data;
  })
  .catch(error => {
    console.error('Footer load error:', error);
  });
