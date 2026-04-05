// LLM Proxy Analytics Dashboard — app.js

const API = '/api';
const APP_VERSION = 'v1.4.0';

const NAV_GROUPS = [
  {
    label: 'Analytics',
    items: [
      {
        href: '/',
        match: '/index.html',
        label: 'Overview',
        icon: '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M3 14V9m6 5V5m6 9V2"/></svg>',
      },
      {
        href: '/conversations.html',
        label: 'Conversations',
        icon: '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2 4c0-.6.4-1 1-1h12c.6 0 1 .4 1 1v7c0 .6-.4 1-1 1h-4l-2 3v-3H3c-.6 0-1-.4-1-1V4z"/></svg>',
      },
      {
        href: '/costs.html',
        label: 'Costs',
        icon: '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="9" r="7"/><path d="M9 5v8M7 7h3a1.5 1.5 0 010 3H7m0 0h3.5a1.5 1.5 0 010 3H7"/></svg>',
      },
      {
        href: '/models.html',
        label: 'Models',
        icon: '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 2L3 5.5v7L9 16l6-3.5v-7L9 2zM3 5.5L9 9m0 0v7m0-7l6-3.5"/></svg>',
      },
    ],
  },
  {
    label: 'Monitoring',
    items: [
      {
        href: '/errors.html',
        label: 'Errors',
        icon: '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3L2 15h14L9 3z"/><path d="M9 8v3"/><circle cx="9" cy="13" r=".5" fill="currentColor"/></svg>',
      },
      {
        href: '/latency.html',
        label: 'Latency',
        icon: '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="9" r="7"/><path d="M9 5v4l2.5 1.5"/></svg>',
      },
      {
        href: '/analyzer.html',
        label: 'Analyzer',
        icon: '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3h12v12H3z"/><path d="M6 11V7m3 4V5m3 6V8"/></svg>',
      },
      {
        href: '/raw-logs.html',
        label: 'Raw Logs',
        adminOnly: true,
        icon: '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 3.5h10a1 1 0 011 1v9a1 1 0 01-1 1H4a1 1 0 01-1-1v-9a1 1 0 011-1z"/><path d="M6 6.5h6M6 9h6M6 11.5h4"/></svg>',
      },
    ],
  },
  {
    label: 'Content',
    items: [
      {
        href: '/prompts.html',
        label: 'Prompts',
        icon: '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 2h6l4 4v10c0 .6-.4 1-1 1H5c-.6 0-1-.4-1-1V3c0-.6.4-1 1-1z"/><path d="M11 2v4h4M7 10h4m-4 3h4"/></svg>',
      },
    ],
  },
];

function normalizePagePath(pathname) {
  return pathname === '/' ? '/index.html' : pathname;
}

function renderAppShell() {
  if (document.querySelector('.app-header') || document.querySelector('.app-sidebar')) return;
  const main = document.querySelector('main.app-content');
  if (!main) return;

  const shell = document.createRange().createContextualFragment(`
    <header class="app-header">
      <div class="header-brand">
        <span class="brand">LLM Proxy Analytics</span>
        <span class="header-version">${APP_VERSION}</span>
      </div>
      <div class="header-actions">
        <button id="theme-toggle" class="theme-toggle" type="button" onclick="toggleTheme()"></button>
        <div id="key-manager" class="key-manager"></div>
      </div>
    </header>
    <aside class="app-sidebar">
      <nav class="sidebar-nav">${buildNavMarkup()}</nav>
    </aside>
  `);
  document.body.insertBefore(shell, main);
}

// ── API Key Hash Management ───────────────────────────────────────────────

const KEY_STORAGE_KEY = 'llm_proxy_key_hashes';
const THEME_STORAGE_KEY = 'llm_proxy_theme';

let keyManagerExpanded = false;
let adminAccessState = { signature: '', isAdmin: false, checked: false };

function getVisibleNavGroups() {
  return NAV_GROUPS
    .map(group => ({
      ...group,
      items: group.items.filter(item => !item.adminOnly || adminAccessState.isAdmin),
    }))
    .filter(group => group.items.length > 0);
}

function buildNavMarkup() {
  const currentPath = normalizePagePath(window.location.pathname);
  return getVisibleNavGroups().map(group => {
    const items = group.items.map(item => {
      const isActive = currentPath === (item.match || item.href);
      return `
        <a href="${item.href}" class="nav-item${isActive ? ' active' : ''}">
          ${item.icon}
          ${item.label}
        </a>`;
    }).join('');

    return `
      <div class="nav-group">
        <div class="nav-group-label">${group.label}</div>
        ${items}
      </div>`;
  }).join('');
}

function renderSidebarNav() {
  const nav = document.querySelector('.sidebar-nav');
  if (!nav) return;
  nav.innerHTML = buildNavMarkup();
}

function formatHashPreview(hash) {
  if (!hash) return '—';
  return `${hash.slice(0, 8)}…${hash.slice(-4)}`;
}

function defaultKeyLabel(hash) {
  return formatHashPreview(hash);
}

function normalizeKeyRecord(entry) {
  if (!entry || typeof entry !== 'object') return null;
  const hash = typeof entry.hash === 'string' ? entry.hash.trim().toLowerCase() : '';
  if (!/^[0-9a-f]{32}$/i.test(hash)) return null;
  const label = typeof entry.label === 'string' && entry.label.trim()
    ? entry.label.trim()
    : defaultKeyLabel(hash);
  return {
    hash,
    label,
    addedAt: typeof entry.addedAt === 'string' && entry.addedAt ? entry.addedAt : new Date().toISOString(),
    active: entry.active !== false,
  };
}

function normalizeKeyRecords(entries) {
  if (!Array.isArray(entries)) return [];
  const deduped = new Map();
  entries.forEach((entry) => {
    const normalized = normalizeKeyRecord(entry);
    if (!normalized) return;
    deduped.set(normalized.hash, normalized);
  });
  return Array.from(deduped.values());
}

function getStoredKeyHashes() {
  try {
    const raw = localStorage.getItem(KEY_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    const normalized = normalizeKeyRecords(parsed);
    if (JSON.stringify(parsed) !== JSON.stringify(normalized)) {
      localStorage.setItem(KEY_STORAGE_KEY, JSON.stringify(normalized));
    }
    return normalized;
  } catch { return []; }
}

function saveKeyHashes(hashes) {
  const normalized = normalizeKeyRecords(hashes);
  localStorage.setItem(KEY_STORAGE_KEY, JSON.stringify(normalized));
  return normalized;
}

function upsertKeyHash(hash, label) {
  const hashes = getStoredKeyHashes();
  const normalizedHash = hash.trim().toLowerCase();
  const normalizedLabel = label && label.trim() ? label.trim() : defaultKeyLabel(normalizedHash);
  const existing = hashes.find((item) => item.hash === normalizedHash);
  if (existing) {
    existing.label = normalizedLabel || existing.label;
    existing.active = true;
    saveKeyHashes(hashes);
    return { status: 'updated', entry: existing };
  }
  const entry = {
    hash: normalizedHash,
    label: normalizedLabel,
    addedAt: new Date().toISOString(),
    active: true,
  };
  hashes.push(entry);
  saveKeyHashes(hashes);
  return { status: 'added', entry };
}

function updateKeyLabel(hash, label) {
  const hashes = getStoredKeyHashes();
  const entry = hashes.find((item) => item.hash === hash);
  if (!entry) return false;
  entry.label = label && label.trim() ? label.trim() : defaultKeyLabel(hash);
  saveKeyHashes(hashes);
  return true;
}

function setKeyHashActive(hash, active) {
  const hashes = getStoredKeyHashes();
  const entry = hashes.find((item) => item.hash === hash);
  if (!entry) return false;
  entry.active = Boolean(active);
  saveKeyHashes(hashes);
  return true;
}

function setAllKeyHashesActive(active) {
  const hashes = getStoredKeyHashes().map((item) => ({ ...item, active: Boolean(active) }));
  saveKeyHashes(hashes);
  return hashes;
}

function removeKeyHash(hash) {
  const hashes = getStoredKeyHashes().filter(h => h.hash !== hash);
  saveKeyHashes(hashes);
}

function hasStoredKeyHashes() {
  return getStoredKeyHashes().length > 0;
}

async function computeKeyHash(apiKey) {
  const normalized = apiKey.trim();
  if (!normalized) return '';

  const subtle = globalThis.crypto && globalThis.crypto.subtle;
  if (subtle) {
    const data = new TextEncoder().encode(normalized);
    const hashBuffer = await subtle.digest('SHA-256', data);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    const fullHex = hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
    return fullHex.slice(0, 32);
  }

  return sha256Hex(normalized).slice(0, 32);
}

function sha256Hex(input) {
  const bytes = new TextEncoder().encode(input);
  const bitLength = bytes.length * 8;
  const paddedLength = (((bytes.length + 9 + 63) >> 6) << 6);
  const padded = new Uint8Array(paddedLength);
  padded.set(bytes);
  padded[bytes.length] = 0x80;

  const view = new DataView(padded.buffer);
  const highBits = Math.floor(bitLength / 0x100000000);
  const lowBits = bitLength >>> 0;
  view.setUint32(paddedLength - 8, highBits, false);
  view.setUint32(paddedLength - 4, lowBits, false);

  const words = new Uint32Array(64);
  const state = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]);
  const constants = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
    0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
    0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
    0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ];

  for (let offset = 0; offset < paddedLength; offset += 64) {
    for (let i = 0; i < 16; i += 1) {
      words[i] = view.getUint32(offset + i * 4, false);
    }
    for (let i = 16; i < 64; i += 1) {
      const s0 = rightRotate(words[i - 15], 7) ^ rightRotate(words[i - 15], 18) ^ (words[i - 15] >>> 3);
      const s1 = rightRotate(words[i - 2], 17) ^ rightRotate(words[i - 2], 19) ^ (words[i - 2] >>> 10);
      words[i] = add32(words[i - 16], s0, words[i - 7], s1);
    }

    let [a, b, c, d, e, f, g, h] = state;

    for (let i = 0; i < 64; i += 1) {
      const s1 = rightRotate(e, 6) ^ rightRotate(e, 11) ^ rightRotate(e, 25);
      const ch = (e & f) ^ (~e & g);
      const temp1 = add32(h, s1, ch, constants[i], words[i]);
      const s0 = rightRotate(a, 2) ^ rightRotate(a, 13) ^ rightRotate(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = add32(s0, maj);

      h = g;
      g = f;
      f = e;
      e = add32(d, temp1);
      d = c;
      c = b;
      b = a;
      a = add32(temp1, temp2);
    }

    state[0] = add32(state[0], a);
    state[1] = add32(state[1], b);
    state[2] = add32(state[2], c);
    state[3] = add32(state[3], d);
    state[4] = add32(state[4], e);
    state[5] = add32(state[5], f);
    state[6] = add32(state[6], g);
    state[7] = add32(state[7], h);
  }

  return Array.from(state)
    .map((word) => word.toString(16).padStart(8, '0'))
    .join('');
}

function rightRotate(value, shift) {
  return (value >>> shift) | (value << (32 - shift));
}

function add32(...values) {
  return values.reduce((sum, value) => (sum + value) >>> 0, 0);
}

function getActiveKeyHashes() {
  return getStoredKeyHashes()
    .filter((item) => item.active)
    .map((item) => item.hash);
}

function buildAuthQuery() {
  const hashes = getActiveKeyHashes();
  if (hashes.length === 0) return '';
  return 'key_hashes=' + hashes.map(encodeURIComponent).join(',');
}

function appendAuthToUrl(url) {
  const authQuery = buildAuthQuery();
  if (!authQuery) return url;
  const separator = url.includes('?') ? '&' : '?';
  return url + separator + authQuery;
}

function getPreferredTheme() {
  const storedTheme = localStorage.getItem(THEME_STORAGE_KEY);
  if (storedTheme === 'light' || storedTheme === 'dark') return storedTheme;
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function syncThemeToggle() {
  const button = document.getElementById('theme-toggle');
  if (!button) return;
  const theme = document.documentElement.dataset.theme || 'light';
  button.textContent = theme === 'dark' ? '切到亮色' : '切到暗色';
  button.setAttribute('aria-label', theme === 'dark' ? '切换到亮色主题' : '切换到暗色主题');
}

function applyTheme(theme, { persist = true } = {}) {
  const resolvedTheme = theme === 'dark' ? 'dark' : 'light';
  document.documentElement.dataset.theme = resolvedTheme;
  if (persist) {
    localStorage.setItem(THEME_STORAGE_KEY, resolvedTheme);
  }
  syncThemeToggle();
}

function toggleTheme() {
  const currentTheme = document.documentElement.dataset.theme || getPreferredTheme();
  applyTheme(currentTheme === 'dark' ? 'light' : 'dark');
}

applyTheme(getPreferredTheme(), { persist: false });

// ── Toast notification system ─────────────────────────────────────────────

let _toastContainer = null;

function _getToastContainer() {
  if (!_toastContainer) {
    _toastContainer = document.createElement('div');
    _toastContainer.id = 'toast-container';
    document.body.appendChild(_toastContainer);
  }
  return _toastContainer;
}

function showToast(message, type = 'info', duration = 4000) {
  const container = _getToastContainer();
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  // Trigger reflow then animate in
  void toast.offsetWidth;
  toast.classList.add('toast-visible');
  setTimeout(() => {
    toast.classList.remove('toast-visible');
    toast.addEventListener('transitionend', () => toast.remove(), { once: true });
  }, duration);
}

async function requestJSON(url, options = {}) {
  if (hasStoredKeyHashes() && getActiveKeyHashes().length === 0) {
    throw Object.assign(new Error('No active key hashes selected'), { status: 401 });
  }
  const authedUrl = appendAuthToUrl(url);
  const headers = { ...(options.headers || {}) };
  if (options.body != null && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }
  const r = await fetch(authedUrl, { ...options, headers });
  if (r.status === 401) {
    if (hasStoredKeyHashes()) {
      keyManagerExpanded = true;
      renderKeyManager();
      showToast('请先激活至少一个 hash', 'warning');
    } else {
      showKeyModal('请添加 API Key 以访问数据');
    }
    throw new Error('Unauthorized — no key hashes');
  }
  if (!r.ok) {
    const error = new Error(`${r.status} ${r.statusText}`);
    error.status = r.status;
    throw error;
  }
  return r.json();
}

async function fetchJSON(url) {
  return requestJSON(url);
}

async function fetchOptionalJSON(url, allowedStatuses = [403]) {
  try {
    return await requestJSON(url);
  } catch (error) {
    if (allowedStatuses.includes(error.status)) {
      return null;
    }
    throw error;
  }
}

async function refreshAdminAccessState({ force = false } = {}) {
  const activeHashes = getActiveKeyHashes();
  const signature = activeHashes.slice().sort().join(',');
  if (!activeHashes.length) {
    const changed = adminAccessState.isAdmin || adminAccessState.checked;
    adminAccessState = { signature: '', isAdmin: false, checked: false };
    if (changed) renderSidebarNav();
    return false;
  }
  if (!force && adminAccessState.checked && adminAccessState.signature === signature) {
    return adminAccessState.isAdmin;
  }

  const adminStatus = await fetchOptionalJSON(`${API}/admin/status`);
  const isAdmin = Boolean(adminStatus);
  const changed = !adminAccessState.checked
    || adminAccessState.signature !== signature
    || adminAccessState.isAdmin !== isAdmin;
  adminAccessState = { signature, isAdmin, checked: true };
  if (changed) renderSidebarNav();
  return isAdmin;
}

function fmt(n, decimals = 0) {
  if (n == null) return '—';
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: decimals });
}

function fmtPercent(value, decimals = 1) {
  if (value == null) return '—';
  return `${(Number(value) * 100).toFixed(decimals)}%`;
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function truncateText(text, max = 60) {
  if (!text) return '';
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function renderPromptCompletionPreview(row) {
  const user = (row.user_prompt_preview || '').trim();
  const assistant = (row.assistant_response_preview || '').trim();
  if (!user && !assistant) {
    if ((row.finish_reason || '').toLowerCase() === 'tool_calls') {
      return '<span class="muted">tool calls / no text</span>';
    }
    return '— / —';
  }

  const userView = truncateText(user, 52) || '—';
  const assistantView = truncateText(assistant, 52) || '—';
  const title = `${user || '—'} / ${assistant || '—'}`;
  return `<span title="${escapeHtml(title)}">${escapeHtml(userView)} / ${escapeHtml(assistantView)}</span>`;
}

// ── Overview page ─────────────────────────────────────────────────────────

let overviewDays = 7;
let trendChartInstance = null;
let modelChartInstance = null;
let tokenChartInstance = null;
let overviewPollHandle = null;

async function loadOverview() {
  try {
    const [summary, daily, modelUsage, adminStatus] = await Promise.all([
      fetchJSON(`${API}/overview`),
      fetchJSON(`${API}/overview/daily?days=${overviewDays}`),
      fetchJSON(`${API}/models/usage`),
      fetchOptionalJSON(`${API}/admin/status`),
    ]);

    adminAccessState = {
      signature: getActiveKeyHashes().slice().sort().join(','),
      isAdmin: Boolean(adminStatus),
      checked: true,
    };
    renderSidebarNav();

    document.getElementById('total-requests').textContent = fmt(summary.total_requests);
    document.getElementById('success-rate').textContent =
      summary.success_rate != null ? (summary.success_rate * 100).toFixed(1) + '%' : '—';
    document.getElementById('total-cost').textContent =
      summary.total_cost_usd != null ? '$' + Number(summary.total_cost_usd).toFixed(4) : '—';
    document.getElementById('avg-latency').textContent =
      summary.avg_duration_ms != null ? fmt(summary.avg_duration_ms, 1) : '—';
    document.getElementById('total-tokens').textContent = fmt(summary.total_tokens);
    document.getElementById('active-models').textContent = fmt(modelUsage.length);

    renderTrendChart(daily);
    renderOverviewModelChart(modelUsage);
    renderOverviewTokenChart(daily);
    if (adminStatus) {
      renderDatabaseObservability(adminStatus);
      scheduleOverviewRefresh(adminStatus.worker);
    } else {
      renderObservabilityRestricted();
      scheduleOverviewRefresh(null);
    }
  } catch (e) {
    console.error('Overview load error:', e);
  }
}

function scheduleOverviewRefresh(worker) {
  if (overviewPollHandle) {
    clearTimeout(overviewPollHandle);
    overviewPollHandle = null;
  }
  if (worker && worker.is_running) {
    overviewPollHandle = setTimeout(() => {
      loadOverview();
    }, 2500);
  }
}

function renderTrendChart(daily) {
  const ctx = document.getElementById('trend-chart');
  if (!ctx) return;
  if (trendChartInstance) trendChartInstance.destroy();
  trendChartInstance = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: daily.map(d => d.date),
      datasets: [
        {
          label: 'Requests',
          data: daily.map(d => d.requests),
          backgroundColor: 'rgba(126,184,247,0.7)',
          yAxisID: 'y',
        },
        {
          label: 'Cost (USD)',
          data: daily.map(d => d.cost_usd),
          type: 'line',
          borderColor: '#e67e22',
          backgroundColor: 'transparent',
          yAxisID: 'y1',
          tension: 0.3,
          pointRadius: 3,
        },
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      scales: {
        y: { position: 'left', title: { display: true, text: 'Requests' } },
        y1: {
          position: 'right',
          title: { display: true, text: 'Cost (USD)' },
          grid: { drawOnChartArea: false },
        },
      },
    },
  });
}

