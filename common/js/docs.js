(function () {
  const body = document.body;
  const toggle = document.querySelector('.nav-toggle');
  const sidebar = document.querySelector('.docs-sidebar');
  const search = document.querySelector('.nav-search');

  if (toggle && sidebar) {
    toggle.addEventListener('click', function () {
      const open = body.classList.toggle('nav-open');
      toggle.setAttribute('aria-expanded', String(open));
    });

    sidebar.addEventListener('click', function (event) {
      if (event.target.closest('a') && window.innerWidth <= 900) {
        body.classList.remove('nav-open');
        toggle.setAttribute('aria-expanded', 'false');
      }
    });
  }

  if (search) {
    const items = Array.from(document.querySelectorAll('.docs-nav li'));
    search.addEventListener('input', function () {
      const query = search.value.trim().toLocaleLowerCase();
      items.forEach(function (item) {
        item.hidden = Boolean(query) && !item.textContent.toLocaleLowerCase().includes(query);
      });
    });
  }

  const current = document.querySelector('.docs-nav a[aria-current="page"]');
  if (current) current.scrollIntoView({ block: 'center' });
})();
