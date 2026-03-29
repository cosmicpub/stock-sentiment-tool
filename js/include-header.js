fetch('/components/header.html')
  .then(response => {
    if (!response.ok) throw new Error('Failed to load header');
    return response.text();
  })
  .then(data => {
    document.getElementById('site-header').innerHTML = data;
  })
  .catch(error => {
    console.error('Header load error:', error);
  });
