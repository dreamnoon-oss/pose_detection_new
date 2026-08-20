"""Headless detection engine for the web backend.

Reuses the existing detection modules (``ParallelDetector``, ``TrainDetector``,
``SequenceAnalyzer``, ``visualization``, ``reporter``) but runs without any
OpenCV GUI: no ``imshow``/``waitKey``/``namedWindow``/trackbars. A single
detection job runs in a background thread and publishes progress into a shared
``JobState`` object polled by the API layer.
"""

import csv
import datetime
import io
import os
import time
import threading

import cv2
import numpy as np

from src import visualization as viz
from src.analyzer import SequenceAnalyzer
from src.annotation import (load_annotations, load_background_info,
                            load_gate_info)
from src.confidence_color import ConfidenceColorMapper
from src.config import CONF_THRESHOLD, GATE_MARGIN
from src.detector import ParallelDetector
from src.device import resolve_device, tune_threads, use_half
from src.person_filter import select_person_idx
from src.reporter import generate_report
from src.timefmt import format_hms
from src.train_detector import TrainDetector

_MODEL_LOCK = threading.Lock()
_MODEL_CACHE = {"key": None, "model": None, "device": None}


def get_model(model_path, device="auto", half=True):
    """Load (and cache) the YOLO model on the best device. Thread-safe.

    On Apple Silicon this resolves to ``mps``; the model is moved to the
    device once, FP16 is applied when supported, and a tiny warmup inference
    triggers graph compilation so the first real frame is fast.
    """
    dev = resolve_device(device)
    hf = use_half(dev, half)
    cache_key = (model_path, dev, hf)

    with _MODEL_LOCK:
        if _MODEL_CACHE["key"] == cache_key and _MODEL_CACHE["model"] is not None:
            return _MODEL_CACHE["model"]

        from ultralytics import YOLO
        tune_threads()
        model = YOLO(model_path)
        model.to(dev)
        quant = 16 if hf else None
        try:
            dummy = np.zeros((128, 128, 3), dtype=np.uint8)
            model(dummy, verbose=False, imgsz=64, quantize=quant, device=dev)
        except Exception:
            pass
        _MODEL_CACHE["key"] = cache_key
        _MODEL_CACHE["model"] = model
        _MODEL_CACHE["device"] = dev
        return model


class JobState:
    """Thread-safe progress/result container for a detection job."""

    def __init__(self, job_id):
        self.job_id = job_id
        self._lock = threading.Lock()
        self.status = "running"          # idle | running | done | error | stopped
        self.progress = 0.0
        self.current_frame = 0
        self.total_frames = 0
        self.video_fps = 0.0
        self.process_fps = 0.0
        self.message = ""
        self.error = None
        self.stop_requested = False
        self.result = None

    def snapshot(self):
        with self._lock:
            return {
                "job_id": self.job_id,
                "status": self.status,
                "progress": self.progress,
                "current_frame": self.current_frame,
                "total_frames": self.total_frames,
                "video_fps": self.video_fps,
                "process_fps": round(self.process_fps, 1),
                "message": self.message,
                "error": self.error,
                "result": self.result,
            }

    def update(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, v)

    def request_stop(self):
        with self._lock:
            self.stop_requested = True

    def should_stop(self):
        with self._lock:
            return self.stop_requested


def _fmt_timestamp(seconds):
    """MM:SS.s display string (per PRD 4.6.2)."""
    if seconds is None:
        return "—"
    seconds = max(0.0, float(seconds))
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m:02d}:{s:04.1f}"


def _serialize_actions(analysis, fps):
    """Convert SequenceAnalyzer action results to JSON-safe dicts."""
    out = []
    for a in analysis["actions"]:
        item = {
            "action": a.get("action"),
            "rule": a.get("rule"),
            "occurrence": a.get("occurrence", 1),
            "found": bool(a.get("found")),
        }
        if a.get("found"):
            item.update({
                "frame": a.get("frame"),
                "timestamp": round(a.get("timestamp", 0), 2),
                "display": _fmt_timestamp(a.get("timestamp")),
                "side": a.get("side"),
                "angle": round(a["angle"], 1) if a.get("angle") is not None else None,
                "conf": a.get("conf"),
                "hit_rate": a.get("hit_rate"),
                "margin": a.get("margin"),
            })
        out.append(item)
    return out


