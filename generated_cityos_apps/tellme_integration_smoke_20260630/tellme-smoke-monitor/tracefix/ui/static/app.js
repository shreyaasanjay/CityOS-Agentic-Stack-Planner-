const state = {
  tasks: [],
  activeTaskId: null,
  activeTask: null,
  difficulty: "All",
  query: "",
  commandTab: "pipeline",
  artifactTab: "ir",
};

const els = {
  repoLabel: document.querySelector("#repoLabel"),
  taskSearch: document.querySelector("#taskSearch"),
  taskList: document.querySelector("#taskList"),
  taskMeta: document.querySelector("#taskMeta"),
  taskTitle: document.querySelector("#taskTitle"),
  artifactPills: document.querySelector("#artifactPills"),
  agentCount: document.querySelector("#agentCount"),
  resourceCount: document.querySelector("#resourceCount"),
  toolCount: document.querySelector("#toolCount"),
  goalText: document.querySelector("#goalText"),
  topologySource: document.querySelector("#topologySource"),
  graph: document.querySelector("#topologyGraph"),
  toolList: document.querySelector("#toolList"),
  toolSummary: document.querySelector("#toolSummary"),
  runtimeStatus: document.querySelector("#runtimeStatus"),
  commandText: document.querySelector("#commandText"),
  copyCommand: document.querySelector("#copyCommand"),
  artifactViewer: document.querySelector("#artifactViewer"),
  toast: document.querySelector("#toast"),
};

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
}

function showToast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => els.toast.classList.remove("visible"), 1800);
}

function truncate(text, length) {
  if (!text || text.length <= length) return text || "";
  return `${text.slice(0, length - 1)}...`;
}

function filteredTasks() {
  const query = state.query.trim().toLowerCase();
  return state.tasks.filter((task) => {
    const difficultyOk = state.difficulty === "All" || task.difficulty === state.difficulty;
    const queryOk =
      !query ||
      task.id.toLowerCase().includes(query) ||
      task.title.toLowerCase().includes(query) ||
      (task.goal || "").toLowerCase().includes(query);
    return difficultyOk && queryOk;
  });
}

function artifactCount(artifacts) {
  return Object.values(artifacts || {}).filter(Boolean).length;
}

function renderTaskList() {
  const tasks = filteredTasks();
  els.taskList.innerHTML = "";

  if (tasks.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No matching tasks";
    els.taskList.appendChild(empty);
    return;
  }

  for (const task of tasks) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `task-card${task.id === state.activeTaskId ? " active" : ""}`;
    button.innerHTML = `
      <strong>${task.id} - ${escapeHtml(task.title.replace(/^Task\s+\w+:\s*/i, ""))}</strong>
      <span>${escapeHtml(task.difficulty)} / ${task.agents.length} agents / ${task.resources.length} resources</span>
      <div class="mini-pills">
        <i class="mini-pill">${task.toolCount} tools</i>
        <i class="mini-pill">${artifactCount(task.artifacts)}/3 artifacts</i>
      </div>
    `;
    button.addEventListener("click", () => loadTask(task.id));
    els.taskList.appendChild(button);
  }
}

function renderArtifactPills(status) {
  const labels = [
    ["ir", "IR"],
    ["protocol", "Protocol"],
    ["states", "States"],
  ];
  els.artifactPills.innerHTML = labels
    .map(([key, label]) => {
      const available = Boolean(status?.[key]);
      return `<span class="artifact-pill ${available ? "available" : "missing"}">${label} ${available ? "ready" : "missing"}</span>`;
    })
    .join("");
}

function renderTask(task) {
  state.activeTask = task;
  state.activeTaskId = task.id;

  const agents = asArray(task.topology.agents);
  const resources = asArray(task.topology.resources);
  els.taskMeta.textContent = `Task ${task.id} / ${task.difficulty} / Scenario ${task.scenario}`;
  els.taskTitle.textContent = task.title;
  els.agentCount.textContent = agents.length;
  els.resourceCount.textContent = resources.length;
  els.toolCount.textContent = asArray(task.tools).length;
  els.goalText.textContent = taskSummaryGoal(task);
  els.topologySource.textContent =
    task.topology.source === "fixture" ? "Rendered from fixture IR" : "Rendered from benchmark metadata";
  renderArtifactPills(task.artifacts.status);
  renderTaskList();
  renderGraph(task.topology);
  renderTools(task.tools);
  renderCommand();
  renderArtifact();
}

function taskSummaryGoal(task) {
  const summary = state.tasks.find((item) => item.id === task.id);
  return summary?.goal || "No explicit goal section found.";
}

