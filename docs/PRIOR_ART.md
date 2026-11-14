# PRIOR_ART · 先例调研结论

> 调研时间：2026-11。目标：找出「每条路径两端各控制一个 IO + 一个延时」这个需求，别人做过没有。
>
> **结论：没有现成方案。这是真缺口，不是我们没找到。**

---

## 0. 一句话结论

开源膏体/DIW 圈里，**没有任何人有「开阀 → 驻留 → 挤出笔画 → 驻留 → 关阀」这个一等公民的 G-code 原语**。

全场的做法只有三种：

1. **把推杆做成 E 轴** —— Jubilee、Replistruder、Printess、Cerambot、Enderstruder、Allstruder 全部如此。Cerambot 的教程直接教你把耗材直径填成注射器内径来糊弄切片器。
2. **把电磁阀接到风扇/加热器引脚，再用后处理脚本改 G-code** —— RUG/Penn State 是典型。
3. **不用 G-code 行顺序，改由 Python 按时间同步阀** —— T-Code 方案（Nature Comms 2025）。

**没有任何一个项目把「每条笔画独立的 pre-flow / post-flow」做成参数。** 全是全局常量。

---

## 1. 那个最接近的先例：笔式绘图仪

这个需求在别的行业里已经成熟二十年了，那个行业叫 **pen plotter**。

落笔 → 延时 → 走 → 抬笔 → 延时。**结构完全同构。**

### vpype-gcode（MIT，最值得抄）

https://github.com/plottertools/vpype-gcode

配置就是 TOML，槽位天然按「路径边界」组织：

```toml
[gwrite.my_own_plotter]
unit = "mm"
document_start = "M3 G21\n"
layer_start = "(Start Layer)\n"
line_start = """G00 Z5
G00 X{x:.4f} Y{y:.4f}
M3 S1000
G4 P0.3
G01 Z1 F3500
"""
segment = """G01 X{x:.4f} Y{y:.4f} Z1\n"""
line_end = """G00 Z 5.0000
M5 S0
G4 P0.5\n"""
document_end = """M5
G00 X0.0000 Y0.0000
M2"""
invert_y = true
```

**注意：官方自带的示例 profile 里就已经包含我们要的模式了** —— `line_start` 里 `M3 S1000`（开）→ `G4 P0.3`（延时），`line_end` 里 `M5 S0`（关）→ `G4 P0.5`（延时）。

**这就是「每路径 on+dwell / off+dwell」，声明式实现。** 6 个槽位覆盖 100% 需求：

| 槽位 | 我们的语义 |
|---|---|
| `document_start` / `document_end` | 文件头尾（回零、抬针、关阀） |
| `layer_start` / `layer_end` | 层开始/结束（Z 定位） |
| `line_start` | **开阀 + 上升延时** |
| `segment` | 中间点 |
| `line_end` | **关阀 + 拖尾延时** |

模板变量：`{x} {y} {dx} {dy} {index} {layer_index} {layer_id}`，用 Python `.format()`。
**限制**：没有时间变量、没有条件语句。延时要写成字面量（对我们够用）。

### AxiDraw（GPL-2.0，只作参考）

https://github.com/evil-mad/axidraw

参数命名是二十年打磨的，直接抄：

| 参数 | 默认 | 单位 | 含义 |
|---|---|---|---|
| `pen_pos_up` / `pen_pos_down` | 60 / 30 | 0–100 | 抬笔/落笔位置 |
| `pen_rate_raise` / `pen_rate_lower` | 75 / 50 | 1–100 | 抬落速度 |
| **`pen_delay_up`** | 0 | **ms** | **抬笔后延时** |
| **`pen_delay_down`** | 0 | **ms** | **落笔后延时** |
| `speed_pendown` / `speed_penup` | 25 / 75 | 1–100 | 速度 |
| `const_speed` | False | bool | 落笔时恒速 |

**关键洞察**：AxiDraw 把「位置」「速度」「延时」三者分开。**这是气压阀需要的三样东西的成熟版本** —— 开度（如果可调）、开关速度、驻留时间。

`saxi`（AGPL，别抄）用的是秒（`penDropDuration: 0.12`），**注意单位不一致**。

