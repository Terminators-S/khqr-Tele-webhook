const state = {
  csrf: "",
  overview: null,
  stores: [],
  businesses: [],
  sources: [],
  intents: [],
  evidence: [],
  telegram: null,
  telegramChats: [],
  integration: null,
  latestApiKey: "",
  latestWebhookSecret: "",
  acceptanceSources: [],
  acceptance: null,
  selectedStoreId: sessionStorage.getItem("khqr_selected_store") || "",
  selectedSourceId: sessionStorage.getItem("khqr_selected_source") || "",
  creatingStore: false,
  replacingKhqr: false,
  telegramAuthStep: "phone",
};

let acceptanceScanTimer = null;
let acceptanceClockTimer = null;

const $ = id => document.getElementById(id);
const pages = ["home", "setup", "integration", "test", "activity", "advanced"];

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function money(minor, currency) {
  const value = Number(minor || 0) / 100;
  return (currency || "USD") === "USD"
    ? "$" + value.toFixed(2)
    : Math.round(value) + " KHR";
}

function dt(value) {
  if (!value) return "—";
  try { return new Date(value).toLocaleString(); }
  catch { return String(value); }
}

function slugify(value) {
  return String(value || "").toLowerCase().normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function badge(text, kind) {
  return '<span class="pill ' + esc(kind || "") + '">' + esc(text) + "</span>";
}

function checkRow(done, title, detail) {
  return '<div class="check-row ' + (done ? "done" : "") + '">' +
    '<span class="check-icon">' + (done ? "✓" : "!") + "</span>" +
    '<div class="check-copy"><strong>' + esc(title) + "</strong><span>" +
    esc(detail) + "</span></div></div>";
}

function notice(message, danger) {
  const good = $("globalNotice");
  const bad = $("globalError");
  good.classList.add("hidden");
  bad.classList.add("hidden");
  const target = danger ? bad : good;
  target.textContent = message;
  target.classList.remove("hidden");
  window.setTimeout(() => target.classList.add("hidden"), 6000);
}

async function api(path, options) {
  options = options || {};
  const headers = Object.assign({}, options.headers || {});
  if (options.body !== undefined && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  if (state.csrf && options.method && options.method !== "GET") {
    headers["X-CSRF-Token"] = state.csrf;
  }
  const response = await fetch(path, Object.assign(
    { credentials: "same-origin" },
    options,
    { headers }
  ));
  let body = {};
  try { body = await response.json(); } catch {}
  if (response.status === 401) {
    showLogin();
    throw new Error("Your dashboard session expired. Sign in again.");
  }
  if (!response.ok) {
    const detail = typeof body.detail === "string"
      ? body.detail
      : JSON.stringify(body.detail || body);
    throw new Error(detail || "Request failed (" + response.status + ")");
  }
  return body;
}

function showLogin() {
  stopAcceptanceLoops();
  $("appView").classList.add("hidden");
  $("loginView").classList.remove("hidden");
  state.csrf = "";
}

function showApp() {
  $("loginView").classList.add("hidden");
  $("appView").classList.remove("hidden");
}

function setPage(name) {
  if (!pages.includes(name)) return;
  pages.forEach(page => {
    $("page-" + page).classList.toggle("active", page === name);
  });
  document.querySelectorAll(".nav-item").forEach(button => {
    button.classList.toggle("active", button.dataset.page === name);
  });
  const titles = {
    home: "Home",
    setup: "Setup",
    integration: "Integration",
    test: "Test payment",
    activity: "Activity",
    advanced: "Advanced",
  };
  $("pageTitle").textContent = titles[name] || name;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function selectedStore() {
  if (state.creatingStore) return null;
  if (state.selectedStoreId) {
    const selected = state.stores.find(row => row.id === state.selectedStoreId);
    if (selected) return selected;
  }
  return state.stores[0] || null;
}

function primaryBusiness() {
  const store = selectedStore();
  if (store) return store;
  return state.businesses.find(row => row.is_active) || state.businesses[0] || null;
}

function storeSources(store) {
  if (!store) return [];
  if (Array.isArray(store.sources) && store.sources.length) return store.sources;
  return store.source ? [store.source] : [];
}

function primarySource() {
  const store = selectedStore();
  if (!store) return null;
  const rows = storeSources(store);
  if (state.selectedSourceId) {
    const selected = rows.find(row => row.id === state.selectedSourceId);
    if (selected) return selected;
  }
  return rows[0] || null;
}

function setupState() {
  const business = selectedStore();
  const source = primarySource();
  const storeReady = Boolean(business);
  const paymentReady = Boolean(source && source.khqr_valid && source.khqr_image_configured);
  const telegramAccountReady = Boolean(
    state.telegram && state.telegram.api_credentials && state.telegram.authorized
  );
  const telegramReady = Boolean(
    source && telegramAccountReady &&
    source.telegram_group_id !== null &&
    source.telegram_sender_id !== null
  );
  const integrationReady = Boolean(business && business.webhook_configured);
  const testPassed = Boolean(source && state.intents.some(row => row.source_id === source.id && row.status === "TEST_VERIFIED"));
  return {
    business, source, storeReady, paymentReady,
    telegramAccountReady, telegramReady, integrationReady, testPassed,
    completed: [storeReady, paymentReady, telegramReady, integrationReady].filter(Boolean).length,
  };
}

function renderGlobalStoreSelector() {
  const label = $("globalStoreLabel");
  const select = $("globalStoreSelect");
  if (!state.stores.length) {
    label.classList.add("hidden");
    select.innerHTML = "";
    return;
  }
  label.classList.remove("hidden");
  select.innerHTML = state.stores.map(row =>
    '<option value="' + esc(row.id) + '">' + esc(row.name) + '</option>'
  ).join("");
  const currentId = state.selectedStoreId || state.stores[0].id;
  if (state.stores.some(row => row.id === currentId)) {
    select.value = currentId;
  }
}

function renderHome() {
  if (!state.overview) return;
  const s = setupState();
  const coreReady = s.storeReady && s.paymentReady && s.telegramReady;
  let headline = "Finish your payment setup";
  let summary = "We will guide you through the remaining steps.";
  let nextPage = "setup";
  let nextLabel = "Continue setup";

  if (coreReady && !s.integrationReady) {
    headline = "Connect your real project";
    summary = "KHQR is configured. Now connect the website, app or bot that owns your products and orders.";
    nextPage = "integration";
    nextLabel = "Open integration";
  } else if (coreReady && s.integrationReady && !s.testPassed) {
    headline = "Your integration is ready for a real test";
    summary = "Send a small test payment to prove the complete flow before activation.";
    nextPage = "test";
    nextLabel = "Test a payment";
  } else if (s.testPassed) {
    headline = "Payment verification is working";
    summary = "Your real payment test passed. Review Integration to activate the live source when your project is ready.";
    nextPage = "integration";
    nextLabel = "Review go-live";
  }

  $("homeHeadline").textContent = headline;
  $("homeSummary").textContent = summary;
  $("homeNextButton").textContent = nextLabel;
  $("homeNextButton").dataset.goto = nextPage;

  $("homeProgress").innerHTML =
    checkRow(s.storeReady, "Store", s.storeReady ? s.business.name : "Add your store name") +
    checkRow(s.paymentReady, "KHQR payment account",
      s.paymentReady ? "Uploaded KHQR verified for " + (s.source.khqr_merchant_name || s.source.merchant_alias || "merchant") : "Upload your store's static KHQR image") +
    checkRow(s.telegramReady, "Telegram notifications",
      s.telegramReady ? "Payment group connected and ABA sender recognized" : "Connect the Telegram payment group") +
    checkRow(s.integrationReady, "Application integration",
      s.integrationReady ? "Webhook callback configured" : "Connect your website/app and callback URL") +
    checkRow(s.testPassed, "Real payment test",
      s.testPassed ? "Passed" : "Send one small payment when integration is ready");

  const safe = state.overview.safety.telegram_shadow_only &&
    !state.overview.safety.allow_live_telegram &&
    !state.overview.safety.allow_shadow_promotion;
  $("homeStatus").innerHTML =
    checkRow(safe, "Safe test mode", safe ? "Testing cannot fulfill products or promote payments." : "Advanced live controls are enabled.") +
    checkRow(Boolean(s.source && !s.source.enabled), "Live source is off",
      s.source && !s.source.enabled ? "Setup changes are safe." : "No source yet or source is enabled.") +
    checkRow(Boolean(state.telegram && state.telegram.authorized), "Telegram account",
      state.telegram && state.telegram.authorized ? "Connected." : "Not connected yet.");

  $("safetyDot").style.background = safe ? "var(--success)" : "var(--danger)";
  $("safetyLabel").textContent = safe ? "Safe test mode" : "Advanced live mode";
  $("apiHealth").textContent = "Online";
  $("apiHealth").className = "pill good";

  const recentRows = s.source
    ? state.evidence.filter(row => row.source_id === s.source.id).slice(0, 6)
    : [];
  renderEvidenceTable("homeRecent", recentRows, true);
}

function renderSetupStepper() {
  const s = setupState();
  const steps = [
    ["1", "Store", s.storeReady],
    ["2", "KHQR", s.paymentReady],
    ["3", "Telegram", s.telegramReady],
    ["4", "Integration", s.integrationReady],
  ];
  $("setupStepper").innerHTML = steps.map(row =>
    '<div class="setup-step-chip ' + (row[2] ? "done" : "") + '">' +
    '<span>' + row[0] + "</span><strong>" + esc(row[1]) + "</strong></div>"
  ).join("");
  $("setupProgressBadge").textContent = s.completed + " / 4";
}

function renderStoreSetup() {
  const select = $("setupStoreSelect");
  const form = $("storeForm");
  const ready = $("storeReady");

  if (!state.stores.length) {
    state.creatingStore = true;
    select.innerHTML = '<option value="">No stores yet</option>';
    select.disabled = true;
  } else {
    select.disabled = false;
    select.innerHTML = state.stores.map(row =>
      '<option value="' + esc(row.id) + '">' + esc(row.name) + '</option>'
    ).join("");
    if (!state.stores.some(row => row.id === state.selectedStoreId)) {
      state.selectedStoreId = state.stores[0].id;
      sessionStorage.setItem("khqr_selected_store", state.selectedStoreId);
    }
    select.value = state.selectedStoreId;
  }

  const store = selectedStore();
  if (state.creatingStore || !store) {
    form.dataset.mode = "create";
    if (form.dataset.renderedMode !== "create") {
      form.reset();
      form.elements.currency.value = "USD";
      form.dataset.renderedMode = "create";
    }
    $("saveStoreButton").textContent = "Create store";
    $("cancelStoreEdit").classList.toggle("hidden", !state.stores.length);
    ready.classList.add("hidden");
    $("storeStepBadge").textContent = "New store";
    $("storeStepBadge").className = "pill accent";
    return;
  }

  form.dataset.mode = "edit";
  if (form.dataset.renderedStore !== store.id || form.dataset.renderedMode !== "edit") {
    form.elements.name.value = store.name || "";
    form.elements.currency.value = (primarySource() && primarySource().currency) || "USD";
    form.elements.webhook_url.value = store.webhook_url || "";
    form.dataset.renderedStore = store.id;
    form.dataset.renderedMode = "edit";
  }
  $("saveStoreButton").textContent = "Save changes";
  $("cancelStoreEdit").classList.add("hidden");
  ready.classList.remove("hidden");
  ready.innerHTML = '<div><span class="pill good">Selected</span><h3>' +
    esc(store.name) + '</h3><p class="muted">' +
    esc(storeSources(store).length + " payment QR account(s) · you can add more without creating another store.") +
    '</p></div>';
  $("storeStepBadge").textContent = "Ready";
  $("storeStepBadge").className = "pill good";
}

function renderPaymentSetup() {
  const store = selectedStore();
  const sources = storeSources(store);
  const source = primarySource();
  const form = $("khqrUploadForm");
  const preview = $("paymentPreview");
  const ready = Boolean(source && source.khqr_valid && source.khqr_image_configured);
  const sourceSelect = $("setupSourceSelect");

  $("addPaymentQrButton").disabled = !store;
  sourceSelect.disabled = !sources.length;
  if (sources.length) {
    sourceSelect.innerHTML = sources.map(row => {
      const suffix = row.khqr_valid ? " · ready QR" : " · needs QR";
      return '<option value="' + esc(row.id) + '">' + esc(row.name + suffix) + "</option>";
    }).join("");
    if (source && sources.some(row => row.id === source.id)) sourceSelect.value = source.id;
    $("paymentSourceHelp").textContent = sources.length +
      " payment QR account" + (sources.length === 1 ? "" : "s") +
      " in this store. Select one to configure its QR and Telegram group.";
  } else {
    sourceSelect.innerHTML = '<option value="">No payment QR accounts</option>';
    $("paymentSourceHelp").textContent = "Create a store, then add one or many real static KHQR images.";
  }

  Array.from(form.elements).forEach(el => { el.disabled = !source; });

  if (!source) {
    form.classList.remove("hidden");
    preview.classList.add("hidden");
    $("paymentStepBadge").textContent = store ? "Add payment QR" : "Create store first";
    $("paymentStepBadge").className = "pill warn";
    $("khqrSelectedFile").textContent = "Add a payment QR account or bulk upload static KHQR images.";
    return;
  }

  if (ready) {
    preview.classList.remove("hidden");
    $("paymentPreviewQr").src = source.khqr_image_url + "?v=" + Date.now();
    $("paymentPreviewName").textContent = source.khqr_merchant_name || source.merchant_alias || store.name;
    $("paymentPreviewAccount").textContent = "Verified static KHQR · " + source.currency;
    $("paymentPreviewBadge").textContent = "Uploaded KHQR verified";
    $("paymentStepBadge").textContent = "Ready";
    $("paymentStepBadge").className = "pill good";
    form.classList.toggle("hidden", !state.replacingKhqr);
  } else {
    preview.classList.add("hidden");
    form.classList.remove("hidden");
    $("paymentStepBadge").textContent = "Upload QR";
    $("paymentStepBadge").className = "pill warn";
  }

  if (!$("khqrImageInput").files.length) {
    $("khqrSelectedFile").textContent = ready
      ? "Choose a replacement image if you want to change this store's KHQR."
      : "Upload the static QR image customers normally scan.";
  }
}

function renderTelegramSetup() {
  const tg = state.telegram || {};
  const source = primarySource();

  $("telegramCredentialsForm").classList.toggle("hidden", Boolean(tg.api_credentials));

  const showPhone = Boolean(tg.api_credentials && !tg.authorized && state.telegramAuthStep === "phone");
  $("telegramPhoneForm").classList.toggle("hidden", !showPhone);
  $("telegramCodeForm").classList.toggle("hidden", state.telegramAuthStep !== "code");
  $("telegramPasswordForm").classList.toggle("hidden", state.telegramAuthStep !== "password");
  $("telegramGroupPanel").classList.toggle("hidden", !(tg.authorized && source));
  $("cancelTelegramAuth").classList.toggle(
    "hidden",
    !["code", "password"].includes(state.telegramAuthStep)
  );

  if (tg.authorized) {
    $("telegramAccountTitle").textContent = "Connected Telegram account";
    $("telegramAccountDetail").textContent =
      "Account ID " + (tg.account_id || "verified") +
      " · This account is used to read payment notifications for every configured store.";
    $("changeTelegramAccount").classList.remove("hidden");
  } else if (tg.api_credentials) {
    $("telegramAccountTitle").textContent = "Sign in to a Telegram account";
    $("telegramAccountDetail").textContent =
      "Enter the phone number of the Telegram account that is a member of your ABA payment-notification group.";
    $("changeTelegramAccount").classList.add("hidden");
  } else {
    $("telegramAccountTitle").textContent = "Telegram account not configured";
    $("telegramAccountDetail").textContent =
      "First save the Telegram API ID and API hash below. Then the phone-number login will appear here.";
    $("changeTelegramAccount").classList.add("hidden");
  }

  const groupReady = Boolean(source && source.telegram_group_id !== null);
  const senderReady = Boolean(source && source.telegram_sender_id !== null);

  $("telegramSetupState").innerHTML =
    checkRow(Boolean(tg.api_credentials), "Telegram API", tg.api_credentials ? "Saved" : "Add API ID and hash below") +
    checkRow(Boolean(tg.authorized), "Telegram account", tg.authorized ? "Connected · account " + (tg.account_id || "authorized") : "Sign in with your phone number") +
    checkRow(groupReady, "Payment group", groupReady ? "Mapped to group " + source.telegram_group_id : "Choose the group that receives ABA notifications") +
    checkRow(senderReady, "ABA notifications", senderReady ? "Trusted sender " + source.telegram_sender_id : "We will detect the trusted sender");

  const ready = Boolean(tg.authorized && groupReady && senderReady);
  $("telegramStepBadge").textContent = ready ? "Ready" : "Not set";
  $("telegramStepBadge").className = "pill " + (ready ? "good" : "warn");

  renderTelegramGroupOptions();
}

function renderTelegramGroupOptions() {
  const select = $("telegramGroupSelect");
  const source = primarySource();
  const currentId = source && source.telegram_group_id !== null
    ? String(source.telegram_group_id)
    : "";

  if (state.telegramChats.length) {
    select.innerHTML = '<option value="">Choose a group…</option>' +
      state.telegramChats.map(row =>
        '<option value="' + esc(row.id) + '">' + esc(row.title) + "</option>"
      ).join("");
    if (state.telegramChats.some(row => String(row.id) === currentId)) {
      select.value = currentId;
    }
  } else if (currentId) {
    select.innerHTML = '<option value="' + esc(currentId) + '">Current payment group</option>';
  } else {
    select.innerHTML = '<option value="">Load your Telegram groups first</option>';
  }
}

function renderReadySetup() {
  const s = setupState();
  const setupReady = s.storeReady && s.paymentReady && s.telegramReady;
  $("setupReadyState").innerHTML =
    checkRow(s.storeReady, "Store", s.storeReady ? "Ready" : "Finish step 1") +
    checkRow(s.paymentReady, "KHQR", s.paymentReady ? "Valid and scannable" : "Finish step 2") +
    checkRow(s.telegramReady, "Telegram", s.telegramReady ? "Payment group connected" : "Finish step 3") +
    checkRow(s.integrationReady, "Project callback", s.integrationReady ? "Webhook configured" : "Configure it on the Integration page");
  $("goToIntegrationButton").disabled = !setupReady;
  $("readyStepBadge").textContent = s.integrationReady ? "Connected" : (setupReady ? "Connect project" : "Waiting");
  $("readyStepBadge").className = "pill " + (s.integrationReady ? "good" : setupReady ? "accent" : "warn");
}

function renderSetup() {
  renderSetupStepper();
  renderStoreSetup();
  renderPaymentSetup();
  renderTelegramSetup();
  renderReadySetup();
}

function renderCredentialReveal() {
  const target = $("integrationCredentialReveal");
  const rows = [];
  if (state.latestApiKey) rows.push(["API key", state.latestApiKey]);
  if (state.latestWebhookSecret) rows.push(["Webhook secret", state.latestWebhookSecret]);
  if (!rows.length) {
    target.classList.add("hidden");
    target.innerHTML = "";
    return;
  }
  target.classList.remove("hidden");
  target.innerHTML = rows.map(row =>
    '<div class="secret-row"><span>' + esc(row[0]) + '</span><code>' + esc(row[1]) +
    '</code><button class="btn secondary small" type="button" data-copy-integration="' +
    esc(row[1]) + '">Copy</button></div>'
  ).join("") + '<p class="field-help">Save these now. They are not stored in readable form.</p>';
}

function integrationCurl(info) {
  if (!info || !info.source_id) return "Select a store to generate the request.";
  const apiKey = state.latestApiKey || "YOUR_API_KEY";
  const body = JSON.stringify({
    source_id: info.source_id,
    external_id: "order-1001",
    amount_minor: 500,
    currency: info.currency || "USD",
    metadata: { product_id: "sku-123", product_name: "Example product" },
  });
  return 'curl -X POST "' + info.payment_intent_url + '" \\\n' +
    '  -H "Content-Type: application/json" \\\n' +
    '  -H "X-Api-Key: ' + apiKey + '" \\\n' +
    '  -H "Idempotency-Key: order-1001" \\\n' +
    "  -d '" + body + "'";
}

function renderIntegration() {
  const info = state.integration;
  const store = selectedStore();
  if (!info || !store) {
    $("integrationStatusBadge").textContent = "Select a store";
    $("integrationStatusBadge").className = "pill warn";
    $("integrationProjectDetails").innerHTML = '<div class="empty-state">Create or select a store first.</div>';
    $("integrationCredentialState").innerHTML = checkRow(false, "API key", "Create a store first");
    $("integrationWebhookUrl").value = "";
    $("integrationCurl").textContent = "Select a store to generate the request.";
    $("integrationReadiness").innerHTML = checkRow(false, "Store", "Create a store first");
    $("integrationOpenTest").disabled = true;
    $("integrationActivate").disabled = true;
    renderCredentialReveal();
    return;
  }

  const rows = [
    ["Project / store", info.project_name],
    ["Project ID", info.store_id],
    ["Payment source ID", info.source_id || "Not created"],
    ["Currency", info.currency || "—"],
    ["Telegram group", info.telegram_group_id || "Not mapped"],
    ["Trusted sender", info.telegram_sender_id || "Not detected"],
    ["API base URL", info.api_base_url],
  ];
  $("integrationProjectDetails").innerHTML = rows.map(row =>
    '<div class="definition-row"><span>' + esc(row[0]) + '</span><strong><code>' +
    esc(row[1]) + '</code></strong></div>'
  ).join("");

  $("integrationCredentialState").innerHTML =
    checkRow(info.api_key_configured, "Client API key", info.api_key_configured ? "Configured · rotate only if lost or compromised" : "Missing") +
    checkRow(info.webhook_secret_configured, "Webhook signing secret", info.webhook_secret_configured ? "Configured" : "Created automatically when you save a webhook URL");

  if (document.activeElement !== $("integrationWebhookUrl")) {
    $("integrationWebhookUrl").value = info.webhook_url || "";
  }
  $("integrationWebhookBadge").textContent = info.webhook_configured ? "Configured" : "Recommended";
  $("integrationWebhookBadge").className = "pill " + (info.webhook_configured ? "good" : "warn");
  $("rotateWebhookSecretButton").disabled = !info.webhook_configured;

  $("integrationCurl").textContent = integrationCurl(info);
  renderCredentialReveal();

  $("integrationReadiness").innerHTML =
    checkRow(info.khqr_configured, "Merchant KHQR", info.khqr_configured ? "Uploaded and validated" : "Upload it in Setup") +
    checkRow(info.telegram_group_configured, "Telegram payment group", info.telegram_group_configured ? "Mapped to this store" : "Select the payment group in Setup") +
    checkRow(info.telegram_sender_configured, "Trusted ABA sender", info.telegram_sender_configured ? "Detected and bound" : "Run sender discovery in Setup") +
    checkRow(info.webhook_configured, "Project webhook", info.webhook_configured ? "Your product can receive signed payment events" : "Configure the callback before live use") +
    checkRow(info.real_payment_test_verified, "Real payment test", info.real_payment_test_verified ? "Verified" : "Run one small real payment") +
    checkRow(info.live_collector_allowed, "Live Telegram mode", info.live_collector_allowed ? "Server cutover flags are unlocked" : "Safe mode is still locked on the server") +
    checkRow(info.source_enabled, "Live source", info.source_enabled ? "Enabled" : "Still safely disabled");

  const coreReady = info.khqr_configured && info.telegram_group_configured && info.telegram_sender_configured;
  $("integrationOpenTest").disabled = !coreReady;
  $("integrationActivate").disabled = !info.activation_ready && !info.source_enabled;
  $("integrationActivate").textContent = info.source_enabled ? "Disable live source" : "Enable live source";
  $("integrationActivate").className = "btn " + (info.source_enabled ? "danger" : "secondary");
  $("integrationLiveBadge").textContent = info.source_enabled ? "Live" : info.activation_ready ? "Ready to activate" : "Not ready";
  $("integrationLiveBadge").className = "pill " + (info.source_enabled ? "good" : info.activation_ready ? "accent" : "warn");
  $("integrationStatusBadge").textContent = info.source_enabled ? "Production live" : info.webhook_configured ? "Project connected" : "Connect project";
  $("integrationStatusBadge").className = "pill " + (info.source_enabled ? "good" : info.webhook_configured ? "accent" : "warn");
}

async function loadIntegration() {
  const store = selectedStore();
  if (!store) {
    state.integration = null;
    renderIntegration();
    return;
  }
  const source = primarySource();
  state.integration = await api(
    "/dashboard/api/stores/" + encodeURIComponent(store.id) + "/integration" +
    (source ? "?source_id=" + encodeURIComponent(source.id) : "")
  );
  renderIntegration();
}

async function saveIntegrationWebhook(form) {
  const store = selectedStore();
  if (!store) throw new Error("Create or select a store first.");
  const webhookUrl = String(new FormData(form).get("webhook_url") || "").trim() || null;
  const result = await api("/dashboard/api/stores/" + encodeURIComponent(store.id), {
    method: "POST",
    body: JSON.stringify({
      name: store.name,
      currency: (primarySource() && primarySource().currency) || "USD",
      webhook_url: webhookUrl,
    }),
  });
  state.latestWebhookSecret = result.webhook_secret || "";
  notice(webhookUrl ? "Webhook saved. Your project can now receive signed payment events." : "Webhook removed.");
  await refreshAll();
}

async function rotateIntegrationApiKey() {
  const store = selectedStore();
  if (!store) throw new Error("Select a store first.");
  const confirm = window.prompt('Type "ROTATE API KEY" to invalidate the old key') || "";
  if (confirm !== "ROTATE API KEY") return;
  const result = await api("/dashboard/api/stores/" + encodeURIComponent(store.id) + "/rotate-api-key", {
    method: "POST",
    body: JSON.stringify({ confirm }),
  });
  state.latestApiKey = result.api_key;
  renderIntegration();
  notice("New API key created. The previous key no longer works.");
}

async function rotateIntegrationWebhookSecret() {
  const store = selectedStore();
  if (!store) throw new Error("Select a store first.");
  const confirm = window.prompt('Type "ROTATE WEBHOOK SECRET" to invalidate the old signing secret') || "";
  if (confirm !== "ROTATE WEBHOOK SECRET") return;
  const result = await api("/dashboard/api/stores/" + encodeURIComponent(store.id) + "/rotate-webhook-secret", {
    method: "POST",
    body: JSON.stringify({ confirm }),
  });
  state.latestWebhookSecret = result.webhook_secret;
  renderIntegration();
  notice("Webhook signing secret rotated. Update your project before relying on new events.");
}

function statusLabel(status) {
  const map = {
    TEST_WAITING: "Test waiting",
    TEST_VERIFIED: "Test passed",
    TEST_MISMATCH: "Test needs attention",
    TEST_EXPIRED: "Test expired",
    TEST_CANCELLED: "Test cancelled",
    PENDING: "Awaiting payment",
    PARTIALLY_PAID: "Partially paid",
    PAID: "Paid",
  };
  return map[status] || String(status || "Unknown").replaceAll("_", " ").toLowerCase();
}

function evidenceLabel(stateName) {
  const map = {
    SHADOW: "Observed",
    RECEIVED: "Received",
    ALLOCATED: "Matched",
    QUARANTINED: "Needs review",
  };
  return map[stateName] || String(stateName || "Observed");
}

function renderEvidenceTable(targetId, rows, compact) {
  const target = $(targetId);
  if (!rows || !rows.length) {
    target.innerHTML = '<div class="empty-state">No payment notifications yet.</div>';
    return;
  }
  const body = rows.map(row =>
    "<tr><td><code>" + esc(row.trx || "—") + "</code></td><td>" +
    esc(money(row.amount_minor, row.currency)) + "</td><td>" +
    badge(evidenceLabel(row.state), row.state === "QUARANTINED" ? "bad" : "good") +
    "</td><td>" + esc(dt(row.received_at)) + "</td></tr>"
  ).join("");
  target.innerHTML = '<table><thead><tr><th>Transaction</th><th>Amount</th><th>Result</th><th>Time</th></tr></thead><tbody>' +
    body + "</tbody></table>";
}

function renderActivity() {
  const source = primarySource();
  const intents = source
    ? state.intents.filter(row => row.source_id === source.id)
    : [];
  const evidence = source
    ? state.evidence.filter(row => row.source_id === source.id)
    : [];
  const intentTarget = $("intentTable");
  if (!intents.length) {
    intentTarget.innerHTML = '<div class="empty-state">No payment tests yet.</div>';
  } else {
    const rows = intents.map(row => {
      const kind = row.status === "TEST_VERIFIED" || row.status === "PAID"
        ? "good"
        : row.status === "TEST_EXPIRED" || row.status === "TEST_MISMATCH"
          ? "bad" : "warn";
      return "<tr><td>" + esc(row.external_id || "Payment") + "</td><td>" +
        esc(money(row.amount_minor, row.currency)) + "</td><td>" +
        badge(statusLabel(row.status), kind) + "</td><td>" +
        esc(dt(row.created_at)) + "</td></tr>";
    }).join("");
    intentTarget.innerHTML = '<table><thead><tr><th>Payment</th><th>Amount</th><th>Status</th><th>Time</th></tr></thead><tbody>' +
      rows + "</tbody></table>";
  }
  renderEvidenceTable("evidenceTable", evidence, false);
}

function renderAdvanced() {
  const businessTarget = $("businessList");
  businessTarget.innerHTML = state.businesses.length
    ? state.businesses.map(row =>
      '<article class="entity-card"><div><div class="entity-title"><h3>' +
      esc(row.name) + "</h3>" + badge(row.is_active ? "active" : "inactive", row.is_active ? "good" : "warn") +
      '</div><div class="entity-meta"><span>slug · ' + esc(row.slug) +
      "</span><span>" + esc(row.source_count) + " payment source(s)</span><span>webhook · " +
      (row.webhook_configured ? "configured" : "not set") + "</span></div></div></article>"
    ).join("")
    : '<div class="empty-state">No store yet.</div>';

  const sourceTarget = $("sourceList");
  sourceTarget.innerHTML = state.sources.length
    ? state.sources.map(row => {
      const status = row.enabled ? "enabled" : row.khqr_valid ? "configured" : "incomplete";
      return '<article class="entity-card"><div><div class="entity-title"><h3>' +
        esc(row.name) + "</h3>" + badge(status, row.enabled ? "bad" : row.khqr_valid ? "good" : "warn") +
        '</div><div class="entity-meta"><span>currency · ' + esc(row.currency) +
        "</span><span>group · " + esc(row.telegram_group_id ?? "not set") +
        "</span><span>sender · " + esc(row.telegram_sender_id ?? "not set") +
        "</span><span>KHQR · " + (row.khqr_valid ? "valid" : row.khqr_error || "missing") +
        '</span></div></div><div class="entity-actions">' +
        (!row.enabled && row.telegram_group_id
          ? '<button class="btn secondary small" data-discover="' + esc(row.id) + '">Rediscover sender</button>'
          : "") +
        '<button class="btn ' + (row.enabled ? "danger" : "secondary") +
        ' small" data-toggle="' + esc(row.id) + '" data-enabled="' + row.enabled + '">' +
        (row.enabled ? "Disable live source" : "Enable live source") +
        "</button></div></article>";
    }).join("")
    : '<div class="empty-state">No payment source yet.</div>';

  if (!state.overview) return;
  const runtime = state.overview.runtime;
  $("runtimeDetails").innerHTML = [
    ["Evidence", Object.entries(runtime.evidence).map(x => x[0] + ":" + x[1]).join(" · ") || "none"],
    ["Payment intents", Object.entries(runtime.intents).map(x => x[0] + ":" + x[1]).join(" · ") || "none"],
    ["Webhook queue", Object.entries(runtime.outbox).map(x => x[0] + ":" + x[1]).join(" · ") || "empty"],
  ].map(row => '<div class="definition-row"><span>' + esc(row[0]) +
    "</span><strong>" + esc(row[1]) + "</strong></div>").join("");

  const o = state.overview;
  $("securityDetails").innerHTML =
    checkRow(o.setup.internal_secret_safe, "Dashboard secret", o.setup.internal_secret_safe ? "Hardened" : "Needs attention") +
    checkRow(o.setup.dashboard_cookie_secure, "Secure cookie", o.setup.dashboard_cookie_secure ? "HTTPS only" : "Not HTTPS only") +
    checkRow(!o.safety.allow_live_telegram, "Live Telegram", o.safety.allow_live_telegram ? "Enabled" : "Off") +
    checkRow(!o.safety.allow_shadow_promotion, "Automatic promotion", o.safety.allow_shadow_promotion ? "Enabled" : "Off");
}

function acceptanceSourcesForStore() {
  const ids = new Set(storeSources(selectedStore()).map(row => row.id));
  if (!ids.size) return [];
  return state.acceptanceSources.filter(row => ids.has(row.source_id));
}

function selectedAcceptanceSource() {
  const rows = acceptanceSourcesForStore();
  const select = $("acceptanceSource");
  const selected = select ? select.value : "";
  return rows.find(row => row.source_id === selected)
    || rows[0]
    || null;
}

function renderAcceptanceSetup() {
  const select = $("acceptanceSource");
  const previous = select.value;
  const rows = acceptanceSourcesForStore();
  if (!rows.length) {
    select.innerHTML = '<option value="">Finish Setup first</option>';
    $("acceptanceChecklist").innerHTML =
      checkRow(false, "Setup", "Complete Store, KHQR and Telegram first");
    $("acceptanceStartButton").disabled = true;
    return;
  }

  select.innerHTML = rows.map(row =>
    '<option value="' + esc(row.source_id) + '">' + esc(row.name) + "</option>"
  ).join("");
  if (rows.some(row => row.source_id === previous)) {
    select.value = previous;
  } else if (primarySource() && rows.some(row => row.source_id === primarySource().id)) {
    select.value = primarySource().id;
  }

  $("acceptanceSourceLabel").classList.toggle("hidden", rows.length === 1);

  const source = selectedAcceptanceSource();
  const telegramReady = Boolean(state.telegram && state.telegram.authorized);
  const safe = Boolean(
    state.overview &&
    state.overview.safety.telegram_shadow_only &&
    !state.overview.safety.allow_live_telegram &&
    !state.overview.safety.allow_shadow_promotion
  );

  const checks = [
    [source.khqr_valid, "KHQR", source.khqr_valid ? "Valid and ready to scan" : "Rebuild the KHQR in Setup"],
    [telegramReady, "Telegram account", telegramReady ? "Connected" : "Connect Telegram in Setup"],
    [source.telegram_group, "Payment group", source.telegram_group ? "Selected" : "Choose the ABA notification group"],
    [source.trusted_sender, "ABA notification sender", source.trusted_sender ? "Recognized" : "Finish group detection"],
    [source.disabled, "Safe test mode", source.disabled ? "Live store remains off" : "Disable live source before testing"],
    [safe, "No fulfillment during test", safe ? "Protected" : "Restore safe settings in Advanced"],
  ];
  $("acceptanceChecklist").innerHTML = checks.map(row => checkRow(row[0], row[1], row[2])).join("");

  const ready = checks.every(row => row[0]) && source.currency === "USD";
  $("acceptanceStartButton").disabled = !ready;
}

function acceptanceTerminal(result) {
  return ["VERIFIED", "EXPIRED", "CANCELLED"].includes(String(result || ""));
}

function acceptanceResultKind(result) {
  if (result === "VERIFIED") return "good";
  if (result === "MISMATCH" || result === "EXPIRED") return "bad";
  return "warn";
}

function acceptanceReasonLabel(reason) {
  const labels = {
    remark_currency_mismatch: "The payment currency did not match this test.",
    remark_belongs_to_other_request: "That payment note belongs to another payment request.",
    remark_amount_conflict: "The payment note matched, but the amount conflicts with another active request.",
    unknown_or_wrong_remark: "The payment arrived with a different or unknown Remark.",
    ambiguous_amount_match: "The amount could belong to more than one active payment request.",
    no_safe_match: "The payment was seen, but neither the exact Remark nor a unique amount safely identified this test.",
    match_window_expired: "The automatic match window expired. You can still search an exact ABA Trx ID below.",
    operator_cancelled: "The test was cancelled by the operator.",
  };
  return labels[reason] || String(reason || "Waiting for a safe match.").replaceAll("_", " ");
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
  $("acceptanceQr").src = "/dashboard/api/acceptance-tests/" +
    encodeURIComponent(test.intent_id) + "/qr.png?v=" + encodeURIComponent(test.intent_id);

  const result = String(test.result || "WAITING");
  const badgeTarget = $("acceptanceResultBadge");
  const labels = {
    WAITING: "Waiting for payment",
    VERIFIED: "Payment matched",
    MISMATCH: "Needs attention",
    EXPIRED: "Expired",
    CANCELLED: "Cancelled",
  };
  badgeTarget.textContent = labels[result] || result;
  badgeTarget.className = "pill " + acceptanceResultKind(result);

  const observed = Boolean(test.evidence_id);
  const verified = result === "VERIFIED";
  $("acceptanceTimeline").innerHTML =
    checkRow(true, "Test payment created", "QR, exact amount and payment note are ready") +
    checkRow(observed, "Bank notification received", observed ? "Telegram saw the real payment" : "Waiting for your payment") +
    checkRow(observed, "ABA notification recognized", observed ? "Trusted notification confirmed" : "Waiting") +
    checkRow(verified, "Amount + note matched", verified ? "This payment belongs to this test" : acceptanceReasonLabel(test.result_reason)) +
    checkRow(Boolean(test.would_status), "Final check",
      test.would_status === "PAID" ? "Would be accepted as paid" :
      test.would_status ? "Would be " + test.would_status : "Waiting");

  const resultBox = $("acceptanceResult");
  if (result === "WAITING") {
    resultBox.classList.add("hidden");
  } else {
    let title = "Test result";
    let detail = acceptanceReasonLabel(test.result_reason);
    if (result === "VERIFIED") {
      title = "PASS — payment verification works";
      detail = test.recovery_method === "trx_id"
        ? "The exact ABA transaction was found by Trx ID and then safely matched to this test. Your live store is still off."
        : "The real bank notification matched the exact amount and payment note. Your live store is still off.";
    } else if (result === "MISMATCH") {
      title = "Payment seen, but it did not match safely";
    } else if (result === "EXPIRED") {
      title = "Automatic test window expired";
    } else if (result === "CANCELLED") {
      title = "Test cancelled";
      detail = "No live store action was taken.";
    }
    const observedDetail = observed
      ? " Observed: " + money(test.observed_amount_minor, test.currency) +
        (test.trx_tail ? " · Trx …" + test.trx_tail : "") +
        (test.observed_remark ? " · Remark " + test.observed_remark : "")
      : "";
    resultBox.innerHTML = "<strong>" + esc(title) + "</strong><span>" +
      esc(detail + observedDetail) + "</span>";
    resultBox.className = "acceptance-result " + acceptanceResultKind(result);
  }

  const terminal = acceptanceTerminal(result);
  const recoverable = result === "MISMATCH" || result === "EXPIRED";
  $("acceptanceRecoveryForm").classList.toggle("hidden", !recoverable);
  $("acceptanceCheckNow").classList.toggle("hidden", terminal);
  $("acceptanceCancel").classList.toggle("hidden", terminal);
  $("acceptanceCheckNow").disabled = false;
  $("acceptanceCancel").disabled = false;
  $("acceptanceNewTest").classList.toggle("hidden", !terminal);
  updateAcceptanceClock();
}

function updateAcceptanceClock() {
  if (!state.acceptance) return;
  const now = Date.now();
  const checkout = new Date(state.acceptance.checkout_expires_at).getTime();
  const match = new Date(state.acceptance.match_expires_at).getTime();
  const target = $("acceptanceTimer");
  let remaining;
  let prefix;
  let kind = "accent";
  if (now < checkout) {
    remaining = checkout - now;
    prefix = "Pay ";
  } else if (now < match) {
    remaining = match - now;
    prefix = "Grace ";
    kind = "warn";
  } else {
    target.textContent = "Expired";
    target.className = "pill bad";
    return;
  }
  const seconds = Math.max(0, Math.floor(remaining / 1000));
  target.textContent = prefix +
    String(Math.floor(seconds / 60)).padStart(2, "0") + ":" +
    String(seconds % 60).padStart(2, "0");
  target.className = "pill " + kind;
}

async function refreshAll() {
  try {
    const values = await Promise.all([
      api("/dashboard/api/overview"),
      api("/dashboard/api/stores"),
      api("/dashboard/api/businesses"),
      api("/dashboard/api/sources"),
      api("/dashboard/api/intents?limit=80"),
      api("/dashboard/api/evidence?limit=80"),
      api("/dashboard/api/telegram/status").catch(() => ({
        api_credentials: false, authorized: false, owner_only: false,
      })),
      api("/dashboard/api/acceptance-tests/prerequisites").catch(() => []),
    ]);
    [
      state.overview,
      state.stores,
      state.businesses,
      state.sources,
      state.intents,
      state.evidence,
      state.telegram,
      state.acceptanceSources,
    ] = values;

    if (state.selectedStoreId &&
        !state.stores.some(row => row.id === state.selectedStoreId)) {
      state.selectedStoreId = "";
    }
    if (!state.selectedStoreId && state.stores.length && !state.creatingStore) {
      state.selectedStoreId = state.stores[0].id;
      sessionStorage.setItem("khqr_selected_store", state.selectedStoreId);
    }
    const currentStore = selectedStore();
    const currentSources = storeSources(currentStore);
    if (!state.selectedSourceId || !currentSources.some(row => row.id === state.selectedSourceId)) {
      state.selectedSourceId = currentSources.length ? currentSources[0].id : "";
    }
    if (state.selectedSourceId) {
      sessionStorage.setItem("khqr_selected_source", state.selectedSourceId);
    } else {
      sessionStorage.removeItem("khqr_selected_source");
    }
    state.integration = currentStore
      ? await api(
          "/dashboard/api/stores/" + encodeURIComponent(currentStore.id) +
          "/integration" + (state.selectedSourceId ? "?source_id=" + encodeURIComponent(state.selectedSourceId) : "")
        )
      : null;

    renderGlobalStoreSelector();
    renderHome();
    renderSetup();
    renderIntegration();
    renderAcceptanceSetup();
    renderActivity();
    renderAdvanced();
  } catch (error) {
    notice(error.message || String(error), true);
  }
}

function showSecrets(data) {
  const rows = [
    ["Store ID", data.id],
    ["API key", data.api_key],
    ["Webhook secret", data.webhook_secret || "Not configured"],
  ];
  $("secretContent").innerHTML = rows.map(row =>
    '<div class="secret-row"><span>' + esc(row[0]) + "</span><code>" +
    esc(row[1]) + '</code><button class="btn secondary small" type="button" data-copy="' +
    esc(row[1]) + '">Copy</button></div>'
  ).join("");
  $("secretDialog").showModal();
}

async function saveStore(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  const payload = {
    name: String(data.name || "").trim(),
    currency: String(data.currency || "USD").toUpperCase(),
    webhook_url: String(data.webhook_url || "").trim() || null,
  };
  if (!payload.name) throw new Error("Enter a store name.");

  const current = selectedStore();
  const creating = state.creatingStore || !current;
  const path = creating
    ? "/dashboard/api/stores"
    : "/dashboard/api/stores/" + encodeURIComponent(current.id);
  const result = await api(path, {
    method: "POST",
    body: JSON.stringify(payload),
  });

  if (creating) {
    state.selectedStoreId = result.id;
    state.creatingStore = false;
    state.latestApiKey = result.api_key || "";
    state.latestWebhookSecret = result.webhook_secret || "";
    sessionStorage.setItem("khqr_selected_store", result.id);
    showSecrets(result);
    notice("Store created. Next, upload this store's real KHQR image.");
  } else {
    notice("Store settings saved.");
  }
  form.dataset.renderedStore = "";
  form.dataset.renderedMode = "";
  await refreshAll();
}

function beginNewStore() {
  state.creatingStore = true;
  state.replacingKhqr = false;
  $("storeForm").dataset.renderedMode = "";
  renderSetup();
}

function cancelNewStore() {
  state.creatingStore = false;
  $("storeForm").dataset.renderedMode = "";
  renderSetup();
}

function selectStore(storeId) {
  if (!state.stores.some(row => row.id === storeId)) return;
  state.creatingStore = false;
  state.replacingKhqr = false;
  state.latestApiKey = "";
  state.latestWebhookSecret = "";
  state.integration = null;
  state.selectedStoreId = storeId;
  sessionStorage.setItem("khqr_selected_store", storeId);
  const store = selectedStore();
  const sources = storeSources(store);
  state.selectedSourceId = sources.length ? sources[0].id : "";
  if (state.selectedSourceId) sessionStorage.setItem("khqr_selected_source", state.selectedSourceId);
  $("storeForm").dataset.renderedStore = "";
  $("storeForm").dataset.renderedMode = "";
  $("bulkKhqrUploadForm").classList.add("hidden");
  renderHome();
  renderSetup();
  renderIntegration();
  renderAcceptanceSetup();
  loadIntegration().catch(error => notice(error.message || String(error), true));
}

function selectPaymentSource(sourceId) {
  const store = selectedStore();
  const sources = storeSources(store);
  if (!sources.some(row => row.id === sourceId)) return;
  state.selectedSourceId = sourceId;
  state.replacingKhqr = false;
  sessionStorage.setItem("khqr_selected_source", sourceId);
  $("bulkKhqrUploadForm").classList.add("hidden");
  renderHome();
  renderSetup();
  renderIntegration();
  renderAcceptanceSetup();
  renderActivity();
  loadIntegration().catch(error => notice(error.message || String(error), true));
}

function showBulkKhqrUpload() {
  if (!selectedStore()) throw new Error("Create or select a store first.");
  const form = $("bulkKhqrUploadForm");
  form.classList.remove("hidden");
  $("bulkKhqrImages").focus();
}

async function uploadBulkKhqrImages(form) {
  const store = selectedStore();
  if (!store) throw new Error("Create or select a store first.");
  const input = $("bulkKhqrImages");
  const files = Array.from(input.files || []);
  if (!files.length) throw new Error("Choose one or more static KHQR images.");
  if (files.length > 20) throw new Error("Choose at most 20 KHQR images at once.");

  const data = new FormData();
  files.forEach(file => data.append("files", file));
  $("bulkKhqrUploadButton").disabled = true;
  $("bulkKhqrUploadButton").textContent = "Validating QR images…";
  try {
    const result = await api(
      "/dashboard/api/stores/" + encodeURIComponent(store.id) + "/khqr-images",
      { method: "POST", body: data }
    );
    if (result.created && result.created.length) {
      state.selectedSourceId = result.created[result.created.length - 1].id;
      sessionStorage.setItem("khqr_selected_source", state.selectedSourceId);
    }
    form.reset();
    form.classList.add("hidden");
    const createdCount = (result.created || []).length;
    const errors = result.errors || [];
    if (errors.length) {
      notice(
        createdCount + " QR account(s) added; " + errors.length +
        " file(s) rejected. First error: " + errors[0].filename + " — " + errors[0].error,
        true
      );
    } else {
      notice(createdCount + " payment QR account(s) added and verified.");
    }
    await refreshAll();
  } finally {
    $("bulkKhqrUploadButton").disabled = false;
    $("bulkKhqrUploadButton").textContent = "Upload payment QR(s)";
  }
}

async function uploadKhqrImage(form) {
  const source = primarySource();
  if (!source) throw new Error("Create or select a store first.");
  const input = $("khqrImageInput");
  if (!input.files || !input.files[0]) throw new Error("Choose a KHQR image first.");

  const data = new FormData();
  data.append("file", input.files[0]);
  const result = await api(
    "/dashboard/api/sources/" + encodeURIComponent(source.id) + "/khqr-image",
    { method: "POST", body: data }
  );
  state.replacingKhqr = false;
  form.reset();
  $("khqrSelectedFile").textContent = "KHQR image uploaded and verified.";
  notice("KHQR verified. The exact uploaded image is now saved for this store.");
  await refreshAll();
  return result;
}

async function saveTelegramCredentials(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  await api("/dashboard/api/telegram/credentials", {
    method: "POST",
    body: JSON.stringify({
      api_id: Number(data.api_id),
      api_hash: String(data.api_hash || "").trim(),
    }),
  });
  form.reset();
  notice("Telegram API saved. Now connect your Telegram account.");
  await refreshAll();
}

function telegramMessage(message, danger) {
  $("telegramAuthMessage").textContent = message || "";
  $("telegramAuthMessage").style.color = danger ? "var(--danger)" : "var(--muted)";
}

async function sendTelegramCode(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  telegramMessage("Sending login code…");
  const result = await api("/dashboard/api/telegram/send-code", {
    method: "POST",
    body: JSON.stringify({ phone: String(data.phone || "").trim() }),
  });
  if (result.authorized) {
    state.telegramAuthStep = "phone";
    telegramMessage("Telegram is already connected.");
    await refreshAll();
    return;
  }
  state.telegramAuthStep = "code";
  renderTelegramSetup();
  telegramMessage("Code sent. Enter the confirmation code.");
}

async function confirmTelegramCode(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  const result = await api("/dashboard/api/telegram/confirm-code", {
    method: "POST",
    body: JSON.stringify({ code: String(data.code || "").trim() }),
  });
  form.reset();
  if (result.requires_password || result.step === "password") {
    state.telegramAuthStep = "password";
    renderTelegramSetup();
    telegramMessage("Code accepted. Enter your Telegram 2-step password.");
    return;
  }
  state.telegramAuthStep = "phone";
  telegramMessage("Telegram connected. Now choose the payment group.");
  await refreshAll();
}

async function confirmTelegramPassword(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  await api("/dashboard/api/telegram/confirm-password", {
    method: "POST",
    body: JSON.stringify({ password: data.password }),
  });
  form.reset();
  state.telegramAuthStep = "phone";
  telegramMessage("Telegram connected. Now choose the payment group.");
  await refreshAll();
}

async function changeTelegramAccount() {
  if (!window.confirm(
    "Change the Telegram account used by KHQR? This will disconnect the saved session and require sender verification again for every store."
  )) return;
  const confirm = window.prompt('Type "CHANGE TELEGRAM ACCOUNT" to continue') || "";
  if (confirm !== "CHANGE TELEGRAM ACCOUNT") return;

  const result = await api("/dashboard/api/telegram/reset-account", {
    method: "POST",
    body: JSON.stringify({ confirm }),
  });
  state.telegramChats = [];
  state.telegramAuthStep = "phone";
  telegramMessage(
    "Old Telegram account disconnected. Sign in with the new phone number, then load and verify each payment group again."
  );
  await refreshAll();
  notice(
    "Telegram account disconnected. " +
    result.sender_bindings_cleared + " trusted sender binding(s) and " +
    result.group_mappings_cleared + " payment group mapping(s) cleared safely."
  );
}

async function loadTelegramChats() {
  telegramMessage("Loading your Telegram groups…");
  state.telegramChats = await api("/dashboard/api/telegram/chats?limit=300");
  renderTelegramGroupOptions();
  telegramMessage(state.telegramChats.length + " groups/channels loaded.");
}

async function useTelegramGroup() {
  const source = primarySource();
  if (!source) throw new Error("Connect your KHQR payment account first.");
  const groupId = Number($("telegramGroupSelect").value);
  if (!Number.isInteger(groupId)) throw new Error("Choose a Telegram payment group.");

  await api("/dashboard/api/sources/" + encodeURIComponent(source.id) + "/telegram-group", {
    method: "POST",
    body: JSON.stringify({ telegram_group_id: groupId }),
  });

  telegramMessage("Checking recent ABA payment notifications in this group…");
  const result = await api(
    "/dashboard/api/telegram/sources/" + encodeURIComponent(source.id) + "/discover-sender",
    {
      method: "POST",
      body: JSON.stringify({ limit: 300, apply: true }),
    }
  );
  if (!result.unanimous || !result.applied) {
    throw new Error("Could not safely recognize one ABA notification sender in this group.");
  }
  telegramMessage("Payment group connected. ABA notifications were recognized automatically.");
  notice("Telegram payment notifications are ready.");
  await refreshAll();
}

async function discoverSender(sourceId) {
  const result = await api(
    "/dashboard/api/telegram/sources/" + encodeURIComponent(sourceId) + "/discover-sender",
    { method: "POST", body: JSON.stringify({ limit: 300, apply: true }) }
  );
  if (!result.unanimous || !result.applied) {
    throw new Error("Sender discovery was not unanimous.");
  }
  notice("Trusted ABA sender refreshed.");
  await refreshAll();
}

async function toggleSource(sourceId, currentlyEnabled) {
  let confirm = "";
  if (!currentlyEnabled) {
    if (!window.confirm("Advanced action: enable live payment source? Only do this after a successful real payment test.")) return;
    confirm = window.prompt('Type "ENABLE SOURCE" to continue') || "";
    if (confirm !== "ENABLE SOURCE") return;
  }
  await api("/dashboard/api/sources/" + encodeURIComponent(sourceId) + "/enabled", {
    method: "POST",
    body: JSON.stringify({ enabled: !currentlyEnabled, confirm }),
  });
  notice(currentlyEnabled ? "Live source disabled." : "Live source enabled.");
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
  if (!source) throw new Error("Finish Setup first.");
  const amount = Number($("acceptanceAmount").value);
  if (!Number.isFinite(amount) || amount < 0.01 || amount > 5) {
    throw new Error("Choose a test amount from $0.01 to $5.00.");
  }
  const result = await api("/dashboard/api/acceptance-tests/start", {
    method: "POST",
    body: JSON.stringify({
      source_id: source.source_id,
      amount_minor: Math.round(amount * 100),
      confirm: $("acceptanceConfirm").value.trim(),
    }),
  });
  state.acceptance = result;
  sessionStorage.setItem("khqr_acceptance_intent", result.intent_id);
  $("acceptanceConfirm").value = "";
  renderAcceptanceLive();
  startAcceptanceLoops();
  notice("Test created. Scan the QR and send the exact amount + payment note.");
}

async function scanAcceptance(silent) {
  if (!state.acceptance || acceptanceTerminal(state.acceptance.result)) return;
  const button = $("acceptanceCheckNow");
  button.disabled = true;
  button.textContent = "Checking Telegram…";
  try {
    state.acceptance = await api(
      "/dashboard/api/acceptance-tests/" +
      encodeURIComponent(state.acceptance.intent_id) + "/scan",
      { method: "POST", body: JSON.stringify({ limit: 300 }) }
    );
    renderAcceptanceLive();
    if (acceptanceTerminal(state.acceptance.result)) {
      stopAcceptanceLoops();
      await refreshAll();
      if (!silent) notice("Payment test finished.");
    } else if (!silent) {
      notice(state.acceptance.result === "MISMATCH"
        ? "Payment notification found, but it needs review. Use the Trx ID recovery section if needed."
        : "Checked Telegram. Still waiting for a matching payment.");
    }
  } catch (error) {
    if (!silent) notice(error.message || String(error), true);
  } finally {
    button.textContent = "Check now";
    button.disabled = false;
  }
}

async function recoverAcceptanceByTrx(form) {
  if (!state.acceptance) throw new Error("Start or restore a payment test first.");
  const trxId = String(new FormData(form).get("trx_id") || "").trim();
  if (trxId.length < 6) throw new Error("Enter the ABA transaction ID.");
  const button = $("acceptanceRecoverButton");
  button.disabled = true;
  button.textContent = "Searching trusted Telegram history…";
  try {
    state.acceptance = await api(
      "/dashboard/api/acceptance-tests/" +
      encodeURIComponent(state.acceptance.intent_id) + "/recover",
      { method: "POST", body: JSON.stringify({ trx_id: trxId }) }
    );
    renderAcceptanceLive();
    if (state.acceptance.result === "VERIFIED") {
      stopAcceptanceLoops();
      await refreshAll();
      notice("Transaction found and safely verified by exact Trx ID lookup.");
    } else {
      notice("Transaction found, but it still does not safely match this test. Review the reason shown above.", true);
    }
  } finally {
    button.disabled = false;
    button.textContent = "Find paid transaction";
  }
}

function startNewAcceptanceTest() {
  stopAcceptanceLoops();
  sessionStorage.removeItem("khqr_acceptance_intent");
  state.acceptance = null;
  $("acceptanceRecoveryForm").reset();
  $("acceptanceLive").classList.add("hidden");
  renderAcceptanceSetup();
  $("acceptanceSetup").scrollIntoView({ behavior: "smooth", block: "start" });
  notice("Ready for a new real payment test.");
}

async function cancelAcceptance() {
  if (!state.acceptance || acceptanceTerminal(state.acceptance.result)) return;
  state.acceptance = await api(
    "/dashboard/api/acceptance-tests/" +
    encodeURIComponent(state.acceptance.intent_id) + "/cancel",
    { method: "POST", body: "{}" }
  );
  stopAcceptanceLoops();
  renderAcceptanceLive();
  notice("Test cancelled.");
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
    state.csrf = session.csrf_token;
    showApp();
    await refreshAll();
    await restoreAcceptanceFromSession();
  } catch {
    showLogin();
  }
}

$("loginForm").addEventListener("submit", async event => {
  event.preventDefault();
  $("loginError").classList.add("hidden");
  try {
    const result = await api("/dashboard/api/login", {
      method: "POST",
      body: JSON.stringify({ secret: $("loginSecret").value }),
    });
    state.csrf = result.csrf_token;
    $("loginSecret").value = "";
    showApp();
    await refreshAll();
    await restoreAcceptanceFromSession();
  } catch (error) {
    $("loginError").textContent = error.message || "Login failed";
    $("loginError").classList.remove("hidden");
  }
});

$("logoutButton").addEventListener("click", async () => {
  try { await api("/dashboard/api/logout", { method: "POST", body: "{}" }); }
  finally { showLogin(); }
});

$("refreshButton").addEventListener("click", refreshAll);

$("globalStoreSelect").addEventListener("change", event => {
  selectStore(event.target.value);
  renderGlobalStoreSelector();
});

$("nav").addEventListener("click", event => {
  const button = event.target.closest("[data-page]");
  if (button) setPage(button.dataset.page);
});

document.addEventListener("click", event => {
  const goto = event.target.closest("[data-goto]");
  if (goto) setPage(goto.dataset.goto);
});

$("homeNextButton").addEventListener("click", event => {
  setPage(event.currentTarget.dataset.goto || "setup");
});

$("goToIntegrationButton").addEventListener("click", () => setPage("integration"));

$("integrationWebhookForm").addEventListener("submit", async event => {
  event.preventDefault();
  try { await saveIntegrationWebhook(event.currentTarget); }
  catch (error) { notice(error.message || String(error), true); }
});

$("rotateApiKeyButton").addEventListener("click", async () => {
  try { await rotateIntegrationApiKey(); }
  catch (error) { notice(error.message || String(error), true); }
});

$("rotateWebhookSecretButton").addEventListener("click", async () => {
  try { await rotateIntegrationWebhookSecret(); }
  catch (error) { notice(error.message || String(error), true); }
});

$("copyIntegrationCurl").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("integrationCurl").textContent || "");
  $("copyIntegrationCurl").textContent = "Copied";
  window.setTimeout(() => $("copyIntegrationCurl").textContent = "Copy", 1200);
});

