const state = {
  tasks: [],
  runMode: "design",
  taskMode: "benchmark",
  runId: null,
  eventSource: null,
  logs: [],
  turns: 0,
  tools: 0,
  artifacts: {},
  activeView: "log",
  usage: null,
  usageDetailsOpen: false,
};

const els = {
  form: document.querySelector("#runForm"),
  provider: document.querySelector("#provider"),
  providerFields: document.querySelector("#providerFields"),
  keyFields: document.querySelector("#keyFields"),
  model: document.querySelector("#model"),
  modelSuggestions: document.querySelector("#modelSuggestions"),
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
  legacyRuntimeOptions: document.querySelector("#legacyRuntimeOptions"),
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
  llmUsageCard: document.querySelector("#llmUsageCard"),
  llmUsageDetails: document.querySelector("#llmUsageDetails"),
  llmCost: document.querySelector("#llmCost"),
  llmTokenSummary: document.querySelector("#llmTokenSummary"),
  llmModelName: document.querySelector("#llmModelName"),
  llmUsageSource: document.querySelector("#llmUsageSource"),
  usageInputTokens: document.querySelector("#usageInputTokens"),
  usageOutputTokens: document.querySelector("#usageOutputTokens"),
  usageTotalTokens: document.querySelector("#usageTotalTokens"),
  usageCost: document.querySelector("#usageCost"),
  usageDesignTokens: document.querySelector("#usageDesignTokens"),
  usageDesignCost: document.querySelector("#usageDesignCost"),
  usageRepairTokens: document.querySelector("#usageRepairTokens"),
  usageRepairCost: document.querySelector("#usageRepairCost"),
  usageVerificationTokens: document.querySelector("#usageVerificationTokens"),
  usageVerificationCost: document.querySelector("#usageVerificationCost"),
  usageTotalRuns: document.querySelector("#usageTotalRuns"),
  usageSessionTokens: document.querySelector("#usageSessionTokens"),
  usageSessionCost: document.querySelector("#usageSessionCost"),
  timeline: document.querySelector("#timeline"),
  graph: document.querySelector("#topologyGraph"),
  graphStatus: document.querySelector("#graphStatus"),
  refreshArtifacts: document.querySelector("#refreshArtifacts"),
  openWorkspace: document.querySelector("#openWorkspace"),
  openTlcError: document.querySelector("#openTlcError"),
  openSpecFolder: document.querySelector("#openSpecFolder"),
  outputView: document.querySelector("#outputView"),
  toast: document.querySelector("#toast"),
};

const modelDefaults = {
  openai: "gpt-5-mini",
  anthropic: "claude-sonnet-4-5-20250929",
  openrouter: "openai/gpt-4.1-mini",
  ollama: "llama3.2:3b",
};

