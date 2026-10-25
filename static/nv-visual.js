/* NeuroVoxel region visuals for the Case Explorer: region cinema (orbit / explode / scan / bounds / replay),
   volumetrics, spatial locator (3 views + hemisphere gauge) and Shape Lab (lit surface, label / height / distance / intensity colours).
   Everything is drawn from the real segmentation voxels of the selected case. Nothing is simulated. */
(() => {
  'use strict';
  if (window.NeuroVoxelAdvanced) return;
  const API = '/api/adv';
  const RM = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const SEQN = { t1: 'T1', t1ce: 'T1c', t2: 'T2', flair: 'FLAIR' };
  const S = { uuid: null, seq: 't1ce', available: Object.keys(SEQN), groups: 'Tumour core:1+4', vol: null, pts: null, loc: null, shell: null };
  const NAMES = { all: 'Whole tumour', label_1: 'Non-enhancing / necrotic', label_2: 'Peritumoral edema', label_4: 'Enhancing tumour', 'group_Tumour core': 'Tumour core' };
  const COLS = { all: '#22d3ee', label_1: '#a78bfa', label_2: '#38bdf8', label_4: '#fb7185', 'group_Tumour core': '#f472b6' };
  const PAL = ['#a78bfa', '#38bdf8', '#fb7185', '#fbbf24', '#34d399', '#fb923c'];

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const fmt = (v, d = 2) => (v == null || !isFinite(v) ? '—' : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const ease = t => 1 - Math.pow(1 - t, 3);
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const labelKeys = () => S.vol.labels.map(i => 'label_' + i);
  const orderKeys = () => Object.keys(S.vol.regions);
  const nm = k => NAMES[k] || (S.vol && S.vol.regions[k] ? S.vol.regions[k].name : k);
  const colorOf = k => COLS[k] || (k.startsWith('label_') ? PAL[Math.max(0, S.vol.labels.indexOf(+k.slice(6))) % PAL.length] : '#e2e8f0');
  const refreshNames = () => { $$('[data-nm]').forEach(el => { el.textContent = nm(el.dataset.nm); }); };

  let threeP = null;
  const getThree = () => (threeP = threeP || import('/static/vendor/three.module.js').catch(() => import('https://cdn.jsdelivr.net/npm/three@0.161.0/build/three.module.js')));

  function tween(ms, fn) {
    if (RM) { fn(1); return; }
    const t0 = performance.now();
    (function frame(now) { const t = clamp((now - t0) / ms, 0, 1); fn(ease(t)); if (t < 1) requestAnimationFrame(frame); })(t0);
  }
  const countUp = (el, to, d) => tween(1000, e => { el.textContent = fmt(to * e, d); });
  async function api(path) {
    const r = await fetch(API + path);
    let j = null;
    try { j = await r.json(); } catch { /* non-JSON error */ }
    if (!r.ok) throw new Error((j && j.detail) || 'Request failed (' + r.status + ')');
    return j;
  }
  const qs = () => '?uuid=' + encodeURIComponent(S.uuid) + '&seq=' + S.seq + '&groups=' + encodeURIComponent(S.groups);

  const HTML = `
  <div class="nva-wrap">
    <div class="nva-bar2"><span class="nva-mut">Sequence used for the position view and the intensity colours</span>
      <span class="nvs-seg" id="nva-seq">${Object.entries(SEQN).map(([k, n]) => `<button data-s="${k}" aria-pressed="${k === 't1ce'}">${n}</button>`).join('')}</span></div>
    <div class="nva-note err" id="nva-vnote" hidden></div>
    <div class="nva-grid">
      <article class="nva-card nva-cin nva-reveal">
        <header><b>Region cinema</b><em>true voxels, one colour per sub-region</em></header>
        <div class="nva-stage play" id="nva-stage">
          <canvas id="nva-gl" aria-label="Interactive 3D view of the labelled regions"></canvas>
          <div class="nva-vig"></div><div class="nva-lb top"></div><div class="nva-lb bot"></div>
          <div class="nva-ttl"><small>RECONSTRUCTING</small><b>Tumour sub-regions</b></div>
          <div class="nva-hud" id="nva-hud"></div>
        </div>
        <div class="nva-ctl">
          <button class="nva-btn" data-act="orbit" aria-pressed="true">Orbit</button>
          <button class="nva-btn" data-act="explode" aria-pressed="false">Explode</button>
          <button class="nva-btn" data-act="scan" aria-pressed="true">Scan</button>
          <button class="nva-btn" data-act="box" aria-pressed="false">Bounds</button>
          <button class="nva-btn" data-act="replay">Replay</button>
          <span class="nva-legend" id="nva-legend"></span>
        </div>
      </article>
      <article class="nva-card nva-reveal" id="nva-vol">
        <header><b>Volumetrics</b><em id="nva-vmeta"></em></header>
        <div class="nva-tiles" id="nva-tiles"></div>
        <div class="nva-stackwrap" id="nva-stack"></div>
        <div class="nva-ratio" id="nva-ratio"></div>
      </article>
    </div>
    <article class="nva-card nva-reveal" id="nva-spa">
      <header><b>Spatial position</b><em>from the image affine, no atlas</em></header>
      <div class="nva-spa" id="nva-spabody"></div>
    </article>
    <article class="nva-card nva-reveal nvs-lab" id="nvs-card" style="margin-top:22px">
      <header><b>Shape Lab</b><em>lit surface of the real segmentation</em></header>
      <div id="nvs-root"></div>
    </article>
  </div>`;

  let mounted = false, labRoot = null;
  function mount() {
    const host = document.getElementById('nva-host');
    if (!host || mounted) return !!host;
    host.innerHTML = HTML; mounted = true;
    const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) e.target.classList.add('in'); }), { threshold: 0.04 });
    $$('.nva-reveal', host).forEach(el => io.observe(el));
    labRoot = $('#nvs-root'); labRoot.innerHTML = LAB_HTML;
    $$('#nva-seq button').forEach(b => b.onclick = () => {
      if (b.dataset.s === S.seq) return;
      S.seq = b.dataset.s; $$('#nva-seq button').forEach(x => x.setAttribute('aria-pressed', x === b)); refreshSeq();
    });
    return true;
  }

  async function load(uuid, available = Object.keys(SEQN)) {
    if (!mount()) return;
    S.available = available.filter(s => SEQN[s]);
    if (!S.available.length) S.available = Object.keys(SEQN);
    $$('#nva-seq button').forEach(b => { b.hidden = !S.available.includes(b.dataset.s); });
    if (!S.available.includes(S.seq)) S.seq = S.available.includes('t1ce') ? 't1ce' : S.available[0];
    $$('#nva-seq button').forEach(b => b.setAttribute('aria-pressed', b.dataset.s === S.seq));
    if (uuid === S.uuid && S.vol) { await refreshSeq(); return; }
    Object.assign(S, { uuid, vol: null, pts: null, loc: null, shell: null });
    await refreshGeometry();
  }
  async function refreshGeometry() {
    const note = $('#nva-vnote'), id = S.uuid;
    note.hidden = true;
    try {
      const [vol, pts, loc, shell] = await Promise.all([api('/subregions' + qs()), api('/points' + qs()), api('/locator' + qs()), api('/shell' + qs()).catch(() => null)]);
      if (id !== S.uuid) return;
      S.vol = vol; S.pts = pts; S.loc = loc; S.shell = shell;
      renderVol(); renderSpatial(); initCinema(); initLab();
    } catch (e) { note.textContent = e.message; note.hidden = false; }
  }
  async function refreshSeq() {
    if (!S.vol) return;
    const id = S.uuid;
    try {
      const [vol, loc, shell] = await Promise.all([api('/subregions' + qs()), api('/locator' + qs()), api('/shell' + qs()).catch(() => null)]);
      if (id !== S.uuid) return;
      S.vol = vol; S.loc = loc; S.shell = shell; renderSpatial(); initLab();
    } catch (e) { $('#nva-vnote').textContent = e.message; $('#nva-vnote').hidden = false; }
  }
  /* shape metrics need the PyRadiomics results of the case; call after a calculation finishes */
  async function refreshShapes() { if (!S.uuid || !S.vol) return; try { S.shell = await api('/shell' + qs()); initLab(); } catch { /* keep the old view */ } }

  /* ================= volumetrics ================= */
  function renderVol() {
    const v = S.vol, keys = labelKeys();
    $('#nva-vmeta').textContent = fmt(v.voxel_mm3, 3) + ' mm³ per voxel';
    $('#nva-tiles').innerHTML = orderKeys().map(k => {
      const r = v.regions[k];
      return `<div class="nva-tile" style="--c:${colorOf(k)}"><small data-nm="${k}">${esc(nm(k))}</small><b><span data-to="${r.ml}">0.00</span><u>mL</u></b><em>${fmt(r.mm3, 0)} mm³ · ${r.voxels.toLocaleString()} voxels</em></div>`;
    }).join('');
    $$('#nva-tiles [data-to]').forEach(el => countUp(el, +el.dataset.to, 2));
    $('#nva-stack').innerHTML = `<div class="nva-bar">${keys.map(k => `<i data-w="${v.regions[k].pct || 0}" style="background:${colorOf(k)};width:0">${(v.regions[k].pct || 0) > 9 ? fmt(v.regions[k].pct, 0) + '%' : ''}</i>`).join('')}</div>
      <div class="nva-key">${keys.map(k => `<span style="--c:${colorOf(k)}"><b data-nm="${k}">${esc(nm(k))}</b> ${fmt(v.regions[k].pct, 1)}%</span>`).join('')}</div>`;
    setTimeout(() => $$('#nva-stack .nva-bar i').forEach((el, i) => setTimeout(() => { el.style.width = el.dataset.w + '%'; }, i * 160)), 120);
    const opts = orderKeys().map(k => `<option value="${k}" data-nm="${k}">${esc(nm(k))}</option>`).join('');
    const box = $('#nva-ratio');
    if (orderKeys().length < 2) { box.innerHTML = ''; return; }
    box.innerHTML = `Ratio <select id="nva-ra" aria-label="Numerator region">${opts}</select> : <select id="nva-rb" aria-label="Denominator region">${opts}</select><span id="nva-rout"></span>`;
    $('#nva-ra').value = keys[keys.length - 1]; $('#nva-rb').value = 'all';
    const upd = () => { const a = v.regions[$('#nva-ra').value], b = v.regions[$('#nva-rb').value]; $('#nva-rout').innerHTML = b.mm3 > 0 ? `= <b>${fmt(a.mm3 / b.mm3, 3)}</b> &nbsp;(${fmt(a.mm3 / b.mm3 * 100, 1)}% of the second)` : '= n/a'; };
    $('#nva-ra').onchange = $('#nva-rb').onchange = upd; upd();
  }

  /* ================= spatial ================= */
  const VIEWS = [['axial', 'Axial'], ['coronal', 'Coronal'], ['sagittal', 'Sagittal']];
  const EDGE = { axial: { t: 'A', b: 'P', l: 'R', r: 'L' }, coronal: { t: 'S', b: 'I', l: 'R', r: 'L' }, sagittal: { t: 'S', b: 'I', l: 'A', r: 'P' } };
  let LOC = null;

  function renderSpatial() {
    const sp = S.vol.spatial, h = sp.hemisphere, w = sp.regions.all, box = $('#nva-spabody');
    if (LOC) { LOC.stop(); LOC = null; }
    if (!w) { box.innerHTML = '<p class="nva-mut">No labelled voxels.</p>'; return; }
    const pos = clamp(50 + (h.offset_mm / Math.max(h.half_width_mm, 1)) * 50, 3, 97);
    const d = sp.depth;
    box.innerHTML = `<div class="nva-loc" id="nva-loc"></div>
      <div class="nva-side">
        <div class="nva-gauge"><div class="lab"><span>Patient right</span><span>Patient left</span></div><div class="rail"><div class="track"></div><div class="mid"></div><div class="pin" id="nva-pin"></div></div></div>
        <dl class="nva-kv">
          <dt>Hemisphere</dt><dd><b>${esc(h.side)}</b><small>${fmt(h.left_fraction * 100, 0)}% of voxels are left of the midline</small></dd>
          <dt>Midline offset</dt><dd>${fmt(Math.abs(h.offset_mm), 1)} mm ${h.offset_mm >= 0 ? 'left' : 'right'}<small>${esc(h.method)}</small></dd>
          <dt>Centroid (RAS)</dt><dd>${w.centroid_ras_mm.map(x => fmt(x, 1)).join(', ')} mm<small>voxel ${w.centroid_voxel.map(x => fmt(x, 1)).join(', ')}</small></dd>
          <dt>Extent</dt><dd>${fmt(w.extent_mm.LR, 0)} × ${fmt(w.extent_mm.AP, 0)} × ${fmt(w.extent_mm.SI, 0)} mm<small>left-right × front-back × up-down</small></dd>
          <dt>Depth</dt><dd>${d ? fmt(d.nearest_mm, 1) + ' mm nearest · ' + fmt(d.centroid_mm, 1) + ' mm at centroid' : 'n/a'}<small>distance to the surface of the scanned tissue (approximate)</small></dd>
        </dl>
      </div>
      <div class="nva-regtbl nva-tbl" style="max-height:230px"><table><thead><tr><th>Region</th><th>Volume (mL)</th><th>Centroid RAS (mm)</th><th>Extent LR × AP × SI (mm)</th></tr></thead><tbody>${orderKeys().filter(k => sp.regions[k]).map(k => { const g = sp.regions[k]; return `<tr><td><span class="nva-dot" style="--c:${colorOf(k)}"></span><span data-nm="${k}">${esc(nm(k))}</span></td><td>${fmt(S.vol.regions[k].ml, 3)}</td><td>${g.centroid_ras_mm.map(x => fmt(x, 1)).join(', ')}</td><td>${fmt(g.extent_mm.LR, 0)} × ${fmt(g.extent_mm.AP, 0)} × ${fmt(g.extent_mm.SI, 0)}</td></tr>`; }).join('')}</tbody></table></div>`;
    setTimeout(() => { const p = $('#nva-pin'); if (p) p.style.left = pos + '%'; }, 200);
    buildLocator();
  }

  function buildLocator() {
    const L = S.loc, host = $('#nva-loc');
    host.style.gridTemplateColumns = VIEWS.map(([k]) => L.views[k].aspect + 'fr').join(' ');
    host.innerHTML = VIEWS.map(([k, t]) => `<figure class="nva-vw"><canvas data-v="${k}"></canvas><figcaption>${t}</figcaption></figure>`).join('');
    const items = VIEWS.map(([k]) => {
      const v = L.views[k], c = $(`canvas[data-v="${k}"]`, host);
      c.style.aspectRatio = String(v.aspect);
      const off = document.createElement('canvas'); off.width = v.w; off.height = v.h;
      const g = off.getContext('2d'), img = g.createImageData(v.w, v.h);
      for (let i = 0; i < v.w * v.h; i++) {
        const hd = Math.pow(v.head[i] / 255, 0.75), r = v.roi[i] / 255, lab = v.lab[i];
        let R = 34 * (0.1 + 0.55 * hd), G = 211 * (0.1 + 0.55 * hd), B = 238 * (0.1 + 0.55 * hd);
        if (lab > 0) { const [cr, cg, cb] = hex(PAL[(lab - 1) % PAL.length]), a = clamp(0.5 + 0.5 * Math.sqrt(r), 0, 1); R = R * (1 - a) + cr * a; G = G * (1 - a) + cg * a; B = B * (1 - a) + cb * a; }
        img.data[i * 4] = R; img.data[i * 4 + 1] = G; img.data[i * 4 + 2] = B; img.data[i * 4 + 3] = 255;
      }
      g.putImageData(img, 0, 0);
      return { k, c, off };
    });
    const map = (k, x, y, z) => (k === 'axial' ? [x, y] : k === 'coronal' ? [x, 1 - z] : [y, 1 - z]);
    const bb = L.bbox;
    const state = { t0: 0, seen: false, visible: false, raf: 0 };
    const size = () => items.forEach(({ c }) => { const d = window.devicePixelRatio || 1; c.width = Math.max(40, Math.round(c.clientWidth * d)); c.height = Math.max(40, Math.round(c.clientHeight * d)); });
    const ro = new ResizeObserver(size); ro.observe(host); size();

    function draw(it, now) {
      const { k, c, off } = it, W = c.width, H = c.height, g = c.getContext('2d'), u = Math.max(1, W / 300);
      const p = RM ? 1 : ease(clamp((now - state.t0) / 1500, 0, 1));
      g.clearRect(0, 0, W, H); g.imageSmoothingEnabled = true; g.imageSmoothingQuality = 'high'; g.drawImage(off, 0, 0, W, H);
      const [cx0, cy0] = map(k, ...L.centroids.all), cx = cx0 * W, cy = cy0 * H;
      if (k !== 'sagittal') { g.save(); g.setLineDash([5 * u, 5 * u]); g.strokeStyle = `rgba(251,191,36,${0.55 * p})`; g.lineWidth = u; g.beginPath(); g.moveTo(L.mid * W, 0); g.lineTo(L.mid * W, H); g.stroke(); g.restore(); }
      if (p < 1) { const x = p * W, gr = g.createLinearGradient(x - 50 * u, 0, x, 0); gr.addColorStop(0, 'rgba(34,211,238,0)'); gr.addColorStop(1, 'rgba(34,211,238,.5)'); g.fillStyle = gr; g.fillRect(x - 50 * u, 0, 50 * u, H); }
      g.save(); g.setLineDash([4 * u, 4 * u]); g.strokeStyle = 'rgba(255,255,255,.6)'; g.lineWidth = u;
      g.beginPath(); g.moveTo(cx - cx * p, cy); g.lineTo(cx + (W - cx) * p, cy); g.moveTo(cx, cy - cy * p); g.lineTo(cx, cy + (H - cy) * p); g.stroke(); g.restore();
      const bx = k === 'sagittal' ? [bb[2], bb[3]] : [bb[0], bb[1]], by = k === 'axial' ? [bb[2], bb[3]] : [1 - bb[5], 1 - bb[4]];
      const X0 = bx[0] * W, X1 = bx[1] * W, Y0 = by[0] * H, Y1 = by[1] * H, a = 7 * u;
      g.strokeStyle = `rgba(34,211,238,${0.95 * p})`; g.lineWidth = 1.6 * u; g.beginPath();
      [[X0, Y0, 1, 1], [X1, Y0, -1, 1], [X0, Y1, 1, -1], [X1, Y1, -1, -1]].forEach(([x, y, sx, sy]) => { g.moveTo(x + a * sx, y); g.lineTo(x, y); g.lineTo(x, y + a * sy); }); g.stroke();
      if (labelKeys().length > 1) labelKeys().forEach(key => { const [lx, ly] = map(k, ...L.centroids[key]); g.beginPath(); g.fillStyle = colorOf(key); g.strokeStyle = '#fff'; g.lineWidth = u; g.arc(lx * W, ly * H, 3.2 * u, 0, 6.283); g.fill(); g.stroke(); });
      const ph = ((now / 1800) % 1);
      g.beginPath(); g.strokeStyle = `rgba(255,255,255,${(1 - ph) * 0.7 * p})`; g.lineWidth = 1.5 * u; g.arc(cx, cy, (4 + 16 * ph) * u, 0, 6.283); g.stroke();
      g.beginPath(); g.fillStyle = '#fff'; g.shadowColor = '#22d3ee'; g.shadowBlur = 12 * u; g.arc(cx, cy, 3.4 * u, 0, 6.283); g.fill(); g.shadowBlur = 0;
      g.fillStyle = 'rgba(141,162,192,.95)'; g.font = `600 ${10 * u}px ui-monospace,Consolas,monospace`; g.textBaseline = 'middle'; g.textAlign = 'center';
      const e = EDGE[k]; g.fillText(e.t, W / 2, 9 * u); g.fillText(e.b, W / 2, H - 9 * u); g.fillText(e.l, 9 * u, H / 2); g.fillText(e.r, W - 9 * u, H / 2);
    }
    (function frame(now) {
      state.raf = requestAnimationFrame(frame);
      if (!state.visible) return;
      if (!state.seen) { state.seen = true; state.t0 = now; }
      items.forEach(it => draw(it, now));
    })(performance.now());
    const io = new IntersectionObserver(es => { state.visible = es[0].isIntersecting; }, { threshold: 0.15 }); io.observe(host);
    LOC = { stop() { cancelAnimationFrame(state.raf); ro.disconnect(); io.disconnect(); } };
  }
  /* ================= 3D cinema ================= */
  const VS = `
    uniform float uTime,uBuild,uExplode,uScan,uScanOn,uPx; uniform float uShow[12];
    attribute vec3 aStart,aColor,aDir; attribute float aSeed,aRegion,aAlpha;
    varying vec3 vC; varying float vA,vG;
    void main(){
      float t=clamp((uBuild-aSeed*0.55)/0.45,0.,1.); t=t*t*(3.-2.*t);
      vec3 p=mix(aStart,position,t);
      p+=aDir*uExplode;
      p+=position*0.016*sin(uTime*1.4+aSeed*40.);
      float show=uShow[int(aRegion+0.5)];
      float g=uScanOn*smoothstep(.24,0.,abs(p.y-uScan));
      vec4 mv=modelViewMatrix*vec4(p,1.);
      gl_Position=projectionMatrix*mv;
      gl_PointSize=uPx*(1.+g*1.5+(1.-t)*.6)*(5.2/-mv.z)*show;
      vC=aColor; vG=g; vA=aAlpha*show*(.3+.7*t);
    }`;
  const FS = `
    varying vec3 vC; varying float vA,vG;
    void main(){
      float d=length(gl_PointCoord-.5); if(d>.5) discard;
      float a=smoothstep(.5,.0,d);
      gl_FragColor=vec4(vC*(.8+vG*1.5),a*a*vA*(1.+vG)*.9);
    }`;

  let GL = null;
  async function initCinema() {
    const stage = $('#nva-stage'), canvas = $('#nva-gl');
    if (GL) { GL.dispose(); GL = null; }
    $$('.nva-fail', stage).forEach(n => n.remove());
    let THREE;
    try { THREE = await getThree(); }
    catch { stage.insertAdjacentHTML('beforeend', '<div class="nva-fail">The 3D engine could not be loaded.<br>Check your internet connection and reload.</div>'); return; }

    const ac = new AbortController(), sg = { signal: ac.signal };
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    renderer.setClearColor(0x000000, 0);
    const scene = new THREE.Scene(), cam = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
    const P = S.pts, keys = labelKeys(), k = 1.5 / Math.max(P.radius_mm, 1);
    const n = keys.reduce((a, key) => a + P.regions[key].xyz.length / 3, 0), maxCount = Math.max(...keys.map(key => P.regions[key].count), 1);
    const pos = new Float32Array(n * 3), start = new Float32Array(n * 3), col = new Float32Array(n * 3), dir = new Float32Array(n * 3);
    const seed = new Float32Array(n), reg = new Float32Array(n), alp = new Float32Array(n);
    let o = 0; const bmin = [1e9, 1e9, 1e9], bmax = [-1e9, -1e9, -1e9];
    keys.forEach((key, ri) => {
      const a = P.regions[key].xyz, m = a.length / 3, c = new THREE.Color(colorOf(key)), mean = [0, 0, 0];
      const alpha = clamp(0.92 - 0.6 * Math.sqrt(P.regions[key].count / maxCount), 0.3, 0.92);
      for (let i = 0; i < m; i++) {                      // RAS -> three: x=R, y=S, z=-A (keeps handedness)
        const x = a[i * 3] * k, y = a[i * 3 + 2] * k, z = -a[i * 3 + 1] * k, q = (o + i) * 3;
        pos[q] = x; pos[q + 1] = y; pos[q + 2] = z; mean[0] += x; mean[1] += y; mean[2] += z;
        [x, y, z].forEach((v, d) => { bmin[d] = Math.min(bmin[d], v); bmax[d] = Math.max(bmax[d], v); });
        col[q] = c.r; col[q + 1] = c.g; col[q + 2] = c.b;
        const th = Math.random() * 6.283, ph = Math.acos(2 * Math.random() - 1), rr = 2.6 + Math.random() * 3.4;
        start[q] = rr * Math.sin(ph) * Math.cos(th); start[q + 1] = rr * Math.sin(ph) * Math.sin(th); start[q + 2] = rr * Math.cos(ph);
        seed[o + i] = Math.random(); reg[o + i] = ri; alp[o + i] = alpha;
      }
      const ang = ri * 2.399, fb = [Math.cos(ang), Math.sin(ang) * 0.6, Math.sin(ang * 1.7) * 0.6], fl = Math.hypot(...fb);
      let d = m ? mean.map(v => v / m) : fb; const len = Math.hypot(...d);
      d = len > 0.08 ? d.map(v => v / len) : fb.map(v => v / fl);
      for (let i = 0; i < m; i++) { const q = (o + i) * 3; dir[q] = d[0] * 0.95; dir[q + 1] = d[1] * 0.95; dir[q + 2] = d[2] * 0.95; }
      o += m;
    });
    const geo = new THREE.BufferGeometry();
    [['position', pos, 3], ['aStart', start, 3], ['aColor', col, 3], ['aDir', dir, 3], ['aSeed', seed, 1], ['aRegion', reg, 1], ['aAlpha', alp, 1]].forEach(([name, arr, s]) => geo.setAttribute(name, new THREE.BufferAttribute(arr, s)));
    geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 8);
    const U = { uTime: { value: 0 }, uBuild: { value: RM ? 1 : 0 }, uExplode: { value: 0 }, uScan: { value: 0 }, uScanOn: { value: 1 }, uPx: { value: 3 }, uShow: { value: new Float32Array(12).fill(1) } };
    const mat = new THREE.ShaderMaterial({ uniforms: U, vertexShader: VS, fragmentShader: FS, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending });
    const cloud = new THREE.Points(geo, mat); cloud.frustumCulled = false; scene.add(cloud);

    const dn = 900, dp = new Float32Array(dn * 3);
    for (let i = 0; i < dn; i++) { const th = Math.random() * 6.283, ph = Math.acos(2 * Math.random() - 1), rr = 3 + Math.random() * 4; dp[i * 3] = rr * Math.sin(ph) * Math.cos(th); dp[i * 3 + 1] = rr * Math.sin(ph) * Math.sin(th); dp[i * 3 + 2] = rr * Math.cos(ph); }
    const dg = new THREE.BufferGeometry(); dg.setAttribute('position', new THREE.BufferAttribute(dp, 3));
    const dust = new THREE.Points(dg, new THREE.PointsMaterial({ size: 0.022, color: 0x38bdf8, transparent: true, opacity: 0.5, depthWrite: false, blending: THREE.AdditiveBlending }));
    scene.add(dust);
    const ring = new THREE.Mesh(new THREE.RingGeometry(1.52, 1.545, 128), new THREE.MeshBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: 0.55, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false }));
    ring.rotation.x = -Math.PI / 2; scene.add(ring);
    const box = new THREE.Box3Helper(new THREE.Box3(new THREE.Vector3(...bmin), new THREE.Vector3(...bmax)), 0x38bdf8);
    box.material.transparent = true; box.material.opacity = 0.55; box.visible = false; scene.add(box);

    const st = { az: Math.PI + 0.55, el: 0.32, dist: RM ? 4.3 : 8, orbit: true, explode: 0, scan: true, drag: false, last: 0, lastY: 0, t0: performance.now(), clock: 0, show: new Array(12).fill(1), showT: new Array(12).fill(1), running: true, raf: 0 };
    const hud = () => { $('#nva-hud').innerHTML = '1 grid unit = ' + fmt(1 / k, 1) + ' mm<br>' + keys.map(key => `<span data-nm="${key}">${esc(nm(key))}</span> ${P.regions[key].count.toLocaleString()} vox`).join('<br>'); };
    hud();
    $('#nva-legend').innerHTML = keys.map((key, i) => `<button class="nva-lg" data-i="${i}" aria-pressed="true" style="--c:${colorOf(key)}"><i></i><span data-nm="${key}">${esc(nm(key))}</span></button>`).join('');
    $$('.nva-lg').forEach(b => b.addEventListener('click', () => { const i = +b.dataset.i; st.showT[i] = st.showT[i] ? 0 : 1; b.setAttribute('aria-pressed', !!st.showT[i]); }, sg));

    const resize = () => { const w = stage.clientWidth, h = stage.clientHeight, dpr = Math.min(devicePixelRatio || 1, 2); renderer.setPixelRatio(dpr); renderer.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix(); U.uPx.value = 3.2 * dpr * (h / 560); };
    const ro = new ResizeObserver(resize); ro.observe(stage); resize();
    canvas.addEventListener('pointerdown', e => { st.drag = true; st.last = e.clientX; st.lastY = e.clientY; canvas.setPointerCapture(e.pointerId); }, sg);
    canvas.addEventListener('pointermove', e => { if (!st.drag) return; st.az -= (e.clientX - st.last) * 0.008; st.el = clamp(st.el + (e.clientY - st.lastY) * 0.006, -1.4, 1.4); st.last = e.clientX; st.lastY = e.clientY; }, sg);
    const up = () => { st.drag = false; };
    canvas.addEventListener('pointerup', up, sg); canvas.addEventListener('pointercancel', up, sg);
    canvas.addEventListener('wheel', e => { e.preventDefault(); st.dist = clamp(st.dist * Math.exp(e.deltaY * 0.0012), 2.4, 9); }, { passive: false, signal: ac.signal });
    const vis = new IntersectionObserver(es => { st.running = es[0].isIntersecting; }); vis.observe(stage);

    $$('.nva-cin [data-act]').forEach(b => b.addEventListener('click', () => {
      const a = b.dataset.act, on = b.getAttribute('aria-pressed') !== 'true';
      if (a === 'orbit') { st.orbit = on; b.setAttribute('aria-pressed', on); }
      if (a === 'explode') { st.explode = on ? 1 : 0; b.setAttribute('aria-pressed', on); }
      if (a === 'scan') { st.scan = on; b.setAttribute('aria-pressed', on); }
      if (a === 'box') { box.visible = on; b.setAttribute('aria-pressed', on); }
      if (a === 'replay') { st.t0 = performance.now(); st.dist = 8; U.uBuild.value = 0; stage.classList.remove('play'); void stage.offsetWidth; stage.classList.add('play'); }
    }, sg));

    let prev = performance.now();
    (function frame(now) {
      st.raf = requestAnimationFrame(frame);
      if (!st.running) { prev = now; return; }
      const dt = Math.min(0.05, (now - prev) / 1000); prev = now; st.clock += dt;
      const el = (now - st.t0) / 1000;
      if (!RM) { U.uBuild.value = ease(clamp(el / 3.4, 0, 1)); if (el < 3.6) st.dist = 4.3 + 3.7 * (1 - ease(clamp(el / 3.4, 0, 1))); }
      if (st.orbit && !st.drag && !RM) st.az += dt * 0.16;
      U.uTime.value = st.clock;
      U.uExplode.value += (st.explode - U.uExplode.value) * Math.min(1, dt * 3);
      U.uScanOn.value += ((st.scan ? 1 : 0) - U.uScanOn.value) * Math.min(1, dt * 4);
      U.uScan.value = Math.sin(st.clock * 0.9) * 1.75;
      ring.position.y = U.uScan.value; ring.material.opacity = 0.55 * U.uScanOn.value; ring.visible = U.uScanOn.value > 0.02;
      for (let i = 0; i < 12; i++) { st.show[i] += (st.showT[i] - st.show[i]) * Math.min(1, dt * 6); U.uShow.value[i] = st.show[i]; }
      dust.rotation.y -= dt * 0.03;
      cam.position.set(st.dist * Math.cos(st.el) * Math.sin(st.az), st.dist * Math.sin(st.el), st.dist * Math.cos(st.el) * Math.cos(st.az));
      cam.lookAt(0, 0, 0);
      renderer.render(scene, cam);
    })(prev);

    GL = { hud, dispose() { cancelAnimationFrame(st.raf); ac.abort(); ro.disconnect(); vis.disconnect(); geo.dispose(); dg.dispose(); mat.dispose(); renderer.dispose(); } };
    stage.classList.remove('play'); void stage.offsetWidth; stage.classList.add('play');
  }

  const LAB_HTML = `
    <div class="nvs-body">
      <div class="nvs-stage" id="nvs-stage">
        <canvas id="nvs-gl" aria-label="Lit 3D surface of the labelled regions"></canvas>
        <div class="nvs-hud" id="nvs-hud"></div>
        <div class="nvs-scale"><i id="nvs-bar"></i><span id="nvs-bartxt"></span></div>
        <div class="nvs-empty" id="nvs-empty">Loading the surface…</div>
      </div>
      <div class="nvs-ctl">
        <div class="nvs-seg" role="group" aria-label="Colour mode"><button data-cm="0" aria-pressed="true">Region</button><button data-cm="1" aria-pressed="false">Height</button><button data-cm="2" aria-pressed="false">Distance</button><button data-cm="3" aria-pressed="false">Intensity</button></div>
        <label class="nvs-cut">Cut<input type="range" id="nvs-cut" min="0" max="100" value="100" aria-label="Cut plane, bottom to top"></label>
        <button class="nva-btn sm" data-lab="orbit" aria-pressed="true">Orbit</button>
        <button class="nva-btn sm" data-lab="box" aria-pressed="true">Bounds</button>
        <button class="nva-btn sm" data-lab="reset">Replay</button>
        <button class="nva-btn sm" data-lab="png">PNG</button>
      </div>
      <p class="nva-mut" style="font-size:12px;padding:0 16px">Intensity colours show the z-scored voxel value of the sequence chosen at the top of this section (same normalisation as the PyRadiomics input), only on the surface voxels.</p>
      <div class="nvs-legend" id="nvs-legend"></div>
      <div class="nvs-metrics" id="nvs-metrics"></div>
    </div>`;
  let LAB = null;
  const LVS = `
    uniform float uPx,uMode; uniform float uShow[12];
    attribute vec3 aNormal,aColor; attribute float aRad,aHei,aRegion,aVal;
    varying vec3 vN,vC,vW; varying float vY,vS;
    vec3 ramp(float t){
      vec3 a=vec3(.05,.12,.45),b=vec3(.45,.1,.75),c=vec3(.95,.2,.5),d=vec3(1.,.72,.2),e=vec3(1.,.95,.75);
      t=clamp(t,0.,1.)*4.;
      if(t<1.) return mix(a,b,t); if(t<2.) return mix(b,c,t-1.); if(t<3.) return mix(c,d,t-2.); return mix(d,e,t-3.);
    }
    void main(){
      vec4 mv=modelViewMatrix*vec4(position,1.);
      gl_Position=projectionMatrix*mv;
      float show=uShow[int(aRegion+.5)];
      gl_PointSize=uPx*(5.2/-mv.z)*show;
      vN=aNormal; vW=position; vY=position.y; vS=show;
      vC=uMode<.5?aColor:(uMode<1.5?ramp(aHei):(uMode<2.5?ramp(aRad):ramp(aVal)));
    }`;
  const LFS = `
    uniform float uClip; varying vec3 vN,vC,vW; varying float vY,vS;
    void main(){
      if(vY>uClip||vS<.05) discard;
      if(length(gl_PointCoord-.5)>.5) discard;
      vec3 N=normalize(vN), V=normalize(cameraPosition-vW);
      if(dot(N,V)<0.) N=-N;
      vec3 L=normalize(vec3(.5,.8,.6)), H=normalize(L+V);
      float diff=max(dot(N,L),0.), rim=pow(1.-max(dot(N,V),0.),2.2), spec=pow(max(dot(N,H),0.),40.);
      gl_FragColor=vec4(vC*(.2+.9*diff)+vec3(.35,.8,1.)*rim*.55+vec3(1.)*spec*.35,1.);
    }`;

  async function initLab() {
    if (!labRoot || !S.vol) return;
    if (LAB) { LAB.dispose(); LAB = null; }
    const empty = $('#nvs-empty', labRoot);
    LAB = { sel: 'all', dispose() {} };       // metrics work even if WebGL or the CDN is unavailable
    labMetrics();
    if (!S.shell) { empty.textContent = 'The surface data could not be loaded. Rebuild the container.'; empty.hidden = false; return; }
    empty.hidden = true;
    let THREE;
    try { THREE = await getThree(); } catch { empty.textContent = 'The 3D engine could not be loaded. Check your internet connection.'; empty.hidden = false; return; }
    const stage = $('#nvs-stage', labRoot), canvas = $('#nvs-gl', labRoot);
    const ac = new AbortController(), sg = { signal: ac.signal };
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true });
    renderer.setClearColor(0x000000, 0);
    const scene = new THREE.Scene(), cam = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
    const SH = S.shell, keys = labelKeys().filter(key => SH.regions[key]), k = 1.5 / Math.max(SH.radius_mm, 1);
    const n = keys.reduce((a, key) => a + SH.regions[key].xyz.length / 3, 0);
    const pos = new Float32Array(n * 3), nrm = new Float32Array(n * 3), col = new Float32Array(n * 3), rad = new Float32Array(n), hei = new Float32Array(n), reg = new Float32Array(n), val = new Float32Array(n);
    let o = 0, ymin = 1e9, ymax = -1e9, rmax = 1e-6; const bmin = [1e9, 1e9, 1e9], bmax = [-1e9, -1e9, -1e9];
    keys.forEach(key => {
      const a = SH.regions[key].xyz, nn = SH.regions[key].nrm, vv = SH.regions[key].val, m = a.length / 3, c = new THREE.Color(colorOf(key)), ri = S.vol.labels.indexOf(+key.slice(6));
      for (let i = 0; i < m; i++) {                         // RAS -> three: x=R, y=S, z=-A
        const q = (o + i) * 3, x = a[i * 3] * k, y = a[i * 3 + 2] * k, z = -a[i * 3 + 1] * k;
        pos[q] = x; pos[q + 1] = y; pos[q + 2] = z;
        nrm[q] = nn[i * 3]; nrm[q + 1] = nn[i * 3 + 2]; nrm[q + 2] = -nn[i * 3 + 1];
        col[q] = c.r; col[q + 1] = c.g; col[q + 2] = c.b; reg[o + i] = ri; val[o + i] = vv ? vv[i] : 0.5;
        ymin = Math.min(ymin, y); ymax = Math.max(ymax, y); rmax = Math.max(rmax, Math.hypot(x, y, z));
        [x, y, z].forEach((v, d) => { bmin[d] = Math.min(bmin[d], v); bmax[d] = Math.max(bmax[d], v); });
      }
      o += m;
    });
    for (let i = 0; i < n; i++) { rad[i] = Math.hypot(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]) / rmax; hei[i] = (pos[i * 3 + 1] - ymin) / ((ymax - ymin) || 1); }
    const geo = new THREE.BufferGeometry();
    [['position', pos, 3], ['aNormal', nrm, 3], ['aColor', col, 3], ['aRad', rad, 1], ['aHei', hei, 1], ['aRegion', reg, 1], ['aVal', val, 1]].forEach(([name, arr, s]) => geo.setAttribute(name, new THREE.BufferAttribute(arr, s)));
    geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 8);
    const U = { uPx: { value: 6 }, uMode: { value: 0 }, uClip: { value: ymin - 0.2 }, uShow: { value: new Float32Array(12).fill(1) } };
    const mat = new THREE.ShaderMaterial({ uniforms: U, vertexShader: LVS, fragmentShader: LFS });
    const cloud = new THREE.Points(geo, mat); cloud.frustumCulled = false; scene.add(cloud);
    const box = new THREE.Box3Helper(new THREE.Box3(new THREE.Vector3(...bmin), new THREE.Vector3(...bmax)), 0x38bdf8);
    box.material.transparent = true; box.material.opacity = 0.5; scene.add(box);

    const st = { az: Math.PI + 0.6, el: 0.3, dist: 4.4, orbit: true, drag: false, last: 0, lastY: 0, clip: ymin - 0.2, clipT: ymax + 0.05, show: new Array(12).fill(1), showT: new Array(12).fill(1), running: true, raf: 0, sw: 0, sl: 0 };
    const w = S.vol.spatial.regions.all;
    $('#nvs-hud', labRoot).innerHTML = w ? `extent ${fmt(w.extent_mm.LR, 0)} × ${fmt(w.extent_mm.AP, 0)} × ${fmt(w.extent_mm.SI, 0)} mm<br>${S.vol.regions.all.voxels.toLocaleString()} voxels · ${fmt(S.vol.regions.all.ml, 2)} mL` : '';
    $('#nvs-legend', labRoot).innerHTML = labelKeys().map(key => `<button class="nva-lg" data-i="${S.vol.labels.indexOf(+key.slice(6))}" aria-pressed="true" style="--c:${colorOf(key)}"><i></i><span data-nm="${key}">${esc(nm(key))}</span></button>`).join('');
    $$('.nva-lg', labRoot).forEach(b => b.addEventListener('click', () => { const i = +b.dataset.i; st.showT[i] = st.showT[i] ? 0 : 1; b.setAttribute('aria-pressed', !!st.showT[i]); }, sg));
    $$('[data-cm]', labRoot).forEach(b => b.addEventListener('click', () => { U.uMode.value = +b.dataset.cm; $$('[data-cm]', labRoot).forEach(x => x.setAttribute('aria-pressed', x === b)); }, sg));
    $('#nvs-cut', labRoot).value = 100;
    $('#nvs-cut', labRoot).addEventListener('input', e => { const v = +e.target.value; st.clipT = v >= 100 ? ymax + 0.05 : ymin + (ymax - ymin) * v / 100; }, sg);
    const act = {
      orbit: b => { st.orbit = !st.orbit; b.setAttribute('aria-pressed', st.orbit); },
      box: b => { box.visible = !box.visible; b.setAttribute('aria-pressed', box.visible); },
      reset: () => { st.az = Math.PI + 0.6; st.el = 0.3; st.dist = 4.4; st.clip = ymin - 0.2; },
      png: () => { renderer.render(scene, cam); canvas.toBlob(blob => { const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'NeuroVoxel_shape_lab.png'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1500); }); },
    };
    $$('.nvs-ctl [data-lab]', labRoot).forEach(b => b.addEventListener('click', () => act[b.dataset.lab](b), sg));

    const resize = () => { const wd = stage.clientWidth, h = stage.clientHeight, dpr = Math.min(devicePixelRatio || 1, 2); renderer.setPixelRatio(dpr); renderer.setSize(wd, h, false); cam.aspect = wd / h; cam.updateProjectionMatrix(); U.uPx.value = 7.2 * dpr * (h / 560) * Math.sqrt(24000 / Math.max(n, 2000)); };
    const ro = new ResizeObserver(resize); ro.observe(stage); resize();
    canvas.addEventListener('pointerdown', e => { st.drag = true; st.last = e.clientX; st.lastY = e.clientY; canvas.setPointerCapture(e.pointerId); }, sg);
    canvas.addEventListener('pointermove', e => { if (!st.drag) return; st.az -= (e.clientX - st.last) * 0.008; st.el = clamp(st.el + (e.clientY - st.lastY) * 0.006, -1.4, 1.4); st.last = e.clientX; st.lastY = e.clientY; }, sg);
    const up = () => { st.drag = false; };
    canvas.addEventListener('pointerup', up, sg); canvas.addEventListener('pointercancel', up, sg);
    canvas.addEventListener('wheel', e => { e.preventDefault(); st.dist = clamp(st.dist * Math.exp(e.deltaY * 0.0012), 2.2, 9); }, { passive: false, signal: ac.signal });
    const vis = new IntersectionObserver(es => { st.running = es[0].isIntersecting; }); vis.observe(stage);

    let prev = performance.now();
    (function frame(now) {
      st.raf = requestAnimationFrame(frame);
      if (!st.running || stage.offsetParent === null) { prev = now; return; }
      const dt = Math.min(0.05, (now - prev) / 1000); prev = now;
      if (st.orbit && !st.drag && !RM) st.az += dt * 0.2;
      st.clip = RM ? st.clipT : st.clip + (st.clipT - st.clip) * Math.min(1, dt * 2.2); U.uClip.value = st.clip;
      for (let i = 0; i < 12; i++) { st.show[i] += (st.showT[i] - st.show[i]) * Math.min(1, dt * 6); U.uShow.value[i] = st.show[i]; }
      cam.position.set(st.dist * Math.cos(st.el) * Math.sin(st.az), st.dist * Math.sin(st.el), st.dist * Math.cos(st.el) * Math.cos(st.az));
      cam.lookAt(0, 0, 0);
      const pxMm = stage.clientHeight / (2 * Math.tan(cam.fov * Math.PI / 360) * st.dist) * k;
      let len = 200; for (const c of [1, 2, 5, 10, 20, 50, 100, 200]) { if (c * pxMm >= 60) { len = c; break; } }
      const wpx = Math.round(len * pxMm);
      if (wpx !== st.sw || len !== st.sl) { st.sw = wpx; st.sl = len; $('#nvs-bar', labRoot).style.width = wpx + 'px'; $('#nvs-bartxt', labRoot).textContent = len + ' mm'; }
      renderer.render(scene, cam);
    })(prev);

    LAB.dispose = () => { cancelAnimationFrame(st.raf); ac.abort(); ro.disconnect(); vis.disconnect(); geo.dispose(); mat.dispose(); renderer.dispose(); };
  }

  function labMetrics() {
    const box = $('#nvs-metrics', labRoot);
    if (!box || !S.vol || !LAB) return;
    const key = LAB.sel, r = S.vol.regions[key], g = S.vol.spatial.regions[key], sh = S.shell && S.shell.regions[key];
    const exact = S.shell && S.shell.shapes && S.shell.shapes[key] ? S.shell.shapes[key] : null, main = null;
    const get = name => (exact ? exact[name] : null);
    const have = exact, md = get('Maximum3DDiameter');
    const sph = get('Sphericity'), C = 163.4;
    const tiles = [
      ['Volume', fmt(r.ml, 2), 'mL'],
      ['Extent', g ? `${fmt(g.extent_mm.LR, 0)} × ${fmt(g.extent_mm.AP, 0)} × ${fmt(g.extent_mm.SI, 0)}` : '—', 'mm'],
      ['Max diameter', md != null ? fmt(md, 1) : sh && sh.max_diameter_mm ? '≈ ' + fmt(sh.max_diameter_mm, 1) : '—', 'mm'],
      ['Surface area', have ? fmt(get('SurfaceArea'), 0) : '—', 'mm²'],
      ['Surface / volume', have ? fmt(get('SurfaceVolumeRatio'), 3) : '—', ''],
      ['Elongation', have ? fmt(get('Elongation'), 3) : '—', ''],
      ['Flatness', have ? fmt(get('Flatness'), 3) : '—', ''],
    ];
    const opts = orderKeys().map(k2 => `<option value="${k2}" data-nm="${k2}" ${k2 === key ? 'selected' : ''}>${esc(nm(k2))}</option>`).join('');
    box.innerHTML = `<div class="nvs-mhead"><span class="nva-mut">Inspect</span><select id="nvs-sel" aria-label="Region to inspect">${opts}</select>${have ? `<span class="nva-mut" style="font-size:12px">PyRadiomics shape features of this region</span>` : ''}</div>
      <div class="nvs-grid">
        <div class="nvs-m nvs-sph"><svg width="62" height="62" viewBox="0 0 62 62"><circle cx="31" cy="31" r="26" fill="none" stroke="rgba(255,255,255,.08)" stroke-width="6"/><circle id="nvs-ring" cx="31" cy="31" r="26" fill="none" stroke="#22d3ee" stroke-width="6" stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${C}" transform="rotate(-90 31 31)" style="transition:stroke-dashoffset 1.2s cubic-bezier(.2,.8,.2,1);filter:drop-shadow(0 0 5px #22d3ee)"/></svg><div><small>Sphericity</small><b>${sph != null ? fmt(sph, 3) : '—'}</b></div></div>
        ${tiles.map(([a, b, c], i) => `<div class="nvs-m" style="animation-delay:${i * 60}ms"><small>${a}</small><b>${b}</b>${c ? `<u>${c}</u>` : ''}</div>`).join('')}
      </div>
      ${have ? '' : '<p class="nva-mut" style="font-size:12.5px;margin:12px 0 0">Exact shape metrics (PyRadiomics, from the mask only) are computed for every region. Nothing is estimated here except the approximate diameter when a region is too small.</p>'}`;
    $('#nvs-sel', box).onchange = e => { LAB.sel = e.target.value; labMetrics(); };
    if (sph != null) setTimeout(() => { const ring = $('#nvs-ring', box); if (ring) ring.style.strokeDashoffset = C * (1 - clamp(sph, 0, 1)); }, 120);
  }
  window.NeuroVoxelAdvanced = { load, refreshShapes };
})();
