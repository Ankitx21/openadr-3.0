'use strict';
const $ = id => document.getElementById(id);
const palette = ['#34775b', '#6d8fb4', '#bd9553', '#9b7baf', '#80a34a', '#c97969'];
const make = (tag, text, parent) => { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (parent) parent.append(el); return el; };
async function api(url, options = {}) {
  const response = await fetch(url, {...options, headers: {'Accept': 'application/json', ...options.headers}});
  if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('Your session may have expired. Sign in again, or retry shortly.');
  const data = await response.json();
  if (!response.ok) { const error = new Error(data.message || 'The request could not be completed.'); error.retry = Number(response.headers.get('Retry-After') || data.retry_after_seconds || 0); throw error; }
  return data;
}
const bars = [...document.querySelectorAll('[data-price]')];
if (bars.length) { const max = Math.max(...bars.map(b => Number(b.dataset.price)), 1); bars.forEach(b => b.style.height = (Number(b.dataset.price) / max * 95) + 'px'); }
if ($('teamSearch')) $('teamSearch').addEventListener('input', event => { let count = 0; document.querySelectorAll('#teamTable tbody tr[data-team]').forEach(row => { row.hidden = !row.dataset.team.includes(event.target.value.trim().toLowerCase()); if (!row.hidden) count++; }); $('noTeams').hidden = count > 0; });