function renderOverviewModelChart(modelUsage) {
  const ctx = document.getElementById('overview-model-chart');
  if (!ctx) return;
  if (modelChartInstance) modelChartInstance.destroy();
  const colors = [
    '#4f46e5', '#0ea5e9', '#10b981', '#f59e0b', '#ef4444',
    '#8b5cf6', '#ec4899', '#06b6d4', '#84cc16', '#f97316',
  ];
  modelChartInstance = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: modelUsage.map(m => m.model || 'unknown'),
      datasets: [{
        data: modelUsage.map(m => m.request_count || 0),
        backgroundColor: colors.slice(0, modelUsage.length),
      }],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { position: 'right', labels: { boxWidth: 12, font: { size: 11 } } },
      },
    },
  });
}

function renderOverviewTokenChart(daily) {
  const ctx = document.getElementById('overview-token-chart');
  if (!ctx) return;
  if (tokenChartInstance) tokenChartInstance.destroy();
  tokenChartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels: daily.map(d => d.date),
      datasets: [{
        label: 'Tokens',
        data: daily.map(d => {
          const row = d;
          return (row.total_tokens || 0);
        }),
        borderColor: '#8b5cf6',
        backgroundColor: 'rgba(139,92,246,0.1)',
        tension: 0.3,
        fill: true,
      }],
    },
    options: { responsive: true },
  });
}

function initOverviewTimeRange() {
  document.querySelectorAll('.range-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.range-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      overviewDays = parseInt(btn.dataset.range, 10);
      loadOverview();
    });
  });
}

function setIfPresent(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function statusClass(statusValue) {
  switch (statusValue) {
    case 'running':
      return 'status-running';
    case 'stopping':
      return 'status-stopping';
    case 'completed':
      return 'status-completed';
    case 'stopped':
      return 'status-stopped';
    case 'failed':
      return 'status-failed';
    default:
      return 'status-idle';
  }
}

function formatDateTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function renderMetricTable(targetId, rows) {
  const tbody = document.getElementById(targetId);
  if (!tbody) return;
  tbody.innerHTML = rows.map(([label, value]) => `
    <tr>
      <th>${escapeHtml(label)}</th>
      <td>${escapeHtml(String(value ?? '—'))}</td>
    </tr>
  `).join('');
}

function renderWorkerStatus(worker) {
  if (!worker) return;
  const pill = document.getElementById('worker-status-pill');
  if (pill) {
    pill.textContent = worker.status || 'idle';
    pill.className = `status-pill ${statusClass(worker.status)}`;
  }

  const progressValue = worker.progress != null ? `${Math.round(Number(worker.progress) * 100)}%` : '0%';
  setIfPresent('worker-progress-label', progressValue);
  setIfPresent('worker-mode', worker.mode || '—');
  setIfPresent('worker-processed', fmt(worker.processed_rows || 0));
  setIfPresent('worker-total', fmt(worker.total_rows || 0));
  setIfPresent('worker-remaining', fmt(worker.remaining_rows || 0));
  setIfPresent('worker-current-seq', fmt(worker.current_seq || 0));
  setIfPresent('worker-target-seq', fmt(worker.target_seq || 0));
  setIfPresent('worker-last-timestamp', formatDateTime(worker.last_timestamp));
  setIfPresent('worker-started-at', formatDateTime(worker.started_at));
  setIfPresent('worker-finished-at', formatDateTime(worker.finished_at));

  const bar = document.getElementById('worker-progress-bar');
  if (bar) {
    const width = worker.progress != null ? Math.max(4, Math.round(Number(worker.progress) * 100)) : 0;
    bar.style.width = `${worker.total_rows > 0 || worker.is_running ? width : 0}%`;
    bar.className = `progress-fill ${statusClass(worker.status)}`;
  }

  const error = document.getElementById('worker-error');
  if (error) {
    error.hidden = !worker.error;
    error.textContent = worker.error || '';
  }
}

function renderDatabaseObservability(status) {
  if (!status) return;
  const rawDb = status.raw_db || {};
  const analyticsDb = status.analytics_db || {};
  const worker = status.worker || {};

  setIfPresent('obs-raw-total', fmt(rawDb.total_rows || 0));
  setIfPresent('obs-raw-finalized', fmt(rawDb.finalized_rows || 0));
  setIfPresent('obs-raw-backlog', fmt(rawDb.backlog_rows || 0));
  setIfPresent('obs-analytics-conversations', fmt(analyticsDb.conversation_count || 0));
  setIfPresent('obs-analytics-templates', fmt(analyticsDb.template_count || 0));
  setIfPresent('obs-worker-status', worker.status || 'idle');

  const obsWorkerStatus = document.getElementById('obs-worker-status');
  if (obsWorkerStatus) {
    obsWorkerStatus.className = `card-value card-value-status ${statusClass(worker.status)}`;
  }

  renderMetricTable('raw-db-metrics', [
    ['Path', rawDb.path || '—'],
    ['File Size', formatBytes(rawDb.file_size_bytes || 0)],
    ['Finalized Rows', fmt(rawDb.finalized_rows || 0)],
    ['Pending Rows', fmt(rawDb.pending_rows || 0)],
    ['Backlog Rows', fmt(rawDb.backlog_rows || 0)],
    ['Error Rows', fmt(rawDb.error_rows || 0)],
    ['Latest Timestamp', formatDateTime(rawDb.last_timestamp)],
    ['Avg Duration', rawDb.avg_duration_ms != null ? `${fmt(rawDb.avg_duration_ms, 1)} ms` : '—'],
    ['Payload Volume', formatBytes(rawDb.payload_bytes || 0)],
  ]);

  renderMetricTable('analytics-db-metrics', [
    ['Path', analyticsDb.path || '—'],
    ['File Size', formatBytes(analyticsDb.file_size_bytes || 0)],
    ['Conversations', fmt(analyticsDb.conversation_count || 0)],
    ['Prompt Templates', fmt(analyticsDb.template_count || 0)],
    ['Daily Stats Rows', fmt(analyticsDb.daily_stats_rows || 0)],
    ['Watermark Seq', fmt(analyticsDb.watermark_seq || 0)],
    ['Records Processed', fmt(analyticsDb.records_processed || 0)],
    ['Last Sync', formatDateTime(analyticsDb.last_updated_at)],
    ['Latest Analytics Timestamp', formatDateTime(analyticsDb.latest_conversation_timestamp)],
  ]);

  renderWorkerStatus(worker);
}

function renderObservabilityRestricted() {
  setIfPresent('obs-raw-total', 'Restricted');
  setIfPresent('obs-raw-finalized', 'Restricted');
  setIfPresent('obs-raw-backlog', 'Restricted');
  setIfPresent('obs-analytics-conversations', 'Restricted');
  setIfPresent('obs-analytics-templates', 'Restricted');
  setIfPresent('obs-worker-status', 'Admin only');

  renderMetricTable('raw-db-metrics', [
    ['Access', 'Admin only'],
  ]);
  renderMetricTable('analytics-db-metrics', [
    ['Access', 'Admin only'],
  ]);

  const obsWorkerStatus = document.getElementById('obs-worker-status');
  if (obsWorkerStatus) {
    obsWorkerStatus.className = 'card-value card-value-status';
  }
}

let analyzerPollHandle = null;
let analyzerAutoRefresh = true;
let _analyzerPrevStatus = null;

function getAnalyzerRequestPayload(modeOverride) {
  const mode = modeOverride || document.querySelector('[name="analyzer-mode"]:checked')?.value || 'incremental';
  const since = document.getElementById('analyzer-since')?.value || null;
  const until = document.getElementById('analyzer-until')?.value || null;
  return {
    mode,
    since: since || null,
    until: until || null,
  };
}

function setAnalyzerNotice(message, variant = 'info') {
  const notice = document.getElementById('analyzer-notice');
  if (!notice) return;
  notice.hidden = !message;
  notice.textContent = message || '';
  notice.className = `inline-notice ${variant}`;
}

function startAnalyzerPolling() {
  if (!analyzerAutoRefresh || analyzerPollHandle) return;
  analyzerPollHandle = setInterval(refreshAnalyzerStatus, 2000);
}

function stopAnalyzerPolling() {
  if (!analyzerPollHandle) return;
  clearInterval(analyzerPollHandle);
  analyzerPollHandle = null;
}

function toDateTimeLocalValue(date) {
  const local = new Date(date.getTime() - (date.getTimezoneOffset() * 60000));
  return local.toISOString().slice(0, 16);
}

function applyAnalyzerRangePreset(hours) {
  const until = new Date();
  const since = new Date(until.getTime() - (hours * 60 * 60 * 1000));
  const sinceInput = document.getElementById('analyzer-since');
  const untilInput = document.getElementById('analyzer-until');
  if (sinceInput) sinceInput.value = toDateTimeLocalValue(since);
  if (untilInput) untilInput.value = toDateTimeLocalValue(until);
  const rangeRadio = document.querySelector('[name="analyzer-mode"][value="range"]');
  if (rangeRadio) rangeRadio.checked = true;
}

function clearAnalyzerRange() {
  const sinceInput = document.getElementById('analyzer-since');
  const untilInput = document.getElementById('analyzer-until');
  if (sinceInput) sinceInput.value = '';
  if (untilInput) untilInput.value = '';
}

function updateAnalyzerButtons(worker) {
  const isRunning = !!worker?.is_running;
  document.querySelectorAll('[data-sync-mode]').forEach((button) => {
    button.disabled = isRunning;
  });
  const stopButton = document.getElementById('analyzer-stop-btn');
  if (stopButton) {
    stopButton.disabled = !isRunning;
  }
}

function renderAnalyzerHistory(history) {
  const tbody = document.getElementById('analyzer-history-tbody');
  if (!tbody) return;
  if (!history || history.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="muted">No sync history yet.</td></tr>';
    return;
  }

  tbody.innerHTML = history.map((row) => {
    const windowText = row.since || row.until
      ? `${row.since || '—'} -> ${row.until || 'now'}`
      : '—';
    const processed = `${fmt(row.processed_rows || 0)} / ${fmt(row.total_rows || 0)}`;
    const retryable = row.status === 'failed' || row.status === 'stopped';
    const actionCell = retryable
      ? `<button type="button" class="ghost-action retry-btn" data-job-id="${row.job_id}">Retry</button>`
      : '';
    return `
      <tr>
        <td>#${row.job_id}</td>
        <td><span class="status-pill ${statusClass(row.status)}">${escapeHtml(row.status || 'idle')}</span></td>
        <td>${escapeHtml(row.mode || '—')}</td>
        <td>${escapeHtml(processed)}</td>
        <td>${escapeHtml(windowText)}</td>
        <td>${escapeHtml(formatDateTime(row.started_at))}</td>
        <td>${escapeHtml(formatDateTime(row.finished_at))}</td>
        <td>${actionCell}</td>
      </tr>
    `;
  }).join('');
}

async function refreshAnalyzerStatus() {
  try {
    const [status, history] = await Promise.all([
      fetchOptionalJSON(`${API}/admin/status`),
      fetchOptionalJSON(`${API}/admin/analyzer/history?limit=15`),
    ]);
    if (!status || !history) {
      renderObservabilityRestricted();
      renderAnalyzerHistory([]);
      updateAnalyzerButtons(null);
      setAnalyzerNotice('当前 key 无管理权限，Analyzer 管理功能仅 admin 可用。', 'warning');
      stopAnalyzerPolling();
      return;
    }
    renderDatabaseObservability(status);
    renderAnalyzerHistory(history);
    updateAnalyzerButtons(status.worker);

    // Detect terminal status transitions and emit toast
    const newStatus = status.worker?.status;
    if (_analyzerPrevStatus && _analyzerPrevStatus !== newStatus) {
      const wasActive = _analyzerPrevStatus === 'running' || _analyzerPrevStatus === 'stopping';
      if (wasActive) {
        const jobId = status.worker?.job_id ? ` #${status.worker.job_id}` : '';
        const processed = status.worker?.processed_rows != null ? ` (${fmt(status.worker.processed_rows)} rows)` : '';
        if (newStatus === 'completed') {
          showToast(`同步任务${jobId} 已完成${processed}`, 'success');
        } else if (newStatus === 'failed') {
          showToast(`同步任务${jobId} 失败：${status.worker.error || '未知错误'}`, 'error', 7000);
        } else if (newStatus === 'stopped') {
          showToast(`同步任务${jobId} 已停止${processed}`, 'info');
        }
      }
    }
    _analyzerPrevStatus = newStatus;

    if (status.worker && !status.worker.is_running) {
      stopAnalyzerPolling();
    }
  } catch (e) {
    console.error('Analyzer status load error:', e);
    setAnalyzerNotice(`状态刷新失败: ${e.message}`, 'error');
  }
}

async function startAnalyzerSync(modeOverride) {
  const payload = getAnalyzerRequestPayload(modeOverride);
  if ((payload.since || payload.until) && payload.mode !== 'range') {
    payload.mode = 'range';
  }

  try {
    const response = await requestJSON(`${API}/admin/analyzer/sync`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    showToast(`已启动 ${response.job.mode || payload.mode} 同步任务 #${response.job.job_id || '—'}`, 'success');
    setAnalyzerNotice('');
    await refreshAnalyzerStatus();
    startAnalyzerPolling();
  } catch (e) {
    console.error('Analyzer sync start error:', e);
    showToast(`启动同步失败: ${e.message}`, 'error');
    setAnalyzerNotice(`启动失败: ${e.message}`, 'error');
  }
}

async function stopAnalyzerSync() {
  try {
    const response = await requestJSON(`${API}/admin/analyzer/stop`, {
      method: 'POST',
    });
    showToast(`正在停止任务 #${response.job.job_id || '—'}`, 'info');
    setAnalyzerNotice('');
    await refreshAnalyzerStatus();
    startAnalyzerPolling();
  } catch (e) {
    console.error('Analyzer sync stop error:', e);
    showToast(`停止失败: ${e.message}`, 'error');
    setAnalyzerNotice(`停止失败: ${e.message}`, 'error');
  }
}

async function retryAnalyzerSync(jobId) {
  try {
    const response = await requestJSON(`${API}/admin/analyzer/retry/${jobId}`, {
      method: 'POST',
    });
    showToast(`已重试任务 #${jobId}，新任务 #${response.job.job_id || '—'}`, 'success');
    setAnalyzerNotice('');
    await refreshAnalyzerStatus();
    startAnalyzerPolling();
  } catch (e) {
    console.error('Analyzer sync retry error:', e);
    showToast(`重试失败: ${e.message}`, 'error');
  }
}

function bindAnalyzerControls() {
  document.querySelectorAll('[data-sync-mode]').forEach((button) => {
    button.addEventListener('click', () => {
      startAnalyzerSync(button.dataset.syncMode || 'incremental');
    });
  });
  document.querySelectorAll('[data-range-hours]').forEach((button) => {
    button.addEventListener('click', () => {
      applyAnalyzerRangePreset(Number(button.dataset.rangeHours || 0));
    });
  });
  document.querySelectorAll('[data-range-clear]').forEach((button) => {
    button.addEventListener('click', () => {
      clearAnalyzerRange();
    });
  });
  const stopButton = document.getElementById('analyzer-stop-btn');
  if (stopButton) {
    stopButton.addEventListener('click', () => {
      stopAnalyzerSync();
    });
  }
  const refreshToggle = document.getElementById('analyzer-auto-refresh');
  if (refreshToggle) {
    analyzerAutoRefresh = refreshToggle.checked;
    refreshToggle.addEventListener('change', () => {
      analyzerAutoRefresh = refreshToggle.checked;
      if (analyzerAutoRefresh) {
        const currentStatus = document.getElementById('worker-status-pill')?.textContent;
        if (currentStatus === 'running' || currentStatus === 'stopping') {
          startAnalyzerPolling();
        }
      } else {
        stopAnalyzerPolling();
      }
    });
  }
  // Event delegation for retry buttons rendered inside the history tbody
  const historyTbody = document.getElementById('analyzer-history-tbody');
  if (historyTbody) {
    historyTbody.addEventListener('click', (evt) => {
      const btn = evt.target.closest('.retry-btn');
      if (btn) retryAnalyzerSync(Number(btn.dataset.jobId));
    });
  }
  // Backup button
  const backupBtn = document.getElementById('backup-create-btn');
  if (backupBtn) {
    backupBtn.addEventListener('click', triggerBackup);
  }
}

// ── Backup helpers ────────────────────────────────────────────────────────

function renderBackupList(backups) {
  const tbody = document.getElementById('backup-list-tbody');
  if (!tbody) return;
  if (!backups || backups.length === 0) {
    tbody.innerHTML = '<tr><td colspan="3" class="muted">No backups found.</td></tr>';
    return;
  }
  tbody.innerHTML = backups.map((b) => `
    <tr>
      <td>${escapeHtml(b.name)}</td>
      <td>${formatBytes(b.size_bytes)}</td>
      <td>${escapeHtml(formatDateTime(b.modified_at))}</td>
    </tr>
  `).join('');
}

async function loadBackupList() {
  try {
    const backups = await fetchJSON(`${API}/admin/backups`);
    renderBackupList(backups);
  } catch (e) {
    console.error('Failed to load backups:', e);
  }
}

async function triggerBackup() {
  const btn = document.getElementById('backup-create-btn');
  if (btn) btn.disabled = true;
  try {
    const result = await requestJSON(`${API}/admin/backup`, { method: 'POST' });
    const count = result.files?.length ?? 0;
    showToast(`备份完成：创建了 ${count} 个文件`, 'success');
    await loadBackupList();
  } catch (e) {
    console.error('Backup failed:', e);
    showToast(`备份失败: ${e.message}`, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function loadAnalyzerPage() {
  bindAnalyzerControls();
  await refreshAnalyzerStatus();
  await loadBackupList();
  const currentStatus = document.getElementById('worker-status-pill')?.textContent;
  if ((currentStatus === 'running' || currentStatus === 'stopping') && analyzerAutoRefresh) {
    startAnalyzerPolling();
  }
}

// ── Conversations page ────────────────────────────────────────────────────

let currentPage = 1;
let selectedConversationId = null;
let currentConversationRows = [];

function maybeJSON(value) {
  if (value == null) return null;
  if (typeof value === 'object') return value;
  if (typeof value !== 'string') return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function prettyJSONOrText(value) {
  if (value == null || value === '') return '—';
  const parsed = maybeJSON(value);
  if (parsed != null) return JSON.stringify(parsed, null, 2);
  if (typeof value === 'string') return value;
  return String(value);
}

function parseField(raw) {
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw);
    } catch {
      return raw;
    }
  }
  return raw;
}

function hasContent(value) {
  if (value == null) return false;
  if (typeof value === 'string') return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value).length > 0;
  return true;
}

function extractMessagesFromRequestBody(requestBody) {
  const parsed = parseField(requestBody);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
  const messages = parsed.messages;
  if (Array.isArray(messages) && messages.length > 0) return messages;
  return null;
}

function normalizeMessageContent(content) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    const texts = content
      .map((item) => {
        if (typeof item === 'string') return item;
        if (item && typeof item === 'object') return item.text || item.content || '';
        return '';
      })
      .filter(Boolean);
    return texts.join(' ').trim() || null;
  }
  if (content && typeof content === 'object') {
    return content.text || content.content || null;
  }
  return null;
}