### sameer/svg2gcode（MIT，Rust）

https://github.com/sameer/svg2gcode

```
--on <TOOL_ON_SEQUENCE>      --off <TOOL_OFF_SEQUENCE>
--begin <...>                --end <...>
--circular-interpolation false    ← 关掉 G2/G3
--tolerance / --dpi / --origin / --dimensions / --feedrate
```

命令行词汇和我们的方言约束对得上。**不支持填充**（FAQ 明说），**没有 dwell 槽位**（塞进 `--on` 里）。

---

## 2. CAM 侧：都支持 start/end G-code，但粒度不对

| 工具 | 许可证 | 粒度 | 能否用于我们 |
|---|---|---|---|
| **LaserWeb4** | **AGPL-3.0** | ✅ **每路径 tool on/off** | ❌ 许可证病毒，**代码一行不能抄** |
| **Kiri:Moto** | MIT | ✅ 每路径自动 on/off + `gcodeDwell` 宏 | ⚠️ 可读可借，UI 耦合重 |
| **FullControl** | GPL-3.0 | `ManualGcode` 可插任意文本 | ❌ 许可证；且是 Python DSL 无编辑器 |
| **FreeCAD Path** | LGPL-2.1 | ❌ `pre_operation`/`post_operation`，**每操作不是每路径** | ⚠️ 架构可学，钩子不对 |
| **LightBurn** | 商业 | ❌ 只有作业级 | 参考其模板语法 |
| dxf2gcode / pycam / HeeksCAM | — | — | ❌ 全死（2015/2021/2018） |

### ⚠️ 更正：LaserWeb4 和 Kiri:Moto 其实是按路径的

早期只查文档会得出「整文件级」的错判。看源码才发现：

**LaserWeb4** `cam-gcode-laser-cut.js` 的 `getLaserCutGcode`：
```
moveRapid(first point) → toolOn(gcodeToolOn) → 所有 G1 → toolOff(gcodeToolOff)
```
**确实按路径发 on/off。** 但 `gcodeToolOn`/`gcodeToolOff` 只是两个自由文本框（`rows: 5`），**没有专用 dwell 字段**，要自己敲 `M42 P4 S1\nG4 P300`。

**Kiri:Moto** `export.js` 的 `moveTo()`：
```js
if (!lasering && laser) filterEmit(laserOp.on, consts)
if (lasering && !laser) filterEmit(laserOp.off, consts)
```
**也是按路径自动发。** 而且 dwell 有专用宏：`cmdDwell = device.gcodeDwell || ["G4 P{time}"]`。

**教训：判断「粒度」必须看源码，不能只看文档。**

### Kiri:Moto 的 device JSON 值得单独说

它是**纯数据驱动的后处理器**：

```json
{ "file-ext":"gcode", "token-space":" ",
  "pre":["G21","G90"], "post":["M30"],
  "laser-on":["M121 U0 V1","G4 P{time}"],
  "laser-off":["M121 U0 V0","G4 P{time}"],
  "gcodeDwell":["G4 P{time}"] }
```

还有 `;; IF {expr}` / `;; ELIF` / `;; ELSE` / `;; END` 条件语法 + `{js-expression}` 求值。

**这意味着你可以把「输出方言」做成一个 JSON 文件**，用户自己就能加 DIW-UM（Klipper 的 `SET_PIN`）或 GRBL（`M3`/`M5`）支持，不用你发版。

---

## 3. 关键技巧：`G0→G1` 状态机

**FreeCAD 的 JTech 激光后处理器**用几行代码实现了路径级延时：

```python
PRE_FEED  = """M03\nG4 P{}\n"""   # G0 切 G1 → 路径开始
POST_FEED = """M05\n"""           # G1 切 G0 → 路径结束
POWER_ON_DELAY = args.power_on_delay / 1000   # 毫秒
```

LaserWeb4 的 `postProcessRaster()` 用同一个技巧（正则包住 G0↔G1 转换）。

**为什么值得学**：它不依赖几何信息，只看 G-code 流里的模态切换。**即使用户手改过 G-code 依然正确**。可以作为「笔画对象展开」之外的第二重保险。

