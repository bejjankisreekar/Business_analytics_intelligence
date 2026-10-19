/* Superadmin console charts: one validated palette and shared Chart.js
   options so every console chart reads as one system.
   Categorical order is fixed (never cycled); light and dark are separately
   stepped sets that passed the dataviz palette validator against the
   console's surfaces. Status colours are reserved for good/bad states.
   Needs Chart.js and static/js/chart-theme.js loaded first. */
(function () {
  "use strict";
  function light() { return document.documentElement.getAttribute("data-theme") === "light"; }

  const CAT = {
    light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#6250d6"],
    dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#9085e9"],
  };

  window.saCharts = {
    palette() {
      const l = light();
      return {
        cat: l ? CAT.light : CAT.dark,
        good: l ? "#0b8a69" : "#3ddbb4",
        bad: l ? "#d42a2a" : "#ff7a7a",
        warn: l ? "#b45309" : "#fbbf24",
        accent: l ? "#c2680a" : "#f5a524",
        muted: l ? "rgba(100,116,139,0.45)" : "rgba(148,163,184,0.4)",
        surface: l ? "#ffffff" : "#10131a",
        grid: l ? "rgba(15,23,42,0.07)" : "rgba(255,255,255,0.05)",
        text: l ? "#475569" : "#a3adc2",
      };
    },
    money(v) {
      const n = Math.abs(v);
      const s = n >= 1e7 ? (n / 1e7).toFixed(1) + "Cr" : n >= 1e5 ? (n / 1e5).toFixed(1) + "L" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : Math.round(n).toString();
      return (v < 0 ? "−₹" : "₹") + s;
    },
    tooltip(extra) {
      const p = saCharts.palette();
      return Object.assign({
        backgroundColor: light() ? "#0f172a" : "#1a1f2c",
        titleColor: "#e8ecf4", bodyColor: "#cbd5e1", borderColor: "rgba(255,255,255,0.08)", borderWidth: 1,
        padding: 10, cornerRadius: 8, boxPadding: 4, usePointStyle: true,
      }, extra || {});
    },
    /* Bars/lines over months. opts: {money: bool, stacked: bool, legend: bool} */
    axes(opts) {
      const p = saCharts.palette();
      opts = opts || {};
      return {
        x: { stacked: !!opts.stacked, grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 12 } },
        y: {
          stacked: !!opts.stacked, beginAtZero: true, grid: { color: p.grid }, border: { display: false },
          ticks: { maxTicksLimit: 5, callback: opts.money ? (v) => saCharts.money(v) : undefined },
        },
      };
    },
    legend(show) {
      return { display: show !== false, position: "bottom", labels: { boxWidth: 8, boxHeight: 8, usePointStyle: true, padding: 14 } };
    },
    /* A tiny line with no axes for stat tiles. */
    sparkline(canvas, values, color) {
      if (!canvas || !values || !values.length) return null;
      const ctx = canvas.getContext("2d");
      const h = canvas.parentElement ? canvas.parentElement.clientHeight || 36 : 36;
      const grad = ctx.createLinearGradient(0, 0, 0, h);
      grad.addColorStop(0, color + "55");
      grad.addColorStop(1, color + "00");
      return new Chart(canvas, {
        type: "line",
        data: { labels: values.map((_, i) => i), datasets: [{ data: values, borderColor: color, backgroundColor: grad, fill: true, borderWidth: 2, pointRadius: 0, tension: 0.35 }] },
        options: {
          responsive: true, maintainAspectRatio: false, animation: false, events: [],
          plugins: { legend: { display: false }, tooltip: { enabled: false } },
          scales: { x: { display: false }, y: { display: false, beginAtZero: true } },
        },
      });
    },
    doughnut(canvas, rows, opts) {
      const p = saCharts.palette();
      opts = opts || {};
      return new Chart(canvas, {
        type: "doughnut",
        data: {
          labels: rows.map((r) => r[0] + " · " + (opts.money ? saCharts.money(r[1]) : r[1])),
          datasets: [{ data: rows.map((r) => r[1]), backgroundColor: rows.map((_, i) => (opts.colors || p.cat)[i % p.cat.length]), borderColor: p.surface, borderWidth: 2, hoverOffset: 4 }],
        },
        options: {
          responsive: true, maintainAspectRatio: false, cutout: "68%",
          plugins: { legend: saCharts.legend(), tooltip: saCharts.tooltip() },
        },
      });
    },
  };
})();
