'use strict';

import { History } from '/static/history.js';
import { LogView } from '/static/logview.js';
import { Menubar, OptionsBar } from '/static/menubar.js';
import { GeoPanel, MachineView, bindDelayApply, bindSpacingPreset } from '/static/panel.js';
import { GridRuler } from '/static/grid.js';

// ══════════════════════════════════════════════════════════
// 状态
// ══════════════════════════════════════════════════════════

const S = {
  project: null,
  sel: null,
  tool: 'select',
  view: '2d',
  bview: 'logs',
  optRotation: 0,     // 下一个画的图形的旋转角度
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
  GRID?.resize();
  for (const c of [cv, cv3, cvS, cvB]) resizeOne(c);
  draw(); draw3D(); drawSim(); drawBottom();
}
window.addEventListener('resize', resizeAll);

// ══════════════════════════════════════════════════════════
// 2D 画布
// ══════════════════════════════════════════════════════════

function fitView() {
  if (!S.project) return;
  const m = S.project.machine;
  const W = cv.clientWidth, H = cv.clientHeight;

  // ① 先算形状包围盒
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  for (const L of S.project.layers) {
    if (!L.visible) continue;
    for (const st of L.strokes) {
      for (const p of st.points) {
        minx = Math.min(minx, p.x); maxx = Math.max(maxx, p.x);
        miny = Math.min(miny, p.y); maxy = Math.max(maxy, p.y);
      }
    }
  }

  let w, h, cx, cy;
  if (isFinite(minx) && (maxx - minx) > 0.5 && (maxy - miny) > 0.5) {
    // 有形状：框住形状，但至少 30mm（太小的块别放到天上），且不超工作台太多
    w = Math.max(maxx - minx, 30);
    h = Math.max(maxy - miny, 30);
    cx = (minx + maxx) / 2;
    cy = (miny + maxy) / 2;
    w = Math.min(w, m.travel_x * 1.4);
    h = Math.min(h, m.travel_y * 1.4);
  } else {
    // 空工程：显示整个工作台
    w = m.travel_x; h = m.travel_y;
    cx = m.travel_x / 2; cy = m.travel_y / 2;
  }

  const pad = Math.max(20, Math.min(W, H) * 0.07);
  S.scale = clamp(Math.min((W - pad * 2) / w, (H - pad * 2) / h), 0.5, 200);
  S.tx = W / 2 - cx * S.scale;
  S.ty = H / 2 + cy * S.scale;
  S.cam.target = [cx, cy, 0];
  S.cam.dist = Math.max(w, h) * 2.2;
  draw(); draw3D();
}

function drawGrid() {
  if (!GRID || !GRID.gridOn) {
    // 网格关了也要画十字线和工作台边界，否则没有方向感
    drawAxes();
    drawBedOutline();
    return;
  }

  const W = cv.clientWidth, H = cv.clientHeight;
  const g = GRID.gridSize;          // 用户选的网格尺寸（mm）
  const [x0, y0] = s2w(0, H);
  const [x1, y1] = s2w(W, 0);

  // 网格太密就跳着画（屏幕上至少 6px 一格）
  let step = g;
  while (step * S.scale < 6) step *= 2;

  // ── 次网格（细线） ──
  ctx.strokeStyle = '#1e242e';
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = Math.floor(x0 / step) * step; x <= x1; x += step) {
    const px = Math.round(w2s(x, 0)[0]) + 0.5;
    ctx.moveTo(px, 0); ctx.lineTo(px, H);
  }
  for (let y = Math.floor(y0 / step) * step; y <= y1; y += step) {
    const py = Math.round(w2s(0, y)[1]) + 0.5;
    ctx.moveTo(0, py); ctx.lineTo(W, py);
  }
  ctx.stroke();

  // ── 主网格（每 5 格一条稍亮的线）──
  const major = step * 5;
  if (major * S.scale >= 30) {
    ctx.strokeStyle = '#2b3542';
    ctx.beginPath();
    for (let x = Math.floor(x0 / major) * major; x <= x1; x += major) {
      const px = Math.round(w2s(x, 0)[0]) + 0.5;
      ctx.moveTo(px, 0); ctx.lineTo(px, H);
    }
    for (let y = Math.floor(y0 / major) * major; y <= y1; y += major) {
      const py = Math.round(w2s(0, y)[1]) + 0.5;
      ctx.moveTo(0, py); ctx.lineTo(W, py);
    }
    ctx.stroke();
  }

  drawAxes();
  drawBedOutline();
}

/** 工作台边界（机器的可打印范围），像 EzCad 那个灰色方框 */
function drawBedOutline() {
  const m = S.project?.machine;
  if (!m) return;
  const W = cv.clientWidth, H = cv.clientHeight;
  const [ax, ay] = w2s(0, m.travel_y);
  const [bx, by] = w2s(m.travel_x, 0);
  const x = Math.min(ax, bx), y = Math.min(ay, by);
  const w = Math.abs(bx - ax), h = Math.abs(by - ay);

  ctx.fillStyle = 'rgba(78,163,255,.025)';
  ctx.fillRect(x, y, w, h);
  ctx.strokeStyle = 'rgba(78,163,255,.28)';
  ctx.lineWidth = 1;
  ctx.strokeRect(Math.round(x) + 0.5, Math.round(y) + 0.5, Math.round(w), Math.round(h));

  // 左下角标尺寸
  ctx.fillStyle = 'rgba(78,163,255,.55)';
  ctx.font = '10px Consolas, monospace';
  ctx.textBaseline = 'bottom';
  ctx.fillText(`工作台 ${m.travel_x} × ${m.travel_y} mm`, x + 4, y + h - 3);
}

/** 中心十字线 + 原点标记 */
function drawAxes() {
  const W = cv.clientWidth, H = cv.clientHeight;
  const [ox, oy] = w2s(0, 0);

  ctx.strokeStyle = '#3a4756';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, Math.round(oy) + 0.5); ctx.lineTo(W, Math.round(oy) + 0.5);
  ctx.moveTo(Math.round(ox) + 0.5, 0); ctx.lineTo(Math.round(ox) + 0.5, H);
  ctx.stroke();

  // 原点小圆点
  if (ox > -20 && ox < W + 20 && oy > -20 && oy < H + 20) {
    ctx.fillStyle = '#4ea3ff';
    ctx.beginPath();
    ctx.arc(ox, oy, 2.5, 0, Math.PI * 2);
    ctx.fill();
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
    const _t = toolById(st.tool) || (S.project.tools && S.project.tools[0]);
    if (_t) ctx.fillText(`↑${_t.on_channel}/${_t.on_level} ${_t.rise_ms}ms`, sx + 7, sy - 6);
    ctx.fillStyle = '#ffb454';
    if (_t) ctx.fillText(`↓${_t.close_channel ? _t.close_channel() : (_t.off_channel ?? _t.on_channel)}/${_t.off_level} ${_t.tail_ms}ms`, ex + 7, ey + 14);
  }
}

