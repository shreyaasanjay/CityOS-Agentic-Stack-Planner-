const state = {
  tasks: [],
  runMode: "pipeline",
  taskMode: "benchmark",
  runId: null,
  eventSource: null,
  logs: [],
  turns: 0,
  tools: 0,
  artifacts: {},
  activeView: "log",
};

const els = {
  form: document.querySelector("#runForm"),
  provider: document.querySelector("#provider"),
  model: document.querySelector("#model"),
  openaiKey: document.querySelector("#openaiKey"),
  anthropicKey: document.querySelector("#anthropicKey"),
  openrouterKey: document.querySelector("#openrouterKey"),
  ollamaUrl: document.querySelector("#ollamaUrl"),
  taskId: document.querySelector("#taskId"),
  taskSourceFields: document.querySelector("#taskSourceFields"),
  benchmarkField: document.querySelector("#benchmarkField"),
  customTaskField: document.querySelector("#customTaskField"),
  customTask: document.querySelector("#customTask"),
  runtimeFields: document.querySelector("#runtimeFields"),
  runtimeWorkspaceField: document.querySelector("#runtimeWorkspaceField"),
  workspacePathInput: document.querySelector("#workspacePathInput"),
  harness: document.querySelector("#harness"),
  runtimeTask: document.querySelector("#runtimeTask"),
  maxTurns: document.querySelector("#maxTurns"),
  maxTokens: document.querySelector("#maxTokens"),
  temperature: document.querySelector("#temperature"),
  tracefixRunFields: document.querySelector("#tracefixRunFields"),
  opencodeBin: document.querySelector("#opencodeBin"),
  timeout: document.querySelector("#timeout"),
  live: document.querySelector("#live"),
  noSummarize: document.querySelector("#noSummarize"),
  batchLint: document.querySelector("#batchLint"),
  startRun: document.querySelector("#startRun"),
  stopRun: document.querySelector("#stopRun"),
  runMeta: document.querySelector("#runMeta"),
  runTitle: document.querySelector("#runTitle"),
  statusBadge: document.querySelector("#statusBadge"),
  turnCount: document.querySelector("#turnCount"),
  toolCount: document.querySelector("#toolCount"),
  artifactCount: document.querySelector("#artifactCount"),
  workspacePath: document.querySelector("#workspacePathDisplay"),
  timeline: document.querySelector("#timeline"),
  graph: document.querySelector("#topologyGraph"),
  graphStatus: document.querySelector("#graphStatus"),
  refreshArtifacts: document.querySelector("#refreshArtifacts"),
  outputView: document.querySelector("#outputView"),
  toast: document.querySelector("#toast"),
};

const modelDefaults = {
  openai: "gpt-5-mini",
  anthropic: "claude-sonnet-4-5-20250929",
  openrouter: "openai/gpt-5-mini",
  ollama: "llama3.2:3b",
};

function showToast(text) {
  els.toast.textContent = text;
  els.toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => els.toast.classList.remove("visible"), 1800);
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
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

function bindEvents() {
  els.provider.addEventListener("change", () => {
    els.model.value = modelDefaults[els.provider.value] || els.model.value;
    updateKeyFields();
  });

  document.querySelectorAll("[data-task-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      state.taskMode = button.dataset.taskMode;
      document.querySelectorAll("[data-task-mode]").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      els.benchmarkField.classList.toggle("hidden", state.taskMode !== "benchmark");
      els.customTaskField.classList.toggle("hidden", state.taskMode !== "custom");
    });
  });

  document.querySelectorAll("[data-run-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      state.runMode = button.dataset.runMode;
      document.querySelectorAll("[data-run-mode]").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      updateModeFields();
    });
  });

  els.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await startRun();
  });

  els.stopRun.addEventListener("click", async () => {
    if (!state.runId) return;
    await postJson(`/api/runs/${state.runId}/stop`, {});
    showToast("Stop requested");
  });

  els.refreshArtifacts.addEventListener("click", async () => {
    await refreshRun();
    showToast("Artifacts refreshed");
  });

  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeView = button.dataset.view;
      document.querySelectorAll("[data-view]").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      renderOutput();
    });
  });
}

