const MEDICHAIN_CONFIG = window.MEDICHAIN_CONFIG || {};
const TARGET_CHAIN_ID = Number(MEDICHAIN_CONFIG.WALLET_CHAIN_ID || 4221);
const TARGET_CHAIN_HEX = `0x${TARGET_CHAIN_ID.toString(16)}`;
const TARGET_CHAIN_NAME = MEDICHAIN_CONFIG.WALLET_CHAIN_NAME || "GenLayer Bradbury";

const walletSession = {
  accessToken: "",
  expiresAt: 0,
  user: null,
  busy: false,
};
let sessionExpiryTimer = null;

function defaultApiBase() {
  return (MEDICHAIN_CONFIG.API_BASE_URL || window.location.origin || "").replace(/\/$/, "");
}

function initConfigControls() {
  const apiBaseInput = document.getElementById("apiBase");
  apiBaseInput.value = defaultApiBase();
}

function apiBase() {
  return defaultApiBase();
}

function walletProvider() {
  return window.ethereum || null;
}

function shortAddress(address) {
  return `${address.slice(0, 6)}...${address.slice(-4)}`;
}

function walletErrorMessage(error) {
  if (error && error.code === 4001) return "Wallet request was cancelled";
  return error && error.message ? error.message : "Wallet request failed";
}

function roleAllows(form, role) {
  return (form.dataset.authRoles || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .includes(role);
}

function updateWalletUi(message = "") {
  const connected = Boolean(walletSession.accessToken && walletSession.user);
  const connectButton = document.getElementById("connectWalletBtn");
  const disconnectButton = document.getElementById("disconnectWalletBtn");
  const status = document.getElementById("walletStatus");
  const role = document.getElementById("walletRole");

  connectButton.hidden = connected;
  connectButton.disabled = walletSession.busy;
  connectButton.textContent = walletSession.busy ? "Connecting..." : "Connect Wallet";
  disconnectButton.hidden = !connected;
  disconnectButton.disabled = walletSession.busy;

  if (connected) {
    status.textContent = shortAddress(walletSession.user.address);
    status.title = walletSession.user.address;
    role.textContent = walletSession.user.role;
    role.hidden = false;
  } else {
    status.textContent = message || "Wallet not connected";
    status.removeAttribute("title");
    role.hidden = true;
  }

  for (const form of document.querySelectorAll("form[data-auth-roles]")) {
    const permitted = connected && roleAllows(form, walletSession.user.role);
    const submitButton = form.querySelector('button[type="submit"]');
    if (submitButton) submitButton.disabled = !permitted || walletSession.busy;
    form.setAttribute("aria-disabled", permitted ? "false" : "true");
  }

  const address = connected ? walletSession.user.address : "Not connected";
  document.getElementById("registerSponsor").textContent = address;
  document.getElementById("appealResolver").textContent = address;
  document.getElementById("flagSubmitter").textContent = address;
}

function clearWalletSession(message = "Wallet not connected") {
  walletSession.accessToken = "";
  walletSession.expiresAt = 0;
  walletSession.user = null;
  if (sessionExpiryTimer) window.clearTimeout(sessionExpiryTimer);
  sessionExpiryTimer = null;
  updateWalletUi(message);
}

function activateWalletSession(result) {
  walletSession.accessToken = result.access_token;
  walletSession.expiresAt = result.expires_at;
  walletSession.user = result.user;
  const remainingMs = Math.max(0, (result.expires_at * 1000) - Date.now());
  if (sessionExpiryTimer) window.clearTimeout(sessionExpiryTimer);
  sessionExpiryTimer = window.setTimeout(
    () => clearWalletSession("Wallet session expired"),
    remainingMs,
  );
  updateWalletUi();
}

function requireFormAccess(form) {
  if (!walletSession.accessToken || !walletSession.user) {
    throw new Error("Connect and sign in with your wallet first");
  }
  if (!roleAllows(form, walletSession.user.role)) {
    throw new Error("Your wallet role cannot perform this action");
  }
}

async function ensureBradburyNetwork(provider) {
  const currentChain = await provider.request({ method: "eth_chainId" });
  if (Number.parseInt(currentChain, 16) === TARGET_CHAIN_ID) return;
  try {
    await provider.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: TARGET_CHAIN_HEX }],
    });
  } catch (error) {
    if (error.code !== 4902) throw error;
    await provider.request({
      method: "wallet_addEthereumChain",
      params: [{
        chainId: TARGET_CHAIN_HEX,
        chainName: TARGET_CHAIN_NAME,
        nativeCurrency: { name: "GEN", symbol: "GEN", decimals: 18 },
        rpcUrls: [MEDICHAIN_CONFIG.WALLET_RPC_URL],
        blockExplorerUrls: [MEDICHAIN_CONFIG.WALLET_EXPLORER_URL],
      }],
    });
  }
}

