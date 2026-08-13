"""FastAPI backend for the driver-behaviour analysis web UI.

Serves the static frontend and exposes the REST API defined in the PRD:
video management, detection control, train events, parameter config,
annotation data management, and result downloads. All detection work is
delegated to :mod:`server.engine` (headless reuse of the existing src/ modules).
"""

import base64
import json
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.config import OUTPUT_DIR, DEFAULT_MODEL

from . import stations as st
from .engine import TASK_MANAGER

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
UPLOAD_DIR = os.path.join(OUTPUT_DIR, "uploads")

app = FastAPI(title="地铁司机标准化作业行为智能分析系统", version="1.0.0")

# ---------------------------------------------------------------------------
# Current-video state (single-user local deployment)
# ---------------------------------------------------------------------------

_current_video = {"path": None}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class DetectStartBody(BaseModel):
    line: str
    station: str
    params: dict = {}
    video_path: str | None = None


class AnnotationSaveBody(BaseModel):
    line: str
    station: str
    regions: list = []
    lines: list = []
    track_roi: str | None = None
    video: str = ""
    frame: int = 0
    width: int = 1920
    height: int = 1080
    background: dict | None = None
    background_data: str | None = None


class AnnotationLoadBody(BaseModel):
    line: str
    station: str


class ExtractFrameBody(BaseModel):
    video_path: str
    frame: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _annotation_json(line, station):
    path = st.resolve_annotation_path(line, station)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _detection_kwargs(params):
    return {
        "angle_threshold": params.get("angle_threshold", 40),
        "min_arm_len": params.get("min_arm_len", 30),
        "min_arm_torso_angle": params.get("min_arm_torso_angle", 45),
        "dynamic_angle_coeff": params.get("dynamic_angle_coeff", 0.6),
    }


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ---------------------------------------------------------------------------
# Station management
# ---------------------------------------------------------------------------


@app.get("/api/stations/list")
def stations_list(line: str | None = None):
    lines = st.get_lines()
    if line is not None:
        lines = [ln for ln in lines if ln["name"] == line]
    for ln in lines:
        ln["stations"] = st.get_stations(ln["name"])
    return {"lines": lines}


@app.get("/api/stations/annotation_status")
def annotation_status(line: str | None = None):
    lines = st.get_lines()
    if line is not None:
        lines = [ln for ln in lines if ln["name"] == line]
    total = 0
    annotated = 0
    detail = []
    for ln in lines:
        entries = st.get_stations(ln["name"])
        for e in entries:
            total += 1
            if e["annotated"]:
                annotated += 1
            detail.append({
                "line": ln["name"],
                "station": e["name"],
                "key": e["key"],
                "status": e["status"],
            })
    return {
        "total": total,
        "annotated": annotated,
        "completion": round(annotated / total, 3) if total else 0.0,
        "lines": lines,
        "detail": detail,
    }


@app.get("/api/stations/bindfile")
def stations_bindfile(line: str, station_key: str):
    path = st.resolve_annotation_path(line, station_key)
    if not os.path.exists(path):
        raise HTTPException(404, "该站点无标注文件")
    return {"path": path, "content": _annotation_json(line, station_key)}


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

_current_params = dict(st.DEFAULT_PARAMS)


@app.get("/api/params/get")
def params_get():
    return {"params": dict(_current_params),
            "defaults": dict(st.DEFAULT_PARAMS),
            "model_available": os.path.exists(DEFAULT_MODEL)}


@app.post("/api/params/set")
def params_set(body: dict):
    _current_params.update(body)
    return {"ok": True, "params": dict(_current_params)}


# ---------------------------------------------------------------------------
# Video management
# ---------------------------------------------------------------------------


def _probe_video(path):
    import cv2
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise HTTPException(400, f"无法打开视频: {path}")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return {
        "name": os.path.basename(path),
        "path": path,
        "duration": round(total / fps, 2) if fps else 0,
        "resolution": f"{width}x{height}",
        "width": width,
        "height": height,
        "fps": round(fps, 2),
        "total_frames": total,
    }