function updateKeyFields() {
  const provider = els.provider.value;
  document.querySelectorAll("[data-key-field]").forEach((field) => {
    field.classList.toggle("hidden", field.dataset.keyField !== provider);
  });
}

function updateModeFields() {
  const isPipeline = state.runMode === "pipeline";
  const isDesignRun = state.runMode === "design_run";
  const isRuntime = state.runMode === "runtime";
  els.taskSourceFields.classList.toggle("hidden", isRuntime);
  els.runtimeFields.classList.toggle("hidden", !(isRuntime || isDesignRun));
  els.runtimeWorkspaceField.classList.toggle("hidden", !isRuntime);
  els.tracefixRunFields.classList.toggle("hidden", isPipeline);
  document.querySelectorAll(".pipeline-only").forEach((item) => {
    item.classList.toggle("hidden", !isPipeline);
  });
  els.startRun.textContent =
    state.runMode === "design"
      ? "Design"
      : state.runMode === "design_run"
        ? "Design + Run"
        : state.runMode === "runtime"
          ? "Run Workspace"
          : "Run LLM";
}

async function loadTasks() {
  const data = await getJson("/api/tasks");
  state.tasks = data.tasks || [];
  els.taskId.innerHTML = state.tasks
    .map((task) => `<option value="${escapeHtml(task.id)}">${escapeHtml(task.id)} - ${escapeHtml(task.title)}</option>`)
    .join("");
  els.taskId.value = state.tasks.find((task) => task.id === "3E") ? "3E" : state.tasks[0]?.id || "";
}

async function startRun() {
  resetRunUi();
  const payload = {
    mode: state.runMode,
    provider: els.provider.value,
    model: els.model.value,
    openaiKey: els.openaiKey.value,
    anthropicKey: els.anthropicKey.value,
    openrouterKey: els.openrouterKey.value,
    ollamaUrl: els.ollamaUrl.value,
    taskMode: state.taskMode,
    taskId: els.taskId.value,
    customTask: els.customTask.value,
    maxTurns: Number(els.maxTurns.value || 20),
    maxTokens: Number(els.maxTokens.value || 32768),
    temperature: Number(els.temperature.value !== "" ? els.temperature.value : 0.3),
    noSummarize: els.noSummarize.checked,
    batchLint: els.batchLint.checked,
    workspacePath: els.workspacePathInput.value,
    harness: els.harness.value,
    runtimeTask: els.runtimeTask.value,
    opencodeBin: els.opencodeBin.value,
    timeout: Number(els.timeout.value || (state.runMode === "runtime" ? 600 : 1800)),
    live: els.live.checked,
  };

  try {
    const run = await postJson("/api/runs", payload);
    state.runId = run.id;
    if (run.artifacts?.workspace) {
      els.workspacePath.textContent = run.artifacts.workspace;
      state.artifacts = run.artifacts;
      renderArtifacts();
    }
    els.startRun.disabled = true;
    els.stopRun.disabled = false;
    if (state.runMode === "runtime") {
      els.runTitle.textContent = `Running ${payload.workspacePath || "workspace"}`;
      els.runMeta.textContent = `${payload.harness} / ${payload.model}`;
    } else if (state.runMode === "design" || state.runMode === "design_run") {
      const action = state.runMode === "design_run" ? "Designing then running" : "Designing";
      els.runTitle.textContent =
        state.taskMode === "benchmark" ? `${action} ${payload.taskId}` : `${action} custom task`;
      els.runMeta.textContent =
        state.runMode === "design_run"
          ? `${payload.provider} / ${payload.model} -> ${payload.harness}`
          : `${payload.provider} / ${payload.model}`;
    } else {
      els.runTitle.textContent =
        state.taskMode === "benchmark" ? `Running ${payload.taskId}` : "Running custom task";
      els.runMeta.textContent = `${payload.provider} / ${payload.model}`;
    }
    setStatus("running");
    connectEvents(run.id);
  } catch (error) {
    setStatus("failed");
    appendTimeline("error", error.message);
    renderOutput();
  }
}

function resetRunUi() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  state.runId = null;
  state.logs = [];
  state.turns = 0;
  state.tools = 0;
  state.artifacts = {};
  els.timeline.innerHTML = "";
  els.outputView.textContent = "";
  els.workspacePath.textContent = "Waiting for run";
  els.turnCount.textContent = "0";
  els.toolCount.textContent = "0";
  els.artifactCount.textContent = "0";
  els.graph.innerHTML = "";
  els.graphStatus.textContent = "Waiting for IR";
}

