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

async function loadOverview() {
  try {
    const [summary, daily, modelUsage] = await Promise.all([
      fetchJSON(`${API}/overview`),
      fetchJSON(`${API}/overview/daily?days=${overviewDays}`),
      fetchJSON(`${API}/models/usage`),
    ]);

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
  } catch (e) {
    console.error('Overview load error:', e);
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
    return { prompt, completion, total };
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

    const resolvedSystemPrompt = detail.system_prompt || fallbackPrompts.systemPrompt || '—';
    const resolvedUserPrompt = detail.user_prompt || fallbackPrompts.userPrompt || '—';
    const resolvedAssistant = detail.assistant_response || fallbackAssistant || '—';

    const resolvedPromptTokens = detail.prompt_tokens ?? fallbackUsage?.prompt ?? null;
    const resolvedCompletionTokens = detail.completion_tokens ?? fallbackUsage?.completion ?? null;
    const resolvedTotalTokens = detail.total_tokens ?? fallbackUsage?.total ?? null;

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
    document.getElementById('detail-system-prompt').textContent = resolvedSystemPrompt;
    document.getElementById('detail-user-prompt').textContent = resolvedUserPrompt;
    document.getElementById('detail-assistant-response').textContent = resolvedAssistant;
    document.getElementById('detail-request-body').textContent = prettyJSONOrText(raw.request_body);
    document.getElementById('detail-response-body').textContent = prettyJSONOrText(raw.response_body);
    document.getElementById('detail-request-headers').textContent = prettyJSONOrText(raw.request_headers);
    document.getElementById('detail-response-headers').textContent = prettyJSONOrText(raw.response_headers);

    const tools = (Array.isArray(detail.tools_list) ? detail.tools_list : maybeJSON(detail.tools_list)) || fallbackTools;
    document.getElementById('detail-tools-list').textContent = tools && tools.length
      ? JSON.stringify(tools, null, 2)
      : 'No tool calls detected';

    const promptTokens = Number(resolvedPromptTokens || 0);
    const completionTokens = Number(resolvedCompletionTokens || 0);
    const total = Math.max(promptTokens + completionTokens, 1);
    const promptRatio = Math.max(4, Math.round((promptTokens / total) * 100));
    const completionRatio = Math.max(4, Math.round((completionTokens / total) * 100));
    document.getElementById('bar-prompt').style.width = `${promptRatio}%`;
    document.getElementById('bar-completion').style.width = `${completionRatio}%`;
    document.getElementById('token-breakdown-meta').textContent =
      `Prompt ${fmt(promptTokens)} (${promptRatio}%) · Completion ${fmt(completionTokens)} (${completionRatio}%) · Total ${fmt(resolvedTotalTokens)}`;

    const analysisEl = document.getElementById('detail-analysis');
    if (analysisEl) {
      analysisEl.innerHTML = promptOptimizationHints({
        ...detail,
        system_prompt: resolvedSystemPrompt,
        user_prompt: resolvedUserPrompt,
        assistant_response: resolvedAssistant,
        prompt_tokens: resolvedPromptTokens,
        completion_tokens: resolvedCompletionTokens,
        total_tokens: resolvedTotalTokens,
      })
        .map((text) => `<li>${text}</li>`)
        .join('');
    }

    // Render chat bubbles
    const bubblesEl = document.getElementById('detail-chat-bubbles');
    if (bubblesEl) {
      renderChatBubbles(bubblesEl, reqMessages, resolvedAssistant);
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

async function loadLatencyPage() {
  try {
    const [summary, daily, byModel, dist] = await Promise.all([
      fetchJSON(`${API}/latency/summary`),
      fetchJSON(`${API}/latency/daily?days=30`),
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
      new Chart(trendCtx, {
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
      new Chart(modelCtx, {
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
      new Chart(distCtx, {
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

// ── Rating & Tags ─────────────────────────────────────────────────────────

async function setConversationRating(convId, rating) {
  try {
    await fetch(`${API}/conversations/${convId}/rating`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rating }),
    });
    showConversationDetail(convId);
  } catch (e) { console.error('Rating error:', e); }
}

async function clearConversationRating(convId) {
  try {
    await fetch(`${API}/conversations/${convId}/rating`, { method: 'DELETE' });
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
    await fetch(`${API}/conversations/${convId}/tags`, {
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
    await fetch(`${API}/conversations/${convId}/tags`, {
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
  window.open(`${API}/conversations/export?${params}`, '_blank');
}

// ── Models page ───────────────────────────────────────────────────────────

async function loadModelsPage() {
  try {
    const usage = await fetchJSON(`${API}/models/usage`);

    const colors = [
      '#4f46e5', '#0ea5e9', '#10b981', '#f59e0b', '#ef4444',
      '#8b5cf6', '#ec4899', '#06b6d4', '#84cc16', '#f97316',
    ];

    // Request distribution doughnut
    const distCtx = document.getElementById('model-dist-chart');
    if (distCtx) {
      new Chart(distCtx, {
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
      new Chart(costCtx, {
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

// ── Auto-detect page ──────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  const path = window.location.pathname;
  if (path === '/' || path.endsWith('index.html')) {
    initOverviewTimeRange();
    loadOverview();
  }

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
