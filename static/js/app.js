/**
 * Agent Task Manager — Frontend Application
 * Handles: API calls, WebSocket log streaming, UI interactions,
 * project switching, workflow visualization, and orchestrator control.
 */

// ============================================================
//  API Client
// ============================================================
const API = {
  base: '',

  async get(path) {
    const res = await fetch(this.base + path);
    if (!res.ok) throw new Error(`API ${path}: ${res.status}`);
    return res.json();
  },

  async post(path, body = {}) {
    const res = await fetch(this.base + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`API POST ${path}: ${res.status}`);
    return res.json();
  },

  async patch(path, body = {}) {
    const res = await fetch(this.base + path, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`API PATCH ${path}: ${res.status}`);
    return res.json();
  },

  async del(path) {
    const res = await fetch(this.base + path, { method: 'DELETE' });
    if (!res.ok) throw new Error(`API DELETE ${path}: ${res.status}`);
    return res.json();
  },
};

// ============================================================
//  State
// ============================================================
const State = {
  projects: [],
  activeProjectId: null,
  activeRoadmap: null,
  activeResults: null,
  orchestratorRunning: false,
  logLines: [],
  wsConnected: false,
  ws: null,
  pollInterval: null,
};

// ============================================================
//  Toast Notifications
// ============================================================
function showToast(message, type = 'info', duration = 3000) {
  const icons = { success: '✅', error: '❌', info: 'ℹ️', warning: '⚠️' };
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span>${icons[type] || 'ℹ️'}</span><span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.animation = 'slideOut 0.2s ease forwards';
    setTimeout(() => toast.remove(), 200);
  }, duration);
}

// ============================================================
//  WebSocket — Live Log Streaming
// ============================================================
function connectWebSocket() {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  const wsUrl = `${protocol}://${location.host}/ws/logs`;

  try {
    State.ws = new WebSocket(wsUrl);

    State.ws.onopen = () => {
      State.wsConnected = true;
      updateWsStatus(true);
      document.getElementById('logContainer').innerHTML = '';
      // Keep-alive ping every 20s
      State._wsPingInterval = setInterval(() => {
        if (State.ws?.readyState === WebSocket.OPEN) State.ws.send('ping');
      }, 20000);
    };

    State.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'log') appendLogLine(data.line, data.timestamp);
    };

    State.ws.onclose = () => {
      State.wsConnected = false;
      updateWsStatus(false);
      clearInterval(State._wsPingInterval);
      // Reconnect after 3 seconds
      setTimeout(connectWebSocket, 3000);
    };

    State.ws.onerror = () => {
      State.ws.close();
    };
  } catch (e) {
    setTimeout(connectWebSocket, 3000);
  }
}

function updateWsStatus(connected) {
  const el = document.getElementById('wsStatus');
  el.textContent = connected ? '🟢 已連接' : '🔴 重連中...';
  el.style.color = connected ? 'var(--green)' : 'var(--red)';
}

// ============================================================
//  Log Panel
// ============================================================
const LOG_LEVEL_COLORS = {
  INFO: 'info', WARNING: 'warning', WARN: 'warning',
  ERROR: 'error', DEBUG: 'debug', CRITICAL: 'error',
};

function parseLogLine(raw) {
  // Format: "2026-04-16 04:01:23,456 [INFO] logger: message"
  const m = raw.match(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[,\d]*)\s+\[(\w+)\]\s+[\w.]+:\s+(.+)$/);
  if (m) return { time: m[1].slice(11, 19), level: m[2].toUpperCase(), msg: m[3] };
  return { time: '', level: '', msg: raw };
}

function appendLogLine(raw, timestamp = '') {
  if (!raw || !raw.trim()) return;

  const container = document.getElementById('logContainer');
  const autoScroll = document.getElementById('autoScrollToggle')?.checked;
  const parsed = parseLogLine(raw);

  const line = document.createElement('div');
  line.className = 'log-line';
  const levelClass = LOG_LEVEL_COLORS[parsed.level] || '';
  const timeStr = parsed.time || timestamp;

  line.innerHTML = `
    <span class="log-time">${timeStr}</span>
    <span class="log-level ${levelClass}">${parsed.level || 'LOG'}</span>
    <span class="log-msg">${escapeHtml(parsed.msg)}</span>
  `;
  container.appendChild(line);
  State.logLines.push(raw);

  // Cap at 500 lines
  if (State.logLines.length > 500) {
    container.removeChild(container.firstChild);
    State.logLines.shift();
  }

  document.getElementById('logCount').textContent = `${State.logLines.length} 行`;

  if (autoScroll) container.scrollTop = container.scrollHeight;
}

function escapeHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ============================================================
//  Projects Panel
// ============================================================
async function loadProjects() {
  try {
    const data = await API.get('/api/projects');
    State.projects = data.projects;
    State.activeProjectId = data.active_project_id;
    renderProjectList();

    // Auto-load active project
    if (State.activeProjectId) {
      await loadProjectDetails(State.activeProjectId);
    }
  } catch (e) {
    showToast('無法載入專案列表: ' + e.message, 'error');
  }
}

function renderProjectList() {
  const list = document.getElementById('projectList');
  document.getElementById('projectCount').textContent = State.projects.length;

  if (!State.projects.length) {
    list.innerHTML = `<div style="text-align:center;color:var(--text-muted);padding:20px;font-size:12px;">
      尚未設定專案<br><br>點擊右上角「＋」新增
    </div>`;
    return;
  }

  list.innerHTML = '';
  State.projects.forEach(p => {
    const done = p.roadmap?.tasks_done ?? 0;
    const total = p.roadmap?.tasks_total ?? 0;
    const percent = total > 0 ? Math.round((done / total) * 100) : 0;
    const sysStatus = p.roadmap?.sys_status ?? 'RUNNING';
    const statusIcon = { RUNNING: '▶', PAUSED: '⏸', SLEEP_RATE_LIMIT: '💤' }[sysStatus] || '?';

    const item = document.createElement('div');
    item.className = `project-item ${p.is_active ? 'active' : ''}`;
    item.dataset.projectId = p.id;
    item.innerHTML = `
      <div class="project-name">${escapeHtml(p.name)}</div>
      <div class="project-meta">
        <span>${statusIcon} ${sysStatus}</span>
        <span>⎇ ${escapeHtml(p.git?.branch ?? p.roadmap?.current_branch ?? 'main')}</span>
        ${done}/${total}
      </div>
      <div class="project-progress">
        <div class="project-progress-fill" style="width:${percent}%"></div>
      </div>
    `;
    item.addEventListener('click', () => activateProject(p.id));
    list.appendChild(item);
  });
}

async function activateProject(projectId) {
  if (projectId === State.activeProjectId) return;
  try {
    await API.post(`/api/project/${projectId}/activate`);
    State.activeProjectId = projectId;
    await loadProjects();
    await loadProjectDetails(projectId);
    showToast('已切換專案', 'success', 1500);
  } catch (e) {
    showToast('切換專案失敗: ' + e.message, 'error');
  }
}

async function loadProjectDetails(projectId) {
  await Promise.all([
    loadRoadmap(projectId),
    loadResults(projectId),
  ]);
  await loadOrchestratorStatus();
}

// ============================================================
//  Workflow Designer (Roadmap Visualization)
// ============================================================
async function loadRoadmap(projectId) {
  try {
    const roadmap = await API.get(`/api/project/${projectId}/roadmap`);
    State.activeRoadmap = roadmap;
    renderWorkflow(roadmap);
    updateSysStatusBar(roadmap);
  } catch (e) {
    document.getElementById('workflowEmpty').style.display = 'flex';
    document.getElementById('taskFlow').style.display = 'none';
    document.getElementById('workflowEmpty').querySelector('p').textContent =
      'roadmap.md 不存在或解析失敗: ' + e.message;
  }
}

