(() => {
  const buttons = Array.from(document.querySelectorAll('[data-reader-mode]'));
  if (!buttons.length) return;
  const panels = Array.from(document.querySelectorAll('[data-reader-panel]'));
  const activate = (mode) => {
    buttons.forEach((button) => {
      const active = button.dataset.readerMode === mode;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    panels.forEach((panel) => {
      panel.hidden = panel.dataset.readerPanel !== mode;
    });
  };
  buttons.forEach((button) => {
    button.addEventListener('click', () => {
      activate(button.dataset.readerMode);
      const url = new URL(window.location.href);
      if (button.dataset.readerMode === 'chapter') history.replaceState(null, '', url.pathname + url.search);
    });
  });
  if (window.location.hash.startsWith('#ev-')) {
    activate('evidence');
    const target = document.querySelector(window.location.hash);
    if (target) target.scrollIntoView({block: 'center'});
  }
})();
