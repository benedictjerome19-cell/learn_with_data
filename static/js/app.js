/**
 * Consistency Pulse™ - Enterprise Frontend Controller
 * Handles:
 * - Persistent Dark/Light theme toggle
 * - Chart.js Rolling Moving Averages & Technology Breakdown
 * - GitHub 52-Week Heatmap Rendering & Floating Tooltips
 * - Multi-Language Filter Slicing (desktop + mobile)
 * - Drag-and-Drop Bulk File Imports (CSV, XLSX, JSON)
 * - Real-time Form Validation, Logging & Editing
 * - Ledger search, paging and deletion
 * - Background Excel Sync Status & Toast Stack
 * - Session timeout countdown, account settings and admin console
 *
 * All user-supplied text is escaped before it is inserted as HTML.
 */

const PAGE_SIZE = 100;

function readMeta(name, fallback = '') {
  const el = document.querySelector(`meta[name="${name}"]`);
  return el ? el.getAttribute('content') : fallback;
}

function safeStorageGet(key) {
  try { return localStorage.getItem(key); } catch (e) { return null; }
}

function safeStorageSet(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* storage unavailable */ }
}

// Global State
const State = {
  currentLanguage: 'all',
  search: '',
  logsOffset: 0,
  logsCache: new Map(),
  theme: safeStorageGet('pulse_theme') || 'dark',
  chartRolling: null,
  chartLanguages: null,
  csrfToken: readMeta('csrf-token'),
  sessionTimeoutMinutes: parseInt(readMeta('session-timeout-minutes', '30'), 10) || 30,
  sessionDeadline: 0,
  sessionTimerInterval: null,
  role: document.body ? document.body.dataset.role : 'learner'
};

// DOM Ready
document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  initSessionTimer();
  initFilters();
  initFormValidation();
  initDropzone();
  initManualLogForm();
  initLedger();
  initSyncButtons();
  initAccountModal();
  showFlashMessages();

  // Initial load
  refreshAll();
  if (State.role === 'admin') loadAdminOverview();
});

function refreshAll() {
  loadDashboardData(State.currentLanguage);
  loadRecentLogs();
  checkSyncStatus();
}

/* ==========================================================================
   0. HELPERS: ESCAPING, API WRAPPER, DATES
   ========================================================================== */
function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function localDateISO(d = new Date()) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

/**
 * fetch() wrapper: sends the CSRF token, resets the idle timer, redirects to
 * the login page on 401 and always resolves to parsed JSON or throws ApiError.
 */
async function api(url, options = {}) {
  const opts = { credentials: 'same-origin', ...options };
  opts.headers = { 'X-CSRFToken': State.csrfToken, 'Accept': 'application/json', ...(options.headers || {}) };
  if (opts.json !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.json);
    delete opts.json;
  }

  let res;
  try {
    res = await fetch(url, opts);
  } catch (err) {
    throw new ApiError('Network error. Check your connection and try again.', 0);
  }

  resetSessionTimer();

  if (res.status === 401) {
    showToast('Your session has expired. Redirecting to sign in…', 'warning', 3000);
    setTimeout(() => { window.location.href = '/login'; }, 1200);
    throw new ApiError('Session expired', 401);
  }

  let data = null;
  try {
    data = await res.json();
  } catch (e) {
    throw new ApiError(`Unexpected server response (HTTP ${res.status}).`, res.status);
  }
  if (!res.ok || data.success === false) {
    throw new ApiError(data.error || data.message || `Request failed (HTTP ${res.status}).`, res.status);
  }
  return data;
}

function setButtonBusy(btn, busy, busyHtml) {
  if (!btn) return;
  if (busy) {
    btn.dataset.originalHtml = btn.dataset.originalHtml || btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = busyHtml;
  } else {
    btn.disabled = false;
    if (btn.dataset.originalHtml) btn.innerHTML = btn.dataset.originalHtml;
    delete btn.dataset.originalHtml;
  }
}

/* ==========================================================================
   1. THEME MANAGEMENT (PERSISTENT DARK/LIGHT MODE)
   ========================================================================== */
function initTheme() {
  const html = document.documentElement;
  const toggleBtn = document.getElementById('theme-toggle-btn');
  const themeIcon = document.getElementById('theme-icon');

  const applyIcon = (isDark) => {
    if (themeIcon) themeIcon.className = isDark ? 'fa-solid fa-sun text-amber-400' : 'fa-solid fa-moon text-indigo-600';
  };

  html.classList.toggle('dark', State.theme === 'dark');
  applyIcon(State.theme === 'dark');

  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      const isDark = html.classList.toggle('dark');
      State.theme = isDark ? 'dark' : 'light';
      safeStorageSet('pulse_theme', State.theme);
      applyIcon(isDark);
      updateChartThemes();
      showToast(`Switched to ${State.theme} mode`, 'info', 2000);
    });
  }
}

/* ==========================================================================
   2. TOAST NOTIFICATION STACK
   ========================================================================== */
