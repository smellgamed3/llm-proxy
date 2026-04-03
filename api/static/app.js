// LLM Proxy Analytics Dashboard — app.js

const API = '/api';

async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

function fmt(n, decimals = 0) {
  if (n == null) return '—';
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: decimals });
}

// ── Overview page ─────────────────────────────────────────────────────────

async function loadOverview() {
  try {
    const [summary, daily] = await Promise.all([
      fetchJSON(`${API}/overview`),
      fetchJSON(`${API}/overview/daily?days=7`),
    ]);

    document.getElementById('total-requests').textContent = fmt(summary.total_requests);
    document.getElementById('success-rate').textContent =
      summary.success_rate != null ? (summary.success_rate * 100).toFixed(1) + '%' : '—';
    document.getElementById('total-cost').textContent =
      summary.total_cost_usd != null ? '$' + Number(summary.total_cost_usd).toFixed(4) : '—';
    document.getElementById('avg-latency').textContent =
      summary.avg_duration_ms != null ? fmt(summary.avg_duration_ms, 1) : '—';

    renderTrendChart(daily);
  } catch (e) {
    console.error('Overview load error:', e);
  }
}

function renderTrendChart(daily) {
  const ctx = document.getElementById('trend-chart');
  if (!ctx) return;
  new Chart(ctx, {
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

function promptOptimizationHints(detail) {
  const hints = [];
  const promptTokens = Number(detail.prompt_tokens || 0);
  const completionTokens = Number(detail.completion_tokens || 0);
  const totalTokens = Number(detail.total_tokens || 0);
  const userPrompt = detail.user_prompt || '';
  const systemPrompt = detail.system_prompt || '';
  const assistantResponse = detail.assistant_response || '';

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
  if (hints.length === 0) {
    hints.push('未发现明显异常，可继续按模型、模板、时段进行横向对比优化。');
  }
  return hints;
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
        <td>${fmt(r.prompt_tokens)} / ${fmt(r.completion_tokens)}</td>
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

    document.getElementById('conversation-detail').hidden = false;
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
    document.getElementById('detail-system-prompt').textContent = detail.system_prompt || '—';
    document.getElementById('detail-user-prompt').textContent = detail.user_prompt || '—';
    document.getElementById('detail-assistant-response').textContent = detail.assistant_response || '—';
    document.getElementById('detail-request-body').textContent = prettyJSONOrText(raw.request_body);
    document.getElementById('detail-response-body').textContent = prettyJSONOrText(raw.response_body);
    document.getElementById('detail-request-headers').textContent = prettyJSONOrText(raw.request_headers);
    document.getElementById('detail-response-headers').textContent = prettyJSONOrText(raw.response_headers);

    const tools = Array.isArray(detail.tools_list) ? detail.tools_list : maybeJSON(detail.tools_list);
    document.getElementById('detail-tools-list').textContent = tools && tools.length
      ? JSON.stringify(tools, null, 2)
      : 'No tool calls detected';

    const promptTokens = Number(detail.prompt_tokens || 0);
    const completionTokens = Number(detail.completion_tokens || 0);
    const total = Math.max(promptTokens + completionTokens, 1);
    const promptRatio = Math.max(4, Math.round((promptTokens / total) * 100));
    const completionRatio = Math.max(4, Math.round((completionTokens / total) * 100));
    document.getElementById('bar-prompt').style.width = `${promptRatio}%`;
    document.getElementById('bar-completion').style.width = `${completionRatio}%`;
    document.getElementById('token-breakdown-meta').textContent =
      `Prompt ${fmt(promptTokens)} (${promptRatio}%) · Completion ${fmt(completionTokens)} (${completionRatio}%) · Total ${fmt(detail.total_tokens)}`;

    const analysisEl = document.getElementById('detail-analysis');
    if (analysisEl) {
      analysisEl.innerHTML = promptOptimizationHints(detail)
        .map((text) => `<li>${text}</li>`)
        .join('');
    }

    document.querySelectorAll('.conversation-row').forEach((row) => {
      row.classList.toggle('selected', row.dataset.conversationId === conversationId);
    });
  } catch (e) {
    console.error('Conversation detail load error:', e);
  }
}

function hideConversationDetail() {
  selectedConversationId = null;
  document.getElementById('conversation-detail').hidden = true;
  document.querySelectorAll('.conversation-row').forEach((row) => row.classList.remove('selected'));
}

function resetConversationFilters() {
  ['q', 'model-filter', 'template-filter', 'status-filter', 'date-from', 'date-to', 'sort-filter', 'order-filter', 'page-size-filter']
    .forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (id === 'sort-filter') el.value = 'timestamp';
      else if (id === 'order-filter') el.value = 'desc';
      else if (id === 'page-size-filter') el.value = '50';
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

async function loadCostsPage() {
  try {
    const [summary, daily, byModel] = await Promise.all([
      fetchJSON(`${API}/costs/summary`),
      fetchJSON(`${API}/costs/daily?days=30`),
      fetchJSON(`${API}/costs/by-model`),
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
      new Chart(ctx, {
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

// ── Prompts page ──────────────────────────────────────────────────────────

async function loadPromptsPage() {
  try {
    const data = await fetchJSON(`${API}/prompts/templates?page_size=50`);
    const tbody = document.getElementById('prompts-tbody');
    if (!tbody) return;
    tbody.innerHTML = data.items.map(r => `
      <tr>
        <td><code>${r.template_id}</code></td>
        <td>${fmt(r.use_count)}</td>
        <td>$${Number(r.avg_cost_usd || 0).toFixed(5)}</td>
        <td>${r.last_seen ? r.last_seen.slice(0, 10) : '—'}</td>
        <td title="${(r.system_prompt_preview || '').replace(/"/g, '&quot;')}">${(r.system_prompt_preview || '').slice(0, 80)}${r.system_prompt_preview?.length > 80 ? '…' : ''}</td>
      </tr>
    `).join('');
  } catch (e) {
    console.error('Prompts load error:', e);
  }
}

// ── Errors page ───────────────────────────────────────────────────────────

async function loadErrorsPage() {
  try {
    const [summary, recent] = await Promise.all([
      fetchJSON(`${API}/errors/summary`),
      fetchJSON(`${API}/errors/recent?limit=50`),
    ]);

    const summaryEl = document.getElementById('error-summary');
    if (summaryEl) {
      summaryEl.innerHTML = `
        <div class="card"><div class="card-value">${fmt(summary.total_requests)}</div><div class="card-label">Total Requests</div></div>
        <div class="card"><div class="card-value">${fmt(summary.error_count)}</div><div class="card-label">Errors</div></div>
        <div class="card"><div class="card-value">${summary.error_rate != null ? (summary.error_rate * 100).toFixed(1) + '%' : '—'}</div><div class="card-label">Error Rate</div></div>
      `;
    }

    const tbody = document.getElementById('errors-tbody');
    if (tbody) {
      tbody.innerHTML = recent.map(r => `
        <tr>
          <td>${r.timestamp ? r.timestamp.slice(0, 19).replace('T', ' ') : '—'}</td>
          <td>${r.model || '—'}</td>
          <td>${r.error_type || '—'}</td>
          <td><span class="badge badge-error">${r.status}</span></td>
          <td>${(r.error_message || '').slice(0, 100)}</td>
        </tr>
      `).join('');
    }
  } catch (e) {
    console.error('Errors load error:', e);
  }
}

// ── Auto-detect page ──────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  const path = window.location.pathname;
  if (path === '/' || path.endsWith('index.html')) loadOverview();

  const tbody = document.getElementById('conv-tbody');
  if (tbody) {
    const q = document.getElementById('q');
    if (q) {
      q.addEventListener('keydown', (evt) => {
        if (evt.key === 'Enter') loadConversations(1);
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
  }
});
