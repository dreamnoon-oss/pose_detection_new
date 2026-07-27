#!/usr/bin/env python3
"""
视频脱敏工具：人脸自动马赛克 + 鼠标框选固定区域马赛克。

功能：
  1. 基于 YOLO pose 关键点自动检测人脸并打码
  2. 手动框选固定区域打码
  3. IoU 多目标跟踪 + 指数平滑，减少人脸框闪烁
  4. 支持 GPU 加速、帧范围选择、预览模式

用法：
  方式一（直接改上方配置）：
      修改 VIDEO_PATH / OUTPUT_PATH → 直接运行 python video_anonymize.py
  方式二（命令行传参，会覆盖配置）：
      python video_anonymize.py -i video.mp4
      python video_anonymize.py -i video.mp4 -o output.mp4 --device cuda:0
      python video_anonymize.py -i video.mp4 --preview --mosaic-blocks 8
      python video_anonymize.py -i video.mp4 --no-fix-roi --track-smooth 0.5
      python video_anonymize.py -i video.mp4 --start-frame 100 --end-frame 500 --merge-audio
"""

import argparse
import os
import subprocess
import sys
import time

import cv2
import numpy as np
from ultralytics import YOLO

# ---------------------------------------------------------------------------
# 配置（可直接修改此处，也支持命令行 -i/-o 覆盖）
# ---------------------------------------------------------------------------
VIDEO_PATH = r"\\10.151.2.205\共享文件2\司机行为规范样本采样\短视频\宝山4.mp4"
OUTPUT_PATH = r"D:\pycharm_pytorch\ultralytics-8.4.75\pose_detection\output\processed"          # 留空 = 自动在输入同目录生成 _masked.mp4

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = os.path.join(os.path.dirname(_SCRIPT_DIR), "models", "yolo26x-pose.pt")

DISPLAY_MAX_W = 1600       # 框选窗口最大宽度
FACE_KP_IDS = [0, 1, 2, 3, 4]  # 鼻, 左眼, 右眼, 左耳, 右耳


