import os
import time
import threading
from dataclasses import dataclass, asdict
from collections import deque

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template

try:
    import serial
except Exception:
    serial = None

app = Flask(__name__)

CAM_INDEX = int(os.getenv('CAM_INDEX', '0'))
CAM_BACKEND = os.getenv('CAM_BACKEND', 'auto')   # 'auto' | 'v4l2' | 'gstreamer'
GST_PIPELINE = os.getenv('GST_PIPELINE', '')      # 커스텀 GStreamer 파이프라인 (비어있으면 자동 생성)
CAP_W = int(os.getenv('CAP_W', '640'))
CAP_H = int(os.getenv('CAP_H', '480'))
CAP_FPS = int(os.getenv('CAP_FPS', '20'))
STREAM_W = int(os.getenv('STREAM_W', '480'))
STREAM_H = int(os.getenv('STREAM_H', '360'))
STREAM_FPS = int(os.getenv('STREAM_FPS', '8'))
JPEG_QUALITY = int(os.getenv('JPEG_QUALITY', '55'))
HTTP_HOST = os.getenv('HTTP_HOST', '0.0.0.0')
HTTP_PORT = int(os.getenv('HTTP_PORT', '8080'))

ROI_START_RATIO = float(os.getenv('ROI_START_RATIO', '0.65'))
AUTO_Q = int(os.getenv('AUTO_Q', '10'))
AUTO_MARGIN = int(os.getenv('AUTO_MARGIN', '20'))
THRESH_MIN = int(os.getenv('THRESH_MIN', '20'))
THRESH_MAX = int(os.getenv('THRESH_MAX', '220'))
ERODE_IT = int(os.getenv('ERODE_IT', '1'))
DILATE_IT = int(os.getenv('DILATE_IT', '2'))
MIN_AREA = int(os.getenv('MIN_AREA', '120'))
MIN_WIDTH = int(os.getenv('MIN_WIDTH', '10'))
MIN_HEIGHT = int(os.getenv('MIN_HEIGHT', '10'))
CENTER_DEADBAND = int(os.getenv('CENTER_DEADBAND', '40'))
LOST_LINE_STOP_FRAMES = int(os.getenv('LOST_LINE_STOP_FRAMES', '3'))
DRAW_DEBUG_TEXT = os.getenv('DRAW_DEBUG_TEXT', '1') == '1'
DRAW_CONTOURS = os.getenv('DRAW_CONTOURS', '1') == '1'
SHOW_MASK_PREVIEW = os.getenv('SHOW_MASK_PREVIEW', '0') == '1'

SERIAL_ENABLED = os.getenv('SERIAL_ENABLED', '1') == '1'
SERIAL_PORT = os.getenv('SERIAL_PORT', '/dev/ttyUSB0')
SERIAL_BAUD = int(os.getenv('SERIAL_BAUD', '115200'))
SERIAL_TIMEOUT = float(os.getenv('SERIAL_TIMEOUT', '0.05'))
SERIAL_RECONNECT_DELAY = float(os.getenv('SERIAL_RECONNECT_DELAY', '3.0'))
AUTO_ON_START = os.getenv('AUTO_ON_START', '1') == '1'
COMMAND_SEND_INTERVAL_MS = int(os.getenv('COMMAND_SEND_INTERVAL_MS', '80'))

latest_jpg = None
jpg_lock = threading.Lock()
result_lock = threading.Lock()
telemetry_lock = threading.Lock()
command_lock = threading.Lock()

latest_result = {
    'detected': False,
    'cx': None,
    'cy': None,
    'err': None,
    'threshold': None,
    'decision': 'S',
    'vision_state': 'IDLE',
}

latest_telemetry = {
    'mode': 'UNKNOWN',
    'direction': 'STOP',
    'rpm_left': 0.0,
    'rpm_right': 0.0,
    'zone': '-',
    'jetson_age_ms': None,
    'manual_speed': None,
    'last_update_ts': 0.0,
    'raw': '',
}

command_history = deque(maxlen=40)
serial_log = deque(maxlen=60)
latest_command = 'S'


