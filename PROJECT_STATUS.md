# Project Status — 端头门司机行为分析

**Updated:** 2026-07-31

## Architecture

v2 parallel detection + post-hoc analysis.  
All rules run independently per frame → timestamped events → mapped to action sequence after video ends → compliance report.

## Current State

### Detection engine (`src/detection.py` + `src/detector.py`)
- 4 detection types: `parallel_line`, `pass_region`, `pointing`, `pointing_with_line`
- COCO 17-keypoint, indices 5-12 (shoulders/elbows/wrists/hips)
- Per-rule hold counter (30 frames confirm, -2 decay) + 90-frame cooldown
- Normal mode: `frame_skip=0, imgsz=640` (every-frame detection)
- All 7 stations configured and operational

### Visualization (`src/visualization.py`)
- **All English, pure OpenCV rendering** — no PIL dependency, no Chinese text
- `draw_status_overlay` — detection panel (top-left): rules, hold progress, fired count, action status
- `draw_action_metrics` — per-action real-time angles (deg/S-W/S-E/W)
- `draw_arm_rays` — cyan/magenta arm segments, green/red extended rays
- `draw_annotations` — regions + reference lines overlay
- `draw_train_status` — train arrival/departure badge (top-right)
- `draw_analysis_result` — final result overlay (bottom-left)
- `draw_frame_info` — frame counter (top-right)
- `draw_confidence_legend` — confidence tier colour legend (bottom-right)

### Confidence Colour (`src/confidence_color.py`)
- Three-tier keypoint confidence colouring: red (<0.3), yellow (0.3-0.6), green (>0.6)
- Configurable per-station via `conf_low_threshold` / `conf_mid_threshold` in VideoPlayer
- Applied to pose keypoints, elbow circles, and wrist circles
- Legend drawn at bottom-right corner

### Player (`src/player.py`)
- Interactive video player with progress bar, pause/seek/annotate
- Pause: only left panel (right panel removed)
- Keys: Space=pause, Q=quit, R=draw region, L=draw line, T=track ROI, B=save bg, S=save JSON, Z=reset
- Confidence mapper created in constructor, passed through all render calls
- **Idle jump-scan** (`idle_fast_forward=True, idle_jump_seconds=5, auto_exit=True`, all stations):
  track empty → decode 1 frame per 5s for MAD only; MAD spike → print "疑似列车进站",
  rewind 5s, per-frame confirm (arrival timestamp exact); false alarm (2s low MAD) → resume jumping.
  Sampled frames written to output with `FAST-SCAN MAD @ mm:ss` overlay (idle ≈ 125× fast-play in output)
- **Multi-stop settlement**: on confirmed departure, current stop's events are analysed,
  stored as a report block, and cleared; last stop settled at video end.
  All timestamps (train + action events) use real video frame numbers, immune to frame skipping

### Scenarios

| Scenario | Script | Actions | Types | Status |
|----------|--------|---------|-------|--------|
| Shangtichang | `scripts/run_shangtichang.py` | Act1 Call, Act2 CloseDoor, Act3 CheckGap, Act4 CheckLight | PAR + CROSS | Done |
| Baoshan | `scripts/run_baoshan.py` | Act1 PointFwd, Act2 CheckR2, Act3 PointFwd, Act4 CheckR3, Act5 CheckR4 | P+L + POINT | Done |
| Jingansi | `scripts/run_jingansi.py` | Act1 Call, Act2 CloseDoor, Act3 CheckGap, Act4 CheckLight, Act5 CheckSwitch | PAR + CROSS | Done |
| Tangqiao | `scripts/run_tangqiao.py` | Act1 Call, Act2 CloseDoor, Act3 CheckGap, Act4 CheckLight | PAR + CROSS | Done |
| Pudongdadao | `scripts/run_pudongdadao.py` | Act1 Call, Act2 CloseDoor, Act3 CheckGap, Act4 CheckLight, Act5 CheckSwitch | PAR + CROSS | Done |
| Linping | `scripts/run_linping.py` | Act1 Call, Act2 CloseDoor, Act3 CheckGap, Act4 CheckLight | PAR + CROSS | Done |
| Longhuazhong | `scripts/run_longhuazhong.py` | Act1 Call, Act2 CloseDoor, Act3 CheckGap, Act4 CheckLight, Act5 CheckSwitch | PAR + CROSS | Done |

### Train Detection (all configured stations)
| Station | Background | Track ROI | Train MAD Threshold |
|---------|-----------|-----------|---------------------|
| Shangtichang | Yes | Yes | 20 |
| Baoshan | Yes | Yes | 20 |
| Jingansi | Yes | Yes | 20 |
| Tangqiao | Yes | Yes | 20 |
| Pudongdadao | Yes | Yes | 20 |
| Linping | Yes | Yes | 20 |
| Longhuazhong | Yes | Yes | 20 |

### Train Detection
- Background-frame differencing via `src/train_detector.py`
- Pure accumulation (no decay, no reset): MAD > 20 increments counter, MAD ≤ 20 does nothing
- Arrival: 20 cumulative frames above 20 → confirmed. Departure: 20 frames below 15 → confirmed
- Real-time MAD + hold counter displayed at top-right via `draw_train_status`
- Requires: `track` region + saved background image in annotations JSON

## Key Parameters

| Param | Value | Notes |
|-------|-------|-------|
| angle_threshold | 40° | arm vs ref_line (PAR) |
| min_arm_torso_angle | 45° | prevents false triggers (per-rule overridable) |
| hold_frames | 20 | consecutive confirm count |
| frame_decay | 2/frame | tolerates brief dropout |
| cooldown | 90 frames | prevents event splitting |
| ray extend | 6× | pass_region extension |
| conf_low_threshold | 0.3 | red keypoints below this |
| conf_mid_threshold | 0.6 | yellow below this, green above |
| train_mad_threshold | 20 | MAD above this → train arriving |
| dynamic_angle_coeff | 0.6 | elbow bend compensation for 2D foreshortening |

