import os
import sqlite3
from datetime import datetime


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("DETECTIONS_DB", os.path.join(BASE_DIR, "detections.db"))


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS detection_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT,
                timestamp TEXT,
                frame_id INTEGER,
                class_name TEXT,
                confidence REAL,
                x1 INTEGER,
                y1 INTEGER,
                x2 INTEGER,
                y2 INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS detection_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT,
                start_time TEXT,
                end_time TEXT,
                class_name TEXT,
                detection_count INTEGER,
                avg_confidence REAL,
                max_confidence REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def normalize_detection(payload, default_device_id="jetson-yolo"):
    bbox = payload.get("bbox") or payload.get("box") or {}
    if isinstance(bbox, (list, tuple)):
        x1, y1, x2, y2 = (list(bbox) + [0, 0, 0, 0])[:4]
    else:
        x1 = bbox.get("x1", payload.get("x1", 0))
        y1 = bbox.get("y1", payload.get("y1", 0))
        x2 = bbox.get("x2", payload.get("x2", 0))
        y2 = bbox.get("y2", payload.get("y2", 0))

    return {
        "device_id": payload.get("device_id") or default_device_id,
        "timestamp": payload.get("timestamp") or _now_iso(),
        "frame_id": payload.get("frame_id"),
        "class_name": payload.get("class_name") or payload.get("label") or payload.get("name") or "unknown",
        "confidence": float(payload.get("confidence", payload.get("conf", 0.0)) or 0.0),
        "x1": int(x1 or 0),
        "y1": int(y1 or 0),
        "x2": int(x2 or 0),
        "y2": int(y2 or 0),
    }


def insert_detection(payload, default_device_id="jetson-yolo"):
    row = normalize_detection(payload, default_device_id)
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO detection_logs (
                device_id, timestamp, frame_id, class_name, confidence, x1, y1, x2, y2
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["device_id"],
                row["timestamp"],
                row["frame_id"],
                row["class_name"],
                row["confidence"],
                row["x1"],
                row["y1"],
                row["x2"],
                row["y2"],
            ),
        )
        row["id"] = cur.lastrowid
    return row


def insert_detections(detections, default_device_id="jetson-yolo"):
    return [insert_detection(item, default_device_id) for item in detections]


def summarize_last_15min():
    end_time = _now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO detection_summary (
                device_id, start_time, end_time, class_name,
                detection_count, avg_confidence, max_confidence
            )
            SELECT
                device_id,
                datetime('now', '-15 minutes'),
                ?,
                class_name,
                COUNT(*),
                AVG(confidence),
                MAX(confidence)
            FROM detection_logs
            WHERE datetime(timestamp) >= datetime('now', '-15 minutes')
            GROUP BY device_id, class_name
            """,
            (end_time,),
        )
    return get_recent_summaries()


def get_recent_detections(limit=30):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, device_id, timestamp, frame_id, class_name, confidence, x1, y1, x2, y2
            FROM detection_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]


def get_recent_summaries(limit=20):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                id, device_id, start_time, end_time, class_name,
                detection_count, avg_confidence, max_confidence, created_at
            FROM detection_summary
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]


def get_counts_last_15min():
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT class_name, COUNT(*) AS detection_count, AVG(confidence) AS avg_confidence
            FROM detection_logs
            WHERE datetime(timestamp) >= datetime('now', '-15 minutes')
            GROUP BY class_name
            ORDER BY detection_count DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]