function extractSystemAndUserPrompt(messages) {
  if (!Array.isArray(messages)) return { systemPrompt: null, userPrompt: null };
  let systemPrompt = null;
  let userPrompt = null;
  messages.forEach((msg) => {
    if (!msg || typeof msg !== 'object') return;
    const role = msg.role;
    const text = normalizeMessageContent(msg.content);
    if (!text) return;
    if (role === 'system') systemPrompt = text;
    if (role === 'user') userPrompt = text;
  });
  return { systemPrompt, userPrompt };
}

function parseSSEChunks(text) {
  if (typeof text !== 'string') return [];
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trim())
    .filter((payload) => payload && payload !== '[DONE]')
    .map((payload) => {
      try {
        return JSON.parse(payload);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

function extractAssistantFromResponseBody(responseBody) {
  const parsed = parseField(responseBody);
  if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
    const choices = parsed.choices;
    if (Array.isArray(choices) && choices.length > 0) {
      const first = choices[0] || {};
      const message = first.message || {};
      const fromMessage = normalizeMessageContent(message.content);
      if (fromMessage) return fromMessage;
      if (typeof first.text === 'string' && first.text.trim()) return first.text;
    }
  }

  if (typeof responseBody === 'string' && responseBody.trim().startsWith('data:')) {
    const chunks = parseSSEChunks(responseBody);
    const parts = [];
    chunks.forEach((chunk) => {
      const choices = chunk.choices;
      if (!Array.isArray(choices) || choices.length === 0) return;
      const delta = (choices[0] || {}).delta || {};
      const content = normalizeMessageContent(delta.content);
      const reasoning = normalizeMessageContent(delta.reasoning_content || delta.reasoning);
      if (content) parts.push(content);
      if (reasoning) parts.push(reasoning);
    });
    if (parts.length > 0) return parts.join('');
  }

  return null;
}

function extractUsageFromResponseBody(responseBody) {
  const readUsage = (obj) => {
    if (!obj || typeof obj !== 'object') return null;
    const usage = obj.usage;
    if (!usage || typeof usage !== 'object') return null;
    const prompt = usage.prompt_tokens ?? usage.input_tokens ?? null;
    const completion = usage.completion_tokens ?? usage.output_tokens ?? null;
    const total = usage.total_tokens ?? ((prompt != null && completion != null) ? (prompt + completion) : null);
    if (prompt == null && completion == null && total == null) return null;
    // Extract cache tokens
    const cacheRead = usage.prompt_cache_read_tokens ?? usage.cache_read_input_tokens
      ?? usage.prompt_tokens_details?.cached_tokens ?? null;
    const cacheCreation = usage.prompt_cache_creation_tokens ?? usage.cache_creation_input_tokens ?? null;
    // Extract reasoning tokens
    const reasoning = usage.reasoning_tokens ?? usage.completion_tokens_details?.reasoning_tokens ?? null;
    return { prompt, completion, total, cacheRead, cacheCreation, reasoning };
  };

  const parsed = parseField(responseBody);
  const direct = readUsage(parsed);
  if (direct) return direct;

  if (typeof responseBody === 'string' && responseBody.trim().startsWith('data:')) {
    const chunks = parseSSEChunks(responseBody);
    let last = null;
    chunks.forEach((chunk) => {
      const usage = readUsage(chunk);
      if (usage) last = usage;
    });
    return last;
  }

  return null;
}

function extractFullToolDefinitions(requestBody) {
  const parsed = parseField(requestBody);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return [];
  const tools = parsed.tools || parsed.functions;
  if (!Array.isArray(tools) || tools.length === 0) return [];
  return tools.map((tool) => {
    if (tool && typeof tool === 'object') {
      if (tool.function && typeof tool.function === 'object') {
        return tool.function;
      }
      return tool;
    }
    return { name: 'unknown' };
  });
}

function extractToolsFromRequestBody(requestBody) {
  const parsed = parseField(requestBody);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
  const tools = parsed.tools || parsed.functions;
  if (!Array.isArray(tools) || tools.length === 0) return null;
  return tools.map((tool) => {
    if (tool && typeof tool === 'object') {
      if (tool.function && typeof tool.function === 'object' && typeof tool.function.name === 'string') {
        return tool.function.name;
      }
      if (typeof tool.name === 'string') return tool.name;
    }
    return 'unknown';
  });
}

function promptOptimizationHints(detail) {
  const hints = [];
  const promptTokens = Number(detail.prompt_tokens || 0);
  const completionTokens = Number(detail.completion_tokens || 0);
  const totalTokens = Number(detail.total_tokens || 0);
  const userPrompt = detail.user_prompt || '';
  const systemPrompt = detail.system_prompt || '';
  const assistantResponse = detail.assistant_response || '';
  const durationMs = Number(detail.duration_ms || 0);
  const extUsage = detail._extUsage || {};
  const cacheRead = Number(extUsage.cacheRead || 0);

  if (systemPrompt.length > 2800) {
    hints.push('System prompt 偏长，建议拆分固定政策与动态上下文，减少重复 token。');
  }
  if (userPrompt.length < 30 && totalTokens > 1200) {
    hints.push('用户输入较短但总 token 偏高，可能上下文注入过多。可考虑摘要历史消息。');
  }
  if (promptTokens > 0 && completionTokens / promptTokens < 0.2) {
    hints.push('Completion/Prompt 比例偏低，可能提示词约束过强，可尝试放宽输出格式。');
  }
  if ((detail.finish_reason || '').toLowerCase() === 'length') {
    hints.push('输出被 length 截断，建议提高 max_tokens 或压缩输入内容。');
  }
  if (assistantResponse.length > 0 && userPrompt.length > 0 && assistantResponse.length / userPrompt.length > 20) {
    hints.push('回复长度远高于用户输入，可尝试增加“简洁回答”约束减少成本。');
  }
  if (promptTokens > 1000 && cacheRead === 0) {
    hints.push('输入 token 较多但未使用缓存，可考虑启用 Prompt Caching 降低成本。');
  }
  if (cacheRead > 0 && promptTokens > 0 && cacheRead / promptTokens > 0.8) {
    hints.push('缓存命中率优秀，Prompt Caching 运作良好。');
  }
  if (durationMs > 0 && completionTokens > 0) {
    const speed = completionTokens / (durationMs / 1000);
    if (speed < 15) hints.push('生成速度较慢 (' + speed.toFixed(1) + ' tok/s)，可考虑使用更快模型。');
  }
  const reqMessages = detail._reqMessages;
  if (Array.isArray(reqMessages) && reqMessages.length > 20) {
    hints.push('消息历史较长 (' + reqMessages.length + ' 条)，可考虑摘要旧消息降低输入 token。');
  }
  if (hints.length === 0) {
    hints.push('未发现明显异常，可继续按模型、模板、时段进行横向对比优化。');
  }
  return hints;
}

// ── Raw Logs page ────────────────────────────────────────────────────────

let currentRawLogsPage = 1;
let selectedRawLogId = null;

function collectRawLogFilters() {
  const params = new URLSearchParams();
  params.set('page', String(currentRawLogsPage));
  params.set('page_size', document.getElementById('raw-page-size-filter')?.value || '50');

  const mappings = [
    ['raw-q', 'q'],
    ['raw-method-filter', 'method'],
    ['raw-path-prefix-filter', 'path_prefix'],
    ['raw-status-filter', 'status'],
  ];
  mappings.forEach(([id, key]) => {
    const value = document.getElementById(id)?.value;
    if (value) params.set(key, value);
  });
  return params;
}

function resetRawLogFilters() {
  ['raw-q', 'raw-method-filter', 'raw-path-prefix-filter', 'raw-status-filter', 'raw-page-size-filter']
    .forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (id === 'raw-page-size-filter') el.value = '50';
      else if (id === 'raw-path-prefix-filter') el.value = '/v1/';
      else el.value = '';
    });
  loadRawLogsPage(1);
}

function renderRawLogsRestricted() {
  setIfPresent('raw-insight-loaded', 'Restricted');
  setIfPresent('raw-insight-success', 'Admin only');
  setIfPresent('raw-insight-error', 'Admin only');
  setIfPresent('raw-insight-pending', 'Admin only');
  const pagination = document.getElementById('raw-pagination');
  if (pagination) pagination.innerHTML = '';
  const tbody = document.getElementById('raw-logs-tbody');
  if (tbody) {
    tbody.innerHTML = '<tr><td colspan="8" class="muted">Admin only: 激活 admin key/hash 后可查看原始请求与返回日志。</td></tr>';
  }
}

function renderRawLogStatus(row) {
  if (row.status_code == null) return '<span class="badge badge-warning">pending</span>';
  if (row.error || Number(row.status_code) >= 400) return '<span class="badge badge-error">error</span>';
  return '<span class="badge badge-success">success</span>';
}

function formatRawPayloadSize(row) {
  const reqSize = row.request_body_size != null ? fmt(row.request_body_size) : '—';
  const respSize = row.response_body_size != null ? fmt(row.response_body_size) : '—';
  return `${reqSize} / ${respSize}`;
}

function renderRawLogPagination(total, page, pageSize) {
  const el = document.getElementById('raw-pagination');
  if (!el) return;
  const pages = Math.ceil(total / pageSize);
  el.innerHTML = '';
  for (let i = 1; i <= Math.min(pages, 20); i += 1) {
    const btn = document.createElement('button');
    btn.textContent = i;
    if (i === page) btn.classList.add('active');
    btn.onclick = () => loadRawLogsPage(i);
    el.appendChild(btn);
  }
}

function updateRawLogInsights(items) {
  const loaded = items.length;
  const success = items.filter(item => item.status_code != null && Number(item.status_code) < 400 && !item.error).length;
  const error = items.filter(item => item.error || Number(item.status_code || 0) >= 400).length;
  const pending = items.filter(item => item.status_code == null).length;
  setIfPresent('raw-insight-loaded', fmt(loaded));
  setIfPresent('raw-insight-success', fmt(success));
  setIfPresent('raw-insight-error', fmt(error));
  setIfPresent('raw-insight-pending', fmt(pending));
}

