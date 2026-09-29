/* ══════════════════════════════════════════════════════════════
   SignalDesk — frontend
   Talks only to /api/*, which drives the same agent as the CLI.
   ══════════════════════════════════════════════════════════════ */

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const SAMPLE = `[2026-09-28] Call — Devon Achebe opened the call 4 minutes late and did not bring the agenda he promised. He said the budget review "moved up" to Oct 15 and that Alison Kerr is now "very close to Freightwise on price". He asked us to send one more written summary but declined a call with the CFO again, saying she is "not taking vendor calls". He did not mention the Q3 SSO commitments at all.`;

const state = { accounts: [], current: null, labAccount: "Meridian Freight" };

/* ── helpers ──────────────────────────────────────────────── */

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const TIER_COLOR = {
  critical: "var(--critical)", high: "var(--high)",
  medium: "var(--medium)",  low: "var(--low)",
};

let toastTimer;
function toast(msg, kind = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = `toast on ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), 4600);
}

function loading(on, text = "Reasoning over memory…") {
  $("#loader").classList.toggle("on", on);
  $("#loader-text").textContent = text;
}

/** fetch + JSON + toast-on-error. Throws so callers can skip rendering. */
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new Error(data?.detail || `${res.status} ${res.statusText}`);
  return data;
}

/** Tiny markdown subset: headings, bold, lists, paragraphs, inline code. */
function mdToHtml(text) {
  if (!text) return "";
  const escaped = esc(text);
  const inline = (s) => s
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  const blocks = escaped.split(/\n{2,}/);
  let out = "";
  for (const raw of blocks) {
    const b = raw.trim();
    if (!b) continue;
    if (/^#{1,4}\s+/.test(b)) {
      const lvl = b.match(/^#+/)[0].length;
      out += `<h${lvl}>${inline(b.replace(/^#+\s*/, ""))}</h${lvl}>`;
    } else if (b.split("\n").every(l => /^\s*[-•]\s+/.test(l))) {
      out += "<ul>" + b.split("\n").map(l => `<li>${inline(l.replace(/^\s*[-•]\s+/, ""))}</li>`).join("") + "</ul>";
    } else {
      out += `<p>${inline(b).replace(/\n/g, "<br>")}</p>`;
    }
  }
  return out;
}

const meter = (score, tier) => `
  <div class="meter">
    <div class="meter-track">
      <div class="meter-fill" style="width:${Math.max(4, score)}%;background:${TIER_COLOR[tier] || "var(--accent)"}"></div>
    </div>
    <span class="meter-val">${score}/100</span>
  </div>`;

const empty = (icon, title, sub) =>
  `<div class="empty"><div class="big">${icon}</div><p>${title}</p>${sub ? `<p class="small">${sub}</p>` : ""}</div>`;

/* ── navigation ───────────────────────────────────────────── */

function showView(name) {
  $$("#tabs .tab").forEach(t => t.classList.toggle("active", t.dataset.view === name));
  $$(".view").forEach(v => v.classList.toggle("active", v.id === `view-${name}`));
  if (name === "memory") loadMemory();
  if (name === "overview" && !state.boardRendered) loadBoardIdle();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

$$("#tabs .tab").forEach(t => t.addEventListener("click", () => showView(t.dataset.view)));

$$("#account-tabs .atab").forEach(t => t.addEventListener("click", () => {
  $$("#account-tabs .atab").forEach(x => x.classList.toggle("active", x === t));
  $$(".pane").forEach(p => p.classList.toggle("active", p.id === `pane-${t.dataset.pane}`));
}));

$("#theme-toggle").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme");
  const next = cur === "light" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("sd-theme", next);
});
document.documentElement.setAttribute("data-theme", localStorage.getItem("sd-theme") || "dark");

/* ── state / header ───────────────────────────────────────── */

/* Cached so tab switches and reloads paint instantly; the server round-trip
   still decides the truth, it just never blocks a render. */
const stateCache = { data: null, at: 0 };
const STATE_TTL = 15000;

