/**
 * OpenAI Pool Orchestrator — 前端交互逻辑 v5.0
 */

// ==========================================
// 状态
// ==========================================
const state = {
  status: 'idle',          // idle | running | stopping
  successCount: 0,
  failCount: 0,
  logCount: 0,
  autoScroll: true,
  currentSteps: {},        // step名 → 状态
  eventSource: null,
  tokens: [],
  tokenFilter: {
    status: 'all',
    keyword: '',
  },
  stepsInRun: false,       // 是否处于一轮步骤追踪中（避免多线程 start 互相清空）
};

// ==========================================
// DOM 引用
// ==========================================
const $ = id => document.getElementById(id);
const DOM = {};

const STEPS = [
  { id: 'check_proxy', label: '网络检查' },
  { id: 'create_email', label: '创建邮箱' },
  { id: 'oauth_init', label: 'OAuth 初始化' },
  { id: 'sentinel', label: 'Sentinel Token' },
  { id: 'signup', label: '提交注册' },
  { id: 'send_otp', label: '发送验证码' },
  { id: 'wait_otp', label: '等待验证码' },
  { id: 'verify_otp', label: '验证 OTP' },
  { id: 'create_account', label: '创建账户' },
  { id: 'workspace', label: '选择 Workspace' },
  { id: 'get_token', label: '获取 Token' },
];

// ==========================================
// 初始化
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
  Object.assign(DOM, {
    statusBadge: $('statusBadge'),
    statusText: $('statusText'),
    statusDot: $('statusDot'),
    proxyInput: $('proxyInput'),
    checkProxyBtn: $('checkProxyBtn'),
    proxyStatus: $('proxyStatus'),
    btnStart: $('btnStart'),
    btnStop: $('btnStop'),
    statSuccess: $('statSuccess'),
    statFail: $('statFail'),
    statTotal: $('statTotal'),
    logBody: $('logBody'),
    logCount: $('logCount'),
    clearLogBtn: $('clearLogBtn'),
    progressFill: $('progressFill'),
    stepsTracker: $('stepsTracker'),
    segmentIndicator: $('segmentIndicator'),
    autoScrollCheck: $('autoScrollCheck'),
    // 多线程
    multithreadCheck: $('multithreadCheck'),
    threadCountInput: $('threadCountInput'),
    // Sub2Api
    sub2apiBaseUrl: $('sub2apiBaseUrl'),
    sub2apiEmail: $('sub2apiEmail'),
    sub2apiPassword: $('sub2apiPassword'),
    autoSyncCheck: $('autoSyncCheck'),
    uploadMode: $('uploadMode'),
    uploadModeSaveBtn: $('uploadModeSaveBtn'),
    uploadModeStatus: $('uploadModeStatus'),
    saveSyncConfigBtn: $('saveSyncConfigBtn'),
    syncStatus: $('syncStatus'),
    // Header 池状态
    headerSub2apiChip: $('headerSub2apiChip'),
    headerSub2apiLabel: $('headerSub2apiLabel'),
    headerSub2apiDelta: $('headerSub2apiDelta'),
    headerSub2apiBar: $('headerSub2apiBar'),
    headerCpaChip: $('headerCpaChip'),
    headerCpaLabel: $('headerCpaLabel'),
    headerCpaDelta: $('headerCpaDelta'),
    headerCpaBar: $('headerCpaBar'),
    themeToggleBtn: $('themeToggleBtn'),
    // CPA config
    cpaBaseUrl: $('cpaBaseUrl'),
    cpaToken: $('cpaToken'),
    cpaMinCandidates: $('cpaMinCandidates'),
    cpaUsedPercent: $('cpaUsedPercent'),
    cpaAutoMaintain: $('cpaAutoMaintain'),
    cpaInterval: $('cpaInterval'),
    cpaTestBtn: $('cpaTestBtn'),
    cpaSaveBtn: $('cpaSaveBtn'),
    cpaStatus: $('cpaStatus'),
    // Mail config（多选）
    mailStrategySelect: $('mailStrategySelect'),
    mailTestBtn: $('mailTestBtn'),
    mailSaveBtn: $('mailSaveBtn'),
    mailStatus: $('mailStatus'),
    // Pool tab — CPA
    poolTotal: $('poolTotal'),
    poolCandidates: $('poolCandidates'),
    poolError: $('poolError'),
    poolThreshold: $('poolThreshold'),
    poolPercent: $('poolPercent'),
    poolRefreshBtn: $('poolRefreshBtn'),
    poolMaintainBtn: $('poolMaintainBtn'),
    poolMaintainStatus: $('poolMaintainStatus'),
    poolTokenList: $('poolTokenList'),
    poolTokenCount: $('poolTokenCount'),
    poolCopyRtBtn: $('poolCopyRtBtn'),
    poolPwSyncBtn: $('poolPwSyncBtn'),
    tokenFilterStatus: $('tokenFilterStatus'),
    tokenFilterKeyword: $('tokenFilterKeyword'),
    tokenFilterApplyBtn: $('tokenFilterApplyBtn'),
    tokenFilterResetBtn: $('tokenFilterResetBtn'),
    // Pool tab — Sub2Api
    sub2apiPoolTotal: $('sub2apiPoolTotal'),
    sub2apiPoolNormal: $('sub2apiPoolNormal'),
    sub2apiPoolError: $('sub2apiPoolError'),
    sub2apiPoolThreshold: $('sub2apiPoolThreshold'),
    sub2apiPoolPercent: $('sub2apiPoolPercent'),
    sub2apiPoolRefreshBtn: $('sub2apiPoolRefreshBtn'),
    sub2apiPoolMaintainBtn: $('sub2apiPoolMaintainBtn'),
    sub2apiPoolMaintainStatus: $('sub2apiPoolMaintainStatus'),
    // Sub2Api config maintenance fields
    sub2apiMinCandidates: $('sub2apiMinCandidates'),
    sub2apiInterval: $('sub2apiInterval'),
    sub2apiAutoMaintain: $('sub2apiAutoMaintain'),
    sub2apiTestPoolBtn: $('sub2apiTestPoolBtn'),
    proxyPoolEnabled: $('proxyPoolEnabled'),
    proxyPoolApiUrl: $('proxyPoolApiUrl'),
    proxyPoolAuthMode: $('proxyPoolAuthMode'),
    proxyPoolApiKey: $('proxyPoolApiKey'),
    proxyPoolCount: $('proxyPoolCount'),
    proxyPoolCountry: $('proxyPoolCountry'),
    proxyPoolTestBtn: $('proxyPoolTestBtn'),
    proxyPoolSaveBtn: $('proxyPoolSaveBtn'),
    proxyPoolStatus: $('proxyPoolStatus'),
    // 代理保存 & 自动注册
    saveProxyBtn: $('saveProxyBtn'),
    autoRegisterCheck: $('autoRegisterCheck'),
  });

  renderSteps();
  connectSSE();
  loadTokens();
  updateStatus();
  loadSyncConfig();
  loadProxyPoolConfig();
  loadPoolConfig();
  loadMailConfig();
  initMailCheckboxes();
  pollPoolStatus();
  pollSub2ApiPoolStatus();
  initThemeSwitch();

  // 折叠面板
  initCollapsibles();

  // 控制中心事件
  DOM.checkProxyBtn.addEventListener('click', checkProxy);
  if (DOM.saveProxyBtn) DOM.saveProxyBtn.addEventListener('click', saveProxy);
  DOM.btnStart.addEventListener('click', startTask);
  DOM.btnStop.addEventListener('click', stopTask);
  DOM.clearLogBtn.addEventListener('click', clearLog);

  // 服务配置事件
  DOM.saveSyncConfigBtn.addEventListener('click', saveSyncConfig);
  if (DOM.uploadModeSaveBtn) DOM.uploadModeSaveBtn.addEventListener('click', saveUploadMode);
  DOM.cpaTestBtn.addEventListener('click', testCpaConnection);
  DOM.cpaSaveBtn.addEventListener('click', savePoolConfig);
  DOM.mailTestBtn.addEventListener('click', testMailConnection);
  DOM.mailSaveBtn.addEventListener('click', saveMailConfig);

  // 账号池事件 — CPA
  DOM.poolRefreshBtn.addEventListener('click', pollPoolStatus);
  DOM.poolMaintainBtn.addEventListener('click', triggerMaintenance);
  if (DOM.poolCopyRtBtn) DOM.poolCopyRtBtn.addEventListener('click', copyAllRt);
  if (DOM.poolPwSyncBtn) DOM.poolPwSyncBtn.addEventListener('click', batchSync);
  if (DOM.tokenFilterApplyBtn) DOM.tokenFilterApplyBtn.addEventListener('click', applyTokenFilter);
  if (DOM.tokenFilterResetBtn) DOM.tokenFilterResetBtn.addEventListener('click', resetTokenFilter);
  if (DOM.tokenFilterKeyword) {
    DOM.tokenFilterKeyword.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') applyTokenFilter();
    });
  }
  // 账号池事件 — Sub2Api
  if (DOM.sub2apiPoolRefreshBtn) DOM.sub2apiPoolRefreshBtn.addEventListener('click', pollSub2ApiPoolStatus);
  if (DOM.sub2apiPoolMaintainBtn) DOM.sub2apiPoolMaintainBtn.addEventListener('click', triggerSub2ApiMaintenance);
  if (DOM.sub2apiTestPoolBtn) DOM.sub2apiTestPoolBtn.addEventListener('click', testSub2ApiPoolConnection);
  if (DOM.proxyPoolTestBtn) DOM.proxyPoolTestBtn.addEventListener('click', testProxyPoolFetch);
  if (DOM.proxyPoolSaveBtn) DOM.proxyPoolSaveBtn.addEventListener('click', saveProxyPoolConfig);

  // Token 列表事件委托
  if (DOM.poolTokenList) {
    DOM.poolTokenList.addEventListener('click', async (e) => {
      const copyBtn = e.target.closest('.token-copy-btn');
      if (copyBtn) {
        try {
          const payload = decodeURIComponent(copyBtn.dataset.payload || '');
          await copyToken(payload);
        } catch { showToast('复制失败', 'error'); }
        return;
      }
      const deleteBtn = e.target.closest('.token-delete-btn');
      if (deleteBtn) {
        const filename = decodeURIComponent(deleteBtn.dataset.filename || '');
        if (filename) deleteToken(filename);
      }
    });
  }

  DOM.logBody.addEventListener('scroll', () => {
    const el = DOM.logBody;
    const isAtBottom = (el.scrollTop + el.clientHeight >= el.scrollHeight - 20);
    state.autoScroll = isAtBottom;
    if (DOM.autoScrollCheck) DOM.autoScrollCheck.checked = isAtBottom;
  });

  // 自动滚动开关
  if (DOM.autoScrollCheck) {
    DOM.autoScrollCheck.addEventListener('change', () => {
      state.autoScroll = DOM.autoScrollCheck.checked;
      if (state.autoScroll) DOM.logBody.scrollTop = DOM.logBody.scrollHeight;
    });
  }

  setInterval(updateStatus, 5000);
  setInterval(loadTokens, 60000);
  setInterval(pollPoolStatus, 30000);
  setInterval(pollSub2ApiPoolStatus, 30000);

  // Tab 导航
  initTabs();
});

