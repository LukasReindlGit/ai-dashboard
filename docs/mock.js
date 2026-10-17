/* Demo data for docs/demo.html: stands in for the dashboard's API so the page runs as a static file.
   Everything here is made up (projects, issues, prompts, output). */
(() => {
  const now = Math.floor(Date.now() / 1000), m = 60, h = 3600;
  const hm = (ago) => new Date((now - ago) * 1000).toTimeString().slice(0, 5);
  const C = (c, t) => `<span style="color:${c}">${t}</span>`;
  const dim = (t) => `<span style="opacity:.6">${t}</span>`;
  const rule = '<span class="rule"></span>';

  const sessions = [
    {
      name: "batch-acme-shop", kind: "batch", state: "working", label: "Batch running",
      project: "acme-shop", family: "acme-shop", created: now - 3 * h - 12 * m, attached: false, windows: 1,
      activity: now - 8, running_for: 3 * h + 10 * m, issues_url: "https://github.com/example/acme-shop/issues",
      batch_cmd: null,
      batch: {
        planned: 8, merged: [41, 43, 44, 47], failed: [45],
        current: { issue: 49, title: "Checkout: show delivery estimate per item", how: "new branch", t: hm(14 * m) },
        limit: null, sleeping: null,
        events: [
          { t: hm(98 * m), issue: 44, outcome: "merged", dur: "21m", text: "Add wishlist sharing link" },
          { t: hm(71 * m), issue: 45, outcome: "tests failed", dur: "26m", text: "Migrate cart to new pricing API" },
          { t: hm(39 * m), issue: 47, outcome: "merged", dur: "18m", text: "Fix currency rounding in invoices" },
          { t: hm(14 * m), issue: null, outcome: "info", dur: null, text: "Picking next issue: #49" },
        ],
      },
      preview: [
        dim(hm(14 * m)) + " #49 → working on it (new branch)",
        "  " + C("#2E844A", "✓") + " read src/checkout/summary.tsx",
        "  " + C("#2E844A", "✓") + " added DeliveryEstimate component",
        "  " + C("#0176D3", "⟳") + " running test suite (212 tests)…",
      ],
    },
    {
      name: "acme-shop", kind: "claude", state: "attention", label: "Needs your input",
      project: "acme-shop", family: "acme-shop", created: now - 5 * h, attached: true, windows: 1,
      activity: now - 2 * m, running_for: null, issues_url: "https://github.com/example/acme-shop/issues",
      task: { spinner: null, prompt: "Rename the old discount fields in the orders table and write the migration" },
      preview: [
        C("#5BA2F2", "●") + " I'd like to run the migration against the local database.",
        rule,
        C("#DD7A01", " Do you want to proceed?"),
        " " + C("#0176D3", "❯ 1. Yes"),
        "   2. Yes, and don't ask again for this command",
        "   3. No, tell Claude what to do differently",
      ],
    },
    {
      name: "weather-api", kind: "claude", state: "working", label: "Working",
      project: "weather-api", family: "weather-api", created: now - 47 * m, attached: false, windows: 1,
      activity: now - 3, running_for: null, issues_url: "https://github.com/example/weather-api/issues",
      task: { spinner: "Writing tests… (3m 12s · ↓ 4.1k tokens)", prompt: "Add a /forecast/hourly endpoint with caching and tests" },
      preview: [
        C("#5BA2F2", "●") + " Update(" + C("#2E844A", "app/routes/forecast.py") + ")",
        "  ⎿  Added 38 lines",
        C("#5BA2F2", "●") + " Write(" + C("#2E844A", "tests/test_hourly.py") + ")",
        "  ⎿  Wrote 64 lines",
        C("#DD7A01", "✻ Writing tests… ") + dim("(3m 12s · ↓ 4.1k tokens)"),
      ],
    },
    {
      name: "batch-weather-api", kind: "batch", state: "waiting", label: "Sleeping 25 min (usage limit)",
      project: "weather-api", family: "weather-api", created: now - 6 * h, attached: false, windows: 1,
      activity: now - 5 * m, running_for: 5 * h + 50 * m, issues_url: "https://github.com/example/weather-api/issues",
      batch_cmd: null,
      batch: {
        planned: 5, merged: [12, 13, 15], failed: [],
        current: { issue: 16, title: "Return wind gusts in metric and imperial", how: "resumed", t: hm(5 * m) },
        limit: null, sleeping: { min: 25, t: hm(5 * m) },
        events: [
          { t: hm(2 * h), issue: 13, outcome: "merged", dur: "16m", text: "Cache geocoding lookups" },
          { t: hm(80 * m), issue: 15, outcome: "merged", dur: "24m", text: "Rate-limit anonymous requests" },
          { t: hm(5 * m), issue: 16, outcome: "limit", dur: "9m", text: "Usage limit reached, sleeping 25 min" },
        ],
      },
      preview: [
        dim(hm(5 * m)) + " #16 → usage limit reached",
        dim(hm(5 * m)) + " sleeping 25 min, then resuming #16",
      ],
    },
    {
      name: "notes-app", kind: "claude", state: "idle", label: "Waiting for you",
      project: "notes-app", family: "notes-app", created: now - 2 * h, attached: false, windows: 1,
      activity: now - 26 * m, running_for: null, issues_url: "https://github.com/example/notes-app/issues",
      task: { spinner: null, prompt: "Make the editor toolbar work on small screens" },
      preview: [
        C("#5BA2F2", "●") + " Done. The toolbar now collapses into a ⋯ menu below 480px",
        "  and the buttons are 44px tall on touch screens.",
        "  Tested in the mobile viewport, all 58 tests pass.",
      ],
    },
    {
      name: "batch-notes-app", kind: "batch", state: "done", label: "Finished",
      project: "notes-app", family: "notes-app", created: now - 9 * h, attached: false, windows: 1,
      activity: now - 3 * h, running_for: null, issues_url: "https://github.com/example/notes-app/issues",
      batch_cmd: null,
      batch: {
        planned: 4, merged: [7, 8, 9, 11], failed: [], current: null, limit: null, sleeping: null,
        events: [
          { t: hm(4 * h), issue: 9, outcome: "merged", dur: "12m", text: "Dark mode for the settings page" },
          { t: hm(3 * h + 10 * m), issue: 11, outcome: "merged", dur: "19m", text: "Export notes as Markdown" },
          { t: hm(3 * h), issue: null, outcome: "info", dur: null, text: "Batch done: 4 of 4 merged" },
        ],
      },
      preview: [dim(hm(3 * h)) + " == Batch done: 4 merged, 0 not merged =="],
    },
    {
      name: "dev-server", kind: "shell", state: "idle", label: "Idle shell",
      project: "blog-engine", family: "blog-engine", created: now - 26 * h, attached: false, windows: 2,
      activity: now - 40 * m, running_for: null, issues_url: "https://github.com/example/blog-engine/issues",
      preview: [
        C("#2E844A", "✓") + " Compiled in 412 ms",
        "  ➜  Local:   http://localhost:5173/",
        dim("  watching for file changes…"),
      ],
    },
  ];

  const limits = {
    updated: now - 40,
    rate_limits: {
      five_hour: { used_percentage: 62, resets_at: now + 1 * h + 50 * m },
      seven_day: { used_percentage: 38, resets_at: now + 3 * 86400 + 4 * h },
    },
  };

  const cron = {
    available: true, now, rev: "demo", writable: true, jobs: [],
  };

  const routes = {
    "/api/sessions": () => ({ ok: true, now, sessions, limits }),
    "/api/projects": () => ({ projects: ["acme-shop", "blog-engine", "notes-app", "weather-api"].map((name) => ({ name, git: true })) }),
    "/api/cron": () => cron,
  };
  window.fetch = async (url) => {
    const path = String(url).replace(/^\.?/, "").split("?")[0];
    const f = routes[path];
    const body = f ? f() : { ok: true };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  };
})();
