/**
 * 输出面板：轮询后端日志，按来源分页、支持搜索/HEX/暂停滚动/导出。
 *
 * 设计要点：
 * - 增量拉取：带上最后一条 seq，后端只返回新增的。刷新页面也不丢。
 * - 批量渲染：一次请求的日志合到同一个 DocumentFragment 里插入，
 *   不逐条 append（672 行打印会产生上千条日志）。
 * - DOM 上限：超过 MAX_NODES 就砍掉最早的，避免页面变卡。
 * - 暂停滚动：用户往上翻时自动停；回到底部就恢复。
 */

const POLL_MS = 600;
const MAX_NODES = 800;

export class LogView {
  constructor() {
    this.lastSeq = 0;
    this.entries = [];        // 全量（用于过滤/导出），上限 MAX_ENTRIES
    this.maxEntries = 3000;
    this.filter = 'all';
    this.search = '';
    this.showHex = false;
    this.paused = false;
    this.pinned = true;       // 是否贴在底部
    this.timer = null;
    this.rendered = 0;

    this.el = {
      panel: document.getElementById('logPanel'),
      body: document.getElementById('logBody'),
      count: document.getElementById('logCount'),
      search: document.getElementById('logSearch'),
      hex: document.getElementById('logHex'),
      pause: document.getElementById('btnLogPause'),
      clear: document.getElementById('btnLogClear'),
      export: document.getElementById('btnLogExport'),
      backBottom: document.getElementById('btnLogBottom'),
      badge: document.getElementById('logBadge'),
      tab: document.querySelector('.btab[data-bview="logs"]'),
    };
    if (!this.el.panel) return;

    this._bind();
    this.start();
  }

