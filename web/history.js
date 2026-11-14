/**
 * 撤销 / 重做栈。
 *
 * 设计要点：
 * - 存**快照**而不是操作序列。笔画数据量小（几百 KB），快照最稳，
 *   不会有「逆操作写错」这类难查的 bug。
 * - 用结构化克隆深拷贝，避免共享引用。
 * - 合并窗口：连续拖动/连续输入不产生几十条历史，
 *   同一个 key 在 mergeMs 内只保留最后一条。
 * - 超过上限时丢最旧的（保留最新的 maxStates 条）。
 */

export class History {
  constructor({ maxStates = 100, mergeMs = 600 } = {}) {
    this.maxStates = maxStates;
    this.mergeMs = mergeMs;
    this.stack = [];      // [{ state, label, key, t }]
    this.index = -1;
    this.onChange = null;
  }

  get canUndo() { return this.index > 0; }
  get canRedo() { return this.index >= 0 && this.index < this.stack.length - 1; }

  get undoLabel() {
    return this.canUndo ? this.stack[this.index].label : '';
  }
  get redoLabel() {
    return this.canRedo ? this.stack[this.index + 1].label : '';
  }

  /** 记录一个状态。key 相同且在合并窗口内 → 替换上一条而不是新增。 */
  push(state, label = '修改', key = null) {
    const snap = structuredClone(state);
    const now = Date.now();

    // 合并：同一操作类型的连续修改
    if (
      key &&
      this.index >= 0 &&
      this.stack[this.index].key === key &&
      now - this.stack[this.index].t < this.mergeMs &&
      this.index === this.stack.length - 1
    ) {
      this.stack[this.index].state = snap;
      this.stack[this.index].t = now;
      this.stack[this.index].label = label;
      this._notify();
      return;
    }

    // 丢弃 redo 分支
    if (this.index < this.stack.length - 1) {
      this.stack = this.stack.slice(0, this.index + 1);
    }

    this.stack.push({ state: snap, label, key, t: now });
    if (this.stack.length > this.maxStates) {
      this.stack.shift();
    }
    this.index = this.stack.length - 1;
    this._notify();
  }

  undo() {
    if (!this.canUndo) return null;
    this.index -= 1;
    this._notify();
    return structuredClone(this.stack[this.index].state);
  }

  redo() {
    if (!this.canRedo) return null;
    this.index += 1;
    this._notify();
    return structuredClone(this.stack[this.index].state);
  }

  /** 当前状态（不改变 index）。 */
  current() {
    if (this.index < 0) return null;
    return structuredClone(this.stack[this.index].state);
  }

  clear(state = null, label = '初始') {
    this.stack = [];
    this.index = -1;
    if (state) this.push(state, label);
    else this._notify();
  }

  /** 用外部状态重置（载入工程时），清空历史。 */
  reset(state, label = '载入') {
    this.stack = [];
    this.index = -1;
    this.push(state, label);
  }

  /** 中断合并窗口，让下一次 push 一定新增一条。 */
  breakMerge() {
    if (this.index >= 0) this.stack[this.index].key = null;
  }

  _notify() {
    if (this.onChange) this.onChange(this);
  }

  /** 给 UI 用的摘要。 */
  summary() {
    return {
      canUndo: this.canUndo,
      canRedo: this.canRedo,
      undoLabel: this.undoLabel,
      redoLabel: this.redoLabel,
      index: this.index,
      total: this.stack.length,
      steps: this.index,           // 已走过的步数
    };
  }
}
