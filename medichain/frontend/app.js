function apiBase() {
  return document.getElementById("apiBase").value.replace(/\/$/, "");
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

async function callApi(path, options) {
  const res = await fetch(apiBase() + path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `HTTP ${res.status}`);
  }
  return data;
}

async function checkHealth() {
  const dot = document.getElementById("healthDot");
  try {
    await callApi("/api/health", { method: "GET" });
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
document.getElementById("apiBase").addEventListener("change", checkHealth);

checkHealth();
refreshTrials();