class DetectionJob:
    """Runs one detection pass over a video and writes output video + CSV."""

    def __init__(self, state, *, model_path, video_path, annotations_file,
                 station_name, rules, action_mapping, detection_kwargs,
                 params):
        self.state = state
        self.model_path = model_path
        self.video_path = video_path
        self.annotations_file = annotations_file
        self.station_name = station_name
        self.rules = rules
        self.action_mapping = action_mapping
        self.detection_kwargs = detection_kwargs
        self.params = params

        self.model = None
        self.detector = None
        self.train_detector = None
        self.cap = None
        self.out = None
        self._output_video_path = None
        self._report_path = None
        self._stop_blocks = []
        self._track_roi_name = None
        self._jump_scan_active = False
        self._confirm_low_count = 0
        self._train_events = []          # collected (frame, ts, type)
        self.device = resolve_device(params.get("device", "auto"))
        self.half = use_half(self.device, params.get("half", True))
        self.train_only = bool(params.get("train_only", False))
        # Gate line (driver-side person filter, optional — enabled by the JSON
        # `gate_line` key). inside_side is +1/-1.
        self.gate_pts, self.gate_side = load_gate_info(annotations_file)
        self._last_selected_idx = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self):
        try:
            self._run()
        except Exception as exc:  # noqa: BLE001 — surface any error to the UI
            import traceback
            traceback.print_exc()
            self.state.update(status="error", error=str(exc), message=f"检测失败: {exc}")
        finally:
            self._cleanup()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self):
        if not os.path.exists(self.video_path):
            raise FileNotFoundError(f"未找到视频文件: {self.video_path}")

        if not self.train_only:
            if not os.path.exists(self.model_path):
                raise FileNotFoundError(f"未找到模型文件: {self.model_path}（请将 yolo26x-pose.pt 放入 models/ 目录）")
            self.state.update(message="加载模型…")
            self.model = get_model(self.model_path, device=self.device, half=self.half)

        regions, lines = load_annotations(self.annotations_file)
        if not self.train_only:
            if not self.rules:
                raise ValueError("该站点未配置检测规则")
            if not regions and not lines:
                raise ValueError("该站点缺少标注数据（regions/lines 为空），请先在标注工具中完成标注")

        self.detector = ParallelDetector(
            self.rules, regions, lines,
            hold_frames=self.params.get("hold_frames", 20),
            frame_decay=self.params.get("frame_decay", 2),
            cooldown_frames=self.params.get("cooldown_frames", 90),
            detection_kwargs=self.detection_kwargs,
        )

        self.conf_mapper = ConfidenceColorMapper(
            low_threshold=self.params.get("conf_low_threshold", 0.3),
            mid_threshold=self.params.get("conf_mid_threshold", 0.6))

        # Train detector (optional for full mode, required for train-only)
        bg_path, track_name = load_background_info(self.annotations_file)
        self._track_roi_name = track_name
        if self._track_roi_name is None:
            for r in self.detector.regions:
                if r["name"] == "track":
                    self._track_roi_name = "track"
                    break
        if bg_path and self._track_roi_name and os.path.exists(bg_path):
            roi = self._lookup_roi(self._track_roi_name)
            if roi is not None:
                self.train_detector = TrainDetector(
                    bg_path, roi, fps=1.0,
                    high_threshold=self.params.get("train_mad_threshold", 20))
                self.detector.enabled = False
        if self.train_only and self.train_detector is None:
            raise ValueError("仅列车检测模式需要该站点配置背景图与轨道ROI，请先在标注工具中完成标注（含背景图）")

        self._open_video()
        idle_jump = self.params.get("idle_jump_seconds", 0)
        self._jump_scan_active = idle_jump > 0

        t_start = time.time()
        frames_done = 0

        while True:
            if self.state.should_stop():
                self.state.update(status="stopped", message="已停止检测")
                break

            ret, frame = self.cap.read()
            if not ret:
                break
            cur_frame = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))

            if self.train_only:
                self._process_train_only_frame(frame, cur_frame, idle_jump)
            elif self.train_detector is not None and self.train_detector.state == "AWAY":
                if idle_jump > 0 and self._jump_scan_active:
                    self._process_jump_scan(frame, cur_frame, idle_jump)
                else:
                    self._process_idle_frame(frame, cur_frame, idle_jump)
            else:
                self._process_detect_frame(frame, cur_frame)
                frames_done += 1

            elapsed = time.time() - t_start
            if elapsed > 0 and cur_frame > 0:
                self.state.update(process_fps=cur_frame / elapsed)
            self.state.update(
                current_frame=cur_frame,
                total_frames=self.total_frames,
                video_fps=self.fps,
                progress=(cur_frame / self.total_frames) if self.total_frames else 0.0,
            )

        if self.state.status == "running":
            self._finish()

    # ------------------------------------------------------------------
    # Video I/O
    # ------------------------------------------------------------------

    def _open_video(self):
        self.cap = cv2.VideoCapture(self.video_path)
        if not self.cap.isOpened():
            raise RuntimeError(f"无法打开视频: {self.video_path}")
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 25.0
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if self.train_detector is not None:
            self.train_detector.fps = self.fps

        out_dir = self.params.get("output_dir")
        os.makedirs(out_dir, exist_ok=True)
        video_dir = os.path.join(out_dir, "video")
        report_dir = os.path.join(out_dir, "report")
        os.makedirs(video_dir, exist_ok=True)
        os.makedirs(report_dir, exist_ok=True)

        video_base = os.path.splitext(os.path.basename(self.video_path))[0]
        out_name = f"pose_output_{video_base}.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self._output_video_path = os.path.join(video_dir, out_name)
        self.out = cv2.VideoWriter(self._output_video_path, fourcc, self.fps,
                                   (self.width, self.height))
        self._report_path = os.path.join(report_dir, f"report_{video_base}.csv")

    # ------------------------------------------------------------------
    # Per-frame processing (mirrors src/player.py, headless)
    # ------------------------------------------------------------------

    def _process_detect_frame(self, frame, cur_frame):
        model_conf = self.params.get("model_conf", 0.5)
        imgsz = self.params.get("imgsz", 640)
        quant = 16 if self.half else None
        results = self.model(frame, verbose=False, conf=model_conf,
                             imgsz=imgsz, quantize=quant, device=self.device)
        kp = results[0].keypoints if (results and results[0].keypoints is not None) else None
        kp = self._apply_person_filter(kp, results)

        active, _new_events = self.detector.update(kp, cur_frame)

        metrics = viz.compute_action_metrics(
            kp, self.action_mapping, self.detector.rules,
            self.detector.regions, self.detector.lines,
            self.detector.detection_kwargs)

        annotated = viz.draw_pose(frame, results, self.conf_mapper)
        viz.draw_arm_rays(annotated, kp, self.detector.regions, self.conf_mapper)
        viz.draw_annotations(annotated, self.detector.regions, self.detector.lines,
                             self._track_roi_name, gate=self._gate_dict())
        viz.draw_confidence_legend(annotated, self.conf_mapper)

        if self.train_detector is not None:
            prev_state = self.train_detector.state
            train_state, train_mad = self.train_detector.update(frame, cur_frame)
            if prev_state != "PRESENT" and train_state == "PRESENT":
                self.detector.enable()
            elif prev_state == "PRESENT" and train_state != "PRESENT":
                self.detector.enabled = False
                self._settle_stop()
                self._jump_scan_active = self.params.get("idle_jump_seconds", 0) > 0
                self._confirm_low_count = 0
            td = self.train_detector
            viz.draw_train_status(annotated, train_state, train_mad,
                                  hold_counter=td.hold_counter,
                                  hold_target=td.hold_target)

        annotated, status_bottom = viz.draw_status_overlay(
            annotated, self.detector.rules, active,
            self.detector.events, self.action_mapping)
        annotated = viz.draw_action_metrics(
            annotated, metrics, y=status_bottom + 6,
            show_arm_bend=False)
        viz.draw_frame_info(annotated, cur_frame, self.total_frames, self.fps)

        self.out.write(annotated)

    def _process_idle_frame(self, frame, cur_frame, idle_jump):
        prev_state = self.train_detector.state
        train_state, train_mad = self.train_detector.update(frame, cur_frame)
        if prev_state != "PRESENT" and train_state == "PRESENT":
            self.detector.enable()
        elif idle_jump > 0 and train_state == "AWAY":
            if train_mad > self.train_detector.high_threshold:
                self._confirm_low_count = 0
            else:
                self._confirm_low_count += 1
                if self._confirm_low_count >= int(self.fps * 2):
                    self._jump_scan_active = True
                    self._confirm_low_count = 0
        td = self.train_detector
        viz.draw_train_status(frame, train_state, train_mad,
                              hold_counter=td.hold_counter,
                              hold_target=td.hold_target)
        self.out.write(frame)

    def _process_jump_scan(self, frame, cur_frame, idle_jump):
        td = self.train_detector
        jump = max(1, int(self.fps * idle_jump))
        mad = td.measure(frame)
        td.frame_num = cur_frame
        td.mad = mad

        if mad > td.high_threshold:
            back = max(0, cur_frame - 1 - jump)
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, back)
            td.hold_counter = 0
            self._jump_scan_active = False
            self._confirm_low_count = 0
            return

        ts = cur_frame / self.fps if self.fps else 0
        label = f">>> FAST-SCAN  MAD={mad:.1f}  @ {int(ts // 60):02d}:{ts % 60:04.1f}"
        cv2.putText(frame, label, (10, self.height - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        viz.draw_train_status(frame, td.state, mad,
                              hold_counter=td.hold_counter,
                              hold_target=td.hold_target)
        self.out.write(frame)

        target = min(cur_frame - 1 + jump, self.total_frames - 1)
        if target > cur_frame - 1:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, target)

    def _process_train_only_frame(self, frame, cur_frame, idle_jump):
        """Train-only mode: MAD detection per frame, no YOLO/pose/action rules."""
        td = self.train_detector
        if idle_jump > 0 and self._jump_scan_active and td.state == "AWAY":
            self._process_jump_scan(frame, cur_frame, idle_jump)
            return

        prev_state = td.state
        train_state, train_mad = td.update(frame, cur_frame)
        if prev_state == "PRESENT" and train_state != "PRESENT":
            self._settle_stop()
            self._jump_scan_active = idle_jump > 0
            self._confirm_low_count = 0
        elif idle_jump > 0 and train_state == "AWAY":
            if train_mad > td.high_threshold:
                self._confirm_low_count = 0
            else:
                self._confirm_low_count += 1
                if self._confirm_low_count >= int(self.fps * 2):
                    self._jump_scan_active = True
                    self._confirm_low_count = 0

        viz.draw_train_status(frame, train_state, train_mad,
                              hold_counter=td.hold_counter,
                              hold_target=td.hold_target)
        self.out.write(frame)

    # ------------------------------------------------------------------
    # Analysis / report
    # ------------------------------------------------------------------

    def _finish(self):
        td = self.train_detector
        if self.detector.events or (td is not None and td.state == "PRESENT"):
            self._settle_stop()
        if td is None and not self._stop_blocks:
            self._settle_stop()

        if td is not None:
            for f, ts, etype in td.events:
                self._train_events.append((f, ts, etype))
            if td.state == "PRESENT" and td.events and td.events[-1][2] == "arrived":
                end = self.total_frames / self.fps if self.fps else 0
                self._train_events.append(("", end, "departed_open"))

        self.out.release()
        self.out = None

        if self.train_only:
            self._write_train_only_report()
        else:
            script_name = "web"
            generate_report(
                self._report_path,
                station_name=self.station_name,
                script_name=script_name,
                video_path=self.video_path,
                output_video_path=self._output_video_path,
                model_path=self.model_path,
                imgsz=self.params.get("imgsz", 640),
                model_conf=self.params.get("model_conf", 0.5),
                stops=self._stop_blocks,
                action_mapping=self.action_mapping,
                detection_kwargs=self.detection_kwargs,
                rules=self.rules,
            )

        self.state.update(
            status="done",
            progress=1.0,
            message="检测完成",
            result=self._build_result(),
        )

    def _write_train_only_report(self):
        """CSV report for train-only mode: arrival/departure per stop."""
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["列车进出站检测报告（仅列车检测）"])
        w.writerow([])
        w.writerow(["基本信息"])
        w.writerow(["站点名称", self.station_name])
        w.writerow(["检测日期", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        w.writerow(["视频文件", self.video_path])
        w.writerow(["列车趟数", str(len(self._stop_blocks))])
        w.writerow([])
        if not self._stop_blocks:
            w.writerow(["检测结果", "未检测到列车进出站事件"])
        for i, stop in enumerate(self._stop_blocks, 1):
            w.writerow([f"====== 第{i}趟列车 ======"])
            w.writerow(["列车到站", stop.get("arrive") or "—"])
            w.writerow(["列车离站", stop.get("depart") or "—"])
            w.writerow(["停靠时长", stop.get("duration") or "—"])
            w.writerow([])
        with open(self._report_path, "w", encoding="utf-8-sig", newline="") as f:
            f.write(buf.getvalue())

    def _build_result(self):
        video_name = os.path.basename(self.video_path)
        duration = self.total_frames / self.fps if self.fps else 0
        train_events = self._build_train_events()

        stops = []
        for i, block in enumerate(self._stop_blocks, 1):
            stops.append({
                "index": i,
                "arrive": block.get("arrive"),
                "depart": block.get("depart"),
                "duration": block.get("duration"),
                "actions": block.get("action_results_serialized", []),
                "evaluation": block.get("evaluation", {}),
            })

        return {
            "video_info": {
                "name": video_name,
                "duration": round(duration, 2),
                "resolution": f"{self.width}x{self.height}",
                "fps": round(self.fps, 2),
                "total_frames": self.total_frames,
            },
            "engine": {
                "device": self.device,
                "half": self.half,
            },
            "mode": "train_only" if self.train_only else "full",
            "train_events": train_events,
            "stops": stops,
            "output": {
                "detected_video": self._output_video_path,
                "csv_report": self._report_path,
                "detected_video_name": os.path.basename(self._output_video_path),
                "csv_report_name": os.path.basename(self._report_path),
            },
        }

    def _build_train_events(self):
        """Pair arrival/departure events into arrival/dwell/departure entries."""
        events = []
        arrivals = [(f, ts) for f, ts, e in self._train_events
                    if e == "arrived"]
        departs = [(f, ts) for f, ts, e in self._train_events
                   if e in ("departed", "departed_open")]

        # Merge into chronological arrival/departure pairs
        idx = 0
        while idx < len(arrivals):
            arr_frame, arr_ts = arrivals[idx]
            dep = departs[idx] if idx < len(departs) else None
            events.append({
                "type": "arrival",
                "label": "列车进站",
                "timestamp": round(arr_ts, 1),
                "display": _fmt_timestamp(arr_ts),
                "frame": arr_frame,
            })
            if dep:
                dep_frame, dep_ts = dep
                n_actions = 0
                if idx < len(self._stop_blocks):
                    ev = self._stop_blocks[idx].get("evaluation", {})
                    n_actions = ev.get("found", 0)
                dwell = {
                    "type": "dwell",
                    "label": "列车停靠",
                    "start": round(arr_ts, 1),
                    "end": round(dep_ts, 1),
                    "display": f"{_fmt_timestamp(arr_ts)} ~ {_fmt_timestamp(dep_ts)}",
                }
                if not self.train_only:
                    dwell["actions_summary"] = f"检测到 {n_actions} 个标准动作"
                events.append(dwell)
                events.append({
                    "type": "departure",
                    "label": "列车离站",
                    "timestamp": round(dep_ts, 1),
                    "display": _fmt_timestamp(dep_ts),
                    "frame": dep_frame,
                })
            idx += 1
        return events

    def _settle_stop(self):
        if self.train_only:
            analysis = {"actions": [], "order_valid": True,
                        "all_found": True, "total_events": 0}
        else:
            analyzer = SequenceAnalyzer(
                self.detector.events, self.action_mapping, fps=self.fps)
            analysis = analyzer.analyze()

        arrive = depart = duration = None
        if self.train_detector is not None:
            arrives = [ts for _f, ts, e in self.train_detector.events
                       if e == "arrived"]
            departs = [ts for _f, ts, e in self.train_detector.events
                       if e == "departed"]
            if arrives:
                arrive = format_hms(arrives[-1])
            if departs and (not arrives or departs[-1] >= arrives[-1]):
                depart = format_hms(departs[-1])
                if arrives:
                    duration = format_hms(departs[-1] - arrives[-1])

        total_expected = len(self.action_mapping)
        total_found = sum(1 for r in analysis["actions"] if r.get("found"))

        self._stop_blocks.append({
            "arrive": arrive,
            "depart": depart,
            "duration": duration,
            "action_results": analysis["actions"],
            "action_results_serialized": _serialize_actions(analysis, self.fps),
            "evaluation": {
                "expected": total_expected,
                "found": total_found,
                "order_valid": analysis["order_valid"],
                "all_found": analysis["all_found"],
                "compliant": bool(analysis["all_found"] and analysis["order_valid"]),
            },
        })
        self.detector.events.clear()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _lookup_roi(self, name):
        for r in self.detector.regions:
            if r["name"] == name:
                return r["xywh"]
        return None

    def _apply_person_filter(self, kp, results):
        """Filter keypoints+boxes to the driver-side person (single index).

        With a gate line configured, only persons with a body anchor on the
        driver side are kept; when nobody qualifies all persons are dropped so
        detection reports nothing. Without a gate line, falls back to the
        highest-confidence bbox (original behaviour). The selected index is
        used for BOTH keypoints and boxes so they stay in sync.
        """
        idx = self._select_person_idx(results)
        if kp is None or results is None:
            return kp
        if idx is not None:
            kp = kp[[idx]]
            results[0].keypoints = kp
            if results[0].boxes is not None and len(results[0].boxes) > 1:
                results[0].boxes = results[0].boxes[[idx]]
        elif self.gate_pts is not None:
            # Gate active but nobody qualifies — drop all persons
            kp = kp[0:0]
            results[0].keypoints = kp
            if results[0].boxes is not None:
                results[0].boxes = results[0].boxes[0:0]
        return kp

    def _select_person_idx(self, results):
        """Choose the driver person index via the gate line, or None to drop all."""
        if results is None or results[0].boxes is None or results[0].keypoints is None:
            self._last_selected_idx = None
            return None
        boxes = results[0].boxes
        if len(boxes) == 0:
            self._last_selected_idx = None
            return None
        kp = results[0].keypoints
        idx = select_person_idx(
            boxes.xyxy.cpu().numpy(), boxes.conf.cpu().numpy(),
            kp.xy.cpu().numpy(), kp.conf.cpu().numpy(),
            self.gate_pts, self.gate_side,
            margin=GATE_MARGIN, conf_threshold=CONF_THRESHOLD,
            last_idx=self._last_selected_idx,
        )
        self._last_selected_idx = idx
        return idx

    def _gate_dict(self):
        """Gate annotation dict for rendering, or None when not configured."""
        if self.gate_pts is None:
            return None
        return {"pts": self.gate_pts, "inside_side": self.gate_side}

    def _cleanup(self):
        if self.out is not None:
            self.out.release()
        if self.cap is not None:
            self.cap.release()


# ---------------------------------------------------------------------------
# Task manager
# ---------------------------------------------------------------------------

class TaskManager:
    """Holds the single active detection job (one at a time)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._job = None
        self._thread = None
        self._counter = 0

    def start(self, *, model_path, video_path, annotations_file, station_name,
              rules, action_mapping, detection_kwargs, params):
        with self._lock:
            if self._job is not None and self._job.status == "running":
                raise RuntimeError("已有检测任务正在运行，请先停止")
            self._counter += 1
            job_id = f"task_{self._counter}"
            state = JobState(job_id)
            job = DetectionJob(
                state,
                model_path=model_path,
                video_path=video_path,
                annotations_file=annotations_file,
                station_name=station_name,
                rules=rules,
                action_mapping=action_mapping,
                detection_kwargs=detection_kwargs,
                params=params,
            )
            self._job = state
            self._thread = threading.Thread(
                target=job.run, name=f"detect-{job_id}", daemon=True)
            self._thread.start()
            return job_id

    def status(self):
        with self._lock:
            if self._job is None:
                return {"status": "idle"}
            return self._job.snapshot()

    def stop(self):
        with self._lock:
            if self._job is None:
                return False
            self._job.request_stop()
            return True

    def result(self):
        with self._lock:
            if self._job is None or self._job.status != "done":
                return None
            return self._job.result


TASK_MANAGER = TaskManager()
