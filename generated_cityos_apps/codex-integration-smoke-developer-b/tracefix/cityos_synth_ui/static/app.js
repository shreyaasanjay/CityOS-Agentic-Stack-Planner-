const state = {
  workspaces: [],
  result: null,
  logs: [],
};

const els = {
  form: document.querySelector("#synthForm"),
  workspaceSelect: document.querySelector("#workspaceSelect"),
  workspacePath: document.querySelector("#workspacePath"),
  cityosRoot: document.querySelector("#cityosRoot"),
  appsDir: document.querySelector("#appsDir"),
  packageName: document.querySelector("#packageName"),
  overwrite: document.querySelector("#overwrite"),
  refresh: document.querySelector("#refresh"),
  synthesize: document.querySelector("#synthesize"),
  statusText: document.querySelector("#statusText"),
  statusBadge: document.querySelector("#statusBadge"),
  appCount: document.querySelector("#appCount"),
  agentCount: document.querySelector("#agentCount"),
  manifestPath: document.querySelector("#manifestPath"),
  appsDirLabel: document.querySelector("#appsDirLabel"),
  appsList: document.querySelector("#appsList"),
  log: document.querySelector("#log"),
  toast: document.querySelector("#toast"),
};

async function getJson(url) {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed: ${response.status}`);
  return data;
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed: ${response.status}`);
  return data;
}

function showToast(text) {
  els.toast.textContent = text;
  els.toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => els.toast.classList.remove("visible"), 1900);
}

function setStatus(status, label = status) {
  els.statusText.textContent = label;
  els.statusBadge.textContent = status;
  els.statusBadge.className = `badge ${status.toLowerCase()}`;
}

function log(line) {
  state.logs.push(line);
  els.log.textContent = state.logs.join("\n");
  els.log.scrollTop = els.log.scrollHeight;
}

async function loadConfig() {
  const config = await getJson("/api/config");
  els.cityosRoot.value = config.cityosRoot || "";
  els.appsDir.value = config.appsDir || "";
  state.workspaces = config.workspaces || [];
  renderWorkspaces();
}

async function refreshWorkspaces() {
  const data = await getJson("/api/workspaces");
  state.workspaces = data.workspaces || [];
  renderWorkspaces();
  showToast("Workspaces refreshed");
}

function renderWorkspaces() {
  els.workspaceSelect.innerHTML = state.workspaces.length
    ? state.workspaces.map((workspace) => {
        const ready = workspace.productionReady ? "verified" : workspace.verificationStatus;
        const label = `${workspace.name} (${ready})`;
        return `<option value="${escapeHtml(workspace.path)}">${escapeHtml(label)}</option>`;
      }).join("")
    : `<option value="">No workspaces found</option>`;

  if (state.workspaces[0]) {
    els.workspaceSelect.value = state.workspaces[0].path;
    els.workspacePath.value = state.workspaces[0].path;
    updateWorkspaceMetrics(state.workspaces[0]);
  }
}

function updateWorkspaceMetrics(workspace) {
  els.agentCount.textContent = String((workspace.agents || []).filter(Boolean).length);
  els.statusText.textContent = workspace.productionReady
    ? "Verified workspace selected"
    : `Selected workspace: ${workspace.verificationStatus || "unknown"}`;
}

function bindEvents() {
  els.workspaceSelect.addEventListener("change", () => {
    els.workspacePath.value = els.workspaceSelect.value;
    const selected = state.workspaces.find((workspace) => workspace.path === els.workspaceSelect.value);
    if (selected) updateWorkspaceMetrics(selected);
  });

  els.cityosRoot.addEventListener("input", () => {
    if (!els.appsDir.value || els.appsDir.value.endsWith("\\apps") || els.appsDir.value.endsWith("/apps")) {
      const root = els.cityosRoot.value.trim();
      els.appsDir.value = root ? `${root.replace(/[\\/]$/, "")}\\apps` : "";
    }
  });

  els.refresh.addEventListener("click", async () => {
    try {
      await refreshWorkspaces();
    } catch (error) {
      showToast(error.message);
    }
  });

  els.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await synthesize();
  });
}

async function synthesize() {
  state.logs = [];
  state.result = null;
  renderResult();
  setStatus("Running", "Synthesis running");
  els.synthesize.disabled = true;
  log(`Workspace: ${els.workspacePath.value}`);
  log(`CityOS apps: ${els.appsDir.value}`);
  try {
    const result = await postJson("/api/synthesize", {
      workspacePath: els.workspacePath.value,
      cityosRoot: els.cityosRoot.value,
      appsDir: els.appsDir.value,
      packageName: els.packageName.value,
      overwrite: els.overwrite.checked,
    });
    state.result = result;
    log(`Plan: ${result.planPath}`);
    log(`Manifest: ${result.manifestPath}`);
    result.apps.forEach((app) => log(`App: ${app.name} -> ${app.path}`));
    setStatus("Done", "Synthesis completed");
    renderResult();
  } catch (error) {
    log(`Error: ${error.message}`);
    setStatus("Failed", "Synthesis failed");
    showToast(error.message);
  } finally {
    els.synthesize.disabled = false;
  }
}

function renderResult() {
  const result = state.result;
  const apps = result?.apps || [];
  els.appCount.textContent = String(apps.length);
  els.manifestPath.textContent = result?.manifestPath || "No synthesis yet";
  els.appsDirLabel.textContent = result?.appsDir || "";
  if (!apps.length) {
    els.appsList.innerHTML = `<div class="empty">No apps generated yet.</div>`;
    return;
  }
  els.appsList.innerHTML = apps.map((app) => `
    <article class="app-card">
      <div>
        <span>${escapeHtml(app.kind)}</span>
        <h4>${escapeHtml(app.name)}</h4>
        <p>${escapeHtml(app.path)}</p>
      </div>
      <code>${escapeHtml(app.buildCommand)}</code>
    </article>
  `).join("");
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function init() {
  bindEvents();
  renderResult();
  await loadConfig();
}

init().catch((error) => {
  setStatus("Failed", "Startup failed");
  log(error.message);
});
