'use strict';

// ── Chart.js global defaults ────────────────────────────────────────
Chart.defaults.color = '#8b949e';
Chart.defaults.borderColor = '#30363d';
Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';

let speedChart = null;

// ── Bootstrap ────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await Promise.all([
    loadStatus(),
    loadSpeedHistory(),
    loadAdherence(),
    loadOutages(),
    loadHeatmap(),
  ]);
  connectSSE();
  initSettings();
  initRunTest();
});


// ── Status strip ─────────────────────────────────────────────────────
async function loadStatus() {
  const data = await fetchJson('/api/status');
  if (data) applyStatus(data);
}

function applyStatus(data) {
  const dot   = document.getElementById('status-dot');
  const label = document.getElementById('status-label');
  const STATUS_TEXT = { online: 'Online', offline: 'Offline', degraded: 'Degraded', unknown: 'Unknown' };

  dot.className = `dot dot--${data.status}`;
  label.textContent = STATUS_TEXT[data.status] ?? data.status;

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
  buildSpeedChart(data.data, data.events, data.target_mbps);
}

function buildSpeedChart(rows, eventRows, targetMbps) {
  const dlData = rows.map(r => ({ x: r.timestamp, y: round1(r.download_mbps) }));
  const ulData = rows.map(r => ({ x: r.timestamp, y: round1(r.upload_mbps) }));

  // Annotation scatter datasets (postponed / skipped / error / forced)
  const STATUS_COLORS = {
    postponed: '#d29922',
    skipped:   '#6e7681',
    forced:    '#a371f7',
    error:     '#f85149',
  };
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
  }));

  const ctx = document.getElementById('speedChart').getContext('2d');
  speedChart = new Chart(ctx, {
    type: 'line',
    data: {
      datasets: [
        {
          label: 'Download',
          data: dlData,
          borderColor: '#58a6ff',
          backgroundColor: 'rgba(88,166,255,0.08)',
          borderWidth: 2,
          pointRadius: 3,
          tension: 0.3,
          fill: true,
        },
        {
          label: 'Upload',
          data: ulData,
          borderColor: '#3fb950',
          backgroundColor: 'transparent',
          borderWidth: 1.5,
          pointRadius: 2,
          tension: 0.3,
        },
        {
          label: `Target (${targetMbps} Mbps)`,
          data: dlData.length
            ? [{ x: dlData[0].x, y: targetMbps }, { x: dlData[dlData.length - 1].x, y: targetMbps }]
            : [],
          borderColor: '#f85149',
          borderWidth: 1.5,
          borderDash: [6, 4],
          pointRadius: 0,
          fill: false,
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
          time: { tooltipFormat: 'MMM d, HH:mm' },
          grid: { color: '#21262d' },
        },
        y: {
          title: { display: true, text: 'Mbps' },
          min: 0,
          grid: { color: '#21262d' },
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
  const ts = data.timestamp;
  speedChart.data.datasets[0].data.push({ x: ts, y: round1(data.download_mbps) });
  speedChart.data.datasets[1].data.push({ x: ts, y: round1(data.upload_mbps) });
  // Extend target line
  speedChart.data.datasets[2].data[1] = { x: ts, y: TARGET_MBPS };
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
  const rows = await fetchJson('/api/outages');
  if (!rows) return;
  renderOutages(rows);
}

function renderOutages(rows) {
  const el = document.getElementById('outage-list');
  if (!rows.length) {
    el.innerHTML = '<span class="dim">No outages in the last 30 days.</span>';
    return;
  }
  el.innerHTML = rows.map(r => {
    const dur    = r.duration_seconds != null ? fmtDuration(r.duration_seconds) : '—';
    const start  = fmtDatetime(r.started_at);
    const badge  = r.is_open ? '<span class="outage-open-badge">OPEN</span>' : '';
    const isHost = r.type === 'host';
    const title  = isHost ? r.triggers[0] : 'Connection';
    const sub    = isHost
      ? 'host check — site/DNS unreachable'
      : r.triggers.join(' · ');
    return `
      <div class="outage-item ${r.is_open ? 'open' : ''} ${isHost ? 'outage-item--host' : ''}">
        <div>
          <div><strong>${title}</strong> — ${start} ${badge}</div>
          <div class="outage-trigger">${sub}</div>
        </div>
        <div class="outage-duration">${dur}</div>
      </div>`;
  }).join('');
}


// ── Packet loss heatmap ───────────────────────────────────────────────
async function loadHeatmap() {
  const data = await fetchJson('/api/ping-heatmap');
  if (!data) return;
  renderHeatmap(data);
}

function renderHeatmap({ targets, by_target }) {
  if (!targets.length) {
    document.getElementById('heatmap-wrap').innerHTML = '<span class="dim">No ping data yet.</span>';
    return;
  }

  const hours = Array.from({ length: 24 }, (_, i) => i);
  let html = '<table class="heatmap-table"><thead><tr><th></th>';
  hours.forEach(h => { html += `<th>${String(h).padStart(2,'0')}h</th>`; });
  html += '</tr></thead><tbody>';

  for (const target of targets) {
    html += `<tr><td class="heatmap-target-label">${target}</td>`;
    for (const h of hours) {
      const pct = by_target[target]?.[h];
      const bg  = lossColor(pct);
      const txt = pct != null ? (pct === 0 ? '' : `${pct}%`) : '';
      const title = pct != null ? `${target} at ${String(h).padStart(2,'0')}:00 UTC — ${pct}% loss` : 'no data';
      html += `<td style="background:${bg}" title="${title}">${txt}</td>`;
    }
    html += '</tr>';
  }
  html += '</tbody></table>';
  document.getElementById('heatmap-wrap').innerHTML = html;
}

function lossColor(pct) {
  if (pct == null)  return '#1e2432';   // no data
  if (pct === 0)    return '#0d2b0d';   // clean
  if (pct < 2)      return '#2b2200';   // trace loss
  if (pct < 10)     return '#3a1a00';   // noticeable
  if (pct < 30)     return '#3a0a00';   // bad
  return '#3a0000';                      // severe
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
    } else if (data.type === 'speed_test_done') {
      setRunTestState(false, data.ok ? undefined : '✕ Test failed');
      if (!data.ok) setTimeout(() => setRunTestState(false), 4000);
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
      ping_interval_seconds:     parseInt($s('set-conn-interval').value, 10),
      outage_threshold_failures: parseInt($s('set-conn-thresh').value, 10),
      ping_targets:              collectTargets(),
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
