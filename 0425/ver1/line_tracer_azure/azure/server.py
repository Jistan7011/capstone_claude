"""
azure/server.py  —  Azure 중계 서버

역할:
  웹 브라우저 ↔ Azure ↔ Jetson(azure_bridge.py) 간 실시간 중계.
  Jetson이 보낸 텔레메트리·영상 프레임을 브라우저에 전달하고,
  브라우저에서 누른 버튼 명령을 Jetson으로 전달한다.

Socket.IO 이벤트 (Jetson → 서버):
  jetson_hello  : Jetson 등록 (연결 시 1회)
  telemetry     : {mode, direction, rpm_left, rpm_right, zone,
                   manual_speed, last_update_ts, ...}
  frame         : {data: <base64 JPEG>}

Socket.IO 이벤트 (브라우저 → 서버):
  command       : {cmd: 'F'|'L'|'R'|'S'|'A'|'M'}
  speed         : {level: 0-9}  또는  {dir: 'up'|'down'}

서버 → 브라우저로 브로드캐스트:
  telemetry_update : 텔레메트리 갱신
  frame_update     : {data: base64 JPEG}
  jetson_status    : {connected: bool}
"""

import eventlet
eventlet.monkey_patch()

import os
import threading
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit

# ── 설정 ────────────────────────────────────────────────────────────────────
PORT       = int(os.getenv('PORT', '8000'))
SECRET_KEY = os.getenv('SECRET_KEY', 'dev-change-in-production')

# ── Flask + SocketIO ─────────────────────────────────────────────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
socketio = SocketIO(
    app,
    cors_allowed_origins='*',
    async_mode='eventlet',
    logger=False,
    engineio_logger=False,
)

# ── 전역 상태 ─────────────────────────────────────────────────────────────────
_lock            = threading.Lock()
latest_telemetry: dict = {}
latest_frame_b64: str | None = None
jetson_sid:       str | None = None   # 현재 연결된 Jetson의 SID


# ── Socket.IO 핸들러 ──────────────────────────────────────────────────────────

@socketio.on('connect')
def on_connect():
    """브라우저 또는 Jetson이 연결됐을 때 현재 Jetson 연결 상태를 전송."""
    with _lock:
        connected = jetson_sid is not None
        tele      = dict(latest_telemetry)
    emit('jetson_status', {'connected': connected})
    if tele:
        emit('telemetry_update', tele)


@socketio.on('disconnect')
def on_disconnect():
    """Jetson이 끊어진 경우 모든 브라우저에 알림."""
    global jetson_sid
    with _lock:
        if request.sid == jetson_sid:
            jetson_sid = None
    socketio.emit('jetson_status', {'connected': False})


@socketio.on('jetson_hello')
def on_jetson_hello(data=None):
    """Jetson이 연결 직후 자신을 서버에 등록."""
    global jetson_sid
    with _lock:
        jetson_sid = request.sid
    print(f'[Server] Jetson 등록됨: sid={request.sid}')
    socketio.emit('jetson_status', {'connected': True})


@socketio.on('telemetry')
def on_telemetry(data: dict):
    """Jetson에서 텔레메트리 수신 → 모든 브라우저로 브로드캐스트."""
    global latest_telemetry, jetson_sid
    newly_registered = False
    with _lock:
        latest_telemetry = dict(data)
        if jetson_sid != request.sid:
            jetson_sid = request.sid
            newly_registered = True
    if newly_registered:
        socketio.emit('jetson_status', {'connected': True})
    socketio.emit('telemetry_update', data)


@socketio.on('frame')
def on_frame(data: dict):
    """Jetson에서 JPEG 프레임 수신 → 모든 브라우저로 브로드캐스트."""
    global latest_frame_b64, jetson_sid
    newly_registered = False
    with _lock:
        latest_frame_b64 = data.get('data')
        if jetson_sid != request.sid:
            jetson_sid = request.sid
            newly_registered = True
    if newly_registered:
        socketio.emit('jetson_status', {'connected': True})
    socketio.emit('frame_update', data)


@socketio.on('command')
def on_command(data: dict):
    """브라우저 → Jetson 명령 전달 (F/L/R/S/A/M)."""
    with _lock:
        jid = jetson_sid
    if jid:
        socketio.emit('command', data, room=jid)
    else:
        emit('error', {'msg': 'Jetson이 연결되지 않았습니다.'})


@socketio.on('speed')
def on_speed(data: dict):
    """브라우저 → Jetson 속도 명령 전달 ({level} 또는 {dir})."""
    with _lock:
        jid = jetson_sid
    if jid:
        socketio.emit('speed', data, room=jid)
    else:
        emit('error', {'msg': 'Jetson이 연결되지 않았습니다.'})


# ── REST 엔드포인트 ───────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/health')
def health():
    with _lock:
        return jsonify({
            'jetson_connected': jetson_sid is not None,
            'telemetry':        dict(latest_telemetry),
        })


# ── 진입점 ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print(f'[Server] Azure 중계 서버 시작: 포트 {PORT}')
    socketio.run(app, host='0.0.0.0', port=PORT, debug=False)
