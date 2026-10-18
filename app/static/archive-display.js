"use strict";

document.querySelectorAll("[data-archive-display]").forEach(display => {
  const buttons = [...display.querySelectorAll("[data-archive-mode]")];
  const panels = [...display.querySelectorAll("[data-archive-panel]")];
  const status = display.querySelector(".archive-scan-status");
  const select = mode => {
    buttons.forEach(button => {
      const active = button.dataset.archiveMode === mode;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    panels.forEach(panel => { panel.hidden = panel.dataset.archivePanel !== mode; });
  };
  buttons.forEach(button => button.addEventListener("click", () => {
    if (!button.disabled) select(button.dataset.archiveMode);
  }));
  const scan = display.querySelector("[data-archive-scan]");
  const fallback = () => {
    const button = display.querySelector('[data-archive-mode="b"]');
    button.disabled = true;
    button.setAttribute("aria-describedby", "archive-scan-status");
    select("a");
    status.textContent = "书影加载失败，已回退 A 模式数字题签；文字证据仍可查阅。";
  };
  if (scan) {
    scan.addEventListener("error", fallback);
    if (scan.complete && scan.naturalWidth === 0) fallback();
  }
});