function renderTools(tools) {
  const rows = asArray(tools);
  els.toolSummary.textContent = `${rows.length} callable tools`;
  els.toolList.innerHTML = "";

  if (rows.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No tools found";
    els.toolList.appendChild(empty);
    return;
  }

  for (const tool of rows) {
    const item = document.createElement("article");
    item.className = "tool-item";
    const agents = asArray(tool.agents);
    const resources = asArray(tool.resources);
    item.innerHTML = `
      <header>
        <strong>${escapeHtml(tool.name)}</strong>
        ${tool.can_fail ? '<span class="tool-chip fail">can fail</span>' : ""}
      </header>
      <p>${escapeHtml(tool.description || "No description")}</p>
      <div class="tool-meta">
        ${agents.map((agent) => `<span class="tool-chip">${escapeHtml(agent)}</span>`).join("")}
        ${resources.map((resource) => `<span class="tool-chip resource">${escapeHtml(resource)}</span>`).join("")}
      </div>
    `;
    els.toolList.appendChild(item);
  }
}

function renderCommand() {
  if (!state.activeTask) return;
  els.commandText.textContent = state.activeTask.commands[state.commandTab] || "";
}

function renderArtifact() {
  const task = state.activeTask;
  if (!task) return;

  const artifacts = task.artifacts || {};
  let content = "";

  if (state.artifactTab === "description") {
    content = task.description || "No task description found.";
  } else if (state.artifactTab === "protocol") {
    content = artifacts.protocol || "No Protocol.tla fixture found for this task.";
  } else if (state.artifactTab === "states") {
    content = artifacts.states ? JSON.stringify(artifacts.states, null, 2) : "No states.json fixture found for this task.";
  } else {
    content = artifacts.ir ? JSON.stringify(artifacts.ir, null, 2) : JSON.stringify(task.topology, null, 2);
  }

  els.artifactViewer.textContent = content;
}

function renderGraph(topology) {
  const svg = els.graph;
  const width = Math.max(svg.clientWidth || 760, 320);
  const height = Math.max(svg.clientHeight || 520, 320);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = "";

  const agents = asArray(topology.agents);
  const resources = asArray(topology.resources);
  const channels = asArray(topology.channels);
  const resourceLinks = asArray(topology.resourceLinks);

  const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
  defs.innerHTML = `
    <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#4f5f99"></path>
    </marker>
  `;
  svg.appendChild(defs);

  const center = { x: width / 2, y: height / 2 };
  const radiusX = Math.max(120, width * 0.34);
  const radiusY = Math.max(95, height * 0.3);
  const positions = new Map();

  agents.forEach((agent, index) => {
    const angle = -Math.PI / 2 + (index / Math.max(agents.length, 1)) * Math.PI * 2;
    positions.set(agent.id, {
      x: center.x + Math.cos(angle) * radiusX,
      y: center.y + Math.sin(angle) * radiusY,
      type: "agent",
    });
  });

  resources.forEach((resource, index) => {
    const offset = (index - (resources.length - 1) / 2) * 88;
    positions.set(resource.id, {
      x: center.x + offset,
      y: center.y,
      type: "resource",
    });
  });

  const fallbackEdges = resources.flatMap((resource) =>
    agents.map((agent) => ({ source: agent.id, target: resource.id }))
  );
  const resourceEdges =
    resourceLinks.length > 0
      ? resourceLinks.flatMap((link) =>
          agents.map((agent) => ({ source: agent.id, target: link.resource, label: link.tool }))
        )
      : fallbackEdges;

  for (const edge of resourceEdges) {
    drawLine(svg, positions.get(edge.source), positions.get(edge.target), "resource-path", false);
  }

  channels.forEach((channel, index) => {
    const froms = asArray(channel.from);
    const tos = asArray(channel.to);
    for (const from of froms) {
      for (const to of tos) {
        if (from !== to) {
          drawCurve(svg, positions.get(from), positions.get(to), index);
        }
      }
    }
  });

  resources.forEach((resource) => {
    const pos = positions.get(resource.id);
    if (pos) drawResource(svg, pos, resource);
  });

  agents.forEach((agent) => {
    const pos = positions.get(agent.id);
    if (pos) drawAgent(svg, pos, agent);
  });

  if (agents.length === 0 && resources.length === 0) {
    const text = node("text", { x: center.x, y: center.y, class: "node-label" });
    text.textContent = "No topology data";
    svg.appendChild(text);
  }
}

function drawLine(svg, source, target, className, arrow) {
  if (!source || !target) return;
  const line = node("path", {
    d: `M ${source.x} ${source.y} L ${target.x} ${target.y}`,
    class: className,
    "marker-end": arrow ? "url(#arrow)" : "",
  });
  svg.appendChild(line);
}

