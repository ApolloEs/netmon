'use strict';

// ── Theme ─────────────────────────────────────────────────────────────
// CSS handles layout colors via variables; everything drawn from JS
// (charts, heatmap/calendar palettes, canvas bands) reads this palette.
const THEMES = {
  dark: {
    chartText: '#9a90c0', grid: '#201943', gridMajor: '#3a3170',
    dl: '#12a0c9', dlFill: 'rgba(18,160,201,0.16)', ul: '#8c7ee8', median: '#c08a1e',
    p95Fill: 'rgba(192,138,30,0.16)',
    outageBand: 'rgba(239,83,104,0.16)', degradedBand: 'rgba(207,148,32,0.14)',
    noData: 'rgba(154,144,192,0.07)', noDataText: 'rgba(154,144,192,0.4)',
    tipBg: '#1a1332', tipBorder: '#372c63', tipText: '#ece8fa',
    target: '#ef5368', local: '#d158c9',
    events: { postponed: '#c08a1e', skipped: '#6f6a8a', forced: '#8c7ee8', error: '#ef5368' },
    loss: { nodata: '#1a1430', clean: '#11291d', trace: '#2b2408', mid: '#382312', bad: '#3c150e', severe: '#45090c' },
    cal: ['#1f7a4c', '#5d7d2e', '#8a6d1f', '#96521f', '#9d2f31'], calEmpty: '#1a1430',
  },
  light: {
    chartText: '#655e82', grid: '#efeaf9', gridMajor: '#d8cff0',
    dl: '#0f93b3', dlFill: 'rgba(15,147,179,0.08)', ul: '#7048e8', median: '#b87f00',
    p95Fill: 'rgba(184,127,0,0.15)',
    outageBand: 'rgba(208,59,59,0.10)', degradedBand: 'rgba(160,110,0,0.12)',
    noData: 'rgba(101,94,130,0.08)', noDataText: 'rgba(101,94,130,0.55)',
    tipBg: '#ffffff', tipBorder: '#e3ddf5', tipText: '#201a35',
    target: '#d03b3b', local: '#b32bb0',
    events: { postponed: '#b87f00', skipped: '#8b85a3', forced: '#7048e8', error: '#d03b3b' },
    loss: { nodata: '#efecf7', clean: '#dcf2e7', trace: '#fdf3cf', mid: '#fbdcb7', bad: '#f8bfad', severe: '#f29d9d' },
    cal: ['#16a05c', '#8fbf4d', '#d9a72c', '#e1782e', '#d03b3b'], calEmpty: '#e9e5f2',
  },
};

function theme() {
  return THEMES[document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'];
}

function applyChartDefaults() {
  const T = theme();
  Chart.defaults.color = T.chartText;
  Chart.defaults.borderColor = T.grid;
  Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
  // Tooltips match the card/tooltip chrome instead of Chart.js's black.
  const tip = Chart.defaults.plugins.tooltip;
  tip.backgroundColor = T.tipBg;
  tip.borderColor = T.tipBorder;
  tip.borderWidth = 1;
  tip.titleColor = T.tipText;
  tip.bodyColor = T.tipText;
  tip.padding = 10;
  tip.cornerRadius = 8;
  tip.boxPadding = 4;
  // Compact legend swatches that mirror each dataset's own style.
  const leg = Chart.defaults.plugins.legend.labels;
  leg.usePointStyle = true;
  leg.boxWidth = 9;
  leg.boxHeight = 9;
  leg.padding = 14;
}
applyChartDefaults();

function initTheme() {
  const btn = document.getElementById('theme-toggle');
  const setIcon = () => {
    // Show the theme you'd switch TO.
    btn.textContent = document.documentElement.dataset.theme === 'light' ? '🌙' : '☀';
  };
  setIcon();
  btn.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('netmon.theme', next);
    setIcon();
    applyChartDefaults();
    // Rebuild everything drawn with JS-side colors.
    loadSpeedHistory();
    loadLatency();
    loadOutages();
    loadDaily();
    loadHeatmap();
  });
}

let speedChart = null;
let speedChartHovered = false;

// Connection-outage intervals ({start, end} in epoch ms) drawn as red bands.
let outageBands = [];

// Periods with no speed-test attempts (app off) drawn as a neutral wash.
let noDataGaps = [];
let attemptTimes = [];              // epoch ms of every test attempt
let gapThresholdMs = 2 * 3600e3;    // max(1h, 2 × interval); set on load

function computeNoDataGaps() {
  const now = Date.now();
  const windowStart = now - 30 * 864e5;
  const times = [...attemptTimes].sort((a, b) => a - b);
  const gaps = [];
  let prev = windowStart;
  for (const t of times) {
    if (t - prev > gapThresholdMs) gaps.push({ start: prev, end: t });
    prev = Math.max(prev, t);
  }
  if (now - prev > gapThresholdMs) gaps.push({ start: prev, end: now });
  noDataGaps = gaps;
}

// Client display preference: show/hide failed-test (error) markers.
const PREF_ERROR_DOTS = 'netmon.showErrorDots';
function showErrorDots() {
  return localStorage.getItem(PREF_ERROR_DOTS) !== 'false';
}

// Washes out periods where no speed tests were attempted (LineProof not
// running), so empty plot area isn't mistaken for a healthy quiet line.
const noDataPlugin = {
  id: 'noData',
  beforeDatasetsDraw(chart) {
    if (!noDataGaps.length) return;
    const x = chart.scales.x;
    const area = chart.chartArea;
    const ctx = chart.ctx;
    ctx.save();
    for (const g of noDataGaps) {
      let x0 = x.getPixelForValue(g.start);
      let x1 = x.getPixelForValue(g.end);
      if (x1 < area.left || x0 > area.right) continue;
      x0 = Math.max(x0, area.left);
      x1 = Math.min(x1, area.right);
      ctx.fillStyle = theme().noData;
      ctx.fillRect(x0, area.top, x1 - x0, area.height);
      if (x1 - x0 > 90) {
        ctx.fillStyle = theme().noDataText;
        ctx.font = 'bold 20px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('no data', (x0 + x1) / 2, area.top + area.height / 2);
      }
    }
    ctx.restore();
  },
};

