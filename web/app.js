'use strict';

import { History } from '/static/history.js';

// ══════════════════════════════════════════════════════════
// 状态
// ══════════════════════════════════════════════════════════

const S = {
  project: null,
  sel: null,
  tool: 'select',
  view: '2d',
  bview: 'timeline',
  tx: 0, ty: 0, scale: 8,
  dragging: null,
  draft: null,
  mouse: { x: 0, y: 0 },
  lastCompile: null,
  cam: { yaw: -0.6, pitch: 0.55, dist: 60, target: [4, 4, 0] },
  camDrag: null,
  showTravel: false,
  showIO: false,
  layerFilter: -1,
};

const hist = new History({ maxStates: 120, mergeMs: 700 });

const cv = document.getElementById('cv');
const ctx = cv.getContext('2d');
const cv3 = document.getElementById('cv3d');
const ctx3 = cv3.getContext('2d');
const cvS = document.getElementById('cvSim');
const ctxS = cvS.getContext('2d');
const cvB = document.getElementById('cvBottom');
const ctxB = cvB.getContext('2d');

const $ = id => document.getElementById(id);

// ══════════════════════════════════════════════════════════
// 工具
// ══════════════════════════════════════════════════════════

function w2s(x, y) { return [x * S.scale + S.tx, -y * S.scale + S.ty]; }
function s2w(px, py) { return [(px - S.tx) / S.scale, -(py - S.ty) / S.scale]; }
function uid(p) { return p + '_' + Math.random().toString(36).slice(2, 8); }
function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

let toastTimer = null;
function toast(msg, ms = 1600) {
  let el = document.querySelector('.toast');
  if (!el) {
    el = document.createElement('div');
    el.className = 'toast';
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), ms);
}

function setStatus(msg, kind) {
  const el = $('statBadge');
  el.textContent = msg;
  el.className = 'badge' + (kind ? ' ' + kind : '');
}

// ══════════════════════════════════════════════════════════
// 历史记录
// ══════════════════════════════════════════════════════════

function snapshot() {
  return { project: S.project, sel: S.sel };
}

/** 记录一步。key 用于合并连续操作（拖动、滑块）。 */
function commit(label, key = null) {
  hist.push(snapshot(), label, key);
  syncHistoryUI();
}

function syncHistoryUI() {
  const s = hist.summary();
  $('btnUndo').disabled = !s.canUndo;
  $('btnRedo').disabled = !s.canRedo;
  $('btnUndo').title = s.canUndo ? `撤销：${s.undoLabel} (Ctrl+Z)` : '没有可撤销的操作';
  $('btnRedo').title = s.canRedo ? `重做：${s.redoLabel} (Ctrl+Shift+Z)` : '没有可重做的操作';
  const badge = $('stepBadge');
  badge.textContent = `${s.steps} 步`;
  badge.classList.toggle('active', s.steps > 0);
  badge.title = `历史 ${s.index + 1}/${s.total}`;
}

function applyState(state) {
  S.project = state.project;
  S.sel = state.sel;
  syncProps();
  syncParamsFromProject();
  refreshShapeList();
  updateNextStep();
  draw();
  if (S.view === '3d') draw3D();
}

function doUndo() {
  const st = hist.undo();
  if (!st) { toast('没有可撤销的操作'); return; }
  applyState(st);
  syncHistoryUI();
  toast(`已撤销（第 ${hist.summary().steps} 步）`);
}

function doRedo() {
  const st = hist.redo();
  if (!st) { toast('没有可重做的操作'); return; }
  applyState(st);
  syncHistoryUI();
  toast(`已重做（第 ${hist.summary().steps} 步）`);
}

$('btnUndo').addEventListener('click', doUndo);
$('btnRedo').addEventListener('click', doRedo);

// ══════════════════════════════════════════════════════════
// 尺寸
// ══════════════════════════════════════════════════════════

function resizeOne(c) {
  const r = c.getBoundingClientRect();
  if (r.width < 1 || r.height < 1) return false;
  const dpr = window.devicePixelRatio || 1;
  c.width = Math.floor(r.width * dpr);
  c.height = Math.floor(r.height * dpr);
  c.getContext('2d').setTransform(dpr, 0, 0, dpr, 0, 0);
  return true;
}

function resizeAll() {
  for (const c of [cv, cv3, cvS, cvB]) resizeOne(c);
  draw(); draw3D(); drawSim(); drawBottom();
}
window.addEventListener('resize', resizeAll);

// ══════════════════════════════════════════════════════════
// 2D 画布
// ══════════════════════════════════════════════════════════

function fitView() {
  if (!S.project) return;
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  for (const L of S.project.layers) {
    for (const st of L.strokes) {
      for (const p of st.points) {
        minx = Math.min(minx, p.x); maxx = Math.max(maxx, p.x);
        miny = Math.min(miny, p.y); maxy = Math.max(maxy, p.y);
      }
    }
  }
  const W = cv.clientWidth, H = cv.clientHeight;
  if (!isFinite(minx)) {
    minx = 0; miny = 0;
    maxx = S.project.machine.travel_x; maxy = S.project.machine.travel_y;
  }
  const pad = 50;
  const w = Math.max(1, maxx - minx), h = Math.max(1, maxy - miny);
  S.scale = clamp(Math.min((W - pad * 2) / w, (H - pad * 2) / h), 0.5, 200);
  const cx = (minx + maxx) / 2, cy = (miny + maxy) / 2;
  S.tx = W / 2 - cx * S.scale;
  S.ty = H / 2 + cy * S.scale;
  S.cam.target = [cx, cy, 0];
  S.cam.dist = Math.max(w, h) * 2.2;
  draw(); draw3D();
}

function drawGrid() {
  const W = cv.clientWidth, H = cv.clientHeight;
  const target = 72;
  let step = Math.pow(10, Math.ceil(Math.log10(target / S.scale)));
  for (const m of [5, 2, 1]) {
    if ((step / m) * S.scale >= 44) { step /= m; break; }
  }
  const [x0, y0] = s2w(0, H);
  const [x1, y1] = s2w(W, 0);

  ctx.strokeStyle = '#1c2129';
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = Math.floor(x0 / step) * step; x <= x1; x += step) {
    const px = Math.round(w2s(x, 0)[0]) + .5;
    ctx.moveTo(px, 0); ctx.lineTo(px, H);
  }
  for (let y = Math.floor(y0 / step) * step; y <= y1; y += step) {
    const py = Math.round(w2s(0, y)[1]) + .5;
    ctx.moveTo(0, py); ctx.lineTo(W, py);
  }
  ctx.stroke();

  ctx.strokeStyle = '#333b47';
  const [ox, oy] = w2s(0, 0);
  ctx.beginPath();
  ctx.moveTo(0, Math.round(oy) + .5); ctx.lineTo(W, Math.round(oy) + .5);
  ctx.moveTo(Math.round(ox) + .5, 0); ctx.lineTo(Math.round(ox) + .5, H);
  ctx.stroke();

  ctx.fillStyle = '#4a525f';
  ctx.font = '10px ui-monospace, monospace';
  for (let x = Math.floor(x0 / step) * step; x <= x1; x += step) {
    const [px, py] = w2s(x, 0);
    if (px > 24 && px < W - 24) ctx.fillText(String(+x.toFixed(2)), px + 4, py + 12);
  }
  for (let y = Math.floor(y0 / step) * step; y <= y1; y += step) {
    const [px, py] = w2s(0, y);
    if (py > 14 && py < H - 6) ctx.fillText(String(+y.toFixed(2)), px + 5, py - 4);
  }
}

function strokeColor(st) {
  if (st.kind === 'contour') return '#4ea3ff';
  if (st.kind === 'fill') return '#5ad19a';
  return '#6b7484';
}

function drawStroke2D(st) {
  const pts = st.points;
  if (pts.length < 2) return;
  const selected = S.sel === st.id;
  ctx.beginPath();
  let first = true;
  for (const p of pts) {
    const [px, py] = w2s(p.x, p.y);
    if (first) { ctx.moveTo(px, py); first = false; }
    else ctx.lineTo(px, py);
  }
  ctx.strokeStyle = strokeColor(st);
  ctx.lineWidth = selected ? 2.6 : 1.6;
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  ctx.globalAlpha = selected ? 1 : .88;
  ctx.stroke();
  ctx.globalAlpha = 1;

  const [sx, sy] = w2s(pts[0].x, pts[0].y);
  const [ex, ey] = w2s(pts[pts.length - 1].x, pts[pts.length - 1].y);
  ctx.fillStyle = selected ? '#fff' : '#4ea3ff';
  ctx.fillRect(sx - 3, sy - 3, 6, 6);
  ctx.beginPath();
  ctx.arc(ex, ey, 3.2, 0, Math.PI * 2);
  ctx.fillStyle = selected ? '#fff' : '#ffb454';
  ctx.fill();

  if (selected) {
    ctx.font = '10px ui-monospace, monospace';
    ctx.fillStyle = '#5ad19a';
    ctx.fillText(`↑${st.start.io}/${st.start.level} ${st.start.delay_ms}ms`, sx + 7, sy - 6);
    ctx.fillStyle = '#ffb454';
    ctx.fillText(`↓${st.end.io}/${st.end.level} ${st.end.delay_ms}ms`, ex + 7, ey + 14);
  }
}

function drawDraft() {
  if (!S.draft) return;
  const d = S.draft;
  ctx.setLineDash([6, 4]);
  ctx.strokeStyle = '#ffb454';
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  if (d.type === 'line') {
    const [ax, ay] = w2s(d.x0, d.y0);
    const [bx, by] = w2s(S.mouse.x, S.mouse.y);
    ctx.moveTo(ax, ay); ctx.lineTo(bx, by);
  } else if (d.type === 'rect') {
    const x0 = Math.min(d.x0, S.mouse.x), x1 = Math.max(d.x0, S.mouse.x);
    const y0 = Math.min(d.y0, S.mouse.y), y1 = Math.max(d.y0, S.mouse.y);
    const [ax, ay] = w2s(x0, y1);
    const [bx, by] = w2s(x1, y0);
    ctx.rect(ax, ay, bx - ax, by - ay);
  } else if (d.type === 'circle') {
    const r = Math.hypot(S.mouse.x - d.x0, S.mouse.y - d.y0);
    const [cx, cy] = w2s(d.x0, d.y0);
    ctx.arc(cx, cy, r * S.scale, 0, Math.PI * 2);
  }
  ctx.stroke();
  ctx.setLineDash([]);
}