function showToast(message, type = 'info', duration = 3500) {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const iconMap = {
    success: 'fa-circle-check text-emerald-400',
    error: 'fa-circle-exclamation text-rose-400',
    warning: 'fa-triangle-exclamation text-amber-400',
    info: 'fa-circle-info text-indigo-400'
  };

  const toast = document.createElement('div');
  toast.className = `toast-item toast-${type}`;
  toast.setAttribute('role', type === 'error' ? 'alert' : 'status');

  const icon = document.createElement('i');
  icon.className = `fa-solid ${iconMap[type] || iconMap.info} text-lg mt-0.5`;

  const text = document.createElement('div');
  text.className = 'flex-1 text-sm font-medium pr-2';
  text.textContent = message;

  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'text-slate-400 hover:text-white transition';
  close.setAttribute('aria-label', 'Dismiss notification');
  close.innerHTML = '<i class="fa-solid fa-xmark text-xs"></i>';
  close.addEventListener('click', () => toast.remove());

  const progress = document.createElement('div');
  progress.className = 'toast-progress';
  progress.style.animationDuration = `${duration}ms`;

  toast.append(icon, text, close, progress);
  container.appendChild(toast);

  const timeoutId = setTimeout(() => {
    toast.style.animation = 'toast-out 0.25s forwards';
    setTimeout(() => toast.remove(), 250);
  }, duration);

  toast.addEventListener('mouseenter', () => {
    clearTimeout(timeoutId);
    progress.style.animationPlayState = 'paused';
  });
  toast.addEventListener('mouseleave', () => {
    setTimeout(() => toast.remove(), 1500);
  });
}

function showFlashMessages() {
  const el = document.getElementById('flash-data');
  if (!el) return;
  try {
    const messages = JSON.parse(el.textContent || '[]');
    messages.forEach(([category, msg]) => {
      const type = { error: 'error', success: 'success', warning: 'warning' }[category] || 'info';
      showToast(msg, type, 5000);
    });
  } catch (e) { /* ignore malformed flash payload */ }
}

/* ==========================================================================
   3. DATA FETCHING & KPI DASHBOARD
   ========================================================================== */
async function loadDashboardData(language = 'all') {
  try {
    const data = await api(`/api/stats?language=${encodeURIComponent(language)}`);
    renderKPIs(data.kpis);
    renderContributionMatrix(data.heatmap);
    renderRollingChart(data.charts);
    renderLanguageChart(data.languages.distribution);
    renderLanguagePills(data.languages.available, language);
    updateLanguageSuggestions(data.languages.available);
  } catch (err) {
    if (err.status === 401) return;
    console.error('Error loading dashboard data:', err);
    showToast(`Failed to refresh analytics: ${err.message}`, 'error');
  }
}

function renderKPIs(kpis) {
  if (!kpis) return;

  const setVal = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  };
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

  setVal('kpi-total-hours', `${kpis.total_hours} hrs`);
  setVal('kpi-total-concepts', kpis.total_concepts);
  setVal('kpi-weekly-ma', `${kpis.weekly_ma_hours} h/d`);
  setVal('kpi-monthly-ma', `${kpis.monthly_ma_hours} h/d`);
  setVal('kpi-current-streak', plural(kpis.current_streak, 'day'));
  setVal('kpi-longest-streak', plural(kpis.longest_streak, 'day'));
  setVal('kpi-active-days', kpis.active_days);
  setVal('kpi-consistency-rate', `${kpis.consistency_rate}%`);
  setVal('kpi-avg-mastery', `${kpis.avg_mastery} / 5`);

  const velocityEl = document.getElementById('kpi-velocity-badge');
  if (velocityEl) {
    const vel = Number(kpis.improvement_velocity) || 0;
    const isPositive = vel >= 0;
    velocityEl.className = `inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full ${
      isPositive ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'
    }`;
    velocityEl.title = '7-day moving average compared with the 30-day moving average';
    velocityEl.innerHTML = `<i class="fa-solid fa-arrow-trend-${isPositive ? 'up' : 'down'} text-[10px]"></i> ${isPositive ? '+' : ''}${escapeHtml(vel)}% vs 30-day`;
  }
}

/* ==========================================================================
   4. GITHUB-STYLE 52-WEEK CONTRIBUTION MATRIX
   ========================================================================== */
function renderContributionMatrix(heatmapData) {
  const matrixContainer = document.getElementById('heatmap-grid');
  const monthHeadersContainer = document.getElementById('heatmap-months');
  if (!matrixContainer || !heatmapData) return;

  matrixContainer.innerHTML = '';
  if (monthHeadersContainer) monthHeadersContainer.innerHTML = '';

  const { matrix = [], month_headers = [] } = heatmapData;

  if (monthHeadersContainer) {
    month_headers.forEach(m => {
      const span = document.createElement('span');
      span.className = 'text-xs font-medium text-slate-500 dark:text-slate-400';
      // 12px cell + 3px gap per week column
      span.style.left = `${m.week_col * 15}px`;
      span.style.position = 'absolute';
      span.textContent = m.name;
      monthHeadersContainer.appendChild(span);
    });
  }

  const fragment = document.createDocumentFragment();
  matrix.forEach(weekCols => {
    const colDiv = document.createElement('div');
    colDiv.className = 'heatmap-col';

    weekCols.forEach(cell => {
      const cellDiv = document.createElement('div');
      cellDiv.className = `heatmap-cell cell-lvl-${Number(cell.level) || 0}`;
      // Tooltip HTML is built server-side with user text already escaped
      cellDiv.dataset.tooltip = cell.tooltip;
      cellDiv.dataset.date = cell.date;
      cellDiv.setAttribute('aria-label', `${cell.date}: ${cell.hours} hours`);
      colDiv.appendChild(cellDiv);
    });

    fragment.appendChild(colDiv);
  });
  matrixContainer.appendChild(fragment);
}

