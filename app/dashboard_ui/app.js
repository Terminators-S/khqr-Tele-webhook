const state = { csrf: "", overview: null, businesses: [], sources: [], intents: [], evidence: [], telegram: null, acceptanceSources: [], acceptance: null };
let acceptanceScanTimer = null;
let acceptanceClockTimer = null;
const $ = (id) => document.getElementById(id);
const pages = ["overview", "setup", "businesses", "payments", "test", "system"];

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
function money(minor, currency) {
  currency = currency || "USD";
  const value = Number(minor || 0) / 100;
  return currency === "USD" ? "$" + value.toFixed(2) : value.toFixed(2) + " " + currency;
}
function dt(value) {
  if (!value) return "—";
  try { return new Date(value).toLocaleString(); } catch { return String(value); }
}
function badge(value) {
  const text = String(value || "unknown");
  const key = text.toLowerCase();
  let cls = "";
  if (["paid","ready","received","success","active"].some(v => key.includes(v))) cls = "good";
  else if (["shadow","pending","partial","disabled"].some(v => key.includes(v))) cls = "warn";
  else if (["failed","quarantine","error","invalid"].some(v => key.includes(v))) cls = "bad";
  return '<span class="pill ' + cls + '">' + esc(text) + '</span>';
}

async function api(path, options) {
  options = options || {};
  const headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
  if (state.csrf && options.method && options.method !== "GET") headers["X-CSRF-Token"] = state.csrf;
  const response = await fetch(path, Object.assign({ credentials: "same-origin" }, options, { headers }));
  let body = {};
  try { body = await response.json(); } catch {}
  if (response.status === 401) { showLogin(); throw new Error("Dashboard session expired"); }
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body);
    throw new Error(detail || "Request failed (" + response.status + ")");
  }
  return body;
}
function notice(message, danger) {
  const good = $("globalNotice"), bad = $("globalError");
  good.classList.add("hidden"); bad.classList.add("hidden");
  const target = danger ? bad : good;
  target.textContent = message; target.classList.remove("hidden");
  window.setTimeout(() => target.classList.add("hidden"), 5000);
}
function showLogin() {
  stopAcceptanceLoops();
  $("appView").classList.add("hidden"); $("loginView").classList.remove("hidden"); state.csrf = "";
}
function showApp() {
  $("loginView").classList.add("hidden"); $("appView").classList.remove("hidden");
}
function setPage(name) {
  if (!pages.includes(name)) return;
  pages.forEach(page => $("page-" + page).classList.toggle("active", page === name));
  document.querySelectorAll(".nav-item").forEach(button => button.classList.toggle("active", button.dataset.page === name));
  $("pageTitle").textContent = name.charAt(0).toUpperCase() + name.slice(1);
}
function checkRow(done, title, detail) {
  return '<div class="check-row ' + (done ? "done" : "") + '">' +
    '<span class="check-icon">' + (done ? "✓" : "!") + '</span>' +
    '<div class="check-copy"><strong>' + esc(title) + '</strong><span>' + esc(detail) + '</span></div></div>';
}
function metric(label, value, note) {
  return '<div class="metric"><span>' + esc(label) + '</span><strong>' + esc(value) +
    '</strong><small>' + esc(note) + '</small></div>';
}

