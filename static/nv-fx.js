/* NeuroVoxel visuals (decorative only, no interaction needed, nothing follows the mouse).

   1. #signals   page background: a slow 3D neural field spread across the WHOLE page. Neurons drift on their own,
                 connections grow between neighbours and fade away, neurons are born and disappear, and signals
                 travel along the connections.
   2. #voxel-hero hero visual: a tesseract (4D hypercube) turning in four dimensions, so the cube folds through itself
                 while signals race along its edges.
   Both are switched off (one still frame) when the system asks for reduced motion, and pause in hidden tabs. */
(() => {
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const rnd = (a, b) => a + Math.random() * (b - a);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const loops = [];
  let running = true, last = performance.now();
  document.addEventListener('visibilitychange', () => { running = !document.hidden; last = performance.now(); if (running) requestAnimationFrame(tick); });

  function setup(canvas, fit) {
    const g = canvas.getContext('2d'); const o = {c: canvas, g, w: 0, h: 0};
    const resize = () => { const dpr = Math.min(devicePixelRatio || 1, 2); const r = fit(); o.w = r.w; o.h = r.h; canvas.width = r.w * dpr; canvas.height = r.h * dpr; canvas.style.width = r.w + 'px'; canvas.style.height = r.h + 'px'; g.setTransform(dpr, 0, 0, dpr, 0, 0); };
    addEventListener('resize', resize); resize(); o.resize = resize; return o;
  }

  /* ------------------------------------------------------------------ 1. page background */
  const bg = document.getElementById('signals');
  if (bg) {
    const o = setup(bg, () => ({w: innerWidth, h: innerHeight}));
    const g = o.g, COUNT = innerWidth < 700 ? 60 : 110;
    const nodes = [], links = new Map(), pulses = [];
    let time = 0;
    const spawn = (n, fresh) => {
      // stratified placement: the field is divided into cells so neurons spread over the whole page instead of clumping
      n.x = rnd(-1.15, 1.15); n.y = rnd(-1.15, 1.15); n.z = rnd(0, 1);
      n.a = rnd(0, 6.283); n.sp = rnd(.012, .03); n.tilt = rnd(-.6, .6);
      n.hue = Math.random() < .75 ? rnd(185, 200) : rnd(245, 265); n.size = rnd(1.1, 2.3); n.flash = 0;
      n.age = fresh ? 0 : rnd(0, 20); n.life = rnd(18, 40); n.fade = 0; return n;
    };
    for (let i = 0; i < COUNT; i++) nodes.push(spawn({}, false));
    function project(n, camX, camY) {
      const f = 1 / (1 + (1 - n.z) * .55);                       // gentle perspective: far (z small) = smaller, closer to the centre
      const asp = o.w / o.h, scale = Math.max(o.w, o.h * 1.15) / 2.05;
      n.px = o.w / 2 + (n.x + camX * (1 - n.z) * .35) * scale * f * (asp > 1 ? 1 : asp * 1.2);
      n.py = o.h / 2 + (n.y + camY * (1 - n.z) * .35) * scale * f * (asp > 1 ? asp * .55 : 1);
      n.f = f;
    }
    function frame(dt) {
      time += dt;
      const camX = Math.sin(time * .05) * .6 + Math.sin(time * .021) * .4, camY = Math.cos(time * .037) * .5;   // slow automatic camera sway
      g.clearRect(0, 0, o.w, o.h);
      for (const n of nodes) {
        n.age += dt;
        n.a += Math.sin(time * .2 + n.tilt * 7) * dt * .25 + n.tilt * dt * .05;                 // direction slowly wanders
        n.x += Math.cos(n.a) * n.sp * dt * 3.2; n.y += Math.sin(n.a) * n.sp * dt * 3.2;
        n.z = clamp(n.z + Math.sin(time * .1 + n.a) * dt * .01, .02, 1);
        if (n.x > 1.3) n.x = -1.3; if (n.x < -1.3) n.x = 1.3; if (n.y > 1.3) n.y = -1.3; if (n.y < -1.3) n.y = 1.3;
        // birth and death: fade in, live, fade out, reappear somewhere else
        const into = clamp(n.age / 2.5, 0, 1), out = clamp((n.life - n.age) / 3, 0, 1); n.fade = Math.min(into, out);
        if (n.age > n.life) spawn(n, true);
        n.flash = Math.max(0, n.flash - dt * 1.4);
        project(n, camX, camY);
      }
      // gentle separation so neurons never pile up
      for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j], dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
        if (d2 < .035 && d2 > 1e-6) { const d = Math.sqrt(d2), p = (.19 - d) * dt * .35; a.x += dx / d * p; a.y += dy / d * p; b.x -= dx / d * p; b.y -= dy / d * p; }
      }
      // connections: each neuron links to its few nearest neighbours; links grow in and melt away
      const maxD = Math.min(o.w, o.h) * .26 * (o.w < 700 ? 1.3 : 1), want = new Set();
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i]; if (a.fade < .3) continue;
        const near = [];
        for (let j = 0; j < nodes.length; j++) { if (i === j) continue; const b = nodes[j]; if (b.fade < .3) continue; const d = Math.hypot(a.px - b.px, a.py - b.py); if (d < maxD) near.push([d, j]); }
        near.sort((p, q) => p[0] - q[0]);
        for (const [, j] of near.slice(0, 3)) want.add(i < j ? i * 1000 + j : j * 1000 + i);
      }
      for (const k of want) if (!links.has(k)) links.set(k, {k, a: nodes[(k / 1000) | 0], b: nodes[k % 1000], life: 0});
      for (const [k, l] of links) {
        if (want.has(k)) l.life = Math.min(1, l.life + dt * .9); else l.life -= dt * 1.1;
        const fadeN = Math.min(l.a.fade, l.b.fade);
        if (l.life <= 0 || fadeN <= 0.02) { if (!want.has(k) || fadeN <= .02) { links.delete(k); continue; } }
        const d = Math.hypot(l.a.px - l.b.px, l.a.py - l.b.py), alpha = l.life * fadeN * (1 - d / (maxD * 1.15)) * .42 * ((l.a.f + l.b.f) * .55);
        if (alpha < .012) continue;
        g.strokeStyle = `rgba(70,205,238,${alpha.toFixed(3)})`; g.lineWidth = .7 + (l.a.f + l.b.f) * .25;
        g.beginPath(); g.moveTo(l.a.px, l.a.py); g.lineTo(l.b.px, l.b.py); g.stroke();
      }
      // signals
      if (links.size && pulses.length < 34 && Math.random() < dt * 9) {
        const arr = [...links.values()], l = arr[(Math.random() * arr.length) | 0];
        if (l.life > .8) pulses.push({l, dir: Math.random() < .5 ? 1 : -1, t: 0, speed: rnd(.35, .8), hue: Math.random() < .22 ? 275 : 186});
      }
      for (let i = pulses.length - 1; i >= 0; i--) {
        const p = pulses[i]; p.t += dt * p.speed;
        if (p.t >= 1 || links.get(p.l.k) !== p.l) {
          if (p.t >= 1) {
            const dest = p.dir > 0 ? p.l.b : p.l.a; dest.flash = 1;
            if (pulses.length < 34 && Math.random() < .6) {
              const next = [...links.values()].filter(q => q !== p.l && q.life > .7 && (q.a === dest || q.b === dest));
              if (next.length) { const q = next[(Math.random() * next.length) | 0]; pulses.push({l: q, dir: q.a === dest ? 1 : -1, t: 0, speed: p.speed, hue: p.hue}); }
            }
          }
          pulses.splice(i, 1); continue;
        }
        const f = p.dir > 0 ? p.t : 1 - p.t, a = p.l.a, b = p.l.b;
        const x = a.px + (b.px - a.px) * f, y = a.py + (b.py - a.py) * f, tail = .14, f0 = p.dir > 0 ? Math.max(0, f - tail) : Math.min(1, f + tail);
        const x0 = a.px + (b.px - a.px) * f0, y0 = a.py + (b.py - a.py) * f0, fadeN = Math.min(a.fade, b.fade);
        const lg = g.createLinearGradient(x0, y0, x, y); lg.addColorStop(0, `hsla(${p.hue},100%,70%,0)`); lg.addColorStop(1, `hsla(${p.hue},100%,75%,${(.85 * fadeN).toFixed(3)})`);
        g.strokeStyle = lg; g.lineWidth = 1.8; g.beginPath(); g.moveTo(x0, y0); g.lineTo(x, y); g.stroke();
        const r = 5 + (a.f + b.f) * 2.5, gr = g.createRadialGradient(x, y, 0, x, y, r);
        gr.addColorStop(0, `hsla(${p.hue},100%,82%,${(.9 * fadeN).toFixed(3)})`); gr.addColorStop(1, `hsla(${p.hue},100%,60%,0)`);
        g.fillStyle = gr; g.beginPath(); g.arc(x, y, r, 0, 6.2832); g.fill();
      }
      // neurons
      for (const n of nodes) {
        if (n.fade < .02) continue;
        const r = n.size * (.55 + n.f * .8) + n.flash * 2.2, al = clamp((.28 + n.f * .5) * n.fade + n.flash * .5, 0, 1);
        if (n.flash > .03) {
          const gr = g.createRadialGradient(n.px, n.py, 0, n.px, n.py, r * 6);
          gr.addColorStop(0, `hsla(${n.hue},100%,70%,${(.45 * n.flash * n.fade).toFixed(3)})`); gr.addColorStop(1, `hsla(${n.hue},100%,60%,0)`);
          g.fillStyle = gr; g.beginPath(); g.arc(n.px, n.py, r * 6, 0, 6.2832); g.fill();
        }
        g.fillStyle = `hsla(${n.hue},90%,${66 + n.flash * 18}%,${al.toFixed(3)})`; g.beginPath(); g.arc(n.px, n.py, r, 0, 6.2832); g.fill();
      }
    }
    loops.push({dt: frame, stills: 120});
  }

  /* ------------------------------------------------------------------ 2. hero: a rotating tesseract (4D hypercube) */
  const hero = document.getElementById('voxel-hero');
  if (hero) {
    const o = setup(hero, () => { const r = hero.parentElement.getBoundingClientRect(); const s = Math.max(260, Math.min(r.width || 460, 540)); return {w: s, h: s}; });
    const g = o.g, V = [], E = [];
    for (let i = 0; i < 16; i++) V.push([i & 1 ? 1 : -1, i & 2 ? 1 : -1, i & 4 ? 1 : -1, i & 8 ? 1 : -1]);
    for (let i = 0; i < 16; i++) for (let b = 0; b < 4; b++) { const j = i ^ (1 << b); if (j > i) E.push([i, j]); }
    const P = V.map(() => ({x: 0, y: 0, z: 0, w: 0, sx: 0, sy: 0, f: 1, flash: 0}));
    const pulses = []; let t = 0, nextPulse = .2;
    const rot = (p, a1, a2, ang) => { const c = Math.cos(ang), s = Math.sin(ang), u = p[a1], v = p[a2]; p[a1] = u * c - v * s; p[a2] = u * s + v * c; };
    function frame(dt) {
      t += dt; nextPulse -= dt;
      // the "movie" motion: the XW rotation turns the cube inside out; a slower YZ / ZW / XY drift keeps it from ever repeating exactly
      const axw = t * .62 + Math.sin(t * .21) * .5, ayz = t * .23, azw = t * .31 + Math.sin(t * .13), axy = t * .11;
      const cx = o.w / 2, cy = o.h / 2, S = o.w * .125;
      for (let i = 0; i < 16; i++) {
        const p = V[i].slice();
        rot(p, 0, 3, axw); rot(p, 1, 2, ayz); rot(p, 2, 3, azw); rot(p, 0, 1, axy);
        const kw = 1 / Math.max(.35, 2.9 - p[3]);                         // 4D -> 3D perspective (what makes the inner cube swell and the outer one shrink)
        const x3 = p[0] * kw * 2.9, y3 = p[1] * kw * 2.9, z3 = p[2] * kw * 2.9;
        const kz = 1 / Math.max(.4, 1 - z3 * .16);                        // 3D -> 2D perspective
        const q = P[i]; q.w = p[3]; q.z = z3; q.f = kz; q.sx = cx + x3 * S * kz; q.sy = cy + y3 * S * kz; q.flash = Math.max(0, q.flash - dt * 1.5);
      }
      g.clearRect(0, 0, o.w, o.h);
      const halo = g.createRadialGradient(cx, cy, 0, cx, cy, o.w * .55); halo.addColorStop(0, 'rgba(60,140,255,.13)'); halo.addColorStop(.6, 'rgba(120,80,255,.05)'); halo.addColorStop(1, 'rgba(0,0,0,0)');
      g.fillStyle = halo; g.fillRect(0, 0, o.w, o.h);
      g.globalCompositeOperation = 'lighter'; g.lineCap = 'round';
      const order = E.map((e, k) => k).sort((a, b) => (P[E[a][0]].z + P[E[a][1]].z) - (P[E[b][0]].z + P[E[b][1]].z));
      for (const k of order) {
        const [i, j] = E[k], a = P[i], b = P[j], wavg = (a.w + b.w) / 2, hue = 190 + (wavg + 2) * 22, depth = clamp(((a.z + b.z) / 2 + 3) / 6, 0, 1), f = (a.f + b.f) / 2;
        const al = .35 + depth * .5;
        g.strokeStyle = `hsla(${hue},100%,60%,${(al * .16).toFixed(3)})`; g.lineWidth = 7 * f; g.beginPath(); g.moveTo(a.sx, a.sy); g.lineTo(b.sx, b.sy); g.stroke();      // soft glow
        g.strokeStyle = `hsla(${hue},100%,${68 + depth * 14}%,${al.toFixed(3)})`; g.lineWidth = 1.5 * f + .4; g.beginPath(); g.moveTo(a.sx, a.sy); g.lineTo(b.sx, b.sy); g.stroke();  // core line
      }
      // signals racing along the edges
      if (nextPulse <= 0 && pulses.length < 7) { const e = E[(Math.random() * E.length) | 0]; pulses.push({e, dir: Math.random() < .5 ? 1 : -1, t: 0, sp: rnd(.55, 1.1), hue: Math.random() < .3 ? 295 : 175}); nextPulse = rnd(.18, .5); }
      for (let i = pulses.length - 1; i >= 0; i--) {
        const p = pulses[i]; p.t += dt * p.sp; const [ia, ib] = p.e, a = P[ia], b = P[ib];
        if (p.t >= 1) { (p.dir > 0 ? b : a).flash = 1;
          const dest = p.dir > 0 ? ib : ia, nexts = E.filter(e => e !== p.e && (e[0] === dest || e[1] === dest));
          if (pulses.length < 8 && Math.random() < .7) { const e = nexts[(Math.random() * nexts.length) | 0]; pulses.push({e, dir: e[0] === dest ? 1 : -1, t: 0, sp: p.sp, hue: p.hue}); }
          pulses.splice(i, 1); continue; }
        const f = p.dir > 0 ? p.t : 1 - p.t, x = a.sx + (b.sx - a.sx) * f, y = a.sy + (b.sy - a.sy) * f, f0 = p.dir > 0 ? Math.max(0, f - .22) : Math.min(1, f + .22);
        const x0 = a.sx + (b.sx - a.sx) * f0, y0 = a.sy + (b.sy - a.sy) * f0, lg = g.createLinearGradient(x0, y0, x, y);
        lg.addColorStop(0, `hsla(${p.hue},100%,70%,0)`); lg.addColorStop(1, `hsla(${p.hue},100%,80%,.95)`);
        g.strokeStyle = lg; g.lineWidth = 2.8; g.beginPath(); g.moveTo(x0, y0); g.lineTo(x, y); g.stroke();
        const gr = g.createRadialGradient(x, y, 0, x, y, 13); gr.addColorStop(0, `hsla(${p.hue},100%,85%,.9)`); gr.addColorStop(1, `hsla(${p.hue},100%,60%,0)`); g.fillStyle = gr; g.beginPath(); g.arc(x, y, 13, 0, 6.2832); g.fill();
      }
      // vertices
      for (const q of P) {
        const depth = clamp((q.z + 3) / 6, 0, 1), r = (2.4 + depth * 2.6) * q.f + q.flash * 4, hue = 190 + (q.w + 2) * 22;
        const gr = g.createRadialGradient(q.sx, q.sy, 0, q.sx, q.sy, r * 4); gr.addColorStop(0, `hsla(${hue},100%,75%,${(.45 + q.flash * .5).toFixed(3)})`); gr.addColorStop(1, `hsla(${hue},100%,60%,0)`);
        g.fillStyle = gr; g.beginPath(); g.arc(q.sx, q.sy, r * 4, 0, 6.2832); g.fill();
        g.fillStyle = `hsla(${hue},100%,${82 + q.flash * 12}%,${(.7 + depth * .3).toFixed(3)})`; g.beginPath(); g.arc(q.sx, q.sy, r, 0, 6.2832); g.fill();
      }
      g.globalCompositeOperation = 'source-over';
    }
    loops.push({dt: frame, stills: 90});
  }

  /* ------------------------------------------------------------------ shared loop */
  function tick(now) {
    if (!running) return;
    const dt = Math.min(.05, (now - last) / 1000); last = now;
    for (const l of loops) l.dt(dt);
    requestAnimationFrame(tick);
  }
  if (reduce) { loops.forEach(l => { for (let i = 0; i < l.stills; i++) l.dt(.05); }); }
  else requestAnimationFrame(tick);
})();
