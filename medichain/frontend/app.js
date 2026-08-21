const MEDICHAIN_CONFIG = window.MEDICHAIN_CONFIG || {};
const TARGET_CHAIN_ID = Number(MEDICHAIN_CONFIG.WALLET_CHAIN_ID || 4221);
const TARGET_CHAIN_HEX = `0x${TARGET_CHAIN_ID.toString(16)}`;
const TARGET_CHAIN_NAME = MEDICHAIN_CONFIG.WALLET_CHAIN_NAME || "GenLayer Bradbury";
const formSubmission = window.MediChainFormSubmission;

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
  if (apiBaseInput) {
    apiBaseInput.value = defaultApiBase();
  }
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

function saveSession() {
  if (walletSession.accessToken && walletSession.user) {
    sessionStorage.setItem("medichain_session", JSON.stringify({
      accessToken: walletSession.accessToken,
      expiresAt: walletSession.expiresAt,
      user: walletSession.user,
    }));
  } else {
    sessionStorage.removeItem("medichain_session");
  }
}

function loadSession() {
  const raw = sessionStorage.getItem("medichain_session");
  if (!raw) return false;
  try {
    const data = JSON.parse(raw);
    if (data.expiresAt && Date.now() / 1000 > data.expiresAt) {
      sessionStorage.removeItem("medichain_session");
      return false;
    }
    walletSession.accessToken = data.accessToken;
    walletSession.expiresAt = data.expiresAt;
    walletSession.user = data.user;
    const remainingMs = Math.max(0, (data.expiresAt * 1000) - Date.now());
    if (sessionExpiryTimer) window.clearTimeout(sessionExpiryTimer);
    sessionExpiryTimer = window.setTimeout(
      () => clearWalletSession("Wallet session expired"),
      remainingMs,
    );
    return true;
  } catch (_) {
    return false;
  }
}

function updateWalletUi(message = "") {
  const connected = Boolean(walletSession.accessToken && walletSession.user);
  const connectButton = document.getElementById("connectWalletBtn");
  const disconnectButton = document.getElementById("disconnectWalletBtn");
  const status = document.getElementById("walletStatus");
  const role = document.getElementById("walletRole");

  if (!connectButton || !disconnectButton || !status || !role) return;

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
    if (submitButton) {
      submitButton.disabled = (
        !permitted
        || walletSession.busy
        || formSubmission.isBusy(form)
      );
    }
    form.setAttribute("aria-disabled", permitted ? "false" : "true");
  }

  const address = connected ? walletSession.user.address : "Not connected";
  const registerSponsor = document.getElementById("registerSponsor");
  const appealResolver = document.getElementById("appealResolver");
  const flagSubmitter = document.getElementById("flagSubmitter");
  if (registerSponsor) registerSponsor.textContent = address;
  if (appealResolver) appealResolver.textContent = address;
  if (flagSubmitter) flagSubmitter.textContent = address;
}

function clearWalletSession(message = "Wallet not connected") {
  walletSession.accessToken = "";
  walletSession.expiresAt = 0;
  walletSession.user = null;
  if (sessionExpiryTimer) window.clearTimeout(sessionExpiryTimer);
  sessionExpiryTimer = null;
  saveSession();
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
  saveSession();
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
  if (!dialog) return Promise.resolve(false);
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

class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
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
    throw new ApiError(apiErrorMessage(data, res.status), res.status, data);
  }
  return data;
}

async function checkHealth() {
  const dot = document.getElementById("healthDot");
  if (!dot) return;
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

function setFormStatus(form, message, pending = false) {
  const status = form.querySelector(".form-status");
  if (!status) return;
  status.textContent = message;
  status.dataset.pending = pending ? "true" : "false";
  status.className = "form-status" + (message.startsWith("Error") || message.startsWith("failed") ? " error" : message.startsWith("Trial registered") || message.startsWith("Results submitted") || message.startsWith("Appeal resolved") || message.startsWith("Flag submitted") ? " success" : "");
}

function updateFormBusyStatus(form, busy, pendingLabel) {
  const status = form.querySelector(".form-status");
  if (!status) return;
  if (busy) {
    setFormStatus(form, pendingLabel, true);
  } else if (status.dataset.pending === "true") {
    setFormStatus(form, "");
  }
}

function runFormMutation(form, pendingLabel, task) {
  return formSubmission.run({
    form,
    pendingLabel,
    task,
    onBusyChange: (busy, label) => updateFormBusyStatus(form, busy, label),
    onSettled: updateWalletUi,
  });
}

function showOutput(id, text) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.classList.add("visible");
}