function renderOverview() {
  const o = state.overview; if (!o) return;
  const t = o.totals;
  const shadow = Number(o.runtime.evidence.SHADOW || 0);
  const pending = Number(o.runtime.intents.PENDING || 0) + Number(o.runtime.intents.PARTIALLY_PAID || 0);
  $("metricGrid").innerHTML =
    metric("Businesses", t.businesses, "Consuming projects") +
    metric("Sources", t.sources, t.ready_sources + " ready") +
    metric("Shadow evidence", shadow, "Observed safely") +
    metric("Pending intents", pending, t.allocations + " allocations");

  const s = o.setup;
  $("setupStage").textContent = s.stage.replaceAll("_", " ");
  $("wizardStage").textContent = s.stage.replaceAll("_", " ");
  $("setupChecklist").innerHTML =
    checkRow(s.internal_secret_safe, "Secure installation secret",
      s.internal_secret_safe ? "Internal secret is hardened." : "Change INTERNAL_SECRET before exposure.") +
    checkRow(s.business_count > 0, "Business created",
      s.business_count ? s.business_count + " configured" : "Create the first consuming project.") +
    checkRow(s.source_count > 0, "Payment source added",
      s.source_count ? s.source_count + " configured" : "Add ABA/KHQR source identity.") +
    checkRow(s.telegram_api_credentials, "Telegram API credentials",
      s.telegram_api_credentials ? "API ID/hash configured." : "Set TELEGRAM_API_ID and TELEGRAM_API_HASH.") +
    checkRow(s.telegram_session_exists && s.telegram_session_owner_only, "Dedicated Telegram session",
      s.telegram_session_exists ? "Session exists with owner-only permissions." : "Bootstrap a dedicated collector session.");
  const safety = o.safety;
  const safe = safety.telegram_shadow_only && !safety.allow_live_telegram && !safety.allow_shadow_promotion;
  $("safetyDot").style.background = safe ? "var(--success)" : "var(--danger)";
  $("safetyLabel").textContent = safe ? "Safe shadow defaults" : "Live fuse enabled";
  $("apiHealth").textContent = "API · healthy"; $("apiHealth").className = "pill good";

  const safetyRows = [
    ["Shadow-only collector", safety.telegram_shadow_only, safety.telegram_shadow_only ? "Enabled" : "Disabled"],
    ["Live Telegram fuse", !safety.allow_live_telegram, safety.allow_live_telegram ? "LIVE ENABLED" : "Off"],
    ["Shadow promotion fuse", !safety.allow_shadow_promotion, safety.allow_shadow_promotion ? "PROMOTION ENABLED" : "Off"],
    ["Checkout TTL", true, (safety.checkout_ttl_seconds / 60) + " minutes"],
    ["Late-match grace", true, (safety.late_match_grace_seconds / 60) + " minutes"]
  ];
  $("safetyCards").innerHTML = safetyRows.map(row =>
    '<div class="safety-row"><span class="check-icon" style="color:' +
    (row[1] ? "var(--success)" : "var(--danger)") + '">' + (row[1] ? "✓" : "!") +
    '</span><div class="check-copy"><strong>' + esc(row[0]) + '</strong><span>' +
    esc(row[2]) + '</span></div></div>').join("");

  renderEvidenceTable("overviewEvidence", o.recent_evidence, true);
  renderSetupDetails(); renderSystem();
}
function renderSetupDetails() {
  const s = state.overview && state.overview.setup; if (!s) return;
  const tg = state.telegram || {};
  $("telegramSetupState").innerHTML =
    checkRow(s.telegram_api_credentials, "API credentials", s.telegram_api_credentials ? "Configured in environment." : "Add Telegram API ID and hash.") +
    checkRow(Boolean(tg.authorized), "Dedicated Telegram session", tg.authorized ? "Authorized account connected." : "Connect the collector account below.") +
    checkRow(Boolean(tg.owner_only), "Session permissions", tg.owner_only ? "Mode 0600." : "Session file must be owner-only.");
  $("telegramPhoneForm").classList.toggle("hidden", Boolean(tg.authorized));
  $("telegramCodeForm").classList.add("hidden");
  $("telegramPasswordForm").classList.add("hidden");
  const shadow = Number(state.overview.runtime.evidence.SHADOW || 0);
  $("verificationState").innerHTML =
    checkRow(s.configured_source_count > 0, "Source identity complete", s.configured_source_count + "/" + s.source_count + " fully configured") +
    checkRow(shadow > 0, "SHADOW observations", shadow ? shadow + " evidence rows observed" : "No SHADOW evidence yet") +
    checkRow(s.ready_source_count > 0, "Live-ready source", s.ready_source_count ? s.ready_source_count + " enabled and ready" : "No source is live yet");
}
function renderBusinesses() {
  const target = $("businessList");
  target.innerHTML = state.businesses.length ? state.businesses.map(row =>
    '<article class="entity-card"><div><div class="entity-title"><h3>' + esc(row.name) + '</h3>' +
    (row.is_active ? badge("active") : badge("inactive")) + '</div><div class="entity-meta">' +
    '<span>slug · ' + esc(row.slug) + '</span><span>' + esc(row.source_count) + ' source(s)</span>' +
    '<span>webhook · ' + (row.webhook_configured ? "configured" : "not set") +
    '</span></div></div></article>').join("") :
    '<div class="empty-state">No businesses yet. Use Setup to create one.</div>';
  $("sourceBusiness").innerHTML = state.businesses.length ?
    state.businesses.map(row => '<option value="' + esc(row.id) + '">' + esc(row.name) + '</option>').join("") :
    '<option value="">Create a business first</option>';
}
function renderSources() {
  const target = $("sourceList");
  if (!state.sources.length) { target.innerHTML = '<div class="empty-state">No payment sources configured.</div>'; return; }
  target.innerHTML = state.sources.map(row => {
    const status = row.ready ? "ready" : row.enabled ? "invalid" : "disabled";
    return '<article class="entity-card"><div><div class="entity-title"><h3>' + esc(row.name) + '</h3>' +
      badge(status) + '</div><div class="entity-meta"><span>' + esc(row.currency) + '</span>' +
      '<span>group · ' + esc(row.telegram_group_id ?? "not set") + '</span>' +
      '<span>sender · ' + esc(row.telegram_sender_id ?? "not set") + '</span>' +
      '<span>KHQR · ' + (row.static_khqr_configured ? "configured" : "missing") + '</span></div></div>' +
      '<div class="entity-actions">' +
      (!row.enabled && row.telegram_group_id ? '<button class="btn secondary small" data-discover="' + esc(row.id) + '">Discover sender</button>' : '') +
      (!row.enabled ? '<button class="btn secondary small" data-sender="' + esc(row.id) + '">Set sender</button>' : '') +
      '<button class="btn ' + (row.enabled ? "danger" : "secondary") + ' small" data-toggle="' + esc(row.id) +
      '" data-enabled="' + row.enabled + '">' + (row.enabled ? "Disable" : "Enable") + '</button></div></article>';
  }).join("");
}
function renderEvidenceTable(targetId, rows, compact) {
  const target = $(targetId);
  if (!rows || !rows.length) { target.innerHTML = '<div class="empty-state">No payment evidence yet.</div>'; return; }
  let head = '<thead><tr><th>Transaction</th><th>Amount</th><th>State</th>' +
    (compact ? '' : '<th>Transport</th>') + '<th>Received</th></tr></thead>';
  let body = rows.map(row => '<tr><td><code>' + esc(row.trx) + '</code></td><td>' +
    esc(money(row.amount_minor, row.currency)) + '</td><td>' + badge(row.state) + '</td>' +
    (compact ? '' : '<td>' + esc(row.transport) + '</td>') + '<td>' + esc(dt(row.received_at)) + '</td></tr>').join("");
  target.innerHTML = '<table>' + head + '<tbody>' + body + '</tbody></table>';
}
function renderIntents() {
  const target = $("intentTable");
  if (!state.intents.length) target.innerHTML = '<div class="empty-state">No payment intents yet.</div>';
  else {
    const rows = state.intents.map(row => '<tr><td>' + esc(row.external_id) + '</td><td>' +
      esc(money(row.amount_minor, row.currency)) + '</td><td>' + badge(row.status) +
      '</td><td>' + esc(dt(row.created_at)) + '</td></tr>').join("");
    target.innerHTML = '<table><thead><tr><th>External ID</th><th>Amount</th><th>Status</th><th>Created</th></tr></thead><tbody>' + rows + '</tbody></table>';
  }
  renderEvidenceTable("evidenceTable", state.evidence, false);
}
function renderSystem() {
  const o = state.overview; if (!o) return;
  const r = o.runtime;
  const defs = [
    ["Evidence states", Object.entries(r.evidence).map(x => x[0] + ":" + x[1]).join(" · ") || "none"],
    ["Intent states", Object.entries(r.intents).map(x => x[0] + ":" + x[1]).join(" · ") || "none"],
    ["Webhook queue", Object.entries(r.outbox).map(x => x[0] + ":" + x[1]).join(" · ") || "empty"],
    ["Reservations", (o.safety.reservation_seconds / 60) + " minutes total"]
  ];
  $("runtimeDetails").innerHTML = defs.map(row => '<div class="definition-row"><span>' +
    esc(row[0]) + '</span><strong>' + esc(row[1]) + '</strong></div>').join("");
  const s = o.setup;
  $("securityDetails").innerHTML =
    checkRow(s.internal_secret_safe, "Internal secret hardened", s.internal_secret_safe ? "Not using development default." : "Change INTERNAL_SECRET.") +
    checkRow(s.dashboard_cookie_secure, "Secure dashboard cookie", s.dashboard_cookie_secure ? "HTTPS-only cookie enabled." : "Enable DASHBOARD_COOKIE_SECURE behind HTTPS.") +
    checkRow(!o.safety.allow_live_telegram, "Live Telegram fuse off", "Safe default until intentional activation.") +
    checkRow(!o.safety.allow_shadow_promotion, "Shadow promotion fuse off", "No SHADOW evidence can settle.");
}
function selectedAcceptanceSource() {
  const select = $("acceptanceSource");
  const selected = select ? select.value : "";
  return state.acceptanceSources.find(row => row.source_id === selected)
    || state.acceptanceSources[0]
    || null;
}