// Draws translucent red bands behind the datasets for each connection
// outage, so the plot area itself "turns red" while the line was down.
const outageBandsPlugin = {
  id: 'outageBands',
  beforeDatasetsDraw(chart) {
    if (!outageBands.length) return;
    const x = chart.scales.x;
    const area = chart.chartArea;
    const ctx = chart.ctx;
    ctx.save();
    for (const b of outageBands) {
      let x0 = x.getPixelForValue(b.start);
      let x1 = x.getPixelForValue(b.end);
      // Keep short outages visible at month scale (min 2px wide).
      if (x1 - x0 < 2) { const mid = (x0 + x1) / 2; x0 = mid - 1; x1 = mid + 1; }
      if (x1 < area.left || x0 > area.right) continue;
      x0 = Math.max(x0, area.left);
      x1 = Math.min(x1, area.right);
      ctx.fillStyle = b.kind === 'deg' ? theme().degradedBand : theme().outageBand;
      ctx.fillRect(x0, area.top, x1 - x0, area.height);
    }
    ctx.restore();
  },
};

// ── Bootstrap ────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await Promise.all([
    loadStatus(),
    loadSpeedHistory(),
    loadAdherence(),
    loadOutages(),
    loadHeatmap(),
    loadLatency(),
    loadDaily(),
  ]);
  connectSSE();
  initSettings();
  initRunTest();
  initChartRanges();
  initTheme();
  initEnroll();
});


// ── Phone enrollment (QR) — button exists only on the PC/localhost view ──
function initEnroll() {
  const openBtn = document.getElementById('enroll-open');
  if (!openBtn) return;
  const overlay = document.getElementById('enroll-overlay');
  openBtn.addEventListener('click', openEnroll);
  document.getElementById('enroll-close').addEventListener('click', () => {
    overlay.classList.add('hidden');
  });
  overlay.addEventListener('click', e => {
    if (e.target === overlay) overlay.classList.add('hidden');
  });
}

async function openEnroll() {
  const overlay = document.getElementById('enroll-overlay');
  const qrEl = document.getElementById('enroll-qr');
  const urlEl = document.getElementById('enroll-url');
  overlay.classList.remove('hidden');
  qrEl.innerHTML = '<span class="dim">Generating…</span>';
  urlEl.textContent = '';

  let data = null;
  try {
    const resp = await fetch('/api/enroll-token', { method: 'POST' });
    data = await resp.json();
  } catch (e) { /* handled below */ }

  if (!data || !data.ok) {
    qrEl.innerHTML = '<span class="dim">Could not generate the code'
      + (data && data.error ? ': ' + data.error : '.') + '</span>';
    return;
  }
  const qr = qrcode(0, 'M');
  qr.addData(data.url);
  qr.make();
  qrEl.innerHTML = qr.createSvgTag({ cellSize: 5, margin: 2, scalable: true });
  urlEl.textContent = data.url;
}


// ── Status strip ─────────────────────────────────────────────────────
async function loadStatus() {
  const data = await fetchJson('/api/status');
  if (data) applyStatus(data);
}

// ── Title & favicon reflect live status ──────────────────────────────
const STATUS_COLORS_UI = {
  online: '#2fb36c', degraded: '#cf9420', offline: '#ef5368', unknown: '#8a84a0',
};

function statusFavicon(color) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">` +
              `<circle cx="8" cy="8" r="7" fill="${color}"/></svg>`;
  return 'data:image/svg+xml,' + encodeURIComponent(svg);
}

function updateTitleAndFavicon(status, lastSpeed) {
  const parts = ['LineProof'];
  if (status === 'offline') parts.push('OFFLINE');
  else if (status === 'degraded') parts.push('degraded');
  else if (lastSpeed && lastSpeed.download_mbps != null) {
    parts.push(`${round1(lastSpeed.download_mbps)} Mbps`);
  }
  document.title = parts.join(' · ');

  let link = document.querySelector('link[rel="icon"]');
  if (!link) {
    link = document.createElement('link');
    link.rel = 'icon';
    document.head.appendChild(link);
  }
  link.href = statusFavicon(STATUS_COLORS_UI[status] ?? STATUS_COLORS_UI.unknown);
}


function applyStatus(data) {
  const dot   = document.getElementById('status-dot');
  const label = document.getElementById('status-label');
  const STATUS_TEXT = { online: 'Online', offline: 'Offline', degraded: 'Degraded', unknown: 'Unknown' };

  dot.className = `dot dot--${data.status}`;
  label.textContent = STATUS_TEXT[data.status] ?? data.status;
  updateTitleAndFavicon(data.status, data.last_speed);

  if (data.last_speed) {
    const s = data.last_speed;
    setText('stat-dl',      `${fmtMbps(s.download_mbps)}`);
    setText('stat-ul',      `${fmtMbps(s.upload_mbps)}`);
    setText('stat-pct',     s.pct_of_target != null ? `${s.pct_of_target}%` : '—');
    setText('last-updated', `Speed test: ${fmtRelative(s.timestamp)}`);
  }

  // Latency: average of successful pings
  const successPings = (data.targets || []).filter(t => t.success && t.latency_ms != null);
  if (successPings.length) {
    const avg = successPings.reduce((s, t) => s + t.latency_ms, 0) / successPings.length;
    setText('stat-latency', `${avg.toFixed(1)} ms`);
  } else {
    setText('stat-latency', '—');
  }

  // Keep the Run-test button in sync (covers page load mid-test and
  // missed speed_test_done events).
  const runBtn = document.getElementById('run-test');
  if (typeof data.test_running === 'boolean' && data.test_running !== runBtn.disabled) {
    setRunTestState(data.test_running);
  }

  // Open outage banner
  const banner = document.getElementById('outage-banner');
  if (data.open_outage) {
    document.getElementById('outage-trigger').textContent = data.open_outage.trigger;
    document.getElementById('outage-since').textContent   = fmtRelative(data.open_outage.started_at);
    banner.classList.remove('hidden');
  } else {
    banner.classList.add('hidden');
  }
}