// ==========================================
// Tab 导航切换 — iOS Segmented Control
// ==========================================
function initTabs() {
  const tabBtns = document.querySelectorAll('.tab-btn');
  const tabPanels = document.querySelectorAll('.tab-panel');
  const indicator = DOM.segmentIndicator;

  tabBtns.forEach((btn, index) => {
    btn.addEventListener('click', () => {
      tabBtns.forEach(b => { b.classList.remove('active'); b.setAttribute('aria-selected', 'false'); });
      tabPanels.forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      btn.setAttribute('aria-selected', 'true');
      document.getElementById(btn.dataset.tab).classList.add('active');

      // 滑动分段指示器
      if (indicator) {
        indicator.setAttribute('data-active', String(index));
      }
    });
  });
}

// ==========================================
// 折叠面板
// ==========================================
function initCollapsibles() {
  document.querySelectorAll('.collapsible-trigger').forEach(trigger => {
    trigger.addEventListener('click', () => {
      const section = trigger.closest('.collapsible');
      if (!section) return;
      const body = section.querySelector('.collapsible-body');
      if (!body) return;
      const icon = trigger.querySelector('.collapse-icon');
      const isOpen = section.classList.contains('open');
      if (isOpen) {
        section.classList.remove('open');
        body.style.display = 'none';
        if (icon) icon.classList.remove('open');
      } else {
        section.classList.add('open');
        body.style.display = 'block';
        if (icon) icon.classList.add('open');
      }
    });
  });
}

// ==========================================
// SSE 日志连接
// ==========================================
function connectSSE() {
  if (state.eventSource) state.eventSource.close();
  const es = new EventSource('/api/logs');
  state.eventSource = es;

  es.onmessage = (e) => {
    const event = JSON.parse(e.data);
    appendLog(event);

    if (event.level === 'token_saved') {
      loadTokens();
      showToast('新 Token 已保存: ' + event.message, 'success');
    }
    if (event.level === 'sync_ok') {
      showToast('已自动同步: ' + event.message, 'success');
    }
    if (event.step === 'start' && !state.stepsInRun) {
      state.stepsInRun = true;
      Object.keys(state.currentSteps).forEach(k => delete state.currentSteps[k]);
      renderSteps();
      if (state.countdownTimer) {
        clearInterval(state.countdownTimer);
        state.countdownTimer = null;
      }
    }
    if (event.step === 'wait' && event.message) {
      const match = event.message.match(/(\d+)\s*秒/);
      if (match) startCountdown(parseInt(match[1]));
    }
    if (event.step) updateStep(event.step, event.level);
  };

  es.onerror = () => setTimeout(connectSSE, 3000);
}

// ==========================================
// 日志渲染
// ==========================================
const LEVEL_ICON = { info: '›', success: '✓', error: '✗', warn: '⚠', connected: '⟳' };

function appendLog(event) {
  const { ts, level, message, step } = event;
  state.logCount++;
  const entry = document.createElement('div');
  entry.className = 'log-entry';
  entry.innerHTML = `
    <span class="log-ts">${ts || ''}</span>
    <span class="log-icon">${LEVEL_ICON[level] || '·'}</span>
    <span class="log-msg ${level}">${escapeHtml(message)}</span>
    ${step && !['start', 'stopped', 'retry', 'wait', 'runtime'].includes(step)
      ? `<span class="log-step">${escapeHtml(step)}</span>` : ''}
  `;
  DOM.logBody.appendChild(entry);
  DOM.logCount.textContent = state.logCount;
  if (state.autoScroll) DOM.logBody.scrollTop = DOM.logBody.scrollHeight;
  const entries = DOM.logBody.querySelectorAll('.log-entry');
  if (entries.length > 2000) entries[0].remove();
}

function clearLog() {
  DOM.logBody.innerHTML = '';
  state.logCount = 0;
  DOM.logCount.textContent = '0';
}

