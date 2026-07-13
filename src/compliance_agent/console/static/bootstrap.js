"use strict";

const token = window.location.hash.slice(1);
const form = document.getElementById("bootstrap-form");
const status = document.getElementById("bootstrap-status");
const help = document.getElementById("bootstrap-help");
const manualButton = document.getElementById("manual-token-button");

if (token) {
  const field = document.getElementById("bootstrap-token");
  if (field) field.value = token;
  history.replaceState(null, "", "/bootstrap");
  form?.requestSubmit();
} else {
  if (status) status.textContent = "Use the automatic launcher to connect securely.";
  if (help) help.hidden = false;
}

manualButton?.addEventListener("click", () => {
  if (form) form.hidden = false;
  manualButton.hidden = true;
  document.getElementById("bootstrap-token")?.focus();
});