// ── Speed chart ───────────────────────────────────────────────────────
async function loadSpeedHistory() {
  const data = await fetchJson('/api/speed-history');
  if (!data) return;

  // Every test attempt marks the app as alive at that moment: completed
  // tests (data) plus postponed/skipped/forced/error events.
  attemptTimes = [
    ...data.data.map(r => new Date(r.timestamp).getTime()),
    ...data.events.map(e => new Date(e.timestamp).getTime()),
  ];
  gapThresholdMs = Math.max(3600e3, 2 * (data.interval_hours || 3) * 3600e3);
  computeNoDataGaps();

  buildSpeedChart(data.data, data.events, data.target_mbps);
}

// Insert a null point wherever a no-data gap separates two measurements,
// so the line breaks instead of bridging periods the app wasn't running.
function insertGapBreaks(points) {
  if (points.length < 2) return points;
  const out = [points[0]];
  for (let i = 1; i < points.length; i++) {
    const prevMs = new Date(points[i - 1].x).getTime();
    const curMs = new Date(points[i].x).getTime();
    if (noDataGaps.some(g => g.start >= prevMs && g.end <= curMs)) {
      out.push({ x: (prevMs + curMs) / 2, y: null });
    }
    out.push(points[i]);
  }
  return out;
}

// Rolling median of download over a trailing 7-day window — smooths the
// scatter into the underlying trend.
function rollingMedian(rows, windowMs = 7 * 864e5) {
  if (rows.length < 5) return [];
  const ts = rows.map(r => new Date(r.timestamp).getTime());
  const out = [];
  let lo = 0;
  for (let i = 0; i < rows.length; i++) {
    while (ts[lo] < ts[i] - windowMs) lo++;
    const win = rows.slice(lo, i + 1)
      .map(r => r.download_mbps).filter(v => v != null)
      .sort((a, b) => a - b);
    if (!win.length) continue;
    const mid = Math.floor(win.length / 2);
    const med = win.length % 2 ? win[mid] : (win[mid - 1] + win[mid]) / 2;
    out.push({ x: rows[i].timestamp, y: round1(med) });
  }
  return out;
}

