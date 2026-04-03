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
    reqMessages, tools,
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