function renderAcceptanceSetup() {
  const select = $("acceptanceSource");
  if (!select) return;
  const previous = select.value;
  if (!state.acceptanceSources.length) {
    select.innerHTML = '<option value="">No sources configured</option>';
    $("acceptanceChecklist").innerHTML = checkRow(false, "Payment source", "Create and configure a source first.");
    $("acceptanceStartButton").disabled = true;
    return;
  }

  select.innerHTML = state.acceptanceSources.map(row =>
    '<option value="' + esc(row.source_id) + '">' + esc(row.name) + ' · ' + esc(row.currency) + '</option>'
  ).join("");
  if (state.acceptanceSources.some(row => row.source_id === previous)) select.value = previous;

  const source = selectedAcceptanceSource();
  const telegramReady = Boolean(state.telegram && state.telegram.authorized && state.telegram.owner_only);
  const safety = state.overview ? state.overview.safety : {};
  const safeFuses = Boolean(
    safety.telegram_shadow_only
    && !safety.allow_live_telegram
    && !safety.allow_shadow_promotion
  );
  const checks = [
    [source.configured, "Source configuration", source.configured ? "Complete" : "Complete all source identity fields"],
    [source.disabled, "Normal source disabled", source.disabled ? "Acceptance test stays isolated" : "Disable the normal source first"],
    [source.telegram_group, "Telegram payment group", source.telegram_group ? "Configured" : "Choose the merchant group"],
    [source.trusted_sender, "Trusted ABA sender", source.trusted_sender ? "Configured" : "Run sender discovery"],
    [source.merchant_alias, "Merchant alias", source.merchant_alias ? "Configured" : "Merchant name is required"],
    [source.static_khqr, "Static KHQR", source.static_khqr ? "Configured" : "Add the merchant KHQR payload"],
    [telegramReady, "Telegram session", telegramReady ? "Authorized and owner-only" : "Authorize the dedicated account"],
    [safeFuses, "Safety fuses", safeFuses ? "SHADOW-only; live promotion off" : "Restore safe fuse defaults"],
  ];
  $("acceptanceChecklist").innerHTML = checks.map(row => checkRow(row[0], row[1], row[2])).join("");
  const ready = checks.every(row => row[0]) && source.currency === "USD";
  $("acceptanceStartButton").disabled = !ready;
}

