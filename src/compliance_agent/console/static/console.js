"use strict";

document.addEventListener("htmx:responseError", (event) => {
  const message = event.detail?.xhr?.responseText || "The local request could not be completed.";
  const region = document.createElement("div");
  region.className = "alert error";
  region.setAttribute("role", "alert");
  region.textContent = message;
  document.querySelector("main")?.prepend(region);
});
