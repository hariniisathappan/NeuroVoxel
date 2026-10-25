// NeuroVoxel Case Explorer front end. No data is invented here: every number comes from /api/*.
import {Niivue} from '/static/vendor/niivue.js';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = t => String(t ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const SEQ = {t1: 'T1', t1ce: 'T1c', t2: 'T2', flair: 'FLAIR'};
const SEQC = {t1: '#38bdf8', t1ce: '#fbbf24', t2: '#a78bfa', flair: '#34d399', mask: '#94a3b8'};
const REG = {whole_tumour: 'Whole tumour', tumour_core: 'Tumour core', enhancing_tumour: 'Enhancing tumour'};
const REGC = {whole_tumour: '#22d3ee', tumour_core: '#f472b6', enhancing_tumour: '#fb923c'};
const DOM = {edema: 'Edema', net: 'Non-enhancing', enhancing: 'Enhancing'};
const FAMILIES = ['firstorder', 'glcm', 'glrlm', 'glszm', 'ngtdm', 'gldm'];
const KEY_FEATURES = ['firstorder_Mean', 'firstorder_Median', 'firstorder_10Percentile', 'firstorder_90Percentile', 'firstorder_InterquartileRange',
  'firstorder_Entropy', 'firstorder_Skewness', 'firstorder_Kurtosis', 'glcm_Contrast', 'glcm_Correlation', 'glcm_JointEntropy', 'glrlm_RunEntropy',
  'glszm_ZoneEntropy', 'ngtdm_Coarseness', 'gldm_DependenceEntropy'];
let CHART_TEXT = '#94a3b8', CHART_GRID = '#33415555';
const state = {filters: {}, offset: 0, limit: 25, sort: 'case_id', desc: false, selected: new Set(), summary: null, case: null, res: null, dist: null, charts: {}};

// The existing dark palette remains the default; the alternate palette changes presentation only.
function setTheme(mode, persist = true) {
  const light = mode === 'light';
  const root = document.documentElement;
  if (light) root.dataset.theme = 'light'; else delete root.dataset.theme;
  if (persist) { try { localStorage.setItem('neurovoxel-theme', light ? 'light' : 'dark'); } catch {} }
  const toggle = $('#theme-toggle');
  if (toggle) {
    toggle.textContent = light ? '☾' : '☀';
    toggle.setAttribute('aria-label', light ? 'Switch to dark mode' : 'Switch to light mode');
    toggle.title = light ? 'Switch to dark mode' : 'Switch to light mode';
  }
  CHART_TEXT = light ? '#53677f' : '#94a3b8';
  CHART_GRID = light ? '#cbd8e6' : '#33415555';
  if (typeof Chart !== 'undefined') {
    for (const instance of Object.values(Chart.instances || {})) {
      for (const scale of Object.values(instance.options.scales || {})) {
        if (scale.ticks) scale.ticks.color = CHART_TEXT;
        if (scale.grid) scale.grid.color = CHART_GRID;
        if (scale.title) scale.title.color = CHART_TEXT;
        if (scale.pointLabels) scale.pointLabels.color = CHART_TEXT;
        if (scale.angleLines) scale.angleLines.color = light ? '#cbd8e6' : '#33415588';
        if (scale.ticks && 'backdropColor' in scale.ticks) scale.ticks.backdropColor = 'transparent';
      }
      const legend = instance.options.plugins?.legend?.labels;
      if (legend) legend.color = CHART_TEXT;
      const tooltip = instance.options.plugins?.tooltip;
      if (tooltip && light) Object.assign(tooltip, {backgroundColor:'#fff', titleColor:'#17283d', bodyColor:'#263b52', borderColor:'#b9cbdc', borderWidth:1});
      else if (tooltip) for (const key of ['backgroundColor','titleColor','bodyColor','borderColor','borderWidth']) delete tooltip[key];
      instance.update('none');
    }
  }
}
$('#theme-toggle')?.addEventListener('click', () => setTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'));
setTheme(document.documentElement.dataset.theme === 'light' ? 'light' : 'dark', false);

// ---------------------------------------------------------------- helpers
async function api(path, opt = {}) {
  const init = {method: opt.method || 'GET', headers: {}};
  if (opt.body !== undefined) { init.method = opt.method || 'POST'; init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opt.body); }
  const r = await fetch(path, init);
  let data = null;
  try { data = await r.json(); } catch { /* not json */ }
  if (!r.ok) throw new Error((data && (data.detail?.message || data.detail)) || `Request failed (${r.status})`);
  return data;
}
function toast(msg, err = false) {
  const t = $('#toast'); t.textContent = msg; t.className = 'toast' + (err ? ' err' : ''); t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => t.hidden = true, err ? 9000 : 4500);
}
const fmt = v => {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const a = Math.abs(v);
  if (a !== 0 && a < 0.001) return v.toExponential(2);
  if (a >= 10000) return v.toLocaleString(undefined, {maximumFractionDigits: 0});
  if (a >= 100) return v.toFixed(1);
  return v.toFixed(4);
};
const pretty = s => String(s).replace(/([a-z])([A-Z])/g, '$1 $2').replace(/_/g, ' ');
const unitFor = n => /^shape_(MeshVolume|VoxelVolume)$/.test(n) ? 'mm³' : n === 'shape_SurfaceArea' ? 'mm²' : /^shape_(MajorAxisLength|MinorAxisLength|LeastAxisLength|Maximum3DDiameter|Maximum2D.*|MaximumDiameter)$/.test(n) ? 'mm' : '';
const sleep = ms => new Promise(r => setTimeout(r, ms));
function chart(id, cfg) {
  state.charts[id]?.destroy();
  const el = $('#' + id); if (!el) return null;
  cfg.options = Object.assign({responsive: true, maintainAspectRatio: false, animation: {duration: 700}}, cfg.options || {});
  return state.charts[id] = new Chart(el, cfg);
}
const axis = (extra = {}) => ({ticks: {color: CHART_TEXT, maxTicksLimit: 9}, grid: {color: CHART_GRID}, ...extra});
async function pollJob(url, onTick, every = 1200) {
  for (;;) {
    const j = await api(url);
    onTick?.(j);
    if (j.state !== 'running') return j;
    await sleep(every);
  }
}
function busy(title, msg = '', p = null) {
  $('#busy').hidden = false; $('#busy-title').textContent = title; $('#busy-msg').textContent = msg;
  $('#busy-bar').style.width = p === null ? '8%' : `${Math.round(p * 100)}%`;
}
const unbusy = () => $('#busy').hidden = true;