function confirmSignatureMessage(message) {
  const dialog = document.getElementById("signatureDialog");
  document.getElementById("signatureMessage").textContent = message;
  dialog.returnValue = "";
  dialog.showModal();
  return new Promise((resolve) => {
    dialog.addEventListener(
      "close",
      () => resolve(dialog.returnValue === "confirm"),
      { once: true },
    );
  });
}

async function personalSign(provider, address, message) {
  try {
    return await provider.request({
      method: "personal_sign",
      params: [message, address],
    });
  } catch (error) {
    if (error.code !== -32602) throw error;
    return provider.request({
      method: "personal_sign",
      params: [address, message],
    });
  }
}

async function connectWallet() {
  const provider = walletProvider();
  if (!provider) {
    clearWalletSession("Compatible wallet not found");
    return;
  }

  walletSession.busy = true;
  updateWalletUi();
  try {
    await ensureBradburyNetwork(provider);
    const accounts = await provider.request({ method: "eth_requestAccounts" });
    if (!accounts || !accounts[0]) throw new Error("Wallet did not provide an account");
    const address = accounts[0];
    const challenge = await callApi("/api/auth/challenge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address, chain_id: TARGET_CHAIN_ID }),
    });
    const confirmed = await confirmSignatureMessage(challenge.message);
    if (!confirmed) throw { code: 4001, message: "Wallet request was cancelled" };
    const signature = await personalSign(provider, address, challenge.message);
    const session = await callApi("/api/auth/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        challenge_id: challenge.challenge_id,
        address,
        signature,
      }),
    });
    activateWalletSession(session);
  } catch (error) {
    clearWalletSession(walletErrorMessage(error));
  } finally {
    walletSession.busy = false;
    updateWalletUi();
  }
}

