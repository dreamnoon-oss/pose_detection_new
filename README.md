# Pose Detection — 端头门司机行为分析

基于 YOLO 姿态估计的列车司机标准动作实时识别与合规判断系统。

## 架构

```
run_xxx.py → VideoPlayer → ParallelDetector → detection.py → geometry.py
                ↕                ↕
          annotation.py    SequenceAnalyzer (事后分析)
          visualization.py
```

**核心思路**：所有动作规则**并行独立检测**，各自记录触发时间戳。视频播放结束后，按事件发生顺序映射到预期动作序列，判定合规性。

## 项目结构

```
pose_detection/
├── README.md
├── pyproject.toml
├── requirements.txt
├── src/
│   ├── config.py          # 全局配置（关键点、骨架、阈值）
│   ├── geometry.py        # 几何计算（角度、线段相交）
│   ├── detection.py       # 4 种检测算法（平行线/穿区域/指向/组合）
│   ├── detector.py        # 并行检测器（多规则同时运行）
│   ├── analyzer.py        # 时序分析器（事件→动作映射+顺序判定）
│   ├── visualization.py   # 可视化（骨架、面板、调试射线，纯 OpenCV 英文渲染）
│   ├── annotation.py      # 标注工具（区域框选、参考线、背景、门线保存）
│   ├── reporter.py        # CSV 检测报告生成
│   ├── timefmt.py         # 时间格式化（秒 → HH:MM:SS）
│   ├── train_detector.py  # 列车进出站检测（背景帧差法）
│   ├── person_filter.py   # 门线选人纯函数（bbox 底边+髋部锚点）
│   └── player.py          # 交互式视频播放器（含门线）
├── scripts/
│   ├── run_shangtichang.py
│   ├── run_baoshan.py
│   ├── run_jingansi.py
│   ├── run_tangqiao.py
│   ├── run_pudongdadao.py
│   ├── run_linping.py
│   ├── run_longhuazhong.py
│   └── video_anonymize.py   # 视频脱敏工具
├── data/                   # 标注数据 (JSON + 背景图)
├── models/                 # 模型文件
├── output/
│   ├── video/              # 检测视频
│   └── report/             # CSV 检测报告
└── docs/                   # 详细文档
```

## 检测策略

| 类型 | 函数 | 说明 |
|------|------|------|
| `parallel_line` | `check_arm_parallel_to_line()` | 肩→腕向量与参考线夹角 < 阈值。支持动态角度补偿、反向平行（anti_parallel）、肘部回退、躯干夹角下限 |
| `pass_region` | `check_arm_passes_region()` | 肩→腕射线（可延长）穿过/落在矩形区域内 |
| `pointing` | `check_pointing()` | 手臂方向与区域角点夹角 < 阈值 |
| `pointing_with_line` | `check_pointing_with_line()` | 手臂平行于线 且 朝向区域 |

### 关键参数

| 参数 | 值 | 说明 |
|------|----|------|
| 关键点 | 5-12 | 肩/肘/腕/髋 |
| 平行角度阈值 | 40° | 手臂与参考线最大夹角 |
| 躯干夹角下限 | 45° | 手臂 vs 肩→髋夹角需 > 45°（防止未抬臂误触发） |
| 延长倍数 | 6× | pass_region 时腕部沿手臂方向延长倍数 |
| 持续帧数 | 20 | 连续命中帧数确认事件 |
| 帧衰减 | -2/帧 | 容忍短暂丢帧 |
| 冷却期 | 90 帧 | 事件触发后同规则暂停检测 |
| 最小手臂长度 | 30px | 过滤无效检测 |
| 动态角度系数 | 0.6× | 肘部弯曲补偿系数，通过 `DETECTION_KWARGS["dynamic_angle_coeff"]` 按站点调整。实际阈值 = 40° + 弯曲角 × 系数 |

## 已配置站点