function drawCurve(svg, source, target, index) {
  if (!source || !target) return;
  const mx = (source.x + target.x) / 2;
  const my = (source.y + target.y) / 2;
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const len = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
  const bend = ((index % 5) - 2) * 18;
  const cx = mx + (-dy / len) * bend;
  const cy = my + (dx / len) * bend;
  const path = node("path", {
    d: `M ${source.x} ${source.y} Q ${cx} ${cy} ${target.x} ${target.y}`,
    class: "channel-path",
    "marker-end": "url(#arrow)",
  });
  svg.appendChild(path);
}

function drawAgent(svg, pos, agent) {
  svg.appendChild(node("circle", { cx: pos.x, cy: pos.y, r: 28, fill: "#d7e8df", stroke: "#2f6f73", "stroke-width": 2 }));
  const icon = node("text", { x: pos.x, y: pos.y + 5, class: "node-label" });
  icon.textContent = initials(agent.id);
  svg.appendChild(icon);

  const label = node("text", { x: pos.x, y: pos.y + 45, class: "node-label" });
  label.textContent = compactLabel(agent.id);
  svg.appendChild(label);

  const sub = node("text", { x: pos.x, y: pos.y + 60, class: "node-sub" });
  sub.textContent = "agent";
  svg.appendChild(sub);
}

function drawResource(svg, pos, resource) {
  svg.appendChild(node("rect", { x: pos.x - 26, y: pos.y - 22, width: 52, height: 44, rx: 7, fill: "#f3d9d4", stroke: "#a64234", "stroke-width": 2 }));
  const label = node("text", { x: pos.x, y: pos.y + 3, class: "node-label" });
  label.textContent = compactLabel(resource.id);
  svg.appendChild(label);

  const sub = node("text", { x: pos.x, y: pos.y + 39, class: "node-sub" });
  sub.textContent = resource.type || "Lock";
  svg.appendChild(sub);
}

function node(tag, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== "") el.setAttribute(key, value);
  }
  return el;
}

function initials(id) {
  const parts = String(id).split(/[_\-\s]+/).filter(Boolean);
  return parts
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

function compactLabel(id) {
  return truncate(String(id).replaceAll("_", " "), 18);
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function loadTask(taskId) {
  const task = await getJson(`/api/task/${taskId}`);
  renderTask(task);
}

function bindEvents() {
  els.taskSearch.addEventListener("input", (event) => {
    state.query = event.target.value;
    renderTaskList();
  });

  document.querySelectorAll("[data-difficulty]").forEach((button) => {
    button.addEventListener("click", () => {
      state.difficulty = button.dataset.difficulty;
      document
        .querySelectorAll("[data-difficulty]")
        .forEach((item) => item.classList.toggle("active", item === button));
      renderTaskList();
    });
  });

  document.querySelectorAll("[data-command]").forEach((button) => {
    button.addEventListener("click", () => {
      state.commandTab = button.dataset.command;
      document
        .querySelectorAll("[data-command]")
        .forEach((item) => item.classList.toggle("active", item === button));
      renderCommand();
    });
  });

  document.querySelectorAll("[data-artifact]").forEach((button) => {
    button.addEventListener("click", () => {
      state.artifactTab = button.dataset.artifact;
      document
        .querySelectorAll("[data-artifact]")
        .forEach((item) => item.classList.toggle("active", item === button));
      renderArtifact();
    });
  });

  els.copyCommand.addEventListener("click", async () => {
    const command = els.commandText.textContent.trim();
    if (!command) return;
    try {
      await navigator.clipboard.writeText(command);
      showToast("Command copied");
    } catch {
      showToast("Copy unavailable");
    }
  });

  window.addEventListener("resize", () => {
    if (state.activeTask) renderGraph(state.activeTask.topology);
  });
}

async function init() {
  bindEvents();
  const [summary, status] = await Promise.all([getJson("/api/summary"), getJson("/api/status")]);
  state.tasks = summary.tasks || [];
  els.repoLabel.textContent = status.repo || "TraceFix repo";
  els.runtimeStatus.textContent = status.java ? "Java detected" : "Java not on PATH";
  renderTaskList();
  const preferred = state.tasks.find((task) => task.id === "3E") || state.tasks[0];
  if (preferred) {
    await loadTask(preferred.id);
  }
}

init().catch((error) => {
  els.taskTitle.textContent = "Unable to load TraceFix Studio";
  els.goalText.textContent = error.message;
  console.error(error);
});