function acceptanceTerminal(result) {
  return ["VERIFIED", "EXPIRED", "CANCELLED"].includes(String(result || ""));
}

function acceptanceResultClass(result) {
  if (result === "VERIFIED") return "good";
  if (result === "MISMATCH" || result === "EXPIRED") return "bad";
  if (result === "CANCELLED") return "";
  return "warn";
}

function renderAcceptanceLive() {
  const test = state.acceptance;
  if (!test) {
    $("acceptanceLive").classList.add("hidden");
    return;
  }

  $("acceptanceLive").classList.remove("hidden");
  $("acceptanceMerchant").textContent = test.merchant_alias || test.source_name || "Merchant";
  $("acceptancePayable").textContent = money(test.payable_amount_minor, test.currency);
  $("acceptanceRemark").textContent = test.remark || "—";
  $("acceptanceQr").src = "/dashboard/api/acceptance-tests/"
    + encodeURIComponent(test.intent_id) + "/qr.png?v=" + encodeURIComponent(test.intent_id);

  const result = String(test.result || "WAITING");
  const badge = $("acceptanceResultBadge");
  badge.textContent = result;
  badge.className = "pill " + acceptanceResultClass(result);

  const observed = Boolean(test.evidence_id);
  const parsed = Boolean(test.trx_tail);
  const matched = result === "VERIFIED";
  const isolated = Boolean(test.webhook_suppressed && !test.source_enabled);
  $("acceptanceTimeline").innerHTML =
    checkRow(true, "Payment request created", "Exact amount fingerprint and Remark reserved") +
    checkRow(true, "Merchant KHQR ready", "Use the configured static merchant QR") +
    checkRow(isolated, "Test isolated from fulfillment", isolated ? "No allocation or webhook allowed" : "Isolation condition failed") +
    checkRow(observed, "Telegram payment observed", observed ? "Trusted merchant notification found" : "Waiting for a new notification") +
    checkRow(observed, "Trusted sender verified", observed ? "Notification sender matched source configuration" : "Pending") +
    checkRow(parsed, "Transaction parsed", parsed ? "Trx ending " + test.trx_tail : "Pending") +
    checkRow(matched, "Core matcher verified", matched ? "Match reason: " + (test.match_reason || "safe match") : (test.result_reason || "Waiting")) +
    checkRow(Boolean(test.would_status), "Settlement preview", test.would_status ? "Would become " + test.would_status : "Not evaluated yet");

  const resultBox = $("acceptanceResult");
  if (result === "WAITING") {
    resultBox.classList.add("hidden");
  } else {
    let title = result;
    let detail = test.result_reason || "";
    if (result === "VERIFIED") {
      const exact = Number(test.would_excess_minor || 0) === 0
        && Number(test.observed_amount_minor || 0) === Number(test.payable_amount_minor || 0);
      title = exact ? "PASS — exact payment verified" : "Payment matched with amount variance";
      detail = "Observed " + money(test.observed_amount_minor, test.currency)
        + " · expected " + money(test.payable_amount_minor, test.currency)
        + " · settlement preview " + (test.would_status || "unknown");
    } else if (result === "MISMATCH") {
      title = "Payment observed but not safely matched";
    } else if (result === "EXPIRED") {
      title = "Test window expired";
    } else if (result === "CANCELLED") {
      title = "Test cancelled";
    }
    resultBox.innerHTML = '<strong>' + esc(title) + '</strong><span>' + esc(detail) + '</span>';
    resultBox.className = "acceptance-result " + acceptanceResultClass(result);
  }

  const terminal = acceptanceTerminal(result);
  $("acceptanceCheckNow").disabled = terminal;
  $("acceptanceCancel").disabled = terminal;
  updateAcceptanceClock();
}