## Recent Changes

- **2026-07-31**:
  - **报告时间改为时分秒格式** (`src/timefmt.py` 新增): CSV 报告中的列车到站/离站/停靠时长、动作检测时间统一为 `HH:MM:SS`（整数秒，四舍五入）。仅报告文档生效，控制台打印和视频叠加仍为秒数。整数秒格式避免 Excel 将带小数秒的时间套用 `mm:ss.0` 格式而丢失小时位。

- **2026-07-30**:
  - **跳跃扫描全站推广** (`src/player.py` `idle_jump_seconds=5`): 空闲段 5 秒一跳只解码采样帧算 MAD；采样帧 MAD>20 → "疑似列车进站"，回退 5 秒逐帧确认（到站时间戳精确不变），误报 2 秒后恢复跳跃。采样帧写输出并叠加 `FAST-SCAN MAD @ mm:ss`（空闲段输出 ≈125 倍速快放）。全部 7 个站点脚本启用 `idle_fast_forward + idle_jump_seconds=5 + auto_exit`。
  - **多趟车按趟结算**: 确认离站时结算本趟事件（分析+报告块+清空），下一趟从零开始；视频结束结算最后一趟。
  - **单 CSV 多分块报告** (`src/reporter.py`): 一个视频一份报告，每趟一个"第N趟列车"分块；总体评估改为"顺序正确/顺序不正确"，新增"动作是否符合规范（是/否）"（全部检出且顺序正确才为是）。
  - **真实帧号同步**: `TrainDetector`/`ParallelDetector` 时间戳改用真实视频帧号，跳帧不影响；`TrainDetector` 新增无状态 `measure()`，删除无人使用的 `train_info`。
  - **删除 `scripts/run_tangqiao_fast.py`**: 跳跃扫描已合并至正式版。

- **2026-07-29**:
  - **空闲快进模式** (`src/player.py` `idle_fast_forward`): 列车 AWAY 时跳过 YOLO 推理和渲染，每帧只做 MAD 帧差检测（~1ms/帧），到站自动恢复逐帧检测。结束打印快进/检测帧数统计。
  - **自动退出** (`auto_exit`): 视频播完自动生成报告并退出，无需手动按 Q。
  - **输出命名跟随输入视频**: 输出视频 `pose_output_<视频名>.mp4`、报告 `report_<视频名>.csv` 由输入视频名自动生成，不同视频不再互相覆盖。所有站点脚本删除写死的 `output_name`，顶部新增 `OUT_DIR` 配置输出根目录。
  - **脱敏工具高斯模糊模式** (`scripts/video_anonymize.py`): 新增 `MASK_MODE`/`--mode`（mosaic/blur）与 `--blur-strength`（核=区域短边×系数，自适应人脸大小），默认改为 blur；`FACE_EXPAND`/`MIN_FACE_SIZE`/`MOSAIC_BLOCKS` 提升为脚本顶部配置。

- **2026-07-27**:
  - **视频脱敏工具** (`scripts/video_anonymize.py`): 独立的人脸自动马赛克脚本。基于 YOLO pose 关键点定位人脸（鼻/眼/耳 + 双肩兜底），IoU 多目标跟踪 + 指数平滑减少闪烁。支持手动框选固定区域打码、预览模式、帧范围选择、GPU 加速、自动合并音频。两种使用方式：直接改脚本顶部配置或命令行传参。

- **2026-07-23**:
  - **静安寺、龙华中新增道岔检测（Act5 CheckSwitch）**: 复用 `line_1` + `anti_parallel`，与浦东大道逻辑一致。两站动作数从 4→5。
  - **删除浦东大道测试脚本** (`scripts/run_pudongdadao_test.py`): 测试功能已合并至正式版，不再需要独立脚本。

- **2026-07-22**:
  - **同帧冲突仲裁** (`src/detector.py`): 同一帧多个角度类规则同时触发时，计算归一化分数（`angle / effective_threshold`），只保留最可信的一个。被淘汰的规则不进冷却，可立即重新累积。`pass_region` 豁免。
  - **`data/` 目录加入 `.gitignore`**: 标注数据不再上传至远程仓库（保密）。

- **2026-07-16**: 
  - **Confidence quality metrics**: conf (keypoint avg), hit_rate (hit/total frames), margin (effective threshold − actual angle) computed at event trigger and displayed in SequenceAnalyzer summary.
  - **CSV report generation** (`src/reporter.py`): Auto-generated after video ends to `output/report/report_xxx.csv`. Contains station info, model params, train arrival/departure, per-action results with quality metrics, overall evaluation.
  - **Output directory restructured**: `output/video/` for annotated videos, `output/report/` for CSV reports.
  - **Standard 5-action template**: 开门后手指呼唤 / 手动关门 / 关门后确认夹缝 / 开车前确认站台指示灯 / 开车前确认站台道岔. Missing actions marked "不需要".
  - All stations now pass `station_name` and `model_path` to VideoPlayer for report generation.
  - `TrainDetector` added `train_info` property for structured arrival/departure data.
  - Dynamic angle compensation: all stations use `dynamic_angle` on parallel_line rules. Effective threshold = 40° + arm_bend × 0.6, compensating for 2D foreshortening.
  - Pudongdadao, Linping, Longhuazhong stations activated with full rules and action mappings.
  - Removed deprecated Streamlit dashboard (`app.py`) and v1 serial state machine (`src/state_machine.py`).
  - Fixed `save_annotations` to preserve `background` and `track_roi` fields on save. Cleaned up stale cache and old package layout.
