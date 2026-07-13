"use strict";

document.addEventListener("htmx:responseError", (event) => {
  const message = event.detail?.xhr?.responseText || "The local request could not be completed.";
  const region = document.createElement("div");
  region.className = "alert error";
  region.setAttribute("role", "alert");
  region.textContent = message;
  document.querySelector("main")?.prepend(region);
});

// A run's SSE stream emits `settled` once it leaves an active phase; reload so
// the action rail and approval forms re-render from the authoritative server.
document.addEventListener("htmx:sseMessage", (event) => {
  if (event.detail?.type === "settled") {
    window.location.reload();
  }
});

// Busy state on every form submit: visible progress plus double-submit protection.
document.addEventListener("submit", (event) => {
  const button = event.target?.querySelector?.("button[type=submit]");
  if (button && !button.disabled) {
    button.classList.add("is-busy");
    window.setTimeout(() => {
      button.disabled = true;
    }, 0);
  }
});

const announce = (message) => {
  let region = document.getElementById("copy-status");
  if (!region) {
    region = document.createElement("div");
    region.id = "copy-status";
    region.className = "visually-hidden";
    region.setAttribute("aria-live", "polite");
    document.body.append(region);
  }
  region.textContent = message;
};

// Copy-to-clipboard buttons, truncated-hash reveal toggles, and notice dismissal.
document.addEventListener("click", (event) => {
  const dismiss = event.target instanceof Element ? event.target.closest("[data-dismiss]") : null;
  if (dismiss) {
    dismiss.closest(".notice-banner")?.remove();
    return;
  }
  const copyButton = event.target instanceof Element ? event.target.closest("[data-copy]") : null;
  if (copyButton && navigator.clipboard) {
    navigator.clipboard.writeText(copyButton.dataset.copy).then(() => {
      copyButton.classList.add("copied");
      announce("Copied to clipboard");
      window.setTimeout(() => copyButton.classList.remove("copied"), 1600);
    });
    return;
  }
  const hash = event.target instanceof Element ? event.target.closest("[data-hash-full]") : null;
  if (hash) {
    const full = hash.dataset.hashFull;
    const truncated = `${full.slice(0, 10)}…`;
    hash.textContent = hash.textContent === full ? truncated : full;
  }
});

// Display-only countdown for the server-owned approval expiry.
const initCountdowns = () => {
  const nodes = document.querySelectorAll("[data-countdown]");
  if (!nodes.length) {
    return;
  }
  const tick = () => {
    const now = Date.now();
    nodes.forEach((node) => {
      const expires = Date.parse(node.dataset.expiresAt || "");
      if (Number.isNaN(expires)) {
        return;
      }
      const remaining = Math.floor((expires - now) / 1000);
      if (remaining <= 0) {
        node.textContent = "Expired — run a new preview";
        node.classList.add("expiring");
        const approve = document.querySelector(".approval-form button[type=submit]");
        if (approve) {
          approve.disabled = true;
        }
        if (!node.dataset.expiryReloaded) {
          node.dataset.expiryReloaded = "true";
          window.location.reload();
        }
        return;
      }
      const minutes = Math.floor(remaining / 60);
      const seconds = String(remaining % 60).padStart(2, "0");
      node.textContent = `${minutes}:${seconds} remaining`;
      if (remaining < 60) {
        node.classList.add("expiring");
      }
    });
  };
  tick();
  window.setInterval(tick, 1000);
};

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initCountdowns);
} else {
  initCountdowns();
}
