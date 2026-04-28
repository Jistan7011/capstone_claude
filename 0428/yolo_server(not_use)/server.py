import eventlet

eventlet.monkey_patch()

import base64
import json
import os
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO, emit

from db import (
    get_counts_last_15min,
    get_recent_detections,
    get_recent_summaries,
    init_db,
    insert_detections,
    summarize_last_15min,
)


PORT = int(os.getenv("PORT", "8000"))
SECRET_KEY = os.getenv("SECRET_KEY", "dev-change-in-production")
YOLO_SUMMARY_INTERVAL_SECONDS = int(os.getenv("YOLO_SUMMARY_INTERVAL_SECONDS", "900"))

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="eventlet",
    logger=False,
    engineio_logger=False,
)

state_lock = threading.Lock()

line_sid = None
line_latest_telemetry = {}
line_latest_frame_b64 = None

yolo_sid = None
yolo_latest_frame_b64 = None
yolo_latest_detections = []
yolo_latest_meta = {}
yolo_last_update_ts = None


def utc_now():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def service_snapshot():
    with state_lock:
        return {
            "line": {
                "connected": line_sid is not None,
                "telemetry": dict(line_latest_telemetry),
                "has_frame": line_latest_frame_b64 is not None,
            },
            "yolo": {
                "connected": yolo_sid is not None,
                "has_frame": yolo_latest_frame_b64 is not None,
                "last_update_ts": yolo_last_update_ts,
                "latest_meta": dict(yolo_latest_meta),
                "latest_detections": list(yolo_latest_detections),
            },
        }


def emit_yolo_tables():
    socketio.emit(
        "yolo_stats_update",
        {
            "recent": get_recent_detections(30),
            "summaries": get_recent_summaries(20),
            "counts15m": get_counts_last_15min(),
        },
    )


def summary_worker():
    while True:
        eventlet.sleep(max(10, YOLO_SUMMARY_INTERVAL_SECONDS))
        try:
            summarize_last_15min()
            emit_yolo_tables()
        except Exception as exc:
            print(f"[Server] YOLO summary error: {exc}")


@socketio.on("connect")
def on_connect():
    snap = service_snapshot()
    emit("service_status", snap)
    emit("jetson_status", {"connected": snap["line"]["connected"]})
    emit("yolo_status", {"connected": snap["yolo"]["connected"]})
    if snap["line"]["telemetry"]:
        emit("telemetry_update", snap["line"]["telemetry"])
    if snap["yolo"]["latest_detections"]:
        emit("yolo_detection_update", snap["yolo"])
    emit(
        "yolo_stats_update",
        {
            "recent": get_recent_detections(30),
            "summaries": get_recent_summaries(20),
            "counts15m": get_counts_last_15min(),
        },
    )


@socketio.on("disconnect")
def on_disconnect():
    global line_sid, yolo_sid
    line_changed = False
    yolo_changed = False
    with state_lock:
        if request.sid == line_sid:
            line_sid = None
            line_changed = True
        if request.sid == yolo_sid:
            yolo_sid = None
            yolo_changed = True
    if line_changed:
        socketio.emit("jetson_status", {"connected": False})
    if yolo_changed:
        socketio.emit("yolo_status", {"connected": False})
    if line_changed or yolo_changed:
        socketio.emit("service_status", service_snapshot())


@socketio.on("jetson_hello")
def on_line_hello(data=None):
    global line_sid
    with state_lock:
        line_sid = request.sid
    print(f"[Server] line tracer connected sid={request.sid}")
    socketio.emit("jetson_status", {"connected": True})
    socketio.emit("service_status", service_snapshot())


@socketio.on("telemetry")
def on_line_telemetry(data):
    global line_latest_telemetry, line_sid
    newly_registered = False
    with state_lock:
        line_latest_telemetry = dict(data or {})
        if line_sid != request.sid:
            line_sid = request.sid
            newly_registered = True
    if newly_registered:
        socketio.emit("jetson_status", {"connected": True})
    socketio.emit("telemetry_update", data or {})
    socketio.emit("service_status", service_snapshot())


@socketio.on("frame")
def on_line_frame(data):
    global line_latest_frame_b64, line_sid
    newly_registered = False
    with state_lock:
        line_latest_frame_b64 = (data or {}).get("data")
        if line_sid != request.sid:
            line_sid = request.sid
            newly_registered = True
    if newly_registered:
        socketio.emit("jetson_status", {"connected": True})
    socketio.emit("frame_update", data or {})


@socketio.on("command")
def on_line_command(data):
    with state_lock:
        sid = line_sid
    if sid:
        socketio.emit("command", data or {}, room=sid)
    else:
        emit("error", {"msg": "Line tracer Jetson is not connected."})


@socketio.on("speed")
def on_line_speed(data):
    with state_lock:
        sid = line_sid
    if sid:
        socketio.emit("speed", data or {}, room=sid)
    else:
        emit("error", {"msg": "Line tracer Jetson is not connected."})


