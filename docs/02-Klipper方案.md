# 02 · Klipper 方案

> 共用部分见 [00-共用内核](00-共用内核.md)。本文只写这条路**独有**的东西。
>
> **这是三条路里技术上最正确的一条。** 原因只有一个：Klipper 的 `SET_PIN` 是**随运动排队的**。

---

## 0. 为什么 Klipper 是对的

`klippy/extras/output_pin.py` 里，`SET_PIN` 走的是 `GCodeRequestQueue`，注册了 `toolhead.register_lookahead_callback` 和 `motion_queuing.register_flush_callback`。

翻译成人话：**`SET_PIN` 不是收到就立刻执行，而是被塞进运动队列，在「队列里前面的运动都跑完之后、你指定的那个打印时刻」执行。**

这带来一个关键能力：

```
G1 X50 F600        ; 走到一半
SET_PIN PIN=valve VALUE=1    ; 在这个精确时刻开阀
G1 X60 F600
```

**阀可以在移动中途开。** 不需要停车，不需要 `M400`。

这是 Marlin 做不到、自研板待测、而 Klipper 从架构上就保证的。

### 对比表

| | Marlin | Klipper | 自研 LCSystem |
|---|---|---|---|
| IO 随运动排队 | ❌ | ✅ **架构保证** | ⏳ 待测 |
| 移动中开阀 | ❌ | ✅ | ⏳ |
| 需要 `M400` | ✅ 必须 | ❌ 不需要 | ⏳ |
| 前瞻规划 | ✅ | ✅ 优秀 | ⏳ 可能没有 |
| 配置方式 | 重编固件 | **改 cfg 文件 + 重启服务** | 改宏 + 重刷 |

**最后一行也很重要**：Klipper 改配置不用重编译、不用刷机，改 `printer.cfg` 然后 `FIRMWARE_RESTART` 就行（几秒）。这意味着**用户可以自己调参数**。

---

## 1. 硬件路线

### 1.1 三条子路线

| 路线 | 说明 | 适用 |
|---|---|---|
| **A. 树莓派/迷你主机 + 任意 Klipper 主板** | 最标准。主机跑 Klipper，板子只做步进+IO | ⭐⭐⭐⭐⭐ |
| **B. 一体化板（Manta/BTT Octopus + CB1/CM4）** | 主机和 MCU 在一块板上，省空间 | ⭐⭐⭐⭐ |
| **C. 把 Ultimaker 主板刷 Klipper** | AVR 支持有限，性能受限 | ⭐⭐ 不推荐 |

**推荐 A 或 B。** 关键要求：**主板上要有空闲的、可被 `[output_pin]` 使用的 GPIO**。

### 1.2 板子选择要点

- **必须有至少 2 路空闲输出**（主阀 + 排气阀）
- 优先选带 **MOSFET 输出**的板子（如 Octopus 的 `FAN`/`HE` 口），可以直接驱动电磁阀，不用外接继电器板
- 如果只有逻辑电平输出，必须外接 MOS 板 + 续流二极管
- **不要用风扇口如果它带 PWM 调速**——`[output_pin]` 用 `pwm: false` 时是纯开关，但硬件上某些风扇口有 RC 滤波，边沿会变慢

### 1.3 气路

```
空压机 → 调压阀（手动） → 三通 → 主阀 → 料筒
                              ↓
                            排气阀 → 大气
```

