"""
jetson_nano/azure_bridge.py  —  Jetson 측 Azure 연결 브릿지

역할:
  로컬 app.py(127.0.0.1:8080)와 Azure 중계 서버(server.py) 사이를 연결한다.
  - 로컬 /health 폴링 → 텔레메트리를 Azure로 Push
  - 로컬 /snapshot 폴링 → JPEG 프레임을 Azure로 Push
  - Azure에서 수신한 명령 → 로컬 /send/<cmd> 또는 /speed/<x>로 전달

사용법:
  # Azure URL 설정 후 실행 (app.py가 먼저 실행 중이어야 함)
  AZURE_URL=https://your-app.azurewebsites.net python jetson_nano/azure_bridge.py

환경 변수:
  AZURE_URL          : Azure 서버 주소 (필수)
  LOCAL_URL          : 로컬 Flask 서버 주소 (기본값: http://127.0.0.1:8080)
  TELEMETRY_INTERVAL : 텔레메트리 전송 간격 초 (기본값: 1.0)
  FRAME_INTERVAL     : 영상 프레임 전송 간격 초 (기본값: 0.5)
"""

import os
import sys
import time
import base64
import threading

import requests
import socketio

# ── 환경 변수 ─────────────────────────────────────────────────────────────────
AZURE_URL          = os.getenv('AZURE_URL', 'http://20.196.194.107').rstrip('/')
LOCAL_URL          = os.getenv('LOCAL_URL', 'http://127.0.0.1:8080').rstrip('/')
TELEMETRY_INTERVAL = float(os.getenv('TELEMETRY_INTERVAL', '1.0'))
FRAME_INTERVAL     = float(os.getenv('FRAME_INTERVAL', '0.15'))   # ~6fps
HEARTBEAT_INTERVAL = float(os.getenv('HEARTBEAT_INTERVAL', '10.0'))  # jetson_hello 재전송 주기

if not AZURE_URL:
    print('[Bridge] 오류: AZURE_URL 환경 변수를 설정해 주세요.')
    print('         예) AZURE_URL=https://your-app.azurewebsites.net python azure_bridge.py')
    sys.exit(1)

# ── Socket.IO 클라이언트 ──────────────────────────────────────────────────────
sio = socketio.Client(
    reconnection=True,
    reconnection_attempts=0,       # 무한 재시도
    reconnection_delay=2,
    reconnection_delay_max=15,
)


@sio.event
def connect():
    print(f'[Bridge] Azure 연결됨: {AZURE_URL}')
    sio.emit('jetson_hello', {})


@sio.event
def disconnect():
    print('[Bridge] Azure 연결 끊김. 자동 재연결 시도 중...')


@sio.on('command')
def on_command(data: dict):
    """Azure 브라우저 → 로컬 /send/<cmd>"""
    cmd = str(data.get('cmd', '')).upper()
    if not cmd:
        return
    try:
        r = requests.post(f'{LOCAL_URL}/send/{cmd}', timeout=2)
        print(f'[Bridge] 명령 전달: {cmd} → {r.status_code}')
    except requests.RequestException as e:
        print(f'[Bridge] 명령 전달 실패 ({cmd}): {e}')


@sio.on('speed')
def on_speed(data: dict):
    """Azure 브라우저 → 로컬 /speed/<level|dir>"""
    try:
        if 'level' in data:
            requests.post(f'{LOCAL_URL}/speed/{int(data["level"])}', timeout=2)
            print(f'[Bridge] 속도 설정: level={data["level"]}')
        elif 'dir' in data:
            requests.post(f'{LOCAL_URL}/speed/{data["dir"]}', timeout=2)
            print(f'[Bridge] 속도 증감: dir={data["dir"]}')
    except requests.RequestException as e:
        print(f'[Bridge] 속도 명령 전달 실패: {e}')


# ── 데이터 Push 루프 ──────────────────────────────────────────────────────────

def push_loop():
    """텔레메트리와 영상 프레임을 주기적으로 Azure에 전송."""
    last_tele      = 0.0
    last_frame     = 0.0
    last_heartbeat = 0.0

    while True:
        if not sio.connected:
            time.sleep(0.5)
            continue

        now = time.time()

        # 주기적 jetson_hello 재전송 — reconnect 시 jetson_sid 누락 방지
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            sio.emit('jetson_hello', {})
            last_heartbeat = now

        # 텔레메트리 전송 (TELEMETRY_INTERVAL 마다)
        if now - last_tele >= TELEMETRY_INTERVAL:
            try:
                r = requests.get(f'{LOCAL_URL}/health', timeout=2)
                if r.ok:
                    tele = r.json().get('telemetry', {})
                    sio.emit('telemetry', tele)
                    last_tele = now
            except requests.RequestException:
                pass

        # 영상 프레임 전송 (FRAME_INTERVAL 마다)
        if now - last_frame >= FRAME_INTERVAL:
            try:
                r = requests.get(f'{LOCAL_URL}/snapshot', timeout=2)
                if r.status_code == 200 and r.content:
                    b64 = base64.b64encode(r.content).decode('ascii')
                    sio.emit('frame', {'data': b64})
                    last_frame = now
            except requests.RequestException:
                pass

        time.sleep(0.05)


# ── 진입점 ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    # 로컬 서버 기동 확인
    print(f'[Bridge] 로컬 서버({LOCAL_URL}) 응답 확인 중...')
    for _ in range(10):
        try:
            requests.get(f'{LOCAL_URL}/health', timeout=2)
            print('[Bridge] 로컬 서버 확인 완료.')
            break
        except requests.RequestException:
            print('[Bridge] 로컬 서버 미응답, 2초 후 재시도...')
            time.sleep(2)
    else:
        print(f'[Bridge] 오류: 로컬 서버({LOCAL_URL})에 연결할 수 없습니다.')
        print('         app.py가 먼저 실행 중인지 확인해 주세요.')
        sys.exit(1)

    threading.Thread(target=push_loop, daemon=True).start()

    print(f'[Bridge] Azure 서버에 연결 중: {AZURE_URL}')
    sio.connect(AZURE_URL)
    sio.wait()