function renderWorkflow(roadmap) {
  const taskFlow = document.getElementById('taskFlow');
  const empty = document.getElementById('workflowEmpty');

  if (!roadmap.tasks || !roadmap.tasks.length) {
    empty.style.display = 'flex';
    taskFlow.style.display = 'none';
    return;
  }

  empty.style.display = 'none';
  taskFlow.style.display = 'flex';
  taskFlow.innerHTML = '';

  const statusIcon = { done: '✅', current: '▶', pending: '○' };

  roadmap.tasks.forEach((task, idx) => {
    // Task Node
    const node = document.createElement('div');
    node.className = 'task-node';

    const statusClass = task.status === 'current' ? 'status-current'
                      : task.status === 'done'    ? 'status-done'
                      : 'status-pending';

    const card = document.createElement('div');
    card.className = `task-card ${statusClass}`;
    card.dataset.taskName = task.name;
    card.innerHTML = `
      <div class="task-node-icon">${statusIcon[task.status] || '○'}</div>
      <div class="task-node-badge">${escapeHtml(task.name)}</div>
      <div class="task-node-title" title="${escapeHtml(task.title)}">${escapeHtml(task.title)}</div>
      ${task.verification_cmd ? `<div class="task-node-cmd">$ ${escapeHtml(task.verification_cmd)}</div>` : ''}
    `;
    card.addEventListener('click', () => showTaskDetail(task));
    node.appendChild(card);

    // Branching rules indicator
    if (task.branching_rules?.length) {
      task.branching_rules.forEach(rule => {
        const branchEl = document.createElement('div');
        branchEl.className = 'branch-connector';
        branchEl.innerHTML = `
          <div style="width:1px;height:12px;background:var(--yellow);opacity:0.5;"></div>
          <div class="branch-label">IF ${escapeHtml(rule.condition)} → ${escapeHtml(rule.target_task)}</div>
        `;
        node.appendChild(branchEl);
      });
    }

    taskFlow.appendChild(node);

    // Connector (not after last item)
    if (idx < roadmap.tasks.length - 1) {
      const connector = document.createElement('div');
      connector.className = 'task-connector';
      connector.innerHTML = `<div class="connector-line"></div><div class="connector-arrow">▶</div>`;
      taskFlow.appendChild(connector);
    }
  });

  // Update current task in Agent Console
  const current = roadmap.tasks.find(t => t.is_current || t.status === 'current');
  if (current) updateCurrentTaskCard(current);
}

function updateSysStatusBar(roadmap) {
  const statusMap = {
    RUNNING: { label: 'RUNNING', cls: 'running' },
    PAUSED:  { label: 'PAUSED',  cls: 'paused' },
    SLEEP_RATE_LIMIT: { label: 'SLEEPING', cls: 'sleep' },
  };
  const info = statusMap[roadmap.sys_status] || { label: roadmap.sys_status, cls: '' };

  const chip = document.getElementById('statusChip');
  chip.className = `status-chip ${info.cls}`;
  chip.querySelector('.chip-label').textContent = info.label;

  const done = roadmap.tasks?.filter(t => t.status === 'done').length ?? 0;
  const total = roadmap.tasks?.length ?? 0;
  document.getElementById('taskProgress').textContent = `${done} / ${total} 任務完成`;
  document.getElementById('branchName').textContent = roadmap.current_branch || 'main';

  const tokenRatio = roadmap.token_limit > 0 ? (roadmap.context_tokens / roadmap.token_limit) * 100 : 0;
  document.getElementById('tokenFill').style.width = `${Math.min(tokenRatio, 100)}%`;
  document.getElementById('tokenLabel').textContent =
    `${(roadmap.context_tokens / 1000).toFixed(1)}k / ${(roadmap.token_limit / 1000).toFixed(0)}k tokens`;
}

function updateCurrentTaskCard(task) {
  document.getElementById('currentTaskBadge').textContent = task.name;
  document.getElementById('currentTaskTitle').textContent = task.title;
  document.getElementById('taskPromptPreview').textContent =
    task.instructions || '（無指令內容）';
  document.getElementById('cliCommand').textContent =
    State.projects.find(p => p.id === State.activeProjectId)?.cli_command || 'claude';
  document.getElementById('guiTarget').textContent =
    State.projects.find(p => p.id === State.activeProjectId)?.gui_target || 'antigravity';
}

function showTaskDetail(task) {
  // Populate task info into current task card (and optional modal in future)
  updateCurrentTaskCard(task);
  document.getElementById('promptInput').value = task.instructions || '';
  showToast(`已選取任務: ${task.name}`, 'info', 1500);
}

// ============================================================
//  Results Panel
// ============================================================
async function loadResults(projectId) {
  try {
    const data = await API.get(`/api/project/${projectId}/results`);
    State.activeResults = data;
    renderResults(data);
  } catch (e) {
    // Silent fail — results_log.md may not exist yet
  }
}