function formatPossiblyJson(value) {
  if (value == null || value === '') return '—';
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function hideRawLogDetail() {
  selectedRawLogId = null;
  const overlay = document.getElementById('raw-log-modal-overlay');
  if (overlay) overlay.hidden = true;
  document.body.style.overflow = '';
  document.querySelectorAll('.raw-log-row').forEach((row) => row.classList.remove('selected'));
}

async function showRawLogDetail(requestId) {
  selectedRawLogId = requestId;
  try {
    const detail = await fetchJSON(`${API}/admin/raw-requests/${requestId}`);
    const overlay = document.getElementById('raw-log-modal-overlay');
    if (!overlay) return;

    const meta = document.getElementById('raw-log-detail-meta');
    if (meta) {
      meta.innerHTML = [
        detail.id,
        detail.method || '—',
        detail.path || '—',
        detail.status_code != null ? `HTTP ${detail.status_code}` : 'pending',
        detail.duration_ms != null ? `${fmt(detail.duration_ms, 1)} ms` : '—',
        detail.is_stream ? 'stream' : 'non-stream',
      ].map(value => `<span>${escapeHtml(String(value))}</span>`).join('');
    }

    const queryEl = document.getElementById('raw-log-query-string');
    if (queryEl) queryEl.textContent = detail.query_string || '—';
    const reqHeadersEl = document.getElementById('raw-log-request-headers');
    if (reqHeadersEl) reqHeadersEl.textContent = formatPossiblyJson(detail.request_headers);
    const reqBodyEl = document.getElementById('raw-log-request-body');
    if (reqBodyEl) reqBodyEl.textContent = formatPossiblyJson(detail.request_body);
    const respHeadersEl = document.getElementById('raw-log-response-headers');
    if (respHeadersEl) respHeadersEl.textContent = formatPossiblyJson(detail.response_headers);
    const respBodyEl = document.getElementById('raw-log-response-body');
    if (respBodyEl) respBodyEl.textContent = formatPossiblyJson(detail.response_body);
    const errorEl = document.getElementById('raw-log-error');
    if (errorEl) errorEl.textContent = detail.error || '—';

    overlay.hidden = false;
    document.body.style.overflow = 'hidden';
    document.querySelectorAll('.raw-log-row').forEach((row) => {
      row.classList.toggle('selected', row.dataset.requestId === requestId);
    });
  } catch (e) {
    console.error('Raw log detail error:', e);
  }
}

async function loadRawLogsPage(page) {
  currentRawLogsPage = page || 1;
  try {
    const isAdmin = await refreshAdminAccessState({ force: true });
    if (!isAdmin) {
      renderRawLogsRestricted();
      return;
    }

    const params = collectRawLogFilters();
    const data = await fetchJSON(`${API}/admin/raw-requests?${params}`);
    const items = data.items || [];
    updateRawLogInsights(items);

    const tbody = document.getElementById('raw-logs-tbody');
    if (!tbody) return;
    tbody.innerHTML = items.map(row => `
      <tr class="conversation-row raw-log-row${selectedRawLogId === row.id ? ' selected' : ''}" data-request-id="${row.id}">
        <td>${row.timestamp ? row.timestamp.replace('T', ' ').slice(0, 19) : '—'}</td>
        <td>${fmt(row.seq)}</td>
        <td>${row.method || '—'}</td>
        <td title="${escapeHtml(row.path || '')}">${escapeHtml(truncateText(row.path || '—', 48))}</td>
        <td>${renderRawLogStatus(row)}</td>
        <td>${row.status_code != null ? row.status_code : '—'}</td>
        <td>${row.duration_ms != null ? fmt(row.duration_ms, 1) : '—'}</td>
        <td title="${escapeHtml(row.error || '')}">${escapeHtml(truncateText(row.error || formatRawPayloadSize(row), 44) || '—')}</td>
      </tr>
    `).join('');
    tbody.querySelectorAll('.raw-log-row').forEach((row) => {
      row.addEventListener('click', () => showRawLogDetail(row.dataset.requestId));
    });
    renderRawLogPagination(data.total, data.page, data.page_size);
  } catch (e) {
    console.error('Raw logs load error:', e);
    renderRawLogsRestricted();
  }
}

function updateConversationInsights(items) {
  const loaded = items.length;
  const success = items.filter((r) => r.status === 'success').length;
  const avgTokens = loaded > 0
    ? items.reduce((sum, r) => sum + Number(r.total_tokens || 0), 0) / loaded
    : 0;
  const avgLatency = loaded > 0
    ? items.reduce((sum, r) => sum + Number(r.duration_ms || 0), 0) / loaded
    : 0;

  const loadedEl = document.getElementById('insight-loaded');
  const successEl = document.getElementById('insight-success');
  const avgTokensEl = document.getElementById('insight-avg-tokens');
  const avgLatencyEl = document.getElementById('insight-avg-latency');
  if (loadedEl) loadedEl.textContent = fmt(loaded);
  if (successEl) successEl.textContent = loaded > 0 ? `${((success / loaded) * 100).toFixed(1)}%` : '0%';
  if (avgTokensEl) avgTokensEl.textContent = fmt(avgTokens, 0);
  if (avgLatencyEl) avgLatencyEl.textContent = fmt(avgLatency, 1);
}

function collectConversationFilters() {
  const params = new URLSearchParams();
  params.set('page', String(currentPage));
  params.set('page_size', document.getElementById('page-size-filter')?.value || '50');

  const mappings = [
    ['q', 'q'],
    ['model-filter', 'model'],
    ['template-filter', 'template_id'],
    ['path-prefix-filter', 'path_prefix'],
    ['request-type-filter', 'request_type'],
    ['status-filter', 'status'],
    ['date-from', 'date_from'],
    ['date-to', 'date_to'],
    ['sort-filter', 'sort'],
    ['order-filter', 'order'],
  ];
  mappings.forEach(([id, key]) => {
    const v = document.getElementById(id)?.value;
    if (v) params.set(key, v);
  });
  return params;
}

async function loadConversations(page) {
  currentPage = page || 1;
  const params = collectConversationFilters();

  try {
    const data = await fetchJSON(`${API}/conversations?${params}`);
    currentConversationRows = data.items || [];
    updateConversationInsights(currentConversationRows);

    const tbody = document.getElementById('conv-tbody');
    if (!tbody) return;
    tbody.innerHTML = currentConversationRows.map(r => `
      <tr class="conversation-row${selectedConversationId === r.id ? ' selected' : ''}" data-conversation-id="${r.id}">
        <td>${r.timestamp ? r.timestamp.replace('T', ' ').slice(0, 19) : '—'}</td>
        <td>${r.model || '—'}</td>
        <td><span class="badge badge-${r.status === 'success' ? 'success' : 'error'}">${r.status}</span></td>
        <td>${r.request_type || '—'}</td>
        <td>${renderPromptCompletionPreview(r)}</td>
        <td>${fmt(r.total_tokens)}</td>
        <td>${r.cost_usd != null ? '$' + Number(r.cost_usd).toFixed(5) : '—'}</td>
        <td>${fmt(r.duration_ms, 1)}</td>
      </tr>
    `).join('');
    tbody.querySelectorAll('.conversation-row').forEach((row) => {
      row.addEventListener('click', () => showConversationDetail(row.dataset.conversationId));
    });
    renderPagination(data.total, data.page, data.page_size);
  } catch (e) {
    console.error('Conversations load error:', e);
  }
}

async function showConversationDetail(conversationId) {
  selectedConversationId = conversationId;
  try {
    const [detail, raw] = await Promise.all([
      fetchJSON(`${API}/conversations/${conversationId}`),
      fetchJSON(`${API}/conversations/${conversationId}/raw`),
    ]);

    const reqMessages = extractMessagesFromRequestBody(raw.request_body);
    const fallbackPrompts = extractSystemAndUserPrompt(reqMessages);
    const fallbackAssistant = extractAssistantFromResponseBody(raw.response_body);
    const fallbackUsage = extractUsageFromResponseBody(raw.response_body);
    const fallbackTools = extractToolsFromRequestBody(raw.request_body);

    const resolvedSystemPrompt = detail.system_prompt || fallbackPrompts.systemPrompt || '';
    const resolvedUserPrompt = detail.user_prompt || fallbackPrompts.userPrompt || '';
    const resolvedAssistant = detail.assistant_response || fallbackAssistant || '';

    const resolvedPromptTokens = detail.prompt_tokens ?? fallbackUsage?.prompt ?? null;
    const resolvedCompletionTokens = detail.completion_tokens ?? fallbackUsage?.completion ?? null;
    const resolvedTotalTokens = detail.total_tokens ?? fallbackUsage?.total ?? null;

    // Extract extended usage info
    const extUsage = fallbackUsage || {};
    const cacheReadTokens = extUsage.cacheRead ?? null;
    const cacheCreationTokens = extUsage.cacheCreation ?? null;
    const reasoningTokens = extUsage.reasoning ?? null;

    // Extract request/response body sizes
    const reqBodySize = raw.request_body_size ?? (raw.request_body ? new Blob([raw.request_body]).size : null);
    const resBodySize = raw.response_body_size ?? (raw.response_body ? new Blob([raw.response_body]).size : null);

    // Extract full tool definitions for parameter display
    const fullToolDefs = extractFullToolDefinitions(raw.request_body);

    // Show the modal
    document.getElementById('conv-modal-overlay').hidden = false;
    document.body.style.overflow = 'hidden';

    document.getElementById('detail-meta').innerHTML = `
      <span><strong>ID:</strong> ${detail.id}</span>
      <span><strong>Provider:</strong> ${detail.provider || '—'}</span>
      <span><strong>Model:</strong> ${detail.model || '—'}</span>
      <span><strong>Status:</strong> ${detail.status || '—'}</span>
      <span><strong>Template:</strong> ${detail.template_id || '—'}</span>
      <span><strong>Finish:</strong> ${detail.finish_reason || '—'}</span>
      <span><strong>Latency:</strong> ${fmt(detail.duration_ms, 1)} ms</span>
      <span><strong>Cost:</strong> ${detail.cost_usd != null ? '$' + Number(detail.cost_usd).toFixed(6) : '—'}</span>
    `;

    // Rating widget
    const ratingEl = document.getElementById('detail-rating');
    if (ratingEl) {
      const currentRating = detail.rating || 0;
      ratingEl.innerHTML = [1,2,3,4,5].map(i =>
        `<span class="rating-star${i <= currentRating ? ' active' : ''}" data-rating="${i}" onclick="setConversationRating('${detail.id}', ${i})">★</span>`
      ).join('') +
        (currentRating ? `<button class="rating-clear" onclick="clearConversationRating('${detail.id}')">Clear</button>` : '') +
        (detail.rating_comment ? `<span class="rating-comment">${escapeHtml(detail.rating_comment)}</span>` : '');
    }

    // Tags widget
    const tagsEl = document.getElementById('detail-tags');
    if (tagsEl) {
      let tags = [];
      try { tags = JSON.parse(detail.tags || '[]'); } catch { tags = []; }
      tagsEl.innerHTML = tags.map(t =>
        `<span class="tag-badge">${escapeHtml(t)} <span class="tag-remove" onclick="removeConversationTag('${detail.id}', '${escapeHtml(t)}')">&times;</span></span>`
      ).join('') +
        `<input type="text" class="tag-input" id="tag-input-${detail.id}" placeholder="Add tag…" onkeydown="if(event.key==='Enter')addConversationTag('${detail.id}')" />`;
    }

    // Token breakdown — enhanced dual chart
    renderTokenBreakdown({
      promptTokens: resolvedPromptTokens,
      completionTokens: resolvedCompletionTokens,
      totalTokens: resolvedTotalTokens,
      cacheReadTokens,
      cacheCreationTokens,
      reasoningTokens,
      reqBodySize,
      resBodySize,
      resolvedSystemPrompt,
      resolvedUserPrompt,
      resolvedAssistant,
      reqMessages,
      tools: fallbackTools,
      fullToolDefs,
    });

    // Raw data
    document.getElementById('detail-request-body').textContent = prettyJSONOrText(raw.request_body);
    document.getElementById('detail-response-body').textContent = prettyJSONOrText(raw.response_body);
    document.getElementById('detail-request-headers').textContent = prettyJSONOrText(raw.request_headers);
    document.getElementById('detail-response-headers').textContent = prettyJSONOrText(raw.response_headers);

    // Build collapsible sections
    const sectionsEl = document.getElementById('detail-sections');
    if (sectionsEl) {
      sectionsEl.innerHTML = buildCollapsibleSections({
        detail,
        reqMessages,
        resolvedSystemPrompt,
        resolvedUserPrompt,
        resolvedAssistant,
        resolvedPromptTokens,
        resolvedCompletionTokens,
        resolvedTotalTokens,
        fallbackTools,
        fullToolDefs,
        reqBodySize,
        resBodySize,
        reasoningTokens,
        rawRequestBody: raw.request_body,
        extUsage,
      });
    }

    document.querySelectorAll('.conversation-row').forEach((row) => {
      row.classList.toggle('selected', row.dataset.conversationId === conversationId);
    });
  } catch (e) {
    console.error('Conversation detail load error:', e);
  }
}

function formatBytes(bytes) {
  if (bytes == null) return '—';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(2) + ' MB';
}

/* ── Skill / Tool Usage Analysis ────────────────────────────────── */

const SKILL_CATEGORIES = [
  { key: 'file-read',  label: '文件读取',  icon: '📖', patterns: [/read_file|cat|head|tail|view_image|view_file|get_file|file_read/i] },
  { key: 'file-write', label: '文件写入',  icon: '✏️', patterns: [/write|create_file|edit|replace|patch|insert|update_file|save/i] },
  { key: 'search',     label: '搜索',      icon: '🔍', patterns: [/search|grep|find|glob|ripgrep|rg|locate|semantic_search/i] },
  { key: 'terminal',   label: '终端命令',  icon: '💻', patterns: [/terminal|exec|run|shell|bash|command|subprocess|spawn/i] },
  { key: 'browser',    label: '浏览器',    icon: '🌐', patterns: [/browser|fetch|url|http|web|page|navigate|screenshot/i] },
  { key: 'git',        label: 'Git',       icon: '🔀', patterns: [/git|commit|branch|merge|diff|pull|push|checkout/i] },
  { key: 'analysis',   label: '分析',      icon: '🧠', patterns: [/analy|lint|diagnos|error|test|check|validat/i] },
  { key: 'other',      label: '其他',      icon: '⚡', patterns: [] },
];

function categorizeToolName(name) {
  const n = String(name || '');
  for (const cat of SKILL_CATEGORIES) {
    if (cat.key === 'other') continue;
    if (cat.patterns.some(p => p.test(n))) return cat.key;
  }
  return 'other';
}

function analyzeSkillUsage(toolDefs, messages) {
  const definedTools = (toolDefs || []).map(t => t.name || '');
  const callMap = {};
  const callOrder = [];

  (messages || []).forEach(msg => {
    if (msg.role === 'assistant' && Array.isArray(msg.tool_calls)) {
      msg.tool_calls.forEach(tc => {
        const name = tc?.function?.name || tc?.name || 'unknown';
        callMap[name] = (callMap[name] || 0) + 1;
        callOrder.push(name);
      });
    }
  });

  const totalCalls = callOrder.length;
  const uniqueCalled = Object.keys(callMap);
  const totalDefined = definedTools.length;

  // Categorize defined tools
  const categoryDefined = {};
  const categoryUsed = {};
  definedTools.forEach(name => {
    const cat = categorizeToolName(name);
    if (!categoryDefined[cat]) categoryDefined[cat] = [];
    categoryDefined[cat].push(name);
  });

  // Categorize called tools
  uniqueCalled.forEach(name => {
    const cat = categorizeToolName(name);
    if (!categoryUsed[cat]) categoryUsed[cat] = [];
    categoryUsed[cat].push({ name, count: callMap[name] });
  });

  // Sort calls by frequency
  const topCalls = Object.entries(callMap)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 15);

  // Unused tools
  const calledSet = new Set(uniqueCalled);
  const unused = definedTools.filter(n => !calledSet.has(n));

  return {
    totalDefined,
    totalCalls,
    uniqueCalled: uniqueCalled.length,
    unusedCount: unused.length,
    unused,
    callMap,
    topCalls,
    categoryDefined,
    categoryUsed,
    callOrder,
  };
}

function renderSkillStats(stats) {
  let html = '';

  // Summary cards row
  html += `<div class="skill-stats-cards">
    <div class="skill-stat-card">
      <div class="skill-stat-value">${stats.totalDefined}</div>
      <div class="skill-stat-label">定义工具</div>
    </div>
    <div class="skill-stat-card">
      <div class="skill-stat-value">${stats.uniqueCalled}</div>
      <div class="skill-stat-label">实际使用</div>
    </div>
    <div class="skill-stat-card">
      <div class="skill-stat-value">${stats.totalCalls}</div>
      <div class="skill-stat-label">调用次数</div>
    </div>
    <div class="skill-stat-card">
      <div class="skill-stat-value">${stats.totalDefined > 0 ? ((stats.uniqueCalled / stats.totalDefined) * 100).toFixed(0) + '%' : '—'}</div>
      <div class="skill-stat-label">使用率</div>
    </div>
  </div>`;

  // Category distribution
  const allCatKeys = new Set([...Object.keys(stats.categoryDefined), ...Object.keys(stats.categoryUsed)]);
  if (allCatKeys.size > 0) {
    html += `<div class="skill-category-section">
      <div class="skill-section-title">能力分布</div>
      <div class="skill-category-grid">`;
    SKILL_CATEGORIES.forEach(cat => {
      const defined = stats.categoryDefined[cat.key] || [];
      const used = stats.categoryUsed[cat.key] || [];
      if (defined.length === 0 && used.length === 0) return;
      const usedCount = used.reduce((s, u) => s + u.count, 0);
      html += `<div class="skill-category-card">
        <div class="skill-cat-header">
          <span class="skill-cat-icon">${cat.icon}</span>
          <span class="skill-cat-name">${cat.label}</span>
        </div>
        <div class="skill-cat-metrics">
          <span class="skill-cat-metric">${defined.length} 定义</span>
          <span class="skill-cat-divider">·</span>
          <span class="skill-cat-metric">${used.length} 使用</span>
          ${usedCount > 0 ? `<span class="skill-cat-divider">·</span><span class="skill-cat-metric">${usedCount} 次调用</span>` : ''}
        </div>
        ${used.length > 0 ? `<div class="skill-cat-tools">${used.map(u => `<span class="skill-tool-badge skill-tool-used">${escapeHtml(u.name)}${u.count > 1 ? ` ×${u.count}` : ''}</span>`).join('')}</div>` : ''}
      </div>`;
    });
    html += `</div></div>`;
  }

  // Top called tools chart
  if (stats.topCalls.length > 0) {
    const maxCount = stats.topCalls[0][1];
    html += `<div class="skill-section-title">调用频率 Top${Math.min(stats.topCalls.length, 15)}</div>`;
    html += `<div class="skill-freq-list">`;
    stats.topCalls.forEach(([name, count]) => {
      const pct = (count / maxCount) * 100;
      const cat = categorizeToolName(name);
      const catInfo = SKILL_CATEGORIES.find(c => c.key === cat) || SKILL_CATEGORIES[SKILL_CATEGORIES.length - 1];
      html += `<div class="skill-freq-row">
        <span class="skill-freq-icon">${catInfo.icon}</span>
        <span class="skill-freq-name">${escapeHtml(name)}</span>
        <div class="skill-freq-bar-bg"><div class="skill-freq-bar skill-freq-cat-${cat}" style="width:${pct}%"></div></div>
        <span class="skill-freq-count">${count}</span>
      </div>`;
    });
    html += `</div>`;
  }

  // Unused tools (collapsed if many)
  if (stats.unused.length > 0) {
    const showMax = 10;
    const displayed = stats.unused.slice(0, showMax);
    html += `<div class="skill-unused-section">
      <div class="skill-section-title">未使用工具 (${stats.unused.length})</div>
      <div class="skill-cat-tools">${displayed.map(n => `<span class="skill-tool-badge skill-tool-unused">${escapeHtml(n)}</span>`).join('')}${stats.unused.length > showMax ? `<span class="skill-tool-badge skill-tool-unused">…+${stats.unused.length - showMax}</span>` : ''}</div>
    </div>`;
  }

  return html;
}

function toggleToolParams(cardId, event) {
  if (event) event.stopPropagation();
  const panel = document.getElementById(cardId);
  const arrow = document.getElementById(cardId + '-arrow');
  if (!panel) return;
  const show = panel.hidden;
  panel.hidden = !show;
  if (arrow) arrow.textContent = show ? '▼' : '▶';
  // Toggle full-width class on the card
  const card = panel.closest('.tool-card');
  if (card) card.classList.toggle('tool-card-expanded', show);
}

/* ── Performance Analysis ────────────────────────────────────── */

function analyzePerformance(detail, usage) {
  const durationMs = Number(detail.duration_ms || 0);
  const durationSec = durationMs / 1000;
  const promptTokens = Number(detail.prompt_tokens || usage?.prompt || 0);
  const completionTokens = Number(detail.completion_tokens || usage?.completion || 0);
  const totalTokens = Number(detail.total_tokens || usage?.total || promptTokens + completionTokens);
  const cacheRead = Number(usage?.cacheRead || 0);
  const cacheCreation = Number(usage?.cacheCreation || 0);
  const reasoning = Number(usage?.reasoning || 0);
  const costUsd = Number(detail.cost_usd || 0);

  const tokensPerSec = durationSec > 0 && completionTokens > 0 ? (completionTokens / durationSec) : null;
  const totalThroughput = durationSec > 0 && totalTokens > 0 ? (totalTokens / durationSec) : null;
  const ioRatio = promptTokens > 0 ? (completionTokens / promptTokens) : null;
  const cacheHitRate = promptTokens > 0 && cacheRead > 0 ? (cacheRead / promptTokens) : null;
  const reasoningRatio = completionTokens > 0 && reasoning > 0 ? (reasoning / completionTokens) : null;
  const costPerOutputToken = costUsd > 0 && completionTokens > 0 ? (costUsd / completionTokens * 1000) : null;
  const costPer1kTokens = costUsd > 0 && totalTokens > 0 ? (costUsd / totalTokens * 1000) : null;

  return {
    durationMs, durationSec, promptTokens, completionTokens, totalTokens,
    cacheRead, cacheCreation, reasoning,
    tokensPerSec, totalThroughput, ioRatio,
    cacheHitRate, reasoningRatio,
    costUsd, costPerOutputToken, costPer1kTokens,
  };
}

function renderPerformanceSection(perf) {
  const cards = [];
  if (perf.tokensPerSec != null) {
    const speedClass = perf.tokensPerSec > 80 ? 'perf-good' : perf.tokensPerSec > 30 ? 'perf-ok' : 'perf-slow';
    cards.push({ label: '生成速度', value: perf.tokensPerSec.toFixed(1) + ' tok/s', cls: speedClass });
  }
  if (perf.totalThroughput != null) {
    cards.push({ label: '总吞吐量', value: perf.totalThroughput.toFixed(1) + ' tok/s', cls: '' });
  }
  cards.push({ label: '延迟', value: perf.durationMs > 0 ? fmt(perf.durationMs, 1) + ' ms' : '—', cls: '' });
  if (perf.ioRatio != null) {
    cards.push({ label: 'I/O 比', value: perf.ioRatio.toFixed(2), cls: '' });
  }
  if (perf.cacheHitRate != null) {
    const pct = (perf.cacheHitRate * 100).toFixed(1);
    cards.push({ label: '缓存命中', value: pct + '%', cls: perf.cacheHitRate > 0.5 ? 'perf-good' : '' });
  }
  if (perf.reasoningRatio != null) {
    cards.push({ label: '推理占比', value: (perf.reasoningRatio * 100).toFixed(1) + '%', cls: '' });
  }
  if (perf.costPerOutputToken != null) {
    cards.push({ label: '$/1k 输出', value: '$' + perf.costPerOutputToken.toFixed(4), cls: '' });
  }
  if (perf.costPer1kTokens != null) {
    cards.push({ label: '$/1k 总 Token', value: '$' + perf.costPer1kTokens.toFixed(4), cls: '' });
  }

  let html = `<div class="perf-cards">`;
  cards.forEach(c => {
    html += `<div class="perf-card ${c.cls}">
      <div class="perf-card-value">${c.value}</div>
      <div class="perf-card-label">${c.label}</div>
    </div>`;
  });
  html += `</div>`;

  // Speed gauge bar
  if (perf.tokensPerSec != null) {
    const maxSpeed = 150;
    const pct = Math.min(100, (perf.tokensPerSec / maxSpeed) * 100);
    const hue = Math.min(120, (perf.tokensPerSec / maxSpeed) * 120);
    html += `<div class="perf-gauge">
      <div class="perf-gauge-label">生成速度 <span>${perf.tokensPerSec.toFixed(1)} tok/s</span></div>
      <div class="perf-gauge-track"><div class="perf-gauge-fill" style="width:${pct}%;background:hsl(${hue},70%,45%)"></div></div>
      <div class="perf-gauge-scale"><span>0</span><span>慢 (&lt;30)</span><span>中等</span><span>快 (&gt;80)</span><span>${maxSpeed}</span></div>
    </div>`;
  }

  return html;
}

