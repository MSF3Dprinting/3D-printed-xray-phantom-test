/* Sign-in page: posts credentials, then reloads into the app. */
"use strict";
document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const err = document.getElementById("login-error");
  err.textContent = "";
  try {
    const r = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: f.username.value,
        password: f.password.value,
      }),
    });
    if (!r.ok) {
      let msg = "Sign-in failed";
      try { msg = (await r.json()).detail || msg; } catch (_) { /* noop */ }
      err.textContent = msg;
      return;
    }
    window.location = "/";
  } catch (ex) {
    err.textContent = "Network error: " + ex.message;
  }
});