// ==========================================
// 代理检测
// ==========================================
async function checkProxy() {
  const proxy = DOM.proxyInput.value.trim();
  if (!proxy) { showToast('请先填写代理地址', 'error'); return; }
  DOM.proxyStatus.className = 'proxy-status';
  DOM.proxyStatus.innerHTML = '<span>检测中...</span>';
  DOM.checkProxyBtn.disabled = true;
  try {
    const res = await fetch('/api/check-proxy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proxy }),
    });
    const data = await res.json();
    if (data.ok) {
      DOM.proxyStatus.className = 'proxy-status ok';
      DOM.proxyStatus.innerHTML = `<span>可用 · 所在地: <b>${escapeHtml(data.loc || '')}</b></span>`;
    } else {
      DOM.proxyStatus.className = 'proxy-status fail';
      DOM.proxyStatus.innerHTML = `<span>不可用 · ${escapeHtml(data.error || '')}</span>`;
    }
  } catch {
    DOM.proxyStatus.className = 'proxy-status fail';
    DOM.proxyStatus.innerHTML = '<span>检测请求失败</span>';
  } finally {
    DOM.checkProxyBtn.disabled = false;
  }
}

// ==========================================
// 代理保存
// ==========================================
async function saveProxy() {
  const proxy = DOM.proxyInput.value.trim();
  if (!proxy) { showToast('请先填写代理地址', 'error'); return; }
  const auto_register = DOM.autoRegisterCheck ? DOM.autoRegisterCheck.checked : false;
  try {
    const res = await fetch('/api/proxy/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proxy, auto_register }),
    });
    if (res.ok) {
      showToast('代理配置已保存', 'success');
    } else {
      showToast('保存失败', 'error');
    }
  } catch (e) {
    showToast('保存请求失败: ' + e.message, 'error');
  }
}

// ==========================================
// 启动 / 停止任务
// ==========================================
async function startTask() {
  const proxy = DOM.proxyInput.value.trim();
  const proxyPoolEnabled = DOM.proxyPoolEnabled ? DOM.proxyPoolEnabled.checked : false;
  if (!proxy && !proxyPoolEnabled) { showToast('请填写代理地址或启用代理池', 'error'); return; }
  const multithread = DOM.multithreadCheck ? DOM.multithreadCheck.checked : false;
  const thread_count = DOM.threadCountInput ? parseInt(DOM.threadCountInput.value) || 3 : 1;
  try {
    const res = await fetch('/api/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proxy, multithread, thread_count }),
    });
    const data = await res.json();
    if (!res.ok) {
      showToast(data.detail || '启动失败', 'error');
      return;
    }
    Object.keys(state.currentSteps).forEach(k => delete state.currentSteps[k]);
    renderSteps();
    const workerMsg = multithread ? ` (${data.workers || thread_count} 线程)` : '';
    showToast('注册任务已启动' + workerMsg, 'success');
    updateStatus();
  } catch (e) {
    showToast('启动请求失败: ' + e.message, 'error');
  }
}

async function stopTask() {
  try {
    const res = await fetch('/api/stop', { method: 'POST' });
    if (!res.ok) {
      const data = await res.json();
      showToast(data.detail || '停止失败', 'error');
      return;
    }
    showToast('正在停止任务...', 'info');
    updateStatus();
  } catch (e) {
    showToast('停止请求失败: ' + e.message, 'error');
  }
}

// ==========================================
// 状态更新
// ==========================================
async function updateStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    state.status = data.status;
    state.successCount = data.success;
    state.failCount = data.fail;

    DOM.statusBadge.className = `status-badge ${data.status}`;
    const labelMap = { idle: '空闲', running: '运行中', stopping: '停止中' };
    DOM.statusText.textContent = labelMap[data.status] || data.status;

    const isRunning = data.status === 'running';
    const isStopping = data.status === 'stopping';
    DOM.btnStart.disabled = isRunning || isStopping;
    DOM.btnStop.disabled = !isRunning;
    DOM.progressFill.className = isRunning ? 'progress-fill running' : 'progress-fill';

    DOM.statSuccess.textContent = data.success;
    DOM.statFail.textContent = data.fail;
    DOM.statTotal.textContent = data.success + data.fail;

    // 任务结束后兜底清理步骤高亮，避免出现“状态已停但步骤还在跑”的视觉残留
    if (data.status === 'idle') {
      state.stepsInRun = false;
      if (Object.keys(state.currentSteps).length > 0) {
        Object.keys(state.currentSteps).forEach(k => delete state.currentSteps[k]);
        renderSteps();
      }
      if (state.countdownTimer) {
        clearInterval(state.countdownTimer);
        state.countdownTimer = null;
      }
    }
  } catch { }
}

// ==========================================
// 步骤追踪
// ==========================================
function renderSteps() {
  DOM.stepsTracker.innerHTML = STEPS.map(s => {
    const st = state.currentSteps[s.id] || '';
    return `<div class="step-item ${st}" id="step-${s.id}"><span class="step-dot"></span><span>${s.label}</span></div>`;
  }).join('');
}

function updateStep(stepId, level) {
  let stepState = '';
  if (level === 'success') stepState = 'done';
  else if (level === 'error') stepState = 'error';
  else if (level === 'info' || level === 'warn') stepState = 'active';
  if (stepState) {
    state.currentSteps[stepId] = stepState;
    const el = $(`step-${stepId}`);
    if (el) el.className = `step-item ${stepState}`;
  }
}

function startCountdown(seconds) {
  if (state.countdownTimer) clearInterval(state.countdownTimer);
  let remaining = seconds;
  const entries = DOM.logBody.querySelectorAll('.log-entry');
  const countdownEntry = entries.length > 0 ? entries[entries.length - 1] : null;
  const countdownMsgEl = countdownEntry ? countdownEntry.querySelector('.log-msg') : null;
  state.countdownTimer = setInterval(() => {
    remaining--;
    if (remaining <= 0) { clearInterval(state.countdownTimer); state.countdownTimer = null; return; }
    if (countdownMsgEl) countdownMsgEl.textContent = `休息中... 剩余 ${remaining} 秒`;
  }, 1000);
}

// ==========================================
// Token 列表
// ==========================================
async function loadTokens() {
  try {
    const res = await fetch('/api/tokens');
    const data = await res.json();
    state.tokens = data.tokens || [];
    renderTokenList();
  } catch { }
}

function getFilteredTokens(tokens) {
  const status = state.tokenFilter.status || 'all';
  const keyword = (state.tokenFilter.keyword || '').trim().toLowerCase();

  return (tokens || []).filter((t) => {
    const platforms = getTokenUploadedPlatforms(t);
    const uploaded = platforms.length > 0;
    if (status === 'synced' && !uploaded) return false;
    if (status === 'unsynced' && uploaded) return false;
    if (status === 'cpa' && !platforms.includes('cpa')) return false;
    if (status === 'sub2api' && !platforms.includes('sub2api')) return false;
    if (status === 'both' && !(platforms.includes('cpa') && platforms.includes('sub2api'))) return false;

    if (!keyword) return true;
    const email = String(t.email || '').toLowerCase();
    const fname = String(t.filename || '').toLowerCase();
    return email.includes(keyword) || fname.includes(keyword);
  });
}

function getTokenUploadedPlatforms(token) {
  const platforms = new Set();
  const fromTop = Array.isArray(token && token.uploaded_platforms) ? token.uploaded_platforms : [];
  const content = (token && token.content) || {};
  const fromContent = Array.isArray(content.uploaded_platforms) ? content.uploaded_platforms : [];
  [...fromTop, ...fromContent].forEach((p) => {
    const name = String(p || '').toLowerCase().trim();
    if (name === 'cpa' || name === 'sub2api') platforms.add(name);
  });
  if (content.cpa_uploaded || content.cpa_synced) platforms.add('cpa');
  if (content.sub2api_uploaded || content.sub2api_synced || content.synced) platforms.add('sub2api');
  return ['cpa', 'sub2api'].filter((p) => platforms.has(p));
}