function updateAcceptanceClock() {
  const test = state.acceptance;
  if (!test) return;
  const now = Date.now();
  const checkout = new Date(test.checkout_expires_at).getTime();
  const match = new Date(test.match_expires_at).getTime();
  const target = $("acceptanceTimer");

  let remaining = 0;
  let label = "";
  let cls = "accent";
  if (now < checkout) {
    remaining = checkout - now;
    label = "Pay ";
  } else if (now < match) {
    remaining = match - now;
    label = "Late grace ";
    cls = "warn";
  } else {
    target.textContent = "Expired";
    target.className = "pill bad";
    return;
  }
  const totalSeconds = Math.max(0, Math.floor(remaining / 1000));
  const mm = String(Math.floor(totalSeconds / 60)).padStart(2, "0");
  const ss = String(totalSeconds % 60).padStart(2, "0");
  target.textContent = label + mm + ":" + ss;
  target.className = "pill " + cls;
}

async function refreshAcceptanceStatus() {
  if (!state.acceptance || !state.acceptance.intent_id) return;
  state.acceptance = await api("/dashboard/api/acceptance-tests/" + encodeURIComponent(state.acceptance.intent_id));
  renderAcceptanceLive();
}

async function refreshAll() {
  try {
    const values = await Promise.all([
      api("/dashboard/api/overview"), api("/dashboard/api/businesses"), api("/dashboard/api/sources"),
      api("/dashboard/api/intents?limit=80"), api("/dashboard/api/evidence?limit=80"),
      api("/dashboard/api/telegram/status").catch(() => ({ authorized: false, owner_only: false })),
      api("/dashboard/api/acceptance-tests/prerequisites").catch(() => [])
    ]);
    [state.overview, state.businesses, state.sources, state.intents, state.evidence, state.telegram, state.acceptanceSources] = values;
    renderOverview(); renderBusinesses(); renderSources(); renderIntents(); renderAcceptanceSetup();
  } catch (error) { notice(error.message || String(error), true); }
}
function showSecrets(data) {
  const rows = [["Business ID", data.id], ["API key", data.api_key], ["Webhook secret", data.webhook_secret || "not generated"]];
  $("secretContent").innerHTML = rows.map(row =>
    '<div class="secret-row"><span>' + esc(row[0]) + '</span><code>' + esc(row[1]) +
    '</code><button class="btn secondary small" type="button" data-copy="' + esc(row[1]) + '">Copy</button></div>').join("");
  $("secretDialog").showModal();
}
async function createBusiness(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  if (!data.webhook_url) data.webhook_url = null;
  const result = await api("/dashboard/api/businesses", { method: "POST", body: JSON.stringify(data) });
  showSecrets(result); form.reset();
  notice("Business created. Save the one-time credentials now."); await refreshAll();
}
async function createSource(form) {
  const raw = Object.fromEntries(new FormData(form).entries());
  const payload = Object.assign({}, raw, {
    telegram_group_id: raw.telegram_group_id ? Number(raw.telegram_group_id) : null,
    static_khqr: raw.static_khqr || null, merchant_alias: raw.merchant_alias || null
  });
  await api("/dashboard/api/sources", { method: "POST", body: JSON.stringify(payload) });
  form.reset(); notice("Payment source created disabled."); await refreshAll();
}
async function setSender(sourceId) {
  const raw = window.prompt("Trusted Telegram sender ID"); if (!raw) return;
  const sender = Number(raw.trim());
  if (!Number.isInteger(sender)) { notice("Sender ID must be an integer.", true); return; }
  await api("/dashboard/api/sources/" + encodeURIComponent(sourceId) + "/sender",
    { method: "POST", body: JSON.stringify({ telegram_sender_id: sender }) });
  notice("Trusted sender updated while source remained disabled."); await refreshAll();
}
async function toggleSource(sourceId, enabled) {
  let confirm = "";
  if (!enabled) {
    if (!window.confirm("Enable this source? Payment intents become available once configuration is complete.")) return;
    confirm = window.prompt('Type "ENABLE SOURCE" to continue') || "";
    if (confirm !== "ENABLE SOURCE") { notice("Activation cancelled.", true); return; }
  }
  await api("/dashboard/api/sources/" + encodeURIComponent(sourceId) + "/enabled",
    { method: "POST", body: JSON.stringify({ enabled: !enabled, confirm: confirm }) });
  notice(enabled ? "Source disabled." : "Source enabled."); await refreshAll();
}
function telegramMessage(message, danger) {
  const target = $("telegramAuthMessage");
  target.textContent = message || "";
  target.style.color = danger ? "var(--danger)" : "var(--muted)";
}

