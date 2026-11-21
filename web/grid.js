/**
 * 标尺 + 网格 + 吸附。
 *
 * 这是"像不像工业软件"最关键的一组细节：
 *   - 标尺：让你知道画的东西到底多大（EzCad/CorelDRAW/LightBurn 都有）
 *   - 网格：对齐的视觉参考
 *   - 吸附：保证画出来的是整数尺寸，不是 37.38mm
 *
 * 标尺刻度自适应：缩放小时显示 10/50mm 刻度，放大后显示 1/0.5mm 刻度。
 */

export class GridRuler {
  /**
   * @param {object} host
   *   getTransform() -> {tx, ty, scale}   世界→屏幕的变换
   *   getViewSize()  -> {w, h}           画布像素尺寸
   *   getMachine()   -> {travel_x, travel_y}
   */
  constructor(host) {
    this.host = host;
    this.top = document.getElementById('rulerTop');
    this.left = document.getElementById('rulerLeft');
    this.gridOn = true;
    this.snapOn = true;
    this.gridSize = 5;
    this._bind();
  }

  _bind() {
    document.getElementById('chkGrid')?.addEventListener('change', e => {
      this.gridOn = e.target.checked;
      this.host.redraw();
    });
    document.getElementById('chkSnap')?.addEventListener('change', e => {
      this.snapOn = e.target.checked;
    });
    document.getElementById('gridSize')?.addEventListener('change', e => {
      this.gridSize = +e.target.value || 5;
      this.host.redraw();
    });
  }