function renderTokenList() {
  const allTokens = state.tokens || [];
  const filteredTokens = getFilteredTokens(allTokens);
  if (DOM.poolTokenCount) {
    DOM.poolTokenCount.textContent = `${filteredTokens.length}/${allTokens.length}`;
  }

  if (!DOM.poolTokenList) return;
  if (filteredTokens.length === 0) {
    const msg = allTokens.length === 0 ? '暂无 Token' : '暂无符合筛选条件的 Token';
    DOM.poolTokenList.innerHTML = `<div class="empty-state"><div class="empty-icon">🔑</div><span>${msg}</span></div>`;
    return;
  }
  DOM.poolTokenList.innerHTML = filteredTokens.map(t => renderTokenItem(t)).join('');
}

function applyTokenFilter() {
  state.tokenFilter.status = DOM.tokenFilterStatus ? DOM.tokenFilterStatus.value : 'all';
  state.tokenFilter.keyword = DOM.tokenFilterKeyword ? DOM.tokenFilterKeyword.value.trim() : '';
  renderTokenList();
}

function resetTokenFilter() {
  state.tokenFilter.status = 'all';
  state.tokenFilter.keyword = '';
  if (DOM.tokenFilterStatus) DOM.tokenFilterStatus.value = 'all';
  if (DOM.tokenFilterKeyword) DOM.tokenFilterKeyword.value = '';
  renderTokenList();
}

function renderTokenItem(t) {
  const platforms = getTokenUploadedPlatforms(t);
  const uploaded = platforms.length > 0;
  const platformBadges = platforms.length > 0
    ? platforms.map((p) => `<span class="platform-badge ${p}">${p === 'cpa' ? 'CPA' : 'Sub2Api'}</span>`).join('')
    : '<span class="platform-badge none">未上传</span>';
  const expiredStr = formatTime(t.expired);
  const tokenPayload = encodeURIComponent(JSON.stringify(t.content || {}));
  const filePayload = encodeURIComponent(t.filename || '');
  return `
    <div class="token-item${uploaded ? ' synced' : ''}" id="token-${cssEscape(t.filename)}">
      <div class="token-info">
        <div class="token-email" title="${escapeHtml(t.email)}">
          <span class="token-email-text">${escapeHtml(t.email || t.filename)}</span>
        </div>
        <div class="token-meta token-platforms">${platformBadges}</div>
        <div class="token-meta">过期: ${expiredStr}</div>
      </div>
      <div class="token-actions">
        <button class="btn btn-ghost btn-sm token-copy-btn" data-payload="${tokenPayload}">复制</button>
        <button class="btn btn-danger btn-sm token-delete-btn" data-filename="${filePayload}">删除</button>
      </div>
    </div>`;
}

function formatTime(timeStr) {
  if (!timeStr) return '未知';
  try {
    const d = new Date(timeStr);
    if (isNaN(d.getTime())) return timeStr;
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } catch { return timeStr; }
}

async function copyToken(jsonStr) {
  const ok = await copyText(jsonStr);
  showToast(ok ? 'Token 已复制到剪贴板' : '复制失败', ok ? 'success' : 'error');
}

async function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    try { await navigator.clipboard.writeText(text); return true; } catch { }
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0;';
    document.body.appendChild(ta);
    ta.focus(); ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch { return false; }
}

async function copyAllRt() {
  try {
    const visibleTokens = getFilteredTokens(state.tokens || []);
    const rts = visibleTokens.map(t => (t.content || {}).refresh_token || '').filter(Boolean);
    if (rts.length === 0) { showToast('没有可用的 Refresh Token', 'error'); return; }
    const ok = await copyText(rts.join('\n'));
    showToast(ok ? `已复制 ${rts.length} 个 RT（当前筛选）` : '复制失败', ok ? 'success' : 'error');
  } catch (e) { showToast('复制失败: ' + e.message, 'error'); }
}

async function deleteToken(filename) {
  if (!confirm(`确认删除 ${filename}？`)) return;
  try {
    const res = await fetch(`/api/tokens/${encodeURIComponent(filename)}`, { method: 'DELETE' });
    if (res.ok) { showToast('已删除', 'info'); loadTokens(); }
    else showToast('删除失败', 'error');
  } catch { showToast('删除请求失败', 'error'); }
}

// ==========================================
// Sub2Api 同步配置
// ==========================================
async function loadSyncConfig() {
  if (DOM.syncStatus) DOM.syncStatus.textContent = '';
  try {
    const res = await fetch('/api/sync-config');
    const cfg = await res.json();
    DOM.sub2apiBaseUrl.value = cfg.base_url || '';
    if (cfg.email) DOM.sub2apiEmail.value = cfg.email;
    DOM.autoSyncCheck.checked = cfg.auto_sync !== 'false';
    if (DOM.uploadMode) DOM.uploadMode.value = cfg.upload_mode || 'snapshot';
    if (DOM.uploadModeStatus) DOM.uploadModeStatus.textContent = '';
    if (DOM.sub2apiMinCandidates) DOM.sub2apiMinCandidates.value = cfg.sub2api_min_candidates || 200;
    if (DOM.sub2apiInterval) DOM.sub2apiInterval.value = cfg.sub2api_maintain_interval_minutes || 30;
    if (DOM.sub2apiAutoMaintain) DOM.sub2apiAutoMaintain.checked = !!cfg.sub2api_auto_maintain;
    if (DOM.multithreadCheck) DOM.multithreadCheck.checked = !!cfg.multithread;
    if (DOM.threadCountInput) DOM.threadCountInput.value = cfg.thread_count || 3;
    if (cfg.proxy && DOM.proxyInput) DOM.proxyInput.value = cfg.proxy;
    if (DOM.autoRegisterCheck) DOM.autoRegisterCheck.checked = !!cfg.auto_register;
    if (DOM.syncStatus) DOM.syncStatus.textContent = '';
  } catch { }
}

async function loadProxyPoolConfig() {
  try {
    const res = await fetch('/api/proxy-pool/config');
    const cfg = await res.json();
    if (DOM.proxyPoolEnabled) DOM.proxyPoolEnabled.checked = !!cfg.proxy_pool_enabled;
    if (DOM.proxyPoolApiUrl) DOM.proxyPoolApiUrl.value = cfg.proxy_pool_api_url || 'https://zenproxy.top/api/fetch';
    if (DOM.proxyPoolAuthMode) DOM.proxyPoolAuthMode.value = cfg.proxy_pool_auth_mode || 'query';
    if (DOM.proxyPoolCount) DOM.proxyPoolCount.value = cfg.proxy_pool_count || 1;
    if (DOM.proxyPoolCountry) DOM.proxyPoolCountry.value = (cfg.proxy_pool_country || 'US').toUpperCase();
    if (DOM.proxyPoolApiKey) {
      DOM.proxyPoolApiKey.value = '';
      DOM.proxyPoolApiKey.placeholder = cfg.proxy_pool_api_key_preview
        ? `已保存: ${cfg.proxy_pool_api_key_preview}`
        : '请输入代理池 API Key';
    }
    if (DOM.proxyPoolStatus) DOM.proxyPoolStatus.textContent = '';
  } catch { }
}