$("integrationCredentialReveal").addEventListener("click", async event => {
  const button = event.target.closest("[data-copy-integration]");
  if (!button) return;
  await navigator.clipboard.writeText(button.dataset.copyIntegration);
  button.textContent = "Copied";
  window.setTimeout(() => button.textContent = "Copy", 1200);
});

$("integrationOpenTest").addEventListener("click", () => setPage("test"));

$("integrationActivate").addEventListener("click", async () => {
  const source = primarySource();
  if (!source) return;
  try {
    await toggleSource(source.id, Boolean(state.integration && state.integration.source_enabled));
  } catch (error) {
    notice(error.message || String(error), true);
  }
});

$("storeForm").addEventListener("submit", async event => {
  event.preventDefault();
  try { await saveStore(event.currentTarget); }
  catch (error) { notice(error.message, true); }
});

$("setupStoreSelect").addEventListener("change", event => {
  selectStore(event.target.value);
});

$("newStoreButton").addEventListener("click", beginNewStore);
$("cancelStoreEdit").addEventListener("click", cancelNewStore);

$("setupSourceSelect").addEventListener("change", event => {
  selectPaymentSource(event.target.value);
});

$("addPaymentQrButton").addEventListener("click", () => {
  try { showBulkKhqrUpload(); }
  catch (error) { notice(error.message || String(error), true); }
});

