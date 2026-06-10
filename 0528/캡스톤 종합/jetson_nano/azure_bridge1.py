"""
azure_bridge.py

Jetson 痢� Azure �곌껐 釉뚮┸吏�.

��븷:
  Azure �쒕쾭(server.py)�� Jetson 濡쒖뺄 app.py �ъ씠瑜� �곌껐�쒕떎.

湲곕뒫:
  1. Jetson app.py /health �대쭅
     �� Azure濡� telemetry push

  2. Jetson app.py /snapshot �대쭅
     �� Azure濡� JPEG frame push

  3. Azure�먯꽌 command �대깽�� �섏떊
     �� Jetson app.py /send/<cmd>濡� �꾨떖

  4. Azure�먯꽌 speed �대깽�� �섏떊
     �� Jetson app.py /speed/<level>, /speed/up, /speed/down�쇰줈 �꾨떖

以묒슂:
  bridge.py�� PID, �쇱씤 寃�異�, 紐⑦꽣 �쒖뼱 �먮떒�� �섏� �딅뒗��.
  �ㅼ젣 �쒖뼱 �먮떒�� app.py媛� �대떦�쒕떎.
"""

import os
import sys
import time
import base64
import threading

import requests
import socketio


# ��������������������������������������������������������������������������������������������������������������������������
# Environment
# ��������������������������������������������������������������������������������������������������������������������������

AZURE_URL = os.getenv(
    'AZURE_URL',
    'http://20.196.194.107'
).rstrip('/')

LOCAL_URL = os.getenv(
    'LOCAL_URL',
    'http://127.0.0.1:8080'
).rstrip('/')

TELEMETRY_INTERVAL = float(os.getenv('TELEMETRY_INTERVAL', '1.0'))
FRAME_INTERVAL = float(os.getenv('FRAME_INTERVAL', '0.25'))
HEARTBEAT_INTERVAL = float(os.getenv('HEARTBEAT_INTERVAL', '10.0'))

HTTP_TIMEOUT = float(os.getenv('HTTP_TIMEOUT', '2.0'))


if not AZURE_URL:
    print('[Bridge] �ㅻ쪟: AZURE_URL �섍꼍 蹂��섎� �ㅼ젙�� 二쇱꽭��.')
    print('��) AZURE_URL=http://20.196.194.107 python3 azure_bridge.py')
    sys.exit(1)


# ��������������������������������������������������������������������������������������������������������������������������
# Socket.IO Client
# ��������������������������������������������������������������������������������������������������������������������������

sio = socketio.Client(
    reconnection=True,
    reconnection_attempts=0,
    reconnection_delay=2,
    reconnection_delay_max=15,
)


@sio.event
def connect():
    print(f'[Bridge] Azure �곌껐��: {AZURE_URL}')
    sio.emit('jetson_hello', {})


@sio.event
def disconnect():
    print('[Bridge] Azure �곌껐 �딄�. �먮룞 �ъ뿰寃� �쒕룄 以�...')


@sio.on('command')
def on_command(data: dict):
    """
    Azure �� ���쒕낫�� 紐낅졊�� Jetson 濡쒖뺄 app.py濡� �꾨떖.

    �덉긽 cmd:
      F: forward
      L: left
      R: right
      S: stop
      A: auto mode
      M: manual mode
    """

    cmd = str(data.get('cmd', '')).upper().strip()

    if cmd not in {'F', 'L', 'R', 'S', 'A', 'M'}:
        print(f'[Bridge] 臾댁떆: �섎せ�� 紐낅졊 cmd={cmd!r}')
        return

    try:
        r = requests.post(
            f'{LOCAL_URL}/send/{cmd}',
            timeout=HTTP_TIMEOUT
        )

        try:
            body = r.json()
        except Exception:
            body = r.text[:200]

        print(f'[Bridge] 紐낅졊 �꾨떖: {cmd} �� {r.status_code} {body}')

    except requests.RequestException as e:
        print(f'[Bridge] 紐낅졊 �꾨떖 �ㅽ뙣 ({cmd}): {e}')


@sio.on('speed')
def on_speed(data: dict):
    """
    Azure �� ���쒕낫�� �띾룄 紐낅졊�� Jetson 濡쒖뺄 app.py濡� �꾨떖.

    吏��� �뺤떇:
      {'level': 0~9}
      {'dir': 'up'}
      {'dir': 'down'}
    """

    try:
        if 'level' in data:
            level = int(data['level'])

            r = requests.post(
                f'{LOCAL_URL}/speed/{level}',
                timeout=HTTP_TIMEOUT
            )

            try:
                body = r.json()
            except Exception:
                body = r.text[:200]

            print(f'[Bridge] �띾룄 �ㅼ젙: level={level} �� {r.status_code} {body}')

        elif 'dir' in data:
            direction = str(data['dir']).lower().strip()

            if direction not in {'up', 'down'}:
                print(f'[Bridge] 臾댁떆: �섎せ�� speed dir={direction!r}')
                return

            r = requests.post(
                f'{LOCAL_URL}/speed/{direction}',
                timeout=HTTP_TIMEOUT
            )

            try:
                body = r.json()
            except Exception:
                body = r.text[:200]

            print(f'[Bridge] �띾룄 利앷컧: dir={direction} �� {r.status_code} {body}')

    except (requests.RequestException, ValueError) as e:
        print(f'[Bridge] �띾룄 紐낅졊 �꾨떖 �ㅽ뙣: {e}')


