(function () {
  "use strict";
  window.baiChartTheme = {
    colors: function () {
      var light = document.documentElement.getAttribute("data-theme") === "light";
      return {
        text: light ? "#475569" : "#9aa4bd",
        grid: light ? "rgba(15,23,42,0.08)" : "rgba(255,255,255,0.05)",
        border: light ? "rgba(15,23,42,0.1)" : "rgba(255,255,255,0.06)",
        brand: "#5b7cfa",
        brandFill: light ? "rgba(91,124,250,0.12)" : "rgba(91,124,250,0.15)",
        mint: light ? "#0d8a6b" : "#34d9b8",
        mintFill: light ? "rgba(13,138,107,0.14)" : "rgba(52,217,184,0.15)",
        red: light ? "#dc2626" : "#f87171",
        redFill: light ? "rgba(220,38,38,0.12)" : "rgba(248,113,113,0.15)",
        amber: light ? "#b45309" : "#fbbf24",
        amberFill: light ? "rgba(180,83,9,0.14)" : "rgba(251,191,36,0.15)",
        donut: light
          ? ["#4361ee", "#0d8a6b", "#dc2626", "#d97706", "#7c3aed", "#0284c7", "#ea580c", "#db2777"]
          : ["#5b7cfa", "#34d9b8", "#f87171", "#fbbf24", "#a78bfa", "#38bdf8", "#fb923c", "#f472b6"],
      };
    },
    // Applies the current theme's text/grid colors as Chart.js defaults.
    // Call this (again) before (re-)building charts, including after a
    // theme change, since values already baked into a chart's own options
    // (e.g. a scale's explicit grid.color) don't pick up new defaults on
    // their own.
    applyDefaults: function () {
      var c = baiChartTheme.colors();
      Chart.defaults.color = c.text;
      Chart.defaults.borderColor = c.border;
      Chart.defaults.font.family = "Inter, ui-sans-serif, system-ui, sans-serif";
      return c;
    },
    // Registers a render function that (re)builds every chart on the page;
    // it's called once now and again on every theme change, after first
    // destroying whatever charts the previous call created (several chart
    // options are fixed at construction time, so re-theming means
    // rebuilding rather than mutating live instances).
    onThemeChange: function (render) {
      var charts = [];
      function run() {
        charts.forEach(function (c) { c.destroy(); });
        charts = render(baiChartTheme.applyDefaults()) || [];
      }
      run();
      document.addEventListener("bai:themechange", run);
    },
    // Lighter-weight alternative for pages that build charts dynamically
    // (DOM inserted in a loop, lazily on fetch, etc.), where a full
    // destroy-and-rebuild risks duplicating that DOM. Re-applies
    // Chart.defaults and walks each chart's scales updating any explicitly
    // set grid.color (axis tick/legend text already follow Chart.defaults
    // automatically); dataset colors picked at creation time are left as
    // they were, which stays readable in both themes.
    restyle: function (charts) {
      var c = baiChartTheme.applyDefaults();
      (charts || []).forEach(function (chart) {
        if (!chart || !chart.options) return;
        var scales = chart.options.scales || {};
        Object.keys(scales).forEach(function (key) {
          if (scales[key] && scales[key].grid && "color" in scales[key].grid) {
            scales[key].grid.color = c.grid;
          }
        });
        chart.update();
      });
    },
  };
})();