| 站点 | 脚本 | 检测类型 | 动作数 | 动作 |
|------|------|------|--------|------|
| 上体场 | `run_shangtichang.py` | PAR + CROSS | 4 | Call / CloseDoor / CheckGap / CheckLight |
| 宝山 | `run_baoshan.py` | P+L + POINT | 5 | PointFwd / CheckR2 / PointFwd / CheckR3 / CheckR4 |
| 静安寺 | `run_jingansi.py` | PAR + CROSS | 5 | Call / CloseDoor / CheckGap / CheckLight / CheckSwitch |
| 塘桥 | `run_tangqiao.py` | PAR + CROSS | 4 | Call / CloseDoor / CheckGap / CheckLight |
| 浦东大道 | `run_pudongdadao.py` | PAR + CROSS | 5 | Call / CloseDoor / CheckGap / CheckLight / CheckSwitch |
| 临平 | `run_linping.py` | PAR + CROSS | 4 | Call / CloseDoor / CheckGap / CheckLight |
| 龙华中 | `run_longhuazhong.py` | PAR + CROSS | 5 | Call / CloseDoor / CheckGap / CheckLight / CheckSwitch |

所有站点均已配置列车进出站检测（背景帧差法，track ROI + 背景图 + MAD 阈值 20）。

## 动作序列（以上体场为例）

| 动作 | 规则 | 检测类型 | 目标 |
|------|------|------|------|
| 动作1 | rule_A (第1次) | parallel_line | line_1 |
| 动作2 | rule_B (第1次) | parallel_line | line_2（肘部回退+躯干夹角） |
| 动作3 | rule_A (第2次) | parallel_line | line_1 |
| 动作4 | rule_C (第1次) | pass_region | region_1（延长射线） |

### 反向平行检测（anti_parallel）

部分站点需要司机**背对**参考线做出确认动作（如道岔确认）。此时手臂方向与参考线接近 180° 而非 0°。

通过规则配置 `"anti_parallel": True`，判定条件从 `ang < threshold` 翻转为 `ang >= 180° - threshold`（即手臂与参考线反向夹角在阈值内）。同样支持动态角度补偿。

**浦东大道（5 动作，含道岔）：**

| 动作 | 规则 | 检测类型 | 目标 |
|------|------|------|------|
| 动作1 | rule_A (第1次) | parallel_line | line_1 |
| 动作2 | rule_B (第1次) | parallel_line | line_2（肘部回退+躯干夹角） |
| 动作3 | rule_A (第2次) | parallel_line | line_1 |
| 动作4 | rule_C (第1次) | pass_region | region_1（延长射线） |
| 动作5 | rule_D (第1次) | parallel_line (anti_parallel) | line_1（反向，140°~180°） |

### 同帧冲突仲裁

同一帧内多个角度类规则（`parallel_line`、`pointing`、`pointing_with_line`）同时达到触发阈值时，计算归一化置信度分数，只保留最可信的一个：

- **正向平行**：`score = angle / effective_threshold`（分数越小越可信）
- **反向平行**：`score = (180° - angle) / effective_threshold`
- 被淘汰的规则 hold counter 归零、**不进冷却**，可立即重新累积
- **`pass_region` 豁免**，不受仲裁影响，独立触发

## 实时指标面板

播放/暂停时左上角显示并行检测面板，下方额外显示每个动作的实时指标：

- **parallel_line 规则**：显示肩→腕（或肩→肘）与参考线的当前夹角
- **pointing 规则**：显示手臂方向与区域的最小夹角
- **pass_region 规则**：显示"穿过"或"未穿过"

暂停后面板自动切换到右上角，避免与"PAUSED"文字重叠。每条规则独立计算，不受检测阈值限制，始终可见。

### 置信度着色

关键点根据置信度分三档显示：
- **红色** < 0.3 — 低置信度
- **黄色** 0.3 ~ 0.6 — 中等置信度
- **绿色** > 0.6 — 高置信度