function buildSpeedChart(rows, eventRows, targetMbps) {
  const dlData = insertGapBreaks(rows.map(r => ({ x: r.timestamp, y: round1(r.download_mbps) })));
  const ulData = insertGapBreaks(rows.map(r => ({ x: r.timestamp, y: round1(r.upload_mbps) })));
  const medianData = insertGapBreaks(rollingMedian(rows));
  // Local host usage at each test — mostly near zero (tests run when idle),
  // which is exactly the point: low speeds don't line up with local load.
  const localData = insertGapBreaks(rows.map(r => ({
    x: r.timestamp, y: r.local_down_mbps != null ? round1(r.local_down_mbps) : null,
  })));

  // Annotation scatter datasets (postponed / skipped / forced / error)
  const STATUS_COLORS = theme().events;
  const annotationDatasets = Object.entries(STATUS_COLORS).map(([status, color]) => ({
    label: status.charAt(0).toUpperCase() + status.slice(1),
    type: 'scatter',
    data: eventRows
      .filter(e => e.status === status)
      .map(e => ({ x: e.timestamp, y: 0 })),
    backgroundColor: color,
    pointRadius: 5,
    pointHoverRadius: 7,
    showLine: false,
    hidden: status === 'error' && !showErrorDots(),
  }));

  const canvas = document.getElementById('speedChart');
  if (!canvas.dataset.hoverBound) {
    // pointerenter/leave cover both mouse hover and touch contact.
    canvas.addEventListener('pointerenter', () => {
      speedChartHovered = true;
      if (speedChart) speedChart.update('none');
    });
    canvas.addEventListener('pointerleave', () => {
      speedChartHovered = false;
      if (speedChart) speedChart.update('none');
    });
    canvas.dataset.hoverBound = '1';
  }

  const T = theme();
  if (speedChart) speedChart.destroy();
  const ctx = canvas.getContext('2d');
  speedChart = new Chart(ctx, {
    type: 'line',
    plugins: [noDataPlugin, outageBandsPlugin],  // order: outage red on top
    data: {
      datasets: [
        {
          label: 'Download',
          data: dlData,
          borderColor: T.dl,
          backgroundColor: T.dlFill,
          borderWidth: 2,
          // All points materialize while the cursor is over the chart
          // (or a finger is on it) — clean lines otherwise.
          pointRadius: () => speedChartHovered ? 3 : 0,
          pointHoverRadius: 5,
          pointHitRadius: 12,
          tension: 0.3,
          fill: true,
        },
        {
          label: 'Upload',
          data: ulData,
          borderColor: T.ul,
          backgroundColor: 'transparent',
          borderWidth: 2,
          pointRadius: () => speedChartHovered ? 2 : 0,
          pointHoverRadius: 4,
          pointHitRadius: 12,
          tension: 0.3,
        },
        {
          label: `Target (${targetMbps} Mbps)`,
          data: dlData.length
            ? [{ x: dlData[0].x, y: targetMbps }, { x: dlData[dlData.length - 1].x, y: targetMbps }]
            : [],
          borderColor: T.target,
          borderWidth: 1.5,
          borderDash: [6, 4],
          pointRadius: 0,
          fill: false,
        },
        {
          label: '7d median',
          data: medianData,
          borderColor: T.median,
          borderWidth: 2,
          borderDash: [8, 4],
          pointRadius: 0,
          tension: 0.2,
        },
        {
          label: 'Local usage',
          data: localData,
          borderColor: T.local,
          backgroundColor: 'transparent',
          borderWidth: 1.5,
          borderDash: [2, 3],
          pointRadius: () => speedChartHovered ? 2 : 0,
          pointHoverRadius: 4,
          pointHitRadius: 8,
          tension: 0.3,
          spanGaps: false,
        },
        ...annotationDatasets,
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: {
          type: 'time',
          time: {
            tooltipFormat: 'MMM d, HH:mm',
            displayFormats: { day: 'MMM d' },
          },
          ticks: {
            major: { enabled: true },  // day boundaries become "MMM d"
            font: c => c.tick && c.tick.major
              ? { weight: 'bold' }
              : {},
          },
          grid: {
            // Brighter gridline at midnights so days read at a glance.
            color: c => c.tick && c.tick.major ? theme().gridMajor : theme().grid,
          },
        },
        y: {
          title: { display: true, text: 'Mbps' },
          min: 0,
          ticks: { maxTicksLimit: 6 },
          grid: { color: c => theme().grid },
        },
      },
      plugins: {
        legend: { position: 'top' },
        tooltip: {
          callbacks: {
            label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y} Mbps`,
          },
        },
      },
    },
  });
}

function appendSpeedPoint(data) {
  if (!speedChart) return;
  attemptTimes.push(new Date(data.timestamp).getTime());
  computeNoDataGaps();
  const ts = data.timestamp;
  speedChart.data.datasets[0].data.push({ x: ts, y: round1(data.download_mbps) });
  speedChart.data.datasets[1].data.push({ x: ts, y: round1(data.upload_mbps) });
  // Extend target line
  speedChart.data.datasets[2].data[1] = { x: ts, y: TARGET_MBPS };
  // Local usage trace (datasets[4]; annotations follow it)
  speedChart.data.datasets[4].data.push({
    x: ts, y: data.local_down_mbps != null ? round1(data.local_down_mbps) : null,
  });
  speedChart.update('active');
}


// ── Adherence ─────────────────────────────────────────────────────────
async function loadAdherence() {
  const data = await fetchJson('/api/adherence');
  if (!data) return;
  applyAdherence(data);
}

function applyAdherence(data) {
  for (const window of ['7d', '30d']) {
    const d = data[window];
    if (!d || d.total == null || d.total === 0) {
      setText(`adh-${window}-pct`,    '—');
      setText(`adh-${window}-detail`, 'no data');
      continue;
    }
    const pct = d.adherence_pct != null ? `${d.adherence_pct}%` : '—';
    setText(`adh-${window}-pct`,    pct);
    setText(`adh-${window}-detail`, `${d.good} / ${d.total} tests`);
  }

  const d30 = data['30d'];
  if (d30 && d30.total > 0) {
    document.getElementById('adh-stats').textContent =
      `Avg: ${d30.avg_download} Mbps  ·  Min: ${d30.min_download} Mbps  ·  Target: ${d30.target_mbps} Mbps`;
  }
}


// ── Outage list ───────────────────────────────────────────────────────
async function loadOutages() {
  const [rows, degraded] = await Promise.all([
    fetchJson('/api/outages'),
    fetchJson('/api/degraded'),
  ]);
  if (!rows) return;
  const deg = degraded || [];
  renderOutages(rows, deg);
  renderGantt(rows, deg);
  updateOutageBands(rows, deg);
}

function updateOutageBands(rows, degraded) {
  const toBand = (r, kind) => ({
    start: new Date(r.started_at).getTime(),
    end: new Date(r.ended_at).getTime(),  // NOW-coalesced while open
    kind,
  });
  // Amber (degraded) first, red (outage) second: draw order makes red
  // win where they overlap. Colors resolve per-draw so theme switches
  // apply without recomputing bands.
  outageBands = [
    ...(degraded || []).map(r => toBand(r, 'deg')),
    ...rows.filter(r => r.type === 'connection').map(r => toBand(r, 'out')),
  ];
  if (speedChart) speedChart.update('none');
}


// ── Chart time range ──────────────────────────────────────────────────
function initChartRanges() {
  document.querySelectorAll('.range-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.range-btn')
        .forEach(b => b.classList.toggle('active', b === btn));
      applyChartRange(btn.dataset.range);
    });
  });
}

function applyChartRange(range) {
  const spans = { day: 864e5, week: 7 * 864e5, month: 30 * 864e5 };
  if (speedChart) {
    if (spans[range]) {
      // Fixed windows so Day ⊂ Week ⊂ Month always holds; empty space on
      // the left simply means no data recorded that far back yet.
      speedChart.options.scales.x.min = Date.now() - spans[range];
      speedChart.options.scales.x.max = Date.now();
    } else {
      // All: auto-fit to whatever data is loaded (page-load default).
      speedChart.options.scales.x.min = undefined;
      speedChart.options.scales.x.max = undefined;
    }
    speedChart.update();
  }
  if (latencyChart) {
    // Latency data only spans the 7-day ping retention — clamp there.
    const span = Math.min(spans[range] ?? 7 * 864e5, 7 * 864e5);
    if (spans[range]) {
      latencyChart.options.scales.x.min = Date.now() - span;
      latencyChart.options.scales.x.max = Date.now();
    } else {
      latencyChart.options.scales.x.min = undefined;
      latencyChart.options.scales.x.max = undefined;
    }
    latencyChart.update();
  }
}

function renderOutages(rows, degraded) {
  const el = document.getElementById('outage-list');
  const merged = [...rows, ...(degraded || [])]
    .sort((a, b) => new Date(b.started_at) - new Date(a.started_at));
  if (!merged.length) {
    el.innerHTML = '<span class="dim">No outages or degraded periods in the last 30 days.</span>';
    return;
  }
  el.innerHTML = merged.map(r => {
    const dur   = r.duration_seconds != null ? fmtDuration(r.duration_seconds) : '—';
    const start = fmtDatetime(r.started_at);
    const badge = r.is_open ? '<span class="outage-open-badge">OPEN</span>' : '';
    let cls = '', title = '', sub = '';
    if (r.type === 'degraded') {
      cls = 'outage-item--degraded';
      title = 'Degraded';
      sub = `avg ${r.avg_loss_pct}% · peak ${r.peak_loss_pct}% packet loss`;
    } else if (r.type === 'host') {
      cls = 'outage-item--host';
      title = r.triggers[0];
      sub = 'host check — site/DNS unreachable';
    } else {
      title = 'Connection';
      sub = r.triggers.join(' · ');
    }
    return `
      <div class="outage-item ${r.is_open ? 'open' : ''} ${cls}">
        <div>
          <div><strong>${title}</strong> — ${start} ${badge}</div>
          <div class="outage-trigger">${sub}</div>
        </div>
        <div class="outage-duration">${dur}</div>
      </div>`;
  }).join('');
}


// ── Latency chart ─────────────────────────────────────────────────────
let latencyChart = null;

async function loadLatency() {
  const rows = await fetchJson('/api/latency-history');
  const empty = document.getElementById('latency-empty');
  if (!rows || !rows.length) {
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');
  buildLatencyChart(rows);
}

// Centered 3-point moving average that respects null gap-breaks.
function smooth3(points) {
  return points.map((p, i) => {
    if (p.y == null) return p;
    const prev = points[i - 1], next = points[i + 1];
    const vals = [prev && prev.y, p.y, next && next.y].filter(v => v != null);
    return { x: p.x, y: Math.round(vals.reduce((s, v) => s + v, 0) / vals.length * 10) / 10 };
  });
}

function buildLatencyChart(rows) {
  // Break the line where buckets are missing (monitor was off).
  const avg = [], p95 = [];
  for (let i = 0; i < rows.length; i++) {
    if (i && new Date(rows[i].t) - new Date(rows[i - 1].t) > 15 * 60e3) {
      const mid = (new Date(rows[i].t).getTime() + new Date(rows[i - 1].t).getTime()) / 2;
      avg.push({ x: mid, y: null });
      p95.push({ x: mid, y: null });
    }
    avg.push({ x: rows[i].t, y: rows[i].avg_ms });
    p95.push({ x: rows[i].t, y: rows[i].p95_ms });
  }

  // Cap the axis at the 99th percentile of p95 so a handful of mega-spikes
  // don't squash the everyday band; clipped spikes still tooltip truthfully.
  const p95vals = rows.map(r => r.p95_ms).sort((a, b) => a - b);
  const p99 = p95vals[Math.floor(p95vals.length * 0.99)] ?? 50;
  const yMax = Math.max(50, Math.ceil(p99 / 25) * 25);

  const T = theme();
  if (latencyChart) latencyChart.destroy();
  latencyChart = new Chart(document.getElementById('latencyChart').getContext('2d'), {
    type: 'line',
    plugins: [outageBandsPlugin],  // outage/degraded context behind latency
    data: {
      datasets: [
        {
          label: 'Average',
          data: smooth3(avg),
          borderColor: T.dl,
          borderWidth: 2,
          pointRadius: 0,
        },
        {
          // Envelope up to p95 — no line of its own, just a soft band.
          label: 'p95',
          data: p95,
          borderWidth: 0,
          backgroundColor: T.p95Fill,
          pointRadius: 0,
          fill: '-1',
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: {
          type: 'time',
          time: { tooltipFormat: 'MMM d, HH:mm', displayFormats: { day: 'MMM d' } },
          ticks: {
            major: { enabled: true },
            font: c => c.tick && c.tick.major ? { weight: 'bold' } : {},
          },
          grid: { color: c => c.tick && c.tick.major ? theme().gridMajor : theme().grid },
        },
        y: {
          min: 0,
          max: yMax,
          title: { display: true, text: 'ms' },
          ticks: { maxTicksLimit: 6 },
          grid: { color: c => theme().grid },
        },
      },
      plugins: {
        legend: { position: 'top' },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: ${c.parsed.y} ms` } },
      },
    },
  });
}


