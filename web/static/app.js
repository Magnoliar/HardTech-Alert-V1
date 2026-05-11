/**
 * HardTech Writer — Frontend Logic
 */

// ─── State ───
let currentTaskId = null;
let eventSource = null;
let logMode = 'simple';
let apiConfig = { base_url: '', api_key: '', model: '' };

// ─── Init ───
document.addEventListener('DOMContentLoaded', () => {
  checkAuth();
  loadStyles();
  loadHistory();
  loadSavedConfig();
  setupInputValidation();
});

// ─── Auth ───
async function checkAuth() {
  try {
    const res = await fetch('/api/history');
    if (res.status === 401) {
      showAuthModal();
    }
  } catch {
    showAuthModal();
  }
}

function showAuthModal() {
  document.getElementById('auth-modal').classList.remove('hidden');
  document.getElementById('auth-key').focus();
}

function hideAuthModal() {
  document.getElementById('auth-modal').classList.add('hidden');
}

async function doAuth() {
  const key = document.getElementById('auth-key').value.trim();
  if (!key) return;
  try {
    const res = await fetch('/api/auth', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key }),
    });
    const data = await res.json();
    if (data.ok) {
      hideAuthModal();
      loadStyles();
      loadHistory();
    } else {
      const err = document.getElementById('auth-error');
      err.textContent = data.error || '密钥错误';
      err.classList.remove('hidden');
    }
  } catch (e) {
    const err = document.getElementById('auth-error');
    err.textContent = '连接失败';
    err.classList.remove('hidden');
  }
}

// Enter key on auth input
document.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !document.getElementById('auth-modal').classList.contains('hidden')) {
    doAuth();
  }
});

// ─── Styles ───
async function loadStyles() {
  try {
    const res = await fetch('/api/styles');
    const data = await res.json();
    const sel = document.getElementById('style-existing');
    sel.innerHTML = '';
    const styles = data.styles || {};
    for (const [key, val] of Object.entries(styles)) {
      const opt = document.createElement('option');
      opt.value = key;
      opt.textContent = val.name || key;
      sel.appendChild(opt);
    }
    if (Object.keys(styles).length === 0) {
      sel.innerHTML = '<option value="">无可用风格</option>';
    }
  } catch {
    document.getElementById('style-existing').innerHTML = '<option value="">加载失败</option>';
  }
}

function switchStyleMode() {
  const mode = document.querySelector('input[name="style-mode"]:checked').value;
  document.getElementById('style-text').classList.toggle('hidden', mode !== 'text');
  document.getElementById('style-link').classList.toggle('hidden', mode !== 'link');
  document.getElementById('style-existing').disabled = mode !== 'existing';
}

// ─── Model Config ───
function toggleModelPanel() {
  document.getElementById('model-panel').classList.toggle('hidden');
}

function loadSavedConfig() {
  try {
    const saved = localStorage.getItem('hw_api_config');
    if (saved) {
      apiConfig = JSON.parse(saved);
      document.getElementById('cfg-base-url').value = apiConfig.base_url || '';
      document.getElementById('cfg-api-key').value = apiConfig.api_key || '';
      document.getElementById('cfg-model').value = apiConfig.model || '';
      updateModelDisplay();
    }
  } catch {}
}

function saveApiConfig() {
  apiConfig.base_url = document.getElementById('cfg-base-url').value.trim();
  apiConfig.api_key = document.getElementById('cfg-api-key').value.trim();
  apiConfig.model = document.getElementById('cfg-model').value.trim();
  localStorage.setItem('hw_api_config', JSON.stringify(apiConfig));
  updateModelDisplay();
  toggleModelPanel();
  showToast('配置已保存');
}

function updateModelDisplay() {
  const label = apiConfig.model || 'gemini-3.1-flash-lite-preview';
  document.getElementById('current-model').textContent = label;
}

// ─── Input Validation ───
function setupInputValidation() {
  const inputs = ['inp-keywords', 'inp-outline', 'inp-ideas'];
  inputs.forEach(id => {
    document.getElementById(id).addEventListener('input', validateInputs);
  });
  // Check links on input
  document.getElementById('links-container').addEventListener('input', validateInputs);
}