// Dependency-free SVG charts. Labels always use text nodes, including student-provided names.
function drawChart(data) {
  const target = $('energyChart'); target.replaceChildren(); $('chartLegend').replaceChildren();
  const empty = !data.labels.length; $('chartEmpty').hidden = !empty; target.hidden = empty;
  if (empty) return;
  const ns = 'http://www.w3.org/2000/svg';
  const node = (tag, attrs, text) => { const el = document.createElementNS(ns, tag); for (const [k,v] of Object.entries(attrs)) el.setAttribute(k,v); if (text !== undefined) el.textContent = text; return el; };
  const svg = node('svg', {viewBox:'0 0 1000 280', role:'img', 'aria-label':`Energy profile for ${data.team_name}; ${data.total_intervals} intervals. Exact values are in the reading details table.`});
  target.append(svg);
  const values = Object.values(data.series_by_appliance).flat().filter(v => v !== null);
  const max = Math.max(...values, 0.01) * 1.15;
  for (let i=0; i<=4; i++) { const y = 220-i*49; svg.append(node('line', {x1:48,x2:915,y1:y,y2:y,class:'gridline'}), node('text', {x:40,y:y+4,'text-anchor':'end'}, (max*i/4).toFixed(2))); }
  const times = data.timestamps.map(x => new Date(x).getTime());
  const start = times[0], span = Math.max(times[times.length-1]-start, 900000);
  const xAt = i => 55 + (times[i]-start)/span*850;
  const every = Math.max(1,Math.ceil(data.labels.length/6));
  data.labels.forEach((label,i) => { if (i%every===0) svg.append(node('text',{x:xAt(i),y:245,'text-anchor':i===0?'start':'middle',class:'axis-label'},label)); });
  Object.entries(data.series_by_appliance).forEach(([name, points], index) => {
    const color = palette[index%palette.length]; let path='', previous = -1;
    points.forEach((value,i) => { if (value===null) { previous=-1; return; } const contiguous = previous >= 0 && times[i]-times[previous] <= 900000; const x=xAt(i), y=220-value/max*196; path += `${contiguous?'L':'M'}${x.toFixed(2)},${y.toFixed(2)} `; previous=i;
      if (points.length < 120 || !contiguous) { const dot=node('circle',{cx:x,cy:y,r:3,fill:color}); dot.append(node('title',{},`${name} · ${data.labels[i]} IST · ${value} kWh`)); svg.append(dot); }
    });
    svg.append(node('path',{d:path,fill:'none',stroke:color,'stroke-width':2.5,'stroke-linejoin':'round'}));
    const legend = make('span', undefined, $('chartLegend')); const icon=make('i',undefined,legend); icon.className='legend'; icon.style.background=color; legend.append(document.createTextNode(name));
  });
  const observed = data.temperature_series.filter(value => value !== null);
  if (observed.length) {
    const low = Math.min(...observed)-3, high = Math.max(...observed)+3;
    let path='', previous=-1;
    data.temperature_series.forEach((value,i)=> {
      if(value===null){previous=-1;return;}
      const contiguous=previous>=0 && times[i]-times[previous]<=900000;
      const x=xAt(i), y=220-(value-low)/(high-low)*196;
      path+=`${contiguous?'L':'M'}${x.toFixed(2)},${y.toFixed(2)} `;previous=i;
      if(data.labels.length<120 || !contiguous){const dot=node('circle',{cx:x,cy:y,r:2,fill:'#c18b4f'});dot.append(node('title',{},`${data.labels[i]} IST ? ${value} ?C`));svg.append(dot);}
    });
    svg.append(node('path',{d:path,fill:'none',stroke:'#c18b4f','stroke-width':1.8,'stroke-dasharray':'5 5'}));
    for(let i=0;i<=4;i++) svg.append(node('text',{x:935,y:224-i*49,'text-anchor':'start'},(low+(high-low)*i/4).toFixed(1)+'?C'));
    const legend=make('span',undefined,$('chartLegend'));const icon=make('i',undefined,legend);icon.className='legend';icon.style.background='#c18b4f';legend.append(document.createTextNode('Outdoor temperature ? dashed'));
  }
}
if ($('teamSelect')) {
  let busy = false, pending = false, cooldownUntil = 0;
  async function load() {
    if (busy) { pending=true; return; }
    if (Date.now() < cooldownUntil) { $('analyticsStatus').textContent='Request limit reached. Refresh will resume after the retry period.'; return; }
    if (!$('teamSelect').value) { $('analyticsStatus').textContent='No teams have been configured. Add teams in team management.'; $('telemetryTableBody').replaceChildren(); $('chartEmpty').hidden=false; return; }
    busy = true; $('refreshAnalytics').disabled=true;
    const params = new URLSearchParams({team:$('teamSelect').value, phase:$('phaseSelect').value, appliance:$('applianceSelect').value, range:$('rangeSelect').value});
    $('analyticsStatus').textContent='Updating readings…';
    try {
      const data = await api('/api/v3/telemetry/intervals?' + params);
      $('metricTotalEnergy').textContent=data.stats.sum_energy_kwh.toLocaleString(); $('metricPeakEnergy').textContent=data.stats.peak_interval_kwh.toLocaleString(); $('metricAvgTemp').textContent=data.stats.avg_temp_c ?? '—'; $('metricIntervalCount').textContent=data.stats.interval_count;
      const select=$('applianceSelect'), selected=select.value; select.replaceChildren(); const all=make('option','All appliances',select); all.value='all'; data.appliances.forEach(name=> {const opt=make('option',name,select); opt.value=name;}); select.value=data.appliances.includes(selected)?selected:'all';
      drawChart(data);
      const tbody=$('telemetryTableBody'); tbody.replaceChildren();
      if (!data.table_rows.length) {const td=make('td','No readings match these filters.',make('tr',undefined,tbody)); td.colSpan=5; td.className='empty';}
      data.table_rows.forEach(row=> { const tr=make('tr',undefined,tbody); [row.interval_ist, row.appliance, row.energy.toFixed(3), row.outdoor_temperature===null?'—':row.outdoor_temperature+' °C', row.recorded_at].forEach(value=>make('td',value,tr)); });
      $('exportTeam').href='/download/telemetry.csv?'+new URLSearchParams({team:params.get('team'),phase:params.get('phase')});
      $('analyticsStatus').textContent=`Updated ${new Date().toLocaleTimeString('en-IN',{timeZone:'Asia/Kolkata'})} IST · ${data.total_records} readings · Gaps represent missing data.`; $('analyticsStatus').className='status-line';
    } catch(error) { if (error.retry) cooldownUntil=Date.now()+error.retry*1000; $('analyticsStatus').textContent=error.message; $('analyticsStatus').className='status-line error'; }
    finally {busy=false; $('refreshAnalytics').disabled=false; if(pending){pending=false; load();}}
  }
  ['teamSelect','phaseSelect','applianceSelect','rangeSelect'].forEach(id=>$(id).addEventListener('change',()=>{if(id==='teamSelect'||id==='phaseSelect') $('applianceSelect').value='all';load();}));
  $('refreshAnalytics').addEventListener('click',load);
  setInterval(()=>{if(!document.hidden && $('autoRefreshToggle').checked) load();},60000);
  load();
}