const tooltipEl = document.getElementById('heatmap-tooltip');

document.addEventListener('mouseover', (e) => {
  const cell = e.target.closest && e.target.closest('.heatmap-cell');
  if (!cell || !tooltipEl) return;
  tooltipEl.innerHTML = cell.dataset.tooltip || '';
  tooltipEl.style.display = 'block';
  positionTooltip(e);
});
document.addEventListener('mousemove', (e) => {
  if (tooltipEl && tooltipEl.style.display === 'block' && e.target.closest && e.target.closest('.heatmap-cell')) positionTooltip(e);
});
document.addEventListener('mouseout', (e) => {
  if (tooltipEl && e.target.closest && e.target.closest('.heatmap-cell')) tooltipEl.style.display = 'none';
});

function positionTooltip(e) {
  if (!tooltipEl) return;
  const offset = 14;
  let x = e.clientX + offset;
  let y = e.clientY - offset - tooltipEl.offsetHeight;

  if (x + tooltipEl.offsetWidth > window.innerWidth) {
    x = e.clientX - tooltipEl.offsetWidth - offset;
  }
  if (y < 10) {
    y = e.clientY + offset;
  }

  tooltipEl.style.left = `${Math.max(4, x)}px`;
  tooltipEl.style.top = `${y}px`;
}

/* ==========================================================================
   5. ROLLING MOVING AVERAGES CHART (CHART.JS)
   ========================================================================= */
function chartColors() {
  const isDark = document.documentElement.classList.contains('dark');
  return {
    isDark,
    gridColor: isDark ? 'rgba(255, 255, 255, 0.06)' : 'rgba(0, 0, 0, 0.06)',
    textColor: isDark ? '#94a3b8' : '#64748b'
  };
}

function renderRollingChart(chartData) {
  const ctx = document.getElementById('rolling-chart');
  if (!ctx || !chartData || typeof Chart === 'undefined') return;

  const { isDark, gridColor, textColor } = chartColors();

  if (State.chartRolling) {
    State.chartRolling.destroy();
  }

  State.chartRolling = new Chart(ctx, {
    type: 'line',
    data: {
      labels: chartData.labels,
      datasets: [
        {
          label: '7-Day Moving Avg (Weekly)',
          data: chartData.ma_7,
          borderColor: '#6366f1',
          backgroundColor: 'rgba(99, 102, 241, 0.12)',
          borderWidth: 2.5,
          tension: 0.35,
          fill: true,
          pointRadius: 0,
          pointHoverRadius: 6,
          pointHoverBackgroundColor: '#6366f1',
          pointHoverBorderColor: '#ffffff',
          pointHoverBorderWidth: 2
        },
        {
          label: '30-Day Moving Avg (Monthly)',
          data: chartData.ma_30,
          borderColor: '#10b981',
          borderWidth: 2,
          borderDash: [5, 4],
          tension: 0.35,
          fill: false,
          pointRadius: 0,
          pointHoverRadius: 6,
          pointHoverBackgroundColor: '#10b981',
          pointHoverBorderColor: '#ffffff',
          pointHoverBorderWidth: 2
        },
        {
          label: 'Daily Hours',
          data: chartData.daily_hours,
          borderColor: 'rgba(148, 163, 184, 0.4)',
          backgroundColor: isDark ? 'rgba(148, 163, 184, 0.1)' : 'rgba(203, 213, 225, 0.25)',
          borderWidth: 1,
          pointRadius: 2,
          pointHoverRadius: 5,
          tension: 0.1,
          fill: false
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          display: true,
          position: 'top',
          labels: { color: textColor, boxWidth: 12, usePointStyle: true, font: { size: 11 } }
        },
        tooltip: {
          backgroundColor: 'rgba(15, 23, 42, 0.94)',
          titleColor: '#ffffff',
          bodyColor: '#cbd5e1',
          borderColor: 'rgba(255, 255, 255, 0.1)',
          borderWidth: 1,
          padding: 10,
          callbacks: {
            label: (c) => `${c.dataset.label}: ${c.raw} hrs`
          }
        }
      },
      scales: {
        x: {
          grid: { color: gridColor },
          ticks: { color: textColor, maxTicksLimit: 12, font: { size: 10 } }
        },
        y: {
          grid: { color: gridColor },
          ticks: { color: textColor, font: { size: 10 } },
          title: { display: true, text: 'Hours / Day', color: textColor, font: { size: 11 } },
          beginAtZero: true
        }
      }
    }
  });
}

/* ==========================================================================
   6. TECHNOLOGY BREAKDOWN CHART (DOUGHNUT)
   ========================================================================== */
