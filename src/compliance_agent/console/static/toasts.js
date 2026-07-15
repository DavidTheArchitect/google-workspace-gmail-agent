"use strict";

(function () {
  const region = () => document.getElementById("toast-region");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function arm(toast) {
    let remaining = 6000;
    let started = Date.now();
    let timer;
    const dismiss = () => {
      toast.classList.add("toast-leaving");
      window.setTimeout(() => toast.remove(), reducedMotion ? 0 : 180);
    };
    const resume = () => {
      started = Date.now();
      timer = window.setTimeout(dismiss, remaining);
    };
    const pause = () => {
      window.clearTimeout(timer);
      remaining = Math.max(0, remaining - (Date.now() - started));
    };
    toast.addEventListener("mouseenter", pause);
    toast.addEventListener("mouseleave", resume);
    toast.addEventListener("focusin", pause);
    toast.addEventListener("focusout", resume);
    resume();
  }

  function push(message, tone = "info") {
    const target = region();
    if (!target) return;
    const toast = document.createElement("div");
    toast.className = `toast tone-${tone}`;
    toast.dataset.toast = "";
    toast.setAttribute("role", tone === "error" ? "alert" : "status");
    const text = document.createElement("span");
    text.className = "notice-text";
    text.textContent = message;
    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "icon-button";
    dismiss.dataset.dismiss = "";
    dismiss.setAttribute("aria-label", "Dismiss notification");
    dismiss.textContent = "×";
    toast.append(text, dismiss);
    target.append(toast);
    while (target.querySelectorAll("[data-toast]").length > 4) {
      target.querySelector("[data-toast]")?.remove();
    }
    arm(toast);
  }

  window.consoleToasts = { push };
  document.querySelectorAll("[data-toast]").forEach(arm);
})();