  _bind() {
    const { search, hex, pause, clear, export: exp, backBottom, body } = this.el;

    document.querySelectorAll('[data-logfilter]').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('[data-logfilter]').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        this.filter = btn.dataset.logfilter;
        this.renderAll();
      });
    });

    search.addEventListener('input', () => {
      this.search = search.value.trim().toLowerCase();
      this.renderAll();
    });

    hex.addEventListener('change', () => {
      this.showHex = hex.checked;
      this.renderAll();
    });

    pause.addEventListener('click', () => {
      this.paused = !this.paused;
      pause.textContent = this.paused ? '继续' : '暂停';
      pause.classList.toggle('active', this.paused);
      if (!this.paused) this.scrollToBottom();
    });

    clear.addEventListener('click', async () => {
      try {
        await fetch('/api/logs/clear', { method: 'POST' });
      } catch { /* ignore */ }
      this.entries = [];
      this.lastSeq = 0;
      this.renderAll();
    });

    exp.addEventListener('click', () => this.exportText());

    backBottom.addEventListener('click', () => {
      this.pinned = true;
      this.scrollToBottom();
    });

    // 用户滚动时判断是否贴底
    body.addEventListener('scroll', () => {
      const gap = body.scrollHeight - body.scrollTop - body.clientHeight;
      const atBottom = gap < 24;
      this.pinned = atBottom;
      backBottom.classList.toggle('hidden', atBottom);
      if (!this.paused && !atBottom) {
        // 用户往上翻了，自动暂停，避免刷屏
        this.paused = true;
        pause.textContent = '继续';
        pause.classList.add('active');
      }
    });
  }

  start() {
    if (this.timer) return;
    this._tick();
    this.timer = setInterval(() => this._tick(), POLL_MS);
  }

  stop() {
    clearInterval(this.timer);
    this.timer = null;
  }

  async _tick() {
    try {
      const r = await fetch(`/api/logs?after=${this.lastSeq}&limit=800`);
      if (!r.ok) return;
      const d = await r.json();
      if (!d.entries || !d.entries.length) return;

      this.entries.push(...d.entries);
      if (this.entries.length > this.maxEntries) {
        this.entries = this.entries.slice(-this.maxEntries);
      }
      this.lastSeq = d.latest;

      const appended = this._appendLines(d.entries);
      this._updateCount(d);
      this._flashBadge(appended > 0 && this._isHidden());
    } catch { /* 后端没起来时静默 */ }
  }

  /** 只把新增的行插进去（避免整表重排）。 */
  _appendLines(list) {
    const vis = list.filter(e => this._match(e));
    if (!vis.length) return 0;

    const body = this.el.body;
    const frag = document.createDocumentFragment();
    for (const e of vis) frag.appendChild(this._makeLine(e));

    // 没有可见内容时清掉占位
    const empty = body.querySelector('.logEmpty');
    if (empty) empty.remove();

    body.appendChild(frag);

    // 超过上限砍掉最早的
    let nodes = body.querySelectorAll('.logLine');
    if (nodes.length > MAX_NODES) {
      const excess = nodes.length - MAX_NODES;
      for (let i = 0; i < excess; i++) nodes[i].remove();
      nodes = body.querySelectorAll('.logLine');
    }
    this.rendered = nodes.length;

    if (this.pinned && !this.paused) this.scrollToBottom();
    return vis.length;
  }

  _match(e) {
    if (this.filter === 'error' && e.level !== 'error' && e.level !== 'warn') return false;
    if (this.filter === 'conn') {
      if (!(e.src === 'UDP→' || e.src === 'UDP←' || e.src === 'MOCK')) return false;
    }
    if (this.filter === 'job') {
      if (!(e.src === 'JOB' || e.src === 'MOCK')) return false;
    }
    if (this.search) {
      const hay = (e.t + ' ' + e.src + ' ' + e.msg + ' ' + (e.hex || '')).toLowerCase();
      if (!hay.includes(this.search)) return false;
    }
    return true;
  }

  _makeLine(e) {
    const div = document.createElement('div');
    div.className = 'logLine lvl-' + e.level + ' ' + this._srcClass(e.src);

    const t = document.createElement('span');
    t.className = 'logTime';
    t.textContent = e.t;

    const s = document.createElement('span');
    s.className = 'logSrc';
    s.textContent = e.src;

    const m = document.createElement('span');
    m.className = 'logMsg';
    m.textContent = e.msg;
    if (this.showHex && e.hex) {
      const h = document.createElement('span');
      h.className = 'logHex';
      h.textContent = '| ' + e.hex;
      m.appendChild(h);
    }

    div.append(t, s, m);
    return div;
  }

  _srcClass(src) {
    const map = {
      'UDP→': 'src-tx', 'UDP←': 'src-rx', 'JOB': 'src-job',
      'MOCK': 'src-mock', 'HTTP': 'src-http', 'SYS': 'src-sys', 'ERR': 'src-err',
    };
    return map[src] || '';
  }

  /** 过滤条件变了，整表重建。 */
  renderAll() {
    const body = this.el.body;
    const vis = this.entries.filter(e => this._match(e)).slice(-MAX_NODES);
    body.innerHTML = '';
    if (!vis.length) {
      const d = document.createElement('div');
      d.className = 'logEmpty';
      d.textContent = this.entries.length ? '没有匹配的日志' : '暂无日志';
      body.appendChild(d);
      this.rendered = 0;
      this._updateCount(null);
      return;
    }
    const frag = document.createDocumentFragment();
    for (const e of vis) frag.appendChild(this._makeLine(e));
    body.appendChild(frag);
    this.rendered = vis.length;
    this._updateCount(null);
    if (this.pinned) this.scrollToBottom();
  }

  _updateCount(d) {
    const total = this.entries.length;
    const shown = this.el.body.querySelectorAll('.logLine').length;
    this.el.count.textContent =
      d && d.truncated ? `${shown}/${total}（已截断）` : `${shown} / ${total} 条`;
  }

  _isHidden() {
    return this.el.panel.classList.contains('hidden');
  }

  _flashBadge(on) {
    if (!this.el.badge) return;
    this.el.badge.classList.toggle('hidden', !on);
  }

  clearBadge() {
    if (this.el.badge) this.el.badge.classList.add('hidden');
  }

  scrollToBottom() {
    const b = this.el.body;
    b.scrollTop = b.scrollHeight;
    this.el.backBottom.classList.add('hidden');
  }

  exportText() {
    const vis = this.entries.filter(e => this._match(e));
    const lines = vis.map(e => {
      const hex = this.showHex && e.hex ? `  | ${e.hex}` : '';
      return `${e.t} [${e.src}] ${e.msg}${hex}`;
    });
    const blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    const d = new Date();
    const pad = n => String(n).padStart(2, '0');
    a.download = `CAM-B日志-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
  }
}