async function saveProxyPoolConfig() {
  if (!DOM.proxyPoolSaveBtn) return;
  const payload = {
    proxy_pool_enabled: DOM.proxyPoolEnabled ? DOM.proxyPoolEnabled.checked : true,
    proxy_pool_api_url: DOM.proxyPoolApiUrl ? DOM.proxyPoolApiUrl.value.trim() : 'https://zenproxy.top/api/fetch',
    proxy_pool_auth_mode: DOM.proxyPoolAuthMode ? DOM.proxyPoolAuthMode.value : 'query',
    proxy_pool_api_key: DOM.proxyPoolApiKey ? DOM.proxyPoolApiKey.value.trim() : '',
    proxy_pool_count: DOM.proxyPoolCount ? (parseInt(DOM.proxyPoolCount.value, 10) || 1) : 1,
    proxy_pool_country: DOM.proxyPoolCountry ? DOM.proxyPoolCountry.value.trim().toUpperCase() : 'US',
  };
  if (!payload.proxy_pool_api_url) {
    showToast('请填写代理池 API 地址', 'error');
    return;
  }
  if (payload.proxy_pool_count < 1) payload.proxy_pool_count = 1;
  if (!payload.proxy_pool_country) payload.proxy_pool_country = 'US';

  DOM.proxyPoolSaveBtn.disabled = true;
  const oldText = DOM.proxyPoolSaveBtn.textContent;
  DOM.proxyPoolSaveBtn.textContent = '保存中...';
  if (DOM.proxyPoolStatus) DOM.proxyPoolStatus.textContent = '正在保存代理池配置...';
  try {
    const res = await fetch('/api/proxy-pool/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      const msg = data.detail || '保存失败';
      if (DOM.proxyPoolStatus) DOM.proxyPoolStatus.textContent = msg;
      showToast(msg, 'error');
      return;
    }
    if (DOM.proxyPoolApiKey && payload.proxy_pool_api_key) {
      DOM.proxyPoolApiKey.value = '';
      DOM.proxyPoolApiKey.placeholder = `已保存: ${payload.proxy_pool_api_key.slice(0, 8)}...`;
    }
    const msg = '代理池配置已保存';
    if (DOM.proxyPoolStatus) DOM.proxyPoolStatus.textContent = msg;
    showToast(msg, 'success');
  } catch (e) {
    const msg = '请求失败: ' + e.message;
    if (DOM.proxyPoolStatus) DOM.proxyPoolStatus.textContent = msg;
    showToast(msg, 'error');
  } finally {
    DOM.proxyPoolSaveBtn.disabled = false;
    DOM.proxyPoolSaveBtn.textContent = oldText || '保存代理池配置';
  }
}

async function saveSyncConfig() {
  const base_url = DOM.sub2apiBaseUrl.value.trim();
  const email = DOM.sub2apiEmail.value.trim();
  const password = DOM.sub2apiPassword.value.trim();
  const auto_sync = DOM.autoSyncCheck.checked ? 'true' : 'false';
  const upload_mode = DOM.uploadMode ? DOM.uploadMode.value : 'snapshot';
  const sub2api_min_candidates = parseInt(DOM.sub2apiMinCandidates.value) || 200;
  const sub2api_auto_maintain = DOM.sub2apiAutoMaintain.checked;
  const sub2api_maintain_interval_minutes = parseInt(DOM.sub2apiInterval.value) || 30;
  const multithread = DOM.multithreadCheck ? DOM.multithreadCheck.checked : false;
  const thread_count = DOM.threadCountInput ? parseInt(DOM.threadCountInput.value) || 3 : 3;
  const auto_register = DOM.autoRegisterCheck ? DOM.autoRegisterCheck.checked : false;

  if (!base_url) { showToast('请填写平台地址', 'error'); return; }
  if (!email) { showToast('请填写邮箱', 'error'); return; }

  DOM.saveSyncConfigBtn.disabled = true;
  DOM.saveSyncConfigBtn.textContent = '验证中...';
  DOM.syncStatus.textContent = '正在验证账号密码...';
  try {
    const res = await fetch('/api/sync-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        base_url, email, password, account_name: 'AutoReg', auto_sync,
        upload_mode,
        sub2api_min_candidates, sub2api_auto_maintain, sub2api_maintain_interval_minutes,
        multithread, thread_count, auto_register,
      }),
    });
    const data = await res.json();
    if (res.ok) {
      showToast('验证通过，配置已保存', 'success');
      DOM.syncStatus.textContent = '验证通过，配置已保存';
      pollSub2ApiPoolStatus();
    } else {
      showToast(data.detail || '验证失败', 'error');
      DOM.syncStatus.textContent = data.detail || '验证失败';
    }
  } catch (e) {
    showToast('请求失败: ' + e.message, 'error');
    DOM.syncStatus.textContent = '请求失败: ' + e.message;
  } finally {
    DOM.saveSyncConfigBtn.disabled = false;
    DOM.saveSyncConfigBtn.textContent = '保存';
  }
}

async function saveUploadMode() {
  const upload_mode = DOM.uploadMode ? DOM.uploadMode.value : 'snapshot';
  if (!DOM.uploadModeSaveBtn) return;
  DOM.uploadModeSaveBtn.disabled = true;
  const oldText = DOM.uploadModeSaveBtn.textContent;
  DOM.uploadModeSaveBtn.textContent = '保存中...';
  if (DOM.uploadModeStatus) DOM.uploadModeStatus.textContent = '正在保存策略...';
  try {
    const res = await fetch('/api/upload-mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ upload_mode }),
    });
    const data = await res.json();
    if (!res.ok) {
      const msg = data.detail || '保存失败';
      showToast(msg, 'error');
      if (DOM.uploadModeStatus) DOM.uploadModeStatus.textContent = msg;
      return;
    }
    const label = upload_mode === 'decoupled' ? '双平台同传（单账号双上传）' : '串行补平台（先CPA后Sub2Api）';
    showToast('上传策略已保存：' + label, 'success');
    if (DOM.uploadModeStatus) DOM.uploadModeStatus.textContent = '已保存：' + label;
  } catch (e) {
    showToast('请求失败: ' + e.message, 'error');
    if (DOM.uploadModeStatus) DOM.uploadModeStatus.textContent = '请求失败: ' + e.message;
  } finally {
    DOM.uploadModeSaveBtn.disabled = false;
    DOM.uploadModeSaveBtn.textContent = oldText || '保存策略';
  }
}

async function batchSync() {
  const btn = DOM.poolPwSyncBtn;
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = '导入中...';
  showToast('批量导入开始', 'info');
  try {
    const res = await fetch('/api/sync-batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filenames: [] }),
    });
    const data = await res.json();
    if (!res.ok) { showToast(data.detail || '导入失败', 'error'); return; }
    const msg = `导入完成：共 ${data.total}，成功 ${data.ok}，失败 ${data.fail}`;
    showToast(msg, data.fail > 0 ? 'info' : 'success');
  } catch (e) {
    showToast('导入失败: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '批量导入';
  }
}

// ==========================================
// CPA 配置
// ==========================================
async function loadPoolConfig() {
  try {
    const res = await fetch('/api/pool/config');
    const cfg = await res.json();
    DOM.cpaBaseUrl.value = cfg.cpa_base_url || '';
    DOM.cpaMinCandidates.value = cfg.min_candidates || 800;
    DOM.cpaUsedPercent.value = cfg.used_percent_threshold || 95;
    DOM.cpaAutoMaintain.checked = !!cfg.auto_maintain;
    DOM.cpaInterval.value = cfg.maintain_interval_minutes || 30;
    if (DOM.cpaStatus) DOM.cpaStatus.textContent = '';
  } catch { }
}

async function savePoolConfig() {
  const payload = {
    cpa_base_url: DOM.cpaBaseUrl.value.trim(),
    cpa_token: DOM.cpaToken.value.trim(),
    min_candidates: parseInt(DOM.cpaMinCandidates.value) || 800,
    used_percent_threshold: parseInt(DOM.cpaUsedPercent.value) || 95,
    auto_maintain: DOM.cpaAutoMaintain.checked,
    maintain_interval_minutes: parseInt(DOM.cpaInterval.value) || 30,
  };
  DOM.cpaSaveBtn.disabled = true;
  try {
    const res = await fetch('/api/pool/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      showToast('CPA 配置已保存', 'success');
      DOM.cpaStatus.textContent = '配置已保存';
      pollPoolStatus();
    } else {
      const data = await res.json();
      showToast(data.detail || '保存失败', 'error');
      DOM.cpaStatus.textContent = data.detail || '保存失败';
    }
  } catch (e) {
    showToast('请求失败: ' + e.message, 'error');
    DOM.cpaStatus.textContent = '请求失败';
  } finally {
    DOM.cpaSaveBtn.disabled = false;
  }
}