// ---------------------------------------------------------------- router
const VIEWS = ['home', 'explorer', 'upload', 'case', 'patients', 'cohort', 'data'];
function show(view, push = true) {
  if (!VIEWS.includes(view)) view = 'home';
  VIEWS.forEach(v => $('#view-' + v).hidden = v !== view);
  $$('.nav a').forEach(a => a.classList.toggle('active', a.dataset.view === view));
  if (push && location.hash !== '#' + view) history.replaceState(null, '', '#' + view);
  window.scrollTo({top: 0, behavior: 'smooth'});
  if (view === 'home') loadHome();
  if (view === 'explorer') loadCases();
  if (view === 'upload') loadRecentUploads();
  if (view === 'patients') initPatients();
  if (view === 'cohort') initCohort();
  if (view === 'data') loadData();
}
$$('[data-view]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); show(a.dataset.view); }));
$('#go-explorer').onclick = () => show('explorer');
$('#go-cohort').onclick = () => show('cohort');

// ---------------------------------------------------------------- Pipeline A: user-provided MRI + mandatory segmentation
let latestUploadId = localStorage.getItem('neurovoxel-latest-upload') || null;
const uploadNames = {t1: 'T1', t1ce: 'T1c', t2: 'T2', flair: 'FLAIR'};
$$('[data-upload-seq]').forEach(input => {
  const box = input.closest('.upload-drop');
  input.addEventListener('change', () => { box.classList.toggle('has-file', !!input.files.length); box.dataset.filename = input.files[0]?.name || ''; });
  box.addEventListener('dragover', e => { e.preventDefault(); box.classList.add('dragging'); });
  box.addEventListener('dragleave', () => box.classList.remove('dragging'));
  box.addEventListener('drop', e => { e.preventDefault(); box.classList.remove('dragging'); if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; input.dispatchEvent(new Event('change')); } });
});
$('#upload-seg').addEventListener('change', e => { e.currentTarget.closest('.upload-drop').classList.toggle('has-file', !!e.currentTarget.files.length); e.currentTarget.closest('.upload-drop').dataset.filename = e.currentTarget.files[0]?.name || ''; });
$('#upload-seg').closest('.upload-drop').addEventListener('dragover', e => { e.preventDefault(); e.currentTarget.classList.add('dragging'); });
$('#upload-seg').closest('.upload-drop').addEventListener('dragleave', e => e.currentTarget.classList.remove('dragging'));
$('#upload-seg').closest('.upload-drop').addEventListener('drop', e => { e.preventDefault(); e.currentTarget.classList.remove('dragging'); if (e.dataTransfer.files.length) { $('#upload-seg').files = e.dataTransfer.files; $('#upload-seg').dispatchEvent(new Event('change')); } });
function renderUploadValidation(report) {
  const checks = (report?.checks || []).map(c => `<tr><td><span class="pill ${c.status === 'pass' ? 'ok' : c.status === 'fail' ? 'off' : ''}">${esc(c.status.toUpperCase())}</span></td><td>${esc(c.title)}</td><td>${esc(c.detail)}</td></tr>`).join('');
  return `<div class="card"><h3>Validation · ${esc(report?.status?.toUpperCase() || 'UNKNOWN')}</h3><div class="tablewrap"><table class="tbl"><thead><tr><th>Status</th><th>Check</th><th>Details</th></tr></thead><tbody>${checks}</tbody></table></div></div>`;
}
function renderUploadResults(r) {
  const entries = [];
  for (const [seq, regions] of Object.entries(r.features || {})) for (const [region, families] of Object.entries(regions)) for (const [family, vals] of Object.entries(families)) for (const [name, value] of Object.entries(vals)) entries.push({sequence: uploadNames[seq] || seq, region, family, feature: name, value});
  for (const [region, vals] of Object.entries(r.shape || {})) for (const [name, value] of Object.entries(vals)) entries.push({sequence: 'Mask', region, family: 'shape', feature: name, value});
  const rows = entries.map(x => `<tr><td>${esc(x.sequence)}</td><td>${esc(x.region.replaceAll('_',' '))}</td><td>${esc(x.family)}</td><td>${esc(x.feature)}</td><td>${esc(fmt(Number(x.value)))}</td></tr>`).join('');
  const regions = Object.entries(r.regions).map(([name, v]) => `<tr><td>${esc(name.replaceAll('_',' '))}</td><td>${v.voxels.toLocaleString()}</td><td>${fmt(v.volume_mm3)} mm³</td><td>${v.available ? 'Analyzed' : 'Skipped (too small)'}</td></tr>`).join('');
  const errors = Object.entries(r.errors || {});
  const errorNote = errors.length ? `<p class="note err">Some sequence/region calculations failed: ${errors.map(([k,v])=>`${esc(k)}: ${esc(v)}`).join('; ')}</p>` : '';
  return `<div class="card"><h3>Radiomics results</h3>${errorNote}<p class="mut">Tumour centroid: voxel (z, y, x) ${r.centroid_voxel_zyx.map(fmt).join(', ')}; physical (x, y, z) ${r.centroid_world_xyz_mm.map(fmt).join(', ')} mm. Computed ${entries.length} feature values from ${r.sequences.map(s=>uploadNames[s]).join(', ')}.</p><h3>Sub-region volumes</h3><div class="tablewrap"><table class="tbl"><thead><tr><th>Region</th><th>Voxels</th><th>Volume</th><th>Radiomics status</th></tr></thead><tbody>${regions}</tbody></table></div><div class="row"><button class="button primary" id="upload-similar">FIND SIMILAR REFERENCE CASES</button><button class="button" id="upload-delete">DELETE THIS UPLOAD</button></div><p class="mut small">Similarity uses numeric features and is not a diagnosis. It is most meaningful for skull-stripped, BraTS-like data. Reference radiomics are calculated for shortlisted cases when needed.</p><div id="upload-similar-output"></div><h3>All features</h3><div class="tablewrap upload-features"><table class="tbl"><thead><tr><th>Sequence</th><th>Region</th><th>Family</th><th>Feature</th><th>Value</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}
function renderUploadedFeatureRows(r) {
  const lines = [];
  for (const [seq, regions] of Object.entries(r.features || {})) for (const [region, families] of Object.entries(regions)) for (const [family, vals] of Object.entries(families)) for (const [feature, value] of Object.entries(vals)) lines.push([uploadNames[seq] || seq, region.replaceAll('_',' '), family, feature, value]);
  for (const [region, vals] of Object.entries(r.shape || {})) for (const [feature, value] of Object.entries(vals)) lines.push(['Mask', region.replaceAll('_',' '), 'shape', feature, value]);
  return `<div class="tablewrap upload-features"><table class="tbl"><thead><tr><th>Sequence</th><th>Region</th><th>Family</th><th>Feature</th><th>Value</th></tr></thead><tbody>${lines.map(x=>`<tr>${x.map((v,i)=>`<td>${i===4?fmt(Number(v)):esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
async function loadRecentUploads() {
  const host = $('#upload-recent-list'); if (!host) return;
  try {
    const d = await api('/api/upload/recent');
    if (!d.uploads.length) { host.textContent = 'No saved uploads yet.'; return; }
    host.innerHTML = d.uploads.map(u => `<div class="row between upload-recent-row"><span><b>${esc(u.upload_id)}</b> · ${esc(u.sequences.map(s=>uploadNames[s]||s).join(', '))} · ${fmt(u.volume_ml)} mL · ${u.voxels.toLocaleString()} voxels <span class="mut">${new Date(u.created_at).toLocaleString()}</span></span><span class="row"><button class="button sm primary" data-reopen-upload="${esc(u.upload_id)}">OPEN RESULTS</button><button class="button sm danger" data-remove-upload="${esc(u.upload_id)}">DELETE</button></span></div>`).join('');
    $$('[data-reopen-upload]', host).forEach(b => b.onclick = async () => {
      b.disabled = true;
      try { await openUploadedCase(await api(`/api/upload/${b.dataset.reopenUpload}`)); }
      catch (e) { toast(e.message, true); }
      finally { b.disabled = false; }
    });
    $$('[data-remove-upload]', host).forEach(b => b.onclick = async () => {
      try {
        await api(`/api/upload/${b.dataset.removeUpload}`, {method: 'DELETE'});
        if (latestUploadId === b.dataset.removeUpload) { latestUploadId = null; localStorage.removeItem('neurovoxel-latest-upload'); }
        await loadRecentUploads();
      } catch (e) { toast(e.message, true); }
    });
  } catch (e) { host.textContent = `Could not load saved uploads: ${e.message}`; }
}
async function openUploadedCase(r) {
  const id = r.upload_id, seqs = r.sequences, whole = r.regions.whole_tumour;
  latestUploadId = id; localStorage.setItem('neurovoxel-latest-upload', id); state.case = null; $('#nav-case').hidden = false; show('case');
  $('#case-title').textContent = 'Uploaded MRI case'; $('#case-eyebrow').textContent = 'PIPELINE A · VALIDATED UPLOAD';
  $('#case-sub').textContent = `${seqs.map(s=>uploadNames[s]).join(' · ')} · ${whole.voxels.toLocaleString()} tumour voxels · ${fmt(whole.volume_mm3 / 1000)} mL · validation ${r.validation.status.toUpperCase()}`;
  $('#case-actions').innerHTML = `<div class="row between"><div><span class="badge done">PYRADIOMICS CALCULATED</span><span class="mut small">Uploaded case</span></div><button class="button danger" id="upload-case-delete">DELETE THIS UPLOAD</button></div>`;
  $('#upload-case-delete').onclick = async () => { try { await api(`/api/upload/${id}`, {method:'DELETE'}); latestUploadId = null; localStorage.removeItem('neurovoxel-latest-upload'); $('#case-viz').innerHTML = ''; $('#nva-host').innerHTML = ''; $('#case-results').hidden = true; show('upload'); $('#upload-output').innerHTML = ''; $('#upload-status').textContent = 'Uploaded files and results deleted.'; await loadRecentUploads(); } catch(e) { toast(e.message, true); } };
  const reg = r.regions, c = {case_id:id, calc_status:'done', vol_whole_ml:reg.whole_tumour.volume_mm3/1000, vol_core_ml:reg.tumour_core.volume_mm3/1000,
    vol_enh_ml:reg.enhancing_tumour.volume_mm3/1000, vol_edema_ml:(r.label_volumes?.edema || 0)/1000, vol_net_ml:(r.label_volumes?.net || 0)/1000,
    enh_pct:0, edema_pct:0, net_pct:0};
  c.enh_pct = c.vol_whole_ml ? c.vol_enh_ml / c.vol_whole_ml * 100 : 0; c.edema_pct = c.vol_whole_ml ? c.vol_edema_ml / c.vol_whole_ml * 100 : 0; c.net_pct = c.vol_whole_ml ? c.vol_net_ml / c.vol_whole_ml * 100 : 0;
  renderTiles(c); state.uploadCase = r;
  loadViewer(id, seqs.includes('t1ce') ? 't1ce' : seqs[0], seqs, true);
  window.NeuroVoxelAdvanced?.load(id, seqs);
  await drawCaseViz(c, true, id);
  $('#case-viz').insertAdjacentHTML('beforeend', `<div class="card upload-case-match"><div class="row between"><div><h3>Compare with the reference cohort</h3><p class="mut small">Find the five closest calculated MSD cases using this upload’s selected sequences and shared radiomics features.</p></div><button class="button primary" id="upload-case-similar">FIND SIMILAR CASES</button></div><div id="upload-case-similar-output"></div></div>`);
  $('#upload-case-similar').onclick = async () => {
    const btn=$('#upload-case-similar'), out=$('#upload-case-similar-output'); btn.disabled=true; out.innerHTML='<p class="mut">Shortlisting reference cases and calculating missing radiomics. This can take several minutes the first time.</p>';
    try {
      const d=await api(`/api/upload/${id}/similar`,{method:'POST'});
      out.innerHTML=`<h4>Top similar reference cases</h4><p class="mut small">${esc(d.basis)}</p><div class="tablewrap"><table class="tbl"><thead><tr><th>Rank</th><th>Reference case</th><th>Numeric similarity</th><th>Volume</th><th>Closest shared features</th></tr></thead><tbody>${d.cases.map((x,i)=>`<tr><td>${i+1}</td><td>${esc(x.case_id)}</td><td>${fmt(x.similarity_percent)}%</td><td>${fmt(x.volume_ml)} mL</td><td><details><summary>Why ranked here</summary><p class="mut small">Compared ${x.shared_feature_values} matching feature values. Similarity by sequence: ${Object.entries(x.by_sequence_similarity||{}).map(([s,v])=>`${uploadNames[s]||s} ${fmt(v)}%`).join(' · ')}.</p><div class="tablewrap"><table class="tbl"><thead><tr><th>Sequence</th><th>Region</th><th>Feature</th><th>Uploaded</th><th>Reference</th><th>Relative difference</th></tr></thead><tbody>${x.closest_features.map(f=>`<tr><td>${esc(uploadNames[f.sequence]||f.sequence)}</td><td>${esc(f.region.replaceAll('_',' '))}</td><td>${esc(f.feature)}</td><td>${fmt(f.uploaded_value)}</td><td>${fmt(f.reference_value)}</td><td>${fmt(f.relative_difference_pct)}%</td></tr>`).join('')}</tbody></table></div></details></td></tr>`).join('')}</tbody></table></div>`;
    } catch(e) { out.textContent=e.message; } finally { btn.disabled=false; }
  };
  $('#case-results').hidden = false;
  $('#case-tabs').innerHTML = '<button class="tab on" data-up-tab="features">PyRadiomics features</button><button class="tab" data-up-tab="validation">Validation</button>';
  const body = $('#case-tab-body'); body.innerHTML = renderUploadedFeatureRows(r);
  $$('#case-tabs [data-up-tab]').forEach(b=>b.onclick=()=>{ $$('#case-tabs [data-up-tab]').forEach(x=>x.classList.toggle('on',x===b)); body.innerHTML = b.dataset.upTab === 'features' ? renderUploadedFeatureRows(r) : renderUploadValidation(r.validation); });
}
$('#upload-analyze').onclick = async () => {
  const selected = $$('[data-upload-seq]').filter(i => i.files.length);
  const seg = $('#upload-seg').files[0];
  if (!selected.length || !seg) { toast('Choose at least one MRI sequence and the matching segmentation.', true); return; }
  let mapping; try { mapping = JSON.parse($('#upload-label-map').value); } catch { toast('Label mapping must be valid JSON.', true); return; }
  const form = new FormData(); form.append('slots', JSON.stringify(selected.map(i => i.dataset.uploadSeq))); form.append('label_mapping', JSON.stringify(mapping));
  selected.forEach(i => form.append('files', i.files[0])); form.append('files', seg);
  const button = $('#upload-analyze'), status = $('#upload-status'); button.disabled = true; status.textContent = 'Uploading and validating…'; $('#upload-output').innerHTML = '';
  try {
    const response = await fetch('/api/upload/analyze', {method: 'POST', body: form}); const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    $('#upload-output').innerHTML = renderUploadValidation(data.validation) + (data.results ? renderUploadResults(data.results) : '<div class="card"><b>Analysis blocked.</b> Resolve every failed validation check and upload again.</div>');
    latestUploadId = data.results?.upload_id || null; status.textContent = data.results ? 'Validation passed. Features calculated.' : 'Validation failed; nothing was calculated.';
    if (data.results) {
      $('#upload-similar').onclick = async () => {
        const out=$('#upload-similar-output'), b=$('#upload-similar'); b.disabled=true; out.innerHTML='<p class="mut">Shortlisting reference cases and calculating missing radiomics…</p>';
        try {
          const d=await api(`/api/upload/${latestUploadId}/similar`,{method:'POST'});
          out.innerHTML=`<h3>Top similar reference cases</h3><p class="mut small">${esc(d.basis)}</p>${d.cases.length?`<div class="chart"><canvas id="upload-match-chart"></canvas></div><div class="tablewrap"><table class="tbl"><thead><tr><th>Case</th><th>Numeric similarity</th><th>Volume (mL)</th><th>Dominant region</th><th>Hemisphere</th></tr></thead><tbody>${d.cases.map(c=>`<tr><td>${esc(c.case_id)}</td><td>${fmt(c.similarity_percent)}%</td><td>${fmt(c.volume_ml)}</td><td>${esc(c.dominant||'—')}</td><td>${esc(c.hemisphere||'—')}</td></tr>`).join('')}</tbody></table></div><h4>What supports the ranking</h4>${d.cases.map(c=>`<details><summary>${esc(c.case_id)} · ${fmt(c.similarity_percent)}% overall</summary><p class="mut small">Compared ${c.shared_feature_values} shared feature values. Similarity by sequence: ${Object.entries(c.by_sequence_similarity||{}).map(([s,v])=>`${uploadNames[s]||s} ${fmt(v)}%`).join(' · ')}. Closest matching values are examples; the score uses all shared features.</p><div class="tablewrap"><table class="tbl"><thead><tr><th>Sequence</th><th>Region</th><th>Feature</th><th>Uploaded</th><th>Reference</th><th>Relative difference</th></tr></thead><tbody>${c.closest_features.map(f=>`<tr><td>${esc(uploadNames[f.sequence]||f.sequence)}</td><td>${esc(f.region.replaceAll('_',' '))}</td><td>${esc(f.feature)}</td><td>${fmt(f.uploaded_value)}</td><td>${fmt(f.reference_value)}</td><td>${fmt(f.relative_difference_pct)}%</td></tr>`).join('')}</tbody></table></div></details>`).join('')}`:'<p class="note">No comparable reference cases were available.</p>'}`;
          if(d.cases.length){const seqs=[...new Set(d.cases.flatMap(c=>Object.keys(c.by_sequence_similarity||{})))];chart('upload-match-chart',{type:'bar',data:{labels:d.cases.map(c=>c.case_id),datasets:[{label:'Overall similarity',data:d.cases.map(c=>c.similarity_percent),backgroundColor:'#22d3eeaa'},...seqs.map(s=>({label:uploadNames[s]||s,data:d.cases.map(c=>c.by_sequence_similarity?.[s]??null),backgroundColor:(SEQC[s]||'#a78bfa')+'99'}))]},options:{scales:{x:axis(),y:axis({min:0,max:100,title:{display:true,text:'numeric similarity (%)',color:CHART_TEXT}})}}});}
        } catch(e) { out.textContent=e.message; } finally { b.disabled=false; }
      };
      $('#upload-delete').onclick = async () => { if (!latestUploadId) return; try { await api(`/api/upload/${latestUploadId}`, {method:'DELETE'}); latestUploadId = null; localStorage.removeItem('neurovoxel-latest-upload'); $('#upload-output').innerHTML = ''; status.textContent = 'Uploaded files and results deleted.'; await loadRecentUploads(); } catch(e) { toast(e.message, true); } };
      await loadRecentUploads();
      // The validation report is returned beside `results` by the API, so pass
      // it into the case view explicitly (the case header reads its status).
      await openUploadedCase({...data.results, validation: data.validation});
    }
  } catch(e) { status.textContent = e.message; toast(e.message, true); }
  finally { button.disabled = false; }
};

// ---------------------------------------------------------------- home
async function refreshStatus() {
  try {
    const s = await api('/api/status');
    const p = $('#db-status');
    p.textContent = `${s.cases} CASES · ${s.calculated} CALCULATED · ${s.research_engine === 'ready' ? 'ENGINE READY' : 'ENGINE MISSING'}`;
    p.className = 'pill ' + (s.research_engine === 'ready' ? 'ok' : 'off');
    return s;
  } catch { $('#db-status').textContent = 'BACKEND OFFLINE'; $('#db-status').className = 'pill off'; }
}
async function loadHome() {
  const [status, sum] = await Promise.all([refreshStatus(), api('/api/dataset/summary')]).catch(() => [null, null]);
  if (!sum) return;
  state.summary = sum;
  const warn = $('#dataset-warning');
  warn.hidden = sum.dataset_found;
  if (!sum.dataset_found) warn.innerHTML = `<b>Dataset folder not found.</b> NeuroVoxel looks for <code>dataset.json</code> in <code>${esc(status?.dataset_dir || '/data/msd')}</code>. Mount your <code>Task01_BrainTumour</code> folder there (see README, step 3) and restart.`;
  const v = sum.validation || {};
  $('#stat-cards').innerHTML = `
    <div class="card stat"><b>${sum.cases}</b><span>cases indexed</span><small>${sum.dataset || 'dataset not indexed yet'}</small></div>
    <div class="card stat"><b>${sum.calculated}</b><span>cases with PyRadiomics calculated</span><small>cohort statistics use only these</small></div>
    <div class="card stat"><b>${v.pass || 0} / ${v.warn || 0} / ${v.fail || 0}</b><span>consistency check: pass / warn / fail</span><small>quick check at index time; full check at calculation</small></div>`;
  const lm = sum.labels, ch = sum.channels;
  $('#mapping').innerHTML = lm && ch ? `
    <p><b>Labels</b> (${esc(lm.source)}):<br>${Object.entries(lm.names || {}).map(([k, n]) => `<code>${esc(k)}</code> ${esc(n)}`).join(' · ')}</p>
    <p><b>Channels</b> (${esc(ch.source)}):<br>${ch.order.map((c, i) => `<code>${i}</code> ${esc(ch.names?.[i] || c)} → ${SEQ[c]}`).join(' · ')}</p>
    <p class="small">Regions used: whole tumour = edema + non-enhancing + enhancing; tumour core = non-enhancing + enhancing; enhancing tumour = enhancing only.</p>` : 'Index the dataset to read it.';
  const st = (await api('/api/status')).settings;
  $('#settings-list').innerHTML = `
    <li>Normalisation: ${esc(st.normalisation)}</li><li>Fixed bin width ${st.bin_width} (z-score units), no resampling (1 mm isotropic data)</li>
    <li>Original image only; classes: shape, first-order, GLCM, GLRLM, GLSZM, NGTDM, GLDM</li>
    <li>Regions smaller than ${st.min_region_voxels} voxels are not analysed</li><li>Shape features computed once per region (they depend only on the mask)</li>`;
  drawHomeCharts();
  const idx = sum.index_job;
  if (idx.state === 'running') watchIndex();
  const b = sum.batch_job; if (b.state === 'running') watchBatch();
}
function watchIndex() {
  $('#index-prog').hidden = false; $('#btn-index-stop').hidden = false; $('#btn-index').disabled = $('#btn-reindex').disabled = true;
  (async () => {
    for (;;) {
      const s = await api('/api/dataset/summary');
      const j = s.index_job;
      $('#index-prog i').style.width = `${Math.round(j.progress * 100)}%`; $('#index-msg').textContent = j.message;
      if (j.state !== 'running') {
        $('#btn-index-stop').hidden = true; $('#btn-index').disabled = $('#btn-reindex').disabled = false;
        if (j.state === 'error') toast(j.error, true); else toast('Dataset indexed.');
        loadHome(); refreshStatus(); return;
      }
      await sleep(1200);
    }
  })();
}
$('#btn-index').onclick = async () => { try { await api('/api/dataset/index', {method: 'POST'}); watchIndex(); } catch (e) { toast(e.message, true); } };
$('#btn-reindex').onclick = async () => { if (!confirm('Re-read every case? Calculated results are kept.')) return; try { await api('/api/dataset/index?force=true', {method: 'POST'}); watchIndex(); } catch (e) { toast(e.message, true); } };
$('#btn-index-stop').onclick = () => api('/api/jobs/index/cancel', {method: 'POST'});

// ---------------------------------------------------------------- explorer
function readFilters() {
  const f = {q: $('#f-q').value.trim(), size: $('#f-size').value, dominant: $('#f-dominant').value, hemisphere: $('#f-hemisphere').value,
    has_enhancing: $('#f-enh').value, validation: $('#f-valid').value, calculated: $('#f-calc').value, min_whole: $('#f-minw').value, max_whole: $('#f-maxw').value};
  return Object.fromEntries(Object.entries(f).filter(([, v]) => v !== ''));
}
const qs = obj => new URLSearchParams(obj).toString();
const COLS = [['', null, 'l'], ['CASE', 'case_id', 'l'], ['WHOLE mL', 'vol_whole_ml'], ['CORE mL', 'vol_core_ml'], ['ENH mL', 'vol_enh_ml'], ['ENH %', 'enh_pct'], ['EDEMA %', 'edema_pct'],
  ['LARGEST', null], ['SIDE', null], ['CHECK', null], ['PYRADIOMICS', 'calculated_at'], ['', null]];
async function loadCases() {
  state.filters = readFilters();
  let data;
  try { data = await api('/api/cases?' + qs({...state.filters, limit: state.limit, offset: state.offset, sort: state.sort, desc: state.desc})); }
  catch (e) { toast(e.message, true); return; }
  const t = data.size_thresholds;
  $('#size-hint').textContent = t && t.medium_max ? `(≤${t.small_max.toFixed(1)} · ≤${t.medium_max.toFixed(1)} · above, mL)` : '';
  $('#result-count').textContent = `${data.total} case${data.total === 1 ? '' : 's'}`;
  $('#result-note').textContent = data.total ? '' : 'Nothing matches. Index the dataset on the Dataset page, or relax the filters.';
  $('#case-table thead').innerHTML = '<tr>' + COLS.map(([h, k, c]) => `<th class="${c || ''} ${state.sort === k ? 'sorted' : ''}" data-sort="${k || ''}">${h}${state.sort === k ? (state.desc ? ' ▼' : ' ▲') : ''}</th>`).join('') + '</tr>';
  $$('#case-table th[data-sort]').forEach(th => th.onclick = () => { const k = th.dataset.sort; if (!k) return; state.desc = state.sort === k ? !state.desc : false; state.sort = k; state.offset = 0; loadCases(); });
  $('#case-table tbody').innerHTML = data.rows.map(r => {
    const calc = r.calc_status;
    return `<tr class="${state.selected.has(r.case_id) ? 'sel' : ''}">
      <td class="l"><input type="checkbox" data-sel="${r.case_id}" ${state.selected.has(r.case_id) ? 'checked' : ''} ${calc === 'done' ? '' : 'disabled title="Calculate this case first"'}></td>
      <td class="l"><b>${esc(r.case_id)}</b></td><td>${fmt1(r.vol_whole_ml)}</td><td>${fmt1(r.vol_core_ml)}</td><td>${fmt1(r.vol_enh_ml)}</td>
      <td>${fmt1(r.enh_pct)}</td><td>${fmt1(r.edema_pct)}</td><td>${DOM[r.dominant] || '—'}</td><td>${r.hemisphere || '—'}</td>
      <td><span class="badge ${r.index_status}" title="${esc(r.index_note)}">${r.index_status.toUpperCase()}</span></td>
      <td><span class="badge ${calc}">${calc === 'none' ? 'NOT CALCULATED' : calc.toUpperCase()}</span></td>
      <td><button class="button sm" data-open="${r.case_id}">OPEN</button> ${calc === 'done' ? '' : `<button class="button sm primary" data-calc="${r.case_id}" ${r.index_status === 'fail' ? 'disabled' : ''}>CALCULATE</button>`}</td></tr>`;
  }).join('');
  $$('[data-open]').forEach(b => b.onclick = () => openCase(b.dataset.open));
  $$('[data-calc]').forEach(b => b.onclick = async () => { await calculate(b.dataset.calc); loadCases(); });
  $$('[data-sel]').forEach(c => c.onchange = () => { c.checked ? state.selected.add(c.dataset.sel) : state.selected.delete(c.dataset.sel); updateSel(); c.closest('tr').classList.toggle('sel', c.checked); });
  updateSel();
  $('#pg-info').textContent = data.total ? `${state.offset + 1}–${Math.min(state.offset + state.limit, data.total)} of ${data.total}` : '';
  $('#pg-prev').disabled = state.offset === 0; $('#pg-next').disabled = state.offset + state.limit >= data.total;
}
const fmt1 = v => v === null || v === undefined ? '—' : Number(v).toFixed(1);
function updateSel() { const n = state.selected.size; $('#btn-compare-sel').disabled = n < 2 || n > 60; $('#btn-compare-sel').textContent = `COMPARE SELECTED (${n})`; }
$('#f-apply').onclick = () => { state.offset = 0; loadCases(); };
$('#f-reset').onclick = () => { $$('.filters input,.filters select').forEach(e => { if (e.id !== 'batch-n') e.value = ''; }); state.offset = 0; loadCases(); };
$('#pg-prev').onclick = () => { state.offset = Math.max(0, state.offset - state.limit); loadCases(); };
$('#pg-next').onclick = () => { state.offset += state.limit; loadCases(); };
$('#f-q').addEventListener('keydown', e => { if (e.key === 'Enter') { state.offset = 0; loadCases(); } });
$('#btn-compare-sel').onclick = () => { state.pre = [...state.selected]; show('patients'); };

async function calculate(id) {
  busy(`Calculating ${id}`, 'Reading the case and validating…', 0.03);
  try {
    await api(`/api/cases/${id}/calculate`, {method: 'POST'});
    const j = await pollJob(`/api/cases/${id}/job/extract`, j => busy(`Calculating ${id}`, j.message, j.progress), 1000);
    if (j.state === 'error') { toast(j.error, true); return false; }
    toast(`${id}: PyRadiomics finished.`); refreshStatus(); return true;
  } catch (e) { toast(e.message, true); return false; } finally { unbusy(); }
}
async function watchBatch() {
  $('#batch-prog').hidden = false; $('#btn-batch-stop').hidden = false; $('#btn-batch').disabled = true;
  for (;;) {
    const j = (await api('/api/dataset/summary')).batch_job;
    $('#batch-prog i').style.width = `${Math.round(j.progress * 100)}%`; $('#batch-msg').textContent = j.message;
    if (j.state !== 'running') {
      $('#btn-batch-stop').hidden = true; $('#btn-batch').disabled = false;
      j.state === 'error' ? toast(j.error, true) : toast('Batch finished.'); refreshStatus(); loadCases(); return;
    }
    await sleep(2000);
  }
}
$('#btn-batch').onclick = async () => {
  try {
    const r = await api('/api/cases/calculate-batch?limit=' + (+$('#batch-n').value || 20), {method: 'POST', body: readFilters()});
    toast(`Queued ${r.queued} case${r.queued === 1 ? '' : 's'}.`); watchBatch();
  } catch (e) { toast(e.message, true); }
};
$('#btn-batch-stop').onclick = () => api('/api/jobs/batch/cancel', {method: 'POST'});

// ---------------------------------------------------------------- case view
let nv = null, cloud = null;
async function openCase(id) {
  show('case');
  $('#nav-case').hidden = false;
  $('#case-title').textContent = id; $('#case-eyebrow').textContent = 'CASE'; $('#case-sub').textContent = 'Loading…';
  const d = await api('/api/cases/' + id).catch(e => { toast(e.message, true); return null; });
  if (!d) return;
  state.case = d.case; state.res = null; state.dist = null;
  const c = d.case;
  $('#case-sub').textContent = `${c.shape || ''} voxels · ${c.spacing || ''} mm · largest sub-region: ${DOM[c.dominant] || '—'} · side: ${c.hemisphere || '—'} (approximate) · quick check: ${c.index_status.toUpperCase()}${c.index_note ? ' (' + c.index_note + ')' : ''}`;
  renderTiles(c);
  renderActions();
  loadViewer(id, 'flair');
  window.NeuroVoxelAdvanced?.load(id).then(() => { if (c.calc_status === 'done') window.NeuroVoxelAdvanced.refreshShapes(); });
  drawCaseViz(c, c.calc_status === 'done');
  if (c.calc_status === 'done') await loadResults(id); else $('#case-results').hidden = true;
}
function renderActions() {
  const c = state.case, done = c.calc_status === 'done';
  $('#case-actions').innerHTML = done
    ? `<div class="row between"><div><span class="badge done">PYRADIOMICS CALCULATED</span> <span class="mut small">${esc(c.calculated_at || '')}</span></div>
        <div class="row" style="margin:0"><button class="button" id="ca-compare">COMPARE WITH OTHER CASES</button><button class="button danger" id="ca-delete">DELETE THIS CASE'S RESULTS</button></div></div>`
    : `<div class="row between"><div><b>Not calculated yet.</b> <span class="mut">4 sequences × 3 regions, about 20–60 seconds.</span></div>
        <button class="button primary" id="ca-calc" ${c.index_status === 'fail' ? 'disabled' : ''}>CALCULATE PYRADIOMICS</button></div>`;
  $('#ca-calc')?.addEventListener('click', async () => { if (await calculate(c.case_id)) openCase(c.case_id); });
  $('#ca-compare')?.addEventListener('click', () => { state.pre = [c.case_id]; show('patients'); });
  $('#ca-delete')?.addEventListener('click', async () => {
    if (!confirm(`Delete the calculated results of ${c.case_id}? The dataset itself is not touched.`)) return;
    await api(`/api/cases/${c.case_id}/results`, {method: 'DELETE'}); toast('Results deleted.'); refreshStatus(); openCase(c.case_id);
  });
}
function renderTiles(c) {
  const t = (label, ml, pct, col) => `<div class="tile" style="--c:${col}"><small>${label}</small><b>${fmt1(ml)}</b><u>mL</u><em>${pct === null || pct === undefined ? '' : pct + '% of whole tumour'}</em></div>`;
  const parts = [['Non-enhancing / necrotic', c.net_pct, '#a78bfa'], ['Peritumoral edema', c.edema_pct, '#22d3ee'], ['Enhancing tumour', c.enh_pct, '#fb7185']];
  $('#vol-tiles').innerHTML = t('WHOLE TUMOUR', c.vol_whole_ml, null, '#22d3ee') + t('TUMOUR CORE', c.vol_core_ml, c.vol_whole_ml ? (c.vol_core_ml / c.vol_whole_ml * 100).toFixed(1) : null, '#f472b6') +
    t('ENHANCING', c.vol_enh_ml, c.enh_pct, '#fb7185') + t('EDEMA', c.vol_edema_ml, c.edema_pct, '#22d3ee') + t('NON-ENHANCING', c.vol_net_ml, c.net_pct, '#a78bfa') +
    `<div class="tile" style="--c:#94a3b8;grid-column:1/-1"><small>COMPOSITION</small><div class="stack">${parts.map(([n, p, col]) => `<i style="width:${p || 0}%;background:${col}" title="${n}">${(p || 0) > 6 ? n.split(' ')[0] + ' ' + p + '%' : ''}</i>`).join('')}</div></div>`;
}

// viewer
const SEG_CM = {red: 'Red', green: 'Green', inferno: 'Thermal'};
function styleSeg() {
  const v = nv?.volumes?.[1]; if (!v) return;
  const cm = $('#vcm').value;
  nv.setColormap(v.id, cm);
  v.cal_min = 0.5; v.cal_max = cm === 'inferno' ? 4 : 1;   // thermal maps canonical labels 1, 2 and 4 to distinct colour levels
  nv.setOpacity(1, +$('#vop').value);
  nv.updateGLVolume();
}
async function loadViewer(id, seq, available = Object.keys(SEQ), uploaded = false) {
  const ctl = $('#viewer-controls');
  ctl.innerHTML = `<div class="seg" id="vseq">${available.map(k => `<button data-s="${k}" class="${k === seq ? 'on' : ''}">${SEQ[k]}</button>`).join('')}</div>
    <div class="seg" id="vtype"><button data-t="multi" class="on">AXIAL · COR · SAG</button><button data-t="render">3D</button></div>
    <label style="margin:0;display:flex;gap:8px;align-items:center">SEGMENTATION
      <select id="vcm">${Object.entries(SEG_CM).map(([k, n]) => `<option value="${k}">${n}</option>`).join('')}</select>
      <input type="range" id="vop" min="0" max="1" step=".05" value=".7"></label>`;
  if (uploaded) loadViewer.cm = 'inferno';
  $('#vcm').value = loadViewer.cm || 'red';
  $('#vcm').onchange = e => { loadViewer.cm = e.target.value; styleSeg(); };
  $$('#vseq button').forEach(b => b.onclick = () => { $$('#vseq button').forEach(x => x.classList.toggle('on', x === b)); loadViewer(id, b.dataset.s, available, uploaded); });
  $$('#vtype button').forEach(b => b.onclick = () => { $$('#vtype button').forEach(x => x.classList.toggle('on', x === b)); if (nv) nv.setSliceType(b.dataset.t === 'render' ? nv.sliceTypeRender : nv.sliceTypeMultiplanar); });
  $('#vop').oninput = e => { if (nv?.volumes?.[1]) nv.setOpacity(1, +e.target.value); };
  try {
    if (!nv) { nv = new Niivue({isResizeCanvas: true, isColorbar: false, backColor: [0, 0, 0, 1], show3Dcrosshair: true}); await nv.attachToCanvas($('#nv-canvas')); nv.opts.dragAndDropEnabled = false; }
    const prefix = uploaded ? `/api/upload/${id}/volume` : `/api/cases/${id}/volume`;
    const sourceName = uploaded ? state.uploadCase?.sequence_files?.[seq] : null;
    await nv.loadVolumes([{url: `${prefix}/${seq}`, name: sourceName || `${seq}.nii.gz`, colormap: 'gray'},
      {url: `${prefix}/seg`, name: 'seg.nii.gz', colormap: 'red', opacity: +$('#vop').value}]);
    styleSeg();
    try {                                   // put the crosshair on the tumour centre (world mm from the image affine)
      const g = await api(`/api/adv/subregions?uuid=${id}&seq=${seq}&groups=`);
      const mm = g.spatial?.regions?.all?.centroid_ras_mm;
      if (mm) { nv.scene.crosshairPos = nv.mm2frac(mm, 0, true); nv.updateGLVolume(); }
    } catch { /* keep the default slice */ }
    nv.setSliceType($('#vtype .on').dataset.t === 'render' ? nv.sliceTypeRender : nv.sliceTypeMultiplanar);
  } catch (e) { toast('Viewer: ' + e.message, true); }
}

// results
const TABS = [['seq', 'Across sequences'], ['reg', 'Across sub-regions'], ['pct', 'Among calculated cases'], ['sens', 'Boundary sensitivity'], ['val', 'Validation'], ['ver', 'Verification'], ['exp', 'Export']];
async function loadResults(id) {
  const [res, dist] = await Promise.all([api(`/api/cases/${id}/results`), api(`/api/cases/${id}/distribution`).catch(() => null)]);
  state.res = res; state.dist = dist; state.tab = state.tab || 'seq';
  $('#case-results').hidden = false;
  $('#case-tabs').innerHTML = TABS.map(([k, n]) => `<button class="tab ${k === state.tab ? 'on' : ''}" data-t="${k}">${n}</button>`).join('');
  $$('#case-tabs .tab').forEach(b => b.onclick = () => { state.tab = b.dataset.t; $$('#case-tabs .tab').forEach(x => x.classList.toggle('on', x === b)); renderTab(); });
  renderTab();
}
function renderTab() { ({seq: tabSeq, reg: tabReg, pct: tabPct, sens: tabSens, val: tabVal, ver: tabVer, exp: tabExp})[state.tab](); }
const availRegions = () => Object.keys(REG).filter(k => state.res.regions[k]?.included);
const fv = (seq, region, key) => { const [f, ...n] = key.split('_'); return state.res.features?.[seq]?.[region]?.[f]?.[n.join('_')]; };
const shapeVal = (region, key) => state.res.shape?.[region]?.[key.replace(/^shape_/, '')];
function featureKeys(region, fam) {
  const set = new Set();
  for (const s of Object.keys(SEQ)) for (const [f, vals] of Object.entries(state.res.features?.[s]?.[region] || {})) if (fam === 'all' || fam === f) for (const n of Object.keys(vals)) set.add(`${f}_${n}`);
  return [...set].sort();
}

function tabSeq() {
  const regs = availRegions(); state.sRegion = regs.includes(state.sRegion) ? state.sRegion : regs[0]; state.sFam = state.sFam || 'all'; state.sFeat = state.sFeat || 'firstorder_Mean';
  const body = $('#case-tab-body');
  body.innerHTML = `<div class="grid2"><div class="card"><div class="row" style="margin-top:0"><label style="margin:0">Region <select id="s-region">${regs.map(r => `<option value="${r}">${REG[r]}</option>`).join('')}</select></label></div>
      <div class="fam" id="s-fam">${['all', ...FAMILIES, 'shape'].map(f => `<button data-f="${f}" class="${f === state.sFam ? 'on' : ''}">${f.toUpperCase()}</button>`).join('')}</div>
      <div class="tablewrap" style="max-height:480px"><table class="tbl cmp"><thead></thead><tbody id="s-body"></tbody></table></div>
      <p class="mut small">Click a row to chart it. Values are from z-scored images (unit-free) except shape features (mm, mm², mm³). Shape depends only on the mask, so it is the same for every sequence.</p></div>
    <div class="card"><h3 id="s-title"></h3><div class="chart"><canvas id="s-chart"></canvas></div>
      <h3 style="margin-top:18px">Intensity distribution inside the region</h3><div class="chart"><canvas id="s-dist"></canvas></div>
      <p class="mut small">Percentage of region voxels per z-score bin, per sequence — where sequences differ visibly, the tumour looks different on that contrast.</p></div></div>`;
  $('#s-region').value = state.sRegion;
  $('#s-region').onchange = e => { state.sRegion = e.target.value; tabSeq(); };
  $$('#s-fam button').forEach(b => b.onclick = () => { state.sFam = b.dataset.f; tabSeq(); });
  const shapeMode = state.sFam === 'shape';
  $('#case-tab-body thead').innerHTML = shapeMode ? '<tr><th class="l">FEATURE</th><th>VALUE</th><th>UNIT</th></tr>' : `<tr><th class="l">FEATURE</th>${Object.values(SEQ).map(n => `<th>${n}</th>`).join('')}<th>SPREAD</th></tr>`;
  const keys = shapeMode ? Object.keys(state.res.shape?.[state.sRegion] || {}).map(k => 'shape_' + k).sort() : featureKeys(state.sRegion, state.sFam);
  $('#s-body').innerHTML = keys.map(k => {
    if (shapeMode) return `<tr data-k="${k}"><td class="l">${pretty(k.slice(6))}</td><td>${fmt(shapeVal(state.sRegion, k))}</td><td>${unitFor(k)}</td></tr>`;
    const v = Object.keys(SEQ).map(s => fv(s, state.sRegion, k)), got = v.filter(x => x !== undefined);
    return `<tr data-k="${k}" class="${k === state.sFeat ? 'pick' : ''}"><td class="l">${pretty(k)}</td>${v.map(x => `<td class="v">${fmt(x)}</td>`).join('')}<td>${got.length ? fmt(Math.max(...got) - Math.min(...got)) : '—'}</td></tr>`;
  }).join('');
  $$('#s-body tr').forEach(tr => tr.onclick = () => { if (shapeMode) return; state.sFeat = tr.dataset.k; $$('#s-body tr').forEach(x => x.classList.toggle('pick', x === tr)); drawSeqChart(); });
  if (!shapeMode) { if (!keys.includes(state.sFeat)) state.sFeat = keys[0]; drawSeqChart(); } else $('#s-title').textContent = 'Shape (mask only)';
  drawDist();
}
function drawSeqChart() {
  const k = state.sFeat, r = state.sRegion; if (!k) return;
  $('#s-title').textContent = `${pretty(k)} · ${REG[r]}`;
  chart('s-chart', {type: 'bar', data: {labels: Object.values(SEQ), datasets: [{data: Object.keys(SEQ).map(s => fv(s, r, k) ?? null), backgroundColor: Object.keys(SEQ).map(s => SEQC[s] + 'cc'), borderColor: Object.keys(SEQ).map(s => SEQC[s]), borderWidth: 1, borderRadius: 6}]},
    options: {plugins: {legend: {display: false}}, scales: {x: axis(), y: axis()}}});
}
function drawDist() {
  const d = state.dist?.[state.sRegion]; if (!d) return;
  const first = d.t1 || d.flair, edges = first.z.edges, labels = edges.slice(0, -1).map((e, i) => ((e + edges[i + 1]) / 2).toFixed(2));
  chart('s-dist', {type: 'line', data: {labels, datasets: Object.keys(SEQ).filter(s => d[s]).map(s => ({label: SEQ[s], data: d[s].z.pct, borderColor: SEQC[s], backgroundColor: SEQC[s] + '22', pointRadius: 0, tension: .35, fill: true}))},
    options: {plugins: {legend: {labels: {color: CHART_TEXT, boxWidth: 10}}}, scales: {x: axis({title: {display: true, text: 'z-score', color: CHART_TEXT}}), y: axis({title: {display: true, text: '% of voxels', color: CHART_TEXT}})}}});
}

function tabReg() {
  const seqs = Object.keys(SEQ); state.rSeq = state.rSeq || 'flair'; state.rFam = state.rFam || 'all'; state.rFeat = state.rFeat || 'firstorder_Mean';
  const regs = availRegions(), res = state.res;
  $('#case-tab-body').innerHTML = `<div class="grid2"><div class="card"><div class="row" style="margin-top:0"><label style="margin:0">Sequence <select id="r-seq">${seqs.map(s => `<option value="${s}">${SEQ[s]}</option>`).join('')}</select></label></div>
      <div class="fam" id="r-fam">${['all', ...FAMILIES].map(f => `<button data-f="${f}" class="${f === state.rFam ? 'on' : ''}">${f.toUpperCase()}</button>`).join('')}</div>
      <div class="tablewrap" style="max-height:480px"><table class="tbl cmp"><thead><tr><th class="l">FEATURE</th>${regs.map(r => `<th>${REG[r].toUpperCase()}</th>`).join('')}</tr></thead><tbody id="r-body"></tbody></table></div>
      <p class="mut small">Regions overlap on purpose: the tumour core lies inside the whole tumour, and the enhancing tumour inside the core. ${regs.length < 3 ? '<b>Missing regions were too small to analyse (fewer than ' + 64 + ' voxels).</b>' : ''}</p></div>
    <div class="card"><h3 id="r-title"></h3><div class="chart"><canvas id="r-chart"></canvas></div>
      <h3 style="margin-top:18px">Region sizes</h3><table class="tbl"><thead><tr><th class="l">REGION</th><th>VOXELS</th><th>VOLUME mL</th></tr></thead>
      <tbody>${Object.entries(res.regions).map(([r, v]) => `<tr><td class="l">${REG[r]}</td><td>${v.voxels.toLocaleString()}</td><td>${v.volume_ml.toFixed(2)}</td></tr>`).join('')}</tbody></table></div></div>`;
  $('#r-seq').value = state.rSeq; $('#r-seq').onchange = e => { state.rSeq = e.target.value; tabReg(); };
  $$('#r-fam button').forEach(b => b.onclick = () => { state.rFam = b.dataset.f; tabReg(); });
  const keys = new Set();
  for (const r of regs) for (const [f, vals] of Object.entries(res.features?.[state.rSeq]?.[r] || {})) if (state.rFam === 'all' || state.rFam === f) for (const n of Object.keys(vals)) keys.add(`${f}_${n}`);
  const list = [...keys].sort();
  $('#r-body').innerHTML = list.map(k => `<tr data-k="${k}" class="${k === state.rFeat ? 'pick' : ''}"><td class="l">${pretty(k)}</td>${regs.map(r => `<td class="v">${fmt(fv(state.rSeq, r, k))}</td>`).join('')}</tr>`).join('');
  const draw = () => {
    if (!list.includes(state.rFeat)) state.rFeat = list[0];
    $('#r-title').textContent = `${pretty(state.rFeat)} · ${SEQ[state.rSeq]}`;
    chart('r-chart', {type: 'bar', data: {labels: regs.map(r => REG[r]), datasets: [{data: regs.map(r => fv(state.rSeq, r, state.rFeat) ?? null), backgroundColor: regs.map(r => REGC[r] + 'cc'), borderColor: regs.map(r => REGC[r]), borderWidth: 1, borderRadius: 6}]},
      options: {plugins: {legend: {display: false}}, scales: {x: axis(), y: axis()}}});
  };
  $$('#r-body tr').forEach(tr => tr.onclick = () => { state.rFeat = tr.dataset.k; $$('#r-body tr').forEach(x => x.classList.toggle('pick', x === tr)); draw(); });
  draw();
}

async function tabPct() {
  const regs = availRegions(); state.pRegion = regs.includes(state.pRegion) ? state.pRegion : regs[0];
  const body = $('#case-tab-body');
  body.innerHTML = `<div class="card"><div class="row" style="margin-top:0"><label style="margin:0">Region <select id="p-region">${regs.map(r => `<option value="${r}">${REG[r]}</option>`).join('')}</select></label></div><div id="p-out" class="mut">Loading…</div></div>`;
  $('#p-region').value = state.pRegion; $('#p-region').onchange = e => { state.pRegion = e.target.value; tabPct(); };
  const d = await api('/api/cohort/percentiles', {body: {case_id: state.case.case_id, region: state.pRegion}}).catch(e => ({note: e.message, values: {}}));
  const seqs = Object.keys(SEQ);
  if (!Object.keys(d.values).length) { $('#p-out').innerHTML = `<div class="note">${esc(d.note)} Calculate more cases (Explorer → “Calculate matching”) to get percentiles.</div>`; return; }
  const col = p => `hsl(${190 - p * 1.6},70%,${22 + Math.abs(p - 50) * .18}%)`;
  $('#p-out').innerHTML = `<p class="mut">${esc(d.note)} A percentile of 90 means 90% of the calculated cases have a lower value for that feature.</p>
    <table class="tbl"><thead><tr><th class="l">FEATURE</th>${seqs.map(s => `<th>${SEQ[s]}</th>`).join('')}</tr></thead><tbody>
    ${KEY_FEATURES.map(k => `<tr><td class="l">${pretty(k)}</td>${seqs.map(s => { const c = d.values[s]?.[k]; return c ? `<td style="background:${col(c.percentile)}" title="value ${fmt(c.value)} · cohort median ${fmt(c.median)} (IQR ${fmt(c.q1)} – ${fmt(c.q3)}) · n=${c.n}">${c.percentile.toFixed(0)}</td>` : '<td>—</td>'; }).join('')}</tr>`).join('')}</tbody></table>
    <p class="mut small">Hover a cell for the value and the cohort median. Percentile rank among ${d.n} calculated cases only.</p>`;
}

async function tabSens() {
  const id = state.case.case_id, body = $('#case-tab-body');
  const sens = await api(`/api/cases/${id}/sensitivity`).catch(() => null);
  if (!sens) {
    body.innerHTML = `<div class="card"><h3>Boundary sensitivity</h3><p class="mut">Erodes and dilates each region by one voxel and recalculates every feature, so you can see which values change when the boundary moves slightly.
      This describes sensitivity to small boundary changes. It is not a measure of agreement between human raters. Takes about 1–3 minutes.</p><button class="button primary" id="sens-run">RUN BOUNDARY SENSITIVITY</button><div class="prog" id="sens-prog" hidden><i></i></div><p class="mut small" id="sens-msg"></p></div>`;
    $('#sens-run').onclick = async () => {
      try { await api(`/api/cases/${id}/sensitivity/start`, {method: 'POST'}); $('#sens-prog').hidden = false;
        const j = await pollJob(`/api/cases/${id}/job/sensitivity`, j => { $('#sens-prog i').style.width = Math.round(j.progress * 100) + '%'; $('#sens-msg').textContent = j.message; }, 1500);
        if (j.state === 'error') toast(j.error, true); else tabSens(); } catch (e) { toast(e.message, true); }
    };
    return;
  }
  const rows = [];
  for (const [seq, regs] of Object.entries(sens.values)) for (const [region, perts] of Object.entries(regs)) for (const [tag, feats] of Object.entries(perts)) for (const [key, v] of Object.entries(feats)) {
    const b = seq === 'mask' ? shapeVal(region, key) : fv(seq, region, key); if (b === undefined) continue;
    rows.push({seq, region, tag, key, base: b, val: v, rel: Math.abs(b) < 1e-12 ? (Math.abs(v) < 1e-12 ? 0 : 100) : Math.abs(v - b) / Math.abs(b) * 100});
  }
  const groups = {};
  for (const r of rows) { const g = groups[`${r.seq}|${r.region}`] ||= {seq: r.seq, region: r.region, n: 0, ok: 0, rels: []}; if (!g.keys) g.keys = {}; const kk = r.key; g.keys[kk] = Math.max(g.keys[kk] || 0, r.rel); }
  const summary = Object.values(groups).map(g => { const vals = Object.values(g.keys); return {...g, n: vals.length, ok: vals.filter(v => v < 10).length, med: vals.sort((a, b) => a - b)[Math.floor(vals.length / 2)]}; }).sort((a, b) => (a.region + a.seq).localeCompare(b.region + b.seq));
  const worst = Object.values(rows.reduce((m, r) => { const k = `${r.seq}|${r.region}|${r.key}`; if (!m[k] || r.rel > m[k].rel) m[k] = r; return m; }, {})).sort((a, b) => b.rel - a.rel).slice(0, 25);
  const bad = sens.runs.filter(r => r.status !== 'ok' && r.sequence === 'mask');
  body.innerHTML = `<div class="grid2"><div class="card"><h3>Share of features that move less than 10%</h3><div class="chart"><canvas id="sn-chart"></canvas></div>
    <p class="mut small">Per sequence × region: the largest relative change over the eroded and dilated masks. 10% is a convention for this display, not a validated threshold.</p>
    ${bad.map(r => `<div class="note">${REG[r.region]} · ${r.perturbation}: ${esc(r.note)}</div>`).join('')}</div>
    <div class="card"><h3>25 most boundary-sensitive features</h3><div class="tablewrap" style="max-height:420px"><table class="tbl"><thead><tr><th class="l">FEATURE</th><th class="l">SEQ</th><th class="l">REGION</th><th>CHANGE %</th></tr></thead>
    <tbody>${worst.map(r => `<tr><td class="l">${pretty(r.key)}</td><td class="l">${SEQ[r.seq] || 'mask'}</td><td class="l">${REG[r.region]}</td><td>${r.rel.toFixed(1)}</td></tr>`).join('')}</tbody></table></div></div></div>`;
  chart('sn-chart', {type: 'bar', data: {labels: summary.map(s => `${SEQ[s.seq] || 'Shape'} · ${REG[s.region].split(' ')[0]}`), datasets: [{data: summary.map(s => +(s.ok / s.n * 100).toFixed(1)), backgroundColor: summary.map(s => (SEQC[s.seq] || '#94a3b8') + 'cc'), borderRadius: 4}]},
    options: {indexAxis: 'y', plugins: {legend: {display: false}}, scales: {x: axis({min: 0, max: 100, title: {display: true, text: '% of features', color: CHART_TEXT}}), y: axis()}}});
}

function tabVal() {
  const r = state.res.report;
  $('#case-tab-body').innerHTML = `<div class="card"><h3>Validation: <span class="badge ${r.status}">${r.status.toUpperCase()}</span> <span class="mut small">${r.summary.pass} pass · ${r.summary.warn} warn · ${r.summary.fail} fail</span></h3>
    <p class="mut small">${esc(r.wording)}</p>${r.checks.map(c => `<div class="chk ${c.status}"><b>${c.status.toUpperCase()}</b>${esc(c.title)} — <span class="mut">${esc(c.detail)}</span></div>`).join('')}
    <h3 style="margin-top:18px">Recorded settings</h3><div class="tablewrap" style="max-height:300px"><table class="tbl"><tbody>${Object.entries(state.res.provenance).map(([k, v]) => `<tr><td class="l">${esc(k)}</td><td class="l" style="white-space:normal;word-break:break-all">${esc(v)}</td></tr>`).join('')}</tbody></table></div></div>`;
}
function tabExp() {
  const id = state.case.case_id;
  $('#case-tab-body').innerHTML = `<div class="card"><h3>Download</h3><div class="row">
    <a class="button primary" href="/api/cases/${id}/export.xlsx">ALL FEATURES (.XLSX)</a><a class="button" href="/api/cases/${id}/export.csv">LONG TABLE (.CSV)</a>
    <a class="button" href="/api/cases/${id}/report.md">METHODS REPORT (.MD)</a><a class="button" href="/api/cases/${id}/download/segmentation">SEGMENTATION (.NII.GZ)</a></div>
    <p class="mut small">The workbook has one sheet per sequence, one comparison sheet per region (the four sequences side by side), the validation report and the recorded settings.</p></div>`;
}

// ---------------------------------------------------------------- patients
async function initPatients() {
  const list = await api('/api/cases?' + qs({calculated: 'yes', limit: 500, sort: 'case_id'})).catch(() => ({rows: []}));
  const pre = new Set(state.pre || []); state.pre = null;
  $('#pt-region').innerHTML = Object.entries(REG).map(([k, n]) => `<option value="${k}">${n}</option>`).join('');
  $('#pt-seq').innerHTML = Object.entries(SEQ).map(([k, n]) => `<option value="${k}">${n}</option>`).join('');
  $('#patient-pick').innerHTML = list.rows.length ? list.rows.map(r => `<label style="display:flex;gap:6px;align-items:center;margin:0;padding:6px 10px;border:1px solid var(--edge);border-radius:8px"><input type="checkbox" value="${r.case_id}" ${pre.has(r.case_id) ? 'checked' : ''}> ${r.case_id} <span class="mut">${fmt1(r.vol_whole_ml)} mL</span></label>`).join('') : '<div class="note">No case is calculated yet. Calculate at least two in the Explorer.</div>';
  $('#pt-out').innerHTML = '';
  if (pre.size >= 2) runPatients();
}
async function runPatients() {
  const ids = $$('#patient-pick input:checked').map(c => c.value);
  if (ids.length < 2 || ids.length > 60) { toast('Choose 2 to 60 calculated cases.', true); return; }
  const region = $('#pt-region').value, seq = $('#pt-seq').value;
  let d; try { d = await api('/api/compare/cases', {body: {case_ids: ids, region}}); } catch (e) { toast(e.message, true); return; }
  const palette = vizPal(ids.length), many = ids.length > 8;
  const feats = KEY_FEATURES.filter(k => ids.some(i => d.cases[i].features?.[seq] && fvCase(d.cases[i], seq, k) !== undefined));
  const tiles = ids.length <= 12 ? `<div class="tiles">${ids.map((i, n) => { const c = d.cases[i].info; return `<div class="tile" style="--c:${palette[n]}"><small>${i}</small><b>${fmt1(c.vol_whole_ml)}</b><u>mL whole tumour</u><em>enh ${fmt1(c.enh_pct)}% · largest: ${DOM[c.dominant] || '—'} · ${c.hemisphere}</em></div>`; }).join('')}</div>` : `<p class="mut small">${ids.length} patients selected.</p>`;
  $('#pt-out').innerHTML = `${tiles}
    <div class="grid2">${many ? '' : `<div class="card"><h3>Percentile among ${d.n_cohort} calculated cases · ${SEQ[seq]} · ${REG[region]}</h3><div class="chart tall"><canvas id="pt-chart"></canvas></div>
      <p class="mut small">Higher = more of the calculated cases have a lower value. This positions the patients within this set only.</p></div>`}
    <div class="card ${many ? 'span2' : ''}"><h3>Values</h3><div class="tablewrap" style="max-height:440px"><table class="tbl"><thead><tr><th class="l">FEATURE</th>${ids.map(i => `<th>${i}</th>`).join('')}</tr></thead>
      <tbody>${feats.map(k => `<tr><td class="l">${pretty(k)}</td>${ids.map(i => { const v = fvCase(d.cases[i], seq, k), p = d.cases[i].percentiles?.[seq]?.[k]?.percentile; return `<td>${fmt(v)}${p !== undefined ? ` <span class="mut">(${p.toFixed(0)})</span>` : ''}</td>`; }).join('')}</tr>`).join('')}
      ${Object.keys(d.cases[ids[0]].shape || {}).filter(k => ['MeshVolume', 'SurfaceArea', 'Sphericity', 'Elongation', 'Flatness', 'Maximum3DDiameter'].includes(k)).map(k => `<tr><td class="l">Shape · ${pretty(k)}</td>${ids.map(i => `<td>${fmt(d.cases[i].shape?.[k])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
      <p class="mut small">Brackets show the percentile. Shape is mask-only.</p></div></div>
    <div id="pt-extra"></div>`;
  if (!many) chart('pt-chart', {type: 'bar', data: {labels: feats.map(pretty), datasets: ids.map((i, n) => ({label: i, data: feats.map(k => d.cases[i].percentiles?.[seq]?.[k]?.percentile ?? null), backgroundColor: palette[n] + 'cc', borderRadius: 3}))},
    options: {indexAxis: 'y', plugins: {legend: {labels: {color: CHART_TEXT, boxWidth: 10}}}, scales: {x: axis({min: 0, max: 100, title: {display: true, text: 'percentile', color: CHART_TEXT}}), y: axis()}}});
  drawPatientViz(ids, d, seq, region);
  if (d.n_cohort < 5) toast('Fewer than 5 calculated cases: percentiles are not shown. Calculate more cases.');
}
const fvCase = (c, seq, key) => { const [f, ...n] = key.split('_'); return c.features?.[seq]?.[f]?.[n.join('_')]; };
$('#pt-run').onclick = runPatients;

// ---------------------------------------------------------------- cohort
function groupForm(el) {
  el.innerHTML = `<label>Tumour size<select data-k="size"><option value="">Any</option><option value="small">Small</option><option value="medium">Medium</option><option value="large">Large</option></select></label>
    <label>Largest sub-region<select data-k="dominant"><option value="">Any</option><option value="edema">Edema</option><option value="net">Non-enhancing</option><option value="enhancing">Enhancing</option></select></label>
    <label>Hemisphere<select data-k="hemisphere"><option value="">Any</option><option value="left">Left</option><option value="right">Right</option><option value="bilateral">Crosses midline</option></select></label>
    <label>Enhancing present<select data-k="has_enhancing"><option value="">Any</option><option value="yes">Yes</option><option value="no">No</option></select></label>
    <label>Whole tumour mL<span class="two"><input type="number" data-k="min_whole" placeholder="min"><input type="number" data-k="max_whole" placeholder="max"></span></label>`;
}
const readGroup = el => Object.fromEntries($$('[data-k]', el).filter(e => e.value !== '').map(e => [e.dataset.k, e.value]));
const setGroup = (el, v) => $$('[data-k]', el).forEach(e => e.value = v[e.dataset.k] ?? '');
const PRESETS = {size: [{size: 'large'}, {size: 'small'}], enh: [{has_enhancing: 'yes'}, {has_enhancing: 'no'}], hemi: [{hemisphere: 'left'}, {hemisphere: 'right'}], dom: [{dominant: 'edema'}, {dominant: 'net'}]};
let cohortReady = false;
async function initCohort() {
  if (!cohortReady) { groupForm($('#gA')); groupForm($('#gB')); setGroup($('#gA'), PRESETS.size[0]); setGroup($('#gB'), PRESETS.size[1]); cohortReady = true;
    $$('[data-preset]').forEach(b => b.onclick = () => { setGroup($('#gA'), PRESETS[b.dataset.preset][0]); setGroup($('#gB'), PRESETS[b.dataset.preset][1]); runCohort(); }); }
  const f = await api('/api/cohort/features').catch(() => ({features: [], n: 0}));
  $('#coh-n').textContent = f.n;
  $('#cf-region').innerHTML = Object.entries(REG).map(([k, n]) => `<option value="${k}">${n}</option>`).join('');
  const keep = $('#cf-feature').value;
  $('#cf-feature').innerHTML = f.features.map(k => `<option value="${k}">${pretty(k)}</option>`).join(''); $('#cf-feature').value = keep || 'firstorder_Mean';
  if (f.n) drawCohortFeature();
}
async function runCohort() {
  $('#coh-msg').textContent = 'Calculating…';
  let d; try { d = await api('/api/cohort/compare', {body: {group_a: readGroup($('#gA')), group_b: readGroup($('#gB'))}}); } catch (e) { toast(e.message, true); return; }
  $('#coh-msg').textContent = '';
  if (!d.ok) { $('#coh-out').innerHTML = `<div class="note">${esc(d.note)}</div>`; return; }
  const rk = d.ranking, max = Math.max(...rk.by_set.map(x => x.median_abs_delta), 0.01);
  const bars = (rows, label) => `<div class="rank">${rows.map(r => `<div class="r"><span>${label(r)}</span><div class="bar"><i style="width:${(r.median_abs_delta / max * 100).toFixed(1)}%"></i></div><span>${r.median_abs_delta.toFixed(2)}</span></div>`).join('')}</div>`;
  $('#coh-out').innerHTML = `<div class="note" style="border-color:var(--cyan);background:#22d3ee10;color:var(--text)">${esc(d.note)}</div>
    <div class="grid2"><div class="card"><h3>Which sequence separates the groups more?</h3>${bars(rk.by_sequence, r => SEQ[r.sequence])}<p class="mut small">Median |Cliff's delta| over that sequence's features (all regions).</p></div>
      <div class="card"><h3>Which region?</h3>${bars(rk.by_region, r => REG[r.region])}</div></div>
    <div class="grid2"><div class="card"><h3>Sequence × region</h3>${bars(rk.by_set.slice(0, 12), r => `${SEQ[r.sequence]} · ${REG[r.region].split(' ')[0]}`)}
      <p class="mut small">${rk.by_set[0] ? `Largest: ${SEQ[rk.by_set[0].sequence]} / ${REG[rk.by_set[0].region]} — ${rk.by_set[0].flagged} of ${rk.by_set[0].features} features flagged (FDR < 0.05 and |delta| ≥ 0.33).` : ''}</p></div>
      <div class="card"><h3>Which feature family?</h3>${bars(rk.by_family, r => r.family.toUpperCase())}
      ${rk.shape ? `<p class="mut small">Shape (mask only): median |delta| ${rk.shape.median_abs_delta}, ${rk.shape.flagged} of ${rk.shape.features} flagged. Not included in the sequence ranking.</p>` : ''}</div></div>
    <div class="card"><h3>Top features</h3><div class="tablewrap" style="max-height:440px"><table class="tbl"><thead><tr><th class="l">FEATURE</th><th class="l">SEQ</th><th class="l">REGION</th><th>MEDIAN A</th><th>MEDIAN B</th><th>DELTA</th><th>EFFECT</th><th>q (FDR)</th><th>ρ vs VOLUME</th></tr></thead>
      <tbody>${d.features.slice(0, 40).map(f => `<tr><td class="l">${pretty(f.feature)}</td><td class="l">${SEQ[f.sequence] || 'Shape'}</td><td class="l">${REG[f.region]}</td><td>${fmt(f.median_a)}</td><td>${fmt(f.median_b)}</td><td>${f.cliffs_delta.toFixed(2)}</td><td>${f.effect}</td><td>${f.q_fdr.toExponential(1)}</td><td>${f.rho_volume === null ? '—' : f.rho_volume.toFixed(2)}</td></tr>`).join('')}</tbody></table></div>
      <p class="mut small">${esc(rk.reading)}</p></div><div id="coh-extra"></div>`;
  drawCohortViz(d);
}
$('#coh-run').onclick = runCohort;
async function drawCohortFeature() {
  const region = $('#cf-region').value || 'whole_tumour', feature = $('#cf-feature').value || 'firstorder_Mean';
  const d = await api(`/api/cohort/feature?${qs({region, feature})}`).catch(() => null);
  if (!d || !Object.keys(d.sequences).length) { $('#cf-note').textContent = 'No data for this feature yet.'; return; }
  const seqs = Object.keys(d.sequences);
  const sets = seqs.map((s, i) => ({label: SEQ[s] || 'Shape', data: Object.entries(d.sequences[s].values).map(([id, v], j) => ({x: i + ((j * 37 % 100) / 100 - .5) * .5, y: v, id})), backgroundColor: SEQC[s] + 'aa', pointRadius: 4}));
  sets.push({label: 'median', data: seqs.map((s, i) => ({x: i, y: d.sequences[s].median})), backgroundColor: '#fff', pointStyle: 'rectRot', pointRadius: 9, borderColor: '#000', borderWidth: 1});
  chart('cf-chart', {type: 'scatter', data: {datasets: sets}, options: {plugins: {legend: {labels: {color: CHART_TEXT, boxWidth: 10}}, tooltip: {callbacks: {label: c => `${c.raw.id || 'median'}: ${fmt(c.raw.y)}`}}},
    scales: {x: {min: -.6, max: seqs.length - .4, ticks: {color: CHART_TEXT, callback: v => Number.isInteger(v) ? (SEQ[seqs[v]] || 'Shape') : '', stepSize: 1}, grid: {color: CHART_GRID}}, y: axis()}}});
  boxChart('cf-box', seqs.map(s => SEQ[s] || 'Shape'), seqs.map(s => Object.values(d.sequences[s].values)), seqs.map(s => SEQC[s] || '#94a3b8'), 'value (z-scored)');
  $('#cf-note').textContent = `${d.n} calculated cases. Each dot is one case; the white diamond is the median. z-scored values: sequences are directly comparable.`;
}
$('#cf-region').onchange = $('#cf-feature').onchange = drawCohortFeature;

// ---------------------------------------------------------------- data
async function loadData() {
  const [st, s] = await Promise.all([api('/api/storage'), api('/api/status')]).catch(() => [null, null]);
  if (!st) return;
  $('#storage-cards').innerHTML = `<div class="card stat"><b>${st.staged_mb} MB</b><span>temporary case files (${st.staged_cases} cases)</span><small>re-created on demand</small></div>
    <div class="card stat"><b>${st.database_mb} MB</b><span>database</span><small>index + calculated features</small></div>
    <div class="card stat"><b>${s.calculated} / ${s.cases}</b><span>cases calculated</span><small>${esc(s.dataset_dir)}</small></div>`;
}
$('#clean-staged').onclick = async () => { const r = await api('/api/storage/cleanup?everything=true', {method: 'POST'}); $('#clean-msg').textContent = `Removed ${r.removed} folders, freed ${r.freed_mb} MB.`; loadData(); };
$('#clean-results').onclick = async () => {
  if (!confirm('Delete ALL calculated features, validation reports and temporary files? The dataset index is kept.')) return;
  try { await api('/api/results', {method: 'DELETE'}); $('#clean-msg').textContent = 'All calculated results deleted.'; refreshStatus(); loadData(); } catch (e) { toast(e.message, true); }
};

// ---------------------------------------------------------------- verification tab
const REFS = [
  ['PyRadiomics (the software)', 'van Griethuysen JJM, Fedorov A, Parmar C, et al. Computational Radiomics System to Decode the Radiographic Phenotype. Cancer Research 2017;77(21):e104–e107.', 'https://doi.org/10.1158/0008-5472.CAN-17-0339'],
  ['Feature definitions (IBSI)', 'Zwanenburg A, Vallières M, Abdalah MA, et al. The Image Biomarker Standardization Initiative: Standardized Quantitative Radiomics for High-Throughput Image-based Phenotyping. Radiology 2020;295(2):328–338.', 'https://doi.org/10.1148/radiol.2020191145'],
  ['PyRadiomics and IBSI', 'PyRadiomics documentation, “Radiomic Features”: most features follow the IBSI definitions; each difference is noted (for example kurtosis is not excess kurtosis, and total energy and standard deviation are not IBSI features).', 'https://pyradiomics.readthedocs.io/en/latest/features.html'],
  ['The dataset', 'Antonelli M, Reinke A, Bakas S, et al. The Medical Segmentation Decathlon. Nature Communications 2022;13:4128.', 'https://doi.org/10.1038/s41467-022-30695-9'],
  ['Where the images come from', 'Menze BH, Jakab A, Bauer S, et al. The Multimodal Brain Tumor Image Segmentation Benchmark (BRATS). IEEE Trans Med Imaging 2015;34(10):1993–2024.', 'https://doi.org/10.1109/TMI.2014.2377694'],
  ['Published radiomic features on the same glioma collections', 'Bakas S, Akbari H, Sotiras A, et al. Advancing The Cancer Genome Atlas glioma MRI collections with expert segmentation labels and radiomic features. Scientific Data 2017;4:170117. These were computed with other software and settings, so they are context, not a reference to match value by value.', 'https://doi.org/10.1038/sdata.2017.117'],
];
const sci = v => (v === 0 ? '0' : Number(v).toExponential(1));
async function tabVer() {
  const id = state.case.case_id, body = $('#case-tab-body');
  const d = await api(`/api/cases/${id}/verify`).catch(() => ({ready: false}));
  if (!d.ready) {
    body.innerHTML = `<div class="card"><h3>Verify the numbers against independent PyRadiomics</h3>
      <p class="mut">This re-checks the values stored for <b>${id}</b> in four independent ways, without using any of NeuroVoxel’s own file handling:</p>
      <ol class="list"><li><b>Independent re-run.</b> Reads the raw MSD files, builds the regions directly from the MSD label numbers, normalises, runs plain PyRadiomics, and compares every stored value.</li>
      <li><b>Textbook formulas.</b> Recomputes the first-order features with numpy only and compares them with PyRadiomics.</li>
      <li><b>Official reference.</b> Runs PyRadiomics on its own published brain-MRI test case and compares with the project’s official baseline values.</li>
      <li><b>Known shapes.</b> A sphere and an ellipsoid whose volume, area and axes are known exactly.</li></ol>
      <div class="row"><button class="button primary" id="ver-run">RUN VERIFICATION</button></div>
      <div class="prog" id="ver-prog" hidden><i></i></div><p class="mut small" id="ver-msg">Takes about 1 minute.</p></div>${refsCard()}`;
    $('#ver-run').onclick = async () => {
      try { await api(`/api/cases/${id}/verify/start`, {method: 'POST'}); $('#ver-prog').hidden = false; $('#ver-run').disabled = true;
        const j = await pollJob(`/api/cases/${id}/job/verify`, j => { $('#ver-prog i').style.width = Math.round(j.progress * 100) + '%'; $('#ver-msg').textContent = j.message; }, 1500);
        if (j.state === 'error') { toast(j.error, true); $('#ver-run').disabled = false; } else tabVer(); } catch (e) { toast(e.message, true); }
    };
    return;
  }
  const ind = d.independent, np_ = d.numpy, bl = d.baseline, ge = d.geometry, ok = b => `<span class="badge ${b ? 'pass' : 'fail'}">${b ? 'PASS' : 'CHECK'}</span>`;
  body.innerHTML = `<div class="card"><h3>Verification: ${ok(d.pass)} <span class="mut small">run ${esc(d.created)} · PyRadiomics ${esc(d.versions.pyradiomics)} · SimpleITK ${esc(d.versions.SimpleITK)} · numpy ${esc(d.versions.numpy)}</span></h3>
    ${d.stale ? '<div class="note err">This case was recalculated after the verification ran. Run it again.</div>' : ''}
    <div class="tiles">
      <div class="tile" style="--c:#34d399"><small>INDEPENDENT RE-RUN</small><b>${ind.identical}</b><u>of ${ind.compared} identical</u><em>worst relative difference ${sci(ind.max_rel_diff)} ${ok(ind.pass)}</em></div>
      <div class="tile" style="--c:#22d3ee"><small>TEXTBOOK FORMULAS</small><b>${np_.rows.length}</b><u>features, numpy only</u><em>worst relative difference ${sci(np_.max_rel_diff)} ${ok(np_.pass)}</em></div>
      <div class="tile" style="--c:#fbbf24"><small>OFFICIAL BASELINE</small><b>${bl.rows.reduce((a, r) => a + r.features, 0)}</b><u>reference values</u><em>worst ${Math.max(...bl.rows.map(r => r.max_pct_diff)).toPrecision(2)} % (limit ${bl.tolerance_pct} %) ${ok(bl.pass)}</em></div>
      <div class="tile" style="--c:#f472b6"><small>KNOWN SHAPES</small><b>${ge.rows.filter(r => r.pass).length}/${ge.rows.length}</b><u>within tolerance</u><em>sphere and ellipsoid ${ok(ge.pass)}</em></div></div>
    <div class="row" style="margin-top:12px"><a class="button" href="/api/cases/${id}/verify.csv">EVERY COMPARED VALUE (.CSV)</a><button class="button" id="ver-again">RUN AGAIN</button></div></div>
  <div class="grid2">
    <div class="card"><h3>1 · Independent re-run ${ok(ind.pass)}</h3><p class="mut small">${esc(ind.note)}</p>
      <p class="small">Channels used (read from dataset.json): ${Object.entries(ind.mapping.channels).map(([k, v]) => `${SEQ[k]} = ${v}`).join(', ')}. Labels: ${Object.entries(ind.mapping.labels).map(([k, v]) => `${k} = ${v}`).join(', ')}.</p>
      <div class="chart sm"><canvas id="ver-chart"></canvas></div>
      <p class="mut small">${ind.within_tolerance} of ${ind.compared} values agree within ${sci(ind.tolerance)} (relative); ${ind.identical} are bit-for-bit identical.${ind.errors.length ? ' Errors: ' + esc(ind.errors.join('; ')) : ''}</p></div>
    <div class="card"><h3>2 · Textbook formulas ${ok(np_.pass)}</h3><p class="mut small">${SEQ[np_.sequence]} · ${REG[np_.region]} · ${np_.voxels.toLocaleString()} voxels. ${esc(np_.note)}</p>
      <div class="tablewrap" style="max-height:330px"><table class="tbl"><thead><tr><th class="l">FEATURE</th><th>NUMPY</th><th>STORED (PYRADIOMICS)</th><th>REL. DIFF</th></tr></thead><tbody>${np_.rows.map(r => `<tr><td class="l">${pretty(r.feature)}</td><td>${fmt(r.numpy)}</td><td>${fmt(r.stored)}</td><td>${sci(r.rel_diff)}</td></tr>`).join('')}</tbody></table></div></div></div>
  <div class="grid2">
    <div class="card"><h3>3 · PyRadiomics’ official brain-MRI baseline ${ok(bl.pass)}</h3><p class="mut small">${esc(bl.note)}</p>
      <div class="tablewrap" style="max-height:330px"><table class="tbl"><thead><tr><th class="l">TEST CASE</th><th class="l">CLASS</th><th>FEATURES</th><th>IDENTICAL</th><th>WORST DIFF %</th></tr></thead><tbody>${bl.rows.map(r => `<tr><td class="l">${r.case}</td><td class="l">${r.class}</td><td>${r.features}</td><td>${r.identical}</td><td>${r.max_pct_diff < 1e-6 ? '< 1e-6' : r.max_pct_diff.toPrecision(2)}</td></tr>`).join('')}</tbody></table></div></div>
    <div class="card"><h3>4 · Shapes with known answers ${ok(ge.pass)}</h3><p class="mut small">${esc(ge.note)}</p>
      <div class="tablewrap" style="max-height:330px"><table class="tbl"><thead><tr><th class="l">CHECK</th><th>EXPECTED</th><th>PYRADIOMICS</th><th>DIFF %</th><th>LIMIT %</th></tr></thead><tbody>${ge.rows.map(r => `<tr><td class="l">${esc(r.check)}</td><td>${fmt(r.expected)}</td><td>${fmt(r.got)}</td><td>${r.rel_diff_pct.toFixed(2)}</td><td>${r.tolerance_pct}</td></tr>`).join('')}</tbody></table></div></div></div>
  <div class="card"><h3>What this shows, and what it does not</h3><ul class="list">
    <li><b>Shows:</b> NeuroVoxel’s channel split, label mapping, region definitions, normalisation, storage and display do not change a single PyRadiomics number; the installed PyRadiomics reproduces its own published reference on a brain MRI; and its first-order and shape values equal the mathematical definitions.</li>
    <li><b>Does not show:</b> that these features are clinically meaningful, or that another software package would give the same numbers. Radiomic values depend on normalisation and binning, so values from other studies are comparable only when the settings are identical (here: z-score on non-zero voxels per sequence, bin width 0.25, no resampling).</li>
    <li><b>Reproduce it yourself:</b> <code>docker compose exec neurovoxel python verify.py ${id}</code>, or run PyRadiomics yourself on the same raw files with the settings above and compare with the CSV.</li></ul></div>${refsCard()}`;
  $('#ver-again').onclick = async () => { await api(`/api/cases/${id}/verify/start`, {method: 'POST'}).catch(e => toast(e.message, true)); const full = await api(`/api/cases/${id}/verify`).catch(() => null); $('#case-tab-body').innerHTML = '<div class="card"><p class="mut">Running verification…</p><div class="prog"><i id="ver-bar"></i></div><p class="mut small" id="ver-msg2"></p></div>';
    const j = await pollJob(`/api/cases/${id}/job/verify`, j => { $('#ver-bar').style.width = Math.round(j.progress * 100) + '%'; $('#ver-msg2').textContent = j.message; }, 1500); if (j.state === 'error') toast(j.error, true); tabVer(); };
  const R = (ind.worst || []).slice(0, 0);
  const byClass = {}; for (const w of (ind.worst || [])) byClass[w.feature] = w.rel_diff;
  chart('ver-chart', {type: 'bar', data: {labels: ['values compared', 'bit-for-bit identical', `within ${sci(ind.tolerance)}`], datasets: [{data: [ind.compared, ind.identical, ind.within_tolerance], backgroundColor: ['#38bdf8cc', '#34d399cc', '#22d3eecc'], borderRadius: 5}]},
    options: {plugins: {legend: {display: false}}, scales: {x: axis({grid: {display: false}}), y: axis({beginAtZero: true})}}});
}
function refsCard() {
  return `<div class="card"><h3>References, and how to cite them</h3><p class="mut small">There is no published “correct value” for each feature of this dataset, because the numbers depend on the settings. What can be cited is the software, the feature definitions and the data.</p>
    <ul class="list">${REFS.map(([t, c, u]) => `<li><b>${esc(t)}.</b> ${esc(c)} <a href="${u}" target="_blank" rel="noopener">${u.replace('https://', '')}</a></li>`).join('')}</ul></div>`;
}

// ---------------------------------------------------------------- extra visualisations (charts for every analysis)
const hslHex = (h, s, l) => { s /= 100; l /= 100; const k = n => (n + h / 30) % 12, a = s * Math.min(l, 1 - l), f = n => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1))); return '#' + [f(0), f(8), f(4)].map(x => Math.round(x * 255).toString(16).padStart(2, '0')).join(''); };
const vizPal = n => Array.from({length: Math.max(n, 1)}, (_, i) => hslHex(Math.round((190 + i * 360 / Math.max(n, 1)) % 360), 78, 62));   // hex, so '+cc' transparency works
const COMP = [['net', 'Non-enhancing / necrotic', '#a78bfa', 'vol_net_ml'], ['edema', 'Peritumoral edema', '#38bdf8', 'vol_edema_ml'], ['enh', 'Enhancing tumour', '#fb7185', 'vol_enh_ml']];
const DOMC = {edema: '#38bdf8', net: '#a78bfa', enhancing: '#fb7185'};
const HEMIC = {left: '#22d3ee', right: '#fbbf24', bilateral: '#f472b6', null: '#64748b', None: '#64748b'};
const legendOpts = pos => ({position: pos || 'right', labels: {color: CHART_TEXT, boxWidth: 10, padding: 10}});
const quant = (a, q) => { const s = [...a].sort((x, y) => x - y); if (!s.length) return null; const p = (s.length - 1) * q, lo = Math.floor(p), hi = Math.ceil(p); return s[lo] + (s[hi] - s[lo]) * (p - lo); };
const vizCard = (id, title, note = '', cls = '') => `<div class="card"><h3>${title}</h3><div class="chart ${cls}"><canvas id="${id}"></canvas></div>${note ? `<p class="mut small">${note}</p>` : ''}</div>`;
function heatColor(t) {
  const S = [[9, 22, 44], [15, 98, 130], [34, 211, 238], [251, 191, 36], [244, 114, 182]], x = Math.min(0.9999, Math.max(0, t)) * (S.length - 1), i = Math.floor(x), f = x - i;
  return `rgb(${S[i].map((v, k) => Math.round(v + (S[i + 1][k] - v) * f)).join(',')})`;
}
function heatGrid(rows, cols, val, label, cellText) {
  // val(r, c) -> 0..1 or null; label(r, c) -> tooltip text
  const head = cols.map(c => `<div class="hc hh" title="${esc(c)}">${esc(c.length > 14 ? c.slice(0, 13) + '…' : c)}</div>`).join('');
  const body = rows.map((r, ri) => `<div class="hc rh" title="${esc(r)}">${esc(r)}</div>` + cols.map((c, ci) => {
    const v = val(ri, ci);
    return `<div class="hc" style="background:${v == null ? '#1e293b' : heatColor(v)};color:${v != null && v > .55 && v < .85 ? '#04101e' : '#e5edf9'}" title="${esc(label(ri, ci))}">${cellText ? cellText(ri, ci) : ''}</div>`;
  }).join('')).join('');
  return `<div class="heatwrap"><div class="heat" style="grid-template-columns:130px repeat(${cols.length},minmax(46px,1fr))"><div class="hc"></div>${head}${body}</div></div>
    <div class="heatkey"><span>low</span><i></i><span>high</span></div>`;
}
const donut = (id, labels, data, colors, extra = {}) => chart(id, {type: 'doughnut', data: {labels, datasets: [{data, backgroundColor: colors, borderColor: '#07162b', borderWidth: 2, hoverOffset: 8}]},
  options: {cutout: '58%', plugins: {legend: legendOpts(extra.legend), tooltip: {callbacks: {label: c => `${c.label}: ${c.raw} (${(c.raw / (c.dataset.data.reduce((a, b) => a + b, 0) || 1) * 100).toFixed(1)}%)`}}}}});
const radar = (id, labels, sets, max) => chart(id, {type: 'radar', data: {labels, datasets: sets.map(s => ({label: s.label, data: s.data, borderColor: s.color, backgroundColor: s.color + '33', pointBackgroundColor: s.color, borderWidth: 2, pointRadius: 3}))},
  options: {plugins: {legend: legendOpts('bottom')}, scales: {r: {min: 0, max: max ?? 100, angleLines: {color: '#33415588'}, grid: {color: '#33415566'}, pointLabels: {color: CHART_TEXT, font: {size: 11}}, ticks: {color: CHART_TEXT, backdropColor: 'transparent', maxTicksLimit: 5}}}}});
function boxChart(id, labels, groups, colors, yTitle) {
  const st = groups.map(v => v.length ? {mn: Math.min(...v), q1: quant(v, .25), md: quant(v, .5), q3: quant(v, .75), mx: Math.max(...v)} : null);
  chart(id, {data: {labels, datasets: [
    {type: 'bar', label: 'middle 50%', data: st.map(s => s ? [s.q1, s.q3] : null), backgroundColor: colors.map(c => c + '88'), borderColor: colors, borderWidth: 1.5, borderRadius: 4, barPercentage: .5},
    {type: 'line', label: 'median', data: st.map(s => s && s.md), showLine: false, pointStyle: 'rect', pointRadius: 9, pointHoverRadius: 11, backgroundColor: '#fff', borderColor: '#fff'},
    {type: 'line', label: 'min', data: st.map(s => s && s.mn), showLine: false, pointStyle: 'line', pointRadius: 9, borderColor: '#cbd5e1', borderWidth: 2},
    {type: 'line', label: 'max', data: st.map(s => s && s.mx), showLine: false, pointStyle: 'line', pointRadius: 9, borderColor: '#cbd5e1', borderWidth: 2}]},
    options: {plugins: {legend: {display: false}, tooltip: {callbacks: {label: c => `${c.dataset.label}: ${Array.isArray(c.raw) ? fmt(c.raw[0]) + ' – ' + fmt(c.raw[1]) : fmt(c.raw)}`}}},
      scales: {x: axis({grid: {display: false}}), y: axis({title: {display: !!yTitle, text: yTitle, color: CHART_TEXT}})}}});
}
const countBy = (rows, f) => rows.reduce((o, r) => { const k = f(r) ?? 'unknown'; o[k] = (o[k] || 0) + 1; return o; }, {});

// ---- Dataset page
async function drawHomeCharts() {
  const host = $('#home-charts'); if (!host) return;
  const d = await api('/api/dataset/charts').catch(() => null);
  if (!d || !d.rows.length) { host.innerHTML = ''; return; }
  const R = d.rows, mean = k => R.reduce((a, r) => a + (r[k] || 0), 0) / R.length;
  host.innerHTML = `<div class="eyebrow" style="margin:8px 0 -4px">THE DATASET AT A GLANCE · ${R.length} CASES</div><div class="vizgrid">
    ${vizCard('hc-dom', 'Largest sub-region', 'Which label has the most voxels in each case.', 'sm')}
    ${vizCard('hc-hemi', 'Side of the brain (approximate)', 'Midline = centre of brain extent. No atlas.', 'sm')}
    ${vizCard('hc-comp', 'Average tumour composition', 'Mean share of whole-tumour volume across all cases.', 'sm')}
    ${vizCard('hc-hist', 'Whole-tumour volume', 'Number of cases per volume range (mL).', 'sm')}
    ${vizCard('hc-sc', 'Enhancing share vs size', 'Each dot is one case. Large tumours are not always the most enhancing.', 'sm')}
    ${vizCard('hc-val', 'Consistency check and calculation status', '', 'sm')}</div>`;
  const dom = countBy(R, r => r.dominant), hemi = countBy(R, r => r.hemisphere);
  donut('hc-dom', Object.keys(dom).map(k => DOM[k] || k), Object.values(dom), Object.keys(dom).map(k => DOMC[k] || '#64748b'));
  donut('hc-hemi', Object.keys(hemi).map(k => ({bilateral: 'Crosses midline'})[k] || k[0].toUpperCase() + k.slice(1)), Object.values(hemi), Object.keys(hemi).map(k => HEMIC[k] || '#64748b'));
  donut('hc-comp', COMP.map(c => c[1]), [mean('net_pct'), mean('edema_pct'), mean('enh_pct')].map(v => +v.toFixed(2)), COMP.map(c => c[2]));
  const vols = R.map(r => r.vol_whole_ml).filter(v => v > 0), mx = Math.max(...vols), bins = 12, bw = mx / bins, counts = Array(bins).fill(0);
  vols.forEach(v => counts[Math.min(bins - 1, Math.floor(v / bw))]++);
  chart('hc-hist', {type: 'bar', data: {labels: counts.map((_, i) => `${(i * bw).toFixed(0)}–${((i + 1) * bw).toFixed(0)}`), datasets: [{label: 'cases', data: counts, backgroundColor: '#22d3eeaa', borderRadius: 4}]},
    options: {plugins: {legend: {display: false}}, scales: {x: axis({grid: {display: false}, title: {display: true, text: 'mL', color: CHART_TEXT}}), y: axis({title: {display: true, text: 'cases', color: CHART_TEXT}})}}});
  chart('hc-sc', {type: 'scatter', data: {datasets: Object.keys(dom).map(k => ({label: DOM[k] || k, data: R.filter(r => r.dominant === k).map(r => ({x: r.vol_whole_ml, y: r.enh_pct, id: r.case_id})), backgroundColor: (DOMC[k] || '#64748b') + 'aa', pointRadius: 3.5}))},
    options: {plugins: {legend: legendOpts('bottom'), tooltip: {callbacks: {label: c => `${c.raw.id}: ${fmt1(c.raw.x)} mL, enhancing ${fmt1(c.raw.y)}%`}}}, scales: {x: axis({title: {display: true, text: 'whole tumour (mL)', color: CHART_TEXT}}), y: axis({title: {display: true, text: 'enhancing % of tumour', color: CHART_TEXT}})}}});
  const vs = countBy(R, r => r.index_status), cs = countBy(R, r => r.calc_status === 'done' ? 'calculated' : 'not calculated');
  const labels = [...['pass', 'warn', 'fail'].filter(k => vs[k]).map(k => 'check: ' + k), ...Object.keys(cs)];
  const colors = [...['pass', 'warn', 'fail'].filter(k => vs[k]).map(k => ({pass: '#34d399', warn: '#fbbf24', fail: '#fb7185'})[k]), ...Object.keys(cs).map(k => k === 'calculated' ? '#22d3ee' : '#475569')];
  donut('hc-val', labels, [...['pass', 'warn', 'fail'].filter(k => vs[k]).map(k => vs[k]), ...Object.values(cs)], colors);
}

// ---- Case page
async function drawCaseViz(c, calculated, uploadId = null) {
  const host = $('#case-viz'); if (!host) return;
  const vals = COMP.map(x => +(c[x[3]] || 0).toFixed(2)); if (!vals.some(v => v > 0)) { host.innerHTML = ''; return; }
  host.innerHTML = `<div class="vizgrid">${vizCard('cv-comp', 'Sub-region composition of this tumour', 'Share of the whole-tumour volume (mL).', 'sm')}
    ${vizCard('cv-vol', 'Volume by region', 'Whole tumour, tumour core (non-enhancing + enhancing), each single label.', 'sm')}
    ${calculated ? vizCard('cv-rad', 'Where this case sits among calculated cases', 'Percentile of key whole-tumour features per sequence (50 = typical). Needs at least 5 calculated cases.', 'sm') : ''}</div>`;
  donut('cv-comp', COMP.map((x, i) => `${x[1]} · ${vals[i]} mL`), vals, COMP.map(x => x[2]));
  const sets = [['Whole tumour', c.vol_whole_ml, '#22d3ee'], ['Tumour core', c.vol_core_ml, '#f472b6'], ['Edema', c.vol_edema_ml, '#38bdf8'], ['Non-enhancing', c.vol_net_ml, '#a78bfa'], ['Enhancing', c.vol_enh_ml, '#fb7185']];
  chart('cv-vol', {type: 'bar', data: {labels: sets.map(s => s[0]), datasets: [{data: sets.map(s => +(s[1] || 0).toFixed(2)), backgroundColor: sets.map(s => s[2] + 'cc'), borderRadius: 5}]},
    options: {indexAxis: 'y', plugins: {legend: {display: false}}, scales: {x: axis({title: {display: true, text: 'mL', color: CHART_TEXT}}), y: axis({grid: {display: false}})}}});
  if (calculated) {
    const p = uploadId ? await api(`/api/upload/${uploadId}/percentiles?region=whole_tumour`).catch(() => null) : await api('/api/cohort/percentiles', {body: {case_id: c.case_id, region: 'whole_tumour'}}).catch(() => null);
    if (!p || !p.values || !Object.keys(p.values).length) { $('#cv-rad')?.closest('.card')?.remove(); return; }
    const feats = KEY_FEATURES.filter(k => Object.keys(p.values).some(s => p.values[s]?.[k])).slice(0, 8);
    radar('cv-rad', feats.map(pretty), Object.keys(p.values).filter(s => SEQC[s] && s !== 'mask').map(s => ({label: SEQ[s], color: SEQC[s], data: feats.map(k => p.values[s]?.[k]?.percentile ?? null)})), 100);
  }
}

// ---- Compare patients
function drawPatientViz(ids, d, seq, region) {
  const host = $('#pt-extra'); if (!host) return;
  const infos = ids.map(i => d.cases[i].info), many = ids.length > 12;
  const feats = KEY_FEATURES.filter(k => ids.some(i => fvCase(d.cases[i], seq, k) !== undefined));
  const col = k => ids.map(i => fvCase(d.cases[i], seq, k)), norm = k => { const v = col(k).filter(x => x != null), lo = Math.min(...v), hi = Math.max(...v); return ids.map(i => { const x = fvCase(d.cases[i], seq, k); return x == null ? null : (hi > lo ? (x - lo) / (hi - lo) : .5); }); };
  const pct = (i, k) => d.cases[i].percentiles?.[seq]?.[k]?.percentile;
  const usePct = d.n_cohort >= 5 && ids.some(i => feats.some(k => pct(i, k) !== undefined));
  const heatVal = (ri, ci) => { const k = feats[ci], i = ids[ri]; if (usePct) { const p = pct(i, k); return p == null ? null : p / 100; } return norm(k)[ri]; };
  const colors = vizPal(ids.length);
  host.innerHTML = `<div class="vizgrid">
    <div class="card span2"><h3>Heatmap: every patient × every feature</h3>${heatGrid(ids, feats.map(pretty), heatVal, (r, c) => `${ids[r]} · ${pretty(feats[c])}: ${fmt(fvCase(d.cases[ids[r]], seq, feats[c]))}${usePct && pct(ids[r], feats[c]) != null ? ` (percentile ${pct(ids[r], feats[c]).toFixed(0)})` : ''}`, many ? null : (r, c) => { const v = heatVal(r, c); return v == null ? '' : Math.round(v * 100); })}
      <p class="mut small">${usePct ? `Colour = percentile among ${d.n_cohort} calculated cases (${SEQ[seq]}, ${REG[region]}).` : 'Colour = value scaled between the lowest and highest of the selected patients (not enough calculated cases for percentiles).'} Hover a cell for the real value.</p></div>
    ${vizCard('pv-comp', 'Sub-region composition', 'Each bar = 100% of that patient’s whole tumour.', many ? 'tall' : 'sm')}
    ${vizCard('pv-vol', 'Whole-tumour volume (mL)', 'Sorted, coloured by the largest sub-region.', many ? 'tall' : 'sm')}
    ${vizCard('pv-rad', ids.length > 6 ? 'Profile of the first 6 patients' : 'Profile of each patient', usePct ? 'Percentile of 8 key features.' : 'Scaled 0–100 between selected patients.', 'sm')}
    <div class="card"><h3>Volume vs feature</h3><div class="row" style="margin-bottom:6px"><label>Feature <select id="pv-feat">${feats.map(k => `<option value="${k}">${pretty(k)}</option>`).join('')}</select></label></div><div class="chart sm"><canvas id="pv-sc"></canvas></div></div>
    ${vizCard('pv-dom', 'Largest sub-region among selected', '', 'sm')}
    ${vizCard('pv-hemi', 'Side among selected', '', 'sm')}</div>`;
  chart('pv-comp', {type: 'bar', data: {labels: ids, datasets: COMP.map(([k, n, c, f]) => ({label: n, backgroundColor: c + 'dd', data: infos.map(x => { const t = COMP.reduce((a, y) => a + (x[y[3]] || 0), 0); return t ? +((x[f] || 0) / t * 100).toFixed(1) : 0; })}))},
    options: {indexAxis: 'y', plugins: {legend: legendOpts('bottom')}, scales: {x: axis({stacked: true, max: 100, title: {display: true, text: '% of whole tumour', color: CHART_TEXT}}), y: axis({stacked: true, grid: {display: false}})}}});
  const order = ids.map((_, i) => i).sort((a, b) => (infos[b].vol_whole_ml || 0) - (infos[a].vol_whole_ml || 0));
  chart('pv-vol', {type: 'bar', data: {labels: order.map(i => ids[i]), datasets: [{data: order.map(i => infos[i].vol_whole_ml), backgroundColor: order.map(i => (DOMC[infos[i].dominant] || '#22d3ee') + 'cc'), borderRadius: 4}]},
    options: {plugins: {legend: {display: false}}, scales: {x: axis({grid: {display: false}}), y: axis({title: {display: true, text: 'mL', color: CHART_TEXT}})}}});
  const rf = feats.slice(0, 8), rIds = ids.slice(0, 6);
  radar('pv-rad', rf.map(pretty), rIds.map((i, n) => ({label: i, color: colors[ids.indexOf(i)], data: rf.map(k => usePct ? (pct(i, k) ?? null) : (norm(k)[ids.indexOf(i)] ?? 0) * 100)})), 100);
  const dom = countBy(infos, x => x.dominant), hemi = countBy(infos, x => x.hemisphere);
  donut('pv-dom', Object.keys(dom).map(k => DOM[k] || k), Object.values(dom), Object.keys(dom).map(k => DOMC[k] || '#64748b'), {legend: 'bottom'});
  donut('pv-hemi', Object.keys(hemi).map(k => ({bilateral: 'Crosses midline'})[k] || k), Object.values(hemi), Object.keys(hemi).map(k => HEMIC[k] || '#64748b'), {legend: 'bottom'});
  const sc = () => { const k = $('#pv-feat').value; chart('pv-sc', {type: 'scatter', data: {datasets: ids.map((i, n) => ({label: i, data: [{x: infos[n].vol_whole_ml, y: fvCase(d.cases[i], seq, k)}], backgroundColor: colors[n], pointRadius: 7}))},
    options: {plugins: {legend: {display: ids.length <= 12, labels: {color: CHART_TEXT, boxWidth: 8}}}, scales: {x: axis({title: {display: true, text: 'whole tumour (mL)', color: CHART_TEXT}}), y: axis({title: {display: true, text: pretty(k), color: CHART_TEXT}})}}}); };
  if (feats.length) { $('#pv-feat').onchange = sc; sc(); }
}

// ---- Cohort comparison
function drawCohortViz(d) {
  const host = $('#coh-extra'); if (!host) return;
  const rk = d.ranking, F = d.features, seqs = [...new Set(rk.by_set.map(r => r.sequence))], regs = [...new Set(rk.by_set.map(r => r.region))];
  const cell = (s, r) => rk.by_set.find(x => x.sequence === s && x.region === r), mx = Math.max(...rk.by_set.map(x => x.median_abs_delta), .01);
  const effects = countBy(F, f => f.effect), top = F.slice(0, 15);
  host.innerHTML = `<div class="eyebrow" style="margin:6px 0 -4px">MORE VIEWS OF THE SAME COMPARISON</div><div class="vizgrid">
    <div class="card"><h3>Heatmap: sequence × region</h3>${heatGrid(seqs.map(s => SEQ[s] || s), regs.map(r => REG[r]), (ri, ci) => { const c = cell(seqs[ri], regs[ci]); return c ? c.median_abs_delta / mx : null; },
      (r, c) => { const x = cell(seqs[r], regs[c]); return x ? `${SEQ[seqs[r]]} · ${REG[regs[c]]}: median |delta| ${x.median_abs_delta.toFixed(2)}, ${x.flagged}/${x.features} features flagged` : ''; }, (r, c) => { const x = cell(seqs[r], regs[c]); return x ? x.median_abs_delta.toFixed(2) : ''; })}
      <p class="mut small">Brighter = the two groups differ more (median |Cliff's delta|).</p></div>
    ${vizCard('cv2-rad', 'Radar: separation per sequence', 'One line per region. Further out = groups separated more.', 'sm')}
    ${vizCard('cv2-vol', 'Volcano: every feature', `Right/left = which group is higher. Higher up = stronger evidence (FDR-corrected). Dotted lines: |delta| 0.33 and q = 0.05. ${F.filter(f => f.q_fdr < 0.05).length} of ${F.length} features reach q < 0.05${F.filter(f => f.q_fdr < 0.05).length ? '.' : ': with few cases nothing can, so calculate more cases per group.'}`, 'tall')}
    ${vizCard('cv2-top', 'Top 15 features', 'Bar length = Cliff’s delta. Positive: group A is higher.', 'tall')}
    ${vizCard('cv2-eff', 'How many features differ, by effect size', '', 'sm')}
    ${vizCard('cv2-n', 'Group sizes', 'Fewer than ~10 cases per group makes the result unstable.', 'sm')}</div>`;
  radar('cv2-rad', seqs.map(s => SEQ[s] || s), regs.map(r => ({label: REG[r], color: REGC[r], data: seqs.map(s => +(cell(s, r)?.median_abs_delta ?? 0).toFixed(3))})), Math.ceil(mx * 10) / 10);
  chart('cv2-vol', {type: 'scatter', data: {datasets: seqs.map(s => ({label: SEQ[s] || s, data: F.filter(f => f.sequence === s).map(f => ({x: f.cliffs_delta, y: -Math.log10(Math.max(f.q_fdr, 1e-300)), n: `${pretty(f.feature)} · ${REG[f.region]}`})), backgroundColor: (SEQC[s] || '#94a3b8') + '99', pointRadius: 3}))},
    options: {plugins: {legend: legendOpts('bottom'), tooltip: {callbacks: {label: c => `${c.raw.n}: delta ${c.raw.x.toFixed(2)}, q ${Math.pow(10, -c.raw.y).toExponential(1)}`}}},
      scales: {x: axis({min: -1, max: 1, title: {display: true, text: '← group B higher      Cliff’s delta      group A higher →', color: CHART_TEXT}}), y: axis({min: 0, suggestedMax: 2, title: {display: true, text: '−log10 q (FDR)', color: CHART_TEXT}})}},
    plugins: [{id: 'volcanoLines', afterDraw(ch) { const {ctx, scales: {x, y}, chartArea: a} = ch; ctx.save(); ctx.setLineDash([4, 4]); ctx.strokeStyle = '#94a3b8aa'; ctx.lineWidth = 1; ctx.beginPath();
      [-.33, .33].forEach(v => { const px = x.getPixelForValue(v); ctx.moveTo(px, a.top); ctx.lineTo(px, a.bottom); }); const py = y.getPixelForValue(-Math.log10(.05)); if (py >= a.top && py <= a.bottom) { ctx.moveTo(a.left, py); ctx.lineTo(a.right, py); } ctx.stroke(); ctx.restore(); }}]});
  chart('cv2-top', {type: 'bar', data: {labels: top.map(f => `${pretty(f.feature)} · ${SEQ[f.sequence] || 'Shape'} · ${REG[f.region].split(' ')[0]}`), datasets: [{data: top.map(f => +f.cliffs_delta.toFixed(3)), backgroundColor: top.map(f => f.cliffs_delta >= 0 ? '#22d3eecc' : '#f472b6cc'), borderRadius: 3}]},
    options: {indexAxis: 'y', plugins: {legend: {display: false}}, scales: {x: axis({min: -1, max: 1}), y: axis({grid: {display: false}, ticks: {color: CHART_TEXT, font: {size: 10}}})}}});
  const ek = Object.keys(effects), EC = {negligible: '#475569', small: '#38bdf8', medium: '#fbbf24', large: '#fb7185'};
  donut('cv2-eff', ek, Object.values(effects), ek.map(k => EC[k] || '#94a3b8'));
  donut('cv2-n', ['Group A', 'Group B'], [d.n_a, d.n_b], ['#22d3ee', '#f472b6']);
}

// ---------------------------------------------------------------- start
refreshStatus();
setInterval(refreshStatus, 30000);
show((location.hash || '#home').slice(1) === 'case' ? 'home' : (location.hash || '#home').slice(1), false);