function validateInputs() {
  const keywords = document.getElementById('inp-keywords').value.trim();
  const outline = document.getElementById('inp-outline').value.trim();
  const ideas = document.getElementById('inp-ideas').value.trim();
  const links = getLinks().filter(l => l.trim());
  const hasInput = !!(keywords || outline || ideas || links.length);
  document.getElementById('btn-write').disabled = !hasInput;
}

function getLinks() {
  return Array.from(document.querySelectorAll('.inp-link')).map(el => el.value.trim()).filter(Boolean);
}

function addLinkInput() {
  const container = document.getElementById('links-container');
  const div = document.createElement('div');
  div.className = 'flex gap-2 mb-2';
  div.innerHTML = `
    <input class="inp-link flex-1 border rounded-lg px-4 py-2.5 focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none"
           placeholder="https://example.com/article">
    <button onclick="this.parentElement.remove(); validateInputs()" class="px-3 py-2 bg-red-50 rounded-lg hover:bg-red-100 text-red-500">-</button>
  `;
  container.appendChild(div);
}

// ─── Example Data ───
function fillExample() {
  document.getElementById('inp-keywords').value = 'CoWoS 先进封装 台积电';
  document.getElementById('inp-outline').value = '产业背景\n技术路径拆解\n竞争格局分析\n投资逻辑';
  document.getElementById('inp-ideas').value = '分析台积电 CoWoS 产能扩张对封测产业链的影响';
  validateInputs();
  showToast('已填入示例数据');
}

