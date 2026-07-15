"use strict";

// Loaded without `defer` so a stored theme applies before first paint.
(function () {
  var STORAGE_KEY = "console-theme";
  var root = document.documentElement;
  root.classList.add("js");

  function storedTheme() {
    try {
      return window.localStorage.getItem(STORAGE_KEY);
    } catch (error) {
      return null;
    }
  }

  function persist(theme) {
    try {
      if (theme === null) {
        window.localStorage.removeItem(STORAGE_KEY);
      } else {
        window.localStorage.setItem(STORAGE_KEY, theme);
      }
    } catch (error) {
      /* Storage may be unavailable; the in-page theme still applies. */
    }
  }

  function apply(theme) {
    if (theme === "light" || theme === "dark") {
      root.dataset.theme = theme;
    } else {
      delete root.dataset.theme;
    }
  }

  function labelFor(theme) {
    if (theme === "light") {
      return "Light";
    }
    if (theme === "dark") {
      return "Dark";
    }
    return "Auto";
  }

  var initial = storedTheme();
  if (initial === "light" || initial === "dark") {
    apply(initial);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var toggle = document.getElementById("theme-toggle");
    if (!toggle) {
      return;
    }
    var label = toggle.querySelector(".theme-label");
    var current = storedTheme();
    if (current !== "light" && current !== "dark") {
      current = null;
    }
    if (label) {
      label.textContent = labelFor(current);
    }
    toggle.addEventListener("click", function () {
      var next = current === null ? "dark" : current === "dark" ? "light" : null;
      current = next;
      apply(next);
      persist(next);
      if (label) {
        label.textContent = labelFor(next);
      }
      toggle.setAttribute("aria-pressed", next === null ? "false" : "true");
    });
  });
})();