async function loadTelegramChats() {
  telegramMessage("Loading Telegram groups…");
  const rows = await api("/dashboard/api/telegram/chats?limit=300");
  $("telegramChatOptions").innerHTML = rows.map(row =>
    '<option value="' + esc(row.id) + '" label="' + esc(row.title + " · " + row.type) + '"></option>'
  ).join("");
  telegramMessage(rows.length + " group/channel dialogs loaded. Select one in the source form.");
}

async function discoverSender(sourceId) {
  telegramMessage("Scanning recent payment notifications for a trusted sender…");
  const result = await api("/dashboard/api/telegram/sources/" + encodeURIComponent(sourceId) + "/discover-sender", {
    method: "POST", body: JSON.stringify({ limit: 300, apply: false })
  });
  if (!result.unanimous || !result.candidate_senders.length) {
    throw new Error("No unanimous ABA sender found in recent group history.");
  }
  const candidate = result.candidate_senders[0];
  const accepted = window.confirm(
    "Found one unanimous sender across " + result.parsed_messages +
    " parsed ABA notification(s). Bind sender " + candidate.sender_id + "?"
  );
  if (!accepted) return;
  await api("/dashboard/api/sources/" + encodeURIComponent(sourceId) + "/sender", {
    method: "POST", body: JSON.stringify({ telegram_sender_id: candidate.sender_id })
  });
  notice("Trusted Telegram sender discovered and bound while source stayed disabled.");
  telegramMessage("Sender discovery complete.");
  await refreshAll();
}

async function sendTelegramCode(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  telegramMessage("Requesting a Telegram login code…");
  const result = await api("/dashboard/api/telegram/send-code", {
    method: "POST", body: JSON.stringify({ phone: data.phone })
  });
  if (result.authorized) {
    telegramMessage("Telegram account is already authorized.");
    await refreshAll();
    return;
  }
  $("telegramPhoneForm").classList.add("hidden");
  $("telegramCodeForm").classList.remove("hidden");
  telegramMessage("Code sent to " + (result.masked_phone || "your Telegram account") + ".");
}

