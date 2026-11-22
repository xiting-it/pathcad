/**
 * 尺寸坐标面板 + 延时批量覆盖 + 线间距预设 + 机器状态页。
 *
 * 这一组都是 v1.3 文档里的需求：
 *   ③ 填充间隙预设（含 2mm）
 *   ⑤ 延时覆盖到所有起点/终点
 *   ⑦ 设置大小和中心点坐标
 *   ① 板回传数据页（16 路 IO 指示灯 + 通讯日志）
 */

// ══════════════════════════════════════════════════════
// ⑦ 尺寸与中心点
// ══════════════════════════════════════════════════════

export class GeoPanel {
  /**
   * @param {object} host  app.js 提供的接口：
   *   getSelected()  -> {stroke, strokeLayer} | null
   *   setGeometry({cx,cy,w,h}) -> void   由 app.js 负责缩放 + 记录撤销
   *   getRatioLock() / setRatioLock(bool)
   */
  constructor(host) {
    this.host = host;
    this.el = {
      x: document.getElementById('geoX'),
      y: document.getElementById('geoY'),
      w: document.getElementById('geoW'),
      h: document.getElementById('geoH'),
      lock: document.getElementById('btnLockRatio'),
    };
    if (!this.el.x) return;
    this._bind();
  }

  _bind() {
    const { x, y, w, h, lock } = this.el;

    // 回车或失焦时应用（避免每敲一个字符就重算）
    for (const el of [x, y, w, h]) {
      el.addEventListener('keydown', e => {
        if (e.key === 'Enter') { el.blur(); }
      });
      el.addEventListener('blur', () => this._apply(el));
    }

    lock?.addEventListener('click', () => {
      const on = !lock.classList.contains('active');
      lock.classList.toggle('active', on);
      lock.textContent = on ? '🔒' : '🔓';
      this.host.setRatioLock(on);
    });
  }

  _apply(changed) {
    const sel = this.host.getSelected();
    if (!sel) return;

    const nums = {
      x: parseFloat(this.el.x.value),
      y: parseFloat(this.el.y.value),
      w: parseFloat(this.el.w.value),
      h: parseFloat(this.el.h.value),
    };
    for (const k of ['x', 'y', 'w', 'h']) {
      if (!isFinite(nums[k])) { this.refresh(); return; }
    }
    if (nums.w <= 0 || nums.h <= 0) { this.refresh(); return; }

    // 锁定宽高比：只联动「另一个」框。
    // 关键：不能靠连续两次 blur 分别改宽改高——第二次 blur 时第一个值
    // 还没写进笔画，refresh 会把用户刚敲的值冲掉，最后两边都变成同一个数。
    const locked = this.el.lock?.classList.contains('active');
    const ratio = this._ratio || 1;
    if (locked) {
      if (changed === this.el.w) {
        nums.h = nums.w / ratio;
        this.el.h.value = nums.h.toFixed(2);
      } else if (changed === this.el.h) {
        nums.w = nums.h * ratio;
        this.el.w.value = nums.w.toFixed(2);
      }
    }

    this._suppressRefresh = true;
    try {
      this.host.setGeometry({ cx: nums.x, cy: nums.y, w: nums.w, h: nums.h });
    } finally {
      this._suppressRefresh = false;
    }
  }

  /** 选中对象变了，把数值填进去 */
  refresh() {
    // apply 过程中不要回填，否则会覆盖用户刚敲的值
    if (this._suppressRefresh) return;

    const sel = this.host.getSelected();
    if (!sel || !sel.stroke) return;
    const st = sel.stroke;
    if (!st.points?.length) return;

    let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    for (const p of st.points) {
      minx = Math.min(minx, p.x); maxx = Math.max(maxx, p.x);
      miny = Math.min(miny, p.y); maxy = Math.max(maxy, p.y);
    }
    const w = maxx - minx, h = maxy - miny;
    this.el.x.value = ((minx + maxx) / 2).toFixed(2);
    this.el.y.value = ((miny + maxy) / 2).toFixed(2);
    this.el.w.value = w.toFixed(2);
    this.el.h.value = h.toFixed(2);
    this._ratio = h > 1e-9 ? w / h : 1;
  }
}

// ══════════════════════════════════════════════════════
// ⑤ 延时覆盖到全部起点 / 终点
// ══════════════════════════════════════════════════════

