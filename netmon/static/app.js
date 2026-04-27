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
    const dur   = r.duration_seconds != null ? fmtDuration(r.duration_seconds) : '—';
    const start = fmtDatetime(r.started_at);
    const badge = r.is_open ? '<span class="outage-open-badge">OPEN</span>' : '';
    return `
      <div class="outage-item ${r.is_open ? 'open' : ''}">
        <div>
          <div>${start} ${badge}</div>
          <div class="outage-trigger">${r.trigger}</div>
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
    }
  };

  es.onerror = () => {
    es.close();
    setTimeout(connectSSE, 5000);
  };
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