async function confirmTelegramCode(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  const result = await api("/dashboard/api/telegram/confirm-code", {
    method: "POST", body: JSON.stringify({ code: data.code })
  });
  form.reset();
  if (result.requires_password || result.step === "password") {
    $("telegramCodeForm").classList.add("hidden");
    $("telegramPasswordForm").classList.remove("hidden");
    telegramMessage("Code accepted. Enter the Telegram 2-step password.");
    return;
  }
  telegramMessage("Telegram session authorized.");
  await refreshAll();
}

async function confirmTelegramPassword(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  await api("/dashboard/api/telegram/confirm-password", {
    method: "POST", body: JSON.stringify({ password: data.password })
  });
  form.reset();
  telegramMessage("Telegram session authorized.");
  await refreshAll();
}

function stopAcceptanceLoops() {
  if (acceptanceScanTimer) window.clearInterval(acceptanceScanTimer);
  if (acceptanceClockTimer) window.clearInterval(acceptanceClockTimer);
  acceptanceScanTimer = null;
  acceptanceClockTimer = null;
}

function startAcceptanceLoops() {
  stopAcceptanceLoops();
  acceptanceClockTimer = window.setInterval(updateAcceptanceClock, 1000);
  acceptanceScanTimer = window.setInterval(() => {
    if (state.acceptance && !acceptanceTerminal(state.acceptance.result)) {
      scanAcceptance(true);
    }
  }, 8000);
}

async function startAcceptanceTest() {
  const source = selectedAcceptanceSource();
  if (!source) throw new Error("No payment source is selected.");
  const amount = Number($("acceptanceAmount").value);
  if (!Number.isFinite(amount) || amount < 0.01 || amount > 5) {
    throw new Error("Choose a test amount from $0.01 to $5.00.");
  }
  const amountMinor = Math.round(amount * 100);
  const confirm = $("acceptanceConfirm").value.trim();
  const result = await api("/dashboard/api/acceptance-tests/start", {
    method: "POST",
    body: JSON.stringify({
      source_id: source.source_id,
      amount_minor: amountMinor,
      confirm: confirm,
    }),
  });
  state.acceptance = result;
  sessionStorage.setItem("khqr_acceptance_intent", result.intent_id);
  $("acceptanceConfirm").value = "";
  renderAcceptanceLive();
  startAcceptanceLoops();
  notice("Real payment test started. Scan the QR and send the exact amount + Remark.");
}

async function scanAcceptance(silent) {
  if (!state.acceptance || acceptanceTerminal(state.acceptance.result)) return;
  const button = $("acceptanceCheckNow");
  if (button) button.disabled = true;
  try {
    state.acceptance = await api(
      "/dashboard/api/acceptance-tests/"
      + encodeURIComponent(state.acceptance.intent_id)
      + "/scan",
      { method: "POST", body: JSON.stringify({ limit: 160 }) }
    );
    renderAcceptanceLive();
    if (acceptanceTerminal(state.acceptance.result)) {
      stopAcceptanceLoops();
      await refreshAll();
      if (!silent) notice("Acceptance test finished: " + state.acceptance.result + ".");
    }
  } catch (error) {
    if (!silent) notice(error.message || String(error), true);
  } finally {
    if (button && state.acceptance && !acceptanceTerminal(state.acceptance.result)) button.disabled = false;
  }
}

async function cancelAcceptance() {
  if (!state.acceptance || acceptanceTerminal(state.acceptance.result)) return;
  state.acceptance = await api(
    "/dashboard/api/acceptance-tests/"
    + encodeURIComponent(state.acceptance.intent_id)
    + "/cancel",
    { method: "POST", body: "{}" }
  );
  stopAcceptanceLoops();
  renderAcceptanceLive();
  notice("Acceptance test cancelled.");
}

async function restoreAcceptanceFromSession() {
  const intentId = sessionStorage.getItem("khqr_acceptance_intent");
  if (!intentId) return;
  try {
    state.acceptance = await api(
      "/dashboard/api/acceptance-tests/" + encodeURIComponent(intentId)
    );
    renderAcceptanceLive();
    if (!acceptanceTerminal(state.acceptance.result)) startAcceptanceLoops();
  } catch {
    sessionStorage.removeItem("khqr_acceptance_intent");
    state.acceptance = null;
  }
}