export function bindDelayApply(host) {
  const rise = document.getElementById('pRise');
  const tail = document.getElementById('pTail');
  const btnRise = document.getElementById('btnApplyRise');
  const btnTail = document.getElementById('btnApplyTail');

  btnRise?.addEventListener('click', () => {
    const v = +rise.value;
    const n = host.applyDelayToAll('start', v);
    host.toast(n ? `已把起点延时 ${v}ms 应用到 ${n} 条路径` : '还没有路径可以应用');
  });

  btnTail?.addEventListener('click', () => {
    const v = +tail.value;
    const n = host.applyDelayToAll('end', v);
    host.toast(n ? `已把终点延时 ${v}ms 应用到 ${n} 条路径` : '还没有路径可以应用');
  });
}

// ══════════════════════════════════════════════════════
// ③ 线间距预设
// ══════════════════════════════════════════════════════

export function bindSpacingPreset() {
  const preset = document.getElementById('fillSpacingPreset');
  const hidden = document.getElementById('fillSpacing');
  const customBox = document.getElementById('paramSpacingCustom');
  const custom = document.getElementById('fillSpacingCustom');
  const out = document.getElementById('outSpacing');
  if (!preset || !hidden) return;

  const sync = val => {
    hidden.value = String(val);
    hidden.dispatchEvent(new Event('input', { bubbles: true }));
  };

  preset.addEventListener('change', () => {
    if (preset.value === 'custom') {
      customBox?.classList.remove('hidden');
      sync(+custom.value);
    } else {
      customBox?.classList.add('hidden');
      sync(+preset.value);
    }
  });

  custom?.addEventListener('input', () => {
    if (out) out.textContent = custom.value;
    if (preset.value === 'custom') sync(+custom.value);
  });

  // 初始化
  sync(+preset.value);
}

// ══════════════════════════════════════════════════════
// ① 机器状态页：16 路 IO 指示灯 + 通讯日志
// ══════════════════════════════════════════════════════

export class MachineView {
  constructor(host) {
    this.host = host;
    this.timer = null;
    this.el = {
      view: document.getElementById('machineView'),
      outGrid: document.getElementById('ioOutGrid'),
      inGrid: document.getElementById('ioInGrid'),
      orgGrid: document.getElementById('ioOrgGrid'),
      raw: document.getElementById('ioRaw'),
      stamp: document.getElementById('ioTimestamp'),
      logBody: document.getElementById('machineLogBody'),
      btnPoll: document.getElementById('btnPollIO'),
      chkAuto: document.getElementById('chkAutoPoll'),
      cx: document.getElementById('coordX'),
      cy: document.getElementById('coordY'),
      cz: document.getElementById('coordZ'),
      cu: document.getElementById('coordU'),
      cstate: document.getElementById('coordState'),
    };
    if (!this.el.view) return;

    this._buildCells(this.el.outGrid, 8, 'OUT');
    this._buildCells(this.el.inGrid, 8, 'IN');
    this._buildCells(this.el.orgGrid, 8, 'ORG');

    this.el.btnPoll?.addEventListener('click', () => this.poll());
    this.el.chkAuto?.addEventListener('change', () => {
      if (this.el.chkAuto.checked) this.startAuto();
      else this.stopAuto();
    });

    document.querySelectorAll('[data-io]').forEach(b => {
      b.addEventListener('click', () => this.sendCommand(b.dataset.io));
    });
  }

  _buildCells(box, n, kind) {
    if (!box) return;
    box.innerHTML = '';
    for (let i = 0; i < n; i++) {
      const c = document.createElement('div');
      c.className = 'ioCell';
      c.dataset.idx = String(i);
      const num = document.createElement('span');
      num.className = 'ioNum';
      num.textContent = String(i);
      const val = document.createElement('span');
      val.textContent = '0';
      c.append(num, val);
      box.appendChild(c);
    }
  }

  _setCells(box, mask) {
    if (!box) return;
    for (const c of box.children) {
      const i = +c.dataset.idx;
      const on = (mask >> i) & 1;
      c.classList.toggle('on', !!on);
      c.lastChild.textContent = on ? '1' : '0';
    }
  }

  /** 读一次 M120 + M114（坐标） */
  async poll() {
    try {
      const r = await this.host.queryIO();
      // 板子正被任务占用时返回一句提示，不要当数据也不要刷日志
      if (String(r).includes('板子忙')) {
        if (this.el.stamp) this.el.stamp.textContent = '板子忙';
      } else {
        this.update(r);
        this.log('RX', r);
      }
    } catch (e) {
      this.log('ERR', `读取失败：${e.message}`);
    }
    await this.pollCoords(false);
  }

  /** 单独读一次坐标。轮询时 log=false，不刷屏 */
  async pollCoords(log = true) {
    try {
      const r = await this.host.queryCoords();
      if (r.busy) {
        // 板子正被任务占用，保留上次数值，只改状态文字
        if (this.el.cstate) this.el.cstate.textContent = '板子忙';
        return;
      }
      if (log) this.log('RX', r.text || '(无回包)');
      this.updateCoords(r.text);
    } catch (e) {
      if (this.el.cstate) this.el.cstate.textContent = '读取失败';
    }
  }