// ── Custom tooltip (theme-matching replacement for title="") ─────────
let tooltipEl = null;

function ensureTooltip() {
  if (!tooltipEl) {
    tooltipEl = document.createElement('div');
    tooltipEl.className = 'nm-tooltip hidden';
    document.body.appendChild(tooltipEl);
  }
  return tooltipEl;
}

function moveTooltip(e) {
  const tip = ensureTooltip();
  const pad = 12;
  const rect = tip.getBoundingClientRect();
  let x = e.clientX + pad;
  let y = e.clientY + pad;
  if (x + rect.width > window.innerWidth - 8) x = e.clientX - rect.width - pad;
  if (y + rect.height > window.innerHeight - 8) y = e.clientY - rect.height - pad;
  tip.style.left = `${Math.max(4, x)}px`;
  tip.style.top = `${Math.max(4, y)}px`;
}

// Delegated hover tooltip: elements matching `selector` inside `container`
// get a themed tooltip with HTML from `htmlGetter(el)` (null = no tip).
function attachTooltip(container, selector, htmlGetter) {
  container.addEventListener('pointerover', e => {
    const el = e.target.closest(selector);
    if (!el || !container.contains(el)) return;
    const html = htmlGetter(el);
    if (!html) return;
    const tip = ensureTooltip();
    tip.innerHTML = html;
    tip.classList.remove('hidden');
    moveTooltip(e);
  });
  container.addEventListener('pointermove', e => {
    if (tooltipEl && !tooltipEl.classList.contains('hidden')) moveTooltip(e);
  });
  container.addEventListener('pointerout', e => {
    const el = e.target.closest(selector);
    if (el && tooltipEl) tooltipEl.classList.add('hidden');
  });
}


// ── Daily quality calendar ────────────────────────────────────────────
async function loadDaily() {
  const rows = await fetchJson('/api/daily');
  if (!rows) return;
  renderCalendar(rows);
}

function qualityColor(d) {
  if (!d || (!d.tests && !d.outage_seconds && !d.degraded_seconds)) return theme().calEmpty;
  let level;  // 0 best … 4 worst
  const adh = d.adherence_pct;
  if (adh == null) level = 2;
  else if (adh >= 80) level = 0;
  else if (adh >= 60) level = 1;
  else if (adh >= 40) level = 2;
  else if (adh >= 20) level = 3;
  else level = 4;
  if (d.outage_seconds > 1800) level = Math.min(4, level + 2);
  else if (d.outage_seconds > 300) level = Math.min(4, level + 1);
  if ((d.degraded_seconds || 0) > 1800) level = Math.min(4, level + 1);
  return theme().cal[level];
}

let calendarByDay = {};

