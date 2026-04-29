let data = null;
let liveData = null;
let selectedPort = null;
let liveInterval = null;

async function fetchData() {
  const r = await fetch('/api/data');
  data = await r.json();
  if (data.error) { showToast('No data yet. Run ingest first.'); return; }
  renderAll();
  startLivePolling();
}

async function fetchLive() {
  try {
    const r = await fetch('/api/live');
    liveData = await r.json();
    if (liveData.error) return;
    updateLiveIndicators();
  } catch (e) {
    console.error('live fetch failed', e);
  }
}

function startLivePolling() {
  fetchLive();
  if (liveInterval) clearInterval(liveInterval);
  liveInterval = setInterval(fetchLive, 15000);
}

async function triggerIngest() {
  const btn = document.getElementById('btn-ingest');
  btn.disabled = true; btn.textContent = '⏳ Ingesting...';
  try {
    const r = await fetch('/api/ingest');
    const result = await r.json();
    if (result.success) { showToast('Ingest complete!'); await fetchData(); }
    else { showToast('Ingest failed: ' + (result.error || result.output)); }
  } catch (e) { showToast('Error: ' + e.message); }
  finally { btn.disabled = false; btn.textContent = '↻ Re-ingest'; }
}

function livePortId(portId) {
  const mod = parseInt(portId.split('/')[1]);
  if (mod === 3) return `10GigabitEthernet${portId}`;
  if (mod === 2) {
    const n = parseInt(portId.split('/')[2]);
    if (n === 1 || n === 6) return `40GigabitEthernet${portId}`;
    return `10GigabitEthernet${portId}`;
  }
  return `GigabitEthernet${portId}`;
}

function liveStatus(portId) {
  if (!liveData || liveData.error || !liveData.interfaces) return null;
  return liveData.interfaces[livePortId(portId)] || null;
}

function liveClass(portId) {
  const ls = liveStatus(portId);
  if (!ls) return null;
  if (ls.oper_status === 1) return 'port-up';
  return 'port-down';
}

function renderAll() {
  if (!data) return;
  const c = data.config || {};
  document.getElementById('hostname').textContent = c.hostname || 'Switch';
  document.getElementById('model').textContent = 'ICX 6610-48P';
  document.getElementById('version').textContent = c.version || '';

  renderSystemChips(data.chassis);
  renderSfpPorts();
  renderRj45Ports();
  renderQsqfPorts();
  renderLags();
}

function renderSystemChips(chassis) {
  const el = document.getElementById('system-chips');
  el.innerHTML = '';
  if (!chassis) return;
  for (const ps of (chassis.power_supplies || [])) {
    const ok = ps.status === 'ok';
    el.appendChild(chip(`${ps.type} PSU${ps.id}`, ok ? 'chip-ok' : 'chip-warn'));
  }
  for (const fan of (chassis.fans || [])) {
    const ok = fan.status === 'ok';
    el.appendChild(chip(`Fan${fan.id} ${fan.status}`, ok ? 'chip-ok' : 'chip-warn'));
  }
  const temps = chassis.temperatures || {};
  for (const [k, v] of Object.entries(temps)) {
    const warn = v > 70;
    el.appendChild(chip(`${k}: ${v}°C`, warn ? 'chip-warn' : 'chip-temp'));
  }
}

function updateLiveIndicators() {
  if (!liveData || liveData.error) return;
  const lc = liveData.chassis || {};
  const chips = document.getElementById('system-chips');

  let liveChip = document.getElementById('live-timestamp');
  if (!liveChip) {
    liveChip = chip('', 'chip-temp');
    liveChip.id = 'live-timestamp';
    chips.prepend(liveChip);
  }
  const ts = liveData.timestamp ? new Date(liveData.timestamp * 1000).toLocaleTimeString() : '?';
  liveChip.innerHTML = `<span class="chip-dot"></span>Live ${ts}`;

  if (lc.temperature !== undefined) {
    let found = false;
    for (const ch of chips.children) {
      if (ch.textContent.includes('Temp')) {
        const parts = ch.textContent.split(': ');
        ch.innerHTML = `<span class="chip-dot"></span>${parts[0]}: ${lc.temperature}°C`;
        ch.className = 'chip chip-temp';
        found = true;
        break;
      }
    }
    if (!found) {
      chips.appendChild(chip(`Temp: ${lc.temperature}°C`, 'chip-temp'));
    }
  }



  document.querySelectorAll('.port').forEach(el => {
    const pid = el.dataset.port;
    const p = (data.interfaces || {})[pid];
    if (p && p.link === 'Disable') {
      el.className = 'port port-disable';
    } else {
      const lc = liveClass(pid);
      if (lc) {
        el.className = `port ${lc}`;
        if (p?.inline_power) el.classList.add('port-poe');
      }
    }
  });

  updateLagChips();

  if (selectedPort) showDetail(selectedPort);
}