/* ── Content Analysis ────────────────────────────────────── */

function analyzeContent(systemPrompt, userPrompt, assistantResponse, reqMessages) {
  const countWords = (text) => {
    if (!text) return 0;
    // Handle mixed Chinese/English text
    const cjk = (text.match(/[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]/g) || []).length;
    const western = (text.match(/[a-zA-Z]+/g) || []).length;
    return cjk + western;
  };

  const countCodeBlocks = (text) => {
    if (!text) return { blocks: 0, lines: 0, languages: [] };
    const matches = text.match(/```(\w*)\n[\s\S]*?```/g) || [];
    const languages = new Set();
    let totalLines = 0;
    matches.forEach(m => {
      const langMatch = m.match(/^```(\w+)/);
      if (langMatch && langMatch[1]) languages.add(langMatch[1]);
      totalLines += m.split('\n').length - 2; // minus opening/closing
    });
    return { blocks: matches.length, lines: totalLines, languages: [...languages] };
  };

  const estimateReadingTime = (text) => {
    if (!text) return 0;
    const words = countWords(text);
    // ~250 words/min for reading speed
    return Math.ceil(words / 250);
  };

  const detectFormat = (text) => {
    if (!text) return 'empty';
    const trimmed = text.trim();
    // JSON
    if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
      try { JSON.parse(trimmed); return 'JSON'; } catch {}
    }
    // Code heavy
    const codeBlocks = (trimmed.match(/```/g) || []).length;
    if (codeBlocks >= 2) return 'Markdown + Code';
    // Markdown
    if (/^#{1,6}\s/m.test(trimmed) || /\*\*[^*]+\*\*/m.test(trimmed) || /^-\s/m.test(trimmed)) return 'Markdown';
    // XML/HTML
    if (/<\w+[^>]*>/.test(trimmed) && /<\/\w+>/.test(trimmed)) return 'XML/HTML';
    return '纯文本';
  };

  // Conversation turn count
  let turns = 0;
  let totalMsgLen = 0;
  let roleCounts = { system: 0, user: 0, assistant: 0, tool: 0 };
  if (Array.isArray(reqMessages)) {
    reqMessages.forEach(msg => {
      if (!msg) return;
      const role = msg.role || 'unknown';
      if (roleCounts[role] !== undefined) roleCounts[role]++;
      const content = normalizeMessageContent(msg.content) || '';
      totalMsgLen += content.length;
      if (role === 'user') turns++;
    });
  }

  const systemLen = (systemPrompt || '').length;
  const userLen = (userPrompt || '').length;
  const assistantLen = (assistantResponse || '').length;
  const systemWords = countWords(systemPrompt);
  const userWords = countWords(userPrompt);
  const assistantWords = countWords(assistantResponse);

  const responseCode = countCodeBlocks(assistantResponse);
  const promptCode = countCodeBlocks(userPrompt);
  const responseFormat = detectFormat(assistantResponse);
  const readingTime = estimateReadingTime(assistantResponse);

  // Compression ratio: how much output per input character
  const compressionRatio = userLen > 0 ? (assistantLen / userLen) : null;

  return {
    systemLen, systemWords,
    userLen, userWords,
    assistantLen, assistantWords,
    turns, roleCounts, totalMsgLen,
    responseCode, promptCode,
    responseFormat, readingTime,
    compressionRatio,
    totalMessages: Array.isArray(reqMessages) ? reqMessages.length : 0,
  };
}

function renderContentAnalysis(content) {
  let html = '';

  // Summary cards
  html += `<div class="content-analysis-cards">
    <div class="content-card">
      <div class="content-card-value">${content.totalMessages}</div>
      <div class="content-card-label">消息数</div>
    </div>
    <div class="content-card">
      <div class="content-card-value">${content.turns}</div>
      <div class="content-card-label">对话轮次</div>
    </div>
    <div class="content-card">
      <div class="content-card-value">${content.readingTime > 0 ? content.readingTime + ' min' : '—'}</div>
      <div class="content-card-label">阅读时间</div>
    </div>
    <div class="content-card">
      <div class="content-card-value">${content.responseFormat}</div>
      <div class="content-card-label">回复格式</div>
    </div>
  </div>`;

  // Text length breakdown table
  html += `<div class="content-table-wrap">
    <table class="content-table">
      <thead><tr><th>内容</th><th>字符数</th><th>词数</th><th>占比</th></tr></thead>
      <tbody>`;
  const totalChars = content.systemLen + content.userLen + content.assistantLen;
  const rows = [
    { label: 'System Prompt', chars: content.systemLen, words: content.systemWords },
    { label: 'User Input', chars: content.userLen, words: content.userWords },
    { label: 'Assistant Output', chars: content.assistantLen, words: content.assistantWords },
  ];
  rows.forEach(r => {
    const pct = totalChars > 0 ? ((r.chars / totalChars) * 100).toFixed(1) : '0.0';
    html += `<tr>
      <td>${r.label}</td>
      <td>${fmt(r.chars)}</td>
      <td>${fmt(r.words)}</td>
      <td>
        <div class="content-pct-bar"><div class="content-pct-fill" style="width:${pct}%"></div></div>
        <span>${pct}%</span>
      </td>
    </tr>`;
  });
  html += `</tbody></table></div>`;

  // Message role distribution
  if (content.totalMessages > 0) {
    const roleEntries = Object.entries(content.roleCounts).filter(([, v]) => v > 0);
    if (roleEntries.length > 0) {
      html += `<div class="content-role-dist">
        <div class="content-subtitle">消息角色分布</div>
        <div class="content-role-bars">`;
      const maxCount = Math.max(...roleEntries.map(([, v]) => v));
      const roleColors = { system: '#f59e0b', user: '#3b82f6', assistant: '#10b981', tool: '#8b5cf6' };
      roleEntries.forEach(([role, count]) => {
        const pct = (count / maxCount) * 100;
        html += `<div class="content-role-row">
          <span class="content-role-name">${role}</span>
          <div class="content-role-bar-bg"><div class="content-role-bar-fill" style="width:${pct}%;background:${roleColors[role] || '#64748b'}"></div></div>
          <span class="content-role-count">${count}</span>
        </div>`;
      });
      html += `</div></div>`;
    }
  }

  // Code blocks detection
  if (content.responseCode.blocks > 0 || content.promptCode.blocks > 0) {
    html += `<div class="content-code-info">
      <div class="content-subtitle">代码块分析</div>
      <div class="content-code-row">`;
    if (content.promptCode.blocks > 0) {
      html += `<div class="content-code-card">
        <strong>输入</strong>
        <span>${content.promptCode.blocks} 块 · ${content.promptCode.lines} 行</span>
        ${content.promptCode.languages.length > 0 ? `<span class="content-code-langs">${content.promptCode.languages.join(', ')}</span>` : ''}
      </div>`;
    }
    if (content.responseCode.blocks > 0) {
      html += `<div class="content-code-card">
        <strong>输出</strong>
        <span>${content.responseCode.blocks} 块 · ${content.responseCode.lines} 行</span>
        ${content.responseCode.languages.length > 0 ? `<span class="content-code-langs">${content.responseCode.languages.join(', ')}</span>` : ''}
      </div>`;
    }
    html += `</div></div>`;
  }

  // Compression ratio
  if (content.compressionRatio != null) {
    const ratio = content.compressionRatio;
    const label = ratio > 5 ? '高膨胀' : ratio > 1 ? '正常' : '压缩';
    html += `<div class="content-compression">
      <span class="content-subtitle">输入输出比</span>
      <span class="content-compression-value">${ratio.toFixed(2)}x <small>(${label})</small></span>
    </div>`;
  }

  return html;
}

/* ── Request Configuration ────────────────────────────────────── */

function extractRequestConfig(requestBody) {
  const parsed = parseField(requestBody);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;

  const config = {};
  const fields = [
    'temperature', 'max_tokens', 'top_p', 'top_k',
    'frequency_penalty', 'presence_penalty', 'repetition_penalty',
    'seed', 'n', 'stop', 'response_format',
    'stream', 'stream_options', 'logprobs', 'top_logprobs',
    'tool_choice', 'parallel_tool_calls',
  ];
  fields.forEach(f => {
    if (parsed[f] !== undefined) config[f] = parsed[f];
  });
  // Also extract model for display
  if (parsed.model) config._model = parsed.model;
  return Object.keys(config).length > 0 ? config : null;
}

function renderRequestConfig(config) {
  if (!config) return '<div class="muted">未检测到模型参数</div>';

  const labels = {
    temperature: { label: '温度', desc: '控制随机性，越高越随机' },
    max_tokens: { label: '最大 Token', desc: '限制输出长度' },
    top_p: { label: 'Top-P', desc: '核采样阈值' },
    top_k: { label: 'Top-K', desc: 'Top-K 采样' },
    frequency_penalty: { label: '频率惩罚', desc: '降低已出现词频' },
    presence_penalty: { label: '存在惩罚', desc: '鼓励新话题' },
    repetition_penalty: { label: '重复惩罚', desc: '抑制重复' },
    seed: { label: '随机种子', desc: '可复现的随机' },
    n: { label: '生成数量', desc: '生成几个回复' },
    stop: { label: '停止序列', desc: '遇到即停止' },
    response_format: { label: '响应格式', desc: 'JSON mode 等' },
    stream: { label: '流式输出', desc: '是否流式' },
    stream_options: { label: '流式选项', desc: '流式配置' },
    logprobs: { label: 'Log Probs', desc: '返回概率' },
    top_logprobs: { label: 'Top Log Probs', desc: '概率数量' },
    tool_choice: { label: 'Tool Choice', desc: '工具选择策略' },
    parallel_tool_calls: { label: '并行调用', desc: '是否并行 tool calls' },
  };

  let html = `<div class="config-grid">`;
  Object.entries(config).forEach(([key, value]) => {
    if (key.startsWith('_')) return;
    const info = labels[key] || { label: key, desc: '' };
    let displayValue = value;
    if (typeof value === 'boolean') displayValue = value ? '✓ 是' : '✗ 否';
    else if (typeof value === 'object') displayValue = JSON.stringify(value);
    else if (value === null || value === undefined) displayValue = '—';

    // Highlight non-default values
    const isDefault = (key === 'temperature' && value === 1) || (key === 'top_p' && value === 1) ||
      (key === 'frequency_penalty' && value === 0) || (key === 'presence_penalty' && value === 0) ||
      (key === 'n' && value === 1);

    html += `<div class="config-item${isDefault ? '' : ' config-item-custom'}">
      <div class="config-item-label" title="${escapeHtml(info.desc)}">${info.label}</div>
      <div class="config-item-value">${escapeHtml(String(displayValue))}</div>
    </div>`;
  });
  html += `</div>`;
  return html;
}

function renderTokenBreakdown(info) {
  const {
    promptTokens: pt, completionTokens: ct, totalTokens: tt,
    cacheReadTokens, cacheCreationTokens, reasoningTokens,
    reqBodySize, resBodySize,
    resolvedSystemPrompt, resolvedUserPrompt, resolvedAssistant,
    reqMessages, tools, fullToolDefs,
  } = info;
  const promptTokens = Number(pt || 0);
  const completionTokens = Number(ct || 0);
  const totalTokens = Number(tt || promptTokens + completionTokens);
  const cacheRead = Number(cacheReadTokens || 0);
  const cacheCreation = Number(cacheCreationTokens || 0);
  const reasoning = Number(reasoningTokens || 0);

  // === Chart 1: Request vs Response with cache breakdown ===
  const chart1El = document.getElementById('breakdown-chart-io');
  if (chart1El) {
    const hasTokenData = promptTokens > 0 || completionTokens > 0;

    if (!hasTokenData) {
      // No token data — show sizes only
      const sizeParts = [];
      if (reqBodySize != null) sizeParts.push(`请求体: ${formatBytes(reqBodySize)}`);
      if (resBodySize != null) sizeParts.push(`响应体: ${formatBytes(resBodySize)}`);
      chart1El.innerHTML = `<div class="breakdown-label">输入 / 输出 Token 分布</div>
        <div class="breakdown-empty">Token 数据不可用（模型未返回 usage 信息）</div>
        ${sizeParts.length > 0 ? `<div class="breakdown-meta">${sizeParts.join(' · ')}</div>` : ''}`;
    } else {
      const nonCachedPrompt = Math.max(0, promptTokens - cacheRead - cacheCreation);
      const normalCompletion = Math.max(0, completionTokens - reasoning);
      // Use max of both for relative scaling
      const scaleMax = Math.max(promptTokens, completionTokens, 1);

      let html = '<div class="breakdown-label">输入 / 输出 Token 分布</div>';
      // Prompt bar
      const pBarWidth = (promptTokens / scaleMax) * 100;
      html += `<div class="breakdown-row">`;
      html += `<span class="breakdown-row-label">Prompt <strong>${fmt(promptTokens)}</strong></span>`;
      html += `<div class="breakdown-bar-container">`;
      if (promptTokens > 0) {
        if (cacheRead > 0) {
          const w = Math.max(2, (cacheRead / scaleMax) * 100);
          html += `<div class="breakdown-bar-seg bar-cache-read" style="width:${w}%" title="Cache Read: ${fmt(cacheRead)}"></div>`;
        }
        if (cacheCreation > 0) {
          const w = Math.max(2, (cacheCreation / scaleMax) * 100);
          html += `<div class="breakdown-bar-seg bar-cache-write" style="width:${w}%" title="Cache Creation: ${fmt(cacheCreation)}"></div>`;
        }
        if (nonCachedPrompt > 0) {
          const w = Math.max(2, (nonCachedPrompt / scaleMax) * 100);
          html += `<div class="breakdown-bar-seg bar-prompt-normal" style="width:${w}%" title="Non-cached: ${fmt(nonCachedPrompt)}"></div>`;
        }
      }
      html += `</div></div>`;

      // Completion bar
      html += `<div class="breakdown-row">`;
      html += `<span class="breakdown-row-label">Completion <strong>${fmt(completionTokens)}</strong></span>`;
      html += `<div class="breakdown-bar-container">`;
      if (completionTokens > 0) {
        if (reasoning > 0) {
          const w = Math.max(2, (reasoning / scaleMax) * 100);
          html += `<div class="breakdown-bar-seg bar-reasoning" style="width:${w}%" title="Reasoning: ${fmt(reasoning)}"></div>`;
        }
        if (normalCompletion > 0) {
          const w = Math.max(2, (normalCompletion / scaleMax) * 100);
          html += `<div class="breakdown-bar-seg bar-completion-normal" style="width:${w}%" title="Output: ${fmt(normalCompletion)}"></div>`;
        }
      }
      html += `</div></div>`;

      // Meta line
      const metaParts = [`Total: ${fmt(totalTokens)}`];
      if (cacheRead > 0) metaParts.push(`Cache Hit: ${fmt(cacheRead)} (${((cacheRead / promptTokens) * 100).toFixed(0)}%)`);
      if (cacheCreation > 0) metaParts.push(`Cache Write: ${fmt(cacheCreation)}`);
      if (reasoning > 0) metaParts.push(`Reasoning: ${fmt(reasoning)} (${((reasoning / completionTokens) * 100).toFixed(0)}%)`);
      if (reqBodySize != null) metaParts.push(`Req: ${formatBytes(reqBodySize)}`);
      if (resBodySize != null) metaParts.push(`Res: ${formatBytes(resBodySize)}`);
      html += `<div class="breakdown-meta">${metaParts.join(' · ')}</div>`;

      // Legend
      html += `<div class="breakdown-legend">`;
      if (cacheRead > 0) html += `<span class="legend-item"><span class="legend-dot bar-cache-read"></span>Cache Read</span>`;
      if (cacheCreation > 0) html += `<span class="legend-item"><span class="legend-dot bar-cache-write"></span>Cache Write</span>`;
      html += `<span class="legend-item"><span class="legend-dot bar-prompt-normal"></span>Prompt</span>`;
      if (reasoning > 0) html += `<span class="legend-item"><span class="legend-dot bar-reasoning"></span>Reasoning</span>`;
      html += `<span class="legend-item"><span class="legend-dot bar-completion-normal"></span>Completion</span>`;
      html += `</div>`;

      chart1El.innerHTML = html;
    }
  }

  // === Chart 2: Content composition breakdown ===
  const chart2El = document.getElementById('breakdown-chart-composition');
  if (chart2El) {
    // Estimate char-based proportions from known content
    const systemLen = (resolvedSystemPrompt || '').length;
    const userLen = (resolvedUserPrompt || '').length;
    const assistantLen = (resolvedAssistant || '').length;

    // Estimate history messages length
    let historyLen = 0;
    let toolMsgLen = 0;
    if (Array.isArray(reqMessages)) {
      let lastUserIdx = -1;
      for (let i = reqMessages.length - 1; i >= 0; i--) {
        if (reqMessages[i] && reqMessages[i].role === 'user') { lastUserIdx = i; break; }
      }
      reqMessages.forEach((msg, idx) => {
        if (!msg) return;
        const content = normalizeMessageContent(msg.content) || '';
        if (msg.role === 'system') return; // counted separately
        if (msg.role === 'tool' || (msg.role === 'assistant' && msg.tool_calls)) {
          toolMsgLen += content.length;
          if (msg.tool_calls) toolMsgLen += JSON.stringify(msg.tool_calls).length;
          return;
        }
        if (idx === lastUserIdx) return; // counted separately
        historyLen += content.length;
      });
    }

    // Estimate tool definitions size
    let toolDefsLen = 0;
    if (tools && tools.length > 0) {
      toolDefsLen = JSON.stringify(tools).length;
    }

    const segments = [];
    if (systemLen > 0) segments.push({ label: 'System Prompt', value: systemLen, cls: 'comp-system' });
    if (toolDefsLen > 0) segments.push({ label: 'Tool 定义', value: toolDefsLen, cls: 'comp-tools' });
    if (toolMsgLen > 0) segments.push({ label: 'Tool 交互', value: toolMsgLen, cls: 'comp-tool-msg' });
    if (historyLen > 0) segments.push({ label: '历史消息', value: historyLen, cls: 'comp-history' });
    if (userLen > 0) segments.push({ label: 'User Prompt', value: userLen, cls: 'comp-user' });
    if (assistantLen > 0) segments.push({ label: 'Assistant', value: assistantLen, cls: 'comp-assistant' });

    const totalChars = segments.reduce((s, seg) => s + seg.value, 0);
    if (totalChars === 0) {
      chart2El.innerHTML = '';
      return;
    }

    let html = '<div class="breakdown-label">内容构成分布（按字符估算）</div>';
    html += `<div class="composition-bar">`;
    segments.forEach(seg => {
      const pct = Math.max(1.5, (seg.value / totalChars) * 100);
      html += `<div class="composition-seg ${seg.cls}" style="width:${pct}%" title="${seg.label}: ${fmt(seg.value)} chars (${((seg.value / totalChars) * 100).toFixed(1)}%)"></div>`;
    });
    html += `</div>`;

    // Detail rows
    html += `<div class="composition-details">`;
    segments.forEach(seg => {
      const pct = ((seg.value / totalChars) * 100).toFixed(1);
      html += `<div class="composition-row">
        <span class="legend-dot ${seg.cls}"></span>
        <span class="composition-name">${seg.label}</span>
        <span class="composition-value">${fmt(seg.value)} chars</span>
        <span class="composition-pct">${pct}%</span>
        <div class="composition-minibar"><div class="${seg.cls}" style="width:${pct}%"></div></div>
      </div>`;
    });
    html += `</div>`;

    chart2El.innerHTML = html;
  }

  // === Chart 3: Skill call distribution ===
  const SKILL_COLORS = {
    'file-read':  '#3b82f6',
    'file-write': '#f59e0b',
    'search':     '#8b5cf6',
    'terminal':   '#ef4444',
    'browser':    '#14b8a6',
    'git':        '#10b981',
    'analysis':   '#a855f7',
    'other':      '#94a3b8',
  };
  const chart3El = document.getElementById('breakdown-chart-skills');
  if (chart3El) {
    const toolDefsArr = fullToolDefs || [];
    const hasToolCalls = Array.isArray(reqMessages) && reqMessages.some(m => m && m.role === 'assistant' && Array.isArray(m.tool_calls) && m.tool_calls.length > 0);
    if (toolDefsArr.length > 0 || hasToolCalls) {
      const skillStats = analyzeSkillUsage(toolDefsArr, reqMessages || []);
      if (skillStats.totalDefined > 0 || skillStats.totalCalls > 0) {
        // Build per-category call counts
        const catCallMap = {};
        Object.entries(skillStats.callMap).forEach(([name, count]) => {
          const cat = categorizeToolName(name);
          catCallMap[cat] = (catCallMap[cat] || 0) + count;
        });

        const segments = Object.entries(catCallMap)
          .sort((a, b) => b[1] - a[1])
          .map(([cat, count]) => {
            const catInfo = SKILL_CATEGORIES.find(c => c.key === cat) || SKILL_CATEGORIES[SKILL_CATEGORIES.length - 1];
            return { key: cat, label: catInfo.icon + '\u00a0' + catInfo.label, count, color: SKILL_COLORS[cat] || '#94a3b8' };
          });

        const totalCalls = segments.reduce((s, seg) => s + seg.count, 0);
        const usageRate = skillStats.totalDefined > 0
          ? ((skillStats.uniqueCalled / skillStats.totalDefined) * 100).toFixed(0) + '%'
          : '—';

        let html3 = `<div class="breakdown-label">Skill 调用分布 <span style="font-weight:400;color:#6b7280;font-size:0.75rem;">${fmt(skillStats.totalCalls)} 次调用 · ${skillStats.totalDefined} 个工具定义 · 使用率 ${usageRate}</span></div>`;

        if (segments.length > 0 && totalCalls > 0) {
          html3 += `<div class="composition-bar">`;
          segments.forEach(seg => {
            const pct = Math.max(1.5, (seg.count / totalCalls) * 100);
            html3 += `<div class="composition-seg" style="width:${pct}%;background:${seg.color}" title="${seg.label}: ${seg.count} 次 (${((seg.count / totalCalls) * 100).toFixed(1)}%)"></div>`;
          });
          html3 += `</div>`;

          html3 += `<div class="composition-details">`;
          segments.forEach(seg => {
            const pct = ((seg.count / totalCalls) * 100).toFixed(1);
            html3 += `<div class="composition-row">
              <span class="legend-dot" style="background:${seg.color}"></span>
              <span class="composition-name">${seg.label}</span>
              <span class="composition-value">${seg.count} 次</span>
              <span class="composition-pct">${pct}%</span>
              <div class="composition-minibar"><div style="width:${pct}%;height:100%;background:${seg.color};border-radius:3px;"></div></div>
            </div>`;
          });
          html3 += `</div>`;
        } else if (skillStats.totalDefined > 0) {
          html3 += `<div class="breakdown-empty">无工具调用记录</div>`;
        }

        chart3El.innerHTML = html3;
      } else {
        chart3El.innerHTML = '';
      }
    } else {
      chart3El.innerHTML = '';
    }
  }
}

function buildCollapsibleSections(ctx) {
  const { detail, reqMessages, resolvedSystemPrompt, resolvedUserPrompt, resolvedAssistant,
    resolvedPromptTokens, resolvedCompletionTokens, resolvedTotalTokens, fallbackTools,
    fullToolDefs, reqBodySize, resBodySize, reasoningTokens, rawRequestBody, extUsage } = ctx;

  const sections = [];

  // 1. System Prompt section
  if (resolvedSystemPrompt) {
    sections.push(buildSection({
      id: 'section-system',
      icon: '⚙️',
      iconClass: 'section-icon-system',
      title: 'System Prompt',
      badge: resolvedSystemPrompt.length > 0 ? `${resolvedSystemPrompt.length} chars` : null,
      contentHTML: `<button class="section-copy-btn" onclick="copySectionText('section-system-pre')">Copy</button>
        <pre id="section-system-pre">${escapeHtml(resolvedSystemPrompt)}</pre>`,
      defaultOpen: false,
    }));
  }

  // 2. Tools section — with click-to-expand parameter details
  const tools = (Array.isArray(detail.tools_list) ? detail.tools_list : maybeJSON(detail.tools_list)) || fallbackTools;
  const toolDefsArr = fullToolDefs || [];
  const hasToolMsgs = Array.isArray(reqMessages) && reqMessages.some(m => m && (m.role === 'tool' || (m.role === 'assistant' && m.tool_calls)));
  if ((tools && tools.length > 0) || hasToolMsgs) {
    let toolHTML = '';

    // Overview: request/response size + reasoning info
    const overviewParts = [];
    if (reqBodySize != null) overviewParts.push(`请求体: ${formatBytes(reqBodySize)}`);
    if (resBodySize != null) overviewParts.push(`响应体: ${formatBytes(resBodySize)}`);
    if (reasoningTokens != null) overviewParts.push(`推理 Tokens: ${fmt(reasoningTokens)}`);
    if (overviewParts.length > 0) {
      toolHTML += `<div class="section-overview-bar">${overviewParts.map(p => `<span>${p}</span>`).join('')}</div>`;
    }

    // Tool definition cards with expandable parameters
    if (toolDefsArr.length > 0) {
      toolHTML += `<div class="tools-grid">${toolDefsArr.map((td, idx) => {
        const name = td.name || 'unknown';
        const desc = td.description || '';
        const params = td.parameters;
        const hasParams = params && typeof params === 'object' && params.properties && Object.keys(params.properties).length > 0;
        const cardId = 'tool-detail-' + idx;
        let paramHTML = '';
        if (hasParams) {
          const props = params.properties;
          const required = params.required || [];
          paramHTML = `<div class="tool-params-panel" id="${cardId}" hidden>
            <table class="tool-params-table">
              <thead><tr><th>参数</th><th>类型</th><th>必填</th><th>说明</th></tr></thead>
              <tbody>${Object.entries(props).map(([pName, pDef]) => {
                const pType = pDef.type || (pDef.enum ? 'enum' : '—');
                const pReq = required.includes(pName) ? '✓' : '';
                const pDesc = pDef.description || '';
                const enumVals = pDef.enum ? ` [${pDef.enum.join(', ')}]` : '';
                return `<tr>
                  <td><code>${escapeHtml(pName)}</code></td>
                  <td>${escapeHtml(pType)}${enumVals ? `<span class="param-enum">${escapeHtml(enumVals)}</span>` : ''}</td>
                  <td class="param-req">${pReq}</td>
                  <td>${escapeHtml(truncateText(pDesc, 120))}</td>
                </tr>`;
              }).join('')}</tbody>
            </table>
          </div>`;
        }
        return `<div class="tool-card${hasParams ? ' tool-card-expandable' : ''}"${hasParams ? ` onclick="toggleToolParams('${cardId}', event)"` : ''}>
          <div class="tool-card-header">
            <div class="tool-card-name">${escapeHtml(name)}</div>
            ${hasParams ? `<span class="tool-card-toggle" id="${cardId}-arrow">▶</span>` : ''}
          </div>
          ${desc ? `<div class="tool-card-desc" title="${escapeHtml(desc)}">${escapeHtml(truncateText(desc, 80))}</div>` : ''}
          ${paramHTML}
        </div>`;
      }).join('')}</div>`;
    } else if (tools && tools.length > 0) {
      toolHTML += `<div class="tools-grid">${tools.map(t => {
        const name = typeof t === 'string' ? t : (t?.name || t?.function?.name || 'unknown');
        return `<div class="tool-card"><div class="tool-card-name">${escapeHtml(name)}</div></div>`;
      }).join('')}</div>`;
    }
    // Tool call messages from history
    if (Array.isArray(reqMessages)) {
      const toolMsgs = reqMessages.filter(m => m && (m.role === 'tool' || (m.role === 'assistant' && m.tool_calls)));
      if (toolMsgs.length > 0) {
        toolHTML += `<h4 style="margin-top:0.75rem;font-size:0.85rem;color:#555;">Tool 交互记录</h4>`;
        toolHTML += `<div class="history-messages">`;
        toolMsgs.forEach(msg => {
          if (msg.role === 'assistant' && msg.tool_calls) {
            const calls = msg.tool_calls.map(tc => tc?.function?.name || tc?.name || 'unknown').join(', ');
            toolHTML += `<div class="history-msg history-msg-tool"><div class="history-msg-header">Tool Calls</div><div class="history-msg-content">${escapeHtml(calls)}</div></div>`;
          }
          if (msg.role === 'tool') {
            const content = normalizeMessageContent(msg.content) || '';
            const displayContent = content.length > 500 ? content.slice(0, 500) + '…' : content;
            toolHTML += `<div class="history-msg history-msg-tool"><div class="history-msg-header">Tool Result${msg.name ? ' (' + escapeHtml(msg.name) + ')' : ''}</div><div class="history-msg-content">${escapeHtml(displayContent)}</div></div>`;
          }
        });
        toolHTML += `</div>`;
      }
    }
    sections.push(buildSection({
      id: 'section-tools',
      icon: '🔧',
      iconClass: 'section-icon-tools',
      title: 'Tools',
      badge: tools ? `${tools.length} tools` : null,
      contentHTML: toolHTML,
      defaultOpen: false,
    }));
  }

  // 3. Skill Statistics section — analyze tool call patterns
  if (Array.isArray(reqMessages)) {
    const skillStats = analyzeSkillUsage(toolDefsArr, reqMessages);
    if (skillStats.totalDefined > 0 || skillStats.totalCalls > 0) {
      sections.push(buildSection({
        id: 'section-skills',
        icon: '📊',
        iconClass: 'section-icon-skills',
        title: 'Skill 统计',
        badge: `${skillStats.totalCalls} calls / ${skillStats.totalDefined} defined`,
        contentHTML: renderSkillStats(skillStats),
        defaultOpen: false,
      }));
    }
  }

  // 4. History section (all messages except last user & system)
  if (Array.isArray(reqMessages) && reqMessages.length > 0) {
    const { historyMessages, lastUserMessage } = categorizeMessages(reqMessages);
    if (historyMessages.length > 0) {
      let histHTML = `<div class="history-messages">`;
      historyMessages.forEach(msg => {
        const role = msg.role || 'unknown';
        const content = normalizeMessageContent(msg.content);
        if (!content && !(msg.tool_calls)) return;
        const roleClass = `history-msg-${role === 'user' ? 'user' : role === 'assistant' ? 'assistant' : role === 'tool' ? 'tool' : 'system'}`;
        const roleLabel = role.charAt(0).toUpperCase() + role.slice(1);
        if (content) {
          const displayContent = content.length > 800 ? content.slice(0, 800) + '…' : content;
          histHTML += `<div class="history-msg ${roleClass}"><div class="history-msg-header">${escapeHtml(roleLabel)}</div><div class="history-msg-content">${escapeHtml(displayContent)}</div></div>`;
        }
      });
      histHTML += `</div>`;
      sections.push(buildSection({
        id: 'section-history',
        icon: '💬',
        iconClass: 'section-icon-history',
        title: '历史消息',
        badge: `${historyMessages.length} messages`,
        contentHTML: histHTML,
        defaultOpen: false,
      }));
    }
  }

  // 5. User Prompt section
  if (resolvedUserPrompt) {
    sections.push(buildSection({
      id: 'section-user',
      icon: '👤',
      iconClass: 'section-icon-user',
      title: 'User Prompt',
      badge: resolvedUserPrompt.length > 0 ? `${resolvedUserPrompt.length} chars` : null,
      contentHTML: `<button class="section-copy-btn" onclick="copySectionText('section-user-pre')">Copy</button>
        <pre id="section-user-pre">${escapeHtml(resolvedUserPrompt)}</pre>`,
      defaultOpen: true,
    }));
  }

  // 6. Assistant Response section
  if (resolvedAssistant) {
    sections.push(buildSection({
      id: 'section-assistant',
      icon: '🤖',
      iconClass: 'section-icon-assistant',
      title: 'Assistant Response',
      badge: resolvedAssistant.length > 0 ? `${resolvedAssistant.length} chars` : null,
      contentHTML: `<button class="section-copy-btn" onclick="copySectionText('section-assistant-pre')">Copy</button>
        <pre id="section-assistant-pre">${escapeHtml(resolvedAssistant)}</pre>`,
      defaultOpen: true,
    }));
  }

  // 7. Performance Analysis section
  {
    const perf = analyzePerformance(detail, extUsage);
    if (perf.durationMs > 0 || perf.totalTokens > 0) {
      const speedBadge = perf.tokensPerSec != null ? `${perf.tokensPerSec.toFixed(1)} tok/s` : '';
      sections.push(buildSection({
        id: 'section-performance',
        icon: '📈',
        iconClass: 'section-icon-performance',
        title: '性能分析',
        badge: speedBadge,
        contentHTML: renderPerformanceSection(perf),
        defaultOpen: false,
      }));
    }
  }

  // 8. Content Analysis section
  {
    const content = analyzeContent(resolvedSystemPrompt, resolvedUserPrompt, resolvedAssistant, reqMessages);
    if (content.totalMessages > 0 || content.userLen > 0 || content.assistantLen > 0) {
      sections.push(buildSection({
        id: 'section-content',
        icon: '📝',
        iconClass: 'section-icon-content',
        title: '内容分析',
        badge: content.totalMessages > 0 ? `${content.totalMessages} msgs · ${content.turns} turns` : null,
        contentHTML: renderContentAnalysis(content),
        defaultOpen: false,
      }));
    }
  }

  // 9. Request Configuration section
  {
    const config = extractRequestConfig(rawRequestBody);
    if (config) {
      const paramCount = Object.keys(config).filter(k => !k.startsWith('_')).length;
      sections.push(buildSection({
        id: 'section-config',
        icon: '⚙️',
        iconClass: 'section-icon-config',
        title: '请求配置',
        badge: `${paramCount} params`,
        contentHTML: renderRequestConfig(config),
        defaultOpen: false,
      }));
    }
  }

  // 10. Optimization Hints section
  const hints = promptOptimizationHints({
    ...detail,
    system_prompt: resolvedSystemPrompt,
    user_prompt: resolvedUserPrompt,
    assistant_response: resolvedAssistant,
    prompt_tokens: resolvedPromptTokens,
    completion_tokens: resolvedCompletionTokens,
    total_tokens: resolvedTotalTokens,
    _extUsage: extUsage,
    _reqMessages: reqMessages,
  });
  sections.push(buildSection({
    id: 'section-hints',
    icon: '💡',
    iconClass: 'section-icon-hints',
    title: '优化建议',
    badge: null,
    contentHTML: `<ul class="analysis-list">${hints.map(h => `<li>${h}</li>`).join('')}</ul>`,
    defaultOpen: false,
  }));

  return sections.join('');
}

function buildSection({ id, icon, iconClass, title, badge, contentHTML, defaultOpen }) {
  return `<details class="collapsible-section" id="${id}"${defaultOpen ? ' open' : ''}>
    <summary class="section-summary">
      <span class="section-icon ${iconClass}">${icon}</span>
      <span>${escapeHtml(title)}</span>
      ${badge ? `<span class="section-badge">${escapeHtml(badge)}</span>` : ''}
      <span class="section-chevron">▶</span>
    </summary>
    <div class="section-content">${contentHTML}</div>
  </details>`;
}

function categorizeMessages(messages) {
  if (!Array.isArray(messages) || messages.length === 0) {
    return { historyMessages: [], lastUserMessage: null };
  }

  // Find the last user message index
  let lastUserIdx = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i] && messages[i].role === 'user') {
      lastUserIdx = i;
      break;
    }
  }

  const historyMessages = [];
  let lastUserMessage = null;

  messages.forEach((msg, idx) => {
    if (!msg) return;
    if (msg.role === 'system') return; // system shown in its own section
    if (idx === lastUserIdx) {
      lastUserMessage = msg;
      return;
    }
    // Skip tool-related messages (shown in tools section)
    if (msg.role === 'tool') return;
    if (msg.role === 'assistant' && msg.tool_calls) return;
    historyMessages.push(msg);
  });

  return { historyMessages, lastUserMessage };
}