// ---------------- Register Trial ----------------
function initRegisterForm() {
  const form = document.getElementById("registerForm");
  if (!form) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = document.getElementById("registerOutput");
    await runFormMutation(form, "Registering...", async () => {
      try {
        requireFormAccess(form);
        const payload = formToJson(form);
        payload.clinicaltrials_gov_url = payload.clinicaltrials_gov_url.trim();
        payload.primary_endpoints = payload.primary_endpoints
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean);
        payload.expected_sample_size = parseInt(payload.expected_sample_size, 10);
        payload.integrity_bond = parseInt(payload.integrity_bond, 10);

        const guarded = await formSubmission.guardedMutation({
          callApi,
          duplicatePath: `/api/trial/${encodeURIComponent(payload.trial_id)}`,
          mutate: () => callApi("/api/register_trial", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          }),
        });
        if (guarded.kind === "duplicate") {
          const state = guarded.record.status ? ` (${guarded.record.status})` : "";
          showOutput("registerOutput", (
            `Trial '${payload.trial_id}' already exists${state}. `
            + "No Bradbury write was sent. Review it in the Integrity Dashboard."
          ));
          setFormStatus(form, "Existing trial found. No transaction was submitted.");
          return;
        }

        showOutput("registerOutput", JSON.stringify(guarded.value, null, 2));
        setFormStatus(form, "Trial registered.");
      } catch (err) {
        showOutput("registerOutput", "Error: " + err.message);
        setFormStatus(form, "Registration failed. Review the error below.");
      }
    });
  });
}

// ---------------- Submit Results ----------------
function initSubmitForm() {
  const form = document.getElementById("submitForm");
  if (!form) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = document.getElementById("submitOutput");
    await runFormMutation(form, "Submitting for analysis...", async () => {
      try {
        requireFormAccess(form);
        const payload = formToJson(form);
        payload.publication_url = payload.publication_url.trim();
        payload.preprint_url = payload.preprint_url.trim();
        const guarded = await formSubmission.guardedMutation({
          callApi,
          duplicatePath: `/api/report/${encodeURIComponent(payload.report_id)}`,
          requiredPath: `/api/trial/${encodeURIComponent(payload.trial_id)}`,
          mutate: () => callApi("/api/submit_results", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          }),
        });
        if (guarded.kind === "duplicate") {
          showOutput("submitOutput", (
            `Report '${payload.report_id}' already exists. `
            + "No Bradbury write was sent. Review the existing report."
          ));
          setFormStatus(form, "Existing report found. No transaction was submitted.");
          return;
        }
        if (guarded.kind === "missing") {
          throw new Error(
            `Trial '${payload.trial_id}' was not found. `
            + "Register the trial before submitting results.",
          );
        }

        const jobId = guarded.value?.job_id;
        if (!jobId) {
          showOutput("submitOutput", JSON.stringify(guarded.value, null, 2));
          setFormStatus(form, "Results submitted and analyzed.");
          return;
        }

        setFormStatus(form, "GenLayer consensus in progress...");
        const deadline = Date.now() + 10 * 60 * 1000;
        let reportData = null;
        for (;;) {
          if (Date.now() > deadline) {
            throw new Error("GenLayer consensus timed out. Try again in a few minutes.");
          }
          const jobStatus = await callApi(`/api/jobs/${encodeURIComponent(jobId)}`);
          if (jobStatus.status === "complete") {
            reportData = jobStatus.result;
            break;
          }
          if (jobStatus.status === "failed") {
            throw new Error(`Analysis failed: ${jobStatus.error || "unknown error"}`);
          }
          await new Promise((r) => setTimeout(r, 10000));
        }

        if (reportData) {
          showOutput("submitOutput", JSON.stringify(reportData, null, 2));
        } else {
          showOutput("submitOutput", "Analysis completed but no report available.");
        }
        setFormStatus(form, "Results submitted and analyzed.");
      } catch (err) {
        showOutput("submitOutput", "Error: " + err.message);
        setFormStatus(form, "Results submission failed. Review the error below.");
      }
    });
  });
}