阈值可通过 `conf_low_threshold` / `conf_mid_threshold` 按站点调整，右下角显示颜色图例。

### 可视化增强

- **手臂线段**：肩→肘→腕以加粗青色（左臂）/ 洋红色（右臂）绘制，置信度阈值降至 0.3
- **延长射线**：绿色 = 命中区域，红色 = 未命中
- 暂停和拖拽进度条时完整渲染所有面板

## 置信度指标

每个检测事件触发时自动计算三个质量指标：

| 指标 | 含义 | 范围 |
|------|------|------|
| **conf** | 持续期内肩/远端/肘三点关键点平均置信度 | 0~1，越高越可信 |
| **hit_rate** | 命中帧数 ÷ 持续期总帧数 | 0~1，越高越稳定 |
| **margin** | 有效阈值 − 实际夹角（仅 parallel_line） | 正值越大 = 角度越小 = 余量越充足 |

非 parallel_line 规则（如 pass_region）无 margin 值。

## 检测报告

视频播放结束后自动生成 CSV 报告到 `output/report/`，Excel 直接打开。

**输出命名规则**：输出视频和报告文件名自动跟随输入视频名——输入 `塘桥4.mp4` 则输出 `video/pose_output_塘桥4.mp4` 和 `report/report_塘桥4.csv`，不同视频不会互相覆盖。输出根目录由各站点脚本顶部的 `OUT_DIR` 配置。

**多趟车分块**：一个视频一份 CSV。视频中每趟列车停靠对应一个"第N趟列车"分块，各分块包含本趟的到站/离站/停靠时长、5 个标准动作结果和总体评估；基本信息中记录"列车趟数"。

**时间格式**：报告中的列车到站/离站/停靠时长及动作检测时间均为 `HH:MM:SS`（时:分:秒，整数秒），由 `src/timefmt.py` 统一格式化；控制台打印和视频画面仍为秒数显示。

报告内容：
- 基本信息（站点、脚本、日期、视频路径、列车趟数）
- 模型参数（模型、分辨率、关键点、置信度阈值）
- 每趟列车分块：
  - 到站/离站时间、停靠时长
  - 5 个标准动作检测结果（序号、动作名、检测状态、时间、conf/hit_rate/margin、合格判定）
  - 总体评估：检出数、顺序合规（顺序正确/顺序不正确）、**动作是否符合规范（是/否）**——全部动作检出且顺序正确才为"是"
- 指标说明

5 个标准动作：
1. 开门后手指呼唤
2. 手动关门
3. 关门后确认夹缝
4. 开车前确认站台指示灯
5. 开车前确认站台道岔（部分站点不需要）

## 列车进出站检测

基于背景帧差法，不需要额外模型。通过轨道 ROI 区域逐帧比较与空轨道背景图的像素差异，用迟滞阈值判断列车是否在场。

### 配置方法

1. 轨道空闲时暂停，按 `R` 框选铁轨区域（track ROI）
2. 确认轨道无车，按 `B` 将当前帧保存为背景参考图
3. 按 `S` 保存标注（JSON 自动记录 background + track_roi）

### 检测逻辑

| 参数 | 默认值 | 说明 |
|------|--------|------|
| high_threshold | 20 | ROI 内 MAD 均值高于此值 → 列车可能在场 |
| low_threshold | 15 | MAD 低于此值 → 轨道可能空闲 |
| confirm_frames | 20 | 连续确认帧数，防抖动 |

- MAD 持续高于 20（20帧）→ 判定"列车到站"，记录到站时间
- MAD 持续低于 15（20帧）→ 判定"列车离站"，记录离站时间
- 视频结束时输出：`列车到站: X.Xs` / `列车离站: Y.Ys` / `停靠时段: X.Xs ~ Y.Ys`

## 空闲跳跃扫描（所有站点已启用）

所有正式站点脚本均已启用以下参数：