function copySectionText(preId) {
  const el = document.getElementById(preId);
  if (!el) return;
  navigator.clipboard.writeText(el.textContent || '');
}

function hideConversationDetail() {
  selectedConversationId = null;
  document.getElementById('conv-modal-overlay').hidden = true;
  document.body.style.overflow = '';
  document.querySelectorAll('.conversation-row').forEach((row) => row.classList.remove('selected'));
}

function resetConversationFilters() {
  ['q', 'model-filter', 'template-filter', 'path-prefix-filter', 'request-type-filter', 'status-filter', 'date-from', 'date-to', 'sort-filter', 'order-filter', 'page-size-filter']
    .forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (id === 'sort-filter') el.value = 'timestamp';
      else if (id === 'order-filter') el.value = 'desc';
      else if (id === 'page-size-filter') el.value = '50';
      else if (id === 'path-prefix-filter') el.value = '/v1/';
      else if (id === 'request-type-filter') el.value = 'chat';
      else el.value = '';
    });
  loadConversations(1);
}

async function copyDetailField(targetId) {
  const el = document.getElementById(targetId);
  if (!el) return;
  const text = el.textContent || '';
  await navigator.clipboard.writeText(text);
}

function renderPagination(total, page, pageSize) {
  const el = document.getElementById('pagination');
  if (!el) return;
  const pages = Math.ceil(total / pageSize);
  el.innerHTML = '';
  for (let i = 1; i <= Math.min(pages, 20); i++) {
    const btn = document.createElement('button');
    btn.textContent = i;
    if (i === page) btn.classList.add('active');
    btn.onclick = () => loadConversations(i);
    el.appendChild(btn);
  }
}