function draw() {
  if (S.view !== '2d') return;
  const W = cv.clientWidth, H = cv.clientHeight;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#0b0e12';
  ctx.fillRect(0, 0, W, H);
  drawGrid();
  if (S.project) {
    for (const L of S.project.layers) {
      if (!L.visible) continue;
      for (const st of L.strokes) drawStroke2D(st);
    }
  }
  drawDraft();
}

// ══════════════════════════════════════════════════════════
// 3D 预览
// ══════════════════════════════════════════════════════════

function camMatrix() {
  const { yaw, pitch } = S.cam;
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  return {
    project(p) {
      const x = p[0] - S.cam.target[0];
      const y = p[1] - S.cam.target[1];
      const z = p[2] - S.cam.target[2];
      const rx = x * cy - y * sy;
      const ry = x * sy + y * cy;
      return [rx, ry * sp - z * cp, ry * cp + z * sp];
    },
  };
}

function draw3D() {
  if (S.view !== '3d') return;
  const W = cv3.clientWidth, H = cv3.clientHeight;
  if (W < 2 || H < 2) return;
  ctx3.clearRect(0, 0, W, H);
  ctx3.fillStyle = '#0b0e12';
  ctx3.fillRect(0, 0, W, H);

  const c = S.lastCompile;
  if (!c || !c.sim) {
    ctx3.fillStyle = '#4a525f';
    ctx3.font = '13px sans-serif';
    ctx3.textAlign = 'center';
    ctx3.fillText('点右上「生成 G-code」后这里显示 3D 效果', W / 2, H / 2);
    ctx3.textAlign = 'left';
    return;
  }

  const M = camMatrix();
  const scale = Math.min(W, H) / (S.cam.dist * 1.6);
  const cxs = W / 2, cys = H / 2;
  const toScreen = p => {
    const [sx, syv, d] = M.project(p);
    return [cxs + sx * scale, cys + syv * scale, d];
  };

  const lw = (S.project?.process?.line_width) || 0.4;
  const segs = c.sim.segments || [];
  const drawList = [];

  const printSegs = segs.filter(g => g[7] === 1);
  const zs = [...new Set(printSegs.map(g => Math.round(g[6] * 1000) / 1000))].sort((a, b) => a - b);
  const filterZ = S.layerFilter >= 0 && zs[S.layerFilter] != null ? zs[S.layerFilter] : null;

  for (const g of segs) {
    const isPrint = g[7] === 1;
    if (!isPrint && !S.showTravel) continue;
    if (filterZ != null && Math.abs(g[6] - filterZ) > 1e-6) continue;
    const a = toScreen([g[2], g[3], g[6]]);
    const b = toScreen([g[4], g[5], g[6]]);
    drawList.push({ a, b, depth: (a[2] + b[2]) / 2, isPrint, z: g[6] });
  }

  drawList.sort((p, q) => p.depth - q.depth);

  // 地面网格
  ctx3.strokeStyle = '#1a1f26';
  ctx3.lineWidth = 1;
  ctx3.beginPath();
  const gs = 5;
  const gx0 = Math.floor((S.cam.target[0] - 40) / gs) * gs;
  const gy0 = Math.floor((S.cam.target[1] - 40) / gs) * gs;
  for (let i = 0; i <= 16; i++) {
    const x = gx0 + i * gs, y = gy0 + i * gs;
    let p1 = toScreen([x, gy0, 0]), p2 = toScreen([x, gy0 + 16 * gs, 0]);
    ctx3.moveTo(p1[0], p1[1]); ctx3.lineTo(p2[0], p2[1]);
    p1 = toScreen([gx0, y, 0]); p2 = toScreen([gx0 + 16 * gs, y, 0]);
    ctx3.moveTo(p1[0], p1[1]); ctx3.lineTo(p2[0], p2[1]);
  }
  ctx3.stroke();

  const zmin = Math.min(...zs, 0), zmax = Math.max(...zs, 0.001);
  ctx3.lineCap = 'round';
  // 线宽：线段很多时收细，否则会糊成一片。
  // 依据是「整幅图有多少段」—— 段数越多，单根线越细。
  const segCount = drawList.length;
  const density = clamp(1 - Math.log10(Math.max(1, segCount)) / 4, 0.25, 1); // 1段→1, 100段→0.5, 10000段→0.25
  const pxWidth = clamp((lw * scale) * 0.9 * (0.5 + density * 0.5), 1.2, 14);
  for (const d of drawList) {
    if (d.isPrint) {
      const t = zmax > zmin ? (d.z - zmin) / (zmax - zmin) : 0;
      ctx3.strokeStyle = `rgb(${Math.round(45 + 75 * t)},${Math.round(115 + 85 * t)},${Math.round(195 + 60 * t)})`;
      ctx3.lineWidth = pxWidth;
    } else {
      ctx3.strokeStyle = 'rgba(90,100,115,.35)';
      ctx3.lineWidth = 1;
    }
    ctx3.beginPath();
    ctx3.moveTo(d.a[0], d.a[1]);
    ctx3.lineTo(d.b[0], d.b[1]);
    ctx3.stroke();
  }

  if (S.showIO) {
    for (const tr of c.io_transitions || []) {
      if (tr.x == null) continue;
      const z = filterZ != null ? filterZ : zmin;
      const p = toScreen([tr.x, tr.y, z]);
      ctx3.beginPath();
      ctx3.arc(p[0], p[1], tr.level ? 4 : 3, 0, Math.PI * 2);
      ctx3.fillStyle = tr.level ? 'rgba(90,209,154,.9)' : 'rgba(255,180,84,.9)';
      ctx3.fill();
    }
  }

  // 坐标轴
  const O = toScreen([0, 0, 0]);
  ctx3.lineWidth = 1.5;
  ctx3.font = '11px ui-monospace, monospace';
  for (const [v, col, name] of [
    [[12, 0, 0], '#ff6b6b', 'X'],
    [[0, 12, 0], '#5ad19a', 'Y'],
    [[0, 0, 12], '#4ea3ff', 'Z'],
  ]) {
    const p = toScreen(v);
    ctx3.strokeStyle = col;
    ctx3.beginPath();
    ctx3.moveTo(O[0], O[1]); ctx3.lineTo(p[0], p[1]);
    ctx3.stroke();
    ctx3.fillStyle = col;
    ctx3.fillText(name, p[0] + 3, p[1] - 3);
  }

  ctx3.fillStyle = '#4a525f';
  ctx3.font = '11px ui-monospace, monospace';
  ctx3.fillText(`${zs.length} 层 · ${drawList.length} 段`, 12, H - 12);
}

cv3.addEventListener('mousedown', e => {
  S.camDrag = { x: e.clientX, y: e.clientY, yaw: S.cam.yaw, pitch: S.cam.pitch };
});
window.addEventListener('mousemove', e => {
  if (!S.camDrag) return;
  // 直接操作手感：往右拖 → 物体跟着往右转（yaw 减小）
  // 往下拖 → 物体前脸朝下翻 → 看到顶部（pitch 增大）
  S.cam.yaw = S.camDrag.yaw - (e.clientX - S.camDrag.x) * 0.008;
  S.cam.pitch = clamp(S.camDrag.pitch + (e.clientY - S.camDrag.y) * 0.008, -0.2, 1.45);
  draw3D();
});
window.addEventListener('mouseup', () => { S.camDrag = null; });

cv3.addEventListener('wheel', e => {
  if (S.view !== '3d') return;
  e.preventDefault();
  S.cam.dist = clamp(S.cam.dist * (e.deltaY > 0 ? 1.12 : 1 / 1.12), 5, 500);
  draw3D();
}, { passive: false });

// ══════════════════════════════════════════════════════════
// 2D 交互
// ══════════════════════════════════════════════════════════

