"use strict";

const healthButton = document.getElementById("check-health");
const healthStatus = document.getElementById("health-status");

if (healthButton && healthStatus) {
  healthButton.addEventListener("click", async () => {
    healthButton.disabled = true;
    healthStatus.textContent = "正在检查…";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch("/health", { signal: controller.signal, cache: "no-store" });
      const result = await response.json();
      if (!response.ok || result.status !== "ok") throw new Error("Service unavailable");
      healthStatus.textContent = "应用与数据库连接正常";
    } catch {
      healthStatus.textContent = "服务检查失败，请稍后重试";
    } finally {
      clearTimeout(timeout);
      healthButton.disabled = false;
    }
  });
}

document.querySelectorAll(".copy-quote[data-copy-target]").forEach((button) => {
  button.hidden = !navigator.clipboard?.writeText;
  button.addEventListener("click", async () => {
    const quote = document.getElementById(button.dataset.copyTarget);
    const status = button.closest(".evidence-record").querySelector(".copy-status");
    try {
      await navigator.clipboard.writeText(quote.textContent);
      status.textContent = "原文已复制，可粘贴并结合出处引用。";
    } catch {
      status.textContent = "无法自动复制，请长按或选中原文后复制。";
    }
  });
});

document.querySelectorAll(".source-copy-quote").forEach((button) => {
  button.hidden = !navigator.clipboard?.writeText;
  button.addEventListener("click", async () => {
    const quote = document.getElementById(button.dataset.copyTarget);
    const status = button.closest(".source-original-entry").querySelector(".source-copy-status");
    try {
      await navigator.clipboard.writeText(quote.textContent);
      status.textContent = "原文已复制，请结合出处引用。";
    } catch {
      status.textContent = "无法自动复制，请长按或选中原文后复制。";
    }
  });
});

const rawDialog = document.getElementById("raw-record-dialog");
if (rawDialog && typeof rawDialog.showModal === "function") {
  const rawTitle = document.getElementById("raw-record-title");
  const rawContent = rawDialog.querySelector("[data-raw-content]");
  const rawStatus = document.getElementById("raw-record-status");
  const rawCopy = rawDialog.querySelector("[data-copy-raw]");
  const directLink = document.getElementById("raw-record-direct");
  let rawTrigger = null;
  let activeRequest = null;

  document.querySelectorAll("a[data-raw-record]").forEach((link) => {
    link.addEventListener("click", async (event) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      activeRequest?.abort();
      const controller = new AbortController();
      activeRequest = controller;
      rawTrigger = link;
      rawTitle.textContent = `${link.dataset.rawTitle || "来源记录"} · 原始数据`;
      rawContent.textContent = "";
      rawContent.hidden = true;
      rawCopy.disabled = true;
      rawStatus.textContent = "正在读取原始数据…";
      directLink.href = link.href;
      if (!rawDialog.open) rawDialog.showModal();
      rawDialog.setAttribute("aria-busy", "true");
      const timeout = setTimeout(() => controller.abort(), 15000);
      try {
        const response = await fetch(link.href, { headers: { Accept: "application/json" }, signal: controller.signal, cache: "no-store" });
        if (!response.ok) throw new Error("Record unavailable");
        const data = await response.json();
        if (activeRequest !== controller || !rawDialog.open) return;
        rawContent.textContent = JSON.stringify(data, null, 2);
        rawContent.hidden = false;
        rawCopy.disabled = !navigator.clipboard?.writeText;
        rawStatus.textContent = "原始数据已载入。";
      } catch {
        if (activeRequest !== controller || !rawDialog.open) return;
        rawStatus.textContent = "暂时无法读取原始数据，请关闭后重试，或直接打开 JSON。";
      } finally {
        clearTimeout(timeout);
        if (activeRequest === controller) rawDialog.removeAttribute("aria-busy");
      }
    });
  });

  rawDialog.querySelector("[data-close-raw]").addEventListener("click", () => rawDialog.close());
  rawDialog.addEventListener("close", () => {
    activeRequest?.abort();
    activeRequest = null;
    rawContent.textContent = "";
    rawContent.hidden = true;
    rawCopy.disabled = true;
    rawDialog.removeAttribute("aria-busy");
    rawTrigger?.focus();
  });
  rawCopy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(rawContent.textContent);
      rawStatus.textContent = "原始数据已复制。";
    } catch {
      rawStatus.textContent = "无法自动复制，请选中原始数据后复制。";
    }
  });
}

const searchForm = document.querySelector(".search-form");
if (searchForm) {
  searchForm.addEventListener("submit", () => {
    if (!searchForm.checkValidity()) return;
    searchForm.setAttribute("aria-busy", "true");
    searchForm.querySelector("button[type=submit]").textContent = "正在检索…";
  });
  window.addEventListener("pageshow", () => {
    searchForm.removeAttribute("aria-busy");
    searchForm.querySelector("button[type=submit]").textContent = "检索称呼";
  });
}