function paintState(s) {
  state.accounts = s.accounts || [];
  $("#bank-name").textContent = s.bank || "—";
  $("#stat-memories").textContent  = s.stats?.memories ?? "—";
  $("#stat-accounts").textContent  = s.stats?.accounts ?? state.accounts.length;
  $("#stat-models").textContent    = s.mental_models?.length ?? 0;
  $("#stat-directives").textContent = s.directives?.length ?? 0;
  $("#ops-list").textContent = (s.memory_ops || []).join(" · ");

  const opts = state.accounts.map(a => `<option value="${esc(a)}">${esc(a)}</option>`).join("");
  $("#account-select").innerHTML = opts || `<option value="">no accounts in memory</option>`;
  $("#lab-select").innerHTML = opts || `<option value="">no accounts in memory</option>`;
  $("#account-list").innerHTML = state.accounts.map(a => `<option value="${esc(a)}">`).join("");
  if (state.accounts.includes(state.labAccount)) $("#lab-select").value = state.labAccount;

  // An unreachable bank must never read as "no risk found".
  if (s.accounts_error) {
    $("#board-area").innerHTML =
      `<div class="err-box"><b>Cannot reach the memory bank.</b><br>${esc(s.accounts_error)}</div>`;
    toast("Memory bank unreachable — risk board unavailable.", "err");
  }
}

async function loadState({ force = false } = {}) {
  if (!force && stateCache.data && (Date.now() - stateCache.at) < STATE_TTL) {
    paintState(stateCache.data);
    return stateCache.data;
  }
  try {
    const s = await api("/api/state");
    stateCache.data = s;
    stateCache.at = Date.now();
    paintState(s);
    return s;
  } catch (e) {
    toast(`Cannot reach the agent: ${e.message}`, "err");
    return null;
  }
}

/* ── overview / board ─────────────────────────────────────── */

function loadBoardIdle() {
  $("#board-area").innerHTML = state.accounts.length
    ? Array.from({ length: 4 }, () => `<div class="skel"></div>`).join("")
    : empty("📉", "Memory is empty", "Load the demo book of business to see the agent work.");
}

async function loadBoard() {
  const area = $("#board-area");
  area.innerHTML = Array.from({ length: 4 }, () => `<div class="skel"></div>`).join("");
  try {
    const r = await api("/api/board?limit=6");
    if (!r.findings.length) {
      area.innerHTML = empty("📉", "Nothing to triage", r.note || "Seed the demo book first.");
      return;
    }
    area.innerHTML = `<div class="board">${r.findings.map(boardRow).join("")}</div>`;
    state.boardRendered = true;
    $$(".board-row").forEach(row => row.addEventListener("click", () => {
      $("#account-select").value = row.dataset.acct;
      showView("account");
      runAssess();
    }));
  } catch (e) {
    area.innerHTML = `<div class="err-box">${esc(e.message)}</div>`;
  }
}

function boardRow(f) {
  const a = f.assessment, p = f.save_plan;
  const failed = (a.primary_driver || "").startsWith("analysis failed");
  return `
    <button class="board-row ${failed ? "failed" : ""}" data-acct="${esc(a.account)}">
      <div>
        <div class="br-name">${esc(a.account)}</div>
        <div class="br-sub">champion: ${esc(a.champion_status || "unknown")}</div>
      </div>
      <div>
        <span class="badge ${esc(a.risk_tier)}">${esc(a.risk_tier)}</span>
        <div style="margin-top:7px">${meter(a.risk_score, a.risk_tier)}</div>
      </div>
      <div class="br-driver">${esc(a.primary_driver || "")}</div>
      <div class="br-play">${esc(p.headline_play || "")}</div>
      <div class="br-arrow">›</div>
    </button>`;
}

$("#btn-board").addEventListener("click", loadBoard);

$("#btn-seed").addEventListener("click", async () => {
  loading(true, "Retaining the book of business…");
  try {
    const r = await api("/api/seed", { method: "POST", body: { reset: false } });
    toast(`Retained ${r.seeded} signals across the demo accounts.`, "ok");
    await loadState();
    await loadBoard();
  } catch (e) { toast(e.message, "err"); }
  finally { loading(false); }
});

/* ── account ──────────────────────────────────────────────── */

function renderVerdictPlaceholder() {
  $("#pane-verdict").innerHTML =
    empty("◔", "No analysis yet", 'Pick an account and press <b>Analysis</b> — the agent reasons over its full history.');
}

async function runAssess() {
  const acct = $("#account-select").value;
  if (!acct) return toast("Pick an account first.", "err");

  loading(true, `Reflecting over the full history of ${acct}…`);
  $("#pane-verdict").innerHTML = Array.from({ length: 3 }, () => `<div class="skel"></div>`).join("");
  try {
    const f = await api(`/api/account/${encodeURIComponent(acct)}`);
    state.current = f;
    renderVerdict(f);
    const ev = (f.assessment.evidence || []).length;
    toast(`Assessed ${acct} from ${ev} cited memory unit(s).`, "ok");
  } catch (e) {
    $("#pane-verdict").innerHTML = `<div class="err-box">${esc(e.message)}</div>`;
    toast(e.message, "err");
  } finally { loading(false); }
}

