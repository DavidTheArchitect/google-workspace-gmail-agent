"use strict";

// Progressive enhancement: humanize every <time data-relative datetime=...>.
// The server-rendered absolute text is the no-JS fallback; the exact
// timestamp stays available in the title attribute.
(function () {
  const MINUTE = 60000;
  const HOUR = 3600000;
  const DAY = 86400000;
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function phrase(value, unit) {
    return `${value} ${unit}${value === 1 ? "" : "s"}`;
  }

  function label(then, now) {
    const diff = now - then; // positive means past
    const abs = Math.abs(diff);
    if (abs < 45000) {
      return "just now";
    }
    const future = diff < 0;
    let body;
    if (abs < HOUR) {
      body = phrase(Math.round(abs / MINUTE), "min");
    } else if (abs < DAY) {
      body = phrase(Math.round(abs / HOUR), "hour");
    } else if (abs < 7 * DAY) {
      const days = Math.round(abs / DAY);
      if (days === 1) {
        return future ? "tomorrow" : "yesterday";
      }
      body = phrase(days, "day");
    } else {
      const date = new Date(then);
      const short = `${MONTHS[date.getMonth()]} ${date.getDate()}`;
      return date.getFullYear() === new Date(now).getFullYear()
        ? short
        : `${short}, ${date.getFullYear()}`;
    }
    return future ? `in ${body}` : `${body} ago`;
  }

  function refresh(root) {
    const scope = root instanceof Element || root instanceof Document ? root : document;
    const now = Date.now();
    scope.querySelectorAll("time[data-relative]").forEach((node) => {
      const then = Date.parse(node.getAttribute("datetime") || "");
      if (!Number.isNaN(then)) {
        node.textContent = label(then, now);
      }
    });
  }

  function start() {
    refresh(document);
    window.setInterval(() => refresh(document), 30000);
    // SSE swaps re-render the run-status fragment; humanize new nodes at once.
    document.addEventListener("htmx:afterSwap", (event) => refresh(event.target));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