- **`idle_fast_forward=True`**：列车不在场（AWAY）时跳过 YOLO 推理，仅做轻量 MAD 帧差检测
- **`idle_jump_seconds=5`**：跳跃扫描——空闲段每 5 秒只解码 1 帧算 MAD，其余帧直接跳过。采样帧写入输出视频并叠加 `>>> FAST-SCAN MAD=x.x @ 分:秒`，空闲段在输出里呈约 125 倍速快放
- **`auto_exit=True`**：视频播完自动完成分析、生成报告并退出，适合挂机批量跑

### 跳跃扫描工作流程

1. **跳跃扫描**：轨道空闲时 5 秒一跳，只对采样帧算 MAD
2. **回退确认**：采样帧 MAD > 20 → 打印"疑似列车进站"，回退 5 秒切换逐帧 MAD 检测，"连续 20 帧超阈值"的到站判定逻辑不变，**到站时间戳与不跳帧完全一致**；若为误报（MAD 连续低于阈值 2 秒）自动恢复跳跃扫描
3. **正常检测**：列车到站后恢复逐帧 YOLO 检测，输出视频正常速度完整写入
4. **离站结算**：确认离站后结算本趟动作事件、清空状态，回到跳跃扫描等待下一趟

所有到离站时间、动作事件时间戳均基于**真实视频帧号**计算，不受跳帧影响。结束时打印统计：`快进 X 帧 / 检测 Y 帧 / 共 Z 帧, 总耗时 Ns, 视频时长 Ms`。

### 多趟车支持

长视频中多趟列车进出站时，每次确认离站即结算本趟（分析 + 报告分块 + 清空动作状态），各趟互不影响；视频结束时未离站的最后一趟也会结算。

## 门线（司机侧人员过滤）— 全部站点启用

**问题**：司机站在隔离门内，乘客全身可见、离镜头近，检测框置信度常高于被门遮挡的司机，导致选人逐帧在司机/乘客间跳变、误触发。

**方案**：在门槛画一条"门线"，只保留**身体锚点在线内侧**的人员参与检测：

- **锚点** = bbox 底边中点（脚部，永远存在）+ 髋部中点（11/12 兜底）；任一在司机侧即算线内。手臂故意排除——司机会越线做确认动作
- **inside_side 旗标**：`signed_dist = cross(B−A, P−A)/|B−A| × inside_side > 0` 判定司机侧；画线时按当前帧司机脚锚点自动定，可 G 键翻转
- **死区容差**：`GATE_MARGIN=12px`，脚压线不抖动；线内无人时沿用上一帧选择（滞回），否则丢弃全部人员
- **kp/boxes 同索引**：选人逻辑一处计算，关键点与检测框用同一个 person 索引，避免画框和检测对不上

**启用方式**：门线能力已并入 `VideoPlayer`（`src/player.py` + `src/person_filter.py` 纯函数），**所有站点脚本零改动**。只要站点 JSON 有 `gate_line` 键即自动启用；无该键时保持原置信度选人。

JSON 顶层键：

```json
"gate_line": {"pts": [[x1,y1],[x2,y2]], "inside_side": 1}
```

**画线**（每站首个视频）：`python scripts/run_<站点>.py` → 暂停 → `G` 画/翻转门线（确认 `IN` 标记在司机侧）→ `S` 保存进该站 JSON → `X` 删除。重新运行该站脚本即启用门线过滤。

## 视频脱敏工具

独立脚本 `scripts/video_anonymize.py`，基于 YOLO pose 模型自动检测人脸并打码（支持**马赛克 / 高斯模糊**两种方式），同时支持手动框选固定区域打码。

**两种使用方式：**

方式一：直接修改脚本顶部 `VIDEO_PATH` / `OUTPUT_PATH`，然后运行：
```bash
python scripts/video_anonymize.py
```

方式二：命令行传参（会覆盖配置中的值）：
```bash
python scripts/video_anonymize.py -i video.mp4 -o output.mp4 --device cuda:0
```