function renderVerdict(f) {
  const a = f.assessment, p = f.save_plan;
  const ev = (a.evidence || []).map(e => `
    <div class="ev">
      <div class="ev-meta">
        <span class="ev-date">${esc(e.date || "undated")}</span>
        <span class="ev-src">${esc(e.source || "memory")}</span>
      </div>
      <div class="ev-q">“${esc(e.quote)}”</div>
    </div>`).join("");

  const actions = (p.actions || []).map((x, i) => `
    <tr>
      <td class="act-n">${i + 1}</td>
      <td>
        <div>${esc(x.action)}</div>
        ${x.rationale ? `<div class="act-why">${esc(x.rationale)}</div>` : ""}
      </td>
      <td class="act-owner">${x.owner ? esc(x.owner) : '<i class="act-unknown">unassigned</i>'}</td>
      <td class="act-when">${x.within_days != null ? `d+${esc(x.within_days)}` : '<i class="act-unknown">—</i>'}</td>
      <td class="act-sig">${x.success_signal ? esc(x.success_signal) : '<i class="act-unknown">not specified</i>'}</td>
    </tr>`).join("");

  const precedent = (p.precedent || []).map(t => {
    const lost = /\b(lost|churn|fail|never|declin|ignore)/i.test(t);
    return `<div class="pre ${lost ? "lost" : ""}">${esc(t)}</div>`;
  }).join("");

  $("#pane-verdict").innerHTML = `
    <div class="verdict-head" style="border-left-color:${TIER_COLOR[a.risk_tier]}">
      <div class="vh-left">
        <h3>${esc(a.account)}</h3>
        <span class="badge ${esc(a.risk_tier)}">${esc(a.risk_tier)} churn risk</span>
        <div class="vh-score">
          <b style="color:${TIER_COLOR[a.risk_tier]}">${a.risk_score}</b><span>/ 100 · higher is riskier</span>
        </div>
        <div class="conf">confidence <b>${esc(a.confidence)}</b> · ${(a.evidence || []).length} cited memory unit(s)</div>
      </div>
      <div class="vh-meter">
        ${meter(a.risk_score, a.risk_tier)}
      </div>
    </div>

    <div class="driver-box" style="border-color:${TIER_COLOR[a.risk_tier]}55">
      <label style="color:${TIER_COLOR[a.risk_tier]}">Primary driver</label>
      <p>${esc(a.primary_driver)}</p>
      ${(a.drivers || []).length ? `<ul class="driver-list">${a.drivers.map(d => `<li>${esc(d)}</li>`).join("")}</ul>` : ""}
    </div>

    <div class="kv">
      <div class="kv-item"><label>Champion</label><p>${esc(a.champion_status || "unknown")}</p></div>
      <div class="kv-item"><label>Last contact</label><p>${a.silent_days != null ? `${a.silent_days} days ago` : "unknown"}</p></div>
      <div class="kv-item"><label>Would change this</label><p>${esc(a.what_would_change_this || "—")}</p></div>
      <div class="kv-item"><label>Account brief</label><p>${f.mental_model_id ? `<code>${esc(f.mental_model_id.slice(0, 16))}…</code>` : "not built"}</p></div>
    </div>

    ${ev ? `<h3 class="section-title">Evidence from memory</h3><div class="evidence">${ev}</div>` : ""}

    <div class="plan">
      <h3>${esc(p.headline_play)}</h3>
      <p class="plan-why">${esc(p.why_this_play)}</p>
      <div class="conf" style="margin-bottom:6px">confidence <b>${esc(p.confidence)}</b></div>
      ${actions ? `<table class="actions">
        <thead><tr><th></th><th>Action</th><th>Owner</th><th>By</th><th>Success signal</th></tr></thead>
        <tbody>${actions}</tbody></table>` : ""}
    </div>

    ${precedent ? `<h3 class="section-title">Precedent — accounts this play rests on</h3><div class="precedent">${precedent}</div>` : ""}
    ${p.counter_evidence ? `<div class="callout" style="margin-top:16px"><b>Watch out:</b> ${esc(p.counter_evidence)}</div>` : ""}
    ${p.expected_effect ? `<div class="callout effect" style="margin-top:10px"><b>Expected effect:</b> ${esc(p.expected_effect)}</div>` : ""}
    ${a.reasoning ? `<div class="callout" style="margin-top:10px;border-color:var(--border);background:var(--bg-elev)">${esc(a.reasoning)}</div>` : ""}
  `;
}

