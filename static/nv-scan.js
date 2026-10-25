/* NeuroVoxel waiting screen (shown while a case is calculated).
   Four glass slices (T1, T1c, T2, FLAIR) float in 3D and turn slowly. Each slice is a grid of voxels that shimmer; the same tumour glows on every slice
   (teal = edema, pink = core, orange = enhancing). A scan bar sweeps over one slice at a time, that slice lifts and lights up in its own colour.
   Pure CSS animation + a few lines of JS. The animation also runs when Windows/Chrome has "reduce motion" on, because the site's global rule would otherwise freeze it (it is a short loading screen). Installs itself: only the <script> tag has to be added to index.html. */
(() => {
  const SEQ = [['t1', 'T1', '#38bdf8'], ['t1ce', 'T1c', '#fbbf24'], ['t2', 'T2', '#a78bfa'], ['flair', 'FLAIR', '#34d399']];
  const N = 10;                                     // voxels per side on each slice

  const CSS = `
.busy-card.scan3d{width:min(720px,94vw);padding:20px 24px 26px;border-color:#2b6f8f;box-shadow:0 30px 90px #000c,0 0 70px #22d3ee1c}
.nvs-stage{height:clamp(250px,40vh,340px);border-radius:14px;border:1px solid #22637a;position:relative;overflow:hidden;perspective:1000px;
  background:radial-gradient(ellipse at 50% 55%,#0d4467 0%,#06182c 58%,#030b18 100%)}
.nvs-stage:after{content:"";position:absolute;left:50%;bottom:8%;width:56%;height:14%;transform:translateX(-50%);border-radius:50%;
  background:radial-gradient(ellipse,#22d3ee33,transparent 70%);filter:blur(6px);animation:nvsfloor 3s ease-in-out infinite !important}
.nvs-scene{position:absolute;left:50%;top:50%;width:0;height:0;transform-style:preserve-3d;animation:nvsspin 16s linear infinite !important}
@keyframes nvsspin{from{transform:translateY(-6%) rotateX(62deg) rotateZ(0deg)}to{transform:translateY(-6%) rotateX(62deg) rotateZ(360deg)}}
@keyframes nvsfloor{50%{opacity:.45;transform:translateX(-50%) scale(.9)}}
.nvs-slice{position:absolute;left:-95px;top:-95px;width:190px;height:190px;display:grid;grid-template-columns:repeat(${N},1fr);gap:2px;padding:6px;
  border:1.5px solid color-mix(in srgb,var(--c) 55%,transparent);border-radius:8px;background:color-mix(in srgb,var(--c) 7%,#06182c99);
  transform:translateZ(var(--z));transition:transform .6s cubic-bezier(.2,.8,.2,1),box-shadow .6s,border-color .6s,background .6s;transition-duration:.6s!important}
.nvs-slice.on{transform:translateZ(calc(var(--z) + 26px));border-color:var(--c);background:color-mix(in srgb,var(--c) 16%,#06182ccc);box-shadow:0 0 34px color-mix(in srgb,var(--c) 70%,transparent)}
.nvs-slice i{border-radius:2px;background:var(--c);opacity:.12;animation:nvscell 2.4s ease-in-out infinite !important;animation-delay:var(--d)}
.nvs-slice.on i{opacity:.3}
@keyframes nvscell{50%{opacity:.42}}
.nvs-slice i.e{background:#34d399;opacity:.55}.nvs-slice i.k{background:#f472b6;opacity:.85}.nvs-slice i.h{background:#fb923c;opacity:1;box-shadow:0 0 8px #fb923c}
.nvs-slice i.e,.nvs-slice i.k,.nvs-slice i.h{animation:nvstum 1.4s ease-in-out infinite !important;animation-delay:var(--d)}
@keyframes nvstum{50%{filter:brightness(1.7)}}
.nvs-bar{position:absolute;left:0;right:0;height:3px;top:0;border-radius:2px;background:var(--c);box-shadow:0 0 16px 4px var(--c);opacity:0}
.nvs-slice.on .nvs-bar{opacity:1;animation:nvsbar 1.7s ease-in-out infinite !important}
@keyframes nvsbar{0%{top:2%}50%{top:96%}100%{top:2%}}
.nvs-stage .tag{position:absolute;left:14px;top:12px;font:700 11px ui-monospace,Consolas,monospace;letter-spacing:.08em;color:var(--tc,#34d399);transition:color .4s}
.nvs-stage .tag2{position:absolute;right:14px;top:12px;font:700 11px ui-monospace,Consolas,monospace;letter-spacing:.08em;color:#94a3b8}
.nvs-chips{display:flex;justify-content:center;gap:8px;margin:14px 0 4px}
.nvs-chips span{font:700 10px ui-monospace,Consolas,monospace;letter-spacing:.08em;padding:5px 11px;border-radius:999px;border:1px solid #243b58;color:#94a3b8;transition:.3s}
.nvs-chips span.on{color:#02111f;background:var(--c);border-color:var(--c);box-shadow:0 0 18px var(--c)}
.scan3d h2{margin:8px 0 2px}.scan3d .prog{margin-top:12px}`;

  let active = 0, timer = 0, lastNamed = 0;

  function build() {
    const slices = SEQ.map(([k, , c], idx) => {
      let cells = '';
      for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
        const d = Math.hypot(x - 6.2, y - 3.6);                      // the tumour sits off-centre, same place on every slice
        const cls = d < 1.0 ? 'h' : d < 2.0 ? 'k' : d < 3.1 ? 'e' : '';
        cells += `<i class="${cls}" style="--d:${((x * 7 + y * 13) % 24) / 10}s"></i>`;
      }
      return `<div class="nvs-slice" data-k="${k}" style="--c:${c};--z:${(idx - 1.5) * 52}px">${cells}<span class="nvs-bar"></span></div>`;
    }).join('');
    return `<div class="nvs-stage"><div class="tag" id="nvs-tag"></div><div class="tag2">4 SEQUENCES · 3 REGIONS</div><div class="nvs-scene">${slices}</div></div>
<div class="nvs-chips" id="nvs-chips">${SEQ.map(([k, n, c]) => `<span data-k="${k}" style="--c:${c}">${n}</span>`).join('')}</div>`;
  }

  function setActive(i) {
    active = i;
    document.querySelectorAll('.nvs-slice').forEach((s, n) => s.classList.toggle('on', n === i));
    document.querySelectorAll('#nvs-chips span').forEach((s, n) => s.classList.toggle('on', n === i));
    const tag = document.getElementById('nvs-tag'); if (tag) { tag.textContent = 'READING ' + SEQ[i][1].toUpperCase(); tag.style.setProperty('--tc', SEQ[i][2]); }
  }

  function start() {
    setActive(active); clearInterval(timer);
    timer = setInterval(() => { if (!lastNamed || Date.now() - lastNamed > 4000) setActive((active + 1) % 4); }, 2600);
  }
  function stop() { clearInterval(timer); timer = 0; }
  function hint(msg) {
    const m = /\b(flair|t1ce|t1c|t1|t2)\b/i.exec(msg || ''); if (!m) return;
    let k = m[1].toLowerCase(); if (k === 't1c') k = 't1ce';
    const i = SEQ.findIndex(s => s[0] === k); if (i >= 0) { lastNamed = Date.now(); if (i !== active) setActive(i); }
  }

  function install() {
    const busy = document.getElementById('busy'); if (!busy || busy.dataset.nvs) return;
    const card = busy.querySelector('.busy-card'); if (!card) return;
    busy.dataset.nvs = '1';
    const st = document.createElement('style'); st.textContent = CSS; document.head.appendChild(st);
    card.classList.add('scan3d');
    const holder = document.createElement('div'); holder.innerHTML = build();
    const old = card.querySelector('.scanner');
    if (old) old.replaceWith(...holder.childNodes); else card.prepend(...holder.childNodes);
    const sync = () => { try { if (busy.hidden) stop(); else { start(); hint((document.getElementById('busy-msg') || {}).textContent); } } catch (e) { console.warn('scan animation', e); } };
    new MutationObserver(sync).observe(busy, {attributes: true, attributeFilter: ['hidden']});
    const msg = document.getElementById('busy-msg');
    if (msg) new MutationObserver(() => hint(msg.textContent)).observe(msg, {childList: true, characterData: true, subtree: true});
    sync();
  }
  window.NVScan = {start, stop, hint};          // kept so older nv-app.js hooks (if present) still work
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install); else install();
})();