或者用 **3/2 阀**（一个阀搞定加压+排气，断电自动泄压），如 Burkert 6012（[Unfold Claystruder](https://wiki.reprap.org/wiki/Unfold_Clay_Results) 用的就是它）。**3/2 阀只需一路 IO，比双阀省一半控制复杂度**，强烈推荐。

---

## 2. 配置

### 2.1 `printer.cfg`

```ini
# ── 阀 ──────────────────────────────
[output_pin valve_main]
pin: PA1
pwm: false
value: 0
shutdown_value: 0        # 关机时强制关闭 —— 安全关键
scale: 1

[output_pin valve_purge]
pin: PA2
pwm: false
value: 0
shutdown_value: 0
scale: 1

# ── 宏：笔画开始 ────────────────────
[gcode_macro STROKE_START]
description: 开阀并驻留
gcode:
  SET_PIN PIN=valve_main VALUE=1
  G4 P{PARAMS.RISE|default(120)|int}

# ── 宏：笔画结束 ────────────────────
[gcode_macro STROKE_END]
description: 关阀并驻留（可带排气）
gcode:
  SET_PIN PIN=valve_main VALUE=0
  {% if PURGE|default(1)|int %}
    SET_PIN PIN=valve_purge VALUE=1
    G4 P{PARAMS.PURGE_MS|default(40)|int}
    SET_PIN PIN=valve_purge VALUE=0
  {% endif %}
  G4 P{TAIL|default(80)|int}
```

> ⚠️ **`shutdown_value: 0` 必须写。** Klipper 关机/报错时会强制把引脚拉到这个值。对阀来说，0 = 关 = 安全。漏写可能导致断电瞬间阀保持开启、料流一地。

### 2.2 方言实现

```ts
const KlipperDialect: Dialect = {
  name: 'klipper',

  caps: {
    queuedIO: true,          // ✅ 关键：SET_PIN 随运动排队
    arcs: true,              // Klipper 支持 G2/G3，但我们统一不用
    multiCmdPerLine: false,
    maxLineLen: 0,           // 无硬限制，实际控制在 200 以内
    dwellUnit: 'ms',
  },
  ...
}
```

### 2.3 指令映射

| 抽象 | Klipper 指令 | 备注 |
|---|---|---|
| 开/关阀 | `SET_PIN PIN=valve_main VALUE=<0\|1>` | **排队执行** |
| 驻留 | `G4 P<ms>` | 毫秒 |
| 等运动停止 | `M400` | **不需要**（排队就没这问题） |
| 空驶 | `G0 X.. Y.. F..` | |
| 打印段 | `G1 X.. Y.. F..` | |
| 抬针 | `G0 Z.. F..` | |
| 回零 | `G28` | |

### 2.4 生成的 G-code

```
; ── stroke s1 (contour) ──────────
SET_PIN PIN=valve_main VALUE=0     ; ① 确保阀关（幂等）
G0 X10.000 Y10.000 F6000           ; ② 空驶到起点
G0 Z0.400 F3000                    ; ③ 降到位
SET_PIN PIN=valve_main VALUE=1     ; ④ 开阀 ← 排队，跟着上一条 Z 走完执行
G4 P120                            ; ⑤ 上升延时
G1 X50.000 Y10.000 F600            ; ⑥ 打印
G1 X50.000 Y40.000 F600
G1 X10.000 Y40.000 F600
G1 X10.000 Y10.000 F600
SET_PIN PIN=valve_main VALUE=0     ; ⑦ 关阀 ← 精确落在最后一点
G4 P80                             ; ⑧ 拖尾
G0 Z0.700 F3000                    ; ⑨ 抬针
```

**对比 Marlin 版本**：没有 `M400`，没有「终点前 Δ 处切一刀」。因为 `SET_PIN` 会精确落在队列位置，**不需要停车换正确性**。

### ⚠️ 但有一个真实存在的坑

社区报告：裸 `SET_PIN` 会在 lookahead 中引入**可见的停顿**（r/klippers 上有 "SET_PIN command is slow" 的讨论）。

原因：`GCodeRequestQueue` 的处理会打断 lookahead 的连续性——它需要在队列里插入一个同步点。

**实测建议**：先做 20 条短笔画，看时间轴渲染里速度曲线有没有在每个 `SET_PIN` 处掉到 0。如果掉了，说明这个版本的 Klipper 会停。

**缓解手段**（按优先级）：
1. 用 **`G4` 和 `SET_PIN` 合并**的方式减少同步点数量（一条笔画只有 2 个，已经很少了）
2. 如果只是「减速到很低但不为 0」，影响可接受（速度变化 < 20% 时线宽变化肉眼不可见）
3. 极端情况下回到「切一刀」策略（跟 Marlin 一样），但那时你已经不需要 Klipper 了

---

## 3. 与 Marlin 的差异（除了上面那些）

### 3.1 配置即代码，可热改

用户改 `printer.cfg` 里的 `value: 0` 或改宏里的默认延时，`FIRMWARE_RESTART` 几秒生效。**不用你发版**。

这意味着：**把标定参数放进 cfg 或独立的 `variables.cfg`，让用户自己调。**

```ini
# 建议用 save_variables 存标定表
[save_variables]
filename: ~/printer_data/config/variables.cfg
```

### 3.2 通讯：Moonraker HTTP API

Marlin 是串口逐行 `ok`，Klipper 是 **Moonraker 的 HTTP/WebSocket API**。

```
POST http://<host>:7125/printer/gcode/script?script=G1%20X10%20F600
```

**好处**：
- 有 `queued` 状态查询（`/printer/objects/query?motion_report`）
- 有 WebSocket 推送，实时拿位置/速度/队列深度
- 上传文件、开始打印、暂停恢复都有现成接口

**坏处**：多一层 HTTP 开销。但对你「一发一等」的策略无影响。

**下发器实现**：
- 短任务（< 1000 行）：逐行 POST
- 长任务：先 `POST /server/files/upload` 上传 `.gcode`，再 `POST /printer/print/start?filename=...`

**后者更快也更稳**——让 Klipper 自己读文件，不用你流式喂。

### 3.3 前端可以白嫖

Klipper 生态有 **Mainsail / Fluidd** 两个成熟 Web 前端。它们能：

- 实时显示位置、速度、温度、队列
- 显示 G-code 预览（带层滑块）
- 手动发指令
- 文件管理

**你不需要自己写控制面板。** 你的上位机只需要负责「画图 → 生成 G-code → 上传」，然后可以嵌一个 Mainsail 的 iframe 或者跳转。

这能省掉大量工作。DIW-UM 那台机器就是这么做的（手册第 22 页：浏览器输 IP 进 Fluidd）。

---

## 4. 时序可信度评估

| 项 | Klipper 表现 | 影响 |
|---|---|---|
| IO 随运动排队 | ✅ 架构保证 | 不需要 `M400` |
| 排队精度 | 微秒级（MCU 时钟） | 远好于需要 |
| 移动中开阀 | ✅ | 可做 pre-flow 而不停车 |
| 前瞻规划 | ✅ 优秀（`square_corner_velocity` 可调） | 短线段保持速度 |
| `SET_PIN` 引入停顿 | ⚠️ 社区报告存在 | **需实测** |
| 单条运动时长上限 | 无 | 比自研板宽松 |
| 行长度 | 无硬限 | 但仍建议 < 200 |

---

## 5. 限制清单

1. **`SET_PIN` 可能有停顿** —— 需实测，见 §2.4 末尾。
2. **需要 Linux 主机** —— 树莓派/迷你主机，不能只靠 MCU。
3. **不能像 Marlin 那样「单板独立」** —— 掉主机 = 停机。但 Klipper 有 `[virtual_sdcard]` 和断电续打，实际影响不大。
4. **MCU 固件需按板子编译** —— 首次配置要 `make menuconfig` + 刷写。一次性成本。
5. **`SET_PIN` 有最小间隔** —— 如果一条笔画极短（< 50ms），两次 `SET_PIN` 可能太密。加 `minSegmentLen` 校验。
6. **多 MCU 配置时 IO 在哪个 MCU 上** —— 如果阀接在 CAN 扩展板上，需要确认排队语义仍然成立（RRF 有这个限制，Klipper 应该没有，但要测）。

---

## 6. 与自研板（LCSystem）的关系

**Klipper 是自研板应该抄的架构。**

具体说，如果你能影响朋友的固件开发，建议他照 Klipper 的 `output_pin.py` 做：

```c
// 伪代码
void cmd_M121(int port, int value) {
    // 不要：立即写寄存器
    // 要：   把写操作挂到运动队列的某个时刻
    enqueue_motion_callback(write_gpio, port, value);
}
```

**成本很低**（几十行），**收益是整条流水线的时序精度**。

如果固件不改，你的后处理器就要为 LCSystem 也加上 `M400` 兜底——那就退化成 Marlin 方案的水平了。

---

## 7. 实施步骤

| 步 | 做什么 | 需要硬件 |
|---|---|---|
| 1 | 选板子（确认 ≥2 路空闲输出） | 否 |
| 2 | 写 `KlipperDialect`（约 70 行） | 否 |
| 3 | 用 mock 板验证 G-code 结构 | 否 |
| 4 | 装 Klipper + Mainsail，跑通空跑 | 是 |
| 5 | **测 `SET_PIN` 停顿**：20 条短笔画，看速度曲线 | 是 |
| 6 | 接阀，测电平/极性/断电行为 | 是 |
| 7 | 标定（00 文档 §5 三组实验） | 是 |

---

## 8. 一句话总结

**这是三条路里唯一不需要妥协的。**

- 不需要 `M400`（IO 排队）
- 不需要重编固件（改 cfg）
- 不需要写控制面板（Mainsurf/Fluidd 白嫖）
- 不需要停车（可以移动中开阀）

唯一要实测的是 `SET_PIN` 的停顿问题。如果那个问题严重到必须停车，那 Klipper 相对 Marlin 的优势就只剩「配置好改 + 前端白嫖」了。

---

## 9. 补充：这条路对 DIW 的一个额外好处

Klipper 的 `[output_pin]` 支持 **`pwm: true` + `cycle_time`**。

如果你将来想用**比例压力阀**（0–10V 或 4–20mA 控制）而不是开关阀，可以把 `output_pin` 配成 PWM 输出，再外接一个电压转换板，就能实现**连续流量控制**。

那时候你的笔画模型可以扩展成：

```
stroke = { points[], flow: [0.3, 0.5, 0.5, 0.3] }   // 每段流量
```

**这打开了「变线宽打印」的门**——填充时可以按需要调粗细，拐角可以减速减流量。

不过这是 v2 的事。v1 先用开关阀把闭环跑通。