  /** 选一个合适的刻度间距（mm），保证屏幕上相邻刻度 ≥ 40px */
  _pickStep(scale) {
    const candidates = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500];
    for (const c of candidates) {
      if (c * scale >= 40) return c;
    }
    return 1000;
  }

  resize() {
    for (const [cv, w, h] of this._sizes()) {
      if (!cv) continue;
      const dpr = window.devicePixelRatio || 1;
      cv.width = Math.max(1, Math.round(w * dpr));
      cv.height = Math.max(1, Math.round(h * dpr));
      cv.style.width = w + 'px';
      cv.style.height = h + 'px';
    }
  }

  _sizes() {
    const stage = this.top?.parentElement;
    if (!stage) return [];
    const W = stage.clientWidth, H = stage.clientHeight;
    return [
      [this.top, Math.max(1, W - 22), 22],
      [this.left, 22, Math.max(1, H - 22)],
    ];
  }

  /** 重画两条标尺 */
  draw() {
    if (!this.top || !this.left) return;
    const { tx, ty, scale } = this.host.getTransform();
    const { w: cw, h: ch } = this.host.getViewSize();

    const dpr = window.devicePixelRatio || 1;
    const ctxT = this.top.getContext('2d');
    const ctxL = this.left.getContext('2d');
    ctxT.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctxL.setTransform(dpr, 0, 0, dpr, 0, 0);

    const TW = this.top.width / dpr;
    const TH = this.left.height / dpr;

    ctxT.clearRect(0, 0, TW, 22);
    ctxL.clearRect(0, 0, 22, TH);

    const step = this._pickStep(scale);
    const minor = step / 5;

    // ── 上标尺 ──
    const x0 = -tx / scale;              // 左边缘对应的世界坐标
    const x1 = (cw - tx) / scale;        // 右边缘
    this._ticks(ctxT, x0, x1, step, minor, scale, tx, 0, TW, 22, 'h');

    // ── 左标尺（Y 轴向上为正，屏幕向下为正，要翻转）──
    const yTop = (ty - 0) / scale;       // 屏幕顶部对应世界 y
    const yBot = (ty - ch) / scale;
    const lo = Math.min(yTop, yBot), hi = Math.max(yTop, yBot);
    this._ticksY(ctxL, lo, hi, step, minor, scale, ty, TH);
  }

  /** 横向刻度 */
  _ticks(ctx, lo, hi, step, minor, scale, offset, _y, W, H, dir) {
    ctx.font = '10px Consolas, monospace';
    ctx.textBaseline = 'top';
    ctx.textAlign = 'center';

    // 次刻度
    ctx.strokeStyle = '#2a323d';
    ctx.beginPath();
    let v = Math.floor(lo / minor) * minor;
    while (v <= hi) {
      const px = Math.round(v * scale + offset) + 0.5;
      if (px >= 0 && px <= W) {
        ctx.moveTo(px, H - 5);
        ctx.lineTo(px, H);
      }
      v += minor;
    }
    ctx.stroke();

    // 主刻度 + 数字
    ctx.strokeStyle = '#4a5566';
    ctx.fillStyle = '#8892a3';
    ctx.beginPath();
    v = Math.floor(lo / step) * step;
    while (v <= hi) {
      const px = Math.round(v * scale + offset) + 0.5;
      if (px >= 0 && px <= W) {
        ctx.moveTo(px, H - 10);
        ctx.lineTo(px, H);
        ctx.fillText(this._fmt(v), px, 3);
      }
      v += step;
    }
    ctx.stroke();

    // 原点标记
    if (0 >= lo && 0 <= hi) {
      const px = Math.round(0 * scale + offset) + 0.5;
      if (px >= 0 && px <= W) {
        ctx.strokeStyle = '#4ea3ff';
        ctx.beginPath();
        ctx.moveTo(px, 0);
        ctx.lineTo(px, H);
        ctx.stroke();
      }
    }
  }

  /** 纵向刻度（数字旋转 90°） */
  _ticksY(ctx, lo, hi, step, minor, scale, ty, H) {
    ctx.font = '10px Consolas, monospace';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'center';

    ctx.strokeStyle = '#2a323d';
    ctx.beginPath();
    let v = Math.floor(lo / minor) * minor;
    while (v <= hi) {
      const py = Math.round(ty - v * scale) + 0.5;
      if (py >= 0 && py <= H) {
        ctx.moveTo(13, py);
        ctx.lineTo(22, py);
      }
      v += minor;
    }
    ctx.stroke();

    ctx.strokeStyle = '#4a5566';
    ctx.fillStyle = '#8892a3';
    v = Math.floor(lo / step) * step;
    while (v <= hi) {
      const py = Math.round(ty - v * scale) + 0.5;
      if (py >= 0 && py <= H) {
        ctx.beginPath();
        ctx.moveTo(8, py);
        ctx.lineTo(22, py);
        ctx.stroke();
        ctx.save();
        ctx.translate(8, py);
        ctx.rotate(-Math.PI / 2);
        ctx.fillText(this._fmt(v), 0, 0);
        ctx.restore();
      }
      v += step;
    }

    if (0 >= lo && 0 <= hi) {
      const py = Math.round(ty - 0 * scale) + 0.5;
      if (py >= 0 && py <= H) {
        ctx.strokeStyle = '#4ea3ff';
        ctx.beginPath();
        ctx.moveTo(0, py);
        ctx.lineTo(22, py);
        ctx.stroke();
      }
    }
  }

  _fmt(v) {
    if (Math.abs(v) < 1e-9) return '0';
    return Number.isInteger(v) ? String(v) : v.toFixed(1);
  }

  // ── 吸附 ──
  // 返回吸附后的世界坐标。snapOn 关闭时原样返回。

  snap(x, y, extraPoints = null, exclude = null) {
    if (!this.snapOn) return [x, y];

    const { scale } = this.host.getTransform();

    // ① 端点吸附优先（阈值按屏幕像素换算）
    if (extraPoints?.length) {
      const thr = 8 / scale;   // 8 像素对应世界距离
      let best = null, bd = thr;
      for (const p of extraPoints) {
        // 排除起点自己，否则刚起拖就被吸回去，宽高永远是 0
        if (exclude && Math.abs(p.x - exclude.x) < 1e-9 && Math.abs(p.y - exclude.y) < 1e-9) {
          continue;
        }
        const d = Math.hypot(p.x - x, p.y - y);
        if (d < bd) { bd = d; best = p; }
      }
      if (best) return [best.x, best.y];
    }

    // ② 网格吸附
    const g = this.gridSize;
    return [Math.round(x / g) * g, Math.round(y / g) * g];
  }

  /** Shift 正交约束：把终点拉到水平或垂直 */
  ortho(x0, y0, x, y) {
    const dx = Math.abs(x - x0), dy = Math.abs(y - y0);
    if (dx >= dy) return [x, y0];
    return [x0, y];
  }
}