function drawDraft() {
  if (!S.draft) return;
  const d = S.draft;
  const mx = S.mouse.x, my = S.mouse.y;

  // ── 预览轮廓 ──
  ctx.save();
  ctx.setLineDash([6, 4]);
  ctx.strokeStyle = '#ffb454';
  ctx.lineWidth = 1.6;
  ctx.beginPath();

  let info = null;   // 要显示在光标旁的提示文字

  if (d.type === 'line') {
    const [ax, ay] = w2s(d.x0, d.y0);
    const [bx, by] = w2s(mx, my);
    ctx.moveTo(ax, ay); ctx.lineTo(bx, by);
    const len = Math.hypot(mx - d.x0, my - d.y0);
    const ang = Math.atan2(my - d.y0, mx - d.x0) * 180 / Math.PI;
    info = `长 ${len.toFixed(2)} mm　角度 ${ang.toFixed(1)}°`;
  } else if (d.type === 'rect') {
    const x0 = Math.min(d.x0, mx), x1 = Math.max(d.x0, mx);
    const y0 = Math.min(d.y0, my), y1 = Math.max(d.y0, my);
    const [ax, ay] = w2s(x0, y1);
    const [bx, by] = w2s(x1, y0);
    ctx.rect(ax, ay, bx - ax, by - ay);
    info = `${(x1 - x0).toFixed(2)} × ${(y1 - y0).toFixed(2)} mm`;
  } else if (d.type === 'circle') {
    const r = Math.hypot(mx - d.x0, my - d.y0);
    const [cx, cy] = w2s(d.x0, d.y0);
    ctx.arc(cx, cy, r * S.scale, 0, Math.PI * 2);
    // 半径指示线
    const [ex, ey] = w2s(mx, my);
    ctx.moveTo(cx, cy); ctx.lineTo(ex, ey);
    info = `半径 ${r.toFixed(2)} mm　直径 ${(r * 2).toFixed(2)} mm`;
  } else if (d.type === 'ellipse') {
    const rx = Math.abs(mx - d.x0), ry = Math.abs(my - d.y0);
    const [cx, cy] = w2s(d.x0, d.y0);
    ctx.ellipse(cx, cy, rx * S.scale, ry * S.scale, 0, 0, Math.PI * 2);
    info = `长轴 ${(rx * 2).toFixed(2)} × 短轴 ${(ry * 2).toFixed(2)} mm`;
  } else if (d.type === 'polygon') {
    const r = Math.hypot(mx - d.x0, my - d.y0);
    const sides = S.optSides || 6;
    const pts = regularPolygon(d.x0, d.y0, r, sides);
    for (let i = 0; i < pts.length; i++) {
      const [px, py] = w2s(pts[i].x, pts[i].y);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    }
    const [ex, ey] = w2s(mx, my);
    const [cx, cy] = w2s(d.x0, d.y0);
    ctx.moveTo(cx, cy); ctx.lineTo(ex, ey);
    info = `${sides} 边形　外接圆半径 ${r.toFixed(2)} mm`;
  } else if (d.type === 'diamond') {
    const rx = Math.abs(mx - d.x0), ry = Math.abs(my - d.y0);
    const pts = [
      [d.x0, d.y0 - ry], [d.x0 + rx, d.y0],
      [d.x0, d.y0 + ry], [d.x0 - rx, d.y0], [d.x0, d.y0 - ry],
    ];
    for (let i = 0; i < pts.length; i++) {
      const [px, py] = w2s(pts[i][0], pts[i][1]);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    }
    info = `对角线 ${(rx * 2).toFixed(2)} × ${(ry * 2).toFixed(2)} mm`;
  } else if (d.type === 'star') {
    const r = Math.hypot(mx - d.x0, my - d.y0);
    const points = S.optPoints || 5;
    const pts = starPolygon(d.x0, d.y0, r, r * 0.45, points);
    for (let i = 0; i < pts.length; i++) {
      const [px, py] = w2s(pts[i].x, pts[i].y);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    }
    info = `${points} 角　外半径 ${r.toFixed(2)} mm`;
  }
  ctx.stroke();
  ctx.restore();

  // ── 起点吸附标记 ──
  const [sx, sy] = w2s(d.x0, d.y0);
  ctx.strokeStyle = '#ffb454';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(sx, sy, 4, 0, Math.PI * 2);
  ctx.stroke();

  // ── 光标位置标记 ──
  const [ex2, ey2] = w2s(mx, my);
  ctx.fillStyle = '#ffb454';
  ctx.beginPath();
  ctx.arc(ex2, ey2, 3, 0, Math.PI * 2);
  ctx.fill();

  // ── 提示文字（跟随光标，带底色） ──
  if (info) {
    ctx.font = '12px -apple-system, "Segoe UI", sans-serif';
    const tw = ctx.measureText(info).width;
    let bx = ex2 + 14, by = ey2 + 14;
    const W = cv.clientWidth, H = cv.clientHeight;
    if (bx + tw + 14 > W) bx = ex2 - tw - 24;
    if (by + 24 > H) by = ey2 - 30;

    ctx.fillStyle = 'rgba(20,24,31,.94)';
    roundRect(ctx, bx, by, tw + 14, 22, 5);
    ctx.fill();
    ctx.strokeStyle = 'rgba(255,180,84,.45)';
    ctx.lineWidth = 1;
    roundRect(ctx, bx, by, tw + 14, 22, 5);
    ctx.stroke();

    ctx.fillStyle = '#ffd9a0';
    ctx.textBaseline = 'middle';
    ctx.fillText(info, bx + 7, by + 11);
  }

  // ── Shift 正交提示 ──
  if (d.shift) {
    ctx.font = '11px -apple-system, sans-serif';
    ctx.fillStyle = 'rgba(78,163,255,.9)';
    ctx.textBaseline = 'top';
    ctx.fillText('正交约束（按住 Shift）', 12, 12);
  }
}

/** 圆角矩形路径（不用 Path2D，兼容性好些） */
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
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
  GRID?.draw();
  updateStatusbar();
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
      // 记录「按下时的鼠标位置」和「形状原始顶点」，拖动时每帧都从原始位置
      // 重算偏移。之前用增量累加 + 吸附，会把吸附量重复叠加，导致拖不动。
      S.dragging = {
        mode: 'move', id: hit.id,
        startX: wx, startY: wy,
        orig: hit.points.map(p => ({ x: p.x, y: p.y })),
        anchor: { x: hit.points[0].x, y: hit.points[0].y },
        moved: false,
      };
    } else {
      S.sel = null;
    }
    syncProps();
    refreshShapeList();
    updateNextStep();
    draw();
  } else if (S.tool === 'fill') {
    // 油漆桶：点哪个形状就填哪个
    const hit = hitTest(wx, wy);
    if (hit) {
      S.sel = hit.id;
      syncProps();
      refreshShapeList();
      $('btnFill').click();
    } else {
      setStatus('油漆桶要点在形状内部', 'err');
    }
  } else if (S.tool === 'point') {
    // 点工具：一下就是一个墨点，不需要拖
    const [sx, sy] = GRID ? GRID.snap(wx, wy) : [wx, wy];
    S.draft = { type: 'point', x0: sx, y0: sy };
    S.mouse = { x: sx, y: sy };
    commitDraft();
  } else {
    // 起点吸附：贴网格或已有端点
    const [sx, sy] = GRID ? GRID.snap(wx, wy, collectSnapPoints()) : [wx, wy];
    S.draft = { type: S.tool, x0: sx, y0: sy };
    S.mouse = { x: sx, y: sy };
  }
});

/** 收集所有已有点，供端点吸附用 */
function collectSnapPoints() {
  const out = [];
  if (!S.project) return out;
  for (const L of S.project.layers) {
    if (!L.visible) continue;
    for (const st of L.strokes) {
      // 只取顶点，点太多会拖慢（圆有几百个点）
      const n = st.points.length;
      const stride = n > 60 ? Math.ceil(n / 40) : 1;
      for (let i = 0; i < n; i += stride) out.push(st.points[i]);
    }
  }
  return out;
}