function calendarTipHtml(el) {
  const key = el.dataset.day;
  const d = calendarByDay[key];
  const dateLabel = new Date(key).toLocaleDateString(undefined, {
    weekday: 'long', day: 'numeric', month: 'short',
  });
  if (!d || (!d.tests && !d.outage_seconds && !d.degraded_seconds)) {
    return `<div class="nm-tooltip-title">${dateLabel}</div><div class="dim">no data</div>`;
  }
  const row = (label, val) =>
    `<div class="nm-tooltip-row"><span class="dim">${label}</span><span>${val}</span></div>`;
  return `<div class="nm-tooltip-title">${dateLabel}</div>` +
    row('Avg download', d.dl_mean != null ? `${d.dl_mean} Mbps` : '—') +
    row('Adherence', d.adherence_pct != null ? `${d.adherence_pct}%` : '—') +
    row('Tests', d.tests) +
    row('Outages', fmtDuration(d.outage_seconds || 0)) +
    row('Degraded', fmtDuration(d.degraded_seconds || 0));
}

function renderCalendar(rows) {
  calendarByDay = Object.fromEntries(rows.map(r => [r.day, r]));
  const pad = n => String(n).padStart(2, '0');
  const cells = [];
  for (let i = 29; i >= 0; i--) {
    const dt = new Date(Date.now() - i * 864e5);
    const key = `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}`;
    const d = calendarByDay[key];
    const empty = !d || (!d.tests && !d.outage_seconds && !d.degraded_seconds);
    cells.push(
      `<div class="cal-day ${empty ? 'cal-day--empty' : ''}" ` +
      `style="background:${qualityColor(d)}" data-day="${key}">` +
      `<span>${dt.getDate()}</span></div>`
    );
  }
  const T = theme();
  const wrap = document.getElementById('calendar-wrap');
  wrap.innerHTML =
    `<div class="cal-grid">${cells.join('')}</div>` +
    `<div class="cal-legend dim small">` +
    `<span class="cal-chip" style="background:${T.cal[0]}"></span> good ` +
    `<span class="cal-chip" style="background:${T.cal[2]}"></span> below target ` +
    `<span class="cal-chip" style="background:${T.cal[4]}"></span> bad / outages ` +
    `<span class="cal-chip" style="background:${T.calEmpty}"></span> no data</div>`;
  if (!wrap.dataset.tooltipBound) {
    attachTooltip(wrap, '.cal-day', calendarTipHtml);
    wrap.dataset.tooltipBound = '1';
  }
}


// ── Outage Gantt timeline ─────────────────────────────────────────────
let ganttItems = [];

function ganttTipHtml(el) {
  const r = ganttItems[Number(el.dataset.gi)];
  if (!r) return null;
  const titles = { connection: 'Connection outage', degraded: 'Degraded period', host: 'Host check failed' };
  const dur = r.duration_seconds != null ? fmtDuration(r.duration_seconds) : 'ongoing';
  const row = (label, val) =>
    `<div class="nm-tooltip-row"><span class="dim">${label}</span><span>${val}</span></div>`;
  let extra = '';
  if (r.type === 'degraded') {
    extra = row('Avg loss', `${r.avg_loss_pct}%`) + row('Peak loss', `${r.peak_loss_pct}%`);
  } else {
    extra = row(r.type === 'host' ? 'Host' : 'Targets', r.triggers.join(', '));
  }
  return `<div class="nm-tooltip-title">${titles[r.type] ?? r.type}</div>` +
    row('Start', fmtDatetime(r.started_at)) +
    row('Duration', dur) + extra;
}

function renderGantt(rows, degraded) {
  const el = document.getElementById('outage-gantt');
  const now = Date.now();
  const start = now - 7 * 864e5;
  const span = now - start;
  const within = r => new Date(r.ended_at).getTime() > start;
  const conn = rows.filter(r => r.type === 'connection' && within(r));
  const host = rows.filter(r => r.type === 'host' && within(r));
  const deg  = (degraded || []).filter(within);

  if (!conn.length && !host.length && !deg.length) {
    el.innerHTML = '<div class="dim small" style="margin-bottom:8px">No outages or degraded periods in the last 7 days.</div>';
    return;
  }

  // Tooltip lookup: every bar carries an index into ganttItems.
  ganttItems = [...deg, ...conn, ...host];
  let gi = 0;
  const bar = (r, cls) => {
    const s = Math.max(new Date(r.started_at).getTime(), start);
    const e = Math.min(new Date(r.ended_at).getTime(), now);
    const left = ((s - start) / span * 100).toFixed(3);
    const width = Math.max((e - s) / span * 100, 0.35).toFixed(3);
    return `<div class="gantt-bar ${cls}" data-gi="${gi++}" style="left:${left}%;width:${width}%"></div>`;
  };

  // Day boundaries: tick lines inside tracks + a labeled axis row below.
  let ticks = '', axis = '';
  const first = new Date(start);
  first.setHours(24, 0, 0, 0);
  for (let t = first.getTime(); t < now; t += 864e5) {
    const left = ((t - start) / span * 100).toFixed(3);
    const d = new Date(t);
    const lbl = d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' });
    ticks += `<div class="gantt-tick" style="left:${left}%"></div>`;
    axis += `<span class="gantt-axis-label" style="left:${left}%">${lbl}</span>`;
  }

  // Degraded (amber) first in the DOM so outage red draws on top of it.
  const lane = (label, bars) =>
    `<div class="gantt-row"><span class="gantt-lane-label dim">${label}</span>` +
    `<div class="gantt-track">${ticks}${bars}</div></div>`;

  el.innerHTML =
    lane('Connection',
      deg.map(r => bar(r, 'gantt-bar--deg')).join('') +
      conn.map(r => bar(r, 'gantt-bar--conn')).join('')) +
    (host.length ? lane('Hosts', host.map(r => bar(r, 'gantt-bar--host')).join('')) : '') +
    `<div class="gantt-row"><span class="gantt-lane-label"></span>` +
      `<div class="gantt-axis">${axis}</div></div>` +
    `<div class="gantt-caption dim small">last 7 days — ` +
      `<span class="cal-chip" style="background:var(--offline)"></span> outage ` +
      `<span class="cal-chip" style="background:color-mix(in srgb, var(--degraded) 80%, transparent)"></span> degraded` +
      `${host.length ? ' <span class="cal-chip" style="background:var(--host)"></span> host check' : ''}</div>`;

  if (!el.dataset.tooltipBound) {
    attachTooltip(el, '.gantt-bar', ganttTipHtml);
    el.dataset.tooltipBound = '1';
  }
}