function renderLanguageChart(distribution) {
  const ctx = document.getElementById('languages-chart');
  if (!ctx || !distribution || typeof Chart === 'undefined') return;

  const { isDark, textColor } = chartColors();
  const labels = distribution.map(d => d.language);
  const data = distribution.map(d => d.hours);
  const palette = [
    '#6366f1', '#06b6d4', '#10b981', '#f59e0b', '#ec4899',
    '#8b5cf6', '#14b8a6', '#f97316', '#3b82f6', '#84cc16'
  ];

  if (State.chartLanguages) {
    State.chartLanguages.destroy();
  }

  State.chartLanguages = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: labels,
      datasets: [{
        data: data,
        backgroundColor: labels.map((_, i) => palette[i % palette.length]),
        borderWidth: isDark ? 2 : 1,
        borderColor: isDark ? '#111827' : '#ffffff'
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: window.innerWidth < 640 ? 'bottom' : 'right',
          labels: { color: textColor, boxWidth: 10, usePointStyle: true, font: { size: 11 } }
        },
        tooltip: {
          backgroundColor: 'rgba(15, 23, 42, 0.94)',
          callbacks: {
            label: (c) => ` ${c.label}: ${c.raw} hrs (${distribution[c.dataIndex]?.percentage}%)`
          }
        }
      },
      cutout: '68%'
    }
  });
}

function updateChartThemes() {
  const { gridColor, textColor, isDark } = chartColors();

  if (State.chartRolling) {
    const opts = State.chartRolling.options;
    opts.scales.x.grid.color = gridColor;
    opts.scales.x.ticks.color = textColor;
    opts.scales.y.grid.color = gridColor;
    opts.scales.y.ticks.color = textColor;
    opts.scales.y.title.color = textColor;
    opts.plugins.legend.labels.color = textColor;
    State.chartRolling.update();
  }
  if (State.chartLanguages) {
    State.chartLanguages.options.plugins.legend.labels.color = textColor;
    State.chartLanguages.data.datasets[0].borderColor = isDark ? '#111827' : '#ffffff';
    State.chartLanguages.update();
  }
}

/* ==========================================================================
   7. MULTI-LANGUAGE CUSTOM FILTERING
   ========================================================================== */
function initFilters() {
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.filter-btn');
    if (!btn) return;

    const lang = btn.dataset.lang;
    if (lang.toLowerCase() === State.currentLanguage.toLowerCase()) return;
    State.currentLanguage = lang.toLowerCase() === 'all' ? 'all' : lang;

    document.querySelectorAll('.filter-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.lang.toLowerCase() === State.currentLanguage.toLowerCase());
    });

    loadDashboardData(State.currentLanguage);
    loadRecentLogs();
  });
}

function renderLanguagePills(availableLangs, selectedLang) {
  const containers = [
    document.getElementById('filter-pills-container'),
    document.getElementById('mobile-filter-container')
  ].filter(Boolean);
  if (!containers.length || !availableLangs) return;

  // Case-insensitive de-duplication; logged languages first after "All"
  const seen = new Set();
  const langs = [];
  ['All', ...availableLangs].forEach(lang => {
    const key = lang.toLowerCase();
    if (!seen.has(key)) { seen.add(key); langs.push(lang); }
  });
  if (selectedLang && !seen.has(selectedLang.toLowerCase())) langs.push(selectedLang);

  containers.forEach(container => {
    container.innerHTML = '';
    langs.forEach(lang => {
      const isAct = lang.toLowerCase() === (selectedLang || 'all').toLowerCase();
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `filter-btn shrink-0 whitespace-nowrap px-3 py-1.5 rounded-full text-xs font-semibold border transition-all border-slate-300 dark:border-slate-700 text-slate-700 dark:text-slate-300 hover:border-indigo-500${isAct ? ' active' : ''}`;
      btn.dataset.lang = lang;
      btn.textContent = lang;
      btn.setAttribute('aria-pressed', String(isAct));
      container.appendChild(btn);
    });
  });
}

function updateLanguageSuggestions(availableLangs) {
  const list = document.getElementById('language-options');
  if (!list || !availableLangs) return;
  const existing = new Set(Array.from(list.options).map(o => o.value.toLowerCase()));
  availableLangs.forEach(lang => {
    if (!existing.has(lang.toLowerCase())) {
      const opt = document.createElement('option');
      opt.value = lang;
      list.appendChild(opt);
      existing.add(lang.toLowerCase());
    }
  });
}

/* ==========================================================================
   8. DRAG-AND-DROP MULTI-FORMAT BULK IMPORT (CSV, XLSX, JSON)
   ========================================================================== */
function initDropzone() {
  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('file-input');
  if (!dropzone || !fileInput) return;

  dropzone.addEventListener('click', () => fileInput.click());
  dropzone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      fileInput.click();
    }
  });

  ['dragenter', 'dragover'].forEach(name => {
    dropzone.addEventListener(name, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.add('drag-over');
    });
  });

  ['dragleave', 'drop'].forEach(name => {
    dropzone.addEventListener(name, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.remove('drag-over');
    });
  });

  dropzone.addEventListener('drop', (e) => {
    const files = e.dataTransfer.files;
    if (files.length > 1) showToast('Only the first file will be imported.', 'warning');
    if (files.length > 0) handleFileUpload(files[0]);
  });

  fileInput.addEventListener('change', () => {
    if (fileInput.files.length > 0) handleFileUpload(fileInput.files[0]);
    // Reset so selecting the same file again still triggers a change event
    fileInput.value = '';
  });
}