function renderResults(data) {
  if (!data.exists) {
    document.getElementById('resultsList').innerHTML =
      '<div class="results-empty">results_log.md 尚未建立<br>任務完成後自動生成</div>';
    document.getElementById('resultsCompleted').textContent = '-';
    document.getElementById('resultsTotal').textContent = '-';
    document.getElementById('resultsLastUpdate').textContent = '-';
    return;
  }

  document.getElementById('resultsCompleted').textContent = data.completed_tasks ?? 0;
  document.getElementById('resultsTotal').textContent = data.total_tasks ?? 0;
  const lastUp = data.last_updated ? data.last_updated.slice(11, 16) : '-';
  document.getElementById('resultsLastUpdate').textContent = lastUp;

  const list = document.getElementById('resultsList');
  if (!data.tasks?.length) {
    list.innerHTML = '<div class="results-empty">尚無任務記錄</div>';
    return;
  }

  list.innerHTML = '';
  data.tasks.forEach(task => {
    const item = document.createElement('div');
    item.className = `result-item ${task.status}`;
    const timeStr = task.completed || task.started || '';
    const branchStr = task.branch ? `<code>${escapeHtml(task.branch)}</code>` : '';
    const commitStr = task.commit ? `<code>${escapeHtml(task.commit)}</code>` : '';
    const validStr = task.validation ? `<span>${escapeHtml(task.validation)}</span>` : '';

    item.innerHTML = `
      <div class="result-header">
        <span class="result-icon">${task.icon}</span>
        <span class="result-name">${escapeHtml(task.name)}</span>
        <span class="result-title">${escapeHtml(task.title)}</span>
      </div>
      <div class="result-meta">
        ${timeStr ? `<span>🕐 ${escapeHtml(timeStr.slice(0, 16))}</span>` : ''}
        ${branchStr ? `<span>⎇ ${branchStr}</span>` : ''}
        ${commitStr ? `<span>🔑 ${commitStr}</span>` : ''}
        ${validStr}
      </div>
    `;
    list.appendChild(item);
  });
}

// ============================================================
//  Orchestrator Control
// ============================================================
async function loadOrchestratorStatus() {
  try {
    const data = await API.get('/api/orchestrator/status');
    State.orchestratorRunning = data.running;
    updateOrchestratorUI(data.running);
  } catch (e) {
    // Silent
  }
}

function updateOrchestratorUI(running) {
  const badge = document.getElementById('orchestratorStatusBadge');
  const dot = badge.querySelector('.status-dot');
  const label = badge.querySelector('.status-label');
  const btnStart = document.getElementById('btnStart');

  if (running) {
    dot.className = 'status-dot running';
    label.textContent = '執行中';
    btnStart.disabled = true;
    btnStart.textContent = '▶ 執行中';
  } else {
    dot.className = 'status-dot idle';
    label.textContent = '待機中';
    btnStart.disabled = false;
    btnStart.textContent = '▶ 啟動';
  }
}

async function startOrchestrator() {
  if (!State.activeProjectId) {
    showToast('請先選擇專案', 'warning');
    return;
  }
  const project = State.projects.find(p => p.id === State.activeProjectId);
  try {
    await API.post('/api/orchestrator/start', {
      project_id: State.activeProjectId,
      mode: document.getElementById('modeSelect').value,
      target: document.getElementById('targetSelect').value,
      gui_target: project?.gui_target || 'vscode',
      cli_command: project?.cli_command || 'claude',
      dry_run: false,
    });
    showToast('Orchestrator 已啟動', 'success');
    State.orchestratorRunning = true;
    updateOrchestratorUI(true);
    // Start polling status
    if (!State.pollInterval) {
      State.pollInterval = setInterval(pollStatus, 5000);
    }
  } catch (e) {
    showToast('啟動失敗: ' + e.message, 'error');
  }
}

async function pauseOrchestrator() {
  try {
    await API.post('/api/orchestrator/pause');
    showToast('Orchestrator 已暫停（更新 roadmap.md）', 'info');
  } catch (e) {
    showToast('暫停失敗: ' + e.message, 'error');
  }
}

async function stopOrchestrator() {
  try {
    await API.post('/api/orchestrator/stop');
    showToast('Orchestrator 已停止', 'info');
    State.orchestratorRunning = false;
    updateOrchestratorUI(false);
    if (State.pollInterval) { clearInterval(State.pollInterval); State.pollInterval = null; }
  } catch (e) {
    showToast('停止失敗: ' + e.message, 'error');
  }
}

async function pollStatus() {
  if (State.activeProjectId) {
    await loadRoadmap(State.activeProjectId);
    await loadResults(State.activeProjectId);
  }
  const status = await API.get('/api/orchestrator/status').catch(() => null);
  if (status) {
    State.orchestratorRunning = status.running;
    updateOrchestratorUI(status.running);
    if (!status.running && State.pollInterval) {
      clearInterval(State.pollInterval);
      State.pollInterval = null;
    }
  }
}

// ============================================================
//  Project Modal
// ============================================================
let editingProjectId = null;