@dataclass
class Diag:
    worker_started: bool = False
    camera_opened: bool = False
    frames_ok: int = 0
    frames_fail: int = 0
    last_frame_ts: float = 0.0
    last_error: str = ''
    last_process_ms: float = 0.0
    last_encode_ms: float = 0.0
    serial_connected: bool = False
    serial_reader_started: bool = False
    last_serial_write_ts: float = 0.0
    last_serial_command: str = 'S'
    last_serial_error: str = ''
    last_serial_line_ts: float = 0.0
    auto_mode_requested: bool = AUTO_ON_START


diag = Diag()


def cmd_to_label(cmd: str) -> str:
    return {'F': 'FORWARD', 'L': 'LEFT', 'R': 'RIGHT', 'S': 'STOP', 'A': 'AUTO', 'M': 'MANUAL'}.get(cmd, cmd)


def get_telemetry_snapshot():
    with telemetry_lock:
        return dict(latest_telemetry)


def get_current_mode() -> str:
    return str(get_telemetry_snapshot().get('mode', 'UNKNOWN')).upper()


class MCUBridge:
    def __init__(self):
        self.ser = None
        self._port_lock = threading.Lock()
        self.last_sent_cmd = None
        self.last_sent_ts = 0.0
        self.reader_thread = None
        self.reader_stop = threading.Event()

    def connect(self):
        if not SERIAL_ENABLED:
            diag.serial_connected = False
            return
        if serial is None:
            diag.last_serial_error = 'pyserial not installed'
            diag.serial_connected = False
            return
        with self._port_lock:
            if self.ser is not None and self.ser.is_open:
                self.start_reader()
                return
            try:
                s = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=SERIAL_TIMEOUT)
                self.ser = s
            except Exception as e:
                self.ser = None
                diag.serial_connected = False
                diag.last_serial_error = str(e)
                self.start_reader()  # 실패해도 reader 스레드는 시작 (재시도 루프)
                return
        time.sleep(2.0)
        diag.serial_connected = True
        diag.last_serial_error = ''
        self.start_reader()
        if AUTO_ON_START:
            self.send_raw('A')

    def start_reader(self):
        if self.reader_thread and self.reader_thread.is_alive():
            return
        self.reader_stop.clear()
        self.reader_thread = threading.Thread(target=self.reader_loop, daemon=True)
        self.reader_thread.start()

    def close(self):
        """reader 스레드를 완전히 종료할 때만 사용한다."""
        self.reader_stop.set()
        self._drop_port()

    def _drop_port(self):
        """reader_stop을 건드리지 않고 포트만 닫는다. 재연결 루프가 살아있게 유지."""
        with self._port_lock:
            try:
                if self.ser:
                    self.ser.close()
            except Exception:
                pass
            self.ser = None
        diag.serial_connected = False

    def send_raw(self, text: str):
        if not SERIAL_ENABLED:
            return False
        with self._port_lock:
            ser = self.ser
        if ser is None or not ser.is_open:
            self.connect()  # 0502처럼 즉시 재연결 시도
            with self._port_lock:
                ser = self.ser
            if ser is None or not ser.is_open:
                return False
        try:
            ser.write(text.encode('ascii', errors='ignore'))
            ser.flush()
            diag.serial_connected = True
            diag.last_serial_write_ts = time.time()
            diag.last_serial_command = text
            diag.last_serial_error = ''
            serial_log.appendleft({'ts': time.time(), 'dir': 'TX', 'text': text})
            return True
        except Exception as e:
            diag.serial_connected = False
            diag.last_serial_error = str(e)
            serial_log.appendleft({'ts': time.time(), 'dir': 'TX', 'text': f'ERROR: {e}'})
            self._drop_port()  # reader 스레드는 유지한 채 포트만 닫음
            return False

    def send_command(self, cmd: str):
        now = time.time()
        min_interval = COMMAND_SEND_INTERVAL_MS / 1000.0
        if self.last_sent_cmd == cmd and (now - self.last_sent_ts) < min_interval:
            return False
        ok = self.send_raw(cmd)
        if ok:
            self.last_sent_cmd = cmd
            self.last_sent_ts = now
        return ok

    def reader_loop(self):
        diag.serial_reader_started = True
        buf = bytearray()
        while not self.reader_stop.is_set():
            with self._port_lock:
                ser = self.ser
            if ser is None or not ser.is_open:
                # 포트가 없으면 일정 간격으로 재연결 시도 (reader 스레드 살아있음)
                time.sleep(SERIAL_RECONNECT_DELAY)
                self.connect()
                buf.clear()
                continue
            try:
                b = ser.read(1)
                if not b:
                    continue
                if b == b'\n':
                    line = buf.decode('utf-8', errors='replace').strip()
                    buf.clear()
                    if line:
                        self.handle_line(line)
                elif b != b'\r':
                    buf.extend(b)
            except Exception as e:
                diag.last_serial_error = str(e)
                serial_log.appendleft({'ts': time.time(), 'dir': 'RX', 'text': f'ERROR: {e}'})
                self._drop_port()  # reader_stop 건드리지 않음 → 루프 유지
                buf.clear()

    def handle_line(self, line: str):
        diag.last_serial_line_ts = time.time()
        serial_log.appendleft({'ts': time.time(), 'dir': 'RX', 'text': line})
        if line.startswith('STAT,'):
            fields = {}
            for chunk in line.split(',')[1:]:
                if '=' in chunk:
                    k, v = chunk.split('=', 1)
                    fields[k.strip().lower()] = v.strip()
            with telemetry_lock:
                latest_telemetry['mode'] = fields.get('mode', latest_telemetry['mode']).upper()
                latest_telemetry['direction'] = fields.get('direction', latest_telemetry['direction']).upper()
                latest_telemetry['zone'] = fields.get('zone', latest_telemetry['zone'])
                latest_telemetry['raw'] = line
                latest_telemetry['last_update_ts'] = time.time()
                try:
                    latest_telemetry['rpm_left'] = float(fields.get('rpm_l', latest_telemetry['rpm_left']))
                except Exception:
                    pass
                try:
                    latest_telemetry['rpm_right'] = float(fields.get('rpm_r', latest_telemetry['rpm_right']))
                except Exception:
                    pass
                try:
                    latest_telemetry['jetson_age_ms'] = int(fields.get('age_ms', latest_telemetry['jetson_age_ms'] or 0))
                except Exception:
                    pass
                try:
                    latest_telemetry['manual_speed'] = int(fields.get('speed', latest_telemetry['manual_speed'] or 0))
                except Exception:
                    pass