cv.addEventListener('mousemove', e => {
  if (S.view !== '2d') return;
  const r = cv.getBoundingClientRect();
  const px = e.clientX - r.left, py = e.clientY - r.top;
  let [wx, wy] = s2w(px, py);

  // 画图时：吸附 + Shift 正交约束
  if (S.draft && GRID) {
    const origin = { x: S.draft.x0, y: S.draft.y0 };
    const [sx, sy] = GRID.snap(wx, wy, collectSnapPoints(), origin);
    wx = sx; wy = sy;
    if (e.shiftKey) {
      const [ox, oy] = GRID.ortho(S.draft.x0, S.draft.y0, wx, wy);
      wx = ox; wy = oy;
    }
    S.draft.shift = e.shiftKey;
  }

  S.mouse = { x: wx, y: wy };
  $('cursorPos').textContent = `${wx.toFixed(2)}, ${wy.toFixed(2)}`;
  const sbx = document.getElementById('sbX'), sby = document.getElementById('sbY');
  if (sbx) { sbx.textContent = wx.toFixed(2); sby.textContent = wy.toFixed(2); }

  if (S.dragging) {
    if (S.dragging.mode === 'pan') {
      S.tx = S.dragging.tx + (px - S.dragging.sx);
      S.ty = S.dragging.ty + (py - S.dragging.sy);
    } else if (S.dragging.mode === 'move') {
      const st = findStroke(S.dragging.id);
      const dg = S.dragging;
      if (st && dg.orig && dg.orig.length === st.points.length) {
        // 总位移 = 当前鼠标 - 按下时的鼠标
        let dx = wx - dg.startX, dy = wy - dg.startY;

        // 吸附：把「锚点将要去的位置」吸到网格上，再反推位移。
        // 注意这是**绝对**计算，不累加——每帧都从 orig 重算，
        // 所以吸附不会越吸越偏。
        if (GRID && GRID.snapOn) {
          const ax = dg.anchor.x + dx, ay = dg.anchor.y + dy;
          const [nx, ny] = GRID.snap(ax, ay);
          dx = nx - dg.anchor.x;
          dy = ny - dg.anchor.y;
        }

        if (Math.abs(dx) > 1e-9 || Math.abs(dy) > 1e-9) dg.moved = true;

        // 从原始位置重算，不是累加
        for (let i = 0; i < st.points.length; i++) {
          st.points[i].x = dg.orig[i].x + dx;
          st.points[i].y = dg.orig[i].y + dy;
        }
      }
    }
    draw();
    return;
  }
  if (S.draft) draw();
  else updateStatusbar();
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
  const shapeName = {
    line: '线段', rect: '矩形', circle: '圆', ellipse: '椭圆',
    polygon: '多边形', diamond: '菱形', star: '星形', point: '点',
  }[d.type];

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
  } else if (d.type === 'ellipse') {
    const rx = Math.abs(mx - d.x0), ry = Math.abs(my - d.y0);
    if (rx < 0.05 || ry < 0.05) { draw(); return; }
    const n = Math.max(36, Math.min(360, Math.ceil(2 * Math.PI * Math.max(rx, ry) / 0.2)));
    pts = [];
    for (let i = 0; i < n; i++) {
      const a = (2 * Math.PI * i) / n;
      pts.push({ x: d.x0 + rx * Math.cos(a), y: d.y0 + ry * Math.sin(a) });
    }
    pts.push({ ...pts[0] });
    src = { type: 'ellipse', cx: d.x0, cy: d.y0, rx, ry, tolerance: 0.05 };
  } else if (d.type === 'polygon') {
    const r = Math.hypot(mx - d.x0, my - d.y0);
    if (r < 0.05) { draw(); return; }
    const sides = S.optSides || 6;
    pts = regularPolygon(d.x0, d.y0, r, sides);
    src = { type: 'polygon', cx: d.x0, cy: d.y0, r, sides };
  } else if (d.type === 'diamond') {
    const rx = Math.abs(mx - d.x0), ry = Math.abs(my - d.y0);
    if (rx < 0.05 || ry < 0.05) { draw(); return; }
    pts = [
      { x: d.x0, y: d.y0 - ry }, { x: d.x0 + rx, y: d.y0 },
      { x: d.x0, y: d.y0 + ry }, { x: d.x0 - rx, y: d.y0 },
      { x: d.x0, y: d.y0 - ry },
    ];
    src = { type: 'diamond', cx: d.x0, cy: d.y0, rx, ry };
  } else if (d.type === 'star') {
    const r = Math.hypot(mx - d.x0, my - d.y0);
    if (r < 0.05) { draw(); return; }
    const points = S.optPoints || 5;
    pts = starPolygon(d.x0, d.y0, r, r * 0.45, points);
    src = { type: 'star', cx: d.x0, cy: d.y0, r, inner: r * 0.45, points };
  } else if (d.type === 'point') {
    // 一个点：用极小的圆表示，G-code 里靠开关阀 + 驻留打出墨滴
    const r = 0.25;
    const n = 12;
    pts = [];
    for (let i = 0; i < n; i++) {
      const a = (2 * Math.PI * i) / n;
      pts.push({ x: d.x0 + r * Math.cos(a), y: d.y0 + r * Math.sin(a) });
    }
    pts.push({ ...pts[0] });
    src = { type: 'circle', cx: d.x0, cy: d.y0, r, tolerance: 0.05 };
  }
  if (!pts) { draw(); return; }

  // 新图形应用选项条上的旋转角度
  const rot = S.optRotation || 0;
  if (rot) {
    let mnx = Infinity, mny = Infinity, mxx = -Infinity, mxy = -Infinity;
    for (const p of pts) {
      mnx = Math.min(mnx, p.x); mxx = Math.max(mxx, p.x);
      mny = Math.min(mny, p.y); mxy = Math.max(mxy, p.y);
    }
    rotatePoints(pts, rot, (mnx + mxx) / 2, (mny + mxy) / 2);
    // 转过之后原始图元描述不再准确，换成折线，避免填充二次旋转
    src = { type: 'polyline', pts: pts.map(p => ({ x: p.x, y: p.y })), tolerance: 0.05 };
  }

  const L = ensureLayer();
  const st = {
    id: uid('s'),
    points: pts,
    closed: pts.length > 2,
    kind: 'contour',
    tool: (S.project.tools && S.project.tools[0]) ? S.project.tools[0].id : 't1',
    source: src,
    _name: shapeName,
    _rot: rot,
  };
  L.strokes.push(st);
  S.sel = st.id;
  commit(`添加${shapeName}`);
  syncProps();
  refreshShapeList();
  refreshSummary();
  updateNextStep();
  draw();
  setStatus(`${shapeName}已添加`, 'ok');
}

/** 正多边形顶点 */
function regularPolygon(cx, cy, r, sides) {
  const pts = [];
  for (let i = 0; i < sides; i++) {
    const a = -Math.PI / 2 + (2 * Math.PI * i) / sides;
    pts.push({ x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) });
  }
  pts.push({ ...pts[0] });
  return pts;
}

/** 星形顶点（外半径 r，内半径 inner，points 个角） */
function starPolygon(cx, cy, r, inner, points) {
  const pts = [];
  for (let i = 0; i < points * 2; i++) {
    const a = -Math.PI / 2 + (Math.PI * i) / points;
    const rad = i % 2 === 0 ? r : inner;
    pts.push({ x: cx + rad * Math.cos(a), y: cy + rad * Math.sin(a) });
  }
  pts.push({ ...pts[0] });
  return pts;
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
  refreshSummary();
  updateNextStep();
  draw();
  toast(`已删除 ${removed._name || '形状'}`);
}

// ══════════════════════════════════════════════════════════
// 菜单用的编辑动作
// ══════════════════════════════════════════════════════════

function deleteSelected() {
  if (!S.sel) { toast('先选中一个形状'); return; }
  deleteStroke(S.sel);
}

function duplicateSelected() {
  if (!S.sel) { toast('先选中一个形状'); return; }
  const st = findStroke(S.sel);
  const L = findStrokeLayer(S.sel);
  if (!st || !L) return;
  const copy = JSON.parse(JSON.stringify(st));
  copy.id = 'st' + Math.random().toString(36).slice(2, 8);
  // 往右下偏 5mm，避免完全重叠看不出来
  for (const p of copy.points) { p.x += 5; p.y += 5; }
  if (copy.source) {
    if (copy.source.type === 'rect') { copy.source.x += 5; copy.source.y += 5; }
    else if (copy.source.type === 'circle') { copy.source.cx += 5; copy.source.cy += 5; }
    else if (Array.isArray(copy.source.pts)) {
      copy.source.pts = copy.source.pts.map(([x, y]) => [x + 5, y + 5]);
    }
  }
  L.strokes.push(copy);
  S.sel = copy.id;
  commit('复制形状');
  syncProps();
  refreshShapeList();
  draw();
  toast('已复制一份');
}

function selectAllStrokes() {
  if (!S.project) return;
  const all = [];
  for (const L of S.project.layers) all.push(...L.strokes);
  if (!all.length) { toast('还没有形状'); return; }
  // 单选模型下"全选"先选第一个，同时把数量显示出来
  S.sel = all[0].id;
  refreshShapeList();
  syncProps();
  draw();
  updateOptSel();
  toast(`共 ${all.length} 个路径（当前选中第 1 个）`);
}

// ══════════════════════════════════════════════════════════
// 属性面板
// ══════════════════════════════════════════════════════════

function syncProps() {
  const st = S.sel ? findStroke(S.sel) : null;
  requestAnimationFrame(() => updateOptSel());
  OPTIONS?.setHasSelection(!!st);
  const panel = $('selPanel');
  if (!st) {
    panel.classList.add('hidden');
    return;
  }
  panel.classList.remove('hidden');
  $('selTitle').textContent = st._name || '形状';
  $('selMeta').textContent = `${st.points.length} 点`;
  // 工具：先填下拉，再显示选中工具的参数
  const sel = $('strokeTool');
  if (sel) {
    ensureTools();
    const tools = S.project.tools || [];
    sel.innerHTML = tools.map(t => {
      const off = t.enabled === false ? '（不输出）' : '';
      return `<option value="${t.id}">${t.name}${off}</option>`;
    }).join('');
    if (st.tool) sel.value = st.tool;
  }
  const tl = toolById(st.tool);
  if (tl) {
    $('toolName').value = tl.name || '';
    $('toolOnChannel').value = tl.on_channel ?? 0;
    $('toolRise').value = tl.rise_ms ?? 120;
    $('toolTail').value = tl.tail_ms ?? 80;
    $('toolEnabled').checked = tl.enabled !== false;
  }
  $('strokeFeed').value = st.feed ?? '';
  $('strokeKind').value = st.kind;
  const rotEl = $('optRotation');
  if (rotEl) rotEl.value = st._rot || 0;
  // 尺寸/中心点数值同步
  try { GEO?.refresh(); } catch {}
}

// ══════════════════════════════════════════════════════════
// ⑦ 改尺寸 / 中心点
// ══════════════════════════════════════════════════════════

/**
 * 把选中笔画缩放到指定尺寸并平移到指定中心。
 * 同时缩放 source，否则后续「填充选中形状」会因为 source 尺寸不匹配而填错。
 */
