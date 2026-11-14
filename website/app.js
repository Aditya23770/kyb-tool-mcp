// Content lives here, separate from the markup. The page reads fine without this script
// (except the example card and install panel, which need it).
const DOCS = "https://github.com/Aditya23770/kyb-tool-mcp#readme";
// The hosted connector. Replace with the Cloud Run URL after the first deploy (one place only).
const CONNECTOR_URL = "https://CONNECTOR-URL-PENDING.run.app/mcp";
const CH_KEY_URL = "https://developer.company-information.service.gov.uk/";

// Illustrative review outputs. Flag codes, severities and the decision rule are the real ones.
const CASES = [
  {
    ask: "“Run a KYB review on company 09876543, business software, director Sarah Holloway, born June 1979.”",
    decision: "approve", meta: "risk low · score 0",
    flags: [{ sev: "ok", code: "No flags", ev: "registry, 2 owners, 3 sanctions screens and website checked" }],
  },
  {
    ask: "“Review Harbourline Logistics Ltd, website harbourline-logistics.co.uk.”",
    decision: "escalate", meta: "risk high · score 30",
    flags: [{ sev: "high", code: "WEBSITE_UNREACHABLE", ev: "evidence ev_website_3f9a1c07d2e4" }],
  },
  {
    ask: "“Check Northwind Trade Partners Ltd and its directors.”",
    decision: "reject", meta: "risk critical · score 100",
    flags: [
      { sev: "critical", code: "SANCTIONS_MATCH", ev: "director matches OFAC SDN entry · ev_ofac_sdn_8b20e6f14a93" },
      { sev: "ok", code: "2 false positives cleared", ev: "birth year differs by more than 1 year" },
    ],
  },
];

// Each step is [label, code]. A null code renders the label as a plain instruction.
const METHODS = {
  claude: {
    intro: "Works on claude.ai (web and mobile) and Claude Desktop. About 5 minutes, most of it getting the free key.",
    steps: [
      [`1. Get a free Companies House API key: register at ${CH_KEY_URL}, create an application, then create a REST key.`, null],
      ["2. In Claude, open Customize → Connectors → Add custom connector. Name it TrustLedger and paste this URL:", CONNECTOR_URL],
      ["3. Under Request headers, add this header name, with your Companies House key as the value:", "X-Companies-House-Key"],
      ["4. Click Add, then Connect. Try: “Run a KYB check on company 00445790, website tesco.com.”", null],
    ],
    note: "Free plan: one custom connector. Team and Enterprise: an Owner adds it in Organization settings, then members click Connect. Never paste your key into the chat.",
  },
  pip: {
    intro: "Install the package into your own environment (Python 3.11+). You get the CLI, the Python library and the MCP server.",
    steps: [
      ["Install and build the sanctions index (re-run daily)", `pip install kyb-tool-mcp
kyb-tool-mcp update-lists`],
      ["Use it from the command line", `kyb-tool-mcp review case.json --exit-code   # 0 approve, 3 escalate, 4 reject
kyb-tool-mcp screen "Viktor Borovkov" --dob 1961-03
kyb-tool-mcp registry 00445790`],
      ["Or from Python", `from kyb_tool_mcp import review_case

result = await review_case({"case_id": "c1", "legal_name": "Acme Ltd", "registration_number": "01234567"})`],
      ["Or run your own connector (Streamable HTTP at /mcp)", "kyb-tool-mcp serve --http --port 8080"],
    ],
  },
  other: {
    intro: "Claude Code, Cursor, VS Code and other MCP clients. Use the hosted URL, or run it locally so nothing leaves your machine.",
    steps: [
      ["Claude Code (hosted)", `claude mcp add --transport http trustledger ${CONNECTOR_URL} \\
  --header "X-Companies-House-Key: your-key"`],
      ["Cursor .cursor/mcp.json (hosted)", JSON.stringify({
        mcpServers: { trustledger: { url: CONNECTOR_URL, headers: { "X-Companies-House-Key": "your-key" } } },
      }, null, 2)],
      ["Local instead: build the sanctions index once, then add the server", `uvx kyb-tool-mcp update-lists
claude mcp add kyb-tool-mcp -e COMPANIES_HOUSE_API_KEY=your-key -- uvx kyb-tool-mcp`],
      ["Local in Claude Desktop or Cursor config", JSON.stringify({
        mcpServers: { "kyb-tool-mcp": {
          command: "uvx", args: ["kyb-tool-mcp"],
          env: { COMPANIES_HOUSE_API_KEY: "your-key" },
        } },
      }, null, 2)],
    ],
  },
};

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