function distToSeg(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const L2 = dx * dx + dy * dy;
  if (L2 < 1e-12) return Math.hypot(px - ax, py - ay);
  const t = clamp(((px - ax) * dx + (py - ay) * dy) / L2, 0, 1);
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function hitTest(wx, wy) {
  const tol = 7 / S.scale;
  let best = null, bestD = Infinity;
  for (const L of S.project.layers) {
    if (!L.visible) continue;
    for (const st of L.strokes) {
      const pts = st.points;
      for (let i = 0; i < pts.length - 1; i++) {
        const d = distToSeg(wx, wy, pts[i].x, pts[i].y, pts[i + 1].x, pts[i + 1].y);
        if (d < bestD && d < tol) { bestD = d; best = st; }
      }
    }
  }
  return best;
}

cv.addEventListener('mousedown', e => {
  if (S.view !== '2d') return;
  const r = cv.getBoundingClientRect();
  const px = e.clientX - r.left, py = e.clientY - r.top;
  const [wx, wy] = s2w(px, py);

  if (e.button === 1 || e.button === 2 || e.shiftKey) {
    S.dragging = { mode: 'pan', sx: px, sy: py, tx: S.tx, ty: S.ty };
    cv.style.cursor = 'grabbing';
    e.preventDefault();
    return;
  }

  if (S.tool === 'select') {
    const hit = hitTest(wx, wy);
    if (hit) {
      S.sel = hit.id;
      S.dragging = { mode: 'move', id: hit.id, lx: wx, ly: wy, moved: false };
    } else {
      S.sel = null;
    }
    syncProps();
    refreshShapeList();
    updateNextStep();
    draw();
  } else {
    S.draft = { type: S.tool, x0: wx, y0: wy };
  }
});

cv.addEventListener('mousemove', e => {
  if (S.view !== '2d') return;
  const r = cv.getBoundingClientRect();
  const px = e.clientX - r.left, py = e.clientY - r.top;
  const [wx, wy] = s2w(px, py);
  S.mouse = { x: wx, y: wy };
  $('cursorPos').textContent = `${wx.toFixed(2)}, ${wy.toFixed(2)}`;

  if (S.dragging) {
    if (S.dragging.mode === 'pan') {
      S.tx = S.dragging.tx + (px - S.dragging.sx);
      S.ty = S.dragging.ty + (py - S.dragging.sy);
    } else if (S.dragging.mode === 'move') {
      const st = findStroke(S.dragging.id);
      if (st) {
        const dx = wx - S.dragging.lx, dy = wy - S.dragging.ly;
        if (Math.abs(dx) > 1e-9 || Math.abs(dy) > 1e-9) S.dragging.moved = true;
        for (const p of st.points) { p.x += dx; p.y += dy; }
        S.dragging.lx = wx; S.dragging.ly = wy;
      }
    }
    draw();
    return;
  }
  if (S.draft) draw();
});

window.addEventListener('mouseup', () => {
  if (S.dragging) {
    // 拖动结束时记一步（合并成一次「移动」）
    if (S.dragging.mode === 'move' && S.dragging.moved) {
      commit('移动形状', 'move:' + S.dragging.id);
    }
    S.dragging = null;
    cv.style.cursor = S.tool === 'select' ? 'default' : 'crosshair';
    return;
  }
  if (S.draft) commitDraft();
});

cv.addEventListener('contextmenu', e => e.preventDefault());

cv.addEventListener('wheel', e => {
  if (S.view !== '2d') return;
  e.preventDefault();
  const r = cv.getBoundingClientRect();
  const px = e.clientX - r.left, py = e.clientY - r.top;
  const [wx, wy] = s2w(px, py);
  S.scale = clamp(S.scale * (e.deltaY < 0 ? 1.15 : 1 / 1.15), 0.3, 200);
  S.tx = px - wx * S.scale;
  S.ty = py + wy * S.scale;
  $('zoomInfo').textContent = Math.round(S.scale * 12.5) + '%';
  draw();
}, { passive: false });

// ══════════════════════════════════════════════════════════
// 图元提交
// ══════════════════════════════════════════════════════════

function commitDraft() {
  const d = S.draft;
  S.draft = null;
  if (!d) return;
  const mx = S.mouse.x, my = S.mouse.y;
  let pts = null, src = null;
  const shapeName = { line: '线段', rect: '矩形', circle: '圆' }[d.type];

  if (d.type === 'line') {
    if (Math.hypot(mx - d.x0, my - d.y0) < 0.05) { draw(); return; }
    pts = [{ x: d.x0, y: d.y0 }, { x: mx, y: my }];
    src = { type: 'polyline', pts };
  } else if (d.type === 'rect') {
    const x = Math.min(d.x0, mx), y = Math.min(d.y0, my);
    const w = Math.abs(mx - d.x0), h = Math.abs(my - d.y0);
    if (w < 0.05 || h < 0.05) { draw(); return; }
    pts = [{ x, y }, { x: x + w, y }, { x: x + w, y: y + h }, { x, y: y + h }, { x, y }];
    src = { type: 'rect', x, y, w, h };
  } else if (d.type === 'circle') {
    const r = Math.hypot(mx - d.x0, my - d.y0);
    if (r < 0.05) { draw(); return; }
    const n = Math.max(24, Math.min(360, Math.ceil(2 * Math.PI * r / 0.2)));
    pts = [];
    for (let i = 0; i < n; i++) {
      const a = (2 * Math.PI * i) / n;
      pts.push({ x: d.x0 + r * Math.cos(a), y: d.y0 + r * Math.sin(a) });
    }
    pts.push({ ...pts[0] });
    src = { type: 'circle', cx: d.x0, cy: d.y0, r, tolerance: 0.05 };
  }
  if (!pts) { draw(); return; }

  const L = ensureLayer();
  const st = {
    id: uid('s'),
    points: pts,
    closed: pts.length > 2,
    kind: 'contour',
    start: { io: 0, level: 1, delay_ms: S.project.process.rise_ms ?? 120 },
    end: { io: 0, level: 0, delay_ms: S.project.process.tail_ms ?? 80 },
    source: src,
    _name: shapeName,
  };
  L.strokes.push(st);
  S.sel = st.id;
  commit(`添加${shapeName}`);
  syncProps();
  refreshShapeList();
  updateNextStep();
  draw();
  setStatus(`${shapeName}已添加`, 'ok');
}

function ensureLayer() {
  if (!S.project.layers.length) {
    S.project.layers.push({
      id: 'L1', z: 0.4, repeat: 1, z_step: 0.4,
      overrides: {}, strokes: [], visible: true,
    });
  }
  return S.project.layers[0];
}

function findStroke(id) {
  for (const L of S.project.layers) {
    const s = L.strokes.find(x => x.id === id);
    if (s) return s;
  }
  return null;
}
function findStrokeLayer(id) {
  for (const L of S.project.layers) {
    if (L.strokes.some(x => x.id === id)) return L;
  }
  return null;
}

// ══════════════════════════════════════════════════════════
// 形状列表
// ══════════════════════════════════════════════════════════

function refreshShapeList() {
  const box = $('shapeList');
  const L = S.project?.layers?.[0];
  const n = L?.strokes?.length || 0;
  $('shapeCount').textContent = n;

  if (!n) {
    box.innerHTML = '<div class="emptyHint">还没有形状<br>用左边的工具在画布上画</div>';
    return;
  }
  box.innerHTML = '';
  for (const st of L.strokes) {
    const el = document.createElement('div');
    el.className = 'shapeItem' + (S.sel === st.id ? ' active' : '');
    const name = st._name || (st.source?.type === 'rect' ? '矩形' : st.source?.type === 'circle' ? '圆' : '路径');
    const sw = document.createElement('span');
    sw.className = 'swatch';
    sw.style.background = strokeColor(st);
    const nm = document.createElement('span');
    nm.className = 'name';
    nm.textContent = name;
    const del = document.createElement('button');
    del.className = 'del';
    del.textContent = '×';
    del.title = '删除';
    del.onclick = ev => {
      ev.stopPropagation();
      deleteStroke(st.id);
    };
    el.append(sw, nm, del);
    el.onclick = () => {
      S.sel = st.id;
      syncProps();
      refreshShapeList();
      updateNextStep();
      draw();
    };
    box.appendChild(el);
  }
}

function deleteStroke(id) {
  const L = findStrokeLayer(id);
  if (!L) return;
  const idx = L.strokes.findIndex(s => s.id === id);
  const removed = L.strokes.splice(idx, 1)[0];
  if (S.sel === id) S.sel = null;
  commit('删除形状');
  syncProps();
  refreshShapeList();
  updateNextStep();
  draw();
  toast(`已删除 ${removed._name || '形状'}`);
}

// ══════════════════════════════════════════════════════════
// 属性面板
// ══════════════════════════════════════════════════════════

function syncProps() {
  const st = S.sel ? findStroke(S.sel) : null;
  const panel = $('selPanel');
  if (!st) {
    panel.classList.add('hidden');
    return;
  }
  panel.classList.remove('hidden');
  $('selTitle').textContent = st._name || '形状';
  $('selMeta').textContent = `${st.points.length} 点`;
  $('startIo').value = st.start.io;
  $('startLevel').value = String(st.start.level);
  $('startDelay').value = st.start.delay_ms;
  $('endIo').value = st.end.io;
  $('endLevel').value = String(st.end.level);
  $('endDelay').value = st.end.delay_ms;
  $('strokeFeed').value = st.feed ?? '';
  $('strokeKind').value = st.kind;
}

function bindProp(id, fn, label) {
  $(id).addEventListener('input', () => {
    const st = S.sel ? findStroke(S.sel) : null;
    if (!st) return;
    fn(st);
    draw();
    commit(label, 'prop:' + id + ':' + st.id);
  });
}

bindProp('startIo', st => st.start.io = clamp(+$('startIo').value | 0, 0, 7), '改起点通道');
bindProp('startLevel', st => st.start.level = +$('startLevel').value, '改起点电平');
bindProp('startDelay', st => st.start.delay_ms = Math.max(0, +$('startDelay').value | 0), '改起点延时');
bindProp('endIo', st => st.end.io = clamp(+$('endIo').value | 0, 0, 7), '改终点通道');
bindProp('endLevel', st => st.end.level = +$('endLevel').value, '改终点电平');
bindProp('endDelay', st => st.end.delay_ms = Math.max(0, +$('endDelay').value | 0), '改终点延时');
bindProp('strokeFeed', st => st.feed = $('strokeFeed').value ? +$('strokeFeed').value : null, '改笔画速度');
bindProp('strokeKind', st => { st.kind = $('strokeKind').value; refreshShapeList(); }, '改笔画类型');

$('btnDelete').addEventListener('click', () => {
  if (S.sel) deleteStroke(S.sel);
});

// ══════════════════════════════════════════════════════════
// 滑块参数
// ══════════════════════════════════════════════════════════

const PARAM_MAP = {
  pRise:      { path: 'process.rise_ms',     out: 'outRise',        dec: 0, label: '改开阀延时' },
  pTail:      { path: 'process.tail_ms',     out: 'outTail',        dec: 0, label: '改关阀延时' },
  pFeed:      { path: 'machine.feed_default',out: 'outFeed',        dec: 0, label: '改打印速度' },
  pLineWidth: { path: 'process.line_width',  out: 'outLineWidth',   dec: 2, label: '改线宽' },
  fillSpacing:{ path: null,                  out: 'outSpacing',     dec: 1, label: '改填充间距' },
  fillAngle:  { path: null,                  out: 'outAngle',       dec: 0, label: '改扫描角度' },
  fillInset:  { path: null,                  out: 'outInset',       dec: 1, label: '改离边距离' },
  pLayerHeight:{ path: 'process.layer_height',out: 'outLayerHeight',dec: 2, label: '改层高' },
  layerRepeat:{ path: null,                  out: 'outRepeat',      dec: 0, label: '改重复层数' },
  layerZ:     { path: null,                  out: 'outLayerZ',      dec: 1, label: '改起始Z' },
  pRapid:     { path: 'machine.feed_rapid',  out: 'outRapid',       dec: 0, label: '改空驶速度' },
  pAccel:     { path: 'machine.accel',       out: 'outAccel',       dec: 0, label: '改加速度' },
  pLiftZ:     { path: 'process.lift_z',      out: 'outLiftZ',       dec: 1, label: '改抬针高度' },
};

function setNested(obj, path, val) {
  const [a, b] = path.split('.');
  if (b) obj[a][b] = val;
  else obj[a] = val;
}

function getNested(obj, path) {
  const [a, b] = path.split('.');
  return b ? obj[a]?.[b] : obj[a];
}

for (const [id, cfg] of Object.entries(PARAM_MAP)) {
  const el = $(id);
  if (!el) continue;
  el.addEventListener('input', () => {
    if (!S.project) return;
    const v = +el.value;
    const out = $(cfg.out);
    if (out) out.textContent = v.toFixed(cfg.dec);

    if (cfg.path) {
      setNested(S.project, cfg.path, v);
    } else if (id === 'layerRepeat') {
      ensureLayer().repeat = Math.max(1, v | 0);
    } else if (id === 'layerZ') {
      ensureLayer().z = v;
    }
    // 填充参数是即时用的，不存工程
    if (id === 'pLineWidth' || id === 'pLayerHeight') draw3D();

    // 拖动滑块时合并成一步
    commit(cfg.label, 'param:' + id);
  });
}

function syncParamsFromProject() {
  if (!S.project) return;
  const p = S.project;
  const L = p.layers?.[0];
  const set = (id, val, dec) => {
    const el = $(id);
    const cfg = PARAM_MAP[id];
    if (!el) return;
    el.value = val;
    if (cfg?.out) $(cfg.out).textContent = (+val).toFixed(dec ?? cfg.dec ?? 0);
  };
  set('pRise', p.process.rise_ms);
  set('pTail', p.process.tail_ms);
  set('pFeed', p.machine.feed_default);
  set('pLineWidth', p.process.line_width ?? 0.4);
  set('pLayerHeight', p.process.layer_height ?? 0.4);
  set('pRapid', p.machine.feed_rapid);
  set('pAccel', p.machine.accel);
  set('pLiftZ', p.process.lift_z);
  if (L) {
    set('layerRepeat', L.repeat);
    set('layerZ', L.z);
  }
}

$('btnResetParams').addEventListener('click', () => {
  if (!S.project) return;
  S.project.process = {
    rise_ms: 120, tail_ms: 80, lift_z: 0.3, lift_feed: 3000,
    line_width: 0.4, layer_height: 0.4, min_segment_len: 1.5,
  };
  S.project.machine.feed_default = 600;
  S.project.machine.feed_rapid = 6000;
  S.project.machine.accel = 300;
  syncParamsFromProject();
  commit('恢复默认参数');
  toast('参数已恢复默认');
});

// ══════════════════════════════════════════════════════════
// 下一步引导
// ══════════════════════════════════════════════════════════

function updateNextStep() {
  const L = S.project?.layers?.[0];
  const n = L?.strokes?.length || 0;
  const txt = $('nextStepText');
  const btn = $('nextStepBtn');
  btn.classList.add('hidden');
  btn.onclick = null;

  if (n === 0) {
    txt.textContent = '先画一个形状：左边选「矩形」或「圆」，在画布上拖出来。';
    return;
  }
  const hasFill = L.strokes.some(s => s.kind === 'fill');
  const st = S.sel ? findStroke(S.sel) : null;

  if (st && !hasFill) {
    txt.textContent = '已选中形状。点「填充选中形状」，把内部填满打印线。';
    btn.textContent = '填充它';
    btn.classList.remove('hidden');
    btn.onclick = () => $('btnFill').click();
    return;
  }
  if (!S.lastCompile) {
    txt.textContent = `有 ${n} 条路径了。点右上「生成 G-code」看成品效果。`;
    btn.textContent = '生成 G-code';
    btn.classList.remove('hidden');
    btn.onclick = () => $('btnCompile').click();
    return;
  }
  txt.textContent = '已生成。切到「3D 预览」看成品，或「仿真」看阀的开关时序。';
  btn.textContent = '看 3D 效果';
  btn.classList.remove('hidden');
  btn.onclick = () => switchView('3d');
}

// ══════════════════════════════════════════════════════════
// 视图切换
// ══════════════════════════════════════════════════════════

function switchView(v) {
  S.view = v;
  document.querySelectorAll('.vtab').forEach(b => b.classList.toggle('active', b.dataset.view === v));
  cv.classList.toggle('hidden', v !== '2d');
  cv3.classList.toggle('hidden', v !== '3d');
  cvS.classList.toggle('hidden', v !== 'sim');
  document.querySelector('.overlay2d').classList.toggle('hidden', v !== '2d');
  document.querySelector('.overlay3d').classList.toggle('hidden', v !== '3d');
  document.querySelector('.overlaySim').classList.toggle('hidden', v !== 'sim');
  document.querySelector('.view3dBar').classList.toggle('hidden', v !== '3d');

  $('viewHint').textContent = {
    '2d': '画形状 · 拖动移动',
    '3d': '成品效果预览',
    'sim': '速度曲线 + 气阀开关时机',
  }[v] || '';

  requestAnimationFrame(() => {
    if (v === '2d') { if (resizeOne(cv)) draw(); }
    else if (v === '3d') { if (resizeOne(cv3)) { update3DLayerSlider(); draw3D(); } }
    else if (v === 'sim') {
      if (resizeOne(cvS)) { updateSimUI(); drawSim(); }
    }
  });

  document.querySelector('.simBar').classList.toggle('hidden', v !== 'sim');
}

document.querySelectorAll('.vtab').forEach(b => b.addEventListener('click', () => switchView(b.dataset.view)));

document.querySelectorAll('.btab').forEach(b => {
  b.addEventListener('click', () => {
    document.querySelectorAll('.btab').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    S.bview = b.dataset.bview;
    $('gcodeBox').classList.toggle('hidden', S.bview !== 'gcode');
    cvB.classList.toggle('hidden', S.bview === 'gcode');
    drawBottom();
  });
});

function update3DLayerSlider() {
  const c = S.lastCompile;
  if (!c || !c.sim) return;
  const printSegs = (c.sim.segments || []).filter(g => g[7] === 1);
  const zs = [...new Set(printSegs.map(g => Math.round(g[6] * 1000) / 1000))].sort((a, b) => a - b);
  const sl = $('layerSlider');
  sl.min = -1;
  sl.max = Math.max(0, zs.length - 1);
  sl.value = S.layerFilter;
  $('layerLabel').textContent = S.layerFilter < 0
    ? `全部 ${zs.length} 层`
    : `第 ${S.layerFilter + 1}/${zs.length} 层 · Z ${zs[S.layerFilter]}`;
}

$('layerSlider').addEventListener('input', () => {
  S.layerFilter = +$('layerSlider').value;
  update3DLayerSlider();
  draw3D();
});
$('chkShowTravel').addEventListener('change', e => { S.showTravel = e.target.checked; draw3D(); });
$('chkShowIO').addEventListener('change', e => { S.showIO = e.target.checked; draw3D(); });

// ══════════════════════════════════════════════════════════
// 工具切换 + 快捷键
// ══════════════════════════════════════════════════════════

document.querySelectorAll('.tool').forEach(btn => {
  btn.addEventListener('click', () => setTool(btn.dataset.tool));
});

function setTool(t) {
  S.tool = t;
  document.querySelectorAll('.tool').forEach(b => b.classList.toggle('active', b.dataset.tool === t));
  cv.style.cursor = t === 'select' ? 'default' : 'crosshair';
}

window.addEventListener('keydown', e => {
  const tag = (e.target.tagName || '').toLowerCase();
  const typing = tag === 'input' || tag === 'textarea' || tag === 'select';

  // 撤销 / 重做
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
    e.preventDefault();
    if (e.shiftKey) doRedo(); else doUndo();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') {
    e.preventDefault();
    doRedo();
    return;
  }
  if (typing) return;

  const k = e.key.toLowerCase();
  if (k === 'v') setTool('select');
  else if (k === 'l') setTool('line');
  else if (k === 'r') setTool('rect');
  else if (k === 'c') setTool('circle');
  else if (k === 'delete' || k === 'backspace') {
    if (S.sel) { e.preventDefault(); deleteStroke(S.sel); }
  } else if (k === 'escape') {
    S.draft = null;
    S.sel = null;
    syncProps(); refreshShapeList(); updateNextStep(); draw();
  }
});

