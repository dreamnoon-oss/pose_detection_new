const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = `请求失败 (${res.status})`;
    try {
      const j = await res.json();
      if (j && j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch (e) { /* ignore */ }
    throw new Error(msg);
  }
  return res.json();
}

function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.hidden = true; }, ms);
}

function fmtHms(sec) {
  if (sec == null || isNaN(sec)) return "00:00";
  sec = Math.max(0, sec);
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function fmtClock(sec) {
  if (sec == null || isNaN(sec)) return "—";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, "0")}:${s.toFixed(1).padStart(4, "0")}`;
}

const PARAMS = [
  { key: "model_conf", label: "YOLO 置信度阈值", min: 0.05, max: 1.0, step: 0.05, unit: "" },
  { key: "angle_threshold", label: "平行角度阈值", min: 0, max: 90, step: 1, unit: "°" },
  { key: "min_arm_torso_angle", label: "躯干夹角下限", min: 0, max: 90, step: 1, unit: "°" },
  { key: "hold_frames", label: "持续帧数", min: 5, max: 60, step: 1, unit: "" },
  { key: "cooldown_frames", label: "冷却期", min: 30, max: 300, step: 5, unit: "" },
  { key: "min_arm_len", label: "最小手臂长度", min: 10, max: 100, step: 1, unit: "px" },
  { key: "train_mad_threshold", label: "列车检测阈值", min: 5, max: 60, step: 1, unit: "" },
  { key: "idle_jump_seconds", label: "跳跃扫描间隔", min: 0, max: 30, step: 1, unit: "s" },
  { key: "device", label: "推理设备", type: "select", options: [["auto", "自动（MPS / GPU / CPU）"], ["mps", "MPS（Apple Silicon）"], ["cpu", "CPU"], ["cuda:0", "CUDA"]] },
  { key: "half", label: "FP16 半精度（MPS/CUDA）", type: "checkbox" },
];

const state = {
  lines: [],
  detectLine: null,
  detectStation: null,
  annoLine: null,
  annoStation: null,
  params: {},
  video: null,
  taskId: null,
  result: null,
  pollTimer: null,
  mode: "play",
  running: false,
};

function fillLineSelect(sel, onchange) {
  sel.innerHTML = '<option value="">请选择线路</option>';
  state.lines.forEach((ln) => {
    const o = document.createElement("option");
    o.value = ln.name;
    o.textContent = `${ln.name}（已标注 ${ln.annotated}/${ln.count}）`;
    sel.appendChild(o);
  });
  sel.onchange = onchange;
}

function fillStationSelect(sel, lineName, annotatedOnly) {
  sel.innerHTML = '<option value="">请选择站点</option>';
  const ln = state.lines.find((l) => l.name === lineName);
  if (!ln) return;
  ln.stations.forEach((s) => {
    const o = document.createElement("option");
    o.value = s.name;
    if (annotatedOnly && !s.annotated) {
      o.disabled = true;
      o.textContent = `${s.name}（未标注）`;
    } else {
      o.textContent = s.name + (s.annotated ? "（已标注）" : "");
    }
    o.dataset.key = s.key;
    o.dataset.annotated = s.annotated ? "1" : "0";
    sel.appendChild(o);
  });
}

function setBadge(el, cls, text) {
  el.className = "badge " + cls;
  el.textContent = text;
}

async function loadStations() {
  const data = await api("/api/stations/list");
  state.lines = data.lines;
  fillLineSelect($("#detectLine"), onDetectLine);
  fillLineSelect($("#annoLine"), onAnnoLine);
}

function onDetectLine() {
  state.detectLine = $("#detectLine").value;
  state.detectStation = null;
  fillStationSelect($("#detectStation"), state.detectLine, true);
  setBadge($("#detectAnnotationBadge"), "", "标注状态: —");
  updateStartButton();
  updateStatusBar();
}

function onDetectStation() {
  state.detectStation = $("#detectStation").value;
  const opt = $("#detectStation").selectedOptions[0];
  const annotated = opt && opt.dataset.annotated === "1";
  setBadge($("#detectAnnotationBadge"), annotated ? "ok" : "warn",
    annotated ? "标注状态: 已标注" : "标注状态: 未标注");
  updateStartButton();
  updateStatusBar();
}

function updateStartButton() {
  const btn = $("#btnStartDetect");
  const hint = $("#detectHint");
  if (state.running) {
    btn.disabled = true;
    hint.textContent = "检测进行中…";
    return;
  }
  btn.disabled = false;
  const missing = [];
  if (!state.video) missing.push("加载视频");
  if (!state.detectLine) missing.push("选择线路");
  if (!state.detectStation) missing.push("选择站点");
  if (state.mode !== "detect") missing.push("将运行模式切换为「开启检测」");
  hint.textContent = missing.length ? `提示：请先${missing.join("、")}` : "准备就绪";
}

function onAnnoLine() {
  state.annoLine = $("#annoLine").value;
  state.annoStation = null;
  fillStationSelect($("#annoStation"), state.annoLine, false);
  setBadge($("#annoBadge"), "", "标注状态: —");
}

async function onAnnoStation() {
  state.annoStation = $("#annoStation").value;
  const opt = $("#annoStation").selectedOptions[0];
  const annotated = opt && opt.dataset.annotated === "1";
  setBadge($("#annoBadge"), annotated ? "ok" : "warn",
    annotated ? "标注状态: 已标注" : "标注状态: 未标注");
  await loadAnnotationForStation();
}

function updateStatusBar() {
  const parts = [];
  parts.push(`${state.detectLine || "未选线路"}/${state.detectStation || "未选站点"}`);
  if (state.result) parts.push("检测完成");
  else if (state.taskId) parts.push("检测中…");
  else parts.push("待检测");
  $("#detectStatusBar").textContent = parts.join(" | ");
}

async function loadAnnotationForStation() {
  if (!state.annoLine || !state.annoStation) return;
  try {
    const res = await api("/api/annotation/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ line: state.annoLine, station: state.annoStation }),
    });
    if (res.found) {
      anno.loadData(res.data);
      if (res.background_url) await anno.loadBackgroundUrl(res.background_url);
      else anno.clearBackground();
      toast(`已加载标注（区域 ${res.data.regions.length}，线段 ${res.data.lines.length}）`);
    } else {
      anno.reset();
      anno.clearBackground();
      toast("该站点暂无标注，请加载背景图并开始标注");
    }
  } catch (e) {
    toast(e.message);
  }
}

async function loadParams() {
  const res = await api("/api/params/get");
  state.params = res.params || {};
  renderParams();
}

async function loadModelStatus() {
  const m = await api("/api/model/status");
  const modelBadge = $("#modelBadge");
  if (!m.available) {
    modelBadge.textContent = "模型: 未找到 yolo26x-pose.pt";
    modelBadge.style.color = "#f87171";
    toast("未找到模型文件 yolo26x-pose.pt，请将其放入 models/ 目录后再进行检测", 5000);
    return;
  }
  const devNames = { mps: "MPS（Apple Silicon）", cpu: "CPU", cuda: "CUDA" };
  const devName = devNames[m.device] || m.device;
  modelBadge.textContent = `模型: 已就绪 · ${devName}${m.half_supported ? " · FP16" : ""}`;
  modelBadge.style.color = "#4ade80";
}

function renderParams() {
  const grid = $("#paramGrid");
  grid.innerHTML = "";
  PARAMS.forEach((p) => {
    const val = state.params[p.key] != null ? state.params[p.key] : p.default;
    const item = document.createElement("div");
    item.className = "param-item";
    if (p.type === "select") {
      item.innerHTML = `
        <div class="param-label">${p.label}</div>
        <div class="param-row">
          <select data-key="${p.key}" style="flex:1">${p.options.map(([v, t]) =>
            `<option value="${v}"${String(val) === v ? " selected" : ""}>${t}</option>`).join("")}</select>
        </div>`;
      item.querySelector("select").addEventListener("change", saveParam);
    } else if (p.type === "checkbox") {
      item.innerHTML = `
        <div class="param-label">${p.label}</div>
        <div class="param-row">
          <input type="checkbox" data-key="${p.key}"${val ? " checked" : ""} />
        </div>`;
      item.querySelector("input").addEventListener("change", saveParam);
    } else {
      item.innerHTML = `
        <div class="param-label">${p.label}</div>
        <div class="param-row">
          <input type="range" min="${p.min}" max="${p.max}" step="${p.step}" value="${val}" data-key="${p.key}" />
          <input type="number" min="${p.min}" max="${p.max}" step="${p.step}" value="${val}" data-key="${p.key}" />
        </div>`;
      const range = item.querySelector("input[type=range]");
      const num = item.querySelector("input[type=number]");
      range.addEventListener("input", () => { num.value = range.value; });
      num.addEventListener("input", () => { range.value = num.value; });
      range.addEventListener("change", saveParam);
      num.addEventListener("change", saveParam);
    }
    grid.appendChild(item);
  });
}

async function saveParam(e) {
  const el = e.target;
  const key = el.dataset.key;
  let val;
  if (el.type === "checkbox") val = el.checked;
  else if (el.type === "number" || el.type === "range") val = parseFloat(el.value);
  else val = el.value;
  state.params[key] = val;
  try {
    await api("/api/params/set", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [key]: val }),
    });
  } catch (err) {
    toast(err.message);
  }
}

async function loadVideoFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await api("/api/video/load", { method: "POST", body: fd });
  onVideoLoaded(res.video);
}

async function loadVideoPath(path) {
  const res = await api("/api/video/load", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  onVideoLoaded(res.video);
}

function onVideoLoaded(video) {
  state.video = video;
  $("#videoName").textContent = video.name;
  $("#videoMeta").textContent = `时长 ${fmtHms(video.duration)} | 分辨率 ${video.resolution} | 帧率 ${video.fps} | 帧数 ${video.total_frames}`;
  const v = $("#videoPlayer");
  v.src = `/api/video/stream?t=${Date.now()}`;
  v.load();
  updateStartButton();
}

function setDetectMode(mode) {
  state.mode = mode;
  updateStartButton();
}

async function startDetect() {
  if (!state.detectLine) { toast("请先选择线路"); return; }
  if (!state.detectStation) { toast("请先选择站点"); return; }
  if (!state.video) { toast("请先加载视频文件（选择视频文件或使用服务器本地路径）"); return; }
  if (state.mode !== "detect") { toast('请将运行模式切换为「开启检测」'); return; }
  const params = {};
  PARAMS.forEach((p) => { params[p.key] = state.params[p.key]; });
  try {
    const res = await api("/api/detect/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        line: state.detectLine,
        station: state.detectStation,
        params,
      }),
    });
    state.taskId = res.task_id;
    state.result = null;
    state.running = true;
    $("#btnStopDetect").disabled = false;
    setBadge($("#detectStatusBadge"), "running", "检测状态: 检测中");
    $("#trainEventsPanel").hidden = true;
    $("#outputPaths").hidden = true;
    updateStartButton();
    updateStatusBar();
    startPolling();
  } catch (e) {
    toast(e.message);
  }
}

async function stopDetect() {
  await api("/api/detect/stop", { method: "POST" });
  toast("已请求停止");
}

function startPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    let s;
    try {
      s = await api("/api/detect/status");
    } catch (e) {
      clearInterval(state.pollTimer);
      return;
    }
    if (s.status === "idle") return;
    if (s.status === "running") {
      const pct = (s.progress * 100).toFixed(1);
      $("#detectStatusBar").textContent =
        `${state.detectLine}/${state.detectStation} | 检测中 ${pct}% | 帧 ${s.current_frame}/${s.total_frames} | FPS ${s.process_fps}`;
    } else if (s.status === "done") {
      clearInterval(state.pollTimer);
      state.running = false;
      state.result = s.result;
      onDetectDone(s.result);
    } else if (s.status === "error") {
      clearInterval(state.pollTimer);
      state.running = false;
      setBadge($("#detectStatusBadge"), "error", "检测状态: 失败");
      toast("检测失败: " + (s.error || "未知错误"));
      $("#btnStopDetect").disabled = true;
      updateStartButton();
      updateStatusBar();
    } else if (s.status === "stopped") {
      clearInterval(state.pollTimer);
      state.running = false;
      setBadge($("#detectStatusBadge"), "", "检测状态: 已停止");
      $("#btnStopDetect").disabled = true;
      updateStartButton();
      updateStatusBar();
    }
  }, 800);
}

function onDetectDone(result) {
  setBadge($("#detectStatusBadge"), "ok", "检测状态: 检测完成");
  $("#btnStopDetect").disabled = true;
  $("#btnDownloadVideo").disabled = false;
  $("#btnDownloadReport").disabled = false;
  $("#outputPaths").hidden = false;
  $("#outputVideoPath").textContent = "视频: " + result.output.detected_video;
  $("#outputReportPath").textContent = "报告: " + result.output.csv_report;
  renderTrainEvents(result.train_events || []);
  renderResultSummary(result);
  updateStartButton();
  updateStatusBar();
  toast("检测完成");
}

function renderTrainEvents(events) {
  const panel = $("#trainEventsPanel");
  const list = $("#trainEventList");
  list.innerHTML = "";
  if (!events.length) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  events.forEach((ev, idx) => {
    const li = document.createElement("li");
    li.dataset.time = ev.timestamp != null ? ev.timestamp : ev.start;
    li.innerHTML = `
      <span class="event-dot ${ev.type}"></span>
      <span class="event-time">${ev.display}</span>
      <span class="event-label">${ev.label}${ev.actions_summary ? "　" + ev.actions_summary : ""}</span>
      <span class="event-jump">跳转 ▶</span>`;
    li.addEventListener("click", () => {
      const t = ev.timestamp != null ? ev.timestamp : ev.start;
      const v = $("#videoPlayer");
      ensurePlayableSource();
      v.currentTime = Math.max(0, t);
      v.play();
      highlightEvent(idx);
    });
    list.appendChild(li);
  });
}

function highlightEvent(idx) {
  $$("#trainEventList li").forEach((li, i) => li.classList.toggle("active", i === idx));
}

function ensurePlayableSource() {
  const v = $("#videoPlayer");
  if ($("#chkDetected").checked) {
    if (!v.src.includes("/api/result/video/stream")) {
      v.src = `/api/result/video/stream?t=${Date.now()}`;
      v.load();
    }
    $("#playerSourceLabel").textContent = "检测结果视频";
  } else {
    if (!v.src.includes("/api/video/stream")) {
      v.src = `/api/video/stream?t=${Date.now()}`;
      v.load();
    }
    $("#playerSourceLabel").textContent = "源视频";
  }
}

function renderResultSummary(result) {
  const stops = result.stops || [];
  if (!stops.length) return;
  const summary = stops.map((s) => {
    const ev = s.evaluation || {};
    const compliant = ev.compliant ? "符合规范" : "不符合规范";
    return `第${s.index}趟：检出 ${ev.found}/${ev.expected} 动作，顺序${ev.order_valid ? "正确" : "不正确"}，${compliant}`;
  }).join(" ｜ ");
  const bar = $("#detectStatusBar");
  bar.textContent = `${state.detectLine}/${state.detectStation} | 检测完成 | ${summary}`;
}

function promptModal(title, bodyHtml, placeholder) {
  return new Promise((resolve) => {
    const modal = $("#modal");
    modal.classList.add("open");
    $("#modalTitle").textContent = title;
    $("#modalBody").innerHTML = bodyHtml;
    const ok = $("#modalOk");
    const cancel = $("#modalCancel");
    ok.onclick = () => { modal.classList.remove("open"); resolve($("#modalInput") ? $("#modalInput").value : true); };
    cancel.onclick = () => { modal.classList.remove("open"); resolve(null); };
  });
}

const anno = {
  regions: [],
  lines: [],
  trackRoi: null,
  background: null,
  backgroundName: null,
  tool: "select",
  view: { scale: 1, ox: 0, oy: 0 },
  selected: null,
  history: [],
  future: [],
  drawing: null,
  bgW: 0,
  bgH: 0,
  videoMeta: { width: 1920, height: 1080, frame: 0, video: "" },
};

function initAnno() {
  const canvas = $("#annoCanvas");
  resizeCanvas();
  window.addEventListener("resize", resizeCanvas);
  canvas.addEventListener("mousedown", annoMouseDown);
  canvas.addEventListener("mousemove", annoMouseMove);
  canvas.addEventListener("mouseup", annoMouseUp);
  canvas.addEventListener("wheel", annoWheel, { passive: false });
  $$(".tool").forEach((b) => b.addEventListener("click", () => setTool(b.dataset.tool)));
  renderAnnoList();
  updateAnnoStatusBar();
}

function resizeCanvas() {
  const wrap = $("#canvasWrap");
  const canvas = $("#annoCanvas");
  canvas.width = wrap.clientWidth;
  canvas.height = wrap.clientHeight;
  renderAnno();
}

function setTool(tool) {
  anno.tool = tool;
  $$(".tool").forEach((b) => b.classList.toggle("active", b.dataset.tool === tool));
  anno.selected = null;
  anno.drawing = null;
  renderAnno();
  renderPropPanel();
}

function fitView() {
  if (!anno.background) return;
  const cw = $("#annoCanvas").width;
  const ch = $("#annoCanvas").height;
  const s = Math.min(cw / anno.bgW, ch / anno.bgH);
  anno.view.scale = s;
  anno.view.ox = (cw - anno.bgW * s) / 2;
  anno.view.oy = (ch - anno.bgH * s) / 2;
}

function imgToScreen(x, y) {
  return { x: x * anno.view.scale + anno.view.ox, y: y * anno.view.scale + anno.view.oy };
}

function screenToImg(sx, sy) {
  return { x: (sx - anno.view.ox) / anno.view.scale, y: (sy - anno.view.oy) / anno.view.scale };
}

function canvasPos(e) {
  const c = $("#annoCanvas");
  const r = c.getBoundingClientRect();
  return { x: e.clientX - r.left, y: e.clientY - r.top };
}

function annoMouseDown(e) {
  const p = canvasPos(e);
  if (anno.tool === "pan") {
    anno.drawing = { type: "pan", sx: p.x, sy: p.y, ox: anno.view.ox, oy: anno.view.oy };
    return;
  }
  const imgP = screenToImg(p.x, p.y);
  if (anno.tool === "rect") {
    anno.drawing = { type: "rect", x0: imgP.x, y0: imgP.y, x1: imgP.x, y1: imgP.y };
    return;
  }
  if (anno.tool === "line") {
    if (!anno.drawing || anno.drawing.type !== "line") {
      anno.drawing = { type: "line", p1: { x: imgP.x, y: imgP.y } };
    } else {
      anno.drawing.p2 = { x: imgP.x, y: imgP.y };
      snapshot();
      const name = nextName("line", anno.lines);
      anno.lines.push({ name, pts: [[anno.drawing.p1.x, anno.drawing.p1.y], [anno.drawing.p2.x, anno.drawing.p2.y]] });
      anno.drawing = null;
      renderAnno();
      renderAnnoList();
      updateAnnoStatusBar();
    }
    return;
  }
  if (anno.tool === "delete") {
    const hit = hitTest(imgP);
    if (hit) { snapshot(); removeElement(hit); anno.selected = null; renderAnno(); renderAnnoList(); renderPropPanel(); updateAnnoStatusBar(); }
    return;
  }
  if (anno.tool === "track") {
    const hit = hitTest(imgP);
    if (hit && hit.kind === "region") {
      snapshot();
      hit.obj.name = "track";
      anno.trackRoi = "track";
      renderAnno();
      renderAnnoList();
      renderPropPanel();
      updateAnnoStatusBar();
    }
    return;
  }
  const hit = hitTest(imgP);
  if (hit) {
    anno.selected = hit;
    anno.drawing = { type: "drag", obj: hit, dx: imgP.x, dy: imgP.y };
  } else {
    anno.selected = null;
  }
  renderAnno();
  renderPropPanel();
  renderAnnoList();
}

function annoMouseMove(e) {
  const p = canvasPos(e);
  _cursorPos = p;
  if (!anno.drawing) return;
  const d = anno.drawing;
  if (d.type === "line") {
    renderAnno();
    return;
  }
  if (d.type === "pan") {
    anno.view.ox = d.ox + (p.x - d.sx);
    anno.view.oy = d.oy + (p.y - d.sy);
    renderAnno();
    return;
  }
  const imgP = screenToImg(p.x, p.y);
  if (d.type === "rect") {
    d.x1 = imgP.x;
    d.y1 = imgP.y;
    renderAnno();
    return;
  }
  if (d.type === "drag" && d.obj) {
    const dx = imgP.x - d.dx;
    const dy = imgP.y - d.dy;
    d.dx = imgP.x;
    d.dy = imgP.y;
    const o = d.obj.obj;
    if (d.obj.kind === "region") {
      o.xywh[0] += dx;
      o.xywh[1] += dy;
    } else {
      o.pts[0][0] += dx; o.pts[0][1] += dy;
      o.pts[1][0] += dx; o.pts[1][1] += dy;
    }
    renderAnno();
    renderPropPanel();
  }
}

function annoMouseUp(e) {
  if (!anno.drawing) return;
  const d = anno.drawing;
  if (d.type === "rect") {
    const x = Math.min(d.x0, d.x1);
    const y = Math.min(d.y0, d.y1);
    const w = Math.abs(d.x1 - d.x0);
    const h = Math.abs(d.y1 - d.y0);
    if (w > 3 && h > 3) {
      snapshot();
      const name = nextName("region", anno.regions);
      anno.regions.push({ name, xywh: [x, y, w, h] });
      renderAnnoList();
      updateAnnoStatusBar();
    }
  }
  if (d.type === "drag" && d.obj) {
    renderAnnoList();
    updateAnnoStatusBar();
  }
  anno.drawing = null;
  renderAnno();
}

function annoWheel(e) {
  e.preventDefault();
  const c = $("#annoCanvas");
  const r = c.getBoundingClientRect();
  const mx = e.clientX - r.left;
  const my = e.clientY - r.top;
  const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
  const oldScale = anno.view.scale;
  const newScale = Math.min(10, Math.max(0.05, oldScale * factor));
  const imgX = (mx - anno.view.ox) / oldScale;
  const imgY = (my - anno.view.oy) / oldScale;
  anno.view.scale = newScale;
  anno.view.ox = mx - imgX * newScale;
  anno.view.oy = my - imgY * newScale;
  renderAnno();
}

function nextName(prefix, arr) {
  let n = 1;
  const names = arr.map((a) => a.name);
  while (names.includes(`${prefix}_${n}`)) n++;
  return `${prefix}_${n}`;
}

function hitTest(imgP) {
  for (const ln of anno.lines) {
    const a = imgToScreen(ln.pts[0][0], ln.pts[0][1]);
    const b = imgToScreen(ln.pts[1][0], ln.pts[1][1]);
    if (distToSeg(imgToScreen(imgP.x, imgP.y), a, b) < 6) return { kind: "line", obj: ln };
  }
  for (const r of anno.regions) {
    const s = imgToScreen(r.xywh[0], r.xywh[1]);
    const e = imgToScreen(r.xywh[0] + r.xywh[2], r.xywh[1] + r.xywh[3]);
    const px = imgToScreen(imgP.x, imgP.y);
    if (px.x >= s.x && px.x <= e.x && px.y >= s.y && px.y <= e.y) return { kind: "region", obj: r };
  }
  return null;
}

function distToSeg(p, a, b) {
  const dx = b.x - a.x, dy = b.y - a.y;
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return Math.hypot(p.x - a.x, p.y - a.y);
  let t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

function removeElement(hit) {
  if (hit.kind === "region") {
    if (hit.obj.name === "track") anno.trackRoi = null;
    anno.regions = anno.regions.filter((r) => r !== hit.obj);
  } else {
    anno.lines = anno.lines.filter((l) => l !== hit.obj);
  }
}

function snapshot() {
  anno.history.push(JSON.stringify({
    regions: anno.regions,
    lines: anno.lines,
    trackRoi: anno.trackRoi,
  }));
  if (anno.history.length > 100) anno.history.shift();
  anno.future = [];
}

function undo() {
  if (!anno.history.length) return;
  anno.future.push(JSON.stringify({ regions: anno.regions, lines: anno.lines, trackRoi: anno.trackRoi }));
  const prev = JSON.parse(anno.history.pop());
  anno.regions = prev.regions;
  anno.lines = prev.lines;
  anno.trackRoi = prev.trackRoi;
  anno.selected = null;
  renderAnno();
  renderAnnoList();
  renderPropPanel();
  updateAnnoStatusBar();
}

function redo() {
  if (!anno.future.length) return;
  anno.history.push(JSON.stringify({ regions: anno.regions, lines: anno.lines, trackRoi: anno.trackRoi }));
  const next = JSON.parse(anno.future.pop());
  anno.regions = next.regions;
  anno.lines = next.lines;
  anno.trackRoi = next.trackRoi;
  renderAnno();
  renderAnnoList();
  renderPropPanel();
  updateAnnoStatusBar();
}

function renderAnno() {
  const canvas = $("#annoCanvas");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#1f2937";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (anno.background) {
    const s = anno.view.scale;
    ctx.drawImage(anno.background, anno.view.ox, anno.view.oy, anno.bgW * s, anno.bgH * s);
  }
  anno.regions.forEach((r) => {
    const [x, y, w, h] = r.xywh;
    const s = imgToScreen(x, y);
    const isTrack = r.name === "track";
    ctx.strokeStyle = isTrack ? "#ff9600" : "#ffd700";
    ctx.lineWidth = isTrack ? 3 : 2;
    ctx.strokeRect(s.x, s.y, w * anno.view.scale, h * anno.view.scale);
    ctx.fillStyle = isTrack ? "#ff9600" : "#ffd700";
    ctx.font = "13px sans-serif";
    ctx.fillText((isTrack ? r.name + " [Track]" : r.name), s.x, s.y - 6);
  });
  anno.lines.forEach((ln) => {
    const a = imgToScreen(ln.pts[0][0], ln.pts[0][1]);
    const b = imgToScreen(ln.pts[1][0], ln.pts[1][1]);
    ctx.strokeStyle = "#00c8ff";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
    ctx.fillStyle = "#00c8ff";
    ctx.fillText(ln.name, a.x + 5, a.y - 8);
  });
  if (anno.selected) {
    drawSelection(ctx, anno.selected);
  }
  if (anno.drawing && anno.drawing.type === "rect") {
    const d = anno.drawing;
    const s = imgToScreen(Math.min(d.x0, d.x1), Math.min(d.y0, d.y1));
    const e = imgToScreen(Math.max(d.x0, d.x1), Math.max(d.y0, d.y1));
    ctx.strokeStyle = "#00ff88";
    ctx.lineWidth = 2;
    ctx.strokeRect(s.x, s.y, e.x - s.x, e.y - s.y);
  }
  if (anno.drawing && anno.drawing.type === "line" && anno.drawing.p1) {
    const a = imgToScreen(anno.drawing.p1.x, anno.drawing.p1.y);
    const b = canvasPosCursor() || a;
    ctx.strokeStyle = "#00ff88";
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }
}

let _cursorPos = null;
function canvasPosCursor() {
  return _cursorPos;
}

function drawSelection(ctx, hit) {
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth = 1.5;
  ctx.setLineDash([4, 4]);
  if (hit.kind === "region") {
    const [x, y, w, h] = hit.obj.xywh;
    const s = imgToScreen(x, y);
    ctx.strokeRect(s.x - 3, s.y - 3, w * anno.view.scale + 6, h * anno.view.scale + 6);
  } else {
    const a = imgToScreen(hit.obj.pts[0][0], hit.obj.pts[0][1]);
    const b = imgToScreen(hit.obj.pts[1][0], hit.obj.pts[1][1]);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }
  ctx.setLineDash([]);
}

function renderPropPanel() {
  const panel = $("#propPanel");
  if (!anno.selected) {
    panel.innerHTML = '<div class="muted">未选中元素</div>';
    return;
  }
  const hit = anno.selected;
  if (hit.kind === "region") {
    const r = hit.obj;
    panel.innerHTML = `
      <div class="field"><span>名称</span><input id="propName" value="${r.name}" /></div>
      <div class="field"><span>类型</span><span>矩形区域</span></div>
      <div class="field"><span>X</span><input id="propX" type="number" value="${Math.round(r.xywh[0])}" /></div>
      <div class="field"><span>Y</span><input id="propY" type="number" value="${Math.round(r.xywh[1])}" /></div>
      <div class="field"><span>W</span><input id="propW" type="number" value="${Math.round(r.xywh[2])}" /></div>
      <div class="field"><span>H</span><input id="propH" type="number" value="${Math.round(r.xywh[3])}" /></div>
      <div style="display:flex;gap:8px;margin-top:10px">
        <button class="btn primary" id="propApply">应用修改</button>
        <button class="btn danger" id="propDelete">删除</button>
      </div>`;
    $("#propApply").onclick = () => {
      snapshot();
      r.name = $("#propName").value || r.name;
      r.xywh = [parseFloat($("#propX").value), parseFloat($("#propY").value),
        parseFloat($("#propW").value), parseFloat($("#propH").value)];
      if (r.name === "track") anno.trackRoi = "track";
      renderAnno(); renderAnnoList(); updateAnnoStatusBar();
    };
    $("#propDelete").onclick = () => { snapshot(); removeElement(hit); anno.selected = null; renderAnno(); renderAnnoList(); renderPropPanel(); updateAnnoStatusBar(); };
  } else {
    const l = hit.obj;
    panel.innerHTML = `
      <div class="field"><span>名称</span><input id="propName" value="${l.name}" /></div>
      <div class="field"><span>类型</span><span>参考线</span></div>
      <div class="field"><span>P1</span><span>(${Math.round(l.pts[0][0])}, ${Math.round(l.pts[0][1])})</span></div>
      <div class="field"><span>P2</span><span>(${Math.round(l.pts[1][0])}, ${Math.round(l.pts[1][1])})</span></div>
      <div style="display:flex;gap:8px;margin-top:10px">
        <button class="btn primary" id="propApply">应用修改</button>
        <button class="btn danger" id="propDelete">删除</button>
      </div>`;
    $("#propApply").onclick = () => { snapshot(); l.name = $("#propName").value || l.name; renderAnno(); renderAnnoList(); };
    $("#propDelete").onclick = () => { snapshot(); removeElement(hit); anno.selected = null; renderAnno(); renderAnnoList(); renderPropPanel(); updateAnnoStatusBar(); };
  }
}

function renderAnnoList() {
  const list = $("#annoList");
  list.innerHTML = "";
  const addGroup = (title) => {
    const g = document.createElement("div");
    g.className = "group";
    g.textContent = title;
    list.appendChild(g);
  };
  addGroup(`regions (${anno.regions.length})`);
  anno.regions.forEach((r) => {
    const it = document.createElement("div");
    it.className = "item" + (anno.selected && anno.selected.obj === r ? " sel" : "");
    it.textContent = r.name;
    it.onclick = () => { anno.selected = { kind: "region", obj: r }; renderAnno(); renderPropPanel(); renderAnnoList(); };
    list.appendChild(it);
  });
  addGroup(`lines (${anno.lines.length})`);
  anno.lines.forEach((l) => {
    const it = document.createElement("div");
    it.className = "item" + (anno.selected && anno.selected.obj === l ? " sel" : "");
    it.textContent = l.name;
    it.onclick = () => { anno.selected = { kind: "line", obj: l }; renderAnno(); renderPropPanel(); renderAnnoList(); };
    list.appendChild(it);
  });
  const track = document.createElement("div");
  track.className = "group";
  track.textContent = `track_roi: ${anno.trackRoi || "—"}`;
  list.appendChild(track);
  const bg = document.createElement("div");
  bg.className = "group";
  bg.textContent = `background: ${anno.backgroundName ? "✓ " + anno.backgroundName : "✗"}`;
  list.appendChild(bg);
}

function updateAnnoStatusBar() {
  $("#annoStatusBar").textContent =
    `${state.annoLine || "未选线路"}/${state.annoStation || "未选站点"} | 区域: ${anno.regions.length} | 线段: ${anno.lines.length} | 轨道: ${anno.trackRoi || "—"}`;
}

function reset() {
  anno.regions = [];
  anno.lines = [];
  anno.trackRoi = null;
  anno.selected = null;
  anno.drawing = null;
  anno.history = [];
  anno.future = [];
  anno.videoMeta = { width: 1920, height: 1080, frame: 0, video: "" };
}

function loadData(data) {
  reset();
  anno.regions = (data.regions || []).map((r) => ({ name: r.name, xywh: r.xywh.slice() }));
  anno.lines = (data.lines || []).map((l) => ({ name: l.name, pts: l.pts.map((p) => p.slice()) }));
  anno.trackRoi = data.track_roi || null;
  if (!anno.trackRoi) {
    const tr = anno.regions.find((r) => r.name === "track");
    if (tr) anno.trackRoi = "track";
  }
  anno.videoMeta = {
    width: data.width || 1920,
    height: data.height || 1080,
    frame: data.frame || 0,
    video: data.video || "",
  };
  anno.backgroundName = data.background ? data.background.image : null;
  renderAnno();
  renderAnnoList();
  renderPropPanel();
  updateAnnoStatusBar();
}

async function loadBackgroundUrl(url) {
  const img = new Image();
  img.onload = () => {
    anno.background = img;
    anno.bgW = img.naturalWidth;
    anno.bgH = img.naturalHeight;
    fitView();
    renderAnno();
    updateAnnoStatusBar();
  };
  img.src = url;
}

function clearBackground() {
  anno.background = null;
  anno.backgroundName = null;
  anno.bgW = 0;
  anno.bgH = 0;
  renderAnno();
  renderAnnoList();
  updateAnnoStatusBar();
}

function loadBackgroundFile(file) {
  const reader = new FileReader();
  reader.onload = () => {
    const img = new Image();
    img.onload = () => {
      anno.background = img;
      anno.bgW = img.naturalWidth;
      anno.bgH = img.naturalHeight;
      anno.backgroundName = file.name;
      anno.videoMeta.width = img.naturalWidth;
      anno.videoMeta.height = img.naturalHeight;
      fitView();
      renderAnno();
      renderAnnoList();
      updateAnnoStatusBar();
    };
    img.src = reader.result;
  };
  reader.readAsDataURL(file);
}

function dataUrlFromCanvas() {
  const c = $("#annoCanvas");
  return c.toDataURL("image/png");
}

async function saveAnnotation() {
  if (!state.annoLine || !state.annoStation) {
    toast("请先选择线路与站点");
    return;
  }
  let bgData = null;
  if (anno.background) {
    bgData = dataUrlFromCanvas();
  }
  const payload = {
    line: state.annoLine,
    station: state.annoStation,
    regions: anno.regions,
    lines: anno.lines,
    track_roi: anno.trackRoi,
    video: anno.videoMeta.video,
    frame: anno.videoMeta.frame,
    width: anno.videoMeta.width,
    height: anno.videoMeta.height,
    background: anno.backgroundName ? { image: anno.backgroundName, frame: anno.videoMeta.frame } : null,
    background_data: bgData,
  };
  try {
    const res = await api("/api/annotation/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    anno.backgroundName = res.key ? `regions_${state.annoLine.replace("号线", "")}_${res.key}_background.png` : anno.backgroundName;
    toast("标注已保存");
    setBadge($("#annoBadge"), "ok", "标注状态: 已标注");
    updateAnnoStatusBar();
    renderAnnoList();
    await loadStations();
    fillStationSelect($("#annoStation"), state.annoLine, false);
    $("#annoStation").value = state.annoStation;
    fillStationSelect($("#detectStation"), state.detectLine || state.annoLine, true);
    updateStartButton();
  } catch (e) {
    toast(e.message);
  }
}

function exportJson() {
  const data = {
    line: state.annoLine,
    station: state.annoStation,
    station_key: state.annoStation ? state.annoStation : "",
    video: anno.videoMeta.video,
    frame: anno.videoMeta.frame,
    width: anno.videoMeta.width,
    height: anno.videoMeta.height,
    regions: anno.regions.map((r) => ({ name: r.name, xywh: r.xywh.map(Math.round) })),
    lines: anno.lines.map((l) => ({ name: l.name, pts: l.pts.map((p) => p.map(Math.round)) })),
    track_roi: anno.trackRoi,
    background: anno.backgroundName ? { image: anno.backgroundName, frame: anno.videoMeta.frame } : null,
  };
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  downloadBlob(blob, `annotation_${data.line || ""}_${data.station_key || "export"}.json`);
}

function exportBackground() {
  if (!anno.background) { toast("当前无背景图"); return; }
  const a = document.createElement("a");
  a.href = dataUrlFromCanvas();
  a.download = "background.png";
  a.click();
}

function importJsonFile(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const data = JSON.parse(reader.result);
      loadData(data);
      if (data.background && data.background.image) {
        const line = data.line || state.annoLine;
        const station = data.station || state.annoStation;
        loadBackgroundUrl(`/api/annotation/background?line=${encodeURIComponent(line)}&station=${encodeURIComponent(station)}`);
      }
      toast("已导入 JSON");
    } catch (e) {
      toast("JSON 解析失败: " + e.message);
    }
  };
  reader.readAsText(file);
}

function downloadBlob(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

async function extractFrame() {
  if (!state.video) {
    toast("请先在检测分析页加载视频");
    return;
  }
  const frameStr = await promptModal("从视频提取背景帧", `<input id="modalInput" type="number" value="0" placeholder="帧号" style="width:100%;padding:8px" />`);
  if (frameStr == null) return;
  const frame = parseInt(frameStr, 10) || 0;
  try {
    const res = await api("/api/annotation/extract_frame", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_path: state.video.path, frame }),
    });
    const img = new Image();
    img.onload = () => {
      anno.background = img;
      anno.bgW = res.width;
      anno.bgH = res.height;
      anno.backgroundName = `extracted_frame_${frame}.png`;
      anno.videoMeta.frame = frame;
      anno.videoMeta.video = state.video.path;
      anno.videoMeta.width = res.width;
      anno.videoMeta.height = res.height;
      fitView();
      renderAnno();
      renderAnnoList();
      updateAnnoStatusBar();
      toast(`已提取第 ${frame} 帧作为背景`);
    };
    img.src = res.image;
  } catch (e) {
    toast(e.message);
  }
}

function initDetection() {
  $("#btnLoadVideo").onclick = () => $("#videoFileInput").click();
  $("#detectStation").addEventListener("change", onDetectStation);
  $("#videoFileInput").onchange = (e) => { if (e.target.files[0]) loadVideoFile(e.target.files[0]); e.target.value = ""; };
  $("#btnLoadPath").onclick = async () => {
    const p = await promptModal("服务器本地路径", `<input id="modalInput" type="text" placeholder="如 /Volumes/share/video.mp4 或 \\\\server\\share\\a.mp4" style="width:100%;padding:8px" />`);
    if (p) loadVideoPath(p);
  };
  $$('input[name="mode"]').forEach((r) => r.addEventListener("change", () => setDetectMode(r.value)));
  $("#btnStartDetect").onclick = startDetect;
  $("#btnStopDetect").onclick = stopDetect;
  $("#btnDownloadVideo").onclick = () => { if (state.result) window.open("/api/download/video", "_blank"); };
  $("#btnDownloadReport").onclick = () => { if (state.result) window.open("/api/download/report", "_blank"); };
  $("#chkDetected").addEventListener("change", ensurePlayableSource);
  $("#paramToggle").onclick = () => $("#paramPanel").classList.toggle("collapsed");

  const v = $("#videoPlayer");
  v.addEventListener("loadedmetadata", () => {
    $("#playerTime").textContent = `00:00 / ${fmtHms(v.duration)}`;
  });
  v.addEventListener("timeupdate", () => {
    $("#playerTime").textContent = `${fmtHms(v.currentTime)} / ${fmtHms(v.duration)}`;
    highlightCurrentEvent();
  });
}

function highlightCurrentEvent() {
  const t = $("#videoPlayer").currentTime;
  let best = -1;
  $$("#trainEventList li").forEach((li, i) => {
    const evTime = parseFloat(li.dataset.time);
    if (evTime != null && evTime <= t) best = i;
  });
  $$("#trainEventList li").forEach((li, i) => li.classList.toggle("active", i === best));
}

function initAnnoButtons() {
  $("#annoStation").addEventListener("change", onAnnoStation);
  $("#btnUndo").onclick = undo;
  $("#btnRedo").onclick = redo;
  $("#btnLoadBg").onclick = () => { const inp = $("#annoFileInput"); inp.accept = "image/*"; inp.onchange = (e) => { if (e.target.files[0]) loadBackgroundFile(e.target.files[0]); e.target.value = ""; }; inp.click(); };
  $("#btnImportJson").onclick = () => { const inp = $("#annoFileInput"); inp.accept = ".json"; inp.onchange = (e) => { if (e.target.files[0]) importJsonFile(e.target.files[0]); e.target.value = ""; }; inp.click(); };
  $("#btnExtractFrame").onclick = extractFrame;
  $("#btnSaveAnno").onclick = saveAnnotation;
  $("#btnExportJson").onclick = exportJson;
  $("#btnExportBg").onclick = exportBackground;
}

function switchPage(name) {
  $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.page === name));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === `page-${name}`));
  if (name === "annotate") resizeCanvas();
}

function initNav() {
  $$(".nav-btn").forEach((b) => b.addEventListener("click", () => {
    location.hash = "/" + b.dataset.page;
  }));
  window.addEventListener("hashchange", () => {
    const name = location.hash.replace(/^#\//, "");
    switchPage(name === "annotate" ? "annotate" : "detect");
  });
  const initial = location.hash.replace(/^#\//, "");
  switchPage(initial === "annotate" ? "annotate" : "detect");
}

async function boot() {
  initNav();
  initDetection();
  initAnno();
  initAnnoButtons();
  await loadStations();
  await loadParams();
  await loadModelStatus();
  updateStartButton();
}

boot();