@app.post("/api/video/load")
async def video_load(request: Request):
    global _current_video
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        file = form.get("file")
        if file is None:
            raise HTTPException(400, "未收到文件")
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        safe_name = os.path.basename(file.filename or "upload.mp4")
        path = os.path.join(UPLOAD_DIR, safe_name)
        with open(path, "wb") as f:
            f.write(await file.read())
    else:
        body = await request.json()
        path = body.get("path")
        if not path:
            raise HTTPException(400, "请上传视频文件或提供本地路径")

    info = _probe_video(path)
    _current_video["path"] = path
    return {"video": info}


@app.get("/api/video/info")
def video_info():
    if not _current_video["path"]:
        raise HTTPException(404, "尚未加载视频")
    return {"video": _probe_video(_current_video["path"])}


@app.get("/api/video/stream")
def video_stream():
    if not _current_video["path"]:
        raise HTTPException(404, "尚未加载视频")
    return FileResponse(_current_video["path"], media_type="video/mp4")


# ---------------------------------------------------------------------------
# Detection control
# ---------------------------------------------------------------------------


@app.post("/api/detect/start")
def detect_start(body: DetectStartBody):
    global _current_video
    cfg = st.get_config(body.line, body.station)
    if cfg is None:
        raise HTTPException(400, "该站点尚未配置检测规则（需先完成标注与规则配置）")

    video_path = body.video_path or _current_video["path"]
    if not video_path or not os.path.exists(video_path):
        raise HTTPException(400, "请先加载视频文件")

    annotations_file = st.resolve_annotation_path(body.line, body.station)
    if not os.path.exists(annotations_file):
        raise HTTPException(400, "该站点缺少标注文件，请先在标注工具中完成标注")

    params = dict(st.DEFAULT_PARAMS)
    params.update(_current_params)
    params.update(body.params or {})
    params["output_dir"] = OUTPUT_DIR

    job_id = TASK_MANAGER.start(
        model_path=DEFAULT_MODEL,
        video_path=video_path,
        annotations_file=annotations_file,
        station_name=cfg["station_name"],
        rules=cfg["rules"],
        action_mapping=cfg["action_mapping"],
        detection_kwargs=_detection_kwargs(params),
        params=params,
    )
    return {"task_id": job_id}


@app.post("/api/detect/stop")
def detect_stop():
    ok = TASK_MANAGER.stop()
    return {"ok": ok, "status": TASK_MANAGER.status()}


@app.get("/api/detect/status")
def detect_status():
    return TASK_MANAGER.status()


@app.get("/api/detect/result")
def detect_result():
    result = TASK_MANAGER.result()
    if result is None:
        raise HTTPException(404, "检测结果尚未生成")
    return result


@app.get("/api/train/events")
def train_events():
    status = TASK_MANAGER.status()
    result = status.get("result")
    if not result:
        return {"train_events": []}
    return {"train_events": result.get("train_events", [])}


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


@app.get("/api/download/video")
def download_video():
    result = TASK_MANAGER.result()
    if not result:
        raise HTTPException(404, "检测视频尚未生成")
    path = result["output"]["detected_video"]
    if not os.path.exists(path):
        raise HTTPException(404, "检测视频文件不存在")
    return FileResponse(path, media_type="video/mp4",
                        filename=os.path.basename(path))


@app.get("/api/download/report")
def download_report():
    result = TASK_MANAGER.result()
    if not result:
        raise HTTPException(404, "CSV 报告尚未生成")
    path = result["output"]["csv_report"]
    if not os.path.exists(path):
        raise HTTPException(404, "CSV 报告文件不存在")
    return FileResponse(path, media_type="text/csv",
                        filename=os.path.basename(path))