mcu = MCUBridge()


def _open_camera() -> cv2.VideoCapture:
    """CAM_BACKEND / GST_PIPELINE 환경 변수에 따라 VideoCapture를 생성한다.

    Jetson Orin Nano에서 V4L2 기본 모드는 select() timeout이 발생할 수 있으므로
    CAM_BACKEND=gstreamer 또는 GST_PIPELINE을 설정하면 GStreamer 파이프라인을 사용한다.
    """
    if GST_PIPELINE:
        return cv2.VideoCapture(GST_PIPELINE, cv2.CAP_GSTREAMER)

    if CAM_BACKEND == 'gstreamer':
        pipeline = (
            f"v4l2src device=/dev/video{CAM_INDEX} ! "
            f"video/x-raw,width={CAP_W},height={CAP_H},framerate={CAP_FPS}/1 ! "
            f"videoconvert ! video/x-raw,format=BGR ! "
            f"appsink max-buffers=1 drop=true sync=false"
        )
        return cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)

    if CAM_BACKEND == 'v4l2':
        cap = cv2.VideoCapture(CAM_INDEX, cv2.CAP_V4L2)
    else:
        cap = cv2.VideoCapture(CAM_INDEX)

    # GStreamer 파이프라인이 아닌 경우에만 set() 호출
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAP_W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAP_H)
        cap.set(cv2.CAP_PROP_FPS, CAP_FPS)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
    return cap


