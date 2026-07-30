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
│   ├── visualization.py   # 可视化（骨架、面板、中文渲染、调试射线）
│   ├── annotation.py      # 标注工具（区域框选、参考线、背景保存）
│   ├── reporter.py        # CSV 检测报告生成
│   ├── train_detector.py  # 列车进出站检测（背景帧差法）
│   └── player.py          # 交互式视频播放器
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

| 站点 | 脚本 | 检测类型 | 动作数 |
|------|------|------|--------|
| 上体场 | `run_shangtichang.py` | PAR + CROSS | 4 |
| 宝山 | `run_baoshan.py` | P+L + POINT | 5 |
| 静安寺 | `run_jingansi.py` | PAR + CROSS | 5 |
| 塘桥 | `run_tangqiao.py` | PAR + CROSS | 4 |
| 浦东大道 | `run_pudongdadao.py` | PAR + CROSS | 5 |
| 临平 | `run_linping.py` | PAR + CROSS | 4 |
| 龙华中 | `run_longhuazhong.py` | PAR + CROSS | 5 |

所有站点均已配置列车进出站检测（背景帧差法）。

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