**主要功能：**
- 人脸自动检测 + 打码（基于 pose 关键点：鼻/眼/耳 + 双肩兜底）
- 两种打码方式：马赛克 / 高斯模糊（脚本顶部 `MASK_MODE` 或 `--mode` 切换；高斯模糊观感自然，推荐后续仍需跑动作检测的视频使用）
- IoU 多目标跟踪 + 指数平滑，减少人脸框闪烁
- 鼠标框选固定区域打码
- 预览模式（`--preview`）：先看检测效果，不输出文件
- 帧范围选择（`--start-frame` / `--end-frame`）
- 自动合并原音频（`--merge-audio`）

**关键参数（脚本顶部配置区可直接改，命令行传参会覆盖）：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `MASK_MODE` / `--mode` | blur | 打码方式：mosaic=马赛克, blur=高斯模糊 |
| `BLUR_STRENGTH` / `--blur-strength` | 0.5 | 模糊强度，核大小 = 区域短边 × 该值 |
| `MOSAIC_BLOCKS` / `--mosaic-blocks` | 12 | 马赛克粒度，越小格子越大 |
| `FACE_EXPAND` / `--face-expand` | 2.4 | 人脸框放大倍数（注意：框大小取"面部点跨度×倍数 / 人体框高×0.22 / 最小边长"三者最大值） |
| `MIN_FACE_SIZE` / `--min-face-size` | 24 | 人脸框最小边长像素 |
| `--kp-conf` | 0.25 | 关键点置信度阈值 |
| `--track-smooth` | 0.6 | 平滑系数（0=不平滑，1=完全不动） |
| `--track-max-lost` | 30 | 跟踪丢失后保留帧数 |
| `--no-face` | — | 跳过人脸检测，只做手动框选 |
| `--no-fix-roi` | — | 跳过手动框选，只做人脸检测 |

## 快速开始

### 环境要求

- Python 3.10+
- CUDA（推荐）

### 安装

```bash
pip install -r requirements.txt
```

> **注意**：`openpyxl` 因网络限制无法安装时可跳过，报告已改用 CSV 格式（标准库，无需额外依赖）。

将 `yolo26x-pose.pt` 放入 `models/` 目录。

### 运行

```bash
python scripts/run_shangtichang.py   # 上体场
python scripts/run_baoshan.py        # 宝山（角度法）
python scripts/run_jingansi.py       # 静安寺
python scripts/run_tangqiao.py       # 塘桥
python scripts/run_pudongdadao.py    # 浦东大道
python scripts/run_linping.py        # 临平
python scripts/run_longhuazhong.py   # 龙华中
```

## 开发约定 / 运行环境

### 开发约定

- **测试先行、单站验证**：新功能先写测试文件，用单个站点（如塘桥）验证，通过后再推广到所有站点
- **实验功能隔离**：新功能优先放独立模块 / 独立脚本，正式站点脚本与 `VideoPlayer` 保持原样；验证通过后并入 `VideoPlayer` 并通过 JSON 配置启用（如 `gate_line` 键），站点脚本零改动
- 与 AI 协作时不需要计划模式 / 任务清单，直接执行并口头说明步骤即可

### 运行环境（实际开发机）

- 运行环境：`D:\anaconda\envs\yolo\python.exe`（已装 ultralytics 8.4.75 与 numpy）
- PATH 上的裸 `python` 是 Windows Store 存根（退出码 49），不可用；`D:\pycharm_pytorch\.venv` 未装 ultralytics
- Windows 控制台 GBK 下中文 print 显示为乱码属显示问题，不影响测试结果
- 用户主目录名含引号，命令行访问 `~` 需用 `os.path.expanduser`

## 操作说明

