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

  const scans = [...display.querySelectorAll("[data-archive-scan]")];
  const scanFailed = scan => {
    const card = scan.closest(".archive-scan-card");
    if (card) card.classList.add("is-scan-error");
    if (status) status.textContent = "真实书影加载失败；AI 拟古绘像仍可查看，A 模式文字证据不受影响。";
  };
  scans.forEach(scan => {
    scan.addEventListener("error", () => scanFailed(scan));
    if (scan.complete && scan.naturalWidth === 0) scanFailed(scan);
  });
});
