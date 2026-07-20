// Real DOM/browser simulation test for MediChain's frontend.
//
// This loads the ACTUAL index.html + app.js files (not a reimplementation)
// into jsdom, points them at a live backend, and programmatically fills in
// and submits every form exactly like a user clicking through a browser
// would -- then reads back what actually landed in the DOM.
//
// Run: node dom_test.js   (backend must already be running on :8000)

const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const FRONTEND_DIR = __dirname;
const API_BASE = "http://127.0.0.1:8000";

let failures = 0;
let checks = 0;

function assert(cond, msg) {
  checks++;
  if (!cond) {
    failures++;
    console.error("FAIL:", msg);
  } else {
    console.log("PASS:", msg);
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function loadPage() {
  const html = fs.readFileSync(path.join(FRONTEND_DIR, "index.html"), "utf8");
  const dom = new JSDOM(html, {
    runScripts: "dangerously",
    resources: "usable",
    url: "http://127.0.0.1:3000/index.html",
  });

  // jsdom doesn't ship fetch -- wire in Node's built-in fetch (Node 22 has
  // global fetch natively) onto the window, exactly like a real browser
  // would provide it.
  dom.window.fetch = fetch;
  dom.window.Headers = Headers;
  dom.window.ethereum = {
    request: async ({ method }) => {
      if (method === "eth_chainId") return "0x107d";
      if (method === "eth_requestAccounts") {
        return ["0x4444444444444444444444444444444444444444"];
      }
      if (method === "personal_sign") return "0x" + "11".repeat(65);
      throw new Error(`unexpected wallet method ${method}`);
    },
    on: () => {},
  };
  dom.window.HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
    this.returnValue = "confirm";
    setTimeout(() => this.dispatchEvent(new dom.window.Event("close")), 0);
  };

  const realFetch = dom.window.fetch;
  dom.window.fetch = async (url, options = {}) => {
    if (String(url).endsWith("/api/auth/challenge")) {
      return new Response(JSON.stringify({
        challenge_id: "dom-test-challenge-001",
        message: "MediChain sign-in challenge",
        expires_at: Math.floor(Date.now() / 1000) + 300,
        chain_id: 4221,
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (String(url).endsWith("/api/auth/verify")) {
      return new Response(JSON.stringify({
        access_token: "dom-test-session",
        token_type: "bearer",
        expires_at: Math.floor(Date.now() / 1000) + 3600,
        user: {
          address: "0x4444444444444444444444444444444444444444",
          role: "sponsor",
        },
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (String(url).endsWith("/api/auth/logout")) {
      return new Response(JSON.stringify({ status: "signed_out" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return realFetch(url, options);
  };

  // Manually inject app.js the same way <script src="app.js"> would, since
  // runScripts + external <script src> file:// loading is unreliable in
  // jsdom -- but the CONTENT executed is byte-identical to the shipped file.
  const configJs = fs.readFileSync(path.join(FRONTEND_DIR, "config.js"), "utf8");
  dom.window.eval(configJs);
  dom.window.MEDICHAIN_CONFIG.API_BASE_URL = API_BASE;
  const formSubmissionJs = fs.readFileSync(
    path.join(FRONTEND_DIR, "form_submission.js"),
    "utf8",
  );
  dom.window.eval(formSubmissionJs);
  const appJs = fs.readFileSync(path.join(FRONTEND_DIR, "app.js"), "utf8");
  dom.window.eval(appJs);

  await sleep(300);

  return dom;
}

function fillForm(doc, formId, values) {
  const form = doc.getElementById(formId);
  for (const [name, value] of Object.entries(values)) {
    const el = form.querySelector(`[name="${name}"]`);
    if (!el) throw new Error(`No field named ${name} in #${formId}`);
    el.value = value;
  }
  return form;
}

async function submitForm(dom, formId, values) {
  const doc = dom.window.document;
  const form = fillForm(doc, formId, values);
  const evt = new dom.window.Event("submit", { bubbles: true, cancelable: true });
  form.dispatchEvent(evt);
  await sleep(400); // allow the async fetch chain in the real handler to resolve
}

(async () => {
  const dom = await loadPage();
  const doc = dom.window.document;

  // ---- health indicator actually reflects real backend state ----
  const dotClass = doc.getElementById("healthDot").className;
  assert(dotClass.includes("dot-ok"), `health dot shows backend as reachable (got class="${dotClass}")`);

  // ---- wallet sign-in enables authenticated forms ----
  doc.getElementById("connectWalletBtn").dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await sleep(300);
  assert(doc.getElementById("walletStatus").textContent.includes("0x4444"), "wallet address appears after sign-in");
  assert(doc.getElementById("walletRole").textContent === "sponsor", "wallet role appears after sign-in");

  // ---- Register Trial form: real submit, real backend round trip ----
  await submitForm(dom, "registerForm", {
    trial_id: "DOMTEST-001",
    clinicaltrials_gov_url: "https://clinicaltrials.gov/study/CARDIO-204",
    primary_hypothesis: "Drug X reduces mortality",
    primary_endpoints: "overall survival at 24 months",
    expected_sample_size: "2000",
    integrity_bond: "500",
  });
  const registerOut = doc.getElementById("registerOutput").textContent;
  assert(registerOut.includes('"status": "active"'), "register form: output shows real backend response (status active)");
  assert(registerOut.includes("DOMTEST-001"), "register form: output echoes submitted trial_id from backend, not a stub");

  // ---- Dashboard actually repopulates after registration (auto-refresh) ----
  await sleep(300);
  let rows = [...doc.querySelectorAll("#trialsTable tbody tr")];
  let found = rows.some(r => r.textContent.includes("DOMTEST-001"));
  assert(found, "dashboard table auto-refreshed and shows the newly registered trial");

  // ---- Submit Results form: real fraud-detection round trip ----
  await submitForm(dom, "submitForm", {
    trial_id: "DOMTEST-001",
    report_id: "domtest-report-1",
    publication_url: "https://journal.example.org/cardio-204-results-2025",
    preprint_url: "",
  });
  const submitOut = doc.getElementById("submitOutput").textContent;
  assert(submitOut.includes('"verdict"'), "submit-results form: output contains a real verdict field from the backend");

  // ---- Dashboard shows real integrity flags (or "none"), not a placeholder ----
  await sleep(500);
  rows = [...doc.querySelectorAll("#trialsTable tbody tr")];
  const domtestRow = rows.find(r => r.textContent.includes("DOMTEST-001"));
  assert(!!domtestRow, "dashboard still shows DOMTEST-001 after results submission");
  const rowHtml = domtestRow ? domtestRow.innerHTML : "";
  assert(rowHtml.includes("none") || rowHtml.includes("badge"), "dashboard integrity-flags cell shows real data (badges or 'none'), not stuck on placeholder '—'");

  // ---- Whistleblower flag form ----
  await submitForm(dom, "flagForm", {
    trial_id: "DOMTEST-001",
    description: "reported via automated DOM test",
    evidence_url: "",
  });
  const flagOut = doc.getElementById("flagOutput").textContent;
  assert(flagOut.includes('"status": "open"'), "whistleblower flag form: real backend response with status open");

  // ---- Refresh button is a real, working click handler ----
  doc.getElementById("refreshBtn").dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await sleep(500);
  rows = [...doc.querySelectorAll("#trialsTable tbody tr")];
  assert(rows.length > 0, "Refresh Trials button actually repopulates the table on click");

  // ---- RACE CONDITION CHECK: overlapping refreshTrials() calls used to
  // interleave and produce duplicate rows (confirmed: 3 concurrent calls
  // against a 2-trial backend produced 6 rows before the fix). Fire two
  // refreshes back to back without waiting and confirm no duplicates. ----
  const beforeCount = [...doc.querySelectorAll("#trialsTable tbody tr")].length;
  const p1 = dom.window.refreshTrials();
  const p2 = dom.window.refreshTrials();
  await Promise.all([p1, p2]);
  await sleep(300);
  const afterRows = [...doc.querySelectorAll("#trialsTable tbody tr")];
  const uniqueTrialIds = new Set(afterRows.map(r => r.querySelector("td")?.textContent));
  assert(
    afterRows.length === uniqueTrialIds.size,
    `overlapping refreshTrials() calls do not produce duplicate rows (got ${afterRows.length} rows for ${uniqueTrialIds.size} unique trials)`
  );

  // ---- Details toggle: was previously missing entirely -- report
  // summary/confidence text and whistleblower flag descriptions existed
  // in the backend but were never surfaced anywhere in the UI. ----
  rows = [...doc.querySelectorAll("#trialsTable tbody tr")];
  const domtestMainRow = rows.find(r => r.textContent.includes("DOMTEST-001") && r.querySelector(".details-btn"));
  assert(!!domtestMainRow, "DOMTEST-001 row has a Details button");
  const detailsBtn = domtestMainRow.querySelector(".details-btn");
  detailsBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await sleep(500);
  const detailRow = doc.querySelector("tr.detail-row");
  assert(!!detailRow, "clicking Details actually inserts an expanded detail row");
  assert(
    detailRow.textContent.includes("domtest-report-1"),
    "detail row shows the real report_id from the backend"
  );
  assert(
    detailRow.textContent.toLowerCase().includes("dsmb") || detailRow.textContent.includes("survival"),
    "detail row shows the real LLM-generated summary text, not a placeholder"
  );
  assert(
    detailRow.textContent.includes("0x0000000000000000000000000000000000000001")
      || detailRow.textContent.includes("0x4444444444444444444444444444444444444444"),
    "detail row shows the authenticated wallet as whistleblower submitter"
  );
  // toggle closed again -- IMPORTANT: refreshTrials() just rebuilt the
  // tbody, so the earlier `detailsBtn` reference is now a detached node
  // (dispatching click on it wouldn't bubble to the live tbody listener).
  // Re-query the live button before the second click.
  const liveDetailsBtn = [...doc.querySelectorAll("#trialsTable tbody .details-btn")]
    .find(b => b.dataset.trialId === "DOMTEST-001");
  liveDetailsBtn.dispatchEvent(new dom.window.Event("click", { bubbles: true }));
  await sleep(500);
  assert(!doc.querySelector("tr.detail-row"), "clicking Details again collapses the detail row");

  // ---- ADVERSARIAL CHECK: does the dashboard safely handle a trial_id
  // containing HTML special characters, or does it break/inject raw HTML? ----
  const evilId = `EVIL<img src=x onerror=alert(1)>`;
  await submitForm(dom, "registerForm", {
    trial_id: evilId,
    clinicaltrials_gov_url: "https://clinicaltrials.gov/study/CARDIO-204",
    primary_hypothesis: "x",
    primary_endpoints: "y",
    expected_sample_size: "10",
    integrity_bond: "10",
  });
  await sleep(400);
  const rejectedMarkup = doc.getElementById("registerOutput").textContent;
  assert(
    rejectedMarkup.startsWith("Error:"),
    "API rejects a trial_id containing markup before it can reach the dashboard"
  );
  assert(
    rejectedMarkup.includes("trial_id"),
    "validation errors identify the field that needs correction"
  );
  const injected = doc.querySelector("#trialsTable tbody img[onerror]");
  assert(
    !injected,
    "dashboard does NOT execute/inject raw HTML from a trial_id containing markup (no <img onerror> landed in the live DOM)"
  );

  console.log(`\n${checks - failures}/${checks} checks passed`);
  process.exit(failures > 0 ? 1 : 0);
})().catch((e) => {
  console.error("DOM TEST CRASHED:", e);
  process.exit(1);
});
