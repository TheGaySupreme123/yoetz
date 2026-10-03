// FinishUP particle artwork: a few thousand points that rearrange, like atoms, into
// each scene of the story (the words agents do, a clock, a swarm of agents, a robot that
// fades and is checked back to green, a sun that rises with the score, the lockup). The
// page tells the engine which scene is in view and how far through it the reader is; the
// engine does the rest on a 2D canvas with a light 3D projection.

const PALETTE = {
  particle: "#064E3B",
  signal: "#10B981",
  faint: "#8FA39B",
};

const HERO_WORDS = ["work", "code", "research", "writing", "reviews"];
const HERO_SECONDS = 2.2;

export function mountParticles(canvas, options = {}) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return { setScene() {}, setAnchor() {}, destroy() {} };
  const reduced = !!options.reducedMotion;
  const N = Math.max(800, Math.min(9000, options.count || 5200));
  const sun = options.sun || "#E9B200";

  const pos = new Float32Array(N * 3);
  const tgt = new Float32Array(N * 3);
  const col = new Uint8Array(N);
  const tcol = new Uint8Array(N);
  const px = new Float32Array(N);
  const py = new Float32Array(N);
  const ps = new Float32Array(N);
  const pa = new Float32Array(N);
  const R = new Float32Array(N * 6);

  let seed = 20260603;
  for (let i = 0; i < N * 6; i++) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    R[i] = seed / 4294967296;
  }
  for (let i = 0; i < N; i++) {
    pos[i * 3] = (R[i * 6] - 0.5) * 3.2;
    pos[i * 3 + 1] = (R[i * 6 + 1] - 0.5) * 3.2;
    pos[i * 3 + 2] = (R[i * 6 + 2] - 0.5) * 3.2;
  }

  let cache = {};
  const off = document.createElement("canvas");
  off.width = 900;
  off.height = 300;

  let scene = { form: "hero", phase: 0 };
  let anchor = null; // canvas-pixel position of the word "computer" in the closing copy
  let anchorN = { x: 0.32, y: 0.52 };
  let tilt = 0;
  let ox = 0;
  let mx = 0;
  let smx = 0;
  let visible = true;
  let raf = 0;
  const t0 = performance.now();

  const set = (i, x, y, z, c) => {
    tgt[i * 3] = x;
    tgt[i * 3 + 1] = y;
    tgt[i * 3 + 2] = z;
    tcol[i] = c;
  };
  const scatter = (i) => set(i, (R[i * 6] - 0.5) * 2.8, (R[i * 6 + 1] - 0.5) * 2.8, (R[i * 6 + 2] - 0.5) * 2.8, 4);
  const fromSet = (i, pts, sc, dx, dy, zj, c) => {
    const n = pts.length / 3;
    if (!n) return scatter(i);
    const j = Math.floor(R[i * 6 + 3] * n) * 3;
    set(i, pts[j] * sc + dx, pts[j + 1] * sc + dy, (R[i * 6 + 4] - 0.5) * zj, c == null ? (pts[j + 2] ? 1 : 0) : c);
  };
  const sphere = (i, cx, cy, cz, r, c) => {
    const u = R[i * 6] * 2 - 1;
    const ph = R[i * 6 + 1] * 6.2832;
    const rr = r * Math.cbrt(R[i * 6 + 2]);
    const q = Math.sqrt(1 - u * u);
    set(i, cx + rr * q * Math.cos(ph), cy + rr * q * Math.sin(ph), cz + rr * u, c);
  };
  const ring = (i, cx, cy, r, w, c, zj) => {
    const a = R[i * 6] * 6.2832;
    const rr = r + (R[i * 6 + 1] - 0.5) * w;
    set(i, cx + rr * Math.cos(a), cy + rr * Math.sin(a), (R[i * 6 + 2] - 0.5) * zj, c);
  };
  const seg = (i, x1, y1, x2, y2, w, c, z) => {
    const u = R[i * 6];
    const o = (R[i * 6 + 1] - 0.5) * w;
    const dx = x2 - x1;
    const dy = y2 - y1;
    const L = Math.hypot(dx, dy) || 1;
    set(i, x1 + dx * u - (dy / L) * o, y1 + dy * u + (dx / L) * o, (z || 0) + (R[i * 6 + 2] - 0.5) * 0.05, c);
  };
  const clock = (i, f, cx, cy, r, hA, mA) => {
    if (f < 0.56) ring(i, cx, cy, r, r * 0.07, 0, 0.08);
    else if (f < 0.72) {
      const k = Math.floor(R[i * 6 + 5] * 12);
      const a = (k / 12) * 6.2832;
      seg(i, cx + Math.cos(a) * r * 0.84, cy + Math.sin(a) * r * 0.84, cx + Math.cos(a) * r * 0.91, cy + Math.sin(a) * r * 0.91, r * 0.035, 0);
    } else if (f < 0.85) seg(i, cx, cy, cx + Math.cos(hA) * r * 0.5, cy + Math.sin(hA) * r * 0.5, r * 0.07, 0);
    else seg(i, cx, cy, cx + Math.cos(mA) * r * 0.78, cy + Math.sin(mA) * r * 0.78, r * 0.045, 0);
  };
  // A laptop the size of a word, drawn where the closing copy says "computer".
  const laptop = (i, f, ax, ay, s, c) => {
    const w = 0.5 * s, h = 0.34 * s;
    const q = (f * 5) | 0;
    if (q === 0) seg(i, ax - w, ay - h, ax + w, ay - h, 0.03 * s, c);
    else if (q === 1) seg(i, ax + w, ay - h, ax + w, ay + h * 0.75, 0.03 * s, c);
    else if (q === 2) seg(i, ax - w, ay + h * 0.75, ax - w, ay - h, 0.03 * s, c);
    else if (q === 3) seg(i, ax - w, ay + h * 0.75, ax + w, ay + h * 0.75, 0.03 * s, c);
    else seg(i, ax - w * 1.25, ay + h, ax + w * 1.25, ay + h, 0.05 * s, c);
  };

  function sample(key, draw) {
    if (cache[key]) return cache[key];
    const W = off.width;
    const H = off.height;
    const c = off.getContext("2d");
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.clearRect(0, 0, W, H);
    draw(c, W, H);
    const d = c.getImageData(0, 0, W, H).data;
    const pts = [];
    for (let y = 0; y < H; y += 2) {
      for (let x = 0; x < W; x += 2) {
        const k = (y * W + x) * 4;
        if (d[k + 3] > 120) pts.push((x - W / 2) / (W / 2), (y - H / 2) / (W / 2), d[k + 1] > 128 ? 1 : 0);
      }
    }
    cache[key] = pts;
    return pts;
  }
  const wordSet = (w) =>
    sample("w:" + w, (c, W, H) => {
      let size = 200;
      c.font = `600 ${size}px "Geist", system-ui, sans-serif`;
      const m = c.measureText(w).width;
      if (m > 800) {
        size = Math.floor((size * 800) / m);
        c.font = `600 ${size}px "Geist", system-ui, sans-serif`;
      }
      c.textAlign = "center";
      c.textBaseline = "middle";
      c.fillStyle = "#000";
      c.fillText(w, W / 2, H / 2 + 4);
    });
  const logoSet = () =>
    sample("logo", (c) => {
      const x = 50, y = 95, w = 140, h = 140, r = 26;
      c.lineCap = "round";
      c.lineJoin = "round";
      c.strokeStyle = "#000";
      c.lineWidth = 32;
      c.beginPath();
      c.moveTo(x + w, y + 70);
      c.arcTo(x + w, y + h, x, y + h, r);
      c.arcTo(x, y + h, x, y, r);
      c.arcTo(x, y, x + w, y, r);
      c.lineTo(x + w - 58, y);
      c.stroke();
      c.strokeStyle = "#00ff00";
      c.lineWidth = 28;
      c.beginPath();
      c.moveTo(x + 92, y + 12);
      c.lineTo(x + 124, y + 46);
      c.lineTo(x + 196, y - 30);
      c.stroke();
      c.font = '600 128px "Geist", system-ui, sans-serif';
      c.textAlign = "left";
      c.textBaseline = "middle";
      c.fillStyle = "#000";
      c.fillText("FinishUP", 280, 168);
    });
  const figureSet = () =>
    sample("figure", (c, W, H) => {
      const cx = W / 2, cy = H / 2;
      c.fillStyle = "#000";
      c.beginPath();
      c.arc(cx, cy - 78, 34, 0, 6.3);
      c.fill();
      c.fillRect(cx - 11, cy - 46, 22, 22);
      c.beginPath();
      c.moveTo(cx - 62, cy - 10);
      c.quadraticCurveTo(cx, cy - 44, cx + 62, cy - 10);
      c.lineTo(cx + 74, cy + 118);
      c.lineTo(cx - 74, cy + 118);
      c.closePath();
      c.fill();
    });
  const rrect = (c, x, y, w, h, r) => {
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
    c.fill();
  };
  const robotSet = () =>
    sample("robot", (c, W) => {
      const cx = W / 2;
      c.fillStyle = "#000";
      c.beginPath();
      c.arc(cx, 34, 9, 0, 6.3);
      c.fill();
      c.fillRect(cx - 3, 40, 6, 16);
      rrect(c, cx - 56, 56, 112, 86, 18);
      c.clearRect(cx - 34, 84, 24, 20);
      c.clearRect(cx + 10, 84, 24, 20);
      c.fillRect(cx - 10, 142, 20, 14);
      rrect(c, cx - 66, 156, 132, 108, 16);
      rrect(c, cx - 104, 166, 30, 82, 12);
      rrect(c, cx + 74, 166, 30, 82, 12);
      rrect(c, cx - 44, 266, 34, 30, 8);
      rrect(c, cx + 10, 266, 34, 30, 8);
    });

  function build(name, t, phase) {
    const spec = { tilt: 0, ox: 0, yawAmp: 0.26, mouse: true };
    let pts, i, f, k, a;
    switch (name) {
      case "logo":
      case "end": {
        // Lockup lifted above the closing copy; the laptop sits on the word "computer".
        pts = logoSet();
        spec.yawAmp = 0.08;
        spec.mouse = false;
        for (i = 0; i < N; i++) {
          f = i / N;
          if (f < 0.96) fromSet(i, pts, 0.78, 0, -0.52, 0.12, null);
          else sphere(i, 0, -0.52, 0, 1.1, 1);
        }
        break;
      }
      case "alone":
        pts = figureSet();
        spec.ox = -0.08;
        for (i = 0; i < N; i++) {
          f = i / N;
          if (f < 0.3) fromSet(i, pts, 1.1, -0.58, 0.04, 0.12, 0);
          else clock(i, (f - 0.3) / 0.7, 0.36, 0, 0.44, -1.5708 + t * 0.05, -1.5708 + t * 0.35);
        }
        break;
      case "swarm":
        pts = figureSet();
        spec.ox = -0.08;
        for (i = 0; i < N; i++) {
          f = i / N;
          if (f < 0.22) fromSet(i, pts, 0.85, 0, 0.1, 0.12, 0);
          else if (f < 0.4) clock(i, (f - 0.22) / 0.18, 0.66, -0.46, 0.2, -1.5708 + t * 0.5, -1.5708 + t * 5);
          else {
            k = Math.floor(((f - 0.4) / 0.6) * 8);
            a = (k / 8) * 6.2832 + t * 0.35;
            sphere(i, Math.cos(a) * 0.64, 0.06 + Math.sin(t * 0.8 + k) * 0.08, Math.sin(a) * 0.64, 0.09, R[i * 6 + 5] < 0.06 ? 1 : 0);
          }
        }
        break;
      case "robot": {
        // One scene over three screens: the agent fades (0 to 0.33), FinishUP's check
        // arrives (0.33 to 0.66), the agent comes back greener than it started (0.66 to 1).
        pts = robotSet();
        spec.ox = -0.12;
        spec.yawAmp = 0.2;
        const fade = Math.max(0, Math.min(1, phase / 0.33));
        const check = Math.max(0, Math.min(1, (phase - 0.33) / 0.33));
        const heal = Math.max(0, Math.min(1, (phase - 0.66) / 0.34));
        const gone = fade * (1 - heal);
        for (i = 0; i < N; i++) {
          f = i / N;
          if (f < 0.88) {
            fromSet(i, pts, 2.1, 0, 0.02, 0.14, 0);
            const r5 = R[i * 6 + 5];
            if (heal > 0 && tgt[i * 3 + 1] > 0.75 - heal * 1.6) tcol[i] = 1;
            else if (r5 < gone * 0.42) {
              const drift = (r5 / Math.max(0.001, gone * 0.42)) * 0.3 + 0.1;
              tgt[i * 3] += (R[i * 6 + 2] - 0.5) * drift * gone;
              tgt[i * 3 + 1] -= (0.3 + R[i * 6 + 4] * 0.6) * drift * gone;
              tgt[i * 3 + 2] += (R[i * 6 + 3] - 0.5) * 0.4 * gone;
              tcol[i] = 4;
            }
          } else if (check > 0) {
            const q = (f - 0.88) / 0.12;
            if (q < 0.4) seg(i, 0.5, 0.06, 0.66, 0.24, 0.07, 1, -0.3);
            else if (check > 0.45) seg(i, 0.66, 0.24, 1.0, -0.2, 0.07, 1, -0.3);
            else sphere(i, 0.66, 0.24, -0.3, 0.05, 1);
          } else sphere(i, 0.75, 0, 0, 1.3, 4);
        }
        break;
      }
      case "sun": {
        spec.yawAmp = 0.05;
        spec.ox = -0.16;
        const y0 = 0.38;
        const sy = 0.42 - phase * 1.0;
        for (i = 0; i < N; i++) {
          f = i / N;
          if (f < 0.28) {
            set(i, (R[i * 6] - 0.5) * 2.3, y0 + (R[i * 6 + 1] - 0.5) * 0.03, (R[i * 6 + 2] - 0.5) * 0.5, 0);
            continue;
          }
          if (f < 0.66) sphere(i, 0, sy, 0, 0.17, 2);
          else {
            a = R[i * 6] * 6.2832;
            const rr = 0.2 + R[i * 6 + 1] * 0.32 * (0.55 + 0.45 * Math.sin(a * 9 + t * 1.5));
            set(i, Math.cos(a) * rr, sy + Math.sin(a) * rr, (R[i * 6 + 2] - 0.5) * 0.1, 3);
          }
          if (tgt[i * 3 + 1] > y0) set(i, (R[i * 6 + 3] - 0.5) * 2.3, y0 + (R[i * 6 + 4] - 0.5) * 0.03, (R[i * 6 + 5] - 0.5) * 0.5, 0);
        }
        break;
      }
      case "hero":
      default: {
        // The word between "We use agents to do" and "FinishUP makes sure they do it."
        const w = HERO_WORDS[Math.floor(t / HERO_SECONDS) % HERO_WORDS.length];
        pts = wordSet(w);
        spec.yawAmp = 0.12;
        for (i = 0; i < N; i++) {
          if (R[i * 6 + 5] < 0.03) sphere(i, 0, -0.04, 0, 1.0, 1);
          else fromSet(i, pts, 0.8, 0, -0.04, 0.14, 0);
        }
      }
    }
    return spec;
  }

  function frame() {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const cw = canvas.clientWidth;
    const ch = canvas.clientHeight;
    if (!cw || !ch) return;
    if (canvas.width !== Math.round(cw * dpr) || canvas.height !== Math.round(ch * dpr)) {
      canvas.width = Math.round(cw * dpr);
      canvas.height = Math.round(ch * dpr);
    }
    const t = (performance.now() - t0) / 1000;
    const S = cw < 720 ? cw * 0.42 : Math.min(cw, ch) * 0.46;
    if (anchor) anchorN = { x: (anchor.x - cw / 2 - ox * cw) / S, y: (anchor.y - ch / 2) / S };
    const spec = build(scene.form, t, Math.max(0, Math.min(1, scene.phase || 0)));
    for (let i = 0; i < N; i++) {
      const k = 0.04 + R[i * 6 + 4] * 0.07;
      const j = i * 3;
      const dx = tgt[j] - pos[j];
      const dy = tgt[j + 1] - pos[j + 1];
      const dz = tgt[j + 2] - pos[j + 2];
      pos[j] += dx * k;
      pos[j + 1] += dy * k;
      pos[j + 2] += dz * k;
      if (dx * dx + dy * dy + dz * dz < 0.02) col[i] = tcol[i];
    }
    tilt += (spec.tilt - tilt) * 0.04;
    ox += (spec.ox - ox) * 0.04;
    smx += ((spec.mouse ? mx : 0) - smx) * 0.05;
    const yaw = reduced ? 0 : Math.sin(t * 0.12) * spec.yawAmp + smx * 0.3;
    const cy0 = Math.cos(yaw), sy0 = Math.sin(yaw), ct = Math.cos(tilt), st = Math.sin(tilt);
    const cx = cw / 2 + ox * cw;
    const cy = ch / 2;
    const F = 2.6;
    const jit = reduced ? 0 : 0.5;
    for (let i = 0; i < N; i++) {
      const j = i * 3;
      const x = pos[j], y = pos[j + 1], z = pos[j + 2];
      const x1 = x * cy0 - z * sy0;
      const z1 = x * sy0 + z * cy0;
      const y2 = y * ct - z1 * st;
      const z2 = y * st + z1 * ct;
      const sc = F / (F + z2);
      px[i] = cx + x1 * S * sc + Math.sin(t * 2.2 + i) * jit;
      py[i] = cy + y2 * S * sc + Math.cos(t * 1.9 + i * 0.7) * jit;
      ps[i] = (1.2 + R[i * 6 + 1] * 1.9) * sc;
      const dep = 1 - (Math.max(-1.2, Math.min(1.2, z2)) + 1.2) / 2.4;
      pa[i] = 0.22 + dep * 0.78;
    }
    const PAL = [PALETTE.particle, PALETTE.signal, sun, sun, PALETTE.faint];
    const AM = [1, 1, 1, 0.5, 0.8];
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cw, ch);
    for (let c = 0; c < 5; c++) {
      ctx.fillStyle = PAL[c];
      const am = AM[c];
      for (let i = 0; i < N; i++) {
        if (col[i] !== c) continue;
        ctx.globalAlpha = pa[i] * am;
        ctx.fillRect(px[i], py[i], ps[i], ps[i]);
      }
    }
    ctx.globalAlpha = 1;
  }

  const onMove = (e) => {
    mx = e.clientX / Math.max(1, window.innerWidth) - 0.5;
  };
  window.addEventListener("pointermove", onMove, { passive: true });
  let io = null;
  if (typeof IntersectionObserver !== "undefined") {
    io = new IntersectionObserver((en) => {
      visible = !!(en[0] && en[0].isIntersecting);
    });
    io.observe(canvas);
  }
  if (document.fonts && document.fonts.load) {
    document.fonts
      .load('600 120px "Geist"')
      .then(() => {
        cache = {};
      })
      .catch(() => {});
  }
  const loop = () => {
    raf = requestAnimationFrame(loop);
    if (visible) frame();
  };
  loop();

  return {
    setScene(form, phase) {
      scene = { form, phase };
    },
    // x, y in canvas pixels; null to fall back to the default spot.
    setAnchor(x, y) {
      anchor = x == null ? null : { x, y };
    },
    destroy() {
      cancelAnimationFrame(raf);
      window.removeEventListener("pointermove", onMove);
      if (io) io.disconnect();
    },
  };
}