async function restoreSession() {
  try {
    const session = await api("/dashboard/api/session");
    state.csrf = session.csrf_token; showApp(); await refreshAll(); await restoreAcceptanceFromSession();
  } catch { showLogin(); }
}
$("loginForm").addEventListener("submit", async event => {
  event.preventDefault(); $("loginError").classList.add("hidden");
  try {
    const result = await api("/dashboard/api/login", {
      method: "POST", body: JSON.stringify({ secret: $("loginSecret").value })
    });
    state.csrf = result.csrf_token; $("loginSecret").value = ""; showApp(); await refreshAll(); await restoreAcceptanceFromSession();
  } catch (error) {
    $("loginError").textContent = error.message || "Login failed"; $("loginError").classList.remove("hidden");
  }
});
$("logoutButton").addEventListener("click", async () => {
  try { await api("/dashboard/api/logout", { method: "POST", body: "{}" }); } finally { showLogin(); }
});
$("refreshButton").addEventListener("click", refreshAll);
$("nav").addEventListener("click", event => {
  const button = event.target.closest("[data-page]"); if (button) setPage(button.dataset.page);
});
$("acceptanceSource").addEventListener("change", renderAcceptanceSetup);
$("acceptanceStartForm").addEventListener("submit", async event => {
  event.preventDefault();
  try { await startAcceptanceTest(); }
  catch (error) { notice(error.message || String(error), true); }
});
$("acceptanceCheckNow").addEventListener("click", async () => {
  await scanAcceptance(false);
});
$("acceptanceCancel").addEventListener("click", async () => {
  try { await cancelAcceptance(); }
  catch (error) { notice(error.message || String(error), true); }
});
$("copyAcceptanceRemark").addEventListener("click", async () => {
  if (!state.acceptance || !state.acceptance.remark) return;
  await navigator.clipboard.writeText(state.acceptance.remark);
  $("copyAcceptanceRemark").textContent = "Copied";
  window.setTimeout(() => $("copyAcceptanceRemark").textContent = "Copy remark", 1200);
});

$("businessForm").addEventListener("submit", async event => {
  event.preventDefault(); try { await createBusiness(event.currentTarget); } catch (error) { notice(error.message, true); }
});
$("sourceForm").addEventListener("submit", async event => {
  event.preventDefault(); try { await createSource(event.currentTarget); } catch (error) { notice(error.message, true); }
});
$("businessForm").elements.name.addEventListener("input", event => {
  const slug = event.target.value.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const field = $("businessForm").elements.slug; if (!field.dataset.touched) field.value = slug;
});
$("businessForm").elements.slug.addEventListener("input", event => { event.target.dataset.touched = "1"; });
$("sourceList").addEventListener("click", async event => {
  const sender = event.target.closest("[data-sender]");
  const discover = event.target.closest("[data-discover]");
  const toggle = event.target.closest("[data-toggle]");
  try {
    if (discover) await discoverSender(discover.dataset.discover);
    if (sender) await setSender(sender.dataset.sender);
    if (toggle) await toggleSource(toggle.dataset.toggle, toggle.dataset.enabled === "true");
  } catch (error) { notice(error.message, true); telegramMessage(error.message, true); }
});
$("telegramPhoneForm").addEventListener("submit", async event => {
  event.preventDefault();
  try { await sendTelegramCode(event.currentTarget); }
  catch (error) { telegramMessage(error.message, true); }
});
$("telegramCodeForm").addEventListener("submit", async event => {
  event.preventDefault();
  try { await confirmTelegramCode(event.currentTarget); }
  catch (error) { telegramMessage(error.message, true); }
});
$("telegramPasswordForm").addEventListener("submit", async event => {
  event.preventDefault();
  try { await confirmTelegramPassword(event.currentTarget); }
  catch (error) { telegramMessage(error.message, true); }
});
$("loadTelegramChats").addEventListener("click", async () => {
  try { await loadTelegramChats(); }
  catch (error) { telegramMessage(error.message, true); }
});
$("cancelTelegramAuth").addEventListener("click", async () => {
  try { await api("/dashboard/api/telegram/cancel", { method: "POST", body: "{}" }); } catch {}
  $("telegramCodeForm").classList.add("hidden");
  $("telegramPasswordForm").classList.add("hidden");
  $("telegramPhoneForm").classList.remove("hidden");
  telegramMessage("Login flow reset.");
});

$("secretContent").addEventListener("click", async event => {
  const button = event.target.closest("[data-copy]"); if (!button) return;
  await navigator.clipboard.writeText(button.dataset.copy); button.textContent = "Copied";
  window.setTimeout(() => button.textContent = "Copy", 1200);
});
restoreSession();