---

## 4. 时序问题：这是最该重视的发现

### Marlin issue #26803

https://github.com/MarlinFirmware/Marlin/issues/26803

标题：`[BUG] M400 and G4 do not sync properly or repeatable between G[0|1|2|3] and M42 S* commands`

**closed as not planned。**

原因：`M42` 由 **G-code 解析器**处理，**不进规划器队列**。一个长移动期间发出的引脚翻转，要等移动**结束之后**才生效。

报告者实测：50–100ms 驻留「有时候行有时候不行」，**要 1000ms 才可靠**。

**这正是我们的失败模式。** 所以 Marlin 路线必须插 `M400`（见 01 文档）。

### RepRapFirmware 做对了

| 指令 | 语义 |
|---|---|
| `M950 P<n> C"io4.out"` | 创建 GpOut 端口 |
| `M42 P<n> S0/1` | 驱动 |
| **`M571 P<n> S1`** | **"Set output on extrude"** —— 正挤出时为高，不挤出为低。**文档：切换延迟约 2ms** |
| **`M670 T<ms> P<port>`** | **端口切换提前量（毫秒）** —— 阀在运动开始**之前**打开 |
| `M260.3` | 直接说 Nordson Ultimus V 点胶机协议 |

**这两个正是我们协议缺的**（自研 LCSystem 只有 `M121`，没有挤出指示、没有提前量）。

### Klipper 做对了（架构上）

`klippy/extras/output_pin.py`：`SET_PIN` 走 `GCodeRequestQueue`，注册 `toolhead.register_lookahead_callback` 和 `motion_queuing.register_flush_callback`。

**写入被调度到移动队列内部的某个打印时刻，不是立即执行。** 这就是正确架构。

---

## 5. 反方论证：T-Code（但要看清它反对的是什么）

**Propst & Mueller, "Time Code for multifunctional 3D printhead controls", Nature Communications 16:1035 (2025)**
doi:10.1038/s41467-025-56140-1
代码：https://github.com/JHU-Mueller-Lab/Time-Code-for-Multifunctional-3D-Printhead-Controls

**论点**：G-code 逐行执行，每插一条辅助指令运动就要减速到零 → **过度挤出鼓包**。Fig 1d 显示低加速度下更严重。

**解法**：把辅助指令从 G-code 剥离，合并成连续路径，Python 算速度曲线，**按时间基**触发辅助事件（起始 ping + 方向变化处的重同步 ping）。

**对我们的意义（两条）**：

1. **不必恐慌。** 他们处理的是「路径中间要高频率调制阀」。而 DIW 笔画**本来就在方向变化处停车**，所以「插入停车」对我们是零成本。他们自己也说：per-stroke valve events are fine *because* DIW strokes already stop。

2. **但有个重要推论**：他们测出的缺陷机制是「**停 → 过度挤出**」。所以：

   > **驻留必须放在关阀之后，不能放在之前。**
   >
   > 先停再关，那点余量留在原地鼓包；先关再停，余量在关阀后泄掉。

这跟我们的时序骨架一致，但现在有论文背书。

---

## 6. 硬件层面最有价值的发现：排气阀

**Frostruder MK2**（气动膏体挤出鼻祖）用**两个 2/2 电磁阀**：一个加压，**一个排气**。

**RJ Paste Extruder** 的 wiki 原话：

> 排气阀 "prevents any paste oozing from the nozzle when extrusion needs to stop, **a difficulty that plagues a lot of motor driven systems**"

**Claystruder** 用 **3/2 阀**（Burkert 6012）：通电加压，**断电自动排气**。只需一路 IO。

### 结论：防拉丝靠泄压，不靠等

你原设想的「关阀 → `G4` 拖尾」是在**等**余压自然衰减。但 Claystruder 的 wiki 记录了另一个数字：降低压力 "can take **a few minutes**" 才稳定。

**加一路排气阀**：关主阀同时开排气阀 → 管路压力瞬间泄掉 → 拖尾从「几百毫秒自然衰减」变成「几十毫秒强制泄放」。

