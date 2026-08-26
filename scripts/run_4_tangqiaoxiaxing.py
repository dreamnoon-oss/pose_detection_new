"""检测入口：4号线 塘桥 下行 — parallel-line + pass-region detection."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ultralytics import YOLO
from server import stations as st
from src.config import MODEL_DIR, DATA_DIR, OUTPUT_DIR
from src.detector import ParallelDetector
from src.annotation import load_annotations
from src.player import VideoPlayer

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
VIDEO_PATH = r"C:\塘桥7.8-7.10\[114](4)塘桥下行端头门1-2026-07-08 08-00-00--2026-07-08 09-00-00.mp4"
LINE = "4号线"     # 线路，用于按上下行解析标注
STATION = "塘桥"   # CSV 站名（别名站需全名）
DIRECTION = "down" # 上行 up / 下行 down
MODEL_PATH = str(Path(MODEL_DIR) / "yolo26x-pose.pt")
ANNOTATIONS_FILE = str(st.resolve_annotation_path(LINE, STATION, DIRECTION))

# 输出配置：视频存到 OUT_DIR/video/，报告存到 OUT_DIR/report/，文件名自动跟随输入视频名
OUT_DIR = str(Path(OUTPUT_DIR))  # 输出根目录，可改成任意路径

# ---------------------------------------------------------------------------
# Detection rules (unique conditions; each runs independently)
# ---------------------------------------------------------------------------
DETECTION_RULES = [
    {"name": "rule_A", "type": "parallel_line", "ref_line": "line_1", "min_arm_torso_angle": 0, "dynamic_angle": True},
    {"name": "rule_B", "type": "parallel_line", "ref_line": "line_2", "allow_elbow": True, "dynamic_angle": True},
    {"name": "rule_C", "type": "pass_region", "target_region": "region_1"},
]

# ---------------------------------------------------------------------------
# Action mapping: which rule occurrence maps to which action
# ---------------------------------------------------------------------------
ACTION_MAPPING = [
    {"action": "Act1", "rule": "rule_A", "occurrence": 1},
    {"action": "Act2", "rule": "rule_B", "occurrence": 1},
    {"action": "Act3", "rule": "rule_A", "occurrence": 2},
    {"action": "Act4", "rule": "rule_C", "occurrence": 1},
]

DETECTION_KWARGS = {
    "angle_threshold": 40,
    "min_arm_len": 30,
    "min_arm_torso_angle": 45,
    "dynamic_angle_coeff": 0.6,
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    regions, lines = load_annotations(ANNOTATIONS_FILE)

    detector = ParallelDetector(
        DETECTION_RULES, regions, lines,
        hold_frames=20, frame_decay=2, cooldown_frames=90,
        detection_kwargs=DETECTION_KWARGS,
    )

    model = YOLO(MODEL_PATH)
    player = VideoPlayer(
        model, VIDEO_PATH, detector, ACTION_MAPPING,
        annotations_file=ANNOTATIONS_FILE,
        output_dir=OUT_DIR,
        station_name="塘桥", model_path=MODEL_PATH,
        imgsz=640, frame_skip=0,
        conf_low_threshold=0.3, conf_mid_threshold=0.6,
        train_mad_threshold=20,
        idle_fast_forward=True,
        idle_jump_seconds=5,
        auto_exit=True,
    )
    player.run()