// ── Costs page ────────────────────────────────────────────────────────────

let costsDays = 30;
let dailyCostChartInstance = null;

function _dateFromDays(days) {
  const d = new Date();
  d.setDate(d.getDate() - (days - 1));
  return d.toISOString().slice(0, 10);
}

async function loadCostsPage(days) {
  if (days != null) costsDays = days;
  const date_from = _dateFromDays(costsDays);
  const title = document.getElementById('daily-cost-chart-title');
  if (title) title.textContent = `Daily Cost (${costsDays} days)`;
  try {
    const [summary, daily, byModel] = await Promise.all([
      fetchJSON(`${API}/costs/summary?date_from=${date_from}`),
      fetchJSON(`${API}/costs/daily?days=${costsDays}`),
      fetchJSON(`${API}/costs/by-model?date_from=${date_from}`),
    ]);

    const summaryEl = document.getElementById('cost-summary');
    if (summaryEl) {
      summaryEl.innerHTML = `
        <div class="card"><div class="card-value">$${Number(summary.total_cost_usd).toFixed(4)}</div><div class="card-label">Total Cost</div></div>
        <div class="card"><div class="card-value">${fmt(summary.total_tokens)}</div><div class="card-label">Total Tokens</div></div>
        <div class="card"><div class="card-value">${fmt(summary.total_requests)}</div><div class="card-label">Requests</div></div>
      `;
    }

    const ctx = document.getElementById('daily-cost-chart');
    if (ctx) {
      if (dailyCostChartInstance) dailyCostChartInstance.destroy();
      dailyCostChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
          labels: daily.map(d => d.date),
          datasets: [{
            label: 'Cost (USD)',
            data: daily.map(d => d.cost_usd),
            borderColor: '#e67e22',
            backgroundColor: 'rgba(230,126,34,0.1)',
            tension: 0.3,
            fill: true,
          }],
        },
        options: { responsive: true },
      });
    }

    const tbody = document.getElementById('model-cost-tbody');
    if (tbody) {
      tbody.innerHTML = byModel.map(r => `
        <tr>
          <td>${r.model || '—'}</td>
          <td>${fmt(r.request_count)}</td>
          <td>${fmt(r.total_tokens)}</td>
          <td>$${Number(r.cost_usd || 0).toFixed(5)}</td>
        </tr>
      `).join('');
    }
  } catch (e) {
    console.error('Costs load error:', e);
  }
}

function initCostsTimeRange() {
  document.querySelectorAll('.range-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.range-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      loadCostsPage(parseInt(btn.dataset.range, 10));
    });
  });
}

// ── Prompts page ──────────────────────────────────────────────────────────

let tmplDailyChartInstance = null;

async function loadPromptsPage() {
  try {
    const data = await fetchJSON(`${API}/prompts/templates?page_size=50`);
    const tbody = document.getElementById('prompts-tbody');
    if (!tbody) return;
    tbody.innerHTML = data.items.map(r => `
      <tr class="conversation-row" data-template-id="${r.template_id}">
        <td><code>${r.template_id}</code></td>
        <td>${fmt(r.use_count)}</td>
        <td>$${Number(r.avg_cost_usd || 0).toFixed(5)}</td>
        <td>$${Number(r.total_cost_usd || 0).toFixed(5)}</td>
        <td>${r.last_seen ? r.last_seen.slice(0, 10) : '—'}</td>
        <td title="${(r.system_prompt_preview || '').replace(/"/g, '&quot;')}">${(r.system_prompt_preview || '').slice(0, 80)}${r.system_prompt_preview?.length > 80 ? '…' : ''}</td>
      </tr>
    `).join('');
    tbody.querySelectorAll('.conversation-row').forEach(row => {
      row.addEventListener('click', () => showTemplateDetail(row.dataset.templateId));
    });
  } catch (e) {
    console.error('Prompts load error:', e);
  }
}

async function showTemplateDetail(templateId) {
  try {
    const [tmpl, stats, daily, conversations, similar] = await Promise.all([
      fetchJSON(`${API}/prompts/templates/${templateId}`),
      fetchJSON(`${API}/prompts/templates/${templateId}/stats`).catch(() => null),
      fetchJSON(`${API}/prompts/templates/${templateId}/daily?days=30`).catch(() => []),
      fetchJSON(`${API}/prompts/templates/${templateId}/conversations?page_size=20`).catch(() => ({items:[]})),
      fetchJSON(`${API}/prompts/similar/${templateId}`).catch(() => []),
    ]);

    document.getElementById('template-detail').hidden = false;
    document.getElementById('tmpl-detail-id').textContent = templateId;

    // Stats cards
    const cardsEl = document.getElementById('tmpl-stats-cards');
    if (cardsEl && stats) {
      const scoreColor = stats.quality_score >= 70 ? '#10b981' : stats.quality_score >= 40 ? '#f59e0b' : '#ef4444';
      cardsEl.innerHTML = `
        <div class="card"><div class="card-value" style="color:${scoreColor}">${stats.quality_score}</div><div class="card-label">Quality Score</div></div>
        <div class="card"><div class="card-value">${fmt(stats.total_conversations)}</div><div class="card-label">Conversations</div></div>
        <div class="card"><div class="card-value">${(stats.success_rate * 100).toFixed(1)}%</div><div class="card-label">Success Rate</div></div>
        <div class="card"><div class="card-value">${fmt(stats.avg_duration_ms, 1)}</div><div class="card-label">Avg Latency (ms)</div></div>
        <div class="card"><div class="card-value">$${stats.total_cost_usd.toFixed(4)}</div><div class="card-label">Total Cost</div></div>
        <div class="card"><div class="card-value">${stats.avg_rating != null ? stats.avg_rating.toFixed(1) + ' ★' : '—'}</div><div class="card-label">Avg Rating (${stats.rated_count})</div></div>
      `;
    }

    // System prompt
    document.getElementById('tmpl-system-prompt').textContent = tmpl.system_prompt || '—';

    // Daily chart
    const dailyCtx = document.getElementById('tmpl-daily-chart');
    if (dailyCtx && daily.length > 0) {
      if (tmplDailyChartInstance) tmplDailyChartInstance.destroy();
      tmplDailyChartInstance = new Chart(dailyCtx, {
        type: 'bar',
        data: {
          labels: daily.map(d => d.date),
          datasets: [
            { label: 'Requests', data: daily.map(d => d.requests), backgroundColor: 'rgba(126,184,247,0.7)', yAxisID: 'y' },
            { label: 'Cost (USD)', data: daily.map(d => d.cost_usd), type: 'line', borderColor: '#e67e22', backgroundColor: 'transparent', yAxisID: 'y1', tension: 0.3, pointRadius: 3 },
          ],
        },
        options: {
          responsive: true,
          interaction: { mode: 'index', intersect: false },
          scales: {
            y: { position: 'left', title: { display: true, text: 'Requests' } },
            y1: { position: 'right', title: { display: true, text: 'Cost' }, grid: { drawOnChartArea: false } },
          },
        },
      });
    }

    // Similar templates
    const similarEl = document.getElementById('tmpl-similar-list');
    if (similarEl) {
      if (similar.length === 0) {
        similarEl.innerHTML = '<p class="muted">No similar templates found</p>';
      } else {
        similarEl.innerHTML = `<table class="data-table"><thead><tr><th>Template</th><th>Similarity</th><th>Uses</th><th>Avg Cost</th></tr></thead><tbody>` +
          similar.map(s => `<tr class="conversation-row" onclick="showTemplateDetail('${escapeHtml(s.template_id)}')">
            <td><code>${s.template_id}</code></td>
            <td>${(s.similarity * 100).toFixed(0)}%</td>
            <td>${fmt(s.use_count)}</td>
            <td>$${Number(s.avg_cost_usd || 0).toFixed(5)}</td>
          </tr>`).join('') + `</tbody></table>`;
      }
    }

    // Conversations table
    const convTbody = document.getElementById('tmpl-conversations-tbody');
    if (convTbody) {
      convTbody.innerHTML = (conversations.items || []).map(r => `
        <tr>
          <td>${r.timestamp ? r.timestamp.replace('T', ' ').slice(0, 19) : '—'}</td>
          <td>${r.model || '—'}</td>
          <td><span class="badge badge-${r.status === 'success' ? 'success' : 'error'}">${r.status}</span></td>
          <td>${fmt(r.total_tokens)}</td>
          <td>$${Number(r.cost_usd || 0).toFixed(5)}</td>
          <td>${fmt(r.duration_ms, 1)}</td>
          <td>${r.rating != null ? '★'.repeat(r.rating) : '—'}</td>
          <td>${escapeHtml(truncateText(r.user_prompt_preview, 60))}</td>
        </tr>
      `).join('');
    }
  } catch (e) {
    console.error('Template detail error:', e);
  }
}

function hideTemplateDetail() {
  document.getElementById('template-detail').hidden = true;
}

// ── Errors page ───────────────────────────────────────────────────────────

async function loadErrorsPage() {
  try {
    const [summary, recent, daily, byType] = await Promise.all([
      fetchJSON(`${API}/errors/summary`),
      fetchJSON(`${API}/errors/recent?limit=50`),
      fetchJSON(`${API}/errors/daily?days=30`).catch(() => []),
      fetchJSON(`${API}/errors/by-type?days=30`).catch(() => []),
    ]);

    const summaryEl = document.getElementById('error-summary');
    if (summaryEl) {
      summaryEl.innerHTML = `
        <div class="card"><div class="card-value">${fmt(summary.total_requests)}</div><div class="card-label">Total Requests</div></div>
        <div class="card"><div class="card-value">${fmt(summary.error_count)}</div><div class="card-label">Errors</div></div>
        <div class="card"><div class="card-value">${summary.error_rate != null ? (summary.error_rate * 100).toFixed(1) + '%' : '—'}</div><div class="card-label">Error Rate</div></div>
      `;
    }

    // Error trend chart
    const trendCtx = document.getElementById('error-trend-chart');
    if (trendCtx && daily.length > 0) {
      new Chart(trendCtx, {
        type: 'bar',
        data: {
          labels: daily.map(d => d.date),
          datasets: [{
            label: 'Errors',
            data: daily.map(d => d.error_count),
            backgroundColor: 'rgba(239,68,68,0.7)',
          }],
        },
        options: { responsive: true },
      });
    }

    // Error type pie chart
    const typeCtx = document.getElementById('error-type-chart');
    if (typeCtx && byType.length > 0) {
      const colors = ['#ef4444','#f59e0b','#8b5cf6','#0ea5e9','#10b981','#ec4899','#f97316','#06b6d4','#84cc16','#4f46e5'];
      new Chart(typeCtx, {
        type: 'doughnut',
        data: {
          labels: byType.map(d => d.error_type),
          datasets: [{
            data: byType.map(d => d.count),
            backgroundColor: colors.slice(0, byType.length),
          }],
        },
        options: {
          responsive: true,
          plugins: { legend: { position: 'right', labels: { boxWidth: 12, font: { size: 11 } } } },
        },
      });
    }

    const tbody = document.getElementById('errors-tbody');
    if (tbody) {
      tbody.innerHTML = recent.map(r => `
        <tr>
          <td>${r.timestamp ? r.timestamp.slice(0, 19).replace('T', ' ') : '—'}</td>
          <td>${r.model || '—'}</td>
          <td>${r.error_type || '—'}</td>
          <td>${r.status_code || '—'}</td>
          <td><span class="badge badge-error">${r.status}</span></td>
          <td>${(r.error_message || '').slice(0, 100)}</td>
        </tr>
      `).join('');
    }
  } catch (e) {
    console.error('Errors load error:', e);
  }
}

// ── Chat Bubbles ──────────────────────────────────────────────────────────

function renderChatBubbles(container, messages, fallbackAssistant) {
  if (!container) return;
  if (!Array.isArray(messages) || messages.length === 0) {
    container.innerHTML = '<div class="chat-empty">No messages to display</div>';
    return;
  }
  let html = '';
  messages.forEach((msg) => {
    if (!msg || typeof msg !== 'object') return;
    const role = msg.role || 'unknown';
    const content = normalizeMessageContent(msg.content);
    const toolCalls = msg.tool_calls;
    const roleClass = `chat-bubble-${role}`;
    const roleLabel = role.charAt(0).toUpperCase() + role.slice(1);

    if (content) {
      html += `<div class="chat-bubble ${roleClass}">
        <div class="chat-role">${escapeHtml(roleLabel)}</div>
        <div class="chat-content">${escapeHtml(content)}</div>
      </div>`;
    }
    if (Array.isArray(toolCalls) && toolCalls.length > 0) {
      const toolNames = toolCalls.map(tc => {
        if (tc && tc.function && tc.function.name) return tc.function.name;
        if (tc && tc.name) return tc.name;
        return 'unknown';
      });
      html += `<div class="chat-bubble chat-bubble-tool">
        <div class="chat-role">Tool Calls</div>
        <div class="chat-content">${escapeHtml(toolNames.join(', '))}</div>
      </div>`;
    }
  });
  // Append assistant response if not already in messages
  const hasAssistant = messages.some(m => m && m.role === 'assistant');
  if (!hasAssistant && fallbackAssistant && fallbackAssistant !== '—') {
    html += `<div class="chat-bubble chat-bubble-assistant">
      <div class="chat-role">Assistant</div>
      <div class="chat-content">${escapeHtml(fallbackAssistant)}</div>
    </div>`;
  }
  container.innerHTML = html || '<div class="chat-empty">No messages to display</div>';
}

// ── Latency page ──────────────────────────────────────────────────────────

let latencyDays = 30;
let latencyTrendChartInstance = null;
let latencyModelChartInstance = null;
let latencyDistChartInstance = null;

async function loadLatencyPage(days) {
  if (days != null) latencyDays = days;
  const titleEl = document.getElementById('latency-trend-title');
  if (titleEl) titleEl.textContent = `Daily Latency Trend (${latencyDays} days)`;
  try {
    const [summary, daily, byModel, dist] = await Promise.all([
      fetchJSON(`${API}/latency/summary`),
      fetchJSON(`${API}/latency/daily?days=${latencyDays}`),
      fetchJSON(`${API}/latency/by-model`),
      fetchJSON(`${API}/latency/distribution`),
    ]);

    // Cards
    document.getElementById('latency-p50').textContent = summary.p50 != null ? fmt(summary.p50, 1) : '—';
    document.getElementById('latency-p95').textContent = summary.p95 != null ? fmt(summary.p95, 1) : '—';
    document.getElementById('latency-p99').textContent = summary.p99 != null ? fmt(summary.p99, 1) : '—';
    document.getElementById('latency-avg').textContent = summary.avg != null ? fmt(summary.avg, 1) : '—';
    document.getElementById('latency-count').textContent = fmt(summary.count);

    // Daily trend
    const trendCtx = document.getElementById('latency-trend-chart');
    if (trendCtx) {
      if (latencyTrendChartInstance) latencyTrendChartInstance.destroy();
      latencyTrendChartInstance = new Chart(trendCtx, {
        type: 'line',
        data: {
          labels: daily.map(d => d.date),
          datasets: [{
            label: 'Avg Latency (ms)',
            data: daily.map(d => d.avg_ms),
            borderColor: '#4f46e5',
            backgroundColor: 'rgba(79,70,229,0.1)',
            tension: 0.3,
            fill: true,
          }],
        },
        options: { responsive: true },
      });
    }

    // By model bar chart
    const modelCtx = document.getElementById('latency-by-model-chart');
    if (modelCtx) {
      const sorted = byModel.slice().sort((a, b) => (b.avg_ms || 0) - (a.avg_ms || 0));
      if (latencyModelChartInstance) latencyModelChartInstance.destroy();
      latencyModelChartInstance = new Chart(modelCtx, {
        type: 'bar',
        data: {
          labels: sorted.map(m => m.model || 'unknown'),
          datasets: [{
            label: 'Avg Latency (ms)',
            data: sorted.map(m => m.avg_ms),
            backgroundColor: 'rgba(14,165,233,0.7)',
          }],
        },
        options: {
          responsive: true,
          indexAxis: sorted.length > 6 ? 'y' : 'x',
        },
      });
    }

    // Distribution histogram
    const distCtx = document.getElementById('latency-dist-chart');
    if (distCtx) {
      if (latencyDistChartInstance) latencyDistChartInstance.destroy();
      latencyDistChartInstance = new Chart(distCtx, {
        type: 'bar',
        data: {
          labels: dist.map(d => d.bucket),
          datasets: [{
            label: 'Requests',
            data: dist.map(d => d.count),
            backgroundColor: 'rgba(139,92,246,0.7)',
          }],
        },
        options: { responsive: true },
      });
    }

    // Table
    const tbody = document.getElementById('latency-model-tbody');
    if (tbody) {
      tbody.innerHTML = byModel.map(r => `
        <tr>
          <td>${r.model || '—'}</td>
          <td>${r.avg_ms != null ? fmt(r.avg_ms, 1) : '—'}</td>
          <td>${fmt(r.count)}</td>
        </tr>
      `).join('');
    }
  } catch (e) {
    console.error('Latency load error:', e);
  }
}