| 按键 | 功能 |
|------|------|
| `空格` | 暂停 / 继续 |
| `Q` | 退出 |
| `Z` | 重置检测器 |
| 拖拽进度条 | 跳转 |
| **暂停时** | |
| `R` | 框选矩形区域 |
| `L` | 鼠标画参考线 |
| `T` | 框选/删除轨道监控区域 |
| `B` | 保存当前帧为背景参考图 |
| `D` | 删除最后区域 |
| `K` | 删除最后参考线 |
| `S` | 保存标注到 JSON |
| `G` | 画/翻转门线（只检测线内侧人员，即司机侧） |
| `X` | 删除门线 |

## 变更历史

- **2026-08-19**: **门线全站启用**：门线能力并入 `VideoPlayer`（`load_gate_info` + 门线选人 + G/X 键），任何站点 JSON 配 `gate_line` 键即启用（脚本零改动）；删除测试文件 `run_tangqiao_test.py` / `test_gate_tangqiao.py` / `src/gate_player.py`；塘桥 JSON 已含门线，其余站点可在各站脚本暂停后按 G 画线保存。详见上文"门线"章节。
- **2026-08-17**: **门线（司机侧人员过滤）— 塘桥先行测试**：`GatePlayer` 子类 + `run_tangqiao_test.py`（G 画/翻转、X 删除、S 保存）；`person_filter.py` 纯函数（bbox 底边 + 髋部锚点、`inside_side` 旗标、12px 死区滞回、kp/boxes 同索引）；`VideoPlayer` 保持无门线，验证后正式站 JSON 配 `gate_line` 键启用。详见上文"门线"章节。
- **2026-07-31**: **报告时间改时分秒** (`src/timefmt.py`) — CSV 中列车到/离站、停靠时长、动作检测时间统一为 `HH:MM:SS`（整数秒）；仅报告生效，控制台/视频仍为秒。
- **2026-07-30**: **跳跃扫描全站推广**（`idle_jump_seconds=5`，7 站全部启用）：空闲段 5 秒一跳只算 MAD，MAD>20 → 回退 5 秒逐帧确认到站（时间戳精确），误报 2 秒自动恢复；**多趟车按趟结算**（确认离站结算本趟，结束结算末趟）；**单 CSV 多分块报告**；**真实帧号同步**（跳帧不影响时间戳）；删除 `run_tangqiao_fast.py`（已并入正式版）。
- **2026-07-29**: **空闲快进**（`idle_fast_forward`）：列车 AWAY 时跳过 YOLO 推理，仅逐帧算 MAD；**自动退出**（`auto_exit`）播完自动生成报告并退出；**输出命名跟随输入视频**（`pose_output_<视频名>.mp4` / `report_<视频名>.csv`），各站脚本顶部新增 `OUT_DIR`；**脱敏工具高斯模糊模式**（默认 `blur`，`BLUR_STRENGTH` 自适应核大小）。
- **2026-07-27**: **视频脱敏工具** (`scripts/video_anonymize.py`) — 人脸自动打码（pose 关键点定位 + IoU 跟踪平滑），支持手动框选、预览、帧范围、GPU、音频合并。
- **2026-07-23**: **静安寺、龙华中新增道岔检测**（Act5 CheckSwitch，复用 `anti_parallel`，动作数 4→5）；删除浦东大道测试脚本（已并入正式版）。
- **2026-07-22**: **同帧冲突仲裁**（`src/detector.py`）— 同帧多角度规则触发时按归一化分数保留最可信一个，被淘汰的不进冷却；`pass_region` 豁免；`data/` 加入 `.gitignore`。
- **2026-07-16**: **置信度质量指标**（conf/hit_rate/margin）；**CSV 报告**（`src/reporter.py`）；输出目录重构为 `output/video` + `output/report`；**标准 5 动作模板**；**动态角度补偿**（`dynamic_angle`，40° + 弯曲角 × 0.6）；浦东大道/临平/龙华中激活；删除废弃 Streamlit `app.py` 与 v1 状态机 `state_machine.py`；修复 `save_annotations` 保留 background/track_roi。