function applyGeometry({ cx, cy, w, h }) {
  const st = S.sel ? findStroke(S.sel) : null;
  if (!st || !st.points.length) return;

  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  for (const p of st.points) {
    minx = Math.min(minx, p.x); maxx = Math.max(maxx, p.x);
    miny = Math.min(miny, p.y); maxy = Math.max(maxy, p.y);
  }
  const ow = maxx - minx, oh = maxy - miny;
  if (ow < 1e-9 || oh < 1e-9) return;

  const sx = w / ow, sy = h / oh;
  const ocx = (minx + maxx) / 2, ocy = (miny + maxy) / 2;

  for (const p of st.points) {
    p.x = (p.x - ocx) * sx + cx;
    p.y = (p.y - ocy) * sy + cy;
  }

  // source 必须同步，否则填充会用到旧的几何
  const s = st.source;
  if (s) {
    if (s.type === 'rect') {
      s.x = cx - w / 2; s.y = cy - h / 2; s.w = w; s.h = h;
    } else if (s.type === 'circle') {
      s.cx = cx; s.cy = cy; s.r = s.r * (Math.abs(sx) + Math.abs(sy)) / 2;
    } else if (s.type === 'ellipse') {
      s.cx = cx; s.cy = cy;
      s.rx = s.rx * Math.abs(sx); s.ry = s.ry * Math.abs(sy);
    } else if (s.type === 'polygon') {
      s.cx = cx; s.cy = cy;
      s.r = s.r * (Math.abs(sx) + Math.abs(sy)) / 2;
    } else if (s.type === 'diamond') {
      s.cx = cx; s.cy = cy;
      s.rx = s.rx * Math.abs(sx); s.ry = s.ry * Math.abs(sy);
    } else if (s.type === 'star') {
      s.cx = cx; s.cy = cy;
      const k = (Math.abs(sx) + Math.abs(sy)) / 2;
      s.r *= k; s.inner = (s.inner || s.r * 0.45) * k;
    } else if (Array.isArray(s.pts)) {
      for (const p of s.pts) {
        p.x = (p.x - ocx) * sx + cx;
        p.y = (p.y - ocy) * sy + cy;
      }
    }
  }

  commit('改尺寸/位置', 'geo:' + st.id);
  draw();
  refreshShapeList();
  updateOptSel();
}

// ══════════════════════════════════════════════════════════
// 旋转（所有图形通用）
// ══════════════════════════════════════════════════════════

/** 把一组点绕中心旋转 deg 度（逆时针为正） */
function rotatePoints(pts, deg, cx, cy) {
  if (!deg) return pts;
  const a = deg * Math.PI / 180;
  const ca = Math.cos(a), sa = Math.sin(a);
  for (const p of pts) {
    const dx = p.x - cx, dy = p.y - cy;
    p.x = cx + dx * ca - dy * sa;
    p.y = cy + dx * sa + dy * ca;
  }
  return pts;
}

function strokeBBoxCenter(st) {
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  for (const p of st.points) {
    minx = Math.min(minx, p.x); maxx = Math.max(maxx, p.x);
    miny = Math.min(miny, p.y); maxy = Math.max(maxy, p.y);
  }
  return { cx: (minx + maxx) / 2, cy: (miny + maxy) / 2 };
}

/**
 * 把选中形状旋转到绝对角度 deg。
 *
 * ⚠️ 这里只转 points，**不写 source.rotation**，而是把 source 换成旋转后的
 * 折线。原因：后端 discretize_source() 会按 source.rotation 再转一次，
 * 如果两边都转就是双重旋转，填充出来的几何会错位。
 */
function rotateSelected(deg) {
  const st = S.sel ? findStroke(S.sel) : null;
  if (!st) { toast('先选中一个形状'); return; }
  deg = Math.max(-360, Math.min(360, Math.round(deg)));
  const prev = st._rot || 0;
  const delta = deg - prev;
  if (Math.abs(delta) < 1e-9) return;

  const { cx, cy } = strokeBBoxCenter(st);
  rotatePoints(st.points, delta, cx, cy);
  st._rot = deg;

  // source 跟着换成「旋转后的折线」——填充分支走 pts，几何与当前点完全一致
  st.source = {
    type: 'polyline',
    pts: st.points.map(p => ({ x: p.x, y: p.y })),
    tolerance: 0.05,
  };

  commit(`旋转 ${deg}°`, 'rot:' + st.id);
  syncProps();
  draw();
  const el = document.getElementById('optRotation');
  if (el) el.value = deg;
}

/** 应用「下一个画的图形」的旋转角度 */
function setOptRotation(deg) {
  deg = Math.max(-360, Math.min(360, Math.round(deg) || 0));
  S.optRotation = deg;
  const el = document.getElementById('optRotation');
  if (el) el.value = deg;

  const st = S.sel ? findStroke(S.sel) : null;
  if (st) rotateSelected(deg);   // 有选中就顺带转它
}

function bindRotation() {
  const el = document.getElementById('optRotation');
  if (!el) return;

  el.addEventListener('change', () => setOptRotation(+el.value));
  el.addEventListener('keydown', e => { if (e.key === 'Enter') el.blur(); });

  document.getElementById('btnRotPlus')?.addEventListener('click', () => {
    const cur = S.sel ? (findStroke(S.sel)?._rot || 0) : (S.optRotation || 0);
    setOptRotation(cur + 15);
  });
  document.getElementById('btnRotMinus')?.addEventListener('click', () => {
    const cur = S.sel ? (findStroke(S.sel)?._rot || 0) : (S.optRotation || 0);
    setOptRotation(cur - 15);
  });
}

// ══════════════════════════════════════════════════════════
// ⑤ 延时覆盖到全部起点 / 终点
// ══════════════════════════════════════════════════════════

/** 把所有工具的延时统一改成 ms。which: 'start' | 'end'。
 *  P3 起延时属于工具而不是笔画，所以这里是改工具、影响使用它的所有笔画。
 */
function applyDelayToAll(which, ms) {
  if (!S.project) return 0;
  ensureTools();
  const tools = S.project.tools || [];
  for (const t of tools) {
    if (which === 'start') t.rise_ms = ms;
    else t.tail_ms = ms;
  }
  if (tools.length) {
    commit(which === 'start' ? '覆盖所有工具的开阀延时' : '覆盖所有工具的关阀延时');
    syncProps();
    draw();
  }
  return tools.length;
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

// ── 工具面板：改的是工具本身，影响所有用它的笔画 ──
function bindToolField(id, fn, label) {
  const el = $(id);
  if (!el) return;
  const ev = el.tagName === 'SELECT' || el.type === 'checkbox' ? 'change' : 'input';
  el.addEventListener(ev, () => {
    const st = S.sel ? findStroke(S.sel) : null;
    if (!st) return;
    const t = toolById(st.tool);
    if (!t) return;
    fn(t);
    draw();
    commit(label, 'tool:' + id + ':' + t.id);
  });
}

bindToolField('toolName', t => { t.name = $('toolName').value || '未命名'; refreshShapeList(); }, '改工具名');
bindToolField('toolOnChannel', t => { t.on_channel = clamp(+$('toolOnChannel').value | 0, 0, 7); draw(); }, '改工具通道');
bindToolField('toolRise', t => { t.rise_ms = Math.max(0, +$('toolRise').value | 0); }, '改开阀延时');
bindToolField('toolTail', t => { t.tail_ms = Math.max(0, +$('toolTail').value | 0); }, '改关阀延时');
bindToolField('toolEnabled', t => { t.enabled = $('toolEnabled').checked; refreshShapeList(); }, '切换工具输出');

// 笔画换工具
$('strokeTool')?.addEventListener('change', () => {
  const st = S.sel ? findStroke(S.sel) : null;
  if (!st) return;
  st.tool = $('strokeTool').value;
  syncProps();
  draw();
  commit('换工具', 'toolpick:' + st.id);
});

// 新建工具
$('btnNewTool')?.addEventListener('click', () => {
  if (!S.project) return;
  ensureTools();
  const n = S.project.tools.length;
  const t = makeTool(`t${n + 1}`, `工具 ${n + 1}`, Math.min(n, 7), 120, 80, n);
  S.project.tools.push(t);
  const st = S.sel ? findStroke(S.sel) : null;
  if (st) st.tool = t.id;
  commit('新建工具');
  syncProps();
  refreshShapeList();
  draw();
  toast(`已新建「${t.name}」`);
});
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
  const prev = S.view;
  S.view = v;
  document.querySelectorAll('.vtab').forEach(b => b.classList.toggle('active', b.dataset.view === v));
  cv.classList.toggle('hidden', v !== '2d');
  cv3.classList.toggle('hidden', v !== '3d');
  cvS.classList.toggle('hidden', v !== 'sim');
  document.querySelector('.overlay2d').classList.toggle('hidden', v !== '2d');
  document.querySelector('.overlay3d').classList.toggle('hidden', v !== '3d');
  document.querySelector('.overlaySim').classList.toggle('hidden', v !== 'sim');
  document.querySelector('.view3dBar').classList.toggle('hidden', v !== '3d');
  $('machineView')?.classList.toggle('hidden', v !== 'machine');

  // 标尺和画布工具只在 2D 编辑时出现。
  // 它们 z-index 6~8，比 machineView/3D/仿真都高，会盖住那些页面的内容。
  const only2d = v === '2d';
  document.querySelectorAll('.ruler, .rulerCorner').forEach(el => {
    el.classList.toggle('gone', !only2d);
  });
  document.querySelector('.canvasTools')?.classList.toggle('gone', !only2d);
  // 画布区域避让标尺的偏移，非 2D 时归零，让内容占满
  document.querySelector('.canvasWrap')?.classList.toggle('noRuler', !only2d);

  $('viewHint').textContent = {
    '2d': '画形状 · 拖动移动',
    '3d': '成品效果预览',
    'sim': '速度曲线 + 气阀开关时机',
    'machine': 'IO 状态 + 底层通讯',
  }[v] || '';

  // 机器状态页：进来才轮询，离开就停（别在后台一直发指令）
  if (v === 'machine') MACHINE?.onShow();
  else if (prev === 'machine') MACHINE?.onHide();

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
    const isGcode = S.bview === 'gcode';
    const isLogs = S.bview === 'logs';
    $('gcodeBox').classList.toggle('hidden', !isGcode);
    $('logPanel').classList.toggle('hidden', !isLogs);
    cvB.classList.toggle('hidden', isGcode || isLogs);
    if (isLogs) {
      LOGS.clearBadge();
      LOGS.renderAll();
      if (LOGS.pinned) LOGS.scrollToBottom();
    }
    drawBottom();
  });
});