function connectEvents(runId) {
  const source = new EventSource(`/api/runs/${runId}/events`);
  state.eventSource = source;
  source.addEventListener("tracefix", async (message) => {
    const event = JSON.parse(message.data);
    handleRunEvent(event);
    if (event.type === "status" && ["completed", "failed"].includes(event.status)) {
      source.close();
      state.eventSource = null;
      els.startRun.disabled = false;
      els.stopRun.disabled = true;
      await refreshRun();
    }
  });
  source.onerror = () => {
    source.close();
    state.eventSource = null;
  };
}

function handleRunEvent(event) {
  if (event.line) {
    state.logs.push(event.line);
    appendTimeline(event.type, event.line);
    if (event.type === "turn") {
      state.turns += 1;
      els.turnCount.textContent = String(state.turns);
    }
    if (event.type === "tool") {
      state.tools += 1;
      els.toolCount.textContent = String(state.tools);
    }
  }

  if (event.workspace) {
    els.workspacePath.textContent = event.workspace;
  }

  if (event.type === "artifacts" && event.artifacts) {
    state.artifacts = event.artifacts;
    renderArtifacts();
  }

  if (event.type === "status") {
    setStatus(event.status);
  }

  renderOutput();
}

function appendTimeline(type, line) {
  const item = document.createElement("div");
  item.className = "timeline-item";
  const kind = ["tool", "error", "turn", "pass"].includes(type) ? type : "log";
  item.innerHTML = `
    <span class="timeline-kind ${kind}">${kind}</span>
    <span class="timeline-line">${escapeHtml(line)}</span>
  `;
  els.timeline.appendChild(item);
  els.timeline.scrollTop = els.timeline.scrollHeight;
}

function setStatus(status) {
  const normalized = status === "starting" || status === "stopping" ? "running" : status;
  els.statusBadge.textContent = status;
  els.statusBadge.className = `status-badge ${normalized || "idle"}`;
}

async function refreshRun() {
  if (!state.runId) return;
  const run = await getJson(`/api/runs/${state.runId}`);
  state.artifacts = run.artifacts || {};
  if (run.artifacts?.workspace) {
    els.workspacePath.textContent = run.artifacts.workspace;
  }
  renderArtifacts();
  renderOutput();
}

function renderArtifacts() {
  const files = state.artifacts.files || [];
  els.artifactCount.textContent = String(files.length);
  renderGraph(state.artifacts.ir);
}

function renderOutput() {
  let content = "";
  if (state.activeView === "log") {
    content = state.logs.join("\n");
  } else if (state.activeView === "ir") {
    content = state.artifacts.ir ? JSON.stringify(state.artifacts.ir, null, 2) : "No ir.json yet.";
  } else if (state.activeView === "protocol") {
    content = state.artifacts.protocol || "No Protocol.tla yet.";
  } else if (state.activeView === "states") {
    content = state.artifacts.states ? JSON.stringify(state.artifacts.states, null, 2) : "No states.json yet.";
  } else if (state.activeView === "error") {
    content = state.artifacts.tlcError || "No tlc_error.md yet.";
  }
  els.outputView.textContent = content;
  if (state.activeView === "log") {
    els.outputView.scrollTop = els.outputView.scrollHeight;
  }
}

