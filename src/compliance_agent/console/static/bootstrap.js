"use strict";

const token = window.location.hash.slice(1);
if (token) {
  const field = document.getElementById("bootstrap-token");
  if (field) field.value = token;
  history.replaceState(null, "", "/bootstrap");
  document.getElementById("bootstrap-form")?.requestSubmit();
}