function update3DLayerSlider() {
  const c = S.lastCompile;
  const sl = $('layerSlider');
  const lb = $('layerLabel');

  // 还没生成，或生成失败时没有 sim —— 明确说"未生成"，不要显示"全部 0 层"
  if (!c || !c.sim) {
    sl.min = -1;
    sl.max = 0;
    sl.value = -1;
    sl.disabled = true;
    lb.textContent = '未生成';
    return;
  }

  const printSegs = (c.sim.segments || []).filter(g => g[7] === 1);
  const zs = [...new Set(printSegs.map(g => Math.round(g[6] * 1000) / 1000))].sort((a, b) => a - b);

  sl.disabled = zs.length <= 1;
  sl.min = -1;
  sl.max = Math.max(0, zs.length - 1);
  // 层数变了要钳住旧值，否则切到不存在的层会白屏
  if (S.layerFilter > zs.length - 1) S.layerFilter = -1;
  sl.value = S.layerFilter;

  lb.textContent = S.layerFilter < 0
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
  OPTIONS?.setTool(t);
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
  else if (k === 'e') setTool('ellipse');
  else if (k === 'g') setTool('polygon');
  else if (k === 'd') setTool('diamond');
  else if (k === 's' && !e.ctrlKey && !e.metaKey) setTool('star');
  else if (k === 'p') setTool('point');
  else if (k === 'f') setTool('fill');
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

$('optSides')?.addEventListener('input', e => {
  S.optSides = Math.max(3, Math.min(20, +e.target.value | 0));
});
$('optPoints')?.addEventListener('input', e => {
  S.optPoints = Math.max(3, Math.min(20, +e.target.value | 0));
});
$('btnBottomExpand')?.addEventListener('click', () => {
  const b = document.querySelector('.bottom');
  b.classList.toggle('expanded');
  requestAnimationFrame(() => { resizeAll(); });
});

$('btnSelectAll').addEventListener('click', selectAllStrokes);
$('btnToggleLogs').addEventListener('click', toggleLogs);


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
    refreshSummary();
    updateNextStep();
    drawBottom();
    simReset();
    updateSendBtn();
    // 先切视图再更新层滑块——3D 画布隐藏时 clientWidth 是 0，
    // 提前调用会让 draw3D 算不出范围，层标签也会停在旧值。
    switchView('3d');
    requestAnimationFrame(() => {
      if (resizeOne(cv3)) {
        update3DLayerSlider();
        draw3D();
      }
    });
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
      tool: (S.project.tools && S.project.tools[0]) ? S.project.tools[0].id : 't1',
      source: null,
      _name: '填充线',
    }));
    L.strokes.push(...made);
    commit(`填充 +${made.length} 条`);
    setStatus(`填充 +${made.length} 条`, 'ok');
    refreshShapeList();
    refreshSummary();
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
    refreshShapeList();
    refreshSummary();
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
  refreshSummary();     // 层数从 1 变 6，摘要卡必须跟着变，否则界面和实际不一致
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
    f1: 'G00/G01 纯运动 · 无 IO 控制 · 无驻留（板子不支持 M121/G4）',
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
  if (S.bview === 'gcode' || S.bview === 'logs') return;
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

  // 旧工程迁移：pathCAM 时代的文件没有 app 字段，读进来补上标识。
  // 不做结构改动，只打标记，保证旧文件能继续用。
  if (p && !p.app) {
    p.app = 'cam-b';
    p.app_version = APP_VERSION || null;
    p._migratedFrom = 'pathCAM';
  }

  S.project = p;
  S.sel = null;
  S.lastCompile = null;
  S.layerFilter = -1;
  syncProps();
  syncParamsFromProject();
  refreshShapeList();
  refreshSummary();
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


// ══════════════════════════════════════════════════════════
// 机器设置（行程 / 轴方向反转 / 原点开关）
// ══════════════════════════════════════════════════════════

function openMachineSettings() {
  const m = S.project?.machine;
  if (!m) return;
  $('msTravelX').value = m.travel_x ?? 200;
  $('msTravelY').value = m.travel_y ?? 200;
  $('msTravelZ').value = m.travel_z ?? 100;
  $('msInvX').checked = !!m.invert_x;
  $('msInvY').checked = !!m.invert_y;
  $('msInvZ').checked = !!m.invert_z;
  $('msInvE').checked = !!m.invert_e;
  $('msHomeSwitch').checked = !!m.has_home_switch;
  $('msSoftLimit').checked = !!m.soft_limit;
  $('machineBox').classList.remove('hidden');
}

function closeMachineSettings() {
  $('machineBox').classList.add('hidden');
}

function saveMachineSettings() {
  const m = S.project?.machine;
  if (!m) return;
  const num = (id, dflt) => {
    const v = parseFloat($(id).value);
    return Number.isFinite(v) && v > 0 ? v : dflt;
  };
  m.travel_x = num('msTravelX', 200);
  m.travel_y = num('msTravelY', 200);
  m.travel_z = num('msTravelZ', 100);
  m.invert_x = $('msInvX').checked;
  m.invert_y = $('msInvY').checked;
  m.invert_z = $('msInvZ').checked;
  m.invert_e = $('msInvE').checked;
  m.has_home_switch = $('msHomeSwitch').checked;
  m.soft_limit = $('msSoftLimit').checked;
  closeMachineSettings();
  commit('改机器设置');
  refreshSummary();
  draw();
  // 行程变了要重新适配视野
  try { fitView(); } catch {}
  toast('机器设置已保存');
}

$('btnMachineClose')?.addEventListener('click', closeMachineSettings);
$('btnMachineSave')?.addEventListener('click', saveMachineSettings);
$('machineBox')?.addEventListener('click', (e) => {
  if (e.target.id === 'machineBox') closeMachineSettings();
});

// ══════════════════════════════════════════════════════════
// 工具（一套输出参数 + 一个物理通道）
// ══════════════════════════════════════════════════════════

const TOOL_COLORS = ['#4ea3ff', '#ffb454', '#90d19a', '#c78cff', '#ff7a7a', '#5ad0d0'];

function makeTool(id, name, channel, rise, tail, idx = 0) {
  return {
    id, name, color: TOOL_COLORS[idx % TOOL_COLORS.length],
    enabled: true, visible: true, kind: 'valve',
    on_channel: channel, on_level: 1, rise_ms: rise,
    off_channel: null, off_level: 0, tail_ms: tail,
  };
}

/** 保证工程有工具、笔画都指向有效工具（和 backend 的 ensure_tools 对齐）。 */
function ensureTools() {
  const p = S.project;
  if (!p) return;
  if (!Array.isArray(p.tools) || !p.tools.length) {
    // 旧工程：按笔画里残留的 start/end 归类
    const combos = new Map();
    const loose = [];
    for (const L of p.layers || []) {
      for (const st of L.strokes || []) {
        if (st.start && st.end) {
          const k = [st.start.io, st.start.level, st.start.delay_ms,
                     st.end.io, st.end.level, st.end.delay_ms].join(',');
          if (!combos.has(k)) combos.set(k, { strokes: [], s: st.start, e: st.end });
          combos.get(k).strokes.push(st);
        } else {
          loose.push(st);
        }
      }
    }
    const made = [];
    let i = 0;
    for (const { strokes, s: st, e: en } of combos.values()) {
      const t = makeTool(`t${i + 1}`, i === 0 ? '主挤出' : `工具 ${i + 1}`,
                         st.io, st.delay_ms, en.delay_ms, i);
      t.on_level = st.level;
      t.off_level = en.level;
      if (en.io !== st.io) t.off_channel = en.io;
      made.push(t);
      for (const x of strokes) { x.tool = t.id; delete x.start; delete x.end; }
      i++;
    }
    if (!made.length) {
      made.push(makeTool('t1', '主挤出', 0,
                         p.process?.rise_ms ?? 120, p.process?.tail_ms ?? 80, 0));
    }
    p.tools = made;
    const fb = made[0].id;
    for (const x of loose) { if (!x.tool) x.tool = fb; delete x.start; delete x.end; }
  }
  const ids = new Set(p.tools.map(t => t.id));
  const fb = p.tools[0].id;
  for (const L of p.layers || []) {
    for (const st of L.strokes || []) {
      if (!ids.has(st.tool)) st.tool = fb;
      delete st.start; delete st.end;
    }
  }
}