def find_contours_compat(bin_img):
    out = cv2.findContours(bin_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if len(out) == 3:
        _, contours, hierarchy = out
    else:
        contours, hierarchy = out
    return contours, hierarchy


def compute_auto_threshold(gray_blur):
    t = int(np.percentile(gray_blur, AUTO_Q))
    t = t + int(AUTO_MARGIN)
    return int(np.clip(t, THRESH_MIN, THRESH_MAX))


def select_best_contour(contours):
    best = None
    best_score = -1e18
    best_meta = None
    for c in contours:
        area = cv2.contourArea(c)
        if area < MIN_AREA:
            continue
        x, y, w, h = cv2.boundingRect(c)
        if w < MIN_WIDTH or h < MIN_HEIGHT:
            continue
        cy = y + (h / 2.0)
        aspect = (w / float(h)) if h > 0 else 0.0
        inv_aspect = (h / float(w)) if w > 0 else 0.0
        elongation = max(aspect, inv_aspect)
        hull = cv2.convexHull(c)
        hull_area = cv2.contourArea(hull)
        if hull_area <= 0:
            continue
        solidity = area / hull_area
        score = (cy * 3.0) + (area * 0.01) + (elongation * 6.0) - (solidity * 2.0)
        if score > best_score:
            best_score = score
            best = c
            best_meta = (x, y, w, h, area, cy, elongation, solidity)
    return best, best_meta


def decide_command(result, lost_count):
    global latest_command
    if not result['detected'] or result['err'] is None:
        if lost_count >= LOST_LINE_STOP_FRAMES:
            return 'S'
        return latest_command
    err = int(result['err'])
    if err < -CENTER_DEADBAND:
        return 'L'
    if err > CENTER_DEADBAND:
        return 'R'
    return 'F'


def process_line(frame_bgr):
    H, W = frame_bgr.shape[:2]
    annotated = frame_bgr.copy()
    y0 = int(H * ROI_START_RATIO)
    roi = annotated[y0:H, 0:W]
    roi_h = roi.shape[0]

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    auto_t = compute_auto_threshold(blur)
    _, th = cv2.threshold(blur, auto_t, 255, cv2.THRESH_BINARY_INV)
    mask = cv2.erode(th, None, iterations=ERODE_IT)
    mask = cv2.dilate(mask, None, iterations=DILATE_IT)
    contours, _ = find_contours_compat(mask.copy())

    cx, cy = None, None
    err = None
    if contours:
        best, meta = select_best_contour(contours)
        if best is not None:
            M = cv2.moments(best)
            if M['m00'] != 0:
                cx = int(M['m10'] / M['m00'])
                cy = int(M['m01'] / M['m00'])
                err = cx - (W // 2)
                if DRAW_CONTOURS:
                    cv2.drawContours(roi, [best], -1, (0, 255, 0), 2)
                    cv2.circle(roi, (cx, cy), 4, (0, 0, 255), -1)
                    center_x = W // 2
                    cv2.line(roi, (center_x, 0), (center_x, roi_h), (255, 0, 0), 1)
                    cv2.line(roi, (cx, 0), (cx, roi_h), (0, 0, 255), 1)
                    x, y, bw, bh, _, _, _, _ = meta
                    cv2.rectangle(roi, (x, y), (x + bw, y + bh), (0, 255, 255), 1)

    if SHOW_MASK_PREVIEW:
        mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        mh, mw = mask_bgr.shape[:2]
        small_w = W // 3
        small_h = max(1, int(mh * (small_w / mw)))
        mask_small = cv2.resize(mask_bgr, (small_w, small_h), interpolation=cv2.INTER_AREA)
        roi[0:small_h, 0:small_w] = mask_small

    return annotated, {
        'detected': cx is not None,
        'cx': cx,
        'cy': cy,
        'err': err,
        'threshold': auto_t,
    }


def draw_status(frame_bgr, result, decision, tele_mode):
    annotated = frame_bgr
    H, W = annotated.shape[:2]
    y0 = int(H * ROI_START_RATIO)
    cv2.rectangle(annotated, (0, y0), (W - 1, H - 1), (255, 255, 0), 2)
    tele = get_telemetry_snapshot()

    if tele_mode != 'AUTO':
        msg1 = f'LINE TRACER PAUSED ({tele_mode})'
        msg2 = f"ATmega dir={tele['direction']} location={tele['zone']}"
        msg3 = f"RPM L:{tele['rpm_left']:.1f} R:{tele['rpm_right']:.1f}"
    else:
        msg1 = f"TH={result['threshold']} CMD={decision} DB={CENTER_DEADBAND}"
        msg2 = f"det={result['detected']} cx={result['cx']} err={result['err']} location={tele['zone']}"
        msg3 = f"ATmega dir={tele['direction']} RPM L:{tele['rpm_left']:.1f} R:{tele['rpm_right']:.1f}"

    cv2.putText(annotated, msg1, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
    cv2.putText(annotated, msg2, (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
    cv2.putText(annotated, msg3, (10, 76), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
    return annotated


def encode_stream_frame(frame_bgr):
    if (frame_bgr.shape[1], frame_bgr.shape[0]) != (STREAM_W, STREAM_H):
        frame_bgr = cv2.resize(frame_bgr, (STREAM_W, STREAM_H), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode('.jpg', frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    return buf.tobytes() if ok else None


def append_history(cmd, sent, err, source, mode):
    global latest_command
    with command_lock:
        latest_command = cmd
        command_history.appendleft({
            'ts': time.time(),
            'cmd': cmd,
            'label': cmd_to_label(cmd),
            'sent': sent,
            'err': err,
            'source': source,
            'mode': mode,
        })


def build_paused_frame(frame, tele_mode):
    result = {
        'detected': False,
        'cx': None,
        'cy': None,
        'err': None,
        'threshold': None,
        'decision': 'S',
        'vision_state': f'PAUSED_{tele_mode}',
    }
    annotated = frame.copy()
    if DRAW_DEBUG_TEXT:
        annotated = draw_status(annotated, result, 'PAUSED', tele_mode)
    return annotated, result


def camera_worker():
    global latest_jpg, latest_result
    diag.worker_started = True

    cap = _open_camera()
    while not cap.isOpened():
        diag.camera_opened = False
        diag.last_error = 'camera open failed, retrying in 3s...'
        cap.release()
        time.sleep(3.0)
        cap = _open_camera()
    diag.camera_opened = True
    diag.last_error = ''

    lost_count = 0
    last_cap = 0.0
    cap_interval = 1.0 / max(1, CAP_FPS)
    fail_streak = 0
    FAIL_REOPEN = 5  # 연속 실패 5회 → 카메라 재오픈

    while True:
        now = time.time()
        if now - last_cap < cap_interval:
            time.sleep(0.001)
            continue
        last_cap = now

        ret, frame = cap.read()
        if not ret or frame is None:
            diag.frames_fail += 1
            fail_streak += 1
            diag.camera_opened = False
            if fail_streak >= FAIL_REOPEN:
                diag.last_error = f'camera stall (fail={fail_streak}), reopening...'
                cap.release()
                time.sleep(1.0)
                cap = _open_camera()
                diag.camera_opened = cap.isOpened()
                if not cap.isOpened():
                    diag.last_error = 'camera reopen failed'
                    time.sleep(2.0)
                else:
                    diag.last_error = ''
                fail_streak = 0
            time.sleep(0.01)
            continue

        diag.camera_opened = True
        fail_streak = 0
        diag.frames_ok += 1
        diag.last_frame_ts = time.time()
        t0 = time.time()

        try:
            tele_mode = get_current_mode()
            if tele_mode != 'AUTO':
                lost_count = 0
                annotated, result = build_paused_frame(frame, tele_mode)
            else:
                annotated, result = process_line(frame)
                if result['detected']:
                    lost_count = 0
                else:
                    lost_count += 1
                decision = decide_command(result, lost_count)
                result['decision'] = decision
                result['vision_state'] = 'ACTIVE'
                sent = mcu.send_command(decision)
                append_history(decision, sent, result['err'], 'vision', tele_mode)
                if DRAW_DEBUG_TEXT:
                    annotated = draw_status(annotated, result, cmd_to_label(decision), tele_mode)
        except Exception as e:
            diag.last_error = f'frame process error: {e}'
            continue

        t1 = time.time()
        jpg = encode_stream_frame(annotated)
        t2 = time.time()

        diag.last_process_ms = round((t1 - t0) * 1000.0, 2)
        diag.last_encode_ms = round((t2 - t1) * 1000.0, 2)
        with result_lock:
            latest_result = result
        if jpg is not None:
            with jpg_lock:
                latest_jpg = jpg
        else:
            diag.last_error = 'jpeg encode failed'


def gen_frames():
    min_interval = 1.0 / max(1, STREAM_FPS)
    last_sent = 0.0
    while True:
        now = time.time()
        if now - last_sent < min_interval:
            time.sleep(0.001)
            continue
        last_sent = now
        with jpg_lock:
            jpg = latest_jpg
        if jpg is None:
            time.sleep(0.01)
            continue
        yield b'--frame\r\nContent-Type: image/jpeg\r\nCache-Control: no-cache\r\n\r\n' + jpg + b'\r\n'


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/health')
def health():
    with jpg_lock:
        has_jpg = latest_jpg is not None
        jpg_size = len(latest_jpg) if has_jpg else 0
    with result_lock:
        result = dict(latest_result)
    tele = get_telemetry_snapshot()
    with command_lock:
        history = list(command_history)
        command = latest_command
    data = asdict(diag)
    data.update({
        'has_jpg': has_jpg,
        'jpg_size': jpg_size,
        'cap_size': [CAP_W, CAP_H],
        'stream_size': [STREAM_W, STREAM_H],
        'stream_fps': STREAM_FPS,
        'jpeg_quality': JPEG_QUALITY,
        'center_deadband': CENTER_DEADBAND,
        'latest_result': result,
        'latest_command': command,
        'latest_command_label': cmd_to_label(command),
        'command_history': history,
        'telemetry': tele,
        'serial_port': SERIAL_PORT,
        'serial_enabled': SERIAL_ENABLED,
        'serial_log': list(serial_log),
        'line_tracer_enabled': tele.get('mode', 'UNKNOWN').upper() == 'AUTO',
    })
    return jsonify(data)


@app.route('/snapshot')
def snapshot():
    """최신 JPEG 프레임 1장 반환 (azure_bridge가 Azure로 전송하는 용도)."""
    with jpg_lock:
        jpg = latest_jpg
    if jpg is None:
        return Response(status=204)
    return Response(jpg, mimetype='image/jpeg',
                    headers={'Cache-Control': 'no-store'})


@app.route('/send/<cmd>', methods=['POST'])
def send_manual(cmd):
    cmd = cmd.upper()
    if cmd not in {'F', 'L', 'R', 'S', 'A', 'M'}:
        return jsonify({'ok': False, 'error': 'invalid command'}), 400
    ok = mcu.send_raw(cmd)
    append_history(cmd, ok, None, 'web', get_current_mode())
    return jsonify({'ok': ok, 'cmd': cmd, 'serial_error': diag.last_serial_error})


@app.route('/speed/<int:level>', methods=['POST'])
def set_speed(level):
    if level < 0 or level > 9:
        return jsonify({'ok': False, 'error': 'level must be 0-9'}), 400
    cmd = str(level)
    ok = mcu.send_raw(cmd)
    append_history(cmd, ok, None, 'web', get_current_mode())
    return jsonify({'ok': ok, 'level': level, 'serial_error': diag.last_serial_error})


@app.route('/speed/up', methods=['POST'])
def speed_up():
    ok = mcu.send_raw('+')
    append_history('+', ok, None, 'web', get_current_mode())
    return jsonify({'ok': ok, 'serial_error': diag.last_serial_error})


@app.route('/speed/down', methods=['POST'])
def speed_down():
    ok = mcu.send_raw('-')
    append_history('-', ok, None, 'web', get_current_mode())
    return jsonify({'ok': ok, 'serial_error': diag.last_serial_error})


if __name__ == '__main__':
    mcu.connect()
    threading.Thread(target=camera_worker, daemon=True).start()
    app.run(host=HTTP_HOST, port=HTTP_PORT, debug=False, threaded=True)