$("cancelBulkKhqrUpload").addEventListener("click", () => {
  $("bulkKhqrUploadForm").reset();
  $("bulkKhqrUploadForm").classList.add("hidden");
  $("bulkKhqrSelected").textContent = "The service validates every file and keeps the exact original image.";
});

$("bulkKhqrImages").addEventListener("change", event => {
  const files = Array.from(event.target.files || []);
  const totalKb = Math.max(1, Math.round(files.reduce((sum, file) => sum + file.size, 0) / 1024));
  $("bulkKhqrSelected").textContent = files.length
    ? files.length + " file(s) selected · " + totalKb + " KB total"
    : "The service validates every file and keeps the exact original image.";
});

$("bulkKhqrUploadForm").addEventListener("submit", async event => {
  event.preventDefault();
  try { await uploadBulkKhqrImages(event.currentTarget); }
  catch (error) { notice(error.message || String(error), true); }
});

$("khqrImageInput").addEventListener("change", event => {
  const file = event.target.files && event.target.files[0];
  $("khqrSelectedFile").textContent = file
    ? file.name + " · " + Math.max(1, Math.round(file.size / 1024)) + " KB"
    : "Upload the static QR image customers normally scan.";
});

$("khqrUploadForm").addEventListener("submit", async event => {
  event.preventDefault();
  try { await uploadKhqrImage(event.currentTarget); }
  catch (error) { notice(error.message, true); }
});