Duet 论坛的真实案例（[topic 39544](https://forum.duet3d.com/topic/39544)）：一家膏体打印机公司用**两路气阀，一路挤出时开、一路回抽时开、空闲时全关**，还提到 "there is some leakage in the hydraulic circuit if the retraction valve is not activated"。

**→ 把 IO 需求从「1 路」改成「至少 2 路」：主挤出阀 + 排气/回抽阀。**

---

## 7. 最短验证路径（两行 hack）

**RUG/Penn State 膏体挤出机**：https://reprap.org/wiki/RUG/Pennsylvania/State_College/Paste_extruder

> 三个 12V 电磁阀接到 RAMPS/RAMBo 的**风扇口**，后处理脚本 "turns the extruding commands into fan commands"（M106/M107），**不需要重刷固件**。

这是「没有 E 轴时怎么最快验证」的最短路径。我们的 `M121` 比 `M106` 正规，但**思路（改写挤出指令 → 输出引脚指令）就是后处理器的本质**。

---

## 8. 学术界的空白（也是机会）

**没有找到任何一篇论文**，把「阀开启/关闭时序」和「线起点/终点质量」做受控实验并公开代码。

相邻的已验证结果只有：
- T-Code 的「停 → 过度挤出」机制
- DIW 工艺参数 DOE 论文（如 β-TCP 支架，*Ceramics International* 2026，Box–Behnken 扫压力/针径/速度/固含量）——但都**把压力当常量**，不是开关量

**→ 这是一个可以自己补的缺口，成本极低：** 一组 dwell ladder（每档打 5 条 20mm 直线），一下午出数据。见 00 文档 §5。

---

## 9. 可用工具的许可证清单

| 工具 | 许可证 | 能直接用代码吗 |
|---|---|---|
| vpype / vpype-gcode | **MIT** | ✅ |
| sameer/svg2gcode | **MIT** | ✅ |
| Kiri:Moto | **MIT** | ✅ |
| gcode2image | **MIT** | ✅ |
| FreeCAD | LGPL-2.1 | ⚠️ 独立进程/插件边界安全，别内嵌 |
| FullControl | GPL-3.0 | ❌ |
| gscrib | GPL-3.0 | ❌ |
| AxiDraw | GPL-2.0 | ❌ |
| **LaserWeb4** | **AGPL-3.0** | ❌ **网络服务也传染** |
| saxi | AGPL-3.0 | ❌ |

**红线：LaserWeb4 的设计可以看、可以学，一行代码都不能抄。**

---

## 10. 最终选型

| 层 | 用什么 | 理由 |
|---|---|---|
| 输出 schema | **vpype-gcode 的 profile 键名** | 6 槽位覆盖全部需求，MIT |
| 路径级触发（兜底） | **`G0→G1` 状态机** | 不依赖几何，手改 G-code 依然正确 |
| 参数命名 | AxiDraw 的 `pen_delay_down/up`（ms） | 二十年打磨 |
| 圆弧处理 | 几何阶段就离散，永不输出 G2/G3 | `SPLIT_ARCS` 证明可行 |
| 方言配置 | **Kiri:Moto 风格的 device JSON** | 用户自己加方言，不用发版 |
| 几何验证 | gcode2image | MIT，`--showG0` 看空驶 |
| 几何库 | Shapely（offset/布尔/扫描线填充） | 成熟 |
| 时序模型 | Klipper 的 `output_pin.py` 架构 | 排队执行，正确 |

---

## 11. 未验证项（诚实标注）

- WASP 当前固件（只有第三方数据库说法 + 旧的 Marlin fork）
- 3D Potter 固件（从「Duet」型号名推断，未确认）
- Enderstruder 的确切 HardwareX DOI
- Kiri:Moto 是否有「禁用 G2/G3」的开关（看到弧输出代码，没找到关闭选项）
- Kiri:Moto 的 `Engine` API 能否 headless 吞 SVG/DXF（文档只写 STL）
- FreeCAD 的 `.fcm` 机器配置文件在树里的位置
- gscrib 是否有 `G4`/dwell 动词（README 未展示）
- **Marlin 的 `LASER_FEATURE` 的 `S` 值是否走规划器**（如果是，那是 Marlin 路线的最优解，见 01 文档 §3 解法二）