// ─── Writing ───
async function startWriting() {
  const keywords = document.getElementById('inp-keywords').value.trim();
  const outline = document.getElementById('inp-outline').value.trim();
  const ideas = document.getElementById('inp-ideas').value.trim();
  const links = getLinks();

  if (!keywords && !outline && !ideas && links.length === 0) {
    showToast('请至少填写一项输入');
    return;
  }

  // Style
  const styleMode = document.querySelector('input[name="style-mode"]:checked').value;
  const styleId = styleMode === 'existing' ? document.getElementById('style-existing').value : '';
  const styleText = styleMode === 'text' ? document.getElementById('style-text').value.trim() : '';
  const styleLink = styleMode === 'link' ? document.getElementById('style-link').value.trim() : '';

  // Advanced
  const chapters = parseInt(document.getElementById('adv-chapters').value) || 4;

  // Build outline array
  const outlineArr = outline ? outline.split('\n').map(s => s.trim()).filter(Boolean) : [];

  const body = {
    keywords,
    outline: outlineArr,
    ideas,
    links,
    style_mode: styleMode,
    style_id: styleId,
    style_text: styleText,
    style_link: styleLink,
  };

  // API config override
  if (apiConfig.model) {
    body.api_config = { ...apiConfig };
  }

  try {
    document.getElementById('btn-write').disabled = true;
    document.getElementById('btn-write').textContent = '⏳ 启动中...';

    const res = await fetch('/api/write', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();

    if (data.error) {
      showToast(data.error);
      resetWriteButton();
      return;
    }

    currentTaskId = data.task_id;
    showProgressSection();
    connectSSE(currentTaskId);
  } catch (e) {
    showToast('请求失败: ' + e.message);
    resetWriteButton();
  }
}

function resetWriteButton() {
  const btn = document.getElementById('btn-write');
  btn.disabled = false;
  btn.textContent = '🚀 开始写作';
}

// ─── Progress / SSE ───
function showProgressSection() {
  document.getElementById('progress-section').classList.remove('hidden');
  document.getElementById('result-section').classList.add('hidden');
  document.getElementById('simple-steps').innerHTML = '';
  document.getElementById('verbose-log').innerHTML = '';
  document.getElementById('progress-bar').style.width = '0%';
}

function connectSSE(taskId) {
  if (eventSource) eventSource.close();
  eventSource = new EventSource(`/api/write/${taskId}/logs`);

  let totalChapters = 4;
  let doneChapters = 0;

  eventSource.addEventListener('log', (e) => {
    const data = JSON.parse(e.data);
    appendLog(data.msg || data.message || '', data.level || 'info');
  });

  eventSource.addEventListener('progress', (e) => {
    const data = JSON.parse(e.data);
    if (data.total) totalChapters = data.total;
    if (data.done != null) {
      doneChapters = data.done;
      const pct = Math.round((doneChapters / totalChapters) * 100);
      document.getElementById('progress-bar').style.width = pct + '%';
      addSimpleStep(`第 ${doneChapters}/${totalChapters} 章完成`, 'done');
    }
  });

  eventSource.addEventListener('step', (e) => {
    const data = JSON.parse(e.data);
    addSimpleStep(data.msg || data.message || '', data.status || 'active');
  });

  eventSource.addEventListener('done', (e) => {
    eventSource.close();
    eventSource = null;
    document.getElementById('progress-bar').style.width = '100%';
    addSimpleStep('写作完成 ✓', 'done');
    fetchResult(taskId);
  });

  eventSource.addEventListener('error', (e) => {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    // Try to fetch result in case it completed
    setTimeout(() => fetchResult(taskId), 1000);
  });
}

function appendLog(msg, level) {
  const logEl = document.getElementById('verbose-log');
  const line = document.createElement('div');
  const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  const color = level === 'error' ? 'text-red-400' : level === 'warn' ? 'text-yellow-400' : 'text-gray-300';
  line.className = color;
  line.textContent = `[${time}] ${msg}`;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
}

function addSimpleStep(text, status) {
  const container = document.getElementById('simple-steps');
  const step = document.createElement('div');
  step.className = 'flex items-center gap-2 text-sm';
  if (status === 'done') {
    step.innerHTML = `<span class="text-green-500">✓</span><span class="text-gray-700">${text}</span>`;
  } else if (status === 'error') {
    step.innerHTML = `<span class="text-red-500">✗</span><span class="text-red-600">${text}</span>`;
  } else {
    step.innerHTML = `<span class="text-blue-500 animate-pulse">●</span><span class="text-gray-700">${text}</span>`;
  }
  container.appendChild(step);
}

function setLogMode(mode) {
  logMode = mode;
  document.getElementById('log-simple').classList.toggle('hidden', mode !== 'simple');
  document.getElementById('log-verbose').classList.toggle('hidden', mode !== 'verbose');
  document.getElementById('btn-simple').className = mode === 'simple'
    ? 'px-3 py-1 text-xs rounded-md bg-white shadow-sm font-medium'
    : 'px-3 py-1 text-xs rounded-md text-gray-500 hover:text-gray-700';
  document.getElementById('btn-verbose').className = mode === 'verbose'
    ? 'px-3 py-1 text-xs rounded-md bg-white shadow-sm font-medium'
    : 'px-3 py-1 text-xs rounded-md text-gray-500 hover:text-gray-700';
}

// ─── Result ───
async function fetchResult(taskId) {
  try {
    const res = await fetch(`/api/write/${taskId}/result`);
    const data = await res.json();

    if (data.status === 'running') {
      setTimeout(() => fetchResult(taskId), 2000);
      return;
    }

    if (data.status === 'error') {
      showToast('写作失败: ' + (data.error || '未知错误'));
      resetWriteButton();
      return;
    }

    showResult(data);
  } catch (e) {
    showToast('获取结果失败: ' + e.message);
    resetWriteButton();
  }
}

function showResult(data) {
  document.getElementById('result-section').classList.remove('hidden');
  document.getElementById('article-editor').value = data.article || '';

  // Stats
  const article = data.article || '';
  const chars = article.length;
  const stats = data.stats || {};
  const duration = stats.duration_sec ? `${Math.floor(stats.duration_sec / 60)}m${stats.duration_sec % 60}s` : '';
  const chapters = stats.chapters || '?';
  document.getElementById('result-stats').textContent = `字数: ${chars} | 章节: ${chapters} | 耗时: ${duration}`;

  // Sources
  const sources = data.sources || [];
  const sourcesSection = document.getElementById('sources-section');
  const sourcesList = document.getElementById('sources-list');
  if (sources.length > 0) {
    sourcesSection.classList.remove('hidden');
    sourcesList.innerHTML = sources.map(s => `
      <a href="${s.url}" target="_blank" rel="noopener"
         class="flex items-center gap-2 text-sm text-blue-600 hover:text-blue-800 hover:underline py-1">
        <span class="text-gray-400 text-xs bg-gray-100 px-1.5 py-0.5 rounded">${s.platform || 'web'}</span>
        <span>${s.title || s.url}</span>
      </a>
    `).join('');
  } else {
    sourcesSection.classList.add('hidden');
  }

  // Auto scroll to result
  document.getElementById('result-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
  resetWriteButton();
  loadHistory();
}

// ─── Article Actions ───
async function saveEdit() {
  if (!currentTaskId) return;
  const article = document.getElementById('article-editor').value;
  try {
    const res = await fetch(`/api/write/${currentTaskId}/edit`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ article }),
    });
    const data = await res.json();
    showToast(data.ok ? '已保存' : data.error || '保存失败');
  } catch (e) {
    showToast('保存失败: ' + e.message);
  }
}