// ---------------- Resolve Appeal ----------------
function initAppealForm() {
  const form = document.getElementById("appealForm");
  if (!form) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = document.getElementById("appealOutput");
    await runFormMutation(form, "Resolving...", async () => {
      try {
        requireFormAccess(form);
        const payload = formToJson(form);
        const result = await callApi("/api/resolve_appeal", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        showOutput("appealOutput", JSON.stringify(result, null, 2));
        setFormStatus(form, "Appeal resolved.");
      } catch (err) {
        showOutput("appealOutput", "Error: " + err.message);
        setFormStatus(form, "Appeal resolution failed. Review the error below.");
      }
    });
  });
}

// ---------------- Whistleblower Flag ----------------
function initFlagForm() {
  const form = document.getElementById("flagForm");
  if (!form) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = document.getElementById("flagOutput");
    await runFormMutation(form, "Submitting flag...", async () => {
      try {
        requireFormAccess(form);
        const payload = formToJson(form);
        payload.evidence_url = payload.evidence_url.trim();
        const result = await callApi("/api/submit_flag", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        showOutput("flagOutput", JSON.stringify(result, null, 2));
        setFormStatus(form, "Flag submitted.");
      } catch (err) {
        showOutput("flagOutput", "Error: " + err.message);
        setFormStatus(form, "Flag submission failed. Review the error below.");
      }
    });
  });
}

// ---------------- Dashboard ----------------
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
    td.innerHTML = `<p class='detail-empty'>Error loading details: ${escapeHtml(err.message)}</p>`;
  }

  detailRow.appendChild(td);
  return detailRow;
}

let refreshGeneration = 0;

async function refreshTrials() {
  const myGeneration = ++refreshGeneration;
  const tbody = document.querySelector("#trialsTable tbody");
  if (!tbody) return;
  tbody.innerHTML = "";
  try {
    const trials = await callApi("/api/trials", { method: "GET" });
    for (const trialId of Object.keys(trials)) {
      if (myGeneration !== refreshGeneration) return;
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

function initDashboard() {
  const refreshBtn = document.getElementById("refreshBtn");
  if (!refreshBtn) return;

  refreshBtn.addEventListener("click", refreshTrials);

  document.querySelector("#trialsTable tbody").addEventListener("click", async (e) => {
    const btn = e.target.closest(".details-btn");
    if (!btn) return;
    const trialId = btn.dataset.trialId;
    expandedTrialId = expandedTrialId === trialId ? null : trialId;
    await refreshTrials();
  });
}

// ---------------- Initialization ----------------
function initTabs() {
  const tabBtns = document.querySelectorAll(".tab-btn");
  const panels = document.querySelectorAll(".tab-panel");

  tabBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tab;

      tabBtns.forEach((b) => b.classList.remove("active"));
      panels.forEach((p) => p.classList.remove("active"));

      btn.classList.add("active");
      const panel = document.getElementById("tab-" + target);
      if (panel) panel.classList.add("active");
    });
  });
}

function initWalletListeners() {
  const connectBtn = document.getElementById("connectWalletBtn");
  const disconnectBtn = document.getElementById("disconnectWalletBtn");
  if (connectBtn) connectBtn.addEventListener("click", connectWallet);
  if (disconnectBtn) disconnectBtn.addEventListener("click", disconnectWallet);

  const provider = walletProvider();
  if (provider && typeof provider.on === "function") {
    provider.on("accountsChanged", () => {
      clearWalletSession("Wallet account changed; sign in again");
    });
    provider.on("chainChanged", () => {
      clearWalletSession("Wallet network changed; connect to Bradbury");
    });
  }
}

// Initialize page-specific components
initConfigControls();
loadSession();
updateWalletUi();
checkHealth();
initWalletListeners();
initTabs();
initRegisterForm();
initSubmitForm();
initAppealForm();
initFlagForm();
initDashboard();