let importInProgress = false;

async function handleFileUpload(file) {
  if (importInProgress) {
    showToast('An import is already running. Please wait.', 'warning');
    return;
  }
  const allowed = ['csv', 'xlsx', 'xls', 'json'];
  const ext = file.name.includes('.') ? file.name.split('.').pop().toLowerCase() : '';

  if (!allowed.includes(ext)) {
    showToast(`Invalid file type${ext ? ` .${ext}` : ''}. Please upload CSV, XLSX, XLS, or JSON.`, 'error');
    return;
  }
  if (file.size === 0) {
    showToast('The selected file is empty.', 'error');
    return;
  }

  importInProgress = true;
  showToast(`Uploading and validating ${file.name}…`, 'info', 2500);
  const formData = new FormData();
  formData.append('file', file);

  const dropzone = document.getElementById('dropzone');
  if (dropzone) {
    dropzone.style.opacity = '0.5';
    dropzone.setAttribute('aria-busy', 'true');
  }

  try {
    const data = await api('/api/import', { method: 'POST', body: formData });
    showToast(data.message, data.skipped ? 'warning' : 'success', 6000);
    if (data.skipped_details && data.skipped_details.length) {
      console.info('Skipped import rows:', data.skipped_details);
    }
    refreshAll();
  } catch (err) {
    if (err.status !== 401) showToast(err.message || 'Import failed.', 'error', 6000);
  } finally {
    importInProgress = false;
    if (dropzone) {
      dropzone.style.opacity = '1';
      dropzone.removeAttribute('aria-busy');
    }
  }
}

/* ==========================================================================
   9. MANUAL LOG FORM WITH REAL-TIME VALIDATION (CREATE + EDIT)
   ========================================================================== */
const validators = {
  'log-date': (v) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(v)) return 'Pick a valid date';
    if (v > localDateISO(new Date(Date.now() + 86400000))) return 'Date cannot be in the future';
    if (v < '2000-01-01') return 'Date cannot be before 2000';
    return '';
  },
  'log-language': (v) => (v.trim() ? '' : 'Language / stack is required'),
  'log-topic': (v) => (v.trim().length >= 3 ? '' : 'Topic must be at least 3 characters'),
  'log-hours': (v) => {
    const n = parseFloat(v);
    return !isNaN(n) && n >= 0.1 && n <= 24 ? '' : 'Hours must be between 0.1 and 24';
  },
  'log-concepts': (v) => {
    const n = Number(v);
    return Number.isInteger(n) && n >= 1 && n <= 500 ? '' : 'Concepts must be a whole number from 1 to 500';
  }
};

function showFieldState(input, errorMsg) {
  let errSpan = input.parentNode.querySelector('.input-error');
  input.classList.remove('border-rose-500', 'border-emerald-500');
  if (!errorMsg) {
    input.classList.add('border-emerald-500');
    input.removeAttribute('aria-invalid');
    if (errSpan) errSpan.remove();
    return true;
  }
  input.classList.add('border-rose-500');
  input.setAttribute('aria-invalid', 'true');
  if (!errSpan) {
    errSpan = document.createElement('span');
    errSpan.className = 'input-error text-xs text-rose-500 mt-1 block';
    input.parentNode.appendChild(errSpan);
  }
  errSpan.textContent = errorMsg;
  return false;
}

function validateField(id) {
  const input = document.getElementById(id);
  if (!input || !validators[id]) return true;
  return showFieldState(input, validators[id](input.value));
}

function initFormValidation() {
  Object.keys(validators).forEach(id => {
    const input = document.getElementById(id);
    if (input) input.addEventListener('input', () => validateField(id));
  });
}

function clearFormState(form) {
  form.querySelectorAll('input, select').forEach(i => {
    i.classList.remove('border-emerald-500', 'border-rose-500');
    i.removeAttribute('aria-invalid');
  });
  form.querySelectorAll('.input-error').forEach(e => e.remove());
}

function setEditMode(log) {
  const form = document.getElementById('manual-log-form');
  const banner = document.getElementById('edit-banner');
  const submitBtn = document.getElementById('log-submit-btn');
  const editId = document.getElementById('log-edit-id');
  if (!form) return;

  clearFormState(form);
  if (log) {
    editId.value = log.id;
    document.getElementById('log-date').value = log.date;
    document.getElementById('log-language').value = log.language;
    document.getElementById('log-topic').value = log.topic;
    document.getElementById('log-concepts').value = log.concepts_count;
    document.getElementById('log-hours').value = log.hours_spent;
    document.getElementById('log-mastery').value = String(log.mastery_score);
    document.getElementById('log-notes').value = log.notes || '';
    banner.classList.remove('hidden');
    banner.classList.add('flex');
    submitBtn.innerHTML = '<i class="fa-solid fa-floppy-disk"></i><span>Save Changes</span>';
    form.scrollIntoView({ behavior: 'smooth', block: 'center' });
    document.getElementById('log-topic').focus({ preventScroll: true });
  } else {
    editId.value = '';
    form.reset();
    document.getElementById('log-date').value = localDateISO();
    document.getElementById('log-language').value = 'Python';
    banner.classList.add('hidden');
    banner.classList.remove('flex');
    submitBtn.innerHTML = '<i class="fa-solid fa-plus"></i><span>Log Session & Trigger Sync</span>';
  }
}

