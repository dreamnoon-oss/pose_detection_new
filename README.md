# Pose Detection — 端头门司机行为分析

基于 YOLO 姿态估计的列车司机标准动作实时识别与合规判断系统。

## 架构

```
脚本模式: run_xxx.py → VideoPlayer → ParallelDetector → detection.py → geometry.py
                ↕                ↕
          annotation.py    SequenceAnalyzer (事后分析)
          visualization.py

Web 模式: run_web.py → FastAPI (server/main.py + engine.py) → DetectionJob（无头引擎，复用同一套 src/ 模块）
                ↕
        static/ 前端页面（检测分析 / 标注工具 / 站点管理）
```

**核心思路**：所有动作规则**并行独立检测**，各自记录触发时间戳。视频播放结束后，按事件发生顺序映射到预期动作序列，判定合规性。

**Web 与脚本共用同一套检测引擎**（`src/detector.py` / `src/train_detector.py` / `src/analyzer.py` / `src/reporter.py` / `src/visualization.py`），`server/engine.py` 的 `DetectionJob` 是无头版本（无 OpenCV GUI），在后台线程运行并通过 `JobState` 轮询进度。

## 项目结构

```
pose_detection/
├── README.md            # 项目文档 + 进度时间线（本文档）
├── pyproject.toml
├── requirements.txt
├── run_web.py           # Web 前端启动入口（FastAPI + 静态页面）
├── server/              # Web 后端
│   ├── main.py          #   FastAPI 路由（视频/检测/参数/标注/下载）
│   ├── engine.py        #   无头检测引擎 DetectionJob + TaskManager
│   ├── stations.py      #   站点注册表（线路/站点/方向、规则模板、默认参数）
│   └── static/          #   前端页面（index.html + app.js + style.css）
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
│   ├── device.py          # 设备选择（auto → cuda/mps/cpu）与线程调优
│   ├── confidence_color.py# 关键点置信度三档着色
│   └── player.py          # 交互式视频播放器（含门线）
├── scripts/
│   ├── run_3_baoshanshangxing.py       # 3号线 宝山 上行
│   ├── run_3_baoshanlushangxing.py     # 3号线 宝山路 上行
│   ├── run_3_longcaoshangxing.py       # 3号线 龙漕路 上行
│   ├── run_3_shanghainanshangxing.py   # 3号线 上海南站 上行
│   ├── run_4_linpingshangxing.py       # 4号线 临平路 上行
│   ├── run_4_pudongdadaoshangxing.py   # 4号线 浦东大道 上行
│   ├── run_4_shangtichangxiaxing.py    # 4号线 上海体育场 下行
│   ├── run_4_tangqiaoxiaxing.py        # 4号线 塘桥 下行
│   ├── run_4_zhongshanparkshangxing.py # 4号线 中山公园 上行
│   ├── run_4_zhongshanparkxiaxing.py   # 4号线 中山公园 下行
│   ├── run_7_changqingshangxing.py     # 7号线 长清路 上行
│   ├── run_7_jingansishangxing.py      # 7号线 静安寺 上行
│   ├── run_7_jingansixiaxing.py        # 7号线 静安寺 下行
│   ├── run_7_longhuazhongxiaxing.py    # 7号线 龙华中路 下行
│   ├── run_7_longyangshangxing.py      # 7号线 龙阳路 上行
│   ├── run_7_longyangxiaxing.py        # 7号线 龙阳路 下行
│   ├── profile_timing.py   # 无头性能分析
│   └── video_anonymize.py   # 视频脱敏工具
├── data/                   # 标注数据 (JSON + 背景图)
├── models/                 # 模型文件
├── output/                 # 输出（video/report/uploads）
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

脚本命名与标注 JSON 一致：`run_{线路}_{站拼音}{shangxing|xiaxing}.py`（上下行分开）。

| 站点 | 脚本 | 方向 | 检测类型 | 动作数 | 动作 |
|------|------|------|------|--------|------|
| 宝山 | `run_3_baoshanshangxing.py` | 上行 | P+L + POINT | 5 | PointFwd / CheckR2 / PointFwd / CheckR3 / CheckR4 |
| 宝山路 | `run_3_baoshanlushangxing.py` | 上行 | PAR + CROSS | 4 | Call / CloseDoor / CheckGap / CheckLight |
| 龙漕路 | `run_3_longcaoshangxing.py` | 上行 | PAR + CROSS | 4 | Call / CloseDoor / CheckGap / CheckLight |
| 上海南站 | `run_3_shanghainanshangxing.py` | 上行 | PAR + CROSS | 4 | Call / CloseDoor / CheckGap / CheckLight |
| 上体场 | `run_4_shangtichangxiaxing.py` | 下行 | PAR + CROSS | 4 | Call / CloseDoor / CheckGap / CheckLight |
| 塘桥 | `run_4_tangqiaoxiaxing.py` | 下行 | PAR + CROSS | 4 | Call / CloseDoor / CheckGap / CheckLight |
| 浦东大道 | `run_4_pudongdadaoshangxing.py` | 上行 | PAR + CROSS | 5 | Call / CloseDoor / CheckGap / CheckLight / CheckSwitch |
| 临平 | `run_4_linpingshangxing.py` | 上行 | PAR + CROSS | 4 | Call / CloseDoor / CheckGap / CheckLight |
| 中山公园 | `run_4_zhongshanpark{shangxing,xiaxing}.py` | 上/下行 | PAR + CROSS | 4 | Call / CloseDoor / CheckGap / CheckLight |
| 静安寺 | `run_7_jingansi{shangxing,xiaxing}.py` | 上/下行 | PAR + CROSS | 5/4 | 上行含 CheckSwitch |
| 长清路 | `run_7_changqingshangxing.py` | 上行 | PAR + CROSS | 4 | Call / CloseDoor / CheckGap / CheckLight |
| 龙阳路 | `run_7_longyang{shangxing,xiaxing}.py` | 上/下行 | PAR + CROSS | 4 | Call / CloseDoor / CheckGap / CheckLight |
| 龙华中 | `run_7_longhuazhongxiaxing.py` | 下行 | PAR + CROSS | 5 | Call / CloseDoor / CheckGap / CheckLight / CheckSwitch |

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

### 可视化模块（`src/visualization.py`，纯 OpenCV 英文渲染，无 PIL 依赖）

| 函数 | 作用 |
|------|------|
| `draw_status_overlay` | 检测面板（左上角）：规则、hold 进度、触发次数、动作状态 |
| `draw_action_metrics` | 每个动作的实时夹角（deg/S-W/S-E/W） |
| `draw_arm_rays` | 青/洋红手臂线段，绿/红延长射线 |
| `draw_annotations` | 区域 + 参考线叠加（含门线） |
| `draw_train_status` | 列车到/离站状态徽章（右上角，MAD + hold） |
| `draw_analysis_result` | 最终分析结果叠加（左下角） |
| `draw_frame_info` | 帧计数器（右上角） |
| `draw_confidence_legend` | 置信度三档颜色图例（右下角） |

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

## Web 前端

基于 FastAPI + 原生 HTML/JS 的本地单机 Web 界面，**与脚本共用同一套检测引擎**。

### 功能（三个页面）

| 页面 | 功能 |
|------|------|
| **检测分析** | 线路/站点/方向选择（标注状态展示）、视频加载（文件上传或服务器本地路径）、运行模式（仅播放 / 开启检测 / **仅检测列车进出站**）、参数配置（模型置信度、imgsz、角度阈值、跳跃扫描间隔等）、检测进度与状态、源视频/结果视频播放、列车到站事件列表、结果下载（检测视频 + CSV 报告） |
| **标注工具** | 浏览器内框选区域 / 画参考线 / 轨道 ROI / 门线（含 IN 侧翻转），保存背景图，生成站点标注 JSON；保存自动按上下行写入新格式文件，导入旧的无方向标注后保存即自动迁移 |
| **站点管理** | 线路站点列表、每站每方向的标注状态（已标注/不完整/未标注） |

### 检测任务模型

- 同时只允许**一个**检测任务（`TaskManager`），后台线程运行，前端轮询 `/api/detect/status`
- 任务结束后生成输出视频（`output/video/pose_output_<视频名>.mp4`）与 CSV 报告（`output/report/report_<视频名>.csv`）
- 结果 JSON 含 `engine.device` / `engine.half`，可核对实际运行设备

### 启动方式

```bash
# 必须用 pose 环境（GPU）启动，否则 torch.cuda.is_available()=False 落到 CPU，速度慢 ~20 倍
C:\anaconda\anaconda3\envs\pose\python.exe run_web.py
# 访问 http://127.0.0.1:8000
```

桌面上有 `启动.bat` 一键启动（自动打开浏览器）。**注意**：项目根 `.venv` 里的 torch 是 CPU 版（pip 默认源），不要用它跑 Web。

### 设备选择

`src/device.py::resolve_device("auto")`：CUDA > MPS > CPU；`half=True`（默认）在 GPU 上启用 FP16。验证：`/api/model/status` 或检测结果 `engine.device` 应为 `cuda:0`。

### 仅检测列车进出站（train_only）模式

- 不加载 YOLO 模型，只做背景帧差法（MAD）检测列车到站/离站，报告只含到站/离站/停靠时长
- **跳跃扫描在空闲和在场状态都生效**（2026-08-26 优化）：每 5 秒采样 1 帧，疑似进站（MAD>20）/疑似离站（MAD<15）时回退 5 秒逐帧确认，时间戳精确到帧；误报自动恢复跳跃
- 优化前在场段逐帧（≈0.9x 实时），优化后全程 ≈11.7x 实时（1 小时视频从 1 小时+ 降到 5~10 分钟）

### API 一览

| 路由 | 说明 |
|------|------|
| `/api/stations/*` | 线路/站点列表、标注状态、绑定文件 |
| `/api/video/*` | 视频加载（上传/路径）、信息、播放流 |
| `/api/detect/start|stop|status|result` | 检测任务控制 |
| `/api/params/get|set` | 检测参数读写 |
| `/api/train/events` | 列车到站事件 |
| `/api/download/*` | 结果视频 / CSV 报告下载 |
| `/api/annotation/*` | 标注加载/保存/背景图/抽帧/删除 |
| `/api/model/status` | 模型存在性与运行设备 |

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
# 每个站点脚本按上下行分开，脚本名与标注 JSON 对应；跑前在脚本顶部填 VIDEO_PATH
python scripts/run_3_baoshanshangxing.py       # 3号线 宝山 上行
python scripts/run_4_shangtichangxiaxing.py    # 4号线 上体场 下行
python scripts/run_7_jingansishangxing.py      # 7号线 静安寺 上行
python scripts/run_4_tangqiaoxiaxing.py        # 4号线 塘桥 下行
python scripts/run_4_pudongdadaoshangxing.py   # 4号线 浦东大道 上行
python scripts/run_4_linpingshangxing.py       # 4号线 临平 上行
python scripts/run_7_longhuazhongxiaxing.py    # 7号线 龙华中 下行
```

## 开发约定 / 运行环境

### 开发约定

- **测试先行、单站验证**：新功能先写测试文件，用单个站点（如塘桥）验证，通过后再推广到所有站点
- **实验功能隔离**：新功能优先放独立模块 / 独立脚本，正式站点脚本与 `VideoPlayer` 保持原样；验证通过后并入 `VideoPlayer` 并通过 JSON 配置启用（如 `gate_line` 键），站点脚本零改动
- 与 AI 协作时不需要计划模式 / 任务清单，直接执行并口头说明步骤即可

### 运行环境（实际开发机）

- **统一使用 conda pose 环境**：`C:\anaconda\anaconda3\envs\pose\python.exe`（Python 3.11，torch 2.13.0+cu126，ultralytics 8.4.110，GPU = RTX 2070）。**脚本和 Web 前端都用它跑**
- ⚠️ 项目根 `.venv`（Python 3.10）里的 torch 是 **CPU 版**（pip 默认源装的，`torch.version.cuda` 为 None），跑 Web 会落到 CPU，推理 ~508ms/帧（GPU ~24ms/帧，慢 20 倍）。**不要用它**；如需使用需重装 CUDA 版：`pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126`
- 老环境 `D:\anaconda\envs\yolo`（README 早期记录）已不用
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

- **2026-08-26（GitHub 托管）**: **项目推送到 GitHub**：仓库 `github.com/dreamnoon-oss/pose_detection_new`（HTTPS，master 分支）。注意 `.gitignore` 排除了 `data/`（标注保密）、`models/*.pt`、`output/`——**新机器 clone 后需手动拷贝这三样**（标注 JSON+背景图、模型、站点 CSV `上海地铁3_4_7_15号线站点信息.csv` 在仓库内），并确认 Python ≥ 3.10 + CUDA 版 torch（3060 等 NVIDIA 卡可直跑）
- **2026-08-26（脚本按上下行）**: **站点脚本全面按上下行分离**：
  - 标注路径解析统一走 `resolve_annotation_path`：16 个站点脚本顶部新增 `LINE`/`STATION`/`DIRECTION` 常量（`STATION` 用 CSV 全名，别名站如"临平路/龙华中路/上海体育场"走 `ALIAS_KEYS` 映射），`ANNOTATIONS_FILE` 不再硬编码旧无方向文件名——桌面脚本与前端标注工具读取同一方向文件；`server/main.py` 的 `bindfile` 接口补 `direction` 参数
  - **脚本重命名**（git mv 保留历史）：7 个现有脚本改为与标注 JSON 同构的 `run_{线路}_{站拼音}{shangxing|xiaxing}.py`，如 `run_baoshan.py` → `run_3_baoshanshangxing.py`、`run_tangqiao.py` → `run_4_tangqiaoxiaxing.py`
  - **补齐缺失站点**：新增 9 个脚本——3号线 宝山路/龙漕路/上海南站上行、4号线 中山公园上/下行、7号线 长清路上行、静安寺下行、龙阳路上/下行；新站用标准 4 动作模板（Act1 Call / Act2 CloseDoor / Act3 CheckGap / Act4 CheckLight），`VIDEO_PATH` 留空待填。至此 `data/` 16 个标注 JSON 与 `scripts/` 16 个站点脚本一一对应
  - 引用同步更新：README 目录树/站点表/运行命令、`docs/report_template.md`、`src/reporter.py` 注释；`profile_timing.py`（无头性能分析）保留原名，注释同步
- **2026-08-26（数据一致性）**: **标注 JSON ↔ 背景图一一对应校验**：全量扫描 `data/` 发现 5 处不匹配，已修复——4 个 JSON 的 `background.image` 指向磁盘实际存在的图（浦东大道上行、中山公园下行、长清上行、龙阳上行），上体场下行背景图重命名 `regions_4_shangtichang_background.png` → `regions_4_shangtichangxiaxing_background.png`；修复后 16 对 JSON/背景图全部匹配，0 失效引用、0 孤儿图片
- **2026-08-26（标注工具）**: **门线标注闭环 + 标注保存自动迁移**：
  - 门线修复（`server/static/app.js`）：保存 payload 补上 `gate_line` 字段、加载时恢复门线显示（含 IN 侧）、导出/导入 JSON 携带门线——之前门线画了保存不上、重开页面不显示
  - 标注列表新增 `gate` 分组（`renderAnnoList`）：始终显示门线状态（未设置灰色提示 / 已设置显示 IN 侧），**点击条目可直接翻转 IN 侧方向**（支持撤销）
  - 标注保存上下行分离 + **自动迁移**（`server/stations.py::resolve_annotation_path` 新增 `for_save` 参数 + `server/main.py`）：保存时强制写入带上下行的新格式文件 `regions_{线}_{站}{上行|下行}.json`，不再回退到无方向的旧文件，**上行/下行标注不再互相覆盖**；保存成功后**自动删除旧格式 JSON、背景图同步改名并更新引用**——加载旧标注 → 修改 → 保存即完成迁移，无僵尸文件（修复旧路径需在写新文件前解析的 bug，否则迁移不触发）；加载仍兼容旧文件
  - 静态文件禁用缓存（`server/main.py` `NoCacheStaticFiles`）：`Cache-Control: no-store`，前端改动普通刷新即生效（修复 `get_response` 未 `await` 导致的 /static 500）
  - 数据整理：宝山站、静安寺下行等旧命名标注已迁移为新格式，僵尸文件已清理
- **2026-08-26**: **Web 检测性能修复与 train_only 提速**：
  - **根因定位**：Web 慢是因为项目根 `.venv` 的 torch 是 CPU 版（`torch.cuda.is_available()=False` → CPU 推理 ~508ms/帧 vs GPU ~24ms/帧）。修复：统一用 conda pose 环境（GPU）启动 Web，桌面 `启动.bat` 一键启动
  - **MAD 计算提速**（`src/train_detector.py`）：`astype(float)` 差分 → `cv2.absdiff + cv2.mean`（uint8 运算，结果数学等价），每帧 34ms → ~2ms，前后端所有模式受益
  - **train_only 在场跳跃扫描**（`server/engine.py`）：列车在场时不再逐帧，改为每 5 秒采样 1 帧；疑似离站（MAD<15）回退逐帧确认（时间戳精确），确认中 MAD 回升 1 秒恢复跳跃。实测 75 秒停站片段 6.4 秒跑完（**0.9x → 11.7x 实时**，约 13 倍提速），1 小时视频预计 5~10 分钟
  - 全检测模式逻辑不变（在场仍需逐帧 YOLO）；`PROJECT_STATUS.md` 归档删除，内容已并入本文档
- **2026-08-13 ~ 08-20**: **Web 前端上线**（`run_web.py` + `server/` + `static/`）：
  - FastAPI 后端：视频加载（上传/本地路径）、检测任务控制（开始/停止/状态/结果）、参数配置、列车事件、结果下载（视频+CSV）、标注管理（保存/背景图/抽帧/删除）、模型状态
  - 前端三个页面：检测分析 / 标注工具 / 站点管理；`server/engine.py` 无头引擎复用 `src/` 全部检测模块，支持仅播放 / 开启检测 / 仅检测列车进出站三种运行模式
  - 站点注册表 `server/stations.py`：4 条线路站点拼音映射、7 个已配置站点的规则模板（含道岔 Act5）、上下行方向标注文件解析（新命名 `regions_{线}_{站}{方向}.json` 与旧命名兼容）
- **2026-08-19**: **门线全站启用**：门线能力并入 `VideoPlayer`（`load_gate_info` + 门线选人 + G/X 键），任何站点 JSON 配 `gate_line` 键即启用（脚本零改动）；删除测试文件 `run_tangqiao_test.py` / `test_gate_tangqiao.py` / `src/gate_player.py`；塘桥 JSON 已含门线，其余站点可在各站脚本暂停后按 G 画线保存。详见上文"门线"章节。
- **2026-08-17**: **门线（司机侧人员过滤）— 塘桥先行测试**：`GatePlayer` 子类 + `run_tangqiao_test.py`（G 画/翻转、X 删除、S 保存）；`person_filter.py` 纯函数（bbox 底边 + 髋部锚点、`inside_side` 旗标、12px 死区滞回、kp/boxes 同索引）；`VideoPlayer` 保持无门线，验证后正式站 JSON 配 `gate_line` 键启用。详见上文"门线"章节。
- **2026-07-31**: **报告时间改时分秒** (`src/timefmt.py`) — CSV 中列车到/离站、停靠时长、动作检测时间统一为 `HH:MM:SS`（整数秒）；仅报告生效，控制台/视频仍为秒。
- **2026-07-30**: **跳跃扫描全站推广**（`idle_jump_seconds=5`，7 站全部启用）：空闲段 5 秒一跳只算 MAD，MAD>20 → 回退 5 秒逐帧确认到站（时间戳精确），误报 2 秒自动恢复；**多趟车按趟结算**（确认离站结算本趟，结束结算末趟）；**单 CSV 多分块报告**；**真实帧号同步**（跳帧不影响时间戳）；删除 `run_tangqiao_fast.py`（已并入正式版）。
- **2026-07-29**: **空闲快进**（`idle_fast_forward`）：列车 AWAY 时跳过 YOLO 推理，仅逐帧算 MAD；**自动退出**（`auto_exit`）播完自动生成报告并退出；**输出命名跟随输入视频**（`pose_output_<视频名>.mp4` / `report_<视频名>.csv`），各站脚本顶部新增 `OUT_DIR`；**脱敏工具高斯模糊模式**（默认 `blur`，`BLUR_STRENGTH` 自适应核大小）。
- **2026-07-27**: **视频脱敏工具** (`scripts/video_anonymize.py`) — 人脸自动打码（pose 关键点定位 + IoU 跟踪平滑），支持手动框选、预览、帧范围、GPU、音频合并。
- **2026-07-23**: **静安寺、龙华中新增道岔检测**（Act5 CheckSwitch，复用 `anti_parallel`，动作数 4→5）；删除浦东大道测试脚本（已并入正式版）。
- **2026-07-22**: **同帧冲突仲裁**（`src/detector.py`）— 同帧多角度规则触发时按归一化分数保留最可信一个，被淘汰的不进冷却；`pass_region` 豁免；`data/` 加入 `.gitignore`。
- **2026-07-16**: **置信度质量指标**（conf/hit_rate/margin）；**CSV 报告**（`src/reporter.py`）；输出目录重构为 `output/video` + `output/report`；**标准 5 动作模板**；**动态角度补偿**（`dynamic_angle`，40° + 弯曲角 × 0.6）；浦东大道/临平/龙华中激活；删除废弃 Streamlit `app.py` 与 v1 状态机 `state_machine.py`；修复 `save_annotations` 保留 background/track_roi。