function toolById(id) {
  return (S.project?.tools || []).find(t => t.id === id) || S.project?.tools?.[0] || null;
}

/** 当前选中笔画用的工具 */
function currentTool() {
  const st = S.sel ? findStroke(S.sel) : null;
  if (!st) return null;
  return toolById(st.tool);
}

// ══════════════════════════════════════════════════════════

function blankProject() {
  return {
    version: 1,
    app: 'cam-b',
    app_version: APP_VERSION || null,
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
    // 工具：一套输出参数 + 一个物理通道。笔画通过 tool 字段引用它。
    tools: [makeTool('t1', '主挤出', 0, 120, 80)],
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
  // 新工程要把视野退回工作台范围，否则上次缩放的位置会让人画到行程外
  fitView();
  draw();
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
        tool: (S.project.tools && S.project.tools[0]) ? S.project.tools[0].id : 't1',
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
        tool: 't1',
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
  jobRunning: false,     // 发送中（控制「中止」按钮）
};

function setConnDot(state, msg, cls) {
  const dot = $('connDot');
  dot.className = 'connDot' + (state ? ' ' + state : '');
  if (msg != null) {
    const el = $('connMsg');
    el.textContent = msg;
    el.className = 'connMsg xs ' + (cls || '');
  }
  // 底部状态栏同步
  const sb = document.getElementById('sbConn');
  if (sb) {
    sb.textContent = cls === 'ok' ? '已连接' : (cls === 'err' ? '连接失败' : (msg || '未连接'));
    sb.className = 'sbConn' + (cls === 'ok' ? ' on' : (cls === 'err' ? ' err' : ''));
  }
}

/** 当前连接方式：serial | udp */
function connMode() {
  return $('connMode')?.value || 'serial';
}

/** 统一的目标参数：串口返回 {port,baud}，UDP 返回 {ip,port} */
function targetAddr() {
  if (connMode() === 'serial') {
    return {
      port: $('serialPort')?.value || 'COM7',
      baud: +($('serialBaud')?.value || 115200),
    };
  }
  return {
    ip: $('ipAddr').value.trim() || '192.168.0.102',
    port: +$('ipPort').value || 5000,
  };
}

/** 按当前连接方式拼 API 路径 */
function apiBase() {
  return connMode() === 'serial' ? '/api/serial' : '/api/link';
}

/**
 * 任务（job）接口的路径前缀。
 * 注意：串口是 /api/serial/job/*，而 UDP 是 /api/job/*（**不带 link**）。
 * 之前统一用 apiBase() 拼，UDP 下会 404。
 */
function jobBase() {
  return connMode() === 'serial' ? '/api/serial/job' : '/api/job';
}

/** 状态查询的 query string */
function statusQuery() {
  const t = targetAddr();
  if (connMode() === 'serial') {
    return `port=${encodeURIComponent(t.port)}&baud=${t.baud}`;
  }
  return `ip=${encodeURIComponent(t.ip)}&port=${t.port}`;
}

async function apiPost(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

// ── 串口列表 ──

async function refreshPorts() {
  try {
    const r = await fetch('/api/serial/ports').then(x => x.json());
    const sel = $('serialPort');
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = '';
    for (const p of r.ports) {
      const opt = document.createElement('option');
      opt.value = p.device;
      opt.textContent = `${p.device} — ${p.description}`;
      if (p.likely_board) opt.textContent += ' ★';
      sel.appendChild(opt);
    }
    // 优先选「可能是板子」的那个
    const board = r.ports.find(p => p.likely_board);
    if (board) sel.value = board.device;
    else if (cur) sel.value = cur;
  } catch (e) {
    console.warn('刷新串口失败', e);
  }
}

$('btnRefreshPorts')?.addEventListener('click', refreshPorts);

// ── 连接方式切换 ──

$('connMode')?.addEventListener('change', () => {
  const serial = connMode() === 'serial';
  $('serialFields').classList.toggle('hidden', !serial);
  $('udpFields').classList.toggle('hidden', serial);
  CONN.connected = false;
  setConnDot('', '未连接');
  updateSendBtn();
});

$('chkMock').addEventListener('change', async e => {
  CONN.useMock = e.target.checked;
  if (CONN.useMock) {
    setConnDot('work', '启动模拟板…');
    const r = await apiPost('/api/link/mock/start');
    if (r.ok) {
      $('connMode').value = 'udp';
      $('connMode').dispatchEvent(new Event('change'));
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
  const r = await apiPost(`${apiBase()}/connect`, t);
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
  const r = await apiPost(`${apiBase()}/probe`, targetAddr());
  let msg = r.message || '';
  if (r.pos) msg += `  · 位置 (${r.pos[0]}, ${r.pos[1]}, ${r.pos[2]})`;
  if (r.detail) msg += `  · ${r.detail}`;
  setConnDot(r.ok ? 'ok' : 'err', msg, r.ok ? 'ok' : 'err');
});

/**
 * 状态门控（防呆）：每个按钮都写明「为什么不能点」。
 * 学 Mainsail 的 status() 谓词 / Fluidd 的 filtered()：
 * 不可用的功能不是静默禁用，而是把原因说出来。
 */
function updateSendBtn() {
  const hasShapes = (S.project?.layers ?? []).some(L => L.strokes.length > 0);
  const has = !!S.lastCompile;
  const conn = CONN.connected;

  // 生成 G-code
  const gen = $('btnCompile');
  if (gen) {
    gen.disabled = !hasShapes;
    gen.title = !hasShapes ? '先在画布上画一个形状' : '把路径编译成机器能执行的 G-code';
  }

  // 发送到机器
  const send = $('btnSend');
  if (send) {
    send.disabled = !has || !conn;
    send.title = !hasShapes ? '先在画布上画一个形状'
      : (!has ? '先点「生成 G-code」' : (!conn ? '先在右侧「连接机器」里连上板子' : '把 G-code 发送给机器'));
  }

  // 中止：只在发送中可用
  const stop = $('btnStopJob');
  if (stop) stop.disabled = !CONN.jobRunning;

  // 填充：没选中形状时说明原因
  const fill = $('btnFill');
  if (fill) {
    fill.disabled = !S.sel;
    fill.title = !S.sel ? '先在画布上选中一个形状（或按 V 用选择工具点一下）' : '给选中的形状生成填充路径';
  }

  // 摘要卡底部给一句人话状态
  const hint = document.getElementById('sumHint');
  if (hint) {
    if (!hasShapes) hint.textContent = '还没画东西 —— 用左侧工具画个矩形试试';
    else if (!has) hint.textContent = '画好了 —— 点右上「生成 G-code」';
    else if (!conn) hint.textContent = '已生成 —— 连接板子后就能发送';
    else hint.textContent = '已就绪 —— 可以发送到机器了';
    hint.className = 'sumHint' + (has && conn ? ' ok' : '');
  }
}

$('btnSend').addEventListener('click', async () => {
  if (!S.lastCompile) { toast('先生成 G-code'); return; }
  const t = targetAddr();
  setConnDot('work', '开始发送…');
  // dwell="host" 的方言（如网口板）不支持 G4，延时靠上位机 sleep，
  // 所以要把它编译时算出的 delays 一起传过去。
  const body = { gcode: S.lastCompile.gcode, ...t };
  if (connMode() === 'udp' && S.lastCompile.delays) {
    body.delays = S.lastCompile.delays;
  }
  const r = await apiPost(`${jobBase()}/start`, body);
  if (!r.ok) {
    setConnDot('err', r.message, 'err');
    return;
  }
  $('jobBox').classList.remove('hidden');
  $('btnStopJob').classList.remove('hidden');
  CONN.jobRunning = true;
  updateSendBtn();
  setConnDot('work', r.message);
  pollJob();
});

$('btnStopJob').addEventListener('click', async () => {
  await apiPost(`${jobBase()}/stop`, targetAddr());
  toast('已请求中止');
});

function pollJob() {
  clearTimeout(CONN.jobTimer);
  const tick = async () => {
    const s = await fetch(`${jobBase()}/status?${statusQuery()}`).then(r => r.json());
    $('jobProgress').style.width = (s.progress * 100).toFixed(1) + '%';
    $('jobText').textContent = `${s.done}/${s.total} 行 · ${s.errors} 错误 · ${s.elapsed}s\n${s.message}`;
    if (s.running) {
      $('jobText').textContent += `\n当前：${s.current}`;
      CONN.jobTimer = setTimeout(tick, 400);
    } else {
      CONN.jobRunning = false;
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
  const r = await apiPost(`${apiBase()}/send-one`, { ...targetAddr(), line });
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
  try { localStorage.setItem('camb_onboarded', '1'); } catch {}
}

$('btnOnboardClose')?.addEventListener('click', hideOnboard);
$('btnOnboardSample')?.addEventListener('click', () => $('btnSample').click());

// ══════════════════════════════════════════════════════════
// 菜单栏 / 工具选项条
// ══════════════════════════════════════════════════════════

let LOGS = null;
let OPTIONS = null;
let GEO = null;
let MACHINE = null;
let GRID = null;

/** 菜单动作表：把菜单项映射到已有函数，不重复实现业务逻辑。 */
const ACTIONS = {
  // 文件
  new: () => $('btnNew').click(),
  sample: () => $('btnSample').click(),
  load: () => $('fileLoad').click(),
  save: () => $('btnSave').click(),
  svg: () => $('fileSvg').click(),
  mesh: () => $('fileMesh').click(),
  exportGcode: () => exportText(S.lastCompile?.gcode, 'gcode'),
  exportCsv: () => exportTimingCsv(),

  // 编辑
  undo: () => doUndo(),
  redo: () => doRedo(),
  duplicate: () => duplicateSelected(),
  delete: () => deleteSelected(),
  selectAll: () => selectAllStrokes(),
  deselect: () => { S.sel = null; refreshShapeList(); syncProps(); updateOptSel(); },

  // 视图
  view2d: () => switchView('2d'),
  view3d: () => switchView('3d'),
  viewSim: () => switchView('sim'),
  fit: () => { fitView(); draw(); },
  zoomIn: () => { S.view.scale *= 1.25; draw(); },
  zoomOut: () => { S.view.scale /= 1.25; draw(); },
  toggleLogs: () => toggleLogs(),

  // 绘制
  toolSelect: () => setTool('select'),
  toolLine: () => setTool('line'),
  toolRect: () => setTool('rect'),
  toolCircle: () => setTool('circle'),
  fillOne: () => $('btnFill').click(),
  fillAll: () => $('btnFillAll').click(),

  // 机器
  connect: () => $('btnConnect').click(),
  probe: () => $('btnProbe').click(),
  send: () => $('btnSend').click(),
  stopJob: () => $('btnStopJob').click(),
  manual: () => window.open('/manual', '_blank'),
  manualBaby: () => window.open('/docs/宝宝级操作手册', '_blank'),
  about: () => showAbout(),
  machineSettings: () => openMachineSettings(),

  // 帮助
  shortcuts: () => showShortcuts(),
  reportF1: () => window.open('/docs/F1板卡测试报告', '_blank'),
  reportNic: () => window.open('/docs/网口板测试报告', '_blank'),
};

function dispatchAction(act) {
  const fn = ACTIONS[act];
  if (fn) {
    try { fn(); } catch (e) { console.error('菜单动作失败', act, e); toast('操作失败：' + e.message); }
  } else {
    console.warn('未实现的菜单动作', act);
  }
}

/** 导出文本文件 */
function exportText(text, ext) {
  if (!text) { toast('先生成 G-code'); return; }
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  const d = new Date();
  const pad = n => String(n).padStart(2, '0');
  a.download = `CAM-B-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}.${ext}`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast('已导出');
}

/** 导出「行号 / 指令 / 延时」的时间序列表，方便核对时序 */
function exportTimingCsv() {
  const c = S.lastCompile;
  if (!c) { toast('先生成 G-code'); return; }
  const lines = c.gcode.split('\n');
  const delays = c.delays || [];
  const rows = [['行号', '指令', '之后等待(秒)']];
  let total = 0;
  lines.forEach((ln, i) => {
    const d = delays[i] || 0;
    total += d;
    rows.push([i + 1, ln.replace(/"/g, '""'), d.toFixed(3)]);
  });
  rows.push([]);
  rows.push(['', '延时合计', total.toFixed(2) + ' 秒']);
  const csv = '\ufeff' + rows.map(r => r.map(x => `"${x}"`).join(',')).join('\r\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'CAM-B-时序表.csv';
  a.click();
  URL.revokeObjectURL(a.href);
  toast(`已导出时序表（延时合计 ${total.toFixed(2)} 秒）`);
}

function toggleLogs() {
  const panel = $('logPanel');
  if (!panel) return;
  const hidden = panel.classList.contains('hidden');
  if (hidden) {
    document.querySelectorAll('.btab').forEach(x => x.classList.remove('active'));
    document.querySelector('.btab[data-bview="logs"]')?.classList.add('active');
    S.bview = 'logs';
    panel.classList.remove('hidden');
    $('gcodeBox').classList.add('hidden');
    cvB.classList.add('hidden');
    LOGS?.clearBadge();
    LOGS?.renderAll();
  } else {
    $('btnToggleLogs')?.classList.remove('active');
    document.querySelector('.btab[data-bview="timeline"]')?.click();
    $('btnToggleLogs')?.classList.add('active');
  }
  $('btnToggleLogs')?.classList.toggle('active', hidden);
}

function showShortcuts() {
  const list = [
    ['V', '选择工具'], ['L', '线段'], ['R', '矩形'], ['C', '圆'],
    ['Ctrl+Z', '撤销'], ['Ctrl+Y / Ctrl+Shift+Z', '重做'],
    ['Ctrl+A', '全选路径'], ['Ctrl+D', '复制一份'],
    ['Delete', '删除选中'], ['Esc', '取消选择'],
    ['Ctrl+S', '保存工程'], ['Ctrl+O', '打开工程'],
    ['+ / -', '放大 / 缩小'], ['滚轮', '缩放'],
  ];
  const text = list.map(([k, v]) => `${k.padEnd(22, ' ')}${v}`).join('\n');
  alert('快捷键\n\n' + text);
}

// ══════════════════════════════════════════════════════════
// 版本号
// ══════════════════════════════════════════════════════════

/** 版本号唯一来源是后端 app/version.py，前端只负责显示。 */
let APP_VERSION = '';
let APP_NAME = 'CAM-B';
let APP_DESC = '';

async function loadVersion() {
  try {
    const r = await fetch('/api/version').then(x => x.json());
    APP_VERSION = r.version || 'unknown';
    APP_NAME = r.app || 'CAM-B';
    APP_DESC = r.desc || '';
  } catch {
    APP_VERSION = 'unknown';
  }
  const el = document.getElementById('sbVersion');
  if (el) {
    el.textContent = 'v' + APP_VERSION;
    el.title = `${APP_NAME} ${APP_VERSION}
点击查看版本信息`;
    // beta 渠道标黄，正式版保持灰色
    el.classList.toggle('beta', APP_VERSION.startsWith('beta'));
  }
  const av = document.getElementById('aboutVer');
  if (av) av.textContent = 'v' + APP_VERSION;
  const an = document.getElementById('aboutName');
  if (an) an.textContent = APP_NAME;
  const ad = document.getElementById('aboutDesc');
  if (ad && APP_DESC) ad.textContent = APP_DESC;
  document.title = `${APP_NAME} ${APP_VERSION} · 路径工艺平台`;
}

function showAbout() {
  const box = document.getElementById('aboutBox');
  if (!box) return;
  const av = document.getElementById('aboutVer');
  if (av) av.textContent = 'v' + (APP_VERSION || '—');
  box.classList.remove('hidden');
}

function bindAbout() {
  document.getElementById('btnAboutClose')?.addEventListener('click', () => {
    document.getElementById('aboutBox')?.classList.add('hidden');
  });
  document.getElementById('aboutBox')?.addEventListener('click', e => {
    if (e.target.id === 'aboutBox') e.target.classList.add('hidden');
  });
  document.getElementById('sbVersion')?.addEventListener('click', showAbout);
  loadVersion();
}

// ══════════════════════════════════════════════════════════
// 打印摘要卡（右面板顶部）
// ══════════════════════════════════════════════════════════

/** 把工程的实际层设置刷到摘要卡上 */
function refreshSummary() {
  const layers = S.project?.layers ?? [];
  const L = layers[0];
  if (!L) return;

  const set = (id, v) => { const e = document.getElementById(id); if (e) e.value = v; };
  const txt = (id, v) => { const e = document.getElementById(id); if (e) e.innerHTML = v; };

  // 层数有两种形态：
  //   ① 手绘工程：一个 layer 带 repeat=N（同一几何打 N 遍）
  //   ② 导入 3D 模型：N 个 layer 各 repeat=1（每层几何不同）
  // 摘要卡必须两种都算对，否则导入模型后显示"1 层 / 0.6mm"是错的。
  const multi = layers.length > 1;
  const totalLayers = layers.reduce((a, x) => a + (x.repeat || 1), 0);

  set('sumLayers', totalLayers);
  set('sumLayerHeight', S.project.process.layer_height ?? 0.4);
  set('sumStartZ', L.z);

  const lh = S.project.process.layer_height ?? 0.4;
  let totalH;
  if (multi) {
    // 取最高那层的 z + 层高
    const zmax = Math.max(...layers.map(x => x.z + ((x.repeat || 1) - 1) * (x.z_step || lh)));
    totalH = zmax + lh - Math.min(...layers.map(x => x.z));
  } else {
    totalH = L.z + (L.repeat - 1) * L.z_step + lh;
  }
  txt('sumHeight', totalH.toFixed(2) + '<i>mm</i>');

  // 多图层时层数输入框改成只读提示（改单层 repeat 没意义）
  const inp = document.getElementById('sumLayers');
  if (inp) {
    inp.readOnly = multi;
    inp.title = multi ? '这是导入的 3D 模型，层数由模型高度决定，不能直接改' : '同一图形重复打印几层';
  }
  ['btnLayerMinus', 'btnLayerPlus'].forEach(id => {
    const b = document.getElementById(id);
    if (b) b.disabled = multi;
  });

  // 笔画数与耗时来自编译结果；没编译过就只显示形状里的笔画数
  const c = S.lastCompile;
  if (c) {
    txt('sumStrokes', String(c.stats.strokes));
    const sec = c.stats.est_seconds || 0;
    txt('sumTime', sec >= 60 ? (sec / 60).toFixed(1) + '<i>min</i>' : Math.round(sec) + '<i>s</i>');
    // 耗时警示：超过 5 分钟橙色，超过 30 分钟红色 —— 通常是层数设多了
    const tEl = document.getElementById('sumTime');
    if (tEl) {
      tEl.className = 'sumValue' + (sec > 1800 ? ' danger' : (sec > 300 ? ' warn' : ''));
      tEl.title = sec > 300
        ? `预计要打 ${(sec/60).toFixed(1)} 分钟。检查一下右上角的「层数」是不是设多了？`
        : '';
    }
    const b = document.getElementById('sumBadge');
    if (b) { b.textContent = '可发送'; b.className = 'badge ok'; }
  } else {
    let n = 0;
    for (const L2 of S.project.layers) n += L2.strokes.length * (L2.repeat || 1);
    txt('sumStrokes', String(n));
    txt('sumTime', '—');
    const b = document.getElementById('sumBadge');
    if (b) { b.textContent = '待生成'; b.className = 'badge dim'; }
  }
  updateSendBtn();
}

/** 改层数（带上下限和撤销记录） */
function setLayerRepeat(n, record = true) {
  const L = ensureLayer();
  n = Math.max(1, Math.min(200, Math.round(n) || 1));
  if (L.repeat === n) return;
  L.repeat = n;
  if (record) commit(`层数改为 ${n}`);
  const el = document.getElementById('layerRepeat');
  if (el) { el.value = n; const o = document.getElementById('outRepeat'); if (o) o.textContent = n; }
  refreshSummary();
  draw(); draw3D();
}

function bindSummary() {
  const wrap = document.querySelector('.summaryCard');
  if (!wrap) return;

  const layers = document.getElementById('sumLayers');
  layers?.addEventListener('change', () => setLayerRepeat(+layers.value));
  layers?.addEventListener('keydown', e => { if (e.key === 'Enter') layers.blur(); });

  document.getElementById('btnLayerMinus')?.addEventListener('click', () => {
    setLayerRepeat((ensureLayer().repeat || 1) - 1);
  });
  document.getElementById('btnLayerPlus')?.addEventListener('click', () => {
    setLayerRepeat((ensureLayer().repeat || 1) + 1);
  });

  const lhEl = document.getElementById('sumLayerHeight');
  lhEl?.addEventListener('change', () => {
    const v = Math.max(0.05, Math.min(2, +lhEl.value || 0.4));
    S.project.process.layer_height = v;
    ensureLayer().z_step = v;      // 层高和层间增量保持一致，避免用户困惑
    commit('改层高');
    syncParamsFromProject();
    refreshSummary();
    draw3D();
  });

  const zEl = document.getElementById('sumStartZ');
  zEl?.addEventListener('change', () => {
    const v = Math.max(0, Math.min(50, +zEl.value || 0.4));
    ensureLayer().z = v;
    commit('改起始Z');
    syncParamsFromProject();
    refreshSummary();
    draw3D();
  });
}

/** 刷新底部状态栏 */
function updateStatusbar() {
  const $s = id => document.getElementById(id);
  if (!$s('sbX')) return;

  $s('sbX').textContent = (S.mouse?.x ?? 0).toFixed(2);
  $s('sbY').textContent = (S.mouse?.y ?? 0).toFixed(2);
  $s('sbZoom').textContent = Math.round(S.scale * 12.5) + '%';
  $s('sbGrid').textContent = (GRID?.gridSize ?? 5) + ' mm';
  $s('sbSnap').textContent = GRID?.snapOn ? '开' : '关';
  $s('sbSnap').style.color = GRID?.snapOn ? 'var(--ok)' : 'var(--dim2)';

  let total = 0;
  for (const L of S.project?.layers ?? []) total += L.strokes.length;
  $s('sbCount').textContent = String(total);

  const sel = S.sel ? findStroke(S.sel) : null;
  $s('sbSel').textContent = sel ? (sel._name || '路径') : '无';

  const dn = { lc: '网口板', f1: 'F1 串口板', marlin: 'Marlin', klipper: 'Klipper' };
  $s('sbDialect').textContent = dn[$('dialect')?.value] || '-';

  const c = $s('sbConn');
  c.textContent = CONN.connected ? '已连接' : '未连接';
  c.className = 'sbConn' + (CONN.connected ? ' on' : '');
}

function updateOptSel() {
  if (!OPTIONS || !S.project) return;
  const all = [];
  for (const L of S.project.layers) all.push(...L.strokes);
  OPTIONS.setSelection(all, S.sel ? [S.sel] : []);
}

// ══════════════════════════════════════════════════════════
// 启动
// ══════════════════════════════════════════════════════════

// 标尺 + 网格 + 吸附
GRID = new GridRuler({
  getTransform: () => ({ tx: S.tx, ty: S.ty, scale: S.scale }),
  getViewSize: () => ({ w: cv.clientWidth, h: cv.clientHeight }),
  getMachine: () => S.project?.machine ?? { travel_x: 200, travel_y: 200 },
  redraw: () => draw(),
});
GRID.resize();
// 状态栏跟着网格设置走
document.getElementById('gridSize')?.addEventListener('change', () => updateStatusbar());
document.getElementById('chkSnap')?.addEventListener('change', () => updateStatusbar());

LOGS = new LogView();
OPTIONS = new OptionsBar();
new Menubar(dispatchAction);

// ⑦ 尺寸/中心点面板 —— 改数值直接改几何，可撤销
GEO = new GeoPanel({
  getSelected: () => {
    const st = S.sel ? findStroke(S.sel) : null;
    const L = S.sel ? findStrokeLayer(S.sel) : null;
    return st ? { stroke: st, layer: L } : null;
  },
  setGeometry: (g) => applyGeometry(g),
  setRatioLock: () => {},
});

// ⑤ 延时覆盖按钮
bindDelayApply({
  applyDelayToAll: (which, ms) => applyDelayToAll(which, ms),
  toast,
});

// ③ 线间距预设
bindSpacingPreset();

// ① 机器状态页
MACHINE = new MachineView({
  queryIO: async () => {
    const r = await apiPost(`${apiBase()}/send-one`, { ...targetAddr(), line: 'M120' });
    return r.raw || r.message || '';
  },
  sendOne: async (line) => {
    const r = await apiPost(`${apiBase()}/send-one`, { ...targetAddr(), line });
    return r.raw || r.message || '';
  },
});

(async function boot() {
  resizeAll();
  $('dialect').dispatchEvent(new Event('change'));
  await refreshPorts();
  $('connMode').dispatchEvent(new Event('change'));
  try {
    const r = await fetch('/api/sample-project');
    applyProject(await r.json(), { record: true });
  } catch {
    setStatus('后端未连接', 'err');
  }
  try {
    if (localStorage.getItem('camb_onboarded') || localStorage.getItem('pathcam_onboarded')) hideOnboard();
  } catch {}
  bindSummary();
  bindRotation();
  bindAbout();
  refreshSummary();
  switchView('2d');
  // 启动后把工作台居中显示，别让原点跑到画布角落
  fitView();
  draw();
  syncHistoryUI();
  setTool('select');
  updateOptSel();
})();

// 调试口：控制台里可以敲 S 看状态（不影响打包，只是个 window 属性）
try { window.S = S; window.OPTIONS = OPTIONS; window.GEO = GEO; window.MACHINE = MACHINE; window.GRID = GRID; window.refreshSummary = refreshSummary; window.CONN = CONN; window.showAbout = showAbout; window.LOGS = LOGS; } catch {}