function updateLagChips() {
  document.querySelectorAll('.lag-port-chip').forEach(el => {
    const pid = el.textContent;
    const ls = liveStatus(pid);
    if (ls) {
      el.className = 'lag-port-chip ' + (ls.oper_status === 1 ? 'lag-port-up' : 'lag-port-down');
    }
  });
}

function chip(text, cls) {
  const d = document.createElement('span');
  d.className = 'chip ' + cls;
  d.innerHTML = `<span class="chip-dot"></span>${text}`;
  return d;
}

function portClass(p) {
  if (!p) return 'port-down';
  if (p.link === 'Up') return 'port-up';
  if (p.link === 'Disable') return 'port-disable';
  return 'port-down';
}

function makePortEl(portId) {
  const p = (data.interfaces || {})[portId];
  const el = document.createElement('div');
  el.className = 'port ' + (liveClass(portId) || portClass(p));
  if (p && p.inline_power) el.classList.add('port-poe');
  el.dataset.port = portId;
  el.textContent = portId.split('/').pop();
  el.title = portId;
  el.addEventListener('click', () => showDetail(portId));
  return el;
}

function renderSfpPorts() {
  const row = document.getElementById('sfp-row');
  row.innerHTML = '';
  for (let i = 1; i <= 8; i++) {
    const pid = `1/3/${i}`;
    row.appendChild(makePortEl(pid));
  }
}

function renderRj45Ports() {
  const grid = document.getElementById('port-grid');
  grid.innerHTML = '';
  const topRow = document.createElement('div');
  topRow.className = 'port-row';
  const botRow = document.createElement('div');
  botRow.className = 'port-row';
  for (let i = 1; i <= 24; i++) {
    const odd = 2 * i - 1;
    const even = 2 * i;
    topRow.appendChild(makePortEl(`1/1/${odd}`));
    botRow.appendChild(makePortEl(`1/1/${even}`));
  }
  grid.appendChild(topRow);
  grid.appendChild(botRow);
}

function renderQsqfPorts() {
  const section = document.getElementById('qsfp-section');
  if (!section) return;

  const groups = [
    { label: 'Stacking', ports: ['1/2/1'], cls: 'port-stack' },
    { label: 'Breakout 10G', ports: ['1/2/2', '1/2/3', '1/2/4', '1/2/5'], cls: '' },
    { label: 'Stacking', ports: ['1/2/6'], cls: 'port-stack' },
    { label: 'Breakout 10G', ports: ['1/2/7', '1/2/8', '1/2/9', '1/2/10'], cls: '' },
  ];

  section.innerHTML = '';
  for (const g of groups) {
    const groupDiv = document.createElement('div');
    groupDiv.className = 'qsfp-group';
    const label = document.createElement('div');
    label.className = 'qsfp-group-label';
    label.textContent = g.label;
    groupDiv.appendChild(label);
    const portsDiv = document.createElement('div');
    portsDiv.className = 'qsfp-ports';
    for (const pid of g.ports) {
      const el = makePortEl(pid);
      if (g.cls) el.classList.add(g.cls);
      portsDiv.appendChild(el);
    }
    groupDiv.appendChild(portsDiv);
    section.appendChild(groupDiv);
  }
}

function renderLags() {
  const section = document.getElementById('lag-section');
  section.innerHTML = '';

  const lags = data.lag_details || [];
  if (lags.length === 0) {
    section.innerHTML = '<div class="lag-card" style="color:var(--text-dim)">No LAGs configured</div>';
    return;
  }

  for (const lag of lags) {
    const card = document.createElement('div');
    card.className = 'lag-card';

    const nameSpan = document.createElement('div');
    nameSpan.className = 'lag-name';
    nameSpan.innerHTML = `${lag.name} <span class="lag-id">#${lag.id}</span>`;
    card.appendChild(nameSpan);

    const portsDiv = document.createElement('div');
    portsDiv.className = 'lag-ports';
    for (const pid of (lag.ports || [])) {
      const ls = liveStatus(pid);
      const chip = document.createElement('span');
      chip.className = 'lag-port-chip ' + (ls ? (ls.oper_status === 1 ? 'lag-port-up' : 'lag-port-down') : 'lag-port-down');
      chip.textContent = pid;
      chip.addEventListener('click', () => showDetail(pid));
      chip.style.cursor = 'pointer';
      portsDiv.appendChild(chip);
    }
    card.appendChild(portsDiv);
    section.appendChild(card);
  }
}

