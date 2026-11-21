/**
 * 菜单栏 + 工具选项条。
 *
 * 设计：菜单项不直接写业务逻辑，而是派发一个 act 名字，
 * 由 app.js 注册的处理器执行。这样菜单和按钮共用同一套动作，
 * 不会出现"菜单能点但按钮不能用"的分裂。
 */

export class Menubar {
  constructor(onAction) {
    this.onAction = onAction;
    this.openMenu = null;
    this._bind();
  }

  _bind() {
    document.querySelectorAll('.menu').forEach(m => {
      const title = m.querySelector('.menuTitle');

      // 点标题：开/关
      title.addEventListener('click', e => {
        e.stopPropagation();
        if (this.openMenu === m) this.close();
        else this.open(m);
      });

      // 悬停切换（已经开着的时候）
      m.addEventListener('mouseenter', () => {
        if (this.openMenu && this.openMenu !== m) this.open(m);
      });

      // 点菜单项
      m.querySelectorAll('.menuDrop button').forEach(btn => {
        btn.addEventListener('click', e => {
          e.stopPropagation();
          const act = btn.dataset.act;
          this.close();
          if (act) this.onAction(act);
        });
      });
    });

    // 点别处关闭
    document.addEventListener('click', () => this.close());
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') this.close();
    });
  }

  open(m) {
    this.close();
    m.classList.add('open');
    this.openMenu = m;
  }

  close() {
    if (this.openMenu) {
      this.openMenu.classList.remove('open');
      this.openMenu = null;
    }
  }
}

/** 工具选项条：显示当前工具名、提示、选中对象信息 */
export class OptionsBar {
  constructor() {
    this.elTool = document.getElementById('optTool');
    this.elHint = document.getElementById('optHint');
    this.elInfo = document.getElementById('optSelInfo');

    this.names = {
      select: '选择', point: '点', line: '线段', rect: '矩形', circle: '圆',
      ellipse: '椭圆', polygon: '正多边形', diamond: '菱形', star: '星形', fill: '填充',
    };
    this.hints = {
      select: '点形状选中，拖动移动',
      point: '点一下画一个墨点',
      line: '按住左键拖一条线；松开完成',
      rect: '按住左键拖一个矩形；松开完成',
      circle: '按住左键从圆心拖出半径；松开完成',
      ellipse: '按住左键拖出长轴和短轴；松开完成',
      polygon: '按住左键从中心拖出半径；边数在右边调',
      diamond: '按住左键拖出两条对角线；松开完成',
      star: '按住左键从中心拖出外半径；角数在右边调',
      fill: '点哪个形状就填哪个（油漆桶）',
    };
    this.polyBox = document.getElementById('optPolyBox');
    this.starBox = document.getElementById('optStarBox');
    this.fillBox = document.getElementById('optFillBox');
    this.sides = document.getElementById('optSides');
    this.points = document.getElementById('optPoints');
  }

  /** 外部告知当前是否有选中形状，用来决定填充组显隐 */
  setHasSelection(has) {
    this._hasSelection = !!has;
    if (this.fillBox) {
      const show = this._currentTool === 'fill' || !!has;
      this.fillBox.classList.toggle('hidden', !show);
    }
  }

  setTool(tool) {
    this._currentTool = tool;
    if (this.elTool) this.elTool.textContent = this.names[tool] || tool;
    if (this.elHint) this.elHint.textContent = this.hints[tool] || '';
    this.polyBox?.classList.toggle('hidden', tool !== 'polygon');
    this.starBox?.classList.toggle('hidden', tool !== 'star');
    // 填充参数：用填充工具时显示（有选中形状时也显示，方便直接点「填充选中形状」）
    const showFill = tool === 'fill' || !!this._hasSelection;
    this.fillBox?.classList.toggle('hidden', !showFill);
  }

  /** 选中对象信息：数量、尺寸、位置 */
  setSelection(strokes, selIds) {
    if (!this.elInfo) return;
    const list = strokes.filter(s => selIds.includes(s.id));
    if (!list.length) {
      this.elInfo.textContent = '';
      return;
    }
    if (list.length === 1) {
      const s = list[0];
      let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
      for (const p of s.points) {
        minx = Math.min(minx, p.x); maxx = Math.max(maxx, p.x);
        miny = Math.min(miny, p.y); maxy = Math.max(maxy, p.y);
      }
      const w = maxx - minx, h = maxy - miny;
      const cx = (minx + maxx) / 2, cy = (miny + maxy) / 2;
      this.elInfo.textContent =
        `尺寸 ${w.toFixed(2)} × ${h.toFixed(2)} mm　中心 (${cx.toFixed(2)}, ${cy.toFixed(2)})`;
    } else {
      this.elInfo.textContent = `已选 ${list.length} 个`;
    }
  }

  clear() {
    if (this.elInfo) this.elInfo.textContent = '';
  }
}