function initManualLogForm() {
  const form = document.getElementById('manual-log-form');
  if (!form) return;

  const dateInput = document.getElementById('log-date');
  if (dateInput) {
    dateInput.value = localDateISO();
    dateInput.max = localDateISO(new Date(Date.now() + 86400000));
  }

  const cancelBtn = document.getElementById('log-cancel-edit');
  if (cancelBtn) cancelBtn.addEventListener('click', () => setEditMode(null));

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const allValid = Object.keys(validators).map(validateField).every(Boolean);
    if (!allValid) {
      showToast('Please fix the highlighted fields.', 'error');
      return;
    }

    const submitBtn = document.getElementById('log-submit-btn');
    const editId = document.getElementById('log-edit-id').value;
    const payload = {
      date: document.getElementById('log-date').value,
      language: document.getElementById('log-language').value.trim(),
      topic: document.getElementById('log-topic').value.trim(),
      concepts_count: Number(document.getElementById('log-concepts').value),
      hours_spent: Number(document.getElementById('log-hours').value),
      mastery_score: Number(document.getElementById('log-mastery').value),
      notes: document.getElementById('log-notes').value.trim()
    };

    setButtonBusy(submitBtn, true, '<i class="fa-solid fa-spinner fa-spin"></i><span>Saving…</span>');
    try {
      const data = editId
        ? await api(`/api/logs/${encodeURIComponent(editId)}`, { method: 'PUT', json: payload })
        : await api('/api/log', { method: 'POST', json: payload });
      setButtonBusy(submitBtn, false);
      showToast(data.message, 'success');
      setEditMode(null);
      refreshAll();
    } catch (err) {
      setButtonBusy(submitBtn, false);
      if (err.status !== 401) showToast(err.message || 'Failed to save log', 'error');
    }
  });
}

/* ==========================================================================
   10. RECENT LOGS TABLE: SEARCH, PAGING, EDIT & DELETE
   ========================================================================== */
function initLedger() {
  const search = document.getElementById('logs-search');
  if (search) {
    let timer = null;
    search.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        State.search = search.value.trim();
        loadRecentLogs();
      }, 300);
    });
  }

  const loadMore = document.getElementById('logs-load-more');
  if (loadMore) loadMore.addEventListener('click', () => loadRecentLogs({ append: true }));

  const tbody = document.getElementById('logs-table-body');
  if (tbody) {
    tbody.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-action]');
      if (!btn) return;
      const id = Number(btn.dataset.id);
      if (btn.dataset.action === 'delete') deleteLogEntry(id, btn);
      if (btn.dataset.action === 'edit') {
        const log = State.logsCache.get(id);
        if (log) setEditMode(log);
      }
    });
  }
}

function logRowHtml(log) {
  const mastery = Math.max(0, Math.min(5, Number(log.mastery_score) || 0));
  const stars = '★'.repeat(mastery) + '☆'.repeat(5 - mastery);
  const notes = log.notes ? `<div class="text-[11px] text-slate-400 mt-0.5 line-clamp-1">${escapeHtml(log.notes)}</div>` : '';
  return `
    <td class="py-3 px-4 font-mono text-xs text-slate-500 dark:text-slate-400 whitespace-nowrap">${escapeHtml(log.date)}</td>
    <td class="py-3 px-4">
      <span class="inline-block px-2.5 py-0.5 rounded-full text-xs font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 whitespace-nowrap">
        ${escapeHtml(log.language)}
      </span>
    </td>
    <td class="py-3 px-4 font-medium text-slate-800 dark:text-slate-200 min-w-[180px]">${escapeHtml(log.topic)}${notes}</td>
    <td class="py-3 px-4 text-center font-semibold text-emerald-500">${escapeHtml(log.concepts_count)}</td>
    <td class="py-3 px-4 text-right font-medium whitespace-nowrap">${escapeHtml(log.hours_spent)} hrs</td>
    <td class="py-3 px-4 text-center text-amber-400 font-mono text-xs whitespace-nowrap" title="Mastery: ${mastery}/5">${stars}</td>
    <td class="py-3 px-4 text-right whitespace-nowrap">
      <button type="button" data-action="edit" data-id="${Number(log.id)}" aria-label="Edit session" title="Edit" class="text-slate-400 hover:text-indigo-400 transition px-2 py-1">
        <i class="fa-solid fa-pen text-xs"></i>
      </button>
      <button type="button" data-action="delete" data-id="${Number(log.id)}" aria-label="Delete session" title="Delete" class="text-slate-400 hover:text-rose-500 transition px-2 py-1">
        <i class="fa-solid fa-trash-can text-xs"></i>
      </button>
    </td>
  `;
}