@app.get("/api/result/video/stream")
def result_video_stream():
    result = TASK_MANAGER.result()
    if not result:
        raise HTTPException(404, "检测视频尚未生成")
    path = result["output"]["detected_video"]
    if not os.path.exists(path):
        raise HTTPException(404, "检测视频文件不存在")
    return FileResponse(path, media_type="video/mp4")


# ---------------------------------------------------------------------------
# Annotation management
# ---------------------------------------------------------------------------


@app.post("/api/annotation/load")
def annotation_load(body: AnnotationLoadBody):
    data = _annotation_json(body.line, body.station)
    if data is None:
        return {"found": False, "data": None, "background_url": None}
    key = st.station_key(body.line, body.station)
    data.setdefault("line", body.line)
    data.setdefault("station", body.station)
    data.setdefault("station_key", key)
    bg_url = None
    if data.get("background") and data["background"].get("image"):
        bg_url = (f"/api/annotation/background?line={body.line}"
                  f"&station={body.station}")
    return {"found": True, "data": data, "background_url": bg_url}


@app.get("/api/annotation/background")
def annotation_background(line: str, station: str):
    path = st.resolve_background_path(line, station)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "背景图不存在")
    return FileResponse(path, media_type="image/png")


@app.post("/api/annotation/save")
def annotation_save(body: AnnotationSaveBody):
    key = st.station_key(body.line, body.station)
    path = st.resolve_annotation_path(body.line, body.station)
    data_dir = os.path.dirname(path)
    os.makedirs(data_dir, exist_ok=True)

    # Persist background image if provided as base64
    background = dict(body.background) if body.background else {}
    if body.background_data:
        bg_name = f"regions_{st.line_number(body.line)}_{key}_background.png"
        bg_path = os.path.join(data_dir, bg_name)
        raw = base64.b64decode(body.background_data.split(",", 1)[-1])
        with open(bg_path, "wb") as f:
            f.write(raw)
        background = {"image": bg_name, "frame": body.frame}

    data = {
        "line": body.line,
        "station": body.station,
        "station_key": key,
        "video": body.video,
        "frame": body.frame,
        "width": body.width,
        "height": body.height,
        "regions": [{"name": r["name"], "xywh": [int(v) for v in r["xywh"]]}
                    for r in body.regions],
        "lines": [{"name": ln["name"],
                   "pts": [[int(p[0]), int(p[1])] for p in ln["pts"]]}
                  for ln in body.lines],
    }
    if background:
        data["background"] = background
    if body.track_roi:
        data["track_roi"] = body.track_roi

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return {"ok": True, "path": path, "key": key}


@app.post("/api/annotation/extract_frame")
def annotation_extract_frame(body: ExtractFrameBody):
    import cv2
    if not os.path.exists(body.video_path):
        raise HTTPException(404, "视频文件不存在")
    cap = cv2.VideoCapture(body.video_path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, body.frame))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise HTTPException(400, "无法读取指定帧")
    ok, buf = cv2.imencode(".png", frame)
    if not ok:
        raise HTTPException(500, "帧编码失败")
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    return {"frame": body.frame, "image": f"data:image/png;base64,{b64}",
            "width": frame.shape[1], "height": frame.shape[0]}


@app.get("/api/annotation/stations")
def annotation_stations(line: str | None = None):
    lines = st.get_lines()
    if line is not None:
        lines = [ln for ln in lines if ln["name"] == line]
    out = []
    for ln in lines:
        for e in st.get_stations(ln["name"]):
            if e["annotated"]:
                out.append({"line": ln["name"], "station": e["name"],
                            "key": e["key"], "status": e["status"]})
    return {"stations": out}


@app.get("/api/model/status")
def model_status():
    from src.device import mps_available, resolve_device
    dev = resolve_device()
    return {
        "available": os.path.exists(DEFAULT_MODEL),
        "path": DEFAULT_MODEL,
        "device": dev,
        "mps_available": mps_available(),
        "half_supported": dev.startswith("cuda") or dev == "mps",
    }