# ---------------------------------------------------------------------------
# 马赛克
# ---------------------------------------------------------------------------
def mosaic_region(frame, x1, y1, x2, y2, blocks=12):
    """对 frame 的 [y1:y2, x1:x2] 区域打马赛克（原地修改）。"""
    h, w = frame.shape[:2]
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(w, int(x2)), min(h, int(y2))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return
    roi = frame[y1:y2, x1:x2]
    rh, rw = roi.shape[:2]
    block = max(2, min(rh, rw) // blocks)
    small = cv2.resize(roi, (max(1, rw // block), max(1, rh // block)),
                       interpolation=cv2.INTER_LINEAR)
    frame[y1:y2, x1:x2] = cv2.resize(small, (rw, rh),
                                     interpolation=cv2.INTER_NEAREST)


# ---------------------------------------------------------------------------
# IoU 工具
# ---------------------------------------------------------------------------
def box_iou(a, b):
    """两个 [x1,y1,x2,y2] 框的 IoU。"""
    x1 = max(a[0], b[0]); y1 = max(a[1], b[1])
    x2 = min(a[2], b[2]); y2 = min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter + 1e-8)


# ---------------------------------------------------------------------------
# 人脸跟踪器（IoU 匹配 + 指数平滑）
# ---------------------------------------------------------------------------
class FaceTracker:
    """
    轻量多目标跟踪器：每帧用 IoU 将检测框与已有轨迹匹配，
    未匹配的检测创建新轨迹，未匹配的轨迹保留 max_lost 帧后移除。
    返回的框经过指数平滑，减少帧间抖动。
    """

    def __init__(self, max_lost=30, iou_thresh=0.3, smooth=0.6):
        self.tracks = []       # [{id, box, smooth_box, lost}]
        self.next_id = 0
        self.max_lost = max_lost
        self.iou_thresh = iou_thresh
        self.smooth = smooth

    def update(self, detections):
        if not self.tracks and not detections:
            return []

        # 全是新检测
        if not self.tracks:
            for d in detections:
                self.tracks.append({
                    "id": self.next_id, "box": d, "smooth_box": d, "lost": 0,
                })
                self.next_id += 1
            return [t["smooth_box"] for t in self.tracks]

        # 无新检测，所有轨迹丢失计数 +1
        if not detections:
            for t in self.tracks:
                t["lost"] += 1
            self.tracks = [t for t in self.tracks if t["lost"] <= self.max_lost]
            return [t["smooth_box"] for t in self.tracks]

        # 构造 IoU 矩阵，贪心匹配
        n_t, n_d = len(self.tracks), len(detections)
        pairs = []
        for ti in range(n_t):
            for di in range(n_d):
                iou = box_iou(self.tracks[ti]["box"], detections[di])
                if iou >= self.iou_thresh:
                    pairs.append((iou, ti, di))
        pairs.sort(key=lambda x: x[0], reverse=True)

        matched_t = set()
        matched_d = set()
        for _iou, ti, di in pairs:
            if ti not in matched_t and di not in matched_d:
                matched_t.add(ti)
                matched_d.add(di)
                prev = self.tracks[ti]["smooth_box"]
                det = detections[di]
                s = self.smooth
                self.tracks[ti]["smooth_box"] = (
                    s * prev[0] + (1 - s) * det[0],
                    s * prev[1] + (1 - s) * det[1],
                    s * prev[2] + (1 - s) * det[2],
                    s * prev[3] + (1 - s) * det[3],
                )
                self.tracks[ti]["box"] = det
                self.tracks[ti]["lost"] = 0

        # 未匹配轨迹丢失 +1
        for ti in range(n_t):
            if ti not in matched_t:
                self.tracks[ti]["lost"] += 1

        # 未匹配检测新建轨迹
        for di in range(n_d):
            if di not in matched_d:
                d = detections[di]
                self.tracks.append({
                    "id": self.next_id, "box": d, "smooth_box": d, "lost": 0,
                })
                self.next_id += 1

        # 移除长期丢失的轨迹
        self.tracks = [t for t in self.tracks if t["lost"] <= self.max_lost]

        return [t["smooth_box"] for t in self.tracks]


# ---------------------------------------------------------------------------
# 人脸定位（基于姿态关键点）
# ---------------------------------------------------------------------------
def face_boxes_from_pose(result, kp_conf=0.25, face_expand=2.4, min_face_size=24):
    """
    从一帧的 YOLO pose 结果中估算所有人脸框。
    优先用 5 个面部关键点；面部点全丢时用双肩兜底（背面人）。
    """
    boxes = []
    kps = result.keypoints
    if kps is None or kps.xy is None or len(kps) == 0:
        return boxes

    person_boxes = None
    if result.boxes is not None and len(result.boxes) == len(kps):
        person_boxes = result.boxes.xyxy.cpu().numpy()

    for i in range(len(kps)):
        xy = kps.xy[i].cpu().numpy()
        conf = kps.conf[i].cpu().numpy() if kps.conf is not None else None
        if conf is None:
            continue

        pts = [(float(xy[j][0]), float(xy[j][1]))
               for j in FACE_KP_IDS if conf[j] > kp_conf]

        box_h = 0.0
        if person_boxes is not None:
            _, by1, _, by2 = person_boxes[i]
            box_h = float(by2 - by1)

        if pts:
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
            span = max(max(xs) - min(xs), max(ys) - min(ys))
            side = max(span * face_expand, box_h * 0.22, min_face_size)
            boxes.append((cx - side / 2, cy - side * 0.65,
                          cx + side / 2, cy + side * 0.65))
        elif conf[5] > kp_conf and conf[6] > kp_conf:
            # 背面兜底：用双肩估算头部位置
            lx, ly = float(xy[5][0]), float(xy[5][1])
            rx, ry = float(xy[6][0]), float(xy[6][1])
            cx, sy = (lx + rx) / 2, (ly + ry) / 2
            sw = max(abs(rx - lx), min_face_size)
            boxes.append((cx - sw * 0.55, sy - sw * 1.4,
                          cx + sw * 0.55, sy - sw * 0.1))

    return boxes


# ---------------------------------------------------------------------------
# 固定区域框选
# ---------------------------------------------------------------------------
def select_fixed_regions(video_path, roi_frame=0):
    """弹窗让用户用鼠标框选固定打码区域，返回 [(x1,y1,x2,y2), ...]（原始分辨率）。"""
    cap = cv2.VideoCapture(video_path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, roi_frame)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"无法读取视频帧: {video_path}")

    h, w = frame.shape[:2]
    scale = min(1.0, DISPLAY_MAX_W / w)
    disp = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1.0 else frame

    print("=" * 60)
    print("固定区域框选：拖拽画框 -> SPACE/ENTER 确认 -> 可框多个 -> ESC 结束")
    print("（不需要固定区域就直接按 ESC）")
    print("=" * 60)

    win = "Select Regions (SPACE=confirm, ESC=done)"
    rois = cv2.selectROIs(win, disp, showCrosshair=True, fromCenter=False)
    cv2.destroyWindow(win)

    regions = []
    for (x, y, rw, rh) in rois:
        if rw > 2 and rh > 2:
            regions.append((int(x / scale), int(y / scale),
                            int((x + rw) / scale), int((y + rh) / scale)))
    print(f"已选择 {len(regions)} 个固定区域")
    return regions


# ---------------------------------------------------------------------------
# 进度显示
# ---------------------------------------------------------------------------
def _fmt_time(seconds):
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m{int(seconds % 60)}s"
    return f"{int(seconds // 3600)}h{int(seconds % 3600 // 60)}m"


def print_progress(current, total, t0):
    elapsed = time.time() - t0
    fps = current / elapsed if elapsed > 0 else 0
    pct = current * 100 // max(total, 1)
    eta = (total - current) / fps if fps > 0 else 0
    bar_len = 30
    filled = int(bar_len * current / max(total, 1))
    bar = "#" * filled + "-" * (bar_len - filled)
    print(f"\r[{bar}] {pct:3d}%  {current}/{total}  "
          f"{fps:.1f} fps  ETA {_fmt_time(eta)}  ", end="", flush=True)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="视频脱敏工具：人脸自动马赛克 + 手动框选")

    parser.add_argument("-i", "--input", default=None,
                        help="输入视频路径（不填则使用上方 VIDEO_PATH）")
    parser.add_argument("-o", "--output", default=None,
                        help="输出路径（不填则使用上方 OUTPUT_PATH，都为空则自动生成 _masked.mp4）")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL,
                        help="YOLO pose 模型路径")
    parser.add_argument("--device", default=None,
                        help="推理设备，如 cuda:0 / cpu")
    parser.add_argument("--imgsz", type=int, default=640,
                        help="推理分辨率 (默认 640)")
    parser.add_argument("--kp-conf", type=float, default=0.25,
                        help="人脸关键点置信度阈值 (默认 0.25)")
    parser.add_argument("--face-expand", type=float, default=2.4,
                        help="人脸框放大倍数 (默认 2.4)")
    parser.add_argument("--min-face-size", type=int, default=24,
                        help="人脸框最小边长像素 (默认 24)")
    parser.add_argument("--mosaic-blocks", type=int, default=12,
                        help="马赛克粒度，越小格子越大 (默认 12)")
    parser.add_argument("--track-max-lost", type=int, default=30,
                        help="跟踪丢失后保留的最大帧数 (默认 30)")
    parser.add_argument("--track-iou", type=float, default=0.3,
                        help="跟踪 IoU 匹配阈值 (默认 0.3)")
    parser.add_argument("--track-smooth", type=float, default=0.6,
                        help="平滑系数，0=不平滑 1=完全不动 (默认 0.6)")
    parser.add_argument("--roi-frame", type=int, default=0,
                        help="固定区域框选用第几帧 (默认 0)")
    parser.add_argument("--no-fix-roi", action="store_true",
                        help="跳过手动框选固定区域")
    parser.add_argument("--no-face", action="store_true",
                        help="跳过人脸自动检测")
    parser.add_argument("--start-frame", type=int, default=0,
                        help="起始帧 (默认 0)")
    parser.add_argument("--end-frame", type=int, default=None,
                        help="结束帧 (默认到视频末尾)")
    parser.add_argument("--preview", action="store_true",
                        help="预览模式：显示检测框绿/红框，不输出视频，按 Q 退出")
    parser.add_argument("--merge-audio", action="store_true",
                        help="处理完后自动用 ffmpeg 合并原音频")

    args = parser.parse_args()

    # ---- 输入路径：命令行优先，否则用配置 ----
    video_path = args.input or VIDEO_PATH
    if not video_path:
        print("[错误] 未指定输入视频，请修改 VIDEO_PATH 或用 -i 指定")
        sys.exit(1)
    if not os.path.exists(video_path):
        print(f"[错误] 视频不存在: {video_path}")
        sys.exit(1)
    if not os.path.exists(args.model):
        print(f"[错误] 模型不存在: {args.model}")
        sys.exit(1)

    # ---- 输出路径 ----
    out_path = args.output or OUTPUT_PATH
    if not out_path:
        base = os.path.splitext(os.path.basename(video_path))[0]
        in_dir = os.path.dirname(video_path) or "."
        out_path = os.path.join(in_dir, base + "_masked.mp4")
    elif os.path.isdir(out_path):
        base = os.path.splitext(os.path.basename(video_path))[0]
        out_path = os.path.join(out_path, base + "_masked.mp4")
    if not args.preview:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    # ---- 固定区域框选 ----
    fixed_regions = []
    if not args.no_fix_roi:
        fixed_regions = select_fixed_regions(video_path, roi_frame=args.roi_frame)

    # ---- 加载模型 ----
    print(f"加载模型: {args.model}")
    model = YOLO(args.model)
    if args.device:
        model.to(args.device)

    # ---- 打开视频 ----
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    raw_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    start_f = max(0, args.start_frame)
    end_f = args.end_frame if args.end_frame else raw_total
    total = min(end_f, raw_total) - start_f

    if start_f > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)

    if not args.preview:
        out = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))

    tracker = FaceTracker(
        max_lost=args.track_max_lost,
        iou_thresh=args.track_iou,
        smooth=args.track_smooth,
    )

    frame_idx = 0
    t0 = time.time()

    print(f"\n输入: {video_path}")
    print(f"分辨率: {w}x{h}  FPS: {fps:.1f}  帧范围: {start_f}-{end_f} (共 {total})")
    print(f"人脸检测: {'关闭' if args.no_face else '开启'}  "
          f"固定区域: {len(fixed_regions)} 个")
    print(f"预览模式: {'是' if args.preview else '否'}\n")

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame_idx += 1
        if frame_idx > total:
            break

        # 人脸检测 -> 跟踪 -> 平滑
        face_boxes = []
        if not args.no_face:
            results = model(frame, imgsz=args.imgsz, verbose=False)
            detections = face_boxes_from_pose(
                results[0], kp_conf=args.kp_conf,
                face_expand=args.face_expand, min_face_size=args.min_face_size,
            )
            face_boxes = tracker.update(detections)

        # 打码
        for box in face_boxes:
            mosaic_region(frame, *box, blocks=args.mosaic_blocks)
        for reg in fixed_regions:
            mosaic_region(frame, *reg, blocks=args.mosaic_blocks)

        if args.preview:
            for box in face_boxes:
                x1, y1, x2, y2 = [int(v) for v in box]
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            for reg in fixed_regions:
                cv2.rectangle(frame, (reg[0], reg[1]), (reg[2], reg[3]),
                              (0, 0, 255), 2)
            cv2.imshow("Preview (Q=quit)", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        else:
            out.write(frame)

        if frame_idx % 50 == 0 or frame_idx == total:
            print_progress(frame_idx, total, t0)

    # ---- 收尾 ----
    cap.release()
    if not args.preview:
        out.release()
    if args.preview:
        cv2.destroyAllWindows()

    elapsed = time.time() - t0
    print(f"\n\n{'=' * 60}")
    if args.preview:
        print(f"预览结束，耗时 {_fmt_time(elapsed)}")
    else:
        avg_fps = total / elapsed if elapsed > 0 else 0
        print(f"完成！耗时 {_fmt_time(elapsed)}  ({avg_fps:.1f} fps)")
        print(f"输出: {out_path}")

        if args.merge_audio:
            audio_out = os.path.splitext(out_path)[0] + "_audio.mp4"
            print(f"\n正在合并音频...")
            cmd = [
                "ffmpeg", "-y",
                "-i", out_path,
                "-i", video_path,
                "-map", "0:v", "-map", "1:a?",
                "-c:v", "copy", "-c:a", "aac",
                audio_out,
            ]
            try:
                subprocess.run(cmd, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                os.replace(audio_out, out_path)
                print("音频合并完成。")
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                print(f"[警告] 音频合并失败: {e}")
                print(f"  可手动执行: ffmpeg -i \"{out_path}\" -i \"{video_path}\" "
                      f"-map 0:v -map 1:a? -c:v copy -c:a aac \"{audio_out}\"")
        elif not args.no_face or fixed_regions:
            # 只在有实际处理时才提示
            print(f"\n如需保留音频，加 --merge-audio 或在处理后执行:")
            print(f'  ffmpeg -i "{out_path}" -i "{video_path}" '
                  f'-map 0:v -map 1:a? -c:v copy -c:a aac output_audio.mp4')
    print("=" * 60)


if __name__ == "__main__":
    main()