const modelOptions = {
  openai: [
    "gpt-5-mini",
    "gpt-5",
    "gpt-4.1-mini",
    "gpt-4.1",
  ],
  anthropic: [
    "claude-sonnet-4-5-20250929",
    "claude-opus-4-1-20250805",
    "claude-3-5-haiku-20241022",
  ],
  openrouter: [
    "openai/gpt-4.1-mini",
    "openai/gpt-4.1",
    "openai/gpt-5-mini",
    "anthropic/claude-sonnet-4",
    "anthropic/claude-3.5-sonnet",
    "google/gemini-2.5-pro",
    "google/gemini-2.5-flash",
    "meta-llama/llama-3.1-405b-instruct",
    "deepseek/deepseek-chat-v3-0324",
  ],
  ollama: [
    "llama3.2:3b",
    "llama3.1:8b",
    "qwen2.5-coder:7b",
    "mistral:7b",
  ],
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
    updateModelSuggestions();
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

  els.openWorkspace.addEventListener("click", async () => {
    const workspacePath = currentWorkspacePath();
    if (!workspacePath) {
      showToast("No workspace selected");
      return;
    }
    try {
      await postJson("/api/open-workspace", { workspacePath });
      showToast("Workspace opened");
    } catch (error) {
      showToast(error.message);
    }
  });

  els.openTlcError.addEventListener("click", async () => {
    await openArtifact("tlc_error", "TLC error opened");
  });

  els.openSpecFolder.addEventListener("click", async () => {
    await openArtifact("spec_dir", "Spec folder opened");
  });

  els.llmUsageCard.addEventListener("click", () => {
    state.usageDetailsOpen = !state.usageDetailsOpen;
    els.llmUsageCard.setAttribute("aria-expanded", String(state.usageDetailsOpen));
    els.llmUsageDetails.classList.toggle("hidden", !state.usageDetailsOpen);
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

function updateModelSuggestions() {
  const options = modelOptions[els.provider.value] || [];
  els.modelSuggestions.innerHTML = options
    .map((model) => `<option value="${escapeHtml(model)}"></option>`)
    .join("");
}

function updateModeFields() {
  const isRuntime = state.runMode === "runtime";
  const isPlan = state.runMode === "plan";
  const isDesign = state.runMode === "design";
  const isDesignRun = state.runMode === "design_run";
  document.querySelectorAll("[data-run-mode]").forEach((item) => {
    item.classList.toggle("active", item.dataset.runMode === state.runMode);
  });
  els.taskSourceFields.classList.toggle("hidden", isRuntime || isPlan);
  els.providerFields.classList.toggle("hidden", isPlan);
  els.keyFields.classList.toggle("hidden", isPlan);
  els.runtimeFields.classList.toggle("hidden", isDesign || isDesignRun);
  els.runtimeWorkspaceField.classList.toggle("hidden", isDesign || isDesignRun);
  els.legacyRuntimeOptions.classList.toggle("hidden", !isRuntime);
  els.tracefixRunFields.classList.toggle("hidden", !(isRuntime || isDesignRun));
  els.startRun.textContent =
    state.runMode === "design"
      ? "Generate Verified Plan"
      : state.runMode === "design_run"
        ? "Design + Run"
      : state.runMode === "plan"
        ? "Export Intermediary Plan"
        : state.runMode === "runtime"
          ? "Run Legacy Debug"
          : "Generate Verified Plan";
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
    live: false,
    legacyDebugView: false,
  };

  try {
    const run = await postJson("/api/runs", payload);
    state.runId = run.id;
    if (run.artifacts?.workspace) {
      els.workspacePath.textContent = run.artifacts.workspace;
      state.artifacts = run.artifacts;
      renderArtifacts();
    }
    if (run.usage) {
      state.usage = run.usage;
      renderUsage();
    }
    els.startRun.disabled = true;
    els.stopRun.disabled = false;
    if (state.runMode === "runtime") {
      els.runTitle.textContent = `Legacy debug: ${payload.workspacePath || "workspace"}`;
      els.runMeta.textContent = `${payload.harness} / ${payload.model}`;
    } else if (state.runMode === "plan") {
      els.runTitle.textContent = `Exporting intermediary plan`;
      els.runMeta.textContent = payload.workspacePath || "workspace required";
    } else if (state.runMode === "design") {
      const action = "Designing";
      els.runTitle.textContent =
        state.taskMode === "benchmark" ? `${action} ${payload.taskId}` : `${action} custom task`;
      els.runMeta.textContent = `${payload.provider} / ${payload.model}`;
    } else if (state.runMode === "design_run") {
      const action = "Designing + running";
      els.runTitle.textContent =
        state.taskMode === "benchmark" ? `${action} ${payload.taskId}` : `${action} custom task`;
      els.runMeta.textContent = `${payload.provider} / ${payload.model}`;
    } else {
      els.runTitle.textContent =
        state.taskMode === "benchmark" ? `Planning ${payload.taskId}` : "Planning custom task";
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
  state.usage = null;
  state.usageDetailsOpen = false;
  els.timeline.innerHTML = "";
  els.outputView.textContent = "";
  els.workspacePath.textContent = "Waiting for intermediary plan";
  if (els.turnCount) els.turnCount.textContent = "0";
  if (els.toolCount) els.toolCount.textContent = "0";
  els.artifactCount.textContent = "0";
  els.llmUsageDetails.classList.add("hidden");
  els.llmUsageCard.setAttribute("aria-expanded", "false");
  els.graph.innerHTML = "";
  els.graphStatus.textContent = "Waiting for IR";
  renderUsage();
}

function connectEvents(runId) {
  const source = new EventSource(`/api/runs/${runId}/events`);
  state.eventSource = source;
  source.addEventListener("tracefix", async (message) => {
    const event = JSON.parse(message.data);
    handleRunEvent(event);
    if (event.type === "status" && ["completed", "failed", "verification_incomplete"].includes(event.status)) {
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
      if (els.turnCount) els.turnCount.textContent = String(state.turns);
    }
    if (event.type === "tool") {
      state.tools += 1;
      if (els.toolCount) els.toolCount.textContent = String(state.tools);
    }
  }

  if (event.workspace) {
    els.workspacePath.textContent = event.workspace;
  }

  if (event.type === "artifacts" && event.artifacts) {
    state.artifacts = event.artifacts;
    renderArtifacts();
  }

  if (event.type === "usage" && event.usage) {
    state.usage = event.usage;
    renderUsage();
  }

  if (event.type === "status") {
    setStatus(event.status);
  }
  if (event.type === "incomplete") {
    setStatus("verification_incomplete");
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
  const label = status === "verification_incomplete" ? "Verification incomplete" : status;
  const normalized =
    status === "starting" || status === "stopping"
      ? "running"
      : status === "verification_incomplete"
        ? "incomplete"
        : status;
  els.statusBadge.textContent = label;
  els.statusBadge.className = `status-badge ${normalized || "idle"}`;
  if (status === "verification_incomplete") {
    setActiveView("error");
  }
}

async function refreshRun() {
  if (!state.runId) return;
  const run = await getJson(`/api/runs/${state.runId}`);
  state.artifacts = run.artifacts || {};
  state.usage = run.usage || state.usage;
  if (run.artifacts?.workspace) {
    els.workspacePath.textContent = run.artifacts.workspace;
  }
  renderArtifacts();
  renderUsage();
  renderOutput();
}

function renderArtifacts() {
  const files = state.artifacts.files || [];
  els.artifactCount.textContent = String(files.length);
  renderGraph(state.artifacts.ir);
}

function renderUsage() {
  const usage = state.usage || {};
  const totalTokens = Number(usage.total_tokens || 0);
  const cost = Number(usage.estimated_cost_usd || 0);
  const estimated = usage.estimated !== false;
  const source = usage.source || "no_usage_metadata";
  els.llmCost.textContent = `${estimated ? "~" : ""}${formatMoney(cost)}`;
  els.llmTokenSummary.textContent = `${formatCompactTokens(totalTokens)} tokens`;
  els.llmModelName.textContent = usage.model || "No model";
  els.llmUsageSource.textContent = sourceLabel(source, estimated, usage.cost_known);

  els.usageInputTokens.textContent = formatNumber(usage.input_tokens || 0);
  els.usageOutputTokens.textContent = formatNumber(usage.output_tokens || 0);
  els.usageTotalTokens.textContent = formatNumber(totalTokens);
  els.usageCost.textContent = `${estimated ? "~" : ""}${formatMoney(cost)}`;

  const phases = usage.phases || {};
  renderPhaseUsage("Design", phases.design, els.usageDesignTokens, els.usageDesignCost);
  renderPhaseUsage("Repair", phases.repair, els.usageRepairTokens, els.usageRepairCost);
  renderPhaseUsage("Verification", phases.verification, els.usageVerificationTokens, els.usageVerificationCost);

  const totals = usage.session_totals || {};
  els.usageTotalRuns.textContent = formatNumber(totals.total_runs || 0);
  els.usageSessionTokens.textContent = formatNumber(totals.total_tokens || 0);
  els.usageSessionCost.textContent = formatMoney(totals.total_cost_usd || 0);
}

function renderPhaseUsage(_label, phase, tokenEl, costEl) {
  const data = phase || {};
  tokenEl.textContent = formatNumber(data.total_tokens || 0);
  costEl.textContent = `${data.cost_known === false ? "~" : ""}${formatMoney(data.estimated_cost_usd || 0)}`;
}

function renderOutput() {
  let content = "";
  if (state.activeView === "log") {
    content = state.logs.join("\n");
  } else if (state.activeView === "ir") {
    content = state.artifacts.ir ? JSON.stringify(state.artifacts.ir, null, 2) : "No ir.json yet.";
  } else if (state.activeView === "plan") {
    content = renderPlanSummary(state.artifacts.cityosPlan, state.artifacts.cityosPlanPath);
  } else if (state.activeView === "protocol") {
    content = state.artifacts.protocol || "No Protocol.tla yet.";
  } else if (state.activeView === "states") {
    content = state.artifacts.states ? JSON.stringify(state.artifacts.states, null, 2) : "No states.json yet.";
  } else if (state.activeView === "error") {
    content = renderTlcError();
  }
  els.outputView.textContent = content;
  if (state.activeView === "log") {
    els.outputView.scrollTop = els.outputView.scrollHeight;
  }
}

async function openArtifact(target, successText) {
  const workspacePath = currentWorkspacePath();
  if (!workspacePath) {
    showToast("No workspace selected");
    return;
  }
  try {
    await postJson("/api/open-artifact", { workspacePath, target });
    showToast(successText);
  } catch (error) {
    showToast(error.message);
  }
}

function currentWorkspacePath() {
  const workspacePath = state.artifacts.workspace || els.workspacePath.textContent.trim();
  return workspacePath.startsWith("Waiting for") ? "" : workspacePath;
}

function setActiveView(view) {
  state.activeView = view;
  document.querySelectorAll("[data-view]").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === view);
  });
  renderOutput();
}

function renderTlcError() {
  const lines = [];
  if (state.artifacts.tlcError) {
    lines.push(state.artifacts.tlcError);
  } else {
    lines.push("No tlc_error.md found. Verification may have failed before TLC produced an error file.");
  }
  const recovery = state.artifacts.recovery;
  if (recovery) {
    lines.push("");
    lines.push("Recovery Guidance");
    lines.push(`Workspace: ${recovery.workspace}`);
    lines.push(`TLC error: ${recovery.tlcErrorPath}`);
    lines.push(`IR: ${recovery.irPath}`);
    lines.push(`Protocol: ${recovery.protocolPath}`);
    lines.push("");
    lines.push("Suggested rerun:");
    lines.push(recovery.rerunDesignCommand);
    lines.push("");
    lines.push("Manual checks:");
    (recovery.manualCommands || []).forEach((command) => lines.push(command));
    if (recovery.notes?.length) {
      lines.push("");
      lines.push("Notes:");
      recovery.notes.forEach((note) => lines.push(`- ${note}`));
    }
  }
  return lines.join("\n");
}

function renderPlanSummary(plan, planPath) {
  if (!plan) {
    return "No spec/cityos_module_plan.json yet. Use Design to generate a workspace, or choose Intermediary Plan with an existing workspace.";
  }
  const lines = [];
  lines.push(`Artifact: ${plan.artifact_type || "unknown"}`);
  lines.push(`Location: ${planPath || "spec/cityos_module_plan.json"}`);
  lines.push(`Verification: ${plan.verification?.status || plan.tracefix?.verification_status || "unknown"}`);
  lines.push(`Production ready: ${plan.verification?.production_ready === true ? "yes" : "no"}`);
  lines.push(`Export status: ${planPath ? "cityos_module_plan.json available" : "not exported"}`);
  lines.push("");
  lines.push("Application Goals");
  const goals = plan.goals || plan.application?.goals || [];
  if (!goals.length) lines.push("- No explicit goals inferred yet.");
  goals.forEach((goal) => {
    lines.push(`- ${goal.id || "goal"}: ${goal.description || ""}`);
  });
  lines.push("");
  lines.push("Generated Agents");
  const agents = plan.agents || [];
  if (!agents.length) lines.push("- No agents inferred yet.");
  agents.forEach((agent) => {
    lines.push(`- ${agent.name || agent.id}: ${agent.role || ""}`);
    if (agent.prompt_path) lines.push(`  prompt: ${agent.prompt_path}`);
    if (agent.inputs?.length) lines.push(`  inputs: ${agent.inputs.join(", ")}`);
    if (agent.outputs?.length) lines.push(`  outputs: ${agent.outputs.join(", ")}`);
  });
  lines.push("");
  lines.push("Protocol / Topology");
  const topology = plan.topology || plan.protocol?.topology || {};
  lines.push(`- agents: ${(topology.agents || []).length}`);
  lines.push(`- resources: ${(topology.resources || []).length}`);
  lines.push(`- channels: ${(topology.channels || []).length}`);
  lines.push("");
  lines.push("Communication Requirements");
  const edges = plan.communication_requirements || plan.protocol?.allowed_communication_edges || [];
  if (!edges.length) lines.push("- No explicit communication edges inferred yet.");
  edges.forEach((edge) => {
    lines.push(`- ${edge.from || "?"} -> ${edge.to || "?"} via ${edge.channel || "channel"} [${(edge.labels || []).join(", ")}]`);
  });
  lines.push("");
  lines.push("Runtime Monitor Requirements");
  lines.push(`- required: ${plan.runtime_monitor?.required === false ? "false" : "true"}`);
  const monitorRules = plan.runtime_monitor?.monitor_rules || [];
  if (!monitorRules.length) lines.push("- Monitor rules will be derived from verified protocol artifacts.");
  monitorRules.forEach((rule) => lines.push(`- ${rule}`));
  lines.push("");
  lines.push("Resource Requirements");
  const resources = plan.resource_requirements || [];
  if (!resources.length) lines.push("- No external resources declared.");
  resources.forEach((resource) => {
    lines.push(`- ${resource.id || resource.name || JSON.stringify(resource)} ${resource.type ? `(${resource.type})` : ""}`);
  });
  lines.push("");
  lines.push("Source Artifacts");
  Object.entries(plan.source_artifacts || {}).forEach(([key, value]) => {
    lines.push(`- ${key}: ${value}`);
  });
  return lines.join("\n");
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

function formatNumber(value) {
  return Number(value || 0).toLocaleString();
}

function formatCompactTokens(value) {
  const tokens = Number(value || 0);
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(tokens >= 10_000_000 ? 0 : 1)}M`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(tokens >= 100_000 ? 0 : 1)}K`;
  return String(tokens);
}

function formatMoney(value) {
  return `$${Number(value || 0).toFixed(2)}`;
}

function sourceLabel(source, estimated, costKnown) {
  if (source === "session_json") return estimated ? "Session metadata, estimated cost" : "Session metadata";
  if (source === "json_usage_event") return estimated ? "API usage metadata, estimated cost" : "API usage metadata";
  if (source?.startsWith("stdout")) return "Parsed from run output, estimated";
  if (costKnown === false) return "No pricing for this model yet";
  return estimated ? "Waiting for usage metadata" : "Usage metadata";
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
  updateModelSuggestions();
  updateModeFields();
  await loadTasks();
  renderUsage();
  renderOutput();
}

init().catch((error) => {
  appendTimeline("error", error.message);
});