$("#btn-assess").addEventListener("click", runAssess);

async function loadBrief() {
  const acct = $("#account-select").value;
  if (!acct) return;
  $("#pane-brief").innerHTML = `<div class="skel" style="height:180px"></div>`;
  try {
    const r = await api(`/api/account/${encodeURIComponent(acct)}/brief`);
    $("#pane-brief").innerHTML = r.brief
      ? `<div class="brief">${mdToHtml(r.brief)}</div>`
      : empty("📋", "No brief yet", "Hit Analyze to build the mental model.");
  } catch (e) { $("#pane-brief").innerHTML = `<div class="err-box">${esc(e.message)}</div>`; }
}

async function loadMemories() {
  const acct = $("#account-select").value;
  if (!acct) return;
  $("#pane-memories").innerHTML = Array.from({ length: 4 }, () => `<div class="skel" style="height:56px"></div>`).join("");
  try {
    const r = await api(`/api/account/${encodeURIComponent(acct)}/memories?limit=40`);
    if (!r.items.length) { $("#pane-memories").innerHTML = empty("🗂", "No memory units", ""); return; }
    $("#pane-memories").innerHTML =
      `<h3 class="section-title">${r.items.length} memory unit(s) tagged acct:${esc(acct)}</h3>` +
      r.items.map(m => `
        <div class="mem">
          <div class="mem-head">
            <span class="mem-type ${esc(m.fact_type || "world")}">${esc(m.fact_type || "world")}</span>
            <span class="mem-date">${esc((m.var_date || m.mentioned_at || "").slice(0, 10))}</span>
            ${m.proof_count ? `<span class="mem-proof">${m.proof_count} proof(s)</span>` : ""}
          </div>
          <div class="mem-text">${esc(m.text)}</div>
          ${m.entities ? `<div class="mem-ents">entities: ${esc(m.entities)}</div>` : ""}
        </div>`).join("");
  } catch (e) { $("#pane-memories").innerHTML = `<div class="err-box">${esc(e.message)}</div>`; }
}

$$("#account-tabs .atab").forEach(t => t.addEventListener("click", () => {
  const acct = $("#account-select").value;
  if (!acct) return;
  if (t.dataset.pane === "brief") loadBrief();
  if (t.dataset.pane === "memories") loadMemories();
}));

// Render the "not analyzed yet" prompt so the tab is never silently blank.
renderVerdictPlaceholder();

// A verdict belongs to one account; switching accounts must not leave the
// previous account's verdict on screen.
$("#account-select").addEventListener("change", () => {
  state.current = null;
  renderVerdictPlaceholder();
  $("#pane-brief").innerHTML = "";
  $("#pane-memories").innerHTML = "";
});

/* ── ingest ───────────────────────────────────────────────── */

$("#btn-sample").addEventListener("click", () => {
  $("#ing-text").value = SAMPLE;
  if (!$("#ing-account").value) $("#ing-account").value = "Meridian Freight";
});

$("#btn-ingest").addEventListener("click", async () => {
  const payload = {
    account: $("#ing-account").value.trim(),
    text: $("#ing-text").value.trim(),
    channel: $("#ing-channel").value,
    date: $("#ing-date").value || null,
  };
  if (!payload.account || !payload.text) return toast("Account and signal text are required.", "err");

  loading(true, "Retaining and consolidating…");
  try {
    await api("/api/ingest", { method: "POST", body: payload });
    $("#ingest-result").innerHTML = `<div class="ok-box">✓ Retained to memory. Re-analyze the account to see the verdict change.</div>`;
    toast(`Signal retained for ${payload.account}.`, "ok");
    $("#ing-text").value = "";
    await loadState();
  } catch (e) {
    $("#ingest-result").innerHTML = `<div class="err-box">${esc(e.message)}</div>`;
    toast(e.message, "err");
  } finally { loading(false); }
});

/* ── memory view ──────────────────────────────────────────── */