function renderGraph(ir) {
  const svg = els.graph;
  const width = Math.max(svg.clientWidth || 600, 320);
  const height = Math.max(svg.clientHeight || 430, 320);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = "";

  if (!ir || !Array.isArray(ir.agents)) {
    els.graphStatus.textContent = "Waiting for IR";
    return;
  }
  els.graphStatus.textContent = "IR loaded";

  const agents = ir.agents.map((agent) => ({ id: String(agent.id || agent), type: "agent" }));
  const resources = (ir.resources || []).map((resource) => ({
    id: String(resource.id || resource),
    type: resource.type || "Lock",
  }));
  const channels = ir.channels || [];
  const center = { x: width / 2, y: height / 2 };
  const radiusX = Math.max(115, width * 0.34);
  const radiusY = Math.max(90, height * 0.3);
  const positions = new Map();

  addArrow(svg);

  agents.forEach((agent, index) => {
    const angle = -Math.PI / 2 + (index / Math.max(agents.length, 1)) * Math.PI * 2;
    positions.set(agent.id, {
      x: center.x + Math.cos(angle) * radiusX,
      y: center.y + Math.sin(angle) * radiusY,
    });
  });
  resources.forEach((resource, index) => {
    positions.set(resource.id, {
      x: center.x + (index - (resources.length - 1) / 2) * 88,
      y: center.y,
    });
  });

  resources.forEach((resource) => {
    agents.forEach((agent) => drawLine(svg, positions.get(agent.id), positions.get(resource.id), "resource-path", false));
  });

  channels.forEach((channel, index) => {
    const froms = normalise(channel.from);
    const tos = normalise(channel.to);
    froms.forEach((from) => {
      tos.forEach((to) => {
        if (from !== to) drawCurve(svg, positions.get(from), positions.get(to), index);
      });
    });
  });

  resources.forEach((resource) => drawResource(svg, positions.get(resource.id), resource));
  agents.forEach((agent) => drawAgent(svg, positions.get(agent.id), agent));
}

function addArrow(svg) {
  const defs = node("defs", {});
  defs.innerHTML = `
    <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#435f93"></path>
    </marker>
  `;
  svg.appendChild(defs);
}

function normalise(value) {
  if (Array.isArray(value)) return value.map(String);
  if (value === undefined || value === null) return [];
  return [String(value)];
}

function drawLine(svg, source, target, className, arrow) {
  if (!source || !target) return;
  svg.appendChild(node("path", {
    d: `M ${source.x} ${source.y} L ${target.x} ${target.y}`,
    class: className,
    "marker-end": arrow ? "url(#arrow)" : "",
  }));
}

function drawCurve(svg, source, target, index) {
  if (!source || !target) return;
  const mx = (source.x + target.x) / 2;
  const my = (source.y + target.y) / 2;
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const len = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
  const bend = ((index % 5) - 2) * 17;
  const cx = mx + (-dy / len) * bend;
  const cy = my + (dx / len) * bend;
  svg.appendChild(node("path", {
    d: `M ${source.x} ${source.y} Q ${cx} ${cy} ${target.x} ${target.y}`,
    class: "channel-path",
    "marker-end": "url(#arrow)",
  }));
}

function drawAgent(svg, pos, agent) {
  if (!pos) return;
  svg.appendChild(node("circle", { cx: pos.x, cy: pos.y, r: 27, fill: "#dbeae4", stroke: "#2f6f5f", "stroke-width": 2 }));
  const icon = node("text", { x: pos.x, y: pos.y + 5, class: "node-label" });
  icon.textContent = initials(agent.id);
  svg.appendChild(icon);
  const label = node("text", { x: pos.x, y: pos.y + 44, class: "node-label" });
  label.textContent = compact(agent.id);
  svg.appendChild(label);
  const sub = node("text", { x: pos.x, y: pos.y + 58, class: "node-sub" });
  sub.textContent = "agent";
  svg.appendChild(sub);
}

function drawResource(svg, pos, resource) {
  if (!pos) return;
  svg.appendChild(node("rect", { x: pos.x - 26, y: pos.y - 22, width: 52, height: 44, rx: 7, fill: "#f1d8d4", stroke: "#9c3d32", "stroke-width": 2 }));
  const label = node("text", { x: pos.x, y: pos.y + 4, class: "node-label" });
  label.textContent = compact(resource.id);
  svg.appendChild(label);
  const sub = node("text", { x: pos.x, y: pos.y + 39, class: "node-sub" });
  sub.textContent = resource.type || "Lock";
  svg.appendChild(sub);
}

function node(tag, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attrs).forEach(([key, value]) => {
    if (value !== "") el.setAttribute(key, value);
  });
  return el;
}

function initials(id) {
  return String(id)
    .split(/[_\-\s]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

function compact(id) {
  const text = String(id).replaceAll("_", " ");
  return text.length > 17 ? `${text.slice(0, 16)}...` : text;
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
  updateKeyFields();
  updateModeFields();
  await loadTasks();
  renderOutput();
}

init().catch((error) => {
  appendTimeline("error", error.message);
});