async function disconnectWallet() {
  const token = walletSession.accessToken;
  clearWalletSession();
  if (!token) return;
  try {
    await callApi("/api/auth/logout", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch (_) {
    // Local session is already cleared; server expiry remains bounded.
  }
}

// BUG FIX: every piece of dynamic data rendered into the dashboard table
// (trial_id, status, bond, flag descriptions, etc.) is either user-supplied
// at registration or LLM-generated text, and was being inserted straight
// into innerHTML via template literals with no escaping. A trial_id like
// `EVIL<img src=x onerror=alert(1)>` would land in the live DOM as real
// markup, not as text -- confirmed by an automated jsdom click-through
// test (frontend/dom_test.js) that actually registers such a trial and
// checks whether the tag executes. Every dynamic value below now goes
// through escapeHtml() before being placed in the table.
function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function apiErrorMessage(data, status) {
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) {
    return data.detail.map((item) => {
      const location = Array.isArray(item.loc) ? item.loc.slice(1).join(".") : "";
      return `${location ? `${location}: ` : ""}${item.msg || "invalid value"}`;
    }).join("; ");
  }
  if (typeof data.raw === "string" && data.raw.trim()) return data.raw.trim();
  return `HTTP ${status}`;
}

async function callApi(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (walletSession.accessToken && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${walletSession.accessToken}`);
  }

  const res = await fetch(apiBase() + path, { ...options, headers });
  const text = await res.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (_) {
      data = { raw: text };
    }
  }
  if (!res.ok) {
    if (
      res.status === 401
      && walletSession.accessToken
      && !path.startsWith("/api/auth/")
    ) {
      clearWalletSession("Wallet session expired");
    }
    throw new Error(apiErrorMessage(data, res.status));
  }
  return data;
}

async function checkHealth() {
  const dot = document.getElementById("healthDot");
  try {
    await callApi("/api/ready", { method: "GET" });
    dot.className = "dot dot-ok";
  } catch (e) {
    dot.className = "dot dot-bad";
  }
}

function formToJson(form) {
  const fd = new FormData(form);
  const obj = {};
  for (const [key, value] of fd.entries()) obj[key] = value;
  return obj;
}

// ---------------- Register Trial ----------------
document.getElementById("registerForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const out = document.getElementById("registerOutput");
  try {
    requireFormAccess(e.target);
    const payload = formToJson(e.target);
    payload.primary_endpoints = payload.primary_endpoints.split(",").map(s => s.trim()).filter(Boolean);
    payload.expected_sample_size = parseInt(payload.expected_sample_size, 10);
    payload.integrity_bond = parseInt(payload.integrity_bond, 10);

    const result = await callApi("/api/register_trial", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    out.textContent = JSON.stringify(result, null, 2);
    refreshTrials();
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

// ---------------- Submit Results ----------------
document.getElementById("submitForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const out = document.getElementById("submitOutput");
  try {
    requireFormAccess(e.target);
    const payload = formToJson(e.target);
    const result = await callApi("/api/submit_results", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    out.textContent = JSON.stringify(result, null, 2);
    refreshTrials();
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

// ---------------- Resolve Appeal ----------------
document.getElementById("appealForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const out = document.getElementById("appealOutput");
  try {
    requireFormAccess(e.target);
    const payload = formToJson(e.target);
    const result = await callApi("/api/resolve_appeal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    out.textContent = JSON.stringify(result, null, 2);
    refreshTrials();
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

// ---------------- Whistleblower Flag ----------------
document.getElementById("flagForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const out = document.getElementById("flagOutput");
  try {
    requireFormAccess(e.target);
    const payload = formToJson(e.target);
    const result = await callApi("/api/submit_flag", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    out.textContent = JSON.stringify(result, null, 2);
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

// ---------------- Dashboard ----------------
// NOTE: this used to only show a whistleblower-flag *count* and never
// showed the integrity flags (outcome_switching, p_hacking, etc.) that
// the spec explicitly asks the dashboard to display ("score + flags per
// trial"). It now fetches /api/trial/{id}/reports and renders the actual
// flag types/severities for the latest report.
//
// GAP FOUND AND FIXED (this pass): even after adding flag badges, there
// was still no way to read a report's actual `summary` text, its
// `confidence`, which `publication_url` it came from, or to see more than
// the single latest report if a trial had multiple submissions -- all of
// that data existed in the backend (/api/trial/{id}/reports) but nothing
// in the UI ever surfaced it. Same for whistleblower flags: only a count
// was shown, never the submitter/description/evidence_url. Added a
// per-row "Details" toggle that fetches and displays all of it.
let expandedTrialId = null;

async function renderDetailRow(trialId) {
  const detailRow = document.createElement("tr");
  detailRow.className = "detail-row";
  const td = document.createElement("td");
  td.colSpan = 8;

  try {
    const [reports, flags] = await Promise.all([
      callApi(`/api/trial/${encodeURIComponent(trialId)}/reports`, { method: "GET" }),
      callApi(`/api/trial/${encodeURIComponent(trialId)}/flags`, { method: "GET" }),
    ]);

    const reportList = Object.values(reports);
    const flagList = Object.values(flags);

    let html = "";

    if (reportList.length === 0) {
      html += "<p class='detail-empty'>No results submitted yet for this trial.</p>";
    } else {
      html += "<h4>Integrity Reports</h4>";
      for (const r of reportList) {
        html += `
          <div class="detail-report">
            <div><strong>${escapeHtml(r.report_id)}</strong> — verdict: ${escapeHtml(r.verdict)} (confidence: ${escapeHtml(r.confidence)}, score: ${escapeHtml(r.integrity_score)})</div>
            <div class="detail-summary">${escapeHtml(r.summary)}</div>
            <div class="detail-meta">Source: ${escapeHtml(r.publication_url)}</div>
          </div>`;
      }
    }

    if (flagList.length > 0) {
      html += "<h4>Whistleblower Flags</h4>";
      for (const f of flagList) {
        html += `
          <div class="detail-flag">
            <div><strong>${escapeHtml(f.submitter)}</strong> (${escapeHtml(f.status)})</div>
            <div>${escapeHtml(f.description)}</div>
            ${f.evidence_url ? `<div class="detail-meta">Evidence: ${escapeHtml(f.evidence_url)}</div>` : ""}
          </div>`;
      }
    }

    td.innerHTML = html;
  } catch (err) {
    td.innerHTML = `<p class="detail-empty">Error loading details: ${escapeHtml(err.message)}</p>`;
  }

  detailRow.appendChild(td);
  return detailRow;
}

// BUG FIX: refreshTrials() can be triggered from several places at once
// (initial page load, every form submit, the Refresh button, and the
// Details toggle) and does several awaited fetches per trial inside its
// loop. If two calls overlap -- e.g. the initial auto-refresh on page
// load is still in flight when the user clicks "Refresh Trials" -- both
// calls clear tbody and then both append rows, interleaved, producing
// duplicate rows. Confirmed directly: firing overlapping refreshTrials()
// calls against a 2-trial backend produced 6 rows instead of 2. Fixed
// with a generation token: each call captures its own token, and bails
// out before touching the DOM if a newer call has since started.
let refreshGeneration = 0;

async function refreshTrials() {
  const myGeneration = ++refreshGeneration;
  const tbody = document.querySelector("#trialsTable tbody");
  tbody.innerHTML = "";
  try {
    const trials = await callApi("/api/trials", { method: "GET" });
    for (const trialId of Object.keys(trials)) {
      if (myGeneration !== refreshGeneration) return; // a newer refresh superseded this one
      const t = trials[trialId];

      let flagBadges = "—";
      try {
        const reports = await callApi(`/api/trial/${encodeURIComponent(trialId)}/reports`, { method: "GET" });
        const reportList = Object.values(reports);
        if (reportList.length > 0) {
          const latest = reportList[reportList.length - 1];
          if (latest.flags && latest.flags.length > 0) {
            flagBadges = latest.flags
              .map(f => `<span class="badge badge-${escapeHtml(f.severity)}" title="${escapeHtml(f.description)}">${escapeHtml(f.type)}</span>`)
              .join(" ");
          } else {
            flagBadges = "none";
          }
        }
      } catch (_) {}

      let whistleblowerCount = 0;
      try {
        const flags = await callApi(`/api/trial/${encodeURIComponent(trialId)}/flags`, { method: "GET" });
        whistleblowerCount = Object.keys(flags).length;
      } catch (_) {}

      if (myGeneration !== refreshGeneration) return;

      const tr = document.createElement("tr");
      tr.className = "verdict-" + escapeHtml(t.latest_verdict || "none");
      tr.innerHTML = `
        <td>${escapeHtml(t.trial_id)}</td>
        <td>${escapeHtml(t.status)}</td>
        <td>${escapeHtml(t.integrity_score ?? "—")}</td>
        <td>${escapeHtml(t.latest_verdict ?? "—")}</td>
        <td>${escapeHtml(t.bond)} (${escapeHtml(t.bond_status)})</td>
        <td>${flagBadges}</td>
        <td>${escapeHtml(whistleblowerCount)}</td>
        <td><button type="button" class="details-btn" data-trial-id="${escapeHtml(trialId)}">Details</button></td>
      `;
      tbody.appendChild(tr);

      if (expandedTrialId === trialId) {
        const detailRow = await renderDetailRow(trialId);
        if (myGeneration !== refreshGeneration) return;
        tbody.appendChild(detailRow);
      }
    }
  } catch (err) {
    if (myGeneration !== refreshGeneration) return;
    tbody.innerHTML = `<tr><td colspan="8">Error loading trials: ${escapeHtml(err.message)}</td></tr>`;
  }
}

// Event delegation: the Details buttons are recreated on every refresh, so
// the listener is bound once on the (static) tbody rather than per-button.
document.querySelector("#trialsTable tbody").addEventListener("click", async (e) => {
  const btn = e.target.closest(".details-btn");
  if (!btn) return;
  const trialId = btn.dataset.trialId;
  expandedTrialId = expandedTrialId === trialId ? null : trialId;
  await refreshTrials();
});

document.getElementById("refreshBtn").addEventListener("click", refreshTrials);

document.getElementById("connectWalletBtn").addEventListener("click", connectWallet);
document.getElementById("disconnectWalletBtn").addEventListener("click", disconnectWallet);

if (walletProvider() && typeof walletProvider().on === "function") {
  walletProvider().on("accountsChanged", () => {
    clearWalletSession("Wallet account changed; sign in again");
  });
  walletProvider().on("chainChanged", () => {
    clearWalletSession("Wallet network changed; connect to Bradbury");
  });
}

initConfigControls();
updateWalletUi();
checkHealth();
refreshTrials();