async function loadMemory() {
  const s = await api("/api/state").catch(() => null);
  if (!s) return;

  $("#acct-list").innerHTML = (s.accounts || []).map(a => `
    <button class="acct-item" data-acct="${esc(a)}"><span class="n">${esc(a)}</span><span class="c">view →</span></button>`).join("") ||
    `<p class="muted small">No accounts yet.</p>`;

  $$("#acct-list .acct-item").forEach(b => b.addEventListener("click", () => {
    $("#account-select").value = b.dataset.acct;
    showView("account");
    runAssess();
  }));

  $("#dir-list").innerHTML = (s.directives || []).map(d => `
    <div class="dir-item"><b>${esc(d.name)}</b><p>${esc(d.content)}</p></div>`).join("") ||
    `<p class="muted small">None installed.</p>`;

  $("#mm-list").innerHTML = (s.mental_models || []).map(m => `
    <div class="mm-item"><code>${esc((m.id || "").slice(0, 18))}…</code> — ${esc(m.name)}</div>`).join("") ||
    `<p class="muted small">No mental models yet — analyzing an account builds one.</p>`;
}

$("#btn-refresh-memory").addEventListener("click", loadMemory);

$("#btn-reset").addEventListener("click", async () => {
  if (!confirm("Wipe every memory from this bank? The demo book can be reloaded.")) return;
  loading(true, "Clearing memories…");
  try { await api("/api/reset", { method: "POST" }); toast("Bank memories cleared.", "ok"); await loadState(); loadBoardIdle(); }
  catch (e) { toast(e.message, "err"); }
  finally { loading(false); }
});

/* ── memory lab (ablation) ────────────────────────────────── */

$("#btn-compare").addEventListener("click", async () => {
  const acct = $("#lab-select").value;
  if (!acct) return toast("Seed memory and pick an account first.", "err");

  $("#lab-result").innerHTML = `<div class="skel" style="height:220px"></div>`;
  loading(true, "Running both arms — full memory vs control bank…");
  try {
    const r = await api("/api/compare", { method: "POST", body: { account: acct } });
    const A = r.with_memory, B = r.without_memory;
    if (!B) { $("#lab-result").innerHTML = `<div class="err-box">Control arm unavailable.</div>`; return; }

    const col = (f, on) => {
      const a = f.assessment, p = f.save_plan;
      return `
        <div class="lab-col ${on ? "on" : "off"}">
          <h4>${on ? "With full memory" : "Memory switched off"}</h4>
          <div class="lab-sub">${on ? "entire account book in the bank" : "only this account's own signals"}</div>
          <div class="lab-play">${esc(p.headline_play)}</div>
          <div class="lab-line"><b>confidence:</b> ${esc(p.confidence)}</div>
          <div class="lab-line"><b>precedent cited:</b> ${(p.precedent || []).length} account(s)</div>
          <div class="lab-line"><b>risk:</b> <span class="badge ${esc(a.risk_tier)}">${esc(a.risk_tier)}</span> ${a.risk_score}/100</div>
          <div class="lab-line"><b>plan:</b> ${(p.actions || []).length} step(s)</div>
          ${on && (p.precedent || []).length
            ? `<div class="precedent" style="margin-top:12px">${p.precedent.map(t =>
                `<div class="pre ${/\b(lost|churn|fail)/i.test(t) ? "lost" : ""}">${esc(t)}</div>`).join("")}</div>`
            : `<div class="lab-line" style="margin-top:12px;color:var(--fg-dim)"><i>No comparable account exists in an empty bank — nothing to cite.</i></div>`}
        </div>`;
    };

    $("#lab-result").innerHTML = `
      <div class="lab-grid">${col(A, true)}${col(B, false)}</div>
      <div class="lab-verdict">
        Same model, same prompts, same account — the only variable is memory.
        <strong>The memory-backed play cites ${(A.save_plan.precedent || []).length} comparable account(s);
        the control cites ${(B.save_plan.precedent || []).length}.</strong>
        Neither answer is wrong. The one with precedent is defensible in a room where the CFO is sitting there.
      </div>`;
  } catch (e) {
    $("#lab-result").innerHTML = `<div class="err-box">${esc(e.message)}</div>`;
    toast(e.message, "err");
  } finally { loading(false); }
});

/* ── boot ─────────────────────────────────────────────────── */

/* Run on DOMContentLoaded when the script is deferred, or immediately when it
   is already past that point. Guarantees the header and board have elements to
   write into — a bare top-level call can fire before the DOM exists. */
function boot() {
  loadState()
    .then(() => {
      if (state.accounts.length) loadBoardIdle();
      showView("overview");
    })
    .catch((e) => {
      // Never leave the header stuck on placeholders without saying why.
      console.error("SignalDesk boot failed:", e);
      const box = $("#board-area");
      if (box) {
        box.innerHTML = `<div class="err-box"><b>Dashboard failed to start.</b><br>${esc(e.message)}</div>`;
      }
      toast(`Dashboard failed to start: ${e.message}`, "err");
    });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