function selectTab(list, btn) {
  $$('[role="tab"]', list).forEach((b) => b.setAttribute("aria-selected", String(b === btn)));
}

// Example case card
function showCase(i) {
  const c = CASES[i];
  const body = $(".case-body");
  $('[data-f="ask"]', body).textContent = c.ask;
  const word = $('[data-f="decision"]', body);
  word.textContent = c.decision;
  word.className = `verdict-word ${c.decision}`;
  $('[data-f="meta"]', body).textContent = c.meta;
  const ul = $('[data-f="flags"]', body);
  ul.replaceChildren(...c.flags.map((f) => {
    const li = el("li");
    li.append(el("span", `sev ${f.sev}`, f.sev === "ok" ? "ok" : f.sev), el("span", "flag-code", f.code), el("span", "flag-ev", f.ev));
    return li;
  }));
  body.classList.remove("swap"); void body.offsetWidth; body.classList.add("swap");
}
$$(".case-picker button").forEach((b) => b.addEventListener("click", () => {
  selectTab($(".case-picker"), b); showCase(Number(b.dataset.case));
}));
showCase(0);

// Install panel
function copyButton(text) {
  const b = el("button", "copy", "Copy");
  b.type = "button";
  b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(text); b.textContent = "Copied"; }
    catch { b.textContent = "Select and copy"; }
    setTimeout(() => (b.textContent = "Copy"), 1600);
  });
  return b;
}
function showMethod(key) {
  const m = METHODS[key];
  $('[data-f="intro"]').textContent = m.intro;
  const steps = m.steps.map(([label, cmd]) => {
    const wrap = el("div", "m-step");
    wrap.append(el("p", "m-step-label", label));
    if (cmd != null) {
      const code = el("div", "code");
      code.append(el("pre", null, cmd), copyButton(cmd));
      wrap.append(code);
    }
    return wrap;
  });
  const extra = m.note ? [el("p", "m-note", m.note)] : [];
  const more = el("p", "sub small");
  const a = el("a", null, "Full setup and troubleshooting in the README");
  a.href = DOCS; more.append(a);
  $('[data-f="steps"]').replaceChildren(...steps, ...extra, more);
}
function openMethod(key) {
  const btn = $(`.methods button[data-m="${key}"]`);
  if (btn) { selectTab($(".methods"), btn); showMethod(key); }
}
$$(".methods button").forEach((b) => b.addEventListener("click", () => openMethod(b.dataset.m)));
$$("[data-goto]").forEach((a) => a.addEventListener("click", () => openMethod(a.dataset.goto)));
showMethod("claude");

// Connector URL in the hero, with a copy button
(() => {
  const box = $(".connector");
  if (!box) return;
  $('[data-f="connector-url"]', box).textContent = CONNECTOR_URL;
  const b = copyButton(CONNECTOR_URL);
  b.classList.add("copy-light");
  box.append(b);
})();

// Arrow-key navigation inside tab lists
$$('[role="tablist"]').forEach((list) => list.addEventListener("keydown", (e) => {
  if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
  const tabs = $$('[role="tab"]', list);
  const i = tabs.indexOf(document.activeElement);
  const next = tabs[(i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
  next.focus(); next.click();
}));

// Benchmark numbers from the latest run, if published
fetch("assets/results.json").then((r) => (r.ok ? r.json() : null)).then((d) => {
  if (!d) return;
  $$("[data-k]").forEach((n) => { if (d[n.dataset.k] != null) n.textContent = d[n.dataset.k]; });
}).catch(() => {});