// ── Packet loss heatmap ───────────────────────────────────────────────
async function loadHeatmap() {
  const data = await fetchJson('/api/ping-heatmap');
  if (!data) return;
  renderHeatmap(data);
}

let heatmapByTarget = {};

function renderHeatmap({ targets, by_target }) {
  if (!targets.length) {
    document.getElementById('heatmap-wrap').innerHTML = '<span class="dim">No ping data yet.</span>';
    return;
  }
  heatmapByTarget = by_target;

  const hours = Array.from({ length: 24 }, (_, i) => i);
  const nowHour = new Date().getHours();
  let html = '<table class="heatmap-table"><thead><tr><th></th>';
  hours.forEach(h => {
    html += `<th class="${h === nowHour ? 'now' : ''}">${String(h).padStart(2,'0')}h</th>`;
  });
  html += '</tr></thead><tbody>';

  for (const target of targets) {
    html += `<tr><td class="heatmap-target-label">${target}</td>`;
    for (const h of hours) {
      const pct = by_target[target]?.[h];
      const bg  = lossColor(pct);
      const txt = pct != null ? (pct === 0 ? '' : `${pct}%`) : '';
      html += `<td class="${h === nowHour ? 'now' : ''}" style="background:${bg}" ` +
              `data-target="${target}" data-hour="${h}">${txt}</td>`;
    }
    html += '</tr>';
  }
  html += '</tbody></table>';
  const wrap = document.getElementById('heatmap-wrap');
  wrap.innerHTML = html;

  if (!wrap.dataset.tooltipBound) {
    attachTooltip(wrap, 'td[data-target]', heatmapTipHtml);
    wrap.dataset.tooltipBound = '1';
  }
}

function heatmapTipHtml(el) {
  const target = el.dataset.target;
  const h = Number(el.dataset.hour);
  const pct = heatmapByTarget[target]?.[h];
  const hourLabel = `${String(h).padStart(2, '0')}:00–${String((h + 1) % 24).padStart(2, '0')}:00`;
  const row = (label, val) =>
    `<div class="nm-tooltip-row"><span class="dim">${label}</span><span>${val}</span></div>`;
  return `<div class="nm-tooltip-title">${target}</div>` +
    row('Hour', hourLabel) +
    row('Packet loss', pct != null ? `${pct}%` : 'no data');
}

function lossColor(pct) {
  const L = theme().loss;
  if (pct == null)  return L.nodata;
  if (pct === 0)    return L.clean;
  if (pct < 2)      return L.trace;
  if (pct < 10)     return L.mid;
  if (pct < 30)     return L.bad;
  return L.severe;
}


// ── SSE ───────────────────────────────────────────────────────────────
function connectSSE() {
  const es = new EventSource('/api/stream');

  es.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === 'status_update') {
      applyStatus(data);
    } else if (data.type === 'speed_update') {
      appendSpeedPoint(data);
      loadAdherence();
      loadOutages();
    } else if (data.type === 'degraded_update') {
      loadOutages();
      loadDaily();
    } else if (data.type === 'speed_test_done') {
      setRunTestState(false, data.ok ? undefined : '✕ Test failed');
      if (!data.ok) setTimeout(() => setRunTestState(false), 4000);
      // Any attempt (even a failed one) proves the app is alive — keep the
      // no-data wash from creeping over the live edge of the chart.
      attemptTimes.push(Date.now());
      computeNoDataGaps();
      if (speedChart) speedChart.update('none');
    }
  };

  es.onerror = () => {
    es.close();
    setTimeout(connectSSE, 5000);
  };
}


// ── Manual speed test ─────────────────────────────────────────────────
let runTestTimer = null;

function initRunTest() {
  document.getElementById('run-test').addEventListener('click', triggerSpeedTest);
}

function setRunTestState(running, label) {
  const btn = document.getElementById('run-test');
  btn.disabled = running;
  btn.textContent = label ?? (running ? 'Running…' : '▶ Run test');
  if (running) {
    // Safety net: never leave the button stuck if events are missed.
    clearTimeout(runTestTimer);
    runTestTimer = setTimeout(() => setRunTestState(false), 3 * 60 * 1000);
  } else {
    clearTimeout(runTestTimer);
  }
}

async function triggerSpeedTest() {
  setRunTestState(true, 'Starting…');
  const resp = await fetch('/api/speed-test/run', { method: 'POST' }).catch(() => null);
  const data = resp ? await resp.json().catch(() => null) : null;

  if (!resp || !resp.ok || !data || !data.ok) {
    setRunTestState(false, (data && data.error) ? '✕ ' + data.error : '✕ Failed');
    setTimeout(() => setRunTestState(false), 4000);
    return;
  }
  setRunTestState(true);
}


// ── Settings modal ────────────────────────────────────────────────────
let avgMbPerTest = null;   // from /api/settings; null until loaded
let costEstimated = true;

const $s = id => document.getElementById(id);

function initSettings() {
  $s('settings-open').addEventListener('click', openSettings);
  $s('settings-close').addEventListener('click', closeSettings);
  $s('settings-cancel').addEventListener('click', closeSettings);
  $s('settings-overlay').addEventListener('click', e => {
    if (e.target === $s('settings-overlay')) closeSettings();
  });
  $s('settings-save').addEventListener('click', saveSettings);
  $s('settings-restart').addEventListener('click', restartMonitoring);
  $s('settings-form').addEventListener('input', updateSettingsStats);
  $s('settings-form').addEventListener('submit', e => { e.preventDefault(); saveSettings(); });

  // Display preference: client-side only (localStorage), applies instantly —
  // independent of the Save / Restart-monitoring flow.
  const errChk = $s('set-show-errors');
  errChk.checked = showErrorDots();
  errChk.addEventListener('change', () => {
    localStorage.setItem(PREF_ERROR_DOTS, String(errChk.checked));
    if (!speedChart) return;
    const idx = speedChart.data.datasets.findIndex(d => d.label === 'Error');
    if (idx >= 0) {
      speedChart.setDatasetVisibility(idx, errChk.checked);
      speedChart.update();
    }
  });
}