# ��������������������������������������������������������������������������������������������������������������������������
# Push loop
# ��������������������������������������������������������������������������������������������������������������������������

def push_loop():
    """
    Jetson 濡쒖뺄 app.py �곹깭�� �곸긽�� Azure濡� 二쇨린�곸쑝濡� push.
    """

    last_tele = 0.0
    last_frame = 0.0
    last_heartbeat = 0.0

    while True:
        if not sio.connected:
            time.sleep(0.5)
            continue

        now = time.time()

        # Heartbeat
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            try:
                sio.emit('jetson_hello', {})
                last_heartbeat = now
            except Exception as e:
                print(f'[Bridge] heartbeat �꾩넚 �ㅽ뙣: {e}')

        # Telemetry
        if now - last_tele >= TELEMETRY_INTERVAL:
            try:
                r = requests.get(
                    f'{LOCAL_URL}/health',
                    timeout=HTTP_TIMEOUT
                )

                if r.ok:
                    health = r.json()

                    tele = health.get('telemetry', {})
                    tele['bridge_ts'] = time.time()
                    tele['requested_mode'] = health.get('requested_mode')
                    tele['effective_mode'] = health.get('effective_mode')
                    tele['line_tracer_enabled'] = health.get('line_tracer_enabled')
                    tele['latest_result'] = health.get('latest_result')
                    tele['serial_connected'] = health.get('serial_connected')
                    tele['last_serial_error'] = health.get('last_serial_error')

                    sio.emit('telemetry', tele)

                else:
                    print(f'[Bridge] /health �묐떟 �ㅻ쪟: {r.status_code}')

                last_tele = now

            except requests.RequestException as e:
                print(f'[Bridge] telemetry 痍⑤뱷 �ㅽ뙣: {e}')
                last_tele = now

        # Frame
        if now - last_frame >= FRAME_INTERVAL:
            try:
                r = requests.get(
                    f'{LOCAL_URL}/snapshot',
                    timeout=HTTP_TIMEOUT
                )

                if r.status_code == 200 and r.content:
                    b64 = base64.b64encode(r.content).decode('ascii')
                    sio.emit('frame', {'data': b64})

                elif r.status_code == 204:
                    pass

                else:
                    print(f'[Bridge] /snapshot �묐떟 �ㅻ쪟: {r.status_code}')

                last_frame = now

            except requests.RequestException as e:
                print(f'[Bridge] frame 痍⑤뱷 �ㅽ뙣: {e}')
                last_frame = now

        time.sleep(0.05)


# ��������������������������������������������������������������������������������������������������������������������������
# Main
# ��������������������������������������������������������������������������������������������������������������������������

if __name__ == '__main__':
    print(f'[Bridge] LOCAL_URL={LOCAL_URL}')
    print(f'[Bridge] AZURE_URL={AZURE_URL}')

    print(f'[Bridge] 濡쒖뺄 �쒕쾭({LOCAL_URL}) �묐떟 �뺤씤 以�...')

    for i in range(10):
        try:
            r = requests.get(
                f'{LOCAL_URL}/health',
                timeout=HTTP_TIMEOUT
            )

            if r.ok:
                print('[Bridge] 濡쒖뺄 �쒕쾭 �뺤씤 �꾨즺.')
                break

            print(f'[Bridge] 濡쒖뺄 �쒕쾭 �묐떟 �ㅻ쪟: {r.status_code}')

        except requests.RequestException as e:
            print(f'[Bridge] 濡쒖뺄 �쒕쾭 誘몄쓳��({i + 1}/10): {e}')

        time.sleep(2.0)

    else:
        print(f'[Bridge] �ㅻ쪟: 濡쒖뺄 �쒕쾭({LOCAL_URL})�� �곌껐�� �� �놁뒿�덈떎.')
        print('app.py媛� 癒쇱� �ㅽ뻾 以묒씤吏� �뺤씤�섏꽭��.')
        sys.exit(1)

    threading.Thread(
        target=push_loop,
        daemon=True
    ).start()

    print(f'[Bridge] Azure �쒕쾭�� �곌껐 以�: {AZURE_URL}')

    sio.connect(AZURE_URL)
    sio.wait()
