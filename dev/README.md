# dev/ — 开发期脚本

这些脚本用于验证功能，**不参与打包**，也不影响运行。

## 用法

脚本里用的是 `from app.xxx import ...`，所以要**从项目根目录**运行：

```bash
# 在 work/ 下执行
python dev/_infill_test.py
python dev/_serial_test.py
```

如果报 `ModuleNotFoundError: No module named 'app'`，加一下路径：

```bash
PYTHONPATH=. python dev/_infill_test.py
```

## 文件说明

### 单元测试（不需要硬件）

| 文件 | 内容 |
|---|---|
| `_infill_test.py` | 格栅/蜂窝填充 18 项（重点：非对称形状、偏心圆） |
| `_fillscan_test.py` | 扫描线算法 30 项（移植自激光切割 CIMFillAlgorithm） |
| `_serial_test.py` | F1 串口协议 33 项（CRC、帧格式、应答解析、错误码） |

### 硬件联调（需要板子）

| 文件 | 目标 | 需要 |
|---|---|---|
| `_f1_probe.py` | F1 板连通性探测 | COM7 |
| `_f1_full_test.py` | F1 板完整功能 + 速度/圆弧测试 | COM7 |
| `_f1_arc_test.py` | F1 圆弧 vs 直线离散耗时 | COM7 |
| `_f1_arc_verify.py` | F1 圆弧复核 | COM7 |
| `_f1_shots.py` | 生成 F1 测试报告配图 | COM7 |
| `_nic_full_test.py` | 网口板完整测试（IO/运动/延时/G4） | 192.168.0.102 |
| `_e2e_f1.py` | F1 端到端：图形→G-code→板子 | COM7 |
| `_e2e_nic.py` | 网口板端到端 | 192.168.0.102 |
| `_e2e_nic_full.py` | 网口板端到端（格栅块 + 延时） | 192.168.0.102 |
| `_batch_perf.py` | 批量发送性能对比 | 192.168.0.102 |

### UI 端到端（需要服务在跑）

| 文件 | 内容 |
|---|---|
| `_e2e_ui.py` | Playwright 驱动浏览器：画图→填充→编译→连接→发送 |

## 注意事项

- **网口板**：先确认本机以太网卡配了 `192.168.0.100/24`，且流量没走 WLAN
- **F1 板**：空载时**不要发 `G28`**，会跑到保护上限把坐标搞成 -6000，只能断电重启
- **网口板**：**不要发 `G4`**，会让队列卡死且不自恢复
- 串口是独占资源，上一个进程没释放时打不开（报 PermissionError 13）