async function testCpaConnection() {
  DOM.cpaTestBtn.disabled = true;
  DOM.cpaStatus.textContent = '测试中...';
  try {
    const res = await fetch('/api/pool/check', { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      DOM.cpaStatus.textContent = data.message || '连接成功';
      showToast('CPA 连接成功', 'success');
    } else {
      DOM.cpaStatus.textContent = data.message || data.detail || '连接失败';
      showToast('CPA 连接失败', 'error');
    }
  } catch (e) {
    DOM.cpaStatus.textContent = '请求失败: ' + e.message;
  } finally {
    DOM.cpaTestBtn.disabled = false;
  }
}

// ==========================================
// 池状态轮询
// ==========================================
async function pollPoolStatus() {
  try {
    const res = await fetch('/api/pool/status');
    const data = await res.json();

    if (!data.configured) {
      if (DOM.poolTotal) DOM.poolTotal.textContent = '--';
      if (DOM.poolCandidates) DOM.poolCandidates.textContent = '--';
      if (DOM.poolError) DOM.poolError.textContent = '--';
      if (DOM.poolThreshold) DOM.poolThreshold.textContent = '--';
      if (DOM.poolPercent) DOM.poolPercent.textContent = '--';
      updateHeaderCpa(null);
      return;
    }

    const candidates = data.candidates || 0;
    const errorCount = data.error_count || 0;
    const threshold = data.threshold || 0;
    const fillPct = threshold > 0 ? Math.round(candidates / threshold * 100) : 100;

    if (DOM.poolTotal) DOM.poolTotal.textContent = data.total || 0;
    if (DOM.poolCandidates) DOM.poolCandidates.textContent = candidates;
    if (DOM.poolError) {
      DOM.poolError.textContent = errorCount;
      DOM.poolError.className = `stat-value ${errorCount > 0 ? 'red' : 'green'}`;
    }
    if (DOM.poolThreshold) DOM.poolThreshold.textContent = threshold;
    if (DOM.poolPercent) {
      DOM.poolPercent.textContent = fillPct + '%';
      DOM.poolPercent.className = `stat-value ${fillPct >= 100 ? 'green' : fillPct >= 80 ? 'yellow' : 'red'}`;
    }

    updateHeaderCpa({ candidates, threshold, fillPct, errorCount });
  } catch { }
}

async function triggerMaintenance() {
  DOM.poolMaintainBtn.disabled = true;
  DOM.poolMaintainBtn.textContent = '维护中...';
  DOM.poolMaintainStatus.textContent = '正在探测并清理无效账号...';
  try {
    const res = await fetch('/api/pool/maintain', { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      const msg = `维护完成: 无效 ${data.invalid_count || 0}, 已删除 ${data.deleted_ok || 0}, 失败 ${data.deleted_fail || 0}`;
      DOM.poolMaintainStatus.textContent = msg;
      showToast(msg, 'success');
      pollPoolStatus();
    } else {
      DOM.poolMaintainStatus.textContent = data.detail || '维护失败';
      showToast(data.detail || '维护失败', 'error');
    }
  } catch (e) {
    DOM.poolMaintainStatus.textContent = '请求失败: ' + e.message;
    showToast('维护请求失败', 'error');
  } finally {
    DOM.poolMaintainBtn.disabled = false;
    DOM.poolMaintainBtn.textContent = '维护';
  }
}

// ==========================================
// Sub2Api 池状态轮询
// ==========================================
async function pollSub2ApiPoolStatus() {
  try {
    const res = await fetch('/api/sub2api/pool/status');
    const data = await res.json();

    if (data.configured && data.error) {
      if (DOM.sub2apiPoolMaintainStatus) DOM.sub2apiPoolMaintainStatus.textContent = 'Sub2Api 状态获取失败: ' + data.error;
      updateHeaderSub2Api(null);
      return;
    }

    if (!data.configured) {
      if (DOM.sub2apiPoolTotal) DOM.sub2apiPoolTotal.textContent = '--';
      if (DOM.sub2apiPoolNormal) DOM.sub2apiPoolNormal.textContent = '--';
      if (DOM.sub2apiPoolError) DOM.sub2apiPoolError.textContent = '--';
      if (DOM.sub2apiPoolThreshold) DOM.sub2apiPoolThreshold.textContent = '--';
      if (DOM.sub2apiPoolPercent) DOM.sub2apiPoolPercent.textContent = '--';
      updateHeaderSub2Api(null);
      return;
    }

    const normal = data.candidates || 0;
    const error = data.error_count || 0;
    const total = data.total || 0;
    const threshold = data.threshold || 0;
    // 充足率: 正常账号 / 目标阈值
    const fillPct = threshold > 0 ? Math.round(normal / threshold * 100) : 100;
    // 健康率: 正常账号 / 总账号 (无异常就是 100%)
    const healthPct = total > 0 ? Math.round(normal / total * 100) : 100;

    if (DOM.sub2apiPoolTotal) DOM.sub2apiPoolTotal.textContent = total;
    if (DOM.sub2apiPoolNormal) DOM.sub2apiPoolNormal.textContent = normal;
    if (DOM.sub2apiPoolError) {
      DOM.sub2apiPoolError.textContent = error;
      DOM.sub2apiPoolError.className = `stat-value ${error > 0 ? 'red' : 'green'}`;
    }
    if (DOM.sub2apiPoolThreshold) DOM.sub2apiPoolThreshold.textContent = threshold;
    if (DOM.sub2apiPoolPercent) {
      DOM.sub2apiPoolPercent.textContent = fillPct + '%';
      DOM.sub2apiPoolPercent.className = `stat-value ${fillPct >= 100 ? 'green' : fillPct >= 80 ? 'yellow' : 'red'}`;
    }

    updateHeaderSub2Api({ normal, threshold, fillPct, error });
  } catch { }
}

function updateHeaderSub2Api(data) {
  if (!data) {
    if (DOM.headerSub2apiLabel) DOM.headerSub2apiLabel.textContent = '-- / --';
    if (DOM.headerSub2apiDelta) DOM.headerSub2apiDelta.textContent = '--';
    if (DOM.headerSub2apiBar) DOM.headerSub2apiBar.style.width = '0%';
    if (DOM.headerSub2apiChip) DOM.headerSub2apiChip.className = 'pool-chip status-idle';
    if (DOM.headerSub2apiBar) DOM.headerSub2apiBar.className = 'pool-chip-fill';
    if (DOM.headerSub2apiDelta) DOM.headerSub2apiDelta.className = 'pool-chip-delta';
    return;
  }
  const { normal, threshold, fillPct, error: errorCount } = data;
  const state = _headerPoolState(fillPct, errorCount);
  if (DOM.headerSub2apiLabel) DOM.headerSub2apiLabel.textContent = `${normal} / ${threshold}`;
  if (DOM.headerSub2apiDelta) DOM.headerSub2apiDelta.textContent = _headerPoolDelta(fillPct);
  if (DOM.headerSub2apiBar) {
    DOM.headerSub2apiBar.style.width = Math.min(100, fillPct) + '%';
    DOM.headerSub2apiBar.className = `pool-chip-fill ${state}`;
  }
  if (DOM.headerSub2apiChip) DOM.headerSub2apiChip.className = `pool-chip status-${state}`;
  if (DOM.headerSub2apiDelta) DOM.headerSub2apiDelta.className = `pool-chip-delta ${state}`;
}

function updateHeaderCpa(data) {
  if (!data) {
    if (DOM.headerCpaLabel) DOM.headerCpaLabel.textContent = '-- / --';
    if (DOM.headerCpaDelta) DOM.headerCpaDelta.textContent = '--';
    if (DOM.headerCpaBar) DOM.headerCpaBar.style.width = '0%';
    if (DOM.headerCpaChip) DOM.headerCpaChip.className = 'pool-chip status-idle';
    if (DOM.headerCpaBar) DOM.headerCpaBar.className = 'pool-chip-fill';
    if (DOM.headerCpaDelta) DOM.headerCpaDelta.className = 'pool-chip-delta';
    return;
  }
  const { candidates, threshold, fillPct, errorCount } = data;
  const state = _headerPoolState(fillPct, errorCount);
  if (DOM.headerCpaLabel) DOM.headerCpaLabel.textContent = `${candidates} / ${threshold}`;
  if (DOM.headerCpaDelta) DOM.headerCpaDelta.textContent = _headerPoolDelta(fillPct);
  if (DOM.headerCpaBar) {
    DOM.headerCpaBar.style.width = Math.min(100, fillPct) + '%';
    DOM.headerCpaBar.className = `pool-chip-fill ${state}`;
  }
  if (DOM.headerCpaChip) DOM.headerCpaChip.className = `pool-chip status-${state}`;
  if (DOM.headerCpaDelta) DOM.headerCpaDelta.className = `pool-chip-delta ${state}`;
}

function _headerPoolState(fillPct, errorCount) {
  if (errorCount > 0) return 'danger';
  if (fillPct > 110) return 'over';
  if (fillPct >= 100) return 'ok';
  if (fillPct >= 80) return 'warn';
  return 'danger';
}

function _headerPoolDelta(fillPct) {
  if (!Number.isFinite(fillPct)) return '--';
  const delta = Math.round(fillPct - 100);
  if (delta === 0) return '0%';
  return `${delta > 0 ? '+' : ''}${delta}%`;
}

async function triggerSub2ApiMaintenance() {
  DOM.sub2apiPoolMaintainBtn.disabled = true;
  DOM.sub2apiPoolMaintainBtn.textContent = '维护中...';
  DOM.sub2apiPoolMaintainStatus.textContent = '正在刷新异常账号并清理...';
  try {
    const res = await fetch('/api/sub2api/pool/maintain', { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      const msg = `维护完成: 异常 ${data.error_count || 0}, 刷新 ${data.refreshed || 0}, 删除 ${data.deleted_ok || 0}, 失败 ${data.deleted_fail || 0}`;
      DOM.sub2apiPoolMaintainStatus.textContent = msg;
      showToast(msg, 'success');
      pollSub2ApiPoolStatus();
    } else {
      DOM.sub2apiPoolMaintainStatus.textContent = data.detail || '维护失败';
      showToast(data.detail || '维护失败', 'error');
    }
  } catch (e) {
    DOM.sub2apiPoolMaintainStatus.textContent = '请求失败: ' + e.message;
    showToast('Sub2Api 维护请求失败', 'error');
  } finally {
    DOM.sub2apiPoolMaintainBtn.disabled = false;
    DOM.sub2apiPoolMaintainBtn.textContent = '维护';
  }
}

async function testProxyPoolFetch() {
  if (!DOM.proxyPoolTestBtn) return;
  DOM.proxyPoolTestBtn.disabled = true;
  const oldText = DOM.proxyPoolTestBtn.textContent;
  if (DOM.proxyPoolStatus) DOM.proxyPoolStatus.textContent = '正在测试代理池取号...';
  DOM.proxyPoolTestBtn.textContent = '测试取号中...';
  try {
    const payload = {
      enabled: DOM.proxyPoolEnabled ? DOM.proxyPoolEnabled.checked : true,
      api_url: DOM.proxyPoolApiUrl ? DOM.proxyPoolApiUrl.value.trim() : 'https://zenproxy.top/api/fetch',
      auth_mode: DOM.proxyPoolAuthMode ? DOM.proxyPoolAuthMode.value : 'query',
      api_key: DOM.proxyPoolApiKey ? DOM.proxyPoolApiKey.value.trim() : '',
      count: DOM.proxyPoolCount ? (parseInt(DOM.proxyPoolCount.value, 10) || 1) : 1,
      country: DOM.proxyPoolCountry ? DOM.proxyPoolCountry.value.trim().toUpperCase() : 'US',
    };
    const res = await fetch('/api/proxy-pool/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      const msg = data.error || data.detail || '代理池取号失败';
      if (DOM.proxyPoolStatus) DOM.proxyPoolStatus.textContent = msg;
      showToast(msg, 'error');
      return;
    }
    const locText = data.loc ? ` loc=${data.loc}` : '';
    const supportText = data.supported === null || data.supported === undefined
      ? ''
      : (data.supported ? ' 可用' : ' 不可用(CN/HK)');
    const traceWarn = data.trace_error ? `；trace失败: ${data.trace_error}` : '';
    const msg = `取号成功: ${data.proxy}${locText}${supportText}${traceWarn}`;
    if (DOM.proxyPoolStatus) DOM.proxyPoolStatus.textContent = msg;
    showToast('代理池取号成功', 'success');
  } catch (e) {
    const msg = '测试请求失败: ' + e.message;
    if (DOM.proxyPoolStatus) DOM.proxyPoolStatus.textContent = msg;
    showToast(msg, 'error');
  } finally {
    DOM.proxyPoolTestBtn.disabled = false;
    if (DOM.syncStatus) DOM.syncStatus.textContent = '';
    DOM.proxyPoolTestBtn.textContent = oldText || '测试代理池取号';
  }
}

async function testSub2ApiPoolConnection() {
  DOM.sub2apiTestPoolBtn.disabled = true;
  DOM.syncStatus.textContent = '测试连接中...';
  try {
    const res = await fetch('/api/sub2api/pool/check', { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      DOM.syncStatus.textContent = data.message || '连接成功';
      showToast('Sub2Api 池连接成功', 'success');
    } else {
      DOM.syncStatus.textContent = data.message || data.detail || '连接失败';
      showToast('Sub2Api 池连接失败', 'error');
    }
  } catch (e) {
    DOM.syncStatus.textContent = '请求失败: ' + e.message;
  } finally {
    DOM.sub2apiTestPoolBtn.disabled = false;
  }
}

// ==========================================
// 邮箱配置（多选）
// ==========================================

function initMailCheckboxes() {
  document.querySelectorAll('.mail-provider-check').forEach(cb => {
    cb.setAttribute('aria-expanded', cb.checked);
    cb.addEventListener('change', () => {
      const item = cb.closest('.provider-item');
      const config = item.querySelector('.provider-config');
      if (config) config.style.display = cb.checked ? 'block' : 'none';
      cb.setAttribute('aria-expanded', cb.checked);
    });
  });
}

async function loadMailConfig() {
  try {
    const res = await fetch('/api/mail/config');
    const data = await res.json();
    const providers = data.mail_providers || [data.mail_provider || 'mailtm'];
    const configs = data.mail_provider_configs || {};
    const strategy = data.mail_strategy || 'round_robin';

    // 设置 checkboxes
    document.querySelectorAll('.mail-provider-check').forEach(cb => {
      const name = cb.value;
      cb.checked = providers.includes(name);
      const item = cb.closest('.provider-item');
      const configDiv = item.querySelector('.provider-config');
      if (configDiv) configDiv.style.display = cb.checked ? 'block' : 'none';

      // 填充 per-provider 配置
      const pcfg = configs[name] || {};
      item.querySelectorAll('[data-key]').forEach(input => {
        const key = input.dataset.key;
        const previewKey = key + '_preview';
        if (pcfg[key]) input.value = pcfg[key];
        else if (pcfg[previewKey]) input.placeholder = pcfg[previewKey];
      });
    });

    // 兼容旧格式
    if (!data.mail_providers && data.mail_config) {
      const mc = data.mail_config;
      const activeProvider = data.mail_provider || 'mailtm';
      const item = document.querySelector(`.provider-item[data-provider="${activeProvider}"]`);
      if (item) {
        const apiBaseInput = item.querySelector('[data-key="api_base"]');
        if (apiBaseInput && mc.api_base) apiBaseInput.value = mc.api_base;
      }
    }

    if (DOM.mailStrategySelect) DOM.mailStrategySelect.value = strategy;
  } catch { }
}

async function saveMailConfig() {
  const checkedProviders = [];
  const providerConfigs = {};

  document.querySelectorAll('.mail-provider-check').forEach(cb => {
    const name = cb.value;
    if (cb.checked) {
      checkedProviders.push(name);
      const item = cb.closest('.provider-item');
      const cfg = {};
      item.querySelectorAll('[data-key]').forEach(input => {
        const val = input.value.trim();
        if (val) cfg[input.dataset.key] = val;
      });
      providerConfigs[name] = cfg;
    }
  });

  if (checkedProviders.length === 0) {
    showToast('请至少选择一个邮箱提供商', 'error');
    return;
  }

  const strategy = DOM.mailStrategySelect ? DOM.mailStrategySelect.value : 'round_robin';
  DOM.mailSaveBtn.disabled = true;
  try {
    const res = await fetch('/api/mail/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mail_provider: checkedProviders[0],
        mail_config: providerConfigs[checkedProviders[0]] || {},
        mail_providers: checkedProviders,
        mail_provider_configs: providerConfigs,
        mail_strategy: strategy,
      }),
    });
    if (res.ok) {
      showToast('邮箱配置已保存', 'success');
      DOM.mailStatus.textContent = '配置已保存';
    } else {
      const data = await res.json();
      DOM.mailStatus.textContent = data.detail || '保存失败';
    }
  } catch (e) {
    DOM.mailStatus.textContent = '请求失败: ' + e.message;
  } finally {
    DOM.mailSaveBtn.disabled = false;
  }
}

async function testMailConnection() {
  DOM.mailTestBtn.disabled = true;
  DOM.mailStatus.textContent = '测试中...';
  try {
    await saveMailConfig();
    const res = await fetch('/api/mail/test', { method: 'POST' });
    const data = await res.json();
    if (data.results) {
      const msgs = data.results.map(r => `${r.provider}: ${r.ok ? 'OK' : r.message}`);
      DOM.mailStatus.textContent = msgs.join(' | ');
    } else {
      DOM.mailStatus.textContent = data.message || (data.ok ? '连接成功' : '连接失败');
    }
    showToast(data.ok ? '邮箱测试通过' : '邮箱测试失败', data.ok ? 'success' : 'error');
  } catch (e) {
    DOM.mailStatus.textContent = '请求失败: ' + e.message;
  } finally {
    DOM.mailTestBtn.disabled = false;
  }
}

// ==========================================
// Toast 通知 — 带图标和退出动画
// ==========================================
const TOAST_ICONS = {
  success: '&#10003;',
  error: '&#10007;',
  info: '&#8505;',
};

const THEME_STORAGE_KEY = 'oai_registrar_theme_v1';

function initThemeSwitch() {
  const btn = DOM.themeToggleBtn;
  if (!btn) return;

  let saved = 'dark';
  try {
    const value = localStorage.getItem(THEME_STORAGE_KEY);
    if (value === 'light' || value === 'dark') saved = value;
  } catch { }

  applyTheme(saved);

  btn.addEventListener('click', () => {
    const isLight = document.body.classList.contains('theme-light');
    const nextTheme = isLight ? 'dark' : 'light';
    applyTheme(nextTheme);
    try { localStorage.setItem(THEME_STORAGE_KEY, nextTheme); } catch { }
  });
}

function applyTheme(theme) {
  const isLight = theme === 'light';
  document.body.classList.toggle('theme-light', isLight);
  updateThemeToggleLabel(isLight);
}

function updateThemeToggleLabel(isLight) {
  const btn = DOM.themeToggleBtn;
  if (!btn) return;
  const currentLabel = isLight ? '\u660e\u4eae' : '\u9ed1\u6697';
  const nextLabel = isLight ? '\u9ed1\u6697' : '\u660e\u4eae';
  const toggleLabel = btn.querySelector('.theme-toggle-label');
  if (toggleLabel) toggleLabel.textContent = currentLabel;
  btn.setAttribute('aria-label', `\u5207\u6362\u5230${nextLabel}\u4e3b\u9898`);
  btn.setAttribute('title', `\u5207\u6362\u5230${nextLabel}\u4e3b\u9898`);
}

function showToast(msg, type = 'info') {
  const container = $('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  const iconHtml = TOAST_ICONS[type] || TOAST_ICONS.info;
  toast.innerHTML = `<span class="toast-icon">${iconHtml}</span><span>${escapeHtml(msg)}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.animation = 'toast-out .25s var(--ease-spring) forwards';
    toast.addEventListener('animationend', () => toast.remove());
  }, 3200);
}

// ==========================================
// 工具函数
// ==========================================
function escapeHtml(str) {
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function cssEscape(str) {
  return str.replace(/[^a-zA-Z0-9_-]/g, '_');
}

// ==========================================
// 拖拽调整栏宽度 + localStorage 持久化
// ==========================================
(function initResizable() {
  const STORAGE_KEY = 'oai_registrar_layout_v3';
  const shell = document.querySelector('.app-shell');
  const resizeLeft = document.getElementById('resizeLeft');
  const resizeRight = document.getElementById('resizeRight');
  if (!shell) return;

  function getTrackPx(index) {
    const tracks = getComputedStyle(shell).gridTemplateColumns.match(/[\d.]+px/g) || [];
    const val = tracks[index] ? parseFloat(tracks[index]) : NaN;
    return Number.isFinite(val) ? val : NaN;
  }

  function loadLayout() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
      if (!saved) return;
      const maxW = shell.getBoundingClientRect().width || window.innerWidth;
      if (saved.left && saved.left >= 220 && saved.left <= maxW * 0.4) {
        shell.style.setProperty('--col-left', saved.left + 'px');
      }
      if (saved.right && saved.right >= 260 && saved.right <= maxW * 0.4) {
        shell.style.setProperty('--col-right', saved.right + 'px');
      }
    } catch { }
  }

  function saveLayout() {
    const left = getTrackPx(0);
    const right = getTrackPx(4);
    const data = {};
    if (Number.isFinite(left) && left > 0) data.left = left;
    if (Number.isFinite(right) && right > 0) data.right = right;
    if (Object.keys(data).length) {
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(data)); } catch { }
    }
  }

  function initHandle(handle, prop, minW, getStart) {
    if (!handle) return;
    handle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      document.body.classList.add('resizing');
      handle.classList.add('active');
      const startX = e.clientX;
      const startVal = getStart();
      const totalW = shell.getBoundingClientRect().width;

      const onMove = (ev) => {
        const dx = ev.clientX - startX;
        const delta = prop === '--col-left' ? dx : -dx;
        shell.style.setProperty(prop, Math.max(minW, Math.min(startVal + delta, totalW * 0.4)) + 'px');
      };
      const onUp = () => {
        document.body.classList.remove('resizing');
        handle.classList.remove('active');
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        saveLayout();
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  initHandle(resizeLeft, '--col-left', 220, () => getTrackPx(0) || 280);
  initHandle(resizeRight, '--col-right', 260, () => getTrackPx(4) || 340);

  loadLayout();
})();
