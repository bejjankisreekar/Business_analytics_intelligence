(function () {
  "use strict";
  window.bai = window.bai || {};

  var STORAGE_KEY = "bai-theme";

  function apply(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    document.documentElement.style.colorScheme = theme === "light" ? "light" : "dark";
  }

  bai.theme = {
    get: function () {
      return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
    },
    set: function (theme) {
      theme = theme === "light" ? "light" : "dark";
      try { localStorage.setItem(STORAGE_KEY, theme); } catch (e) { /* storage unavailable */ }
      apply(theme);
      document.dispatchEvent(new CustomEvent("bai:themechange", { detail: { theme: theme } }));
    },
    toggle: function () {
      bai.theme.set(bai.theme.get() === "light" ? "dark" : "light");
    },
  };

  // Delegated on document (not queried directly) since this script loads in
  // <head>, before <body> — and its toggle buttons — exist yet; delegation
  // also covers any toggle button added later (e.g. inside a modal).
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-theme-toggle]")) bai.theme.toggle();
  });
})();