function showDetail(portId) {
  selectedPort = portId;
  const p = (data.interfaces || {})[portId];
  const ls = liveStatus(portId);
  const empty = document.getElementById('detail-empty');
  const content = document.getElementById('detail-content');
  empty.style.display = 'none';
  content.style.display = 'block';

  if (!p) {
    content.innerHTML = `<div class="detail-empty">No data for port ${portId}</div>`;
    return;
  }

  const vlanHtml = (p.vlans || []).map(v => {
    const tags = [];
    if (v.tagged) tags.push('<span class="vlan-tag vlan-tag-t">T</span>');
    if (v.untagged) tags.push('<span class="vlan-tag vlan-tag-u">U</span>');
    if (v.native && !v.tagged && !v.untagged) tags.push('<span class="vlan-tag vlan-tag-u">PVID</span>');
    const label = v.name ? `${v.id} (${v.name})` : `${v.id}`;
    return `<div>${tags.join('')} ${label}</div>`;
  }).join('') || '<div style="color:var(--text-dim)">None</div>';

  const parsedStats = p.stats || {};
  const linkState = p.link === 'Disable' ? 'Disable'
    : ls && ls.oper_status === 1 ? 'Up'
    : p.link === 'Up' ? 'Up'
    : 'Down';
  const speed = p.link === 'Disable' ? '' : (p.speed && p.speed !== 'None' ? p.speed : '');
  const status = linkState === 'Up' ? 'up' : linkState === 'Disable' ? 'disable' : 'down';
  const poeClass = p.inline_power ? 'poe-on' : 'poe-off';
  const poeText = p.inline_power ? '● On' : '○ Off';

  content.innerHTML = `
    <div class="detail-port-header">
      <span class="detail-port-id">${portId}</span>
      <span class="detail-status-badge ${status}">${linkState} ${speed}</span>
    </div>
    <div class="detail-grid">
      <div class="detail-field">
        <div class="detail-field-label">State</div>
        <div class="detail-field-value">${p.state || 'N/A'}</div>
      </div>
      <div class="detail-field">
        <div class="detail-field-label">Duplex</div>
        <div class="detail-field-value">${p.duplex || 'N/A'}</div>
      </div>
      <div class="detail-field">
        <div class="detail-field-label">Speed</div>
        <div class="detail-field-value">${p.speed || 'N/A'}</div>
      </div>
      <div class="detail-field">
        <div class="detail-field-label">PVID</div>
        <div class="detail-field-value">${p.pvid !== null && p.pvid !== undefined ? p.pvid : 'N/A'}</div>
      </div>
      <div class="detail-field">
        <div class="detail-field-label">MAC</div>
        <div class="detail-field-value" style="font-size:12px">${p.mac || 'N/A'}</div>
      </div>
      <div class="detail-field">
        <div class="detail-field-label">Trunk (LAG)</div>
        <div class="detail-field-value">${p.trunk || 'None'}</div>
      </div>
      <div class="detail-field">
        <div class="detail-field-label">PoE</div>
        <div class="detail-field-value ${poeClass}">${poeText}</div>
      </div>
      <div class="detail-field">
        <div class="detail-field-label">Tagged</div>
        <div class="detail-field-value">${p.tag || 'N/A'}</div>
      </div>
    </div>
    <div class="detail-section">
      <div class="detail-section-title">VLAN Membership</div>
      <div>${vlanHtml}</div>
    </div>
    <div class="detail-section">
      <div class="detail-section-title">Live Statistics (SNMP)</div>
      <div class="detail-grid">
        <div class="detail-field">
          <div class="detail-field-label">In Bytes</div>
          <div class="detail-field-value">${ls ? (ls.in_octets || 0).toLocaleString() : 'N/A'}</div>
        </div>
        <div class="detail-field">
          <div class="detail-field-label">Out Bytes</div>
          <div class="detail-field-value">${ls ? (ls.out_octets || 0).toLocaleString() : 'N/A'}</div>
        </div>
        <div class="detail-field">
          <div class="detail-field-label">In Errors</div>
          <div class="detail-field-value">${ls ? (ls.in_errors || 0).toLocaleString() : (parsedStats.in_errors || 0).toLocaleString()}</div>
        </div>
        <div class="detail-field">
          <div class="detail-field-label">Out Errors</div>
          <div class="detail-field-value">${ls ? (ls.out_errors || 0).toLocaleString() : (parsedStats.out_errors || 0).toLocaleString()}</div>
        </div>
      </div>
      <div style="font-size:10px;color:var(--text-dim);margin-top:4px">
        ${liveData && liveData.timestamp ? 'Updated ' + new Date(liveData.timestamp * 1000).toLocaleTimeString() : ''}
      </div>
    </div>
  `;
}

let toastTimer = null;
function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 3000);
}

fetchData();