// ══════════════════════════════════════════════════════════
// API
// ══════════════════════════════════════════════════════════

async function api(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

$('btnCompile').addEventListener('click', async () => {
  if (!S.project) return;
  const L = S.project.layers[0];
  if (!L || !L.strokes.length) { setStatus('先画个形状', 'err'); return; }

  setStatus('生成中…', 'work');
  $('btnCompile').disabled = true;
  try {
    const res = await api('/api/compile', { project: S.project, dialect: $('dialect').value });
    S.lastCompile = res;
    S.layerFilter = -1;
    $('gcodeBox').textContent = res.gcode;

    const st = res.stats;
    $('simStats').textContent = `${st.strokes} 笔画 · ${st.path_length}mm · 约 ${st.est_seconds}s`;
    $('simStats').className = 'badge ok';

    const wbox = $('warnings');
    if (res.warnings.length) {
      wbox.textContent = res.warnings.join('\n');
      wbox.classList.add('has');
    } else {
      wbox.textContent = '无问题';
      wbox.classList.remove('has');
    }

    setStatus(`已生成 ${st.strokes} 笔画`, 'ok');
    updateNextStep();
    update3DLayerSlider();
    drawBottom();
    simReset();
    updateSendBtn();
    switchView('3d');
  } catch (e) {
    setStatus('生成失败', 'err');
    $('warnings').textContent = String(e);
    $('warnings').classList.add('has');
  } finally {
    $('btnCompile').disabled = false;
  }
});

$('btnFill').addEventListener('click', async () => {
  const st = S.sel ? findStroke(S.sel) : null;
  if (!st) { setStatus('先选中一个形状', 'err'); return; }
  if (!st.source) { setStatus('该形状不支持填充', 'err'); return; }

  setStatus('填充中…', 'work');
  try {
    const res = await api('/api/fill', {
      source: st.source,
      pattern: $('fillPattern')?.value || 'grid',
      spacing: +($('fillSpacing')?.value || 2.0),
      angle: +($('fillAngle')?.value || 0),
      inset: +($('fillInset')?.value || 0.4),
      min_len: 1.5,
      sort: true,
      merge: false,
      stagger: $('fillStagger')?.checked || false,
      outline: true,
    });
    if (!res.count) {
      setStatus('填充为空', 'err');
      $('warnings').textContent =
        `填充结果为空。形状太小了：内缩 ${$('fillInset')?.value || 0.4}mm 加上线宽，把整个区域吃掉了。\n` +
        '把形状画大一点，或在「填充」分组里把「离边距离」调小。';
      $('warnings').classList.add('has');
      return;
    }

    const L = findStrokeLayer(S.sel);
    const made = res.paths.map(p => ({
      id: uid('f'),
      points: p.map(([x, y]) => ({ x, y })),
      closed: false,
      kind: 'fill',
      start: { io: 0, level: 1, delay_ms: S.project.process.rise_ms ?? 120 },
      end: { io: 0, level: 0, delay_ms: S.project.process.tail_ms ?? 80 },
      source: null,
      _name: '填充线',
    }));
    L.strokes.push(...made);
    commit(`填充 +${made.length} 条`);
    setStatus(`填充 +${made.length} 条`, 'ok');
    refreshShapeList();
    updateNextStep();
    draw();
  } catch (e) {
    setStatus('填充失败', 'err');
    $('warnings').textContent = String(e);
    $('warnings').classList.add('has');
  }
});

$('btnFillAll').addEventListener('click', async () => {
  if (!S.project) return;
  const total = S.project.layers.length;
  if (!total) { setStatus('工程里没有层', 'err'); return; }

  setStatus('填充所有层…', 'work');
  $('btnFillAll').disabled = true;
  try {
    const res = await api('/api/fill-all', {
      project: S.project,
      pattern: $('fillPattern')?.value || 'grid',
      spacing: +($('fillSpacing')?.value || 2.0),
      angle: +($('fillAngle')?.value || 0),
      inset: +($('fillInset')?.value || 0.4),
      min_len: 1.5,
      skip_filled: true,
      replace_existing: false,
      stagger: $('fillStagger')?.checked || false,
      outline: true,
    });

    if (!res.added) {
      setStatus('没有新增填充', 'err');
      $('warnings').textContent = res.warnings.join('\n');
      $('warnings').classList.add('has');
      return;
    }

    S.project = res.project;
    commit(`全层填充 +${res.added} 条`);
    refreshShapeList();
    updateNextStep();
    draw();

    setStatus(`${res.layers_done} 层已填充`, 'ok');
    const msg = [
      `已填充 ${res.layers_done} 层，新增 ${res.added} 条填充线`,
      res.layers_skipped ? `跳过 ${res.layers_skipped} 层（已有填充）` : '',
      `共 ${res.layers_total} 层`,
    ].filter(Boolean);
    if (res.warnings.length) msg.push(...res.warnings.slice(0, 5));
    $('warnings').textContent = msg.join('\n');
    $('warnings').classList.toggle('has', res.warnings.length > 0);
    toast(`全层填充完成：+${res.added} 条`);
  } catch (e) {
    setStatus('全层填充失败', 'err');
    $('warnings').textContent = String(e);
    $('warnings').classList.add('has');
  } finally {
    $('btnFillAll').disabled = false;
  }
});

// ── 填充图案切换：错开选项只对格栅有效 ──
function syncFillUI() {
  const p = $('fillPattern')?.value;
  const row = $('rowStagger');
  if (row) row.classList.toggle('hidden', p !== 'grid');
  const spacing = $('fillSpacing');
  if (spacing) {
    // 格栅默认 2mm，实心扫描默认 1.5mm
    const want = (p === 'grid' || p === 'honeycomb') ? '2' : '1.5';
    if (!spacing.dataset.touched) {
      spacing.value = want;
      const out = $('outSpacing');
      if (out) out.textContent = (+want).toFixed(1);
    }
  }
}

$('fillPattern')?.addEventListener('change', syncFillUI);

// ── 视频同款「格栅块」预设 ──
$('btnLatticePreset').addEventListener('click', () => {
  if (!S.project) return;

  const setRange = (id, val) => {
    const el = $(id);
    if (!el) return;
    el.value = val;
    el.dispatchEvent(new Event('input'));
  };

  // 1. 填充方式与格栅参数（视频里格栅间距约 2mm，8mm 见方 → 内部 2×2 格）
  $('fillPattern').value = 'grid';
  $('fillStagger').checked = false;
  setRange('fillSpacing', 2.0);
  setRange('fillInset', 0.4);

  // 2. 工艺参数（视频那台的量级）
  setRange('pFeed', 600);
  setRange('pRise', 120);
  setRange('pTail', 80);
  setRange('pLiftZ', 0.3);
  setRange('pLineWidth', 0.45);
  setRange('pLayerHeight', 0.4);

  // 3. 层设置：6 层
  const L = ensureLayer();
  L.z = 0.4;
  L.repeat = 6;
  L.z_step = 0.4;

  // 4. 关键：把已有形状缩放到「8mm 见方」——视频里那个格栅块就是这个尺度。
  //    否则用户随手画个 80mm 大方块，会得到 900 笔画、4 小时的任务。
  const st = S.sel ? findStroke(S.sel) : (L.strokes[0] || null);
  let scaled = false;
  if (st && st.points.length >= 3) {
    let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    for (const p of st.points) {
      minx = Math.min(minx, p.x); maxx = Math.max(maxx, p.x);
      miny = Math.min(miny, p.y); maxy = Math.max(maxy, p.y);
    }
    const w = maxx - minx, h = maxy - miny;
    const span = Math.max(w, h);
    if (span > 0.01) {
      const k = 8.0 / span;          // 缩放到 8mm
      const cx = (minx + maxx) / 2, cy = (miny + maxy) / 2;
      for (const p of st.points) {
        p.x = 10 + (p.x - cx) * k;   // 摆到 (10,10) 附近，留出空白
        p.y = 10 + (p.y - cy) * k;
      }
      // 图元描述同步缩放（保留 source 才能继续填充）
      if (st.source) {
        const sxy = p => ({ x: 10 + (p.x - cx) * k, y: 10 + (p.y - cy) * k });
        if (st.source.pts) st.source.pts = st.source.pts.map(sxy);
        if (st.source.type === 'rect') {
          st.source.x = 10 + (st.source.x - cx) * k;
          st.source.y = 10 + (st.source.y - cy) * k;
          st.source.w *= k;
          st.source.h *= k;
        } else if (st.source.type === 'circle') {
          st.source.cx = 10 + (st.source.cx - cx) * k;
          st.source.cy = 10 + (st.source.cy - cy) * k;
          st.source.r *= k;
        }
      }
      scaled = true;
      S.sel = st.id;
      syncProps();
      refreshShapeList();
      draw();
    }
  }

  syncParamsFromProject();
  syncFillUI();
  fitView();

  commit('套用格栅块参数');
  toast(scaled
    ? '已套用格栅块参数，形状缩放到 8mm 见方（6 层 · 格栅 2mm）'
    : '已套用格栅块参数：格栅 2mm / 6 层');
  updateNextStep();
});

// 记录用户手动改过间距，避免切换图案时被覆盖
$('fillSpacing')?.addEventListener('input', e => {
  e.target.dataset.touched = '1';
});

$('dialect').addEventListener('change', () => {
  const notes = {
    lc: 'M121 U<n> V0/1 · 每个 IO 前插 M400（时序保守）',
    marlin: 'M42 P<pin> S<level> · M42 不排队，必须 M400 兜底',
    klipper: 'SET_PIN · IO 随运动排队，不需要 M400，不停车',
  };
  $('dialectNote').textContent = notes[$('dialect').value] || '';
});

// ══════════════════════════════════════════════════════════
// 仿真主视图：动画播放（不是时间轴复制）
// ══════════════════════════════════════════════════════════

const SIM = {
  t: 0,          // 当前播放时刻（秒）
  playing: false,
  speed: 1,      // 播放倍速
  raf: null,
  lastTs: 0,
  trace: [],     // 已经走过的轨迹点 [x,y]
};

function simReset() {
  SIM.t = 0;
  SIM.trace = [];
  SIM.playing = false;
  cancelAnimationFrame(SIM.raf);
  SIM.raf = null;
  updateSimUI();
  drawSim();
}

function simToggle() {
  const c = S.lastCompile;
  if (!c || !c.sim) { toast('先点「生成 G-code」'); return; }
  if (SIM.playing) {
    SIM.playing = false;
    cancelAnimationFrame(SIM.raf);
    SIM.raf = null;
    updateSimUI();
    drawSim();
    return;
  }
  if (SIM.t >= c.sim.duration) SIM.t = 0;
  SIM.playing = true;
  SIM.lastTs = performance.now();
  updateSimUI();
  const step = ts => {
    if (!SIM.playing) return;
    const dt = (ts - SIM.lastTs) / 1000;
    SIM.lastTs = ts;
    SIM.t += dt * SIM.speed;
    const dur = S.lastCompile?.sim?.duration || 0;
    if (SIM.t >= dur) { SIM.t = dur; SIM.playing = false; }
    updateSimUI();
    drawSim();
    if (SIM.playing) SIM.raf = requestAnimationFrame(step);
  };
  SIM.raf = requestAnimationFrame(step);
}

function updateSimUI() {
  const c = S.lastCompile;
  const dur = c?.sim?.duration || 0;
  const pct = dur > 0 ? (SIM.t / dur) * 100 : 0;
  const btn = $('btnSimPlay');
  if (btn) {
    btn.textContent = SIM.playing ? '⏸ 暂停' : (SIM.t >= dur && dur > 0 ? '↻ 重播' : '▶ 播放');
    btn.disabled = !c || !c.sim;
  }
  const bar = $('simProgress');
  if (bar) bar.style.width = pct + '%';
  const tEl = $('simTime');
  if (tEl) tEl.textContent = `${SIM.t.toFixed(2)} / ${dur.toFixed(2)} s`;
}

/** 在时间线里找当前时刻的位置和速度。 */
function simSampleAt(t) {
  const c = S.lastCompile;
  if (!c || !c.sim || !c.sim.samples.length) return null;
  const ss = c.sim.samples;
  // 二分
  let lo = 0, hi = ss.length - 1;
  if (t <= ss[0][0]) return ss[0];
  if (t >= ss[hi][0]) return ss[hi];
  while (lo < hi - 1) {
    const mid = (lo + hi) >> 1;
    if (ss[mid][0] <= t) lo = mid; else hi = mid;
  }
  const a = ss[lo], b = ss[hi];
  const span = b[0] - a[0];
  const k = span > 1e-9 ? (t - a[0]) / span : 0;
  return [
    t,
    a[1] + (b[1] - a[1]) * k,
    a[2] + (b[2] - a[2]) * k,
    a[3] + (b[3] - a[3]) * k,
    a[4] + (b[4] - a[4]) * k,
  ];
}

/** 当前时刻各通道的 IO 电平。 */
function simIOAt(t) {
  const c = S.lastCompile;
  const lv = {};
  if (!c || !c.sim) return lv;
  for (const e of c.sim.io_events) {
    if (e[0] > t) break;
    lv[e[1]] = e[2];
  }
  return lv;
}

function drawSim() {
  if (S.view !== 'sim') return;
  const W = cvS.clientWidth, H = cvS.clientHeight;
  if (W < 2 || H < 2) return;
  ctxS.clearRect(0, 0, W, H);
  ctxS.fillStyle = '#0b0e12';
  ctxS.fillRect(0, 0, W, H);

  const c = S.lastCompile;
  if (!c || !c.sim) {
    ctxS.fillStyle = '#4a525f';
    ctxS.font = '14px sans-serif';
    ctxS.textAlign = 'center';
    ctxS.fillText('还没有生成 G-code', W / 2, H / 2 - 12);
    ctxS.font = '12.5px sans-serif';
    ctxS.fillStyle = '#3d4553';
    ctxS.fillText('点右上角「生成 G-code」后，这里可以播放打印过程', W / 2, H / 2 + 14);
    ctxS.textAlign = 'left';
    return;
  }

  const segs = c.sim.segments || [];
  if (!segs.length) return;

  // ── 左侧：俯视动画 ──
  const leftW = Math.min(W * 0.62, W - 300);
  const padL = 16, padT = 16, padB = 16;
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  for (const g of segs) {
    minx = Math.min(minx, g[2], g[4]); maxx = Math.max(maxx, g[2], g[4]);
    miny = Math.min(miny, g[3], g[5]); maxy = Math.max(maxy, g[3], g[5]);
  }
  const w = Math.max(.001, maxx - minx), h = Math.max(.001, maxy - miny);
  const sc = Math.min((leftW - padL * 2) / w, (H - padT - padB) / h);
  const ox = padL + (leftW - padL * 2 - w * sc) / 2 - minx * sc;
  const oy = padT + (H - padT - padB - h * sc) / 2 + maxy * sc;
  const P = (x, y) => [x * sc + ox, -y * sc + oy];

  // 全路径底图（暗）
  ctxS.strokeStyle = '#1e2833';
  ctxS.lineWidth = 1.6;
  ctxS.lineCap = 'round';
  ctxS.beginPath();
  for (const g of segs) {
    if (!g[7]) continue;
    const [ax, ay] = P(g[2], g[3]);
    const [bx, by] = P(g[4], g[5]);
    ctxS.moveTo(ax, ay); ctxS.lineTo(bx, by);
  }
  ctxS.stroke();

  // 已打印部分（亮）
  ctxS.strokeStyle = '#4ea3ff';
  ctxS.lineWidth = 2.4;
  ctxS.beginPath();
  for (const g of segs) {
    if (!g[7]) continue;
    if (g[1] <= SIM.t) {
      const [ax, ay] = P(g[2], g[3]);
      const [bx, by] = P(g[4], g[5]);
      ctxS.moveTo(ax, ay); ctxS.lineTo(bx, by);
    } else if (g[0] < SIM.t) {
      // 正在走的那一段：画一部分
      const k = (SIM.t - g[0]) / Math.max(1e-9, g[1] - g[0]);
      const [ax, ay] = P(g[2], g[3]);
      const [bx, by] = P(g[2] + (g[4] - g[2]) * k, g[3] + (g[5] - g[3]) * k);
      ctxS.moveTo(ax, ay); ctxS.lineTo(bx, by);
    }
  }
  ctxS.stroke();

  // 当前位置（针头）
  const s = simSampleAt(SIM.t);
  if (s) {
    const [px, py] = P(s[1], s[2]);
    const iolv = simIOAt(SIM.t);
    const open = Object.values(iolv).some(v => v === 1);
    // 光晕
    ctxS.beginPath();
    ctxS.arc(px, py, open ? 11 : 7, 0, Math.PI * 2);
    ctxS.fillStyle = open ? 'rgba(90,209,154,.18)' : 'rgba(120,130,145,.12)';
    ctxS.fill();
    // 针头
    ctxS.beginPath();
    ctxS.arc(px, py, 4.5, 0, Math.PI * 2);
    ctxS.fillStyle = open ? '#5ad19a' : '#8b95a5';
    ctxS.fill();
    ctxS.strokeStyle = '#0b0e12';
    ctxS.lineWidth = 1.5;
    ctxS.stroke();
  }

  // ── 右侧：实时仪表 ──
  const rx = leftW + 18;
  const rw = W - rx - 16;
  let ry = padT + 8;

  const iolv = simIOAt(SIM.t);
  const open = Object.values(iolv).some(v => v === 1);

  const line = (label, val, color) => {
    ctxS.fillStyle = '#5a6373';
    ctxS.font = '10.5px ui-monospace, monospace';
    ctxS.fillText(label, rx, ry);
    ctxS.fillStyle = color || '#dfe4ec';
    ctxS.font = '600 15px ui-monospace, monospace';
    ctxS.fillText(val, rx, ry + 19);
    ry += 42;
  };

  // 气阀状态（大字）
  ctxS.fillStyle = open ? 'rgba(90,209,154,.14)' : 'rgba(120,130,145,.08)';
  ctxS.beginPath();
  ctxS.roundRect(rx - 6, ry - 14, rw + 12, 50, 7);
  ctxS.fill();
  ctxS.fillStyle = open ? '#5ad19a' : '#6b7484';
  ctxS.font = '600 16px ui-monospace, monospace';
  ctxS.fillText(open ? '● 气阀 开' : '○ 气阀 关', rx + 4, ry + 18);
  ry += 62;

  line('速度', `${(s ? s[4] : 0).toFixed(1)} mm/s`, '#4ea3ff');
  line('位置 X', (s ? s[1] : 0).toFixed(2) + ' mm', '#dfe4ec');
  line('位置 Y', (s ? s[2] : 0).toFixed(2) + ' mm', '#dfe4ec');
  line('位置 Z', (s ? s[3] : 0).toFixed(2) + ' mm', '#dfe4ec');

  // 通道状态
  const chans = [...new Set(c.sim.io_events.map(e => e[1]))].sort();
  if (chans.length) {
    ctxS.fillStyle = '#5a6373';
    ctxS.font = '10.5px ui-monospace, monospace';
    ctxS.fillText('通道', rx, ry);
    ry += 16;
    const colors = ['#5ad19a', '#ffb454', '#ff6b6b', '#b48cff', '#4ea3ff'];
    chans.forEach((ch, i) => {
      const on = iolv[ch] === 1;
      ctxS.fillStyle = on ? colors[i % colors.length] : '#2b323c';
      ctxS.beginPath();
      ctxS.arc(rx + 5, ry - 3, 4, 0, Math.PI * 2);
      ctxS.fill();
      ctxS.fillStyle = on ? '#dfe4ec' : '#4a525f';
      ctxS.font = '11px ui-monospace, monospace';
      ctxS.fillText(`IO${ch} ${on ? '高' : '低'}`, rx + 15, ry);
      ry += 18;
    });
  }

  // 分隔线
  ctxS.strokeStyle = '#1e242c';
  ctxS.lineWidth = 1;
  ctxS.beginPath();
  ctxS.moveTo(leftW + 8, padT);
  ctxS.lineTo(leftW + 8, H - padB);
  ctxS.stroke();
}

// ══════════════════════════════════════════════════════════
// 底部视图
// ══════════════════════════════════════════════════════════

function drawBottom() {
  if (S.bview === 'gcode') return;
  const W = cvB.clientWidth, H = cvB.clientHeight;
  if (W < 2 || H < 2) return;
  ctxB.clearRect(0, 0, W, H);
  ctxB.fillStyle = '#0b0e12';
  ctxB.fillRect(0, 0, W, H);

  const c = S.lastCompile;
  if (!c || !c.sim) {
    ctxB.fillStyle = '#4a525f';
    ctxB.font = '12px sans-serif';
    ctxB.textAlign = 'center';
    ctxB.fillText('点右上「生成 G-code」后，这里显示阀的开关时机', W / 2, H / 2);
    ctxB.textAlign = 'left';
    return;
  }
  if (S.bview === 'timeline') drawTimeline(W, H, c);
  else if (S.bview === 'top') drawSimTop(W, H, c);
  else if (S.bview === 'zoom') drawStrokeZoom(W, H, c);
}

function drawTimeline(W, H, c) {
  const dur = Math.max(0.001, c.sim.duration);
  const padL = 52, padR = 16, padT = 18, padB = 28;
  const pw = W - padL - padR, ph = H - padT - padB;
  const T = t => padL + (t / dur) * pw;
  const vmax = Math.max(1, c.sim.max_v);
  const V = v => padT + ph * (1 - v / vmax);

  ctxB.strokeStyle = '#1c2129';
  ctxB.lineWidth = 1;
  ctxB.beginPath();
  for (let i = 0; i <= 4; i++) {
    const y = padT + (ph * i) / 4;
    ctxB.moveTo(padL, y); ctxB.lineTo(W - padR, y);
  }
  ctxB.stroke();

  ctxB.strokeStyle = '#4ea3ff';
  ctxB.lineWidth = 1.4;
  ctxB.beginPath();
  let first = true;
  for (const s of c.sim.samples) {
    const px = T(s[0]), py = V(s[4]);
    if (first) { ctxB.moveTo(px, py); first = false; }
    else ctxB.lineTo(px, py);
  }
  ctxB.stroke();

  const ioTop = padT + ph * 0.60;
  const ioH = ph * 0.34;
  const chans = [...new Set(c.sim.io_events.map(e => e[1]))].sort();
  const colors = ['#5ad19a', '#ffb454', '#ff6b6b', '#b48cff', '#4ea3ff'];
  chans.forEach((ch, ci) => {
    const evs = c.sim.io_events.filter(e => e[1] === ch);
    if (!evs.length) return;
    const bandH = ioH / Math.max(1, chans.length);
    const yBase = ioTop + bandH * ci;
    const yHi = yBase + 3;
    const yLo = yBase + bandH * 0.6;
    ctxB.strokeStyle = colors[ci % colors.length];
    ctxB.lineWidth = 1.6;
    ctxB.beginPath();
    let lvl = 0;
    ctxB.moveTo(T(0), yLo);
    for (const e of evs) {
      ctxB.lineTo(T(e[0]), lvl ? yHi : yLo);
      lvl = e[2];
      ctxB.lineTo(T(e[0]), lvl ? yHi : yLo);
    }
    ctxB.lineTo(T(dur), lvl ? yHi : yLo);
    ctxB.stroke();
  });

  ctxB.fillStyle = '#4a525f';
  ctxB.font = '10px ui-monospace, monospace';
  ctxB.fillText(`${vmax.toFixed(0)} mm/s`, 6, padT + 9);
  ctxB.fillText('速度', 6, padT + 22);
  ctxB.fillText('阀', 30, ioTop + 12);
  for (let i = 0; i <= 5; i++) {
    const t = (dur * i) / 5;
    ctxB.fillText(t.toFixed(1) + 's', T(t) - 11, H - 9);
  }
}

function drawSimTop(W, H, c) {
  const segs = c.sim.segments;
  if (!segs.length) return;
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  for (const g of segs) {
    minx = Math.min(minx, g[2], g[4]); maxx = Math.max(maxx, g[2], g[4]);
    miny = Math.min(miny, g[3], g[5]); maxy = Math.max(maxy, g[3], g[5]);
  }
  const pad = 30;
  const w = Math.max(.001, maxx - minx), h = Math.max(.001, maxy - miny);
  const sc = Math.min((W - pad * 2) / w, (H - pad * 2) / h);
  const ox = W / 2 - (minx + maxx) / 2 * sc;
  const oy = H / 2 + (miny + maxy) / 2 * sc;
  const P = (x, y) => [x * sc + ox, -y * sc + oy];

  ctxB.setLineDash([3, 3]);
  ctxB.strokeStyle = '#2b323c';
  ctxB.lineWidth = 1;
  ctxB.beginPath();
  for (const g of segs) {
    if (g[7]) continue;
    const [ax, ay] = P(g[2], g[3]);
    const [bx, by] = P(g[4], g[5]);
    ctxB.moveTo(ax, ay); ctxB.lineTo(bx, by);
  }
  ctxB.stroke();
  ctxB.setLineDash([]);

  ctxB.strokeStyle = '#4ea3ff';
  ctxB.lineWidth = 2;
  ctxB.lineCap = 'round';
  ctxB.beginPath();
  for (const g of segs) {
    if (!g[7]) continue;
    const [ax, ay] = P(g[2], g[3]);
    const [bx, by] = P(g[4], g[5]);
    ctxB.moveTo(ax, ay); ctxB.lineTo(bx, by);
  }
  ctxB.stroke();

  ctxB.fillStyle = 'rgba(255,180,84,.8)';
  for (const tr of c.io_transitions || []) {
    if (tr.x == null) continue;
    const [px, py] = P(tr.x, tr.y);
    ctxB.beginPath();
    ctxB.arc(px, py, 2.4, 0, Math.PI * 2);
    ctxB.fill();
  }
}

function drawStrokeZoom(W, H, c) {
  const segs = c.sim.segments;
  const firstPrint = segs.find(g => g[7]);
  if (!firstPrint) {
    ctxB.fillStyle = '#4a525f';
    ctxB.fillText('没有出料段', 16, 26);
    return;
  }
  const t0 = Math.max(0, firstPrint.t0 - 0.45);
  const t1 = Math.min(c.sim.duration, firstPrint.t1 + 0.45);
  const dur = Math.max(0.001, t1 - t0);
  const padL = 52, padR = 16, padT = 22, padB = 28;
  const pw = W - padL - padR, ph = H - padT - padB;
  const T = t => padL + ((t - t0) / dur) * pw;
  const vmax = Math.max(1, c.sim.max_v);
  const V = v => padT + ph * (1 - v / vmax);

  ctxB.strokeStyle = '#1c2129';
  ctxB.beginPath();
  for (let i = 0; i <= 4; i++) {
    const y = padT + (ph * i) / 4;
    ctxB.moveTo(padL, y); ctxB.lineTo(W - padR, y);
  }
  ctxB.stroke();

  ctxB.strokeStyle = '#4ea3ff';
  ctxB.lineWidth = 2;
  ctxB.beginPath();
  let first = true;
  for (const s of c.sim.samples) {
    if (s[0] < t0 || s[0] > t1) continue;
    const px = T(s[0]), py = V(s[4]);
    if (first) { ctxB.moveTo(px, py); first = false; }
    else ctxB.lineTo(px, py);
  }
  ctxB.stroke();

  ctxB.font = '10px ui-monospace, monospace';
  for (const e of c.sim.io_events) {
    if (e[0] < t0 || e[0] > t1) continue;
    const x = T(e[0]);
    ctxB.setLineDash([3, 3]);
    ctxB.strokeStyle = e[2] ? '#5ad19a' : '#ff6b6b';
    ctxB.lineWidth = 1.2;
    ctxB.beginPath();
    ctxB.moveTo(x, padT); ctxB.lineTo(x, padT + ph);
    ctxB.stroke();
    ctxB.setLineDash([]);
    ctxB.fillStyle = e[2] ? '#5ad19a' : '#ff6b6b';
    ctxB.fillText(e[2] ? '开阀' : '关阀', x + 3, padT + 10);
  }

  const opens = (c.sim.io_events || []).filter(e => e[2] === 1 && e[0] >= t0 && e[0] <= t1);
  const closes = (c.sim.io_events || []).filter(e => e[2] === 0 && e[0] >= t0 && e[0] <= t1);
  ctxB.fillStyle = '#ffb454';
  if (opens.length) {
    ctxB.fillText(`开阀 → 开始运动: ${((firstPrint.t0 - opens[0][0]) * 1000).toFixed(0)}ms`, padL, H - 9);
  }
  if (closes.length) {
    const tail = closes[closes.length - 1][0] - firstPrint.t1;
    ctxB.fillText(`运动停止 → 关阀: ${(tail * 1000).toFixed(0)}ms`, padL + 210, H - 9);
  }
}

// ══════════════════════════════════════════════════════════
// 工程管理
// ══════════════════════════════════════════════════════════

function applyProject(p, { record = false, label = '载入工程' } = {}) {
  const prev = S.project;
  S.project = p;
  S.sel = null;
  S.lastCompile = null;
  S.layerFilter = -1;
  syncProps();
  syncParamsFromProject();
  refreshShapeList();
  updateNextStep();
  fitView();
  drawBottom();
  $('simStats').textContent = '未生成';
  $('simStats').className = 'badge dim';

  if (record) {
    // 首次载入：建立历史起点，不产生可撤销步
    // 之后的操作（新建/载入/导入）：保留历史，让用户能撤销回去
    if (!prev) {
      hist.reset(snapshot(), label);
    } else {
      commit(label);
    }
    syncHistoryUI();
  }
}

function blankProject() {
  return {
    version: 1,
    name: 'untitled',
    machine: {
      name: 'lc-diym', travel_x: 200, travel_y: 200, travel_z: 100,
      soft_limit: true, has_home_switch: false,
      feed_default: 600, feed_rapid: 6000, accel: 300,
    },
    process: {
      rise_ms: 120, tail_ms: 80, lift_z: 0.3, lift_feed: 3000,
      line_width: 0.4, layer_height: 0.4, min_segment_len: 1.5,
    },
    layers: [{ id: 'L1', z: 0.4, repeat: 1, z_step: 0.4, overrides: {}, strokes: [], visible: true }],
    calibration_profile_id: null,
  };
}

$('btnSample').addEventListener('click', async () => {
  const r = await fetch('/api/sample-project');
  applyProject(await r.json(), { record: true });
  hideOnboard();
  setStatus('已载入示例', 'ok');
});

$('btnNew').addEventListener('click', () => {
  applyProject(blankProject(), { record: true });
  hideOnboard();
  setStatus('新工程', 'ok');
});

$('btnSave').addEventListener('click', () => {
  const blob = new Blob([JSON.stringify(S.project, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (S.project.name || 'project') + '.json';
  a.click();
});

$('btnLoad').addEventListener('click', () => $('fileLoad').click());
$('fileLoad').addEventListener('change', async e => {
  const f = e.target.files[0];
  if (!f) return;
  try {
    applyProject(JSON.parse(await f.text()), { record: true });
    hideOnboard();
    setStatus('已载入', 'ok');
  } catch { setStatus('载入失败', 'err'); }
  e.target.value = '';
});

// ══════════════════════════════════════════════════════════
// SVG 导入
// ══════════════════════════════════════════════════════════

$('btnSvg').addEventListener('click', () => $('fileSvg').click());

$('fileSvg').addEventListener('change', async e => {
  const f = e.target.files[0];
  e.target.value = '';
  if (!f) return;

  setStatus('解析 SVG…', 'work');
  try {
    const res = await api('/api/svg-import', {
      svg: await f.text(), scale: 1.0, tol: 0.05,
      flip_y: true, only_closed: false, max_paths: 2000,
    });

    if (!res.count) {
      setStatus('SVG 里没有路径', 'err');
      $('warnings').textContent =
        (res.warnings.length ? res.warnings.join('\n') + '\n' : '') +
        'SVG 里没有可导入的矢量路径。如果是位图或纯文字，需要先在 Inkscape 里转成路径（路径 → 对象转路径）。';
      $('warnings').classList.add('has');
      return;
    }

    const L = ensureLayer();
    const made = [];
    for (const sp of res.paths) {
      if (sp.length < 2) continue;
      const closed = sp.length >= 3 &&
        Math.hypot(sp[0][0] - sp[sp.length - 1][0], sp[0][1] - sp[sp.length - 1][1]) < 0.01;
      made.push({
        id: uid('svg'),
        points: sp.map(([x, y]) => ({ x, y })),
        closed,
        kind: 'contour',
        start: { io: 0, level: 1, delay_ms: S.project.process.rise_ms ?? 120 },
        end: { io: 0, level: 0, delay_ms: S.project.process.tail_ms ?? 80 },
        source: { type: 'svg', pts: sp.map(([x, y]) => ({ x, y })) },
        _name: closed ? 'SVG 闭合路径' : 'SVG 路径',
      });
    }
    L.strokes.push(...made);
    commit(`导入 SVG ${made.length} 条`);

    fitView();
    refreshShapeList();
    updateNextStep();
    draw();

    setStatus(`SVG 导入 ${made.length} 条`, 'ok');
    const msg = [`已导入 ${made.length} 条路径，尺寸 ${res.size.width}×${res.size.height}`];
    if (res.unsupported.length) {
      msg.push(`忽略了不支持的元素：${res.unsupported.map(u => '<' + u + '>').join(' ')}`);
    }
    if (res.warnings.length) msg.push(...res.warnings);
    $('warnings').textContent = msg.join('\n');
    $('warnings').classList.toggle('has', res.unsupported.length > 0);
  } catch (err) {
    setStatus('SVG 导入失败', 'err');
    $('warnings').textContent = String(err);
    $('warnings').classList.add('has');
  }
});

// ══════════════════════════════════════════════════════════
// 3D 模型导入（STL / OBJ）
// ══════════════════════════════════════════════════════════

$('btnMesh').addEventListener('click', () => $('fileMesh').click());

$('fileMesh').addEventListener('change', async e => {
  const f = e.target.files[0];
  e.target.value = '';
  if (!f) return;

  setStatus('读取模型…', 'work');
  try {
    const buf = await f.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let bin = '';
    const CHUNK = 0x8000;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
    }
    const b64 = btoa(bin);

    setStatus('切片中…', 'work');
    const lh = +($('pLayerHeight')?.value || 0.4);
    const res = await api('/api/mesh-slice', {
      filename: f.name,
      data_b64: b64,
      scale: 1.0,
      layer_height: lh,
      max_layers: 200,
      offset_x: 0,
      offset_y: 0,
      only_largest: false,
    });

    if (!res.count) {
      setStatus('切片为空', 'err');
      $('warnings').textContent = (res.warnings || ['没有切出任何层']).join('\n') +
        '\n\n常见原因：模型没有体积（是个平面）、层高太大、或模型在 Z 轴方向高度为 0。';
      $('warnings').classList.add('has');
      return;
    }

    // 把切出来的层变成工程的 layers（每层一个 Layer）
    const proj = blankProject();
    proj.name = f.name.replace(/\.[^.]+$/, '');
    proj.process.layer_height = lh;
    proj.layers = res.layers.map((ly, i) => ({
      id: 'L' + (i + 1),
      z: ly.z,
      repeat: 1,
      z_step: lh,
      overrides: {},
      visible: true,
      strokes: ly.paths.map(p => ({
        id: uid('m'),
        points: p.map(([x, y]) => ({ x, y })),
        closed: p.length >= 3 &&
          Math.hypot(p[0][0] - p[p.length - 1][0], p[0][1] - p[p.length - 1][1]) < 0.01,
        kind: 'contour',
        start: { io: 0, level: 1, delay_ms: 120 },
        end: { io: 0, level: 0, delay_ms: 80 },
        source: null,
        _name: `层 ${i + 1} 轮廓`,
      })),
    }));

    applyProject(proj, { record: true });
    hideOnboard();
    fitView();
    draw();

    const sz = res.bounds?.size || [0, 0, 0];
    setStatus(`模型 ${res.stats.layers} 层`, 'ok');
    const msg = [
      `已导入 ${f.name}`,
      `尺寸 ${sz[0]}×${sz[1]}×${sz[2]} mm，${res.stats.triangles} 个三角形`,
      `切成 ${res.stats.layers} 层（层高 ${lh}mm），共 ${res.stats.paths} 条轮廓`,
    ];
    if (res.warnings.length) msg.push(...res.warnings);
    msg.push('提示：现在只有轮廓。想填实，逐层选中后点「填充选中形状」。');
    $('warnings').textContent = msg.join('\n');
    $('warnings').classList.toggle('has', res.warnings.length > 0);
  } catch (err) {
    setStatus('模型导入失败', 'err');
    $('warnings').textContent = String(err);
    $('warnings').classList.add('has');
  }
});

// ══════════════════════════════════════════════════════════
// 连接与下发
// ══════════════════════════════════════════════════════════

const CONN = {
  connected: false,
  useMock: false,
  jobTimer: null,
};

function setConnDot(state, msg, cls) {
  const dot = $('connDot');
  dot.className = 'connDot' + (state ? ' ' + state : '');
  if (msg != null) {
    const el = $('connMsg');
    el.textContent = msg;
    el.className = 'connMsg xs ' + (cls || '');
  }
}

function targetAddr() {
  return {
    ip: $('ipAddr').value.trim() || '192.168.0.102',
    port: +$('ipPort').value || 5000,
  };
}

async function apiPost(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

$('chkMock').addEventListener('change', async e => {
  CONN.useMock = e.target.checked;
  if (CONN.useMock) {
    setConnDot('work', '启动模拟板…');
    const r = await apiPost('/api/link/mock/start');
    if (r.ok) {
      $('ipAddr').value = '127.0.0.1';
      $('ipPort').value = '5000';
      setConnDot('ok', r.message, 'ok');
      $('mockState').textContent = '运行中';
      CONN.connected = true;
      updateSendBtn();
    } else {
      setConnDot('err', r.message, 'err');
      $('mockState').textContent = '启动失败';
      e.target.checked = false;
      CONN.useMock = false;
    }
  } else {
    await apiPost('/api/link/mock/stop');
    $('mockState').textContent = '未启动';
    $('ipAddr').value = '192.168.0.102';
    CONN.connected = false;
    setConnDot('', '未连接');
    updateSendBtn();
  }
});

$('btnConnect').addEventListener('click', async () => {
  setConnDot('work', '连接中…');
  const t = targetAddr();
  const r = await apiPost('/api/link/connect', t);
  if (r.ok) {
    CONN.connected = true;
    setConnDot('ok', r.message, 'ok');
  } else {
    CONN.connected = false;
    setConnDot('err', r.message, 'err');
  }
  updateSendBtn();
});

$('btnProbe').addEventListener('click', async () => {
  setConnDot('work', '探活中…');
  const r = await apiPost('/api/link/probe', targetAddr());
  setConnDot(r.ok ? 'ok' : 'err', r.message + (r.detail ? ' · ' + r.detail : ''), r.ok ? 'ok' : 'err');
});

function updateSendBtn() {
  const has = !!S.lastCompile;
  $('btnSend').disabled = !has || !CONN.connected;
  $('btnSend').title = !has ? '先生成 G-code' : (!CONN.connected ? '先连接机器' : '把 G-code 发送给机器');
}

$('btnSend').addEventListener('click', async () => {
  if (!S.lastCompile) { toast('先生成 G-code'); return; }
  const t = targetAddr();
  setConnDot('work', '开始发送…');
  const r = await apiPost('/api/job/start', { gcode: S.lastCompile.gcode, ...t });
  if (!r.ok) {
    setConnDot('err', r.message, 'err');
    return;
  }
  $('jobBox').classList.remove('hidden');
  $('btnStopJob').classList.remove('hidden');
  $('btnSend').disabled = true;
  setConnDot('work', r.message);
  pollJob();
});

$('btnStopJob').addEventListener('click', async () => {
  await apiPost('/api/job/stop', targetAddr());
  toast('已请求中止');
});

function pollJob() {
  clearTimeout(CONN.jobTimer);
  const tick = async () => {
    const t = targetAddr();
    const s = await fetch(`/api/job/status?ip=${encodeURIComponent(t.ip)}&port=${t.port}`).then(r => r.json());
    $('jobProgress').style.width = (s.progress * 100).toFixed(1) + '%';
    $('jobText').textContent = `${s.done}/${s.total} 行 · ${s.errors} 错误 · ${s.elapsed}s\n${s.message}`;
    if (s.running) {
      $('jobText').textContent += `\n当前：${s.current}`;
      CONN.jobTimer = setTimeout(tick, 400);
    } else {
      $('btnStopJob').classList.add('hidden');
      updateSendBtn();
      setConnDot(s.errors ? 'err' : 'ok', s.message, s.errors ? 'err' : 'ok');
      if (s.log && s.log.length) {
        $('warnings').textContent = s.log.join('\n');
        $('warnings').classList.add('has');
      }
    }
  };
  tick();
}

$('btnManualSend').addEventListener('click', async () => {
  const line = $('manualCmd').value.trim();
  if (!line) return;
  const r = await apiPost('/api/link/send-one', { ...targetAddr(), line });
  $('manualResult').textContent = r.message;
  $('manualResult').style.color = r.ok ? 'var(--ok)' : 'var(--danger)';
});

$('manualCmd').addEventListener('keydown', e => {
  if (e.key === 'Enter') $('btnManualSend').click();
});

// 常用指令快捷按钮
document.querySelectorAll('[data-cmd]').forEach(b => {
  b.addEventListener('click', () => {
    $('manualCmd').value = b.dataset.cmd;
    $('manualCmd').focus();
  });
});

// ══════════════════════════════════════════════════════════
// 仿真播放控制
// ══════════════════════════════════════════════════════════

$('btnSimPlay').addEventListener('click', simToggle);
$('btnSimReset').addEventListener('click', simReset);
$('simSpeed').addEventListener('change', e => { SIM.speed = +e.target.value; });

// ══════════════════════════════════════════════════════════
// 引导
// ══════════════════════════════════════════════════════════

function hideOnboard() {
  const el = $('onboard');
  if (el) el.classList.add('hidden');
  try { localStorage.setItem('pathcam_onboarded', '1'); } catch {}
}

$('btnOnboardClose')?.addEventListener('click', hideOnboard);
$('btnOnboardSample')?.addEventListener('click', () => $('btnSample').click());

// ══════════════════════════════════════════════════════════
// 启动
// ══════════════════════════════════════════════════════════

(async function boot() {
  resizeAll();
  $('dialect').dispatchEvent(new Event('change'));
  try {
    const r = await fetch('/api/sample-project');
    applyProject(await r.json(), { record: true });
  } catch {
    setStatus('后端未连接', 'err');
  }
  try {
    if (localStorage.getItem('pathcam_onboarded')) hideOnboard();
  } catch {}
  switchView('2d');
  syncHistoryUI();
})();