async function openSettings() {
  const data = await fetchJson('/api/settings');
  if (!data) {
    showSettingsError('Could not load current settings.');
  } else {
    const s = data.settings;
    $s('set-target').value        = s.target_mbps;
    $s('set-st-interval').value   = s.speed_test.interval_hours;
    $s('set-st-soft').value       = s.speed_test.soft_threshold;
    $s('set-st-hard').value       = s.speed_test.hard_threshold;
    $s('set-st-retry').value      = s.speed_test.postpone_retry_minutes;
    $s('set-st-maxpost').value    = s.speed_test.max_postpones;
    $s('set-conn-interval').value = s.connectivity.ping_interval_seconds;
    $s('set-conn-thresh').value   = s.connectivity.outage_threshold_failures;
    $s('set-conn-degpct').value   = s.connectivity.degraded_loss_threshold_pct;
    $s('set-conn-degwin').value   = s.connectivity.degraded_window_minutes;
    $s('set-conn-targets').value  = s.connectivity.ping_targets.join('\n');

    avgMbPerTest  = data.data_cost.avg_mb_per_test;
    costEstimated = data.data_cost.estimated;
    $s('stat-cost-note').textContent = costEstimated
      ? '(estimated — no byte data yet)'
      : `(measured, avg of ${data.data_cost.sample_count} tests)`;
    hideSettingsError();
    updateSettingsStats();
  }
  $s('settings-notice').textContent = '';
  $s('settings-restart').classList.add('hidden');
  $s('settings-save').classList.remove('hidden');
  $s('settings-overlay').classList.remove('hidden');
}

function closeSettings() {
  $s('settings-overlay').classList.add('hidden');
}

function updateSettingsStats() {
  const intervalH  = parseFloat($s('set-st-interval').value);
  const pingSec    = parseFloat($s('set-conn-interval').value);
  const threshold  = parseInt($s('set-conn-thresh').value, 10);
  const nTargets   = collectTargets().length;

  if (avgMbPerTest != null) {
    $s('stat-cost-test').textContent = `${Math.round(avgMbPerTest)} MB`;
    if (intervalH > 0) {
      const perDay = avgMbPerTest * (24 / intervalH);
      $s('stat-tests-day').textContent = (24 / intervalH).toFixed(1);
      $s('stat-cost-day').textContent  = perDay >= 1000
        ? `${(perDay / 1000).toFixed(2)} GB`
        : `${Math.round(perDay)} MB`;
    }
  }
  if (pingSec > 0 && threshold > 0) {
    $s('stat-detect').textContent = fmtDuration(Math.round(pingSec * threshold));
  }
  if (pingSec > 0 && nTargets > 0) {
    $s('stat-rows-day').textContent = Math.round((86400 / pingSec) * nTargets).toLocaleString();
  }
}

function collectTargets() {
  return $s('set-conn-targets').value
    .split('\n').map(t => t.trim()).filter(Boolean);
}

function collectSettings() {
  return {
    target_mbps: parseFloat($s('set-target').value),
    speed_test: {
      interval_hours:         parseFloat($s('set-st-interval').value),
      soft_threshold:         parseFloat($s('set-st-soft').value),
      hard_threshold:         parseFloat($s('set-st-hard').value),
      postpone_retry_minutes: parseInt($s('set-st-retry').value, 10),
      max_postpones:          parseInt($s('set-st-maxpost').value, 10),
    },
    connectivity: {
      ping_interval_seconds:       parseInt($s('set-conn-interval').value, 10),
      outage_threshold_failures:   parseInt($s('set-conn-thresh').value, 10),
      degraded_loss_threshold_pct: parseFloat($s('set-conn-degpct').value),
      degraded_window_minutes:     parseInt($s('set-conn-degwin').value, 10),
      ping_targets:                collectTargets(),
    },
  };
}

async function saveSettings() {
  hideSettingsError();
  const body = collectSettings();
  const vals = [
    body.target_mbps,
    ...Object.values(body.speed_test),
    body.connectivity.ping_interval_seconds,
    body.connectivity.outage_threshold_failures,
  ];
  if (vals.some(v => v == null || Number.isNaN(v))) {
    showSettingsError('All fields must be filled with valid numbers.');
    return;
  }

  const resp = await fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).catch(() => null);
  const data = resp ? await resp.json().catch(() => null) : null;

  if (!resp || !resp.ok || !data || !data.ok) {
    showSettingsError((data && data.error) || 'Save failed.');
    return;
  }
  $s('settings-notice').textContent = 'Saved — restart monitoring to apply.';
  $s('settings-save').classList.add('hidden');
  $s('settings-restart').classList.remove('hidden');
}

async function restartMonitoring() {
  hideSettingsError();
  $s('settings-notice').textContent = 'Restarting…';
  const resp = await fetch('/api/restart', { method: 'POST' }).catch(() => null);
  const data = resp ? await resp.json().catch(() => null) : null;

  if (!resp || !resp.ok || !data || !data.ok) {
    showSettingsError((data && data.error) || 'Restart failed.');
    $s('settings-notice').textContent = 'Saved (not yet applied).';
    return;
  }
  $s('settings-notice').textContent = 'Monitoring restarted with new settings.';
  setTimeout(() => location.reload(), 800);
}

function showSettingsError(msg) {
  const el = $s('settings-error');
  el.textContent = msg;
  el.classList.remove('hidden');
}

function hideSettingsError() {
  $s('settings-error').classList.add('hidden');
}


// ── Helpers ───────────────────────────────────────────────────────────
async function fetchJson(url) {
  try {
    const r = await fetch(url);
    // Session expired (auth enabled) — bounce to the login page.
    if (r.status === 401) { window.location = '/login'; return null; }
    return r.ok ? r.json() : null;
  } catch {
    return null;
  }
}

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function fmtMbps(v) {
  return v != null ? `${round1(v)} Mbps` : '—';
}

function fmtRelative(iso) {
  if (!iso) return '—';
  const diff = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (diff < 60)   return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return fmtDatetime(iso);
}

function fmtDatetime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

function fmtDuration(seconds) {
  if (seconds < 60)   return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

function round1(v) {
  return v != null ? Math.round(v * 10) / 10 : null;
}