async function loadRecentLogs({ append = false } = {}) {
  const tbody = document.getElementById('logs-table-body');
  const loadMore = document.getElementById('logs-load-more');
  const countLabel = document.getElementById('logs-count-label');
  if (!tbody) return;

  const offset = append ? State.logsOffset : 0;
  const params = new URLSearchParams({
    language: State.currentLanguage,
    limit: PAGE_SIZE,
    offset,
    q: State.search
  });

  try {
    const data = await api(`/api/logs?${params.toString()}`);

    if (!append) {
      tbody.innerHTML = '';
      State.logsCache.clear();
    }
    State.logsOffset = offset + data.logs.length;

    if (countLabel) countLabel.textContent = `${data.total} session${data.total === 1 ? '' : 's'}`;

    if (!append && data.logs.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="7" class="text-center py-8 text-slate-400">
            ${State.search ? 'No sessions match your search.' : 'No learning sessions found for this filter. Log one above or import a file.'}
          </td>
        </tr>
      `;
    }

    const fragment = document.createDocumentFragment();
    data.logs.forEach(log => {
      State.logsCache.set(Number(log.id), log);
      const tr = document.createElement('tr');
      tr.className = 'border-b border-slate-200 dark:border-slate-800/80 hover:bg-slate-500/5 transition';
      tr.innerHTML = logRowHtml(log);
      fragment.appendChild(tr);
    });
    tbody.appendChild(fragment);

    if (loadMore) loadMore.classList.toggle('hidden', State.logsOffset >= data.total);
  } catch (err) {
    if (err.status === 401) return;
    console.error('Logs fetch error:', err);
    if (!append) {
      tbody.innerHTML = `<tr><td colspan="7" class="text-center py-8 text-rose-400">Could not load sessions: ${escapeHtml(err.message)}</td></tr>`;
    } else {
      showToast(`Could not load more sessions: ${err.message}`, 'error');
    }
  }
}

async function deleteLogEntry(logId, btn) {
  const log = State.logsCache.get(logId);
  const label = log ? `"${log.topic}" (${log.date})` : 'this learning session';
  if (!confirm(`Delete ${label}? This cannot be undone.`)) return;

  if (btn) btn.disabled = true;
  try {
    await api(`/api/logs/${encodeURIComponent(logId)}`, { method: 'DELETE' });
    showToast('Entry deleted successfully', 'info');
    if (String(document.getElementById('log-edit-id').value) === String(logId)) setEditMode(null);
    refreshAll();
  } catch (err) {
    if (btn) btn.disabled = false;
    if (err.status !== 401) showToast(err.message || 'Failed to delete entry', 'error');
  }
}

/* ==========================================================================
   11. REAL-TIME EXCEL BACKGROUND SYNC STATUS & TRIGGER
   ========================================================================== */
function initSyncButtons() {
  const syncBtn = document.getElementById('sync-now-btn');
  if (!syncBtn) return;
  syncBtn.addEventListener('click', async () => {
    setButtonBusy(syncBtn, true, '<i class="fa-solid fa-arrows-rotate fa-spin"></i><span>Syncing…</span>');
    try {
      const data = await api('/api/sync/now', { method: 'POST' });
      showToast(data.message, 'success', 4000);
      checkSyncStatus();
    } catch (err) {
      if (err.status !== 401) showToast(err.message || 'Sync failed', 'error', 6000);
    } finally {
      setButtonBusy(syncBtn, false);
    }
  });
}

async function checkSyncStatus() {
  const statusEl = document.getElementById('sync-status-badge');
  if (!statusEl) return;

  try {
    // Background syncs finish shortly after a save; give them a moment
    await new Promise(r => setTimeout(r, 800));
    const data = await api('/api/sync/status');
    if (data.last_sync) {
      statusEl.innerHTML = `
        <span class="inline-block w-2 h-2 rounded-full bg-emerald-400 sync-pulse mr-1.5"></span>
        Synced: <span class="text-slate-200 ml-1 font-mono">${escapeHtml(data.last_sync.created_at)}</span>
      `;
    } else {
      statusEl.innerHTML = `
        <span class="inline-block w-2 h-2 rounded-full bg-amber-400 mr-1.5"></span>
        Ready to sync
      `;
    }
  } catch (err) {
    console.warn('Sync status check warning:', err);
  }
}

/* ==========================================================================
   12. SESSION TIMEOUT & EXPIRY COUNTDOWN (IDLE TIMER)
   ========================================================================== */
function resetSessionTimer() {
  // The server refreshes the session cookie on every request, so the idle window restarts
  State.sessionDeadline = Date.now() + State.sessionTimeoutMinutes * 60 * 1000;
}

function initSessionTimer() {
  const timerBadge = document.getElementById('session-timer-badge');
  resetSessionTimer();

  const tick = async () => {
    const secondsRemaining = Math.max(0, Math.round((State.sessionDeadline - Date.now()) / 1000));
    const mins = Math.floor(secondsRemaining / 60);
    const secs = secondsRemaining % 60;

    if (timerBadge) {
      timerBadge.textContent = `${mins}:${secs < 10 ? '0' : ''}${secs}`;
      const warn = mins < 5;
      timerBadge.classList.toggle('text-amber-400', warn);
      timerBadge.classList.toggle('animate-pulse', warn);
    }

    if (secondsRemaining <= 0) {
      clearInterval(State.sessionTimerInterval);
      showToast('Your session has expired. Please sign in again.', 'warning', 6000);
      setTimeout(() => { window.location.href = '/login'; }, 2000);
    }
  };

  tick();
  State.sessionTimerInterval = setInterval(tick, 1000);
}

/* ==========================================================================
   13. ACCOUNT SETTINGS (CHANGE PASSWORD)
   ========================================================================== */
function initAccountModal() {
  const modal = document.getElementById('account-modal');
  const openBtn = document.getElementById('account-btn');
  const closeBtn = document.getElementById('account-close');
  const form = document.getElementById('password-form');
  if (!modal || !openBtn || !form) return;

  const open = () => {
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    document.getElementById('pw-current').focus();
  };
  const close = () => {
    modal.classList.add('hidden');
    modal.classList.remove('flex');
    form.reset();
    openBtn.focus();
  };

  openBtn.addEventListener('click', open);
  closeBtn.addEventListener('click', close);
  modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !modal.classList.contains('hidden')) close();
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const current = document.getElementById('pw-current').value;
    const next = document.getElementById('pw-new').value;
    const confirmPw = document.getElementById('pw-confirm').value;

    if (!current) return showToast('Enter your current password.', 'error');
    if (next.length < 8 || !/[A-Za-z]/.test(next) || !/\d/.test(next)) {
      return showToast('New password must be at least 8 characters with a letter and a number.', 'error');
    }
    if (next !== confirmPw) return showToast('New passwords do not match.', 'error');

    const btn = document.getElementById('pw-submit');
    setButtonBusy(btn, true, 'Updating…');
    try {
      const data = await api('/api/account/password', {
        method: 'POST',
        json: { current_password: current, new_password: next }
      });
      showToast(data.message, 'success');
      close();
    } catch (err) {
      if (err.status !== 401) showToast(err.message, 'error');
    } finally {
      setButtonBusy(btn, false);
    }
  });
}

/* ==========================================================================
   14. ADMIN CONSOLE
   ========================================================================== */
async function loadAdminOverview() {
  const usersBody = document.getElementById('admin-users-body');
  const auditBody = document.getElementById('admin-audit-body');
  if (!usersBody || !auditBody) return;

  try {
    const data = await api('/api/admin/overview');

    usersBody.innerHTML = data.users.map(u => `
      <tr class="border-b border-slate-200 dark:border-slate-800/60">
        <td class="py-2 px-3">
          <div class="font-semibold text-slate-800 dark:text-slate-200">${escapeHtml(u.username)}</div>
          <div class="text-[11px] text-slate-400">${escapeHtml(u.email)}</div>
        </td>
        <td class="py-2 px-3 text-right">${escapeHtml(u.sessions)}</td>
        <td class="py-2 px-3 text-right">${escapeHtml(u.total_hours)}</td>
        <td class="py-2 px-3 font-mono text-[11px] text-slate-400">${escapeHtml(u.last_active || '—')}</td>
        <td class="py-2 px-3">
          <select data-user-id="${Number(u.id)}" data-current="${escapeHtml(u.role)}" aria-label="Role for ${escapeHtml(u.username)}" class="admin-role-select text-xs px-2 py-1 rounded bg-slate-800/90 border border-slate-700 text-white">
            ${data.roles.map(r => `<option value="${escapeHtml(r)}"${r === u.role ? ' selected' : ''}>${escapeHtml(r)}</option>`).join('')}
          </select>
        </td>
      </tr>
    `).join('') || '<tr><td colspan="5" class="py-4 text-center text-slate-400">No users.</td></tr>';

    auditBody.innerHTML = data.audits.map(a => `
      <tr class="border-b border-slate-200 dark:border-slate-800/60">
        <td class="py-2 px-3 font-mono text-[11px] text-slate-400 whitespace-nowrap">${escapeHtml(a.created_at)}</td>
        <td class="py-2 px-3">${escapeHtml(a.username || '—')}</td>
        <td class="py-2 px-3 text-right">${escapeHtml(a.records_count)}</td>
        <td class="py-2 px-3" title="${escapeHtml(a.message || '')}">
          <span class="${a.status === 'success' ? 'text-emerald-400' : 'text-rose-400'} font-semibold">${escapeHtml(a.status)}</span>
        </td>
      </tr>
    `).join('') || '<tr><td colspan="4" class="py-4 text-center text-slate-400">No sync activity yet.</td></tr>';

    usersBody.querySelectorAll('.admin-role-select').forEach(sel => {
      sel.addEventListener('change', async () => {
        const role = sel.value;
        if (!confirm(`Change this user's role to "${role}"?`)) {
          sel.value = sel.dataset.current;
          return;
        }
        sel.disabled = true;
        try {
          const res = await api(`/api/admin/users/${sel.dataset.userId}/role`, { method: 'PUT', json: { role } });
          sel.dataset.current = role;
          showToast(res.message, 'success');
        } catch (err) {
          sel.value = sel.dataset.current;
          if (err.status !== 401) showToast(err.message, 'error');
        } finally {
          sel.disabled = false;
        }
      });
    });
  } catch (err) {
    if (err.status === 401) return;
    usersBody.innerHTML = `<tr><td colspan="5" class="py-4 text-center text-rose-400">${escapeHtml(err.message)}</td></tr>`;
    auditBody.innerHTML = '';
  }
}

// Register Progressive Web App Service Worker
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js')
      .catch(err => console.log('Pulse PWA ServiceWorker registration failed:', err));
  });
}