function openAddProjectModal() {
  editingProjectId = null;
  document.getElementById('projectModalTitle').textContent = '新增專案';
  document.getElementById('projectForm').reset();
  document.getElementById('projectModal').style.display = 'flex';
}

function closeProjectModal() {
  document.getElementById('projectModal').style.display = 'none';
}

async function saveProject() {
  const id = document.getElementById('fProjectId').value.trim();
  const name = document.getElementById('fProjectName').value.trim();
  const roadmapPath = document.getElementById('fRoadmapPath').value.trim();

  if (!id || !name || !roadmapPath) {
    showToast('請填寫必填欄位（ID、名稱、roadmap 路徑）', 'warning');
    return;
  }

  // Auto-derive results_log_path from roadmap_path if empty
  let resultsPath = document.getElementById('fResultsPath').value.trim();
  if (!resultsPath) {
    resultsPath = roadmapPath.replace(/roadmap\.md$/i, 'results_log.md')
      .replace(/[^/\\]+$/, 'results_log.md');
  }

  const payload = {
    id,
    name,
    description: document.getElementById('fProjectDesc').value.trim(),
    roadmap_path: roadmapPath,
    results_log_path: resultsPath,
    obsidian_vault_path: document.getElementById('fVaultPath').value.trim() || null,
    git_enabled: document.getElementById('fGitEnabled').checked,
    git_remote: 'origin',
    git_default_branch: 'main',
    workflow_mode: document.getElementById('fWorkflowMode').value,
    rpa_target: document.getElementById('fRpaTarget').value,
    cli_command: document.getElementById('fCliCommand').value.trim() || 'claude',
    gui_target: document.getElementById('fGuiTarget').value,
    active: false,
  };

  try {
    if (editingProjectId) {
      await API.patch(`/api/projects/${editingProjectId}`, payload);
      showToast('專案已更新', 'success');
    } else {
      await API.post('/api/projects', payload);
      showToast('專案已新增', 'success');
    }
    closeProjectModal();
    await loadProjects();
  } catch (e) {
    showToast('儲存失敗: ' + e.message, 'error');
  }
}

// ============================================================
//  Refresh & Misc
// ============================================================
async function refreshAll() {
  await loadProjects();
  if (State.activeProjectId) await loadProjectDetails(State.activeProjectId);
  showToast('已重新整理', 'info', 1200);
}

function clearLog() {
  document.getElementById('logContainer').innerHTML = '';
  State.logLines = [];
  document.getElementById('logCount').textContent = '0 行';
}

// ============================================================
//  Event Listeners
// ============================================================
function bindEvents() {
  document.getElementById('btnStart').addEventListener('click', startOrchestrator);
  document.getElementById('btnPause').addEventListener('click', pauseOrchestrator);
  document.getElementById('btnStop').addEventListener('click', stopOrchestrator);
  document.getElementById('btnRefreshAll').addEventListener('click', refreshAll);
  document.getElementById('btnRefreshResults').addEventListener('click', () => {
    if (State.activeProjectId) loadResults(State.activeProjectId);
  });
  document.getElementById('btnClearLog').addEventListener('click', clearLog);
  document.getElementById('btnAddProject').addEventListener('click', openAddProjectModal);
  document.getElementById('btnManageProjects').addEventListener('click', openAddProjectModal);
  document.getElementById('closeProjectModal').addEventListener('click', closeProjectModal);
  document.getElementById('cancelProjectModal').addEventListener('click', closeProjectModal);
  document.getElementById('saveProjectModal').addEventListener('click', saveProject);
  document.getElementById('btnDetectAgent').addEventListener('click', async () => {
    await loadOrchestratorStatus();
    showToast('Agent 狀態已更新', 'info', 1200);
  });

  // Close modal on overlay click
  document.getElementById('projectModal').addEventListener('click', (e) => {
    if (e.target === document.getElementById('projectModal')) closeProjectModal();
  });

  // Keyboard shortcuts
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeProjectModal();
  });
}

// ============================================================
//  Boot
// ============================================================
async function init() {
  bindEvents();
  connectWebSocket();

  // Load initial data
  await loadProjects();

  // Start periodic refresh every 30s (roadmap changes from Obsidian Sync)
  setInterval(() => {
    if (State.activeProjectId && !State.orchestratorRunning) {
      loadRoadmap(State.activeProjectId);
    }
  }, 30000);
}

document.addEventListener('DOMContentLoaded', init);