  /** 从 M114 回包解析 X/Y/Z/E 并显示。位置行格式 `X:0.000 Y:0.000 ...` */
  updateCoords(raw) {
    if (!raw) {
      if (this.el.cstate) this.el.cstate.textContent = '读不到坐标';
      return;
    }
    const txt = String(raw);
    const g = ax => {
      // 用 [0-9.] 而不是 \d，避免字符串里的反斜杠被当成转义吃掉
      const m = txt.match(new RegExp(ax + ':(-?[0-9.]+)'));
      return m ? m[1] : null;
    };
    const pairs = [
      [this.el.cx, g('X')],
      [this.el.cy, g('Y')],
      [this.el.cz, g('Z')],
      [this.el.cu, g('E')],   // 板子第四轴叫 E，界面显示 U
    ];
    let any = false;
    for (const [el, v] of pairs) {
      if (!el || v === null) continue;
      any = true;
      const nv = Number(v).toFixed(3);
      if (el.textContent !== nv) {
        el.textContent = nv;
        // 数字变了就闪一下蓝色，让"在动"看得见
        el.classList.add('moved');
        clearTimeout(el._t);
        el._t = setTimeout(() => el.classList.remove('moved'), 260);
      }
    }
    if (this.el.cstate) {
      this.el.cstate.textContent = any ? '实时' : '解析失败';
    }
  }

  /** 从 M120 回包里解析三组掩码 */
  update(raw) {
    if (!raw) return;
    const txt = String(raw);

    const grab = re => {
      const m = txt.match(re);
      return m ? parseInt(m[1], 16) : null;
    };
    // 注意：回包里 E000 在最前面，三组数据跟在后面，
    // 用 [\s\S] 而不是 . 来跨行匹配更保险
    const out = grab(/IOOUT\s+(0x[0-9A-Fa-f]{1,2})/i);
    const inp = grab(/IOIN\s+(0x[0-9A-Fa-f]{1,2})/i);
    const org = grab(/IOORG\s+(0x[0-9A-Fa-f]{1,2})/i);

    if (out !== null) this._setCells(this.el.outGrid, out);
    if (inp !== null) this._setCells(this.el.inGrid, inp);
    if (org !== null) this._setCells(this.el.orgGrid, org);

    if (this.el.raw) {
      this.el.raw.textContent = txt.replace(/\r/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 200);
    }
    if (this.el.stamp) {
      const d = new Date();
      const p = n => String(n).padStart(2, '0');
      this.el.stamp.textContent = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    }
  }

  async sendCommand(cmd) {
    try {
      this.log('TX', cmd);
      const r = await this.host.sendOne(cmd);
      this.log('RX', r || '(无回包)');
      if (cmd.startsWith('M120')) this.update(r);
    } catch (e) {
      this.log('ERR', e.message);
    }
  }

  log(kind, text) {
    const body = this.el.logBody;
    if (!body) return;
    const empty = body.querySelector('.logEmpty');
    if (empty) empty.remove();

    const d = document.createElement('div');
    d.className = 'logLine ' + (kind === 'ERR' ? 'lvl-error' : kind === 'TX' ? 'src-tx' : 'src-rx');
    const now = new Date();
    const p = n => String(n).padStart(2, '0');
    const ts = document.createElement('span');
    ts.className = 'logTime';
    ts.textContent = `${p(now.getHours())}:${p(now.getMinutes())}:${p(now.getSeconds())}`;
    const tag = document.createElement('span');
    tag.className = 'logSrc';
    tag.textContent = kind === 'TX' ? 'UDP→' : kind === 'RX' ? 'UDP←' : 'ERR';
    const msg = document.createElement('span');
    msg.className = 'logMsg';
    msg.textContent = String(text).replace(/\r/g, '\\r').replace(/\n/g, '\\n');
    d.append(ts, tag, msg);
    body.appendChild(d);

    while (body.children.length > 300) body.removeChild(body.firstChild);
    body.scrollTop = body.scrollHeight;
  }

  startAuto() {
    this.stopAuto();
    this.poll();
    this.timer = setInterval(() => this.poll(), 1000);
  }

  stopAuto() {
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
  }

  /** 切到本页时调用 */
  onShow() {
    this.pollCoords(false);
    this.poll();
  }

  /** 切走时停掉轮询，免得一直在发指令 */
  onHide() {
    this.stopAuto();
    if (this.el.chkAuto) this.el.chkAuto.checked = false;
  }
}