$("replaceKhqrButton").addEventListener("click", () => {
  state.replacingKhqr = true;
  renderPaymentSetup();
  window.setTimeout(() => $("khqrImageInput").click(), 0);
});

$("telegramCredentialsForm").addEventListener("submit", async event => {
  event.preventDefault();
  try { await saveTelegramCredentials(event.currentTarget); }
  catch (error) { telegramMessage(error.message, true); }
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

$("changeTelegramAccount").addEventListener("click", async () => {
  try { await changeTelegramAccount(); }
  catch (error) { telegramMessage(error.message || String(error), true); }
});

$("loadTelegramChats").addEventListener("click", async () => {
  try { await loadTelegramChats(); }
  catch (error) { telegramMessage(error.message, true); }
});

$("useTelegramGroup").addEventListener("click", async () => {
  try { await useTelegramGroup(); }
  catch (error) { telegramMessage(error.message, true); }
});

$("cancelTelegramAuth").addEventListener("click", async () => {
  try { await api("/dashboard/api/telegram/cancel", { method: "POST", body: "{}" }); } catch {}
  state.telegramAuthStep = "phone";
  renderTelegramSetup();
  telegramMessage("Telegram login reset.");
});

$("sourceList").addEventListener("click", async event => {
  const discover = event.target.closest("[data-discover]");
  const toggle = event.target.closest("[data-toggle]");
  try {
    if (discover) await discoverSender(discover.dataset.discover);
    if (toggle) await toggleSource(toggle.dataset.toggle, toggle.dataset.enabled === "true");
  } catch (error) {
    notice(error.message, true);
  }
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

$("acceptanceRecoveryForm").addEventListener("submit", async event => {
  event.preventDefault();
  try { await recoverAcceptanceByTrx(event.currentTarget); }
  catch (error) { notice(error.message || String(error), true); }
});

$("acceptanceNewTest").addEventListener("click", startNewAcceptanceTest);

$("acceptanceCancel").addEventListener("click", async () => {
  try { await cancelAcceptance(); }
  catch (error) { notice(error.message || String(error), true); }
});

$("copyAcceptanceRemark").addEventListener("click", async () => {
  if (!state.acceptance || !state.acceptance.remark) return;
  await navigator.clipboard.writeText(state.acceptance.remark);
  $("copyAcceptanceRemark").textContent = "Copied";
  window.setTimeout(() => $("copyAcceptanceRemark").textContent = "Copy note", 1200);
});

$("secretContent").addEventListener("click", async event => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  await navigator.clipboard.writeText(button.dataset.copy);
  button.textContent = "Copied";
  window.setTimeout(() => button.textContent = "Copy", 1200);
});

restoreSession();