function initLatencyTimeRange() {
  document.querySelectorAll('.range-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.range-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      loadLatencyPage(parseInt(btn.dataset.range, 10));
    });
  });
}

// ── Rating & Tags ─────────────────────────────────────────────────────────

async function setConversationRating(convId, rating) {
  try {
    await requestJSON(`${API}/conversations/${convId}/rating`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rating }),
    });
    showConversationDetail(convId);
  } catch (e) { console.error('Rating error:', e); }
}

async function clearConversationRating(convId) {
  try {
    await requestJSON(`${API}/conversations/${convId}/rating`, { method: 'DELETE' });
    showConversationDetail(convId);
  } catch (e) { console.error('Clear rating error:', e); }
}

async function addConversationTag(convId) {
  const input = document.getElementById(`tag-input-${convId}`);
  if (!input || !input.value.trim()) return;
  const newTag = input.value.trim();
  // Get current tags from detail
  try {
    const detail = await fetchJSON(`${API}/conversations/${convId}`);
    let tags = [];
    try { tags = JSON.parse(detail.tags || '[]'); } catch { tags = []; }
    if (!tags.includes(newTag)) tags.push(newTag);
    await requestJSON(`${API}/conversations/${convId}/tags`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tags }),
    });
    showConversationDetail(convId);
  } catch (e) { console.error('Add tag error:', e); }
}

async function removeConversationTag(convId, tagToRemove) {
  try {
    const detail = await fetchJSON(`${API}/conversations/${convId}`);
    let tags = [];
    try { tags = JSON.parse(detail.tags || '[]'); } catch { tags = []; }
    tags = tags.filter(t => t !== tagToRemove);
    await requestJSON(`${API}/conversations/${convId}/tags`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tags }),
    });
    showConversationDetail(convId);
  } catch (e) { console.error('Remove tag error:', e); }
}

// ── Export ─────────────────────────────────────────────────────────────────

function exportConversations(format) {
  const params = collectConversationFilters();
  params.delete('page');
  params.delete('page_size');
  params.set('fmt', format);
  window.open(appendAuthToUrl(`${API}/conversations/export?${params}`), '_blank');
}

// ── Models page ───────────────────────────────────────────────────────────

let modelsDays = 30;
let modelDistChartInstance = null;
let modelCostDistChartInstance = null;

async function loadModelsPage(days) {
  if (days != null) modelsDays = days;
  const date_from = _dateFromDays(modelsDays);
  try {
    const usage = await fetchJSON(`${API}/models/usage?date_from=${date_from}`);

    const colors = [
      '#4f46e5', '#0ea5e9', '#10b981', '#f59e0b', '#ef4444',
      '#8b5cf6', '#ec4899', '#06b6d4', '#84cc16', '#f97316',
    ];

    // Request distribution doughnut
    const distCtx = document.getElementById('model-dist-chart');
    if (distCtx) {
      if (modelDistChartInstance) modelDistChartInstance.destroy();
      modelDistChartInstance = new Chart(distCtx, {
        type: 'doughnut',
        data: {
          labels: usage.map(m => m.model || 'unknown'),
          datasets: [{
            data: usage.map(m => m.request_count || 0),
            backgroundColor: colors.slice(0, usage.length),
          }],
        },
        options: {
          responsive: true,
          plugins: { legend: { position: 'right', labels: { boxWidth: 12, font: { size: 11 } } } },
        },
      });
    }

    // Cost distribution doughnut
    const costCtx = document.getElementById('model-cost-dist-chart');
    if (costCtx) {
      if (modelCostDistChartInstance) modelCostDistChartInstance.destroy();
      modelCostDistChartInstance = new Chart(costCtx, {
        type: 'doughnut',
        data: {
          labels: usage.map(m => m.model || 'unknown'),
          datasets: [{
            data: usage.map(m => m.cost_usd || 0),
            backgroundColor: colors.slice(0, usage.length),
          }],
        },
        options: {
          responsive: true,
          plugins: { legend: { position: 'right', labels: { boxWidth: 12, font: { size: 11 } } } },
        },
      });
    }

    // Table
    const tbody = document.getElementById('model-usage-tbody');
    if (tbody) {
      tbody.innerHTML = usage.map(r => {
        const total = (r.success_count || 0) + (r.error_count || 0);
        const rate = total > 0 ? ((r.success_count || 0) / total * 100).toFixed(1) + '%' : '—';
        return `
          <tr>
            <td>${r.model || '—'}</td>
            <td>${r.provider || '—'}</td>
            <td>${fmt(r.request_count)}</td>
            <td>${fmt(r.success_count)}</td>
            <td>${fmt(r.error_count)}</td>
            <td>${rate}</td>
            <td>${fmt(r.total_tokens)}</td>
            <td>$${Number(r.cost_usd || 0).toFixed(5)}</td>
            <td>${r.avg_duration_ms != null ? fmt(r.avg_duration_ms, 1) : '—'}</td>
          </tr>
        `;
      }).join('');
    }
  } catch (e) {
    console.error('Models load error:', e);
  }
}

function initModelsTimeRange() {
  document.querySelectorAll('.range-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.range-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      loadModelsPage(parseInt(btn.dataset.range, 10));
    });
  });
}

// ── Auto-detect page ──────────────────────────────────────────────────────

// ── Key Management UI ─────────────────────────────────────────────────────

function getCurrentPageLoader() {
  const path = window.location.pathname;
  if (path === '/' || path.endsWith('index.html')) return () => loadOverview();
  if (path.endsWith('analyzer.html')) return () => loadAnalyzerPage();
  if (path.endsWith('raw-logs.html')) return () => loadRawLogsPage(1);
  if (path.endsWith('costs.html')) return () => loadCostsPage();
  if (path.endsWith('latency.html')) return () => loadLatencyPage();
  if (path.endsWith('models.html')) return () => loadModelsPage();
  if (path.endsWith('prompts.html')) return () => loadPromptsPage();
  if (path.endsWith('errors.html')) return () => loadErrorsPage();
  if (path.endsWith('conversations.html')) {
    return () => {
      selectedConversationId = null;
      currentPage = 1;
      const detail = document.getElementById('conv-detail');
      if (detail) detail.hidden = true;
      return loadConversations(1);
    };
  }
  return null;
}

async function refreshCurrentPageData() {
  const loader = getCurrentPageLoader();
  if (!loader || getActiveKeyHashes().length === 0) return;
  await loader();
}

function reloadPageToEmptyState() {
  window.location.reload();
}

function setKeyManagerExpanded(expanded) {
  keyManagerExpanded = Boolean(expanded);
  renderKeyManager();
}

function buildKeyManagerSummary(hashes) {
  const activeCount = hashes.filter((item) => item.active).length;
  if (hashes.length === 0) return '未添加 Key';
  if (activeCount === 0) return `已保存 ${hashes.length} 个，未激活`;
  return `已激活 ${activeCount} / ${hashes.length}`;
}

function renderKeyManager() {
  const container = document.getElementById('key-manager');
  if (!container) return;
  const hashes = getStoredKeyHashes();

  if (hashes.length === 0) {
    container.innerHTML = `
      <button class="key-manager-trigger empty" type="button" onclick="showKeyModal('首次使用请添加 API Key')">
        <span class="key-manager-title">添加 API Key</span>
      </button>`;
    return;
  }

  const summary = buildKeyManagerSummary(hashes);
  const adminHint = hashes.some((item) => item.active)
    ? '<div class="key-manager-note">当前页面仅显示激活 hash 的数据；若激活组合含 admin hash，则自动按 admin 规则显示全量数据。</div>'
    : '<div class="key-manager-note warning">当前没有激活的 hash，请先启用至少一个。</div>';

  container.innerHTML = `
    <div class="key-manager-shell${keyManagerExpanded ? ' open' : ''}">
      <button class="key-manager-trigger" type="button" onclick="setKeyManagerExpanded(${keyManagerExpanded ? 'false' : 'true'})" aria-expanded="${keyManagerExpanded ? 'true' : 'false'}">
        <span class="key-manager-title">Key 管理</span>
        <span class="key-manager-summary">${escapeHtml(summary)}</span>
      </button>
      <div class="key-manager-popover" ${keyManagerExpanded ? '' : 'hidden'}>
        <div class="key-manager-toolbar">
          <div>
            <div class="key-manager-heading">本地已保存 ${hashes.length} 个 hash</div>
            ${adminHint}
          </div>
          <div class="key-manager-toolbar-actions">
            <button type="button" class="key-toolbar-btn" onclick="setAllKeysActive(true)">全部激活</button>
            <button type="button" class="key-toolbar-btn" onclick="setAllKeysActive(false)">全部停用</button>
            <button type="button" class="key-toolbar-btn primary" onclick="showKeyModal()">新增</button>
          </div>
        </div>
        <div class="key-manager-list">
          ${hashes.map((item) => `
            <div class="key-item${item.active ? ' active' : ''}">
              <label class="key-item-toggle">
                <input type="checkbox" ${item.active ? 'checked' : ''} onchange="toggleKeyActive('${item.hash}', this.checked)" />
                <span>${item.active ? '已激活' : '未激活'}</span>
              </label>
              <div class="key-item-body">
                <div class="key-item-label-row">
                  <span class="key-item-label">${escapeHtml(item.label || defaultKeyLabel(item.hash))}</span>
                  <span class="key-item-hash">${escapeHtml(formatHashPreview(item.hash))}</span>
                </div>
                <div class="key-item-meta" title="${escapeHtml(item.hash)}">${escapeHtml(item.hash)}</div>
              </div>
              <div class="key-item-actions">
                <button type="button" class="key-item-btn" onclick="showEditKeyModal('${item.hash}')">别名</button>
                <button type="button" class="key-item-btn" onclick="copyHash('${item.hash}')">复制</button>
                <button type="button" class="key-item-btn danger" onclick="removeKey('${item.hash}')">删除</button>
              </div>
            </div>
          `).join('')}
        </div>
      </div>
    </div>`;
}

function showKeyModal(message = '', options = {}) {
  const mode = options.mode === 'edit' ? 'edit' : 'add';
  const existing = mode === 'edit'
    ? getStoredKeyHashes().find((item) => item.hash === options.hash)
    : null;
  let modal = document.getElementById('key-modal');
  if (modal) modal.remove();

  modal = document.createElement('div');
  modal.id = 'key-modal';
  modal.className = 'modal-overlay';
  modal.innerHTML = `
    <div class="modal-box key-modal-box">
      <h3>${mode === 'edit' ? '修改别名' : '添加 API Key / Hash'}</h3>
      ${message ? `<p class="key-modal-msg">${escapeHtml(message)}</p>` : ''}
      ${mode === 'edit' ? `
        <p class="key-modal-hint">原始 API Key 不会显示，当前只管理 hash 的展示别名。</p>
        <div class="key-modal-readonly">
          <span>Hash</span>
          <strong>${escapeHtml(existing ? existing.hash : options.hash || '')}</strong>
        </div>
      ` : `
        <p class="key-modal-hint">输入您的 LLM API Key，系统会在浏览器中计算 SHA-256 hash。<br>原始 Key 不会被发送或存储，添加完成后页面仅显示 hash。</p>
        <input type="password" id="key-modal-input" class="key-modal-input" placeholder="sk-..." autocomplete="off" />
        <div class="key-modal-or">── 或直接输入 Hash ──</div>
        <input type="text" id="key-modal-hash-input" class="key-modal-input" placeholder="已知的 32 位 hex hash" maxlength="32" />
      `}
      <input type="text" id="key-modal-label" class="key-modal-input" placeholder="别名（可选，如 Production）" value="${escapeHtml(existing ? existing.label : '')}" />
      <div class="key-modal-actions">
        <button class="btn" onclick="closeKeyModal()">取消</button>
        <button class="btn btn-primary" onclick="${mode === 'edit' ? `submitKeyLabel('${options.hash || ''}')` : 'submitKey()'}">${mode === 'edit' ? '保存' : '添加'}</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click', (e) => {
    if (e.target === modal) closeKeyModal();
  });
  const focusTarget = document.getElementById(mode === 'edit' ? 'key-modal-label' : 'key-modal-input');
  if (focusTarget) focusTarget.focus();
}

function showEditKeyModal(hash) {
  showKeyModal('', { mode: 'edit', hash });
}

function closeKeyModal() {
  const modal = document.getElementById('key-modal');
  if (modal) modal.remove();
}

async function submitKey() {
  const apiKeyInput = document.getElementById('key-modal-input');
  const labelInput = document.getElementById('key-modal-label');
  const hashInput = document.getElementById('key-modal-hash-input');

  const rawKey = (apiKeyInput.value || '').trim();
  const directHash = hashInput ? (hashInput.value || '').trim() : '';
  const label = (labelInput.value || '').trim();

  let hash = '';
  if (rawKey) {
    hash = await computeKeyHash(rawKey);
  } else if (directHash && /^[0-9a-f]{32}$/i.test(directHash)) {
    hash = directHash.toLowerCase();
  } else {
    showToast('请输入 API Key 或有效的 32 位 hex hash', 'error');
    return;
  }

  const result = upsertKeyHash(hash, label);
  closeKeyModal();
  keyManagerExpanded = true;
  renderKeyManager();
  await refreshAdminAccessState({ force: true });
  if (result.status === 'added') {
    showToast('已新增 hash: ' + formatHashPreview(hash), 'success');
  } else {
    showToast('已更新现有 hash，并设为激活', 'info');
  }
  await refreshCurrentPageData();
}

function submitKeyLabel(hash) {
  const labelInput = document.getElementById('key-modal-label');
  const label = (labelInput?.value || '').trim();
  if (!updateKeyLabel(hash, label)) {
    showToast('未找到要更新的 hash', 'error');
    return;
  }
  closeKeyModal();
  renderKeyManager();
  showToast('别名已更新', 'success');
}

async function toggleKeyActive(hash, active) {
  setKeyHashActive(hash, active);
  keyManagerExpanded = true;
  renderKeyManager();
  await refreshAdminAccessState({ force: true });
  if (getActiveKeyHashes().length === 0) {
    showToast('当前没有激活的 hash，请先启用至少一个', 'warning');
    reloadPageToEmptyState();
    return;
  }
  await refreshCurrentPageData();
}

async function setAllKeysActive(active) {
  setAllKeyHashesActive(active);
  keyManagerExpanded = true;
  renderKeyManager();
  await refreshAdminAccessState({ force: true });
  if (!active) {
    showToast('已停用全部 hash', 'warning');
    reloadPageToEmptyState();
    return;
  }
  showToast('已激活全部 hash', 'success');
  await refreshCurrentPageData();
}

async function removeKey(hash) {
  removeKeyHash(hash);
  keyManagerExpanded = true;
  renderKeyManager();
  await refreshAdminAccessState({ force: true });
  showToast('Key 已移除', 'info');
  if (getActiveKeyHashes().length === 0) {
    reloadPageToEmptyState();
    return;
  }
  await refreshCurrentPageData();
}

async function copyHash(hash) {
  // 优先用 Clipboard API；降级到 execCommand（兼容 Playwright / 受限 iframe）
  let ok = false;
  try {
    await navigator.clipboard.writeText(hash);
    ok = true;
  } catch {
    try {
      const ta = document.createElement('textarea');
      ta.value = hash;
      ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      ok = document.execCommand('copy');
      document.body.removeChild(ta);
    } catch { /* ignore */ }
  }
  if (ok) {
    showToast('Hash 已复制到剪贴板', 'success', 2000);
  } else {
    showToast('复制失败，请手动复制：' + hash, 'error', 6000);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  renderAppShell();
  syncThemeToggle();

  // Initialize key management UI
  renderKeyManager();
  if (!hasStoredKeyHashes()) {
    showKeyModal('首次使用请添加 API Key');
  } else if (getActiveKeyHashes().length === 0) {
    keyManagerExpanded = true;
    renderKeyManager();
    showToast('当前没有激活的 hash，请先启用至少一个', 'warning');
  } else {
    refreshAdminAccessState().catch((error) => console.error('Admin access detect error:', error));
  }

  const path = window.location.pathname;
  if (path === '/' || path.endsWith('index.html')) {
    initOverviewTimeRange();
    loadOverview();
  }

  if (path.endsWith('costs.html')) {
    initCostsTimeRange();
    loadCostsPage();
  }

  if (path.endsWith('latency.html')) {
    initLatencyTimeRange();
    loadLatencyPage();
  }

  if (path.endsWith('models.html')) {
    initModelsTimeRange();
    loadModelsPage();
  }

  if (path.endsWith('analyzer.html')) {
    loadAnalyzerPage();
  }

  if (path.endsWith('raw-logs.html')) {
    loadRawLogsPage(1);
  }

  const q = document.getElementById('q');
  if (q) {
    q.addEventListener('keydown', (evt) => {
      if (evt.key === 'Enter') loadConversations(1);
    });
  }

  const rawQ = document.getElementById('raw-q');
  if (rawQ) {
    rawQ.addEventListener('keydown', (evt) => {
      if (evt.key === 'Enter') loadRawLogsPage(1);
    });
  }

  document.body.addEventListener('click', async (evt) => {
    const btn = evt.target.closest('[data-copy-target]');
    if (!btn) return;
    const targetId = btn.getAttribute('data-copy-target');
    if (!targetId) return;
    try {
      await copyDetailField(targetId);
      btn.textContent = 'Copied';
      setTimeout(() => { btn.textContent = 'Copy'; }, 1200);
    } catch (e) {
      console.error('Copy failed:', e);
    }
  });

  const rawOverlay = document.getElementById('raw-log-modal-overlay');
  if (rawOverlay) {
    document.addEventListener('keydown', (evt) => {
      if (evt.key === 'Escape' && !rawOverlay.hidden) {
        hideRawLogDetail();
      }
    });
    rawOverlay.addEventListener('click', (evt) => {
      if (evt.target === evt.currentTarget) hideRawLogDetail();
    });
  }

  document.addEventListener('click', (evt) => {
    const manager = document.getElementById('key-manager');
    if (!keyManagerExpanded || !manager) return;
    // composedPath() 捕获事件派发时的原始路径，防止 renderKeyManager() 重渲染后
    // evt.target 已不在 DOM 中导致误判为「外部点击」从而立刻关闭弹窗
    if (evt.composedPath().includes(manager)) return;
    setKeyManagerExpanded(false);
  });
});