function copyArticle() {
  const text = document.getElementById('article-editor').value;
  navigator.clipboard.writeText(text).then(() => {
    showToast('已复制到剪贴板');
  }).catch(() => {
    // Fallback
    const textarea = document.createElement('textarea');
    textarea.value = text;
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand('copy');
    document.body.removeChild(textarea);
    showToast('已复制到剪贴板');
  });
}

function resetForm() {
  document.getElementById('result-section').classList.add('hidden');
  document.getElementById('progress-section').classList.add('hidden');
  currentTaskId = null;
  document.getElementById('inp-keywords').value = '';
  document.getElementById('inp-outline').value = '';
  document.getElementById('inp-ideas').value = '';
  document.querySelectorAll('.inp-link').forEach((el, i) => {
    if (i === 0) el.value = '';
    else el.parentElement.remove();
  });
  validateInputs();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ─── Email ───
function showEmailModal() {
  if (!currentTaskId) return;
  document.getElementById('email-modal').classList.remove('hidden');
  const title = document.getElementById('article-editor').value.split('\n')[0].replace(/^#+\s*/, '');
  const date = new Date().toISOString().slice(0, 10);
  document.getElementById('email-subject').value = `📰 [深度文章] ${date} | ${title || '未命名'}`;
  document.getElementById('email-recipient').focus();
}

function closeEmailModal() {
  document.getElementById('email-modal').classList.add('hidden');
}

async function doSendEmail() {
  if (!currentTaskId) return;
  const recipient = document.getElementById('email-recipient').value.trim();
  const subject = document.getElementById('email-subject').value.trim();
  if (!recipient) {
    showToast('请输入收件人');
    return;
  }
  try {
    const res = await fetch(`/api/write/${currentTaskId}/email`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ recipient, subject }),
    });
    const data = await res.json();
    closeEmailModal();
    showToast(data.ok ? data.msg : data.error || '发送失败');
  } catch (e) {
    showToast('发送失败: ' + e.message);
  }
}

// ─── History ───
async function loadHistory() {
  try {
    const res = await fetch('/api/history');
    const data = await res.json();
    const list = document.getElementById('history-list');
    const items = data.history || [];
    if (items.length === 0) {
      list.innerHTML = '<p class="text-sm text-gray-400">暂无记录</p>';
      return;
    }
    list.innerHTML = items.map(item => `
      <div class="flex items-center justify-between py-2 px-3 rounded-lg hover:bg-gray-50 cursor-pointer transition"
           onclick="loadHistoryItem('${item.id}')">
        <div class="flex items-center gap-3 min-w-0">
          <span class="text-gray-400 text-sm">📄</span>
          <span class="text-sm text-gray-800 truncate">${item.title || '未命名'}</span>
        </div>
        <div class="flex items-center gap-3 flex-shrink-0">
          <span class="text-xs text-gray-400">${item.chars || 0} 字</span>
          <span class="text-xs text-gray-400">${item.date || ''}</span>
        </div>
      </div>
    `).join('');
  } catch {}
}

async function loadHistoryItem(id) {
  try {
    const res = await fetch(`/api/history/${id}`);
    const data = await res.json();
    if (data.item) {
      currentTaskId = id;
      showResult({
        article: data.item.article || '',
        stats: { chars: data.item.chars, chapters: '?', duration_sec: 0 },
        sources: data.item.sources || [],
      });
      document.getElementById('result-section').scrollIntoView({ behavior: 'smooth' });
    }
  } catch (e) {
    showToast('加载失败: ' + e.message);
  }
}

// ─── Toast ───
function showToast(msg, duration = 3000) {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.classList.remove('hidden');
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => toast.classList.add('hidden'), duration);
}

// Close panels on outside click
document.addEventListener('click', (e) => {
  const panel = document.getElementById('model-panel');
  if (!panel.classList.contains('hidden') && !panel.contains(e.target) &&
      !e.target.closest('[onclick*="toggleModelPanel"]')) {
    panel.classList.add('hidden');
  }
});