@socketio.on("yolo_hello")
def on_yolo_hello(data=None):
    global yolo_sid, yolo_latest_meta
    with state_lock:
        yolo_sid = request.sid
        yolo_latest_meta = dict(data or {})
    print(f"[Server] YOLO stream connected sid={request.sid}")
    socketio.emit("yolo_status", {"connected": True, "meta": yolo_latest_meta})
    socketio.emit("service_status", service_snapshot())


@socketio.on("yolo_frame")
def on_yolo_frame(data):
    global yolo_sid
    payload = data or {}
    with state_lock:
        yolo_sid = request.sid
    handle_yolo_frame_payload(payload)


def handle_yolo_frame_payload(payload, frame_b64=None):
    global yolo_latest_frame_b64, yolo_latest_meta, yolo_last_update_ts
    payload = payload or {}
    if frame_b64 is not None:
        payload = {**payload, "data": frame_b64}

    with state_lock:
        yolo_latest_frame_b64 = payload.get("data") or yolo_latest_frame_b64
        yolo_last_update_ts = utc_now()
        yolo_latest_meta = {
            **yolo_latest_meta,
            "device_id": payload.get("device_id", yolo_latest_meta.get("device_id", "jetson-yolo")),
            "frame_id": payload.get("frame_id", yolo_latest_meta.get("frame_id")),
            "fps": payload.get("fps", yolo_latest_meta.get("fps")),
            "detection_count": len(payload.get("detections") or yolo_latest_detections),
        }
    socketio.emit("yolo_status", {"connected": True, "meta": dict(yolo_latest_meta)})
    if payload.get("data"):
        socketio.emit("yolo_frame_update", payload)

    saved = []
    if "detections" in payload:
        saved = handle_yolo_detections(payload)
    return saved


def handle_yolo_detections(payload):
    global yolo_sid, yolo_latest_detections, yolo_latest_meta, yolo_last_update_ts
    payload = payload or {}
    detections = payload.get("detections")
    if detections is None:
        detections = [payload]
    if not isinstance(detections, list):
        detections = []

    default_device_id = payload.get("device_id", "jetson-yolo")
    frame_id = payload.get("frame_id")
    normalized = []
    for item in detections:
        item = dict(item or {})
        item.setdefault("device_id", default_device_id)
        item.setdefault("frame_id", frame_id)
        item.setdefault("timestamp", payload.get("timestamp") or utc_now())
        normalized.append(item)

    saved = insert_detections(normalized, default_device_id) if normalized else []
    request_sid = getattr(request, "sid", None)
    with state_lock:
        yolo_sid = request_sid or yolo_sid
        yolo_latest_detections = saved
        yolo_last_update_ts = utc_now()
        yolo_latest_meta = {
            **yolo_latest_meta,
            "device_id": default_device_id,
            "frame_id": frame_id,
            "detection_count": len(saved),
        }

    update = {
        "connected": True,
        "last_update_ts": yolo_last_update_ts,
        "latest_meta": dict(yolo_latest_meta),
        "latest_detections": saved,
    }
    socketio.emit("yolo_status", {"connected": True, "meta": dict(yolo_latest_meta)})
    socketio.emit("yolo_detection_update", update)
    emit_yolo_tables()
    return saved


@socketio.on("yolo_detection")
def on_yolo_detection(data):
    handle_yolo_detections(data)


@socketio.on("yolo_detections")
def on_yolo_detections(data):
    handle_yolo_detections(data)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    data = service_snapshot()
    data["yolo"]["counts15m"] = get_counts_last_15min()
    return jsonify(data)


@app.route("/api/yolo/detections", methods=["GET", "POST"])
def yolo_detections_api():
    if request.method == "POST":
        saved = handle_yolo_detections(request.get_json(silent=True) or {})
        return jsonify({"ok": True, "saved": saved})
    limit = int(request.args.get("limit", "30"))
    return jsonify(get_recent_detections(limit))


@app.route("/upload_frame", methods=["POST"])
def upload_frame():
    metadata_raw = request.form.get("metadata", "{}")
    try:
        metadata = json.loads(metadata_raw)
    except json.JSONDecodeError:
        return jsonify({"ok": False, "error": "invalid metadata json"}), 400

    frame = request.files.get("frame")
    frame_b64 = None
    if frame is not None:
        frame_b64 = base64.b64encode(frame.read()).decode("ascii")

    saved = handle_yolo_frame_payload(metadata, frame_b64)
    return jsonify({"ok": True, "saved": len(saved)})


@app.route("/api/yolo/summaries")
def yolo_summaries_api():
    limit = int(request.args.get("limit", "20"))
    return jsonify(get_recent_summaries(limit))


@app.route("/api/yolo/summarize", methods=["POST"])
def yolo_summarize_api():
    return jsonify({"ok": True, "summaries": summarize_last_15min()})


init_db()
socketio.start_background_task(summary_worker)


if __name__ == "__main__":
    print(f"[Server] unified dashboard listening on {PORT}")
    socketio.run(app, host="0.0.0.0", port=PORT, debug=False)
