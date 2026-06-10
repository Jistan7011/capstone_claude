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


# ��������������������������������������������������������������������������������������������������������������������������
# Environment
# ��������������������������������������������������������������������������������������������������������������������������

CAM_INDEX = int(os.getenv('CAM_INDEX', '0'))
CAM_BACKEND = os.getenv('CAM_BACKEND', 'auto')   # 'auto' | 'v4l2' | 'gstreamer'
GST_PIPELINE = os.getenv('GST_PIPELINE', '')

CAP_W = int(os.getenv('CAP_W', '640'))
CAP_H = int(os.getenv('CAP_H', '480'))
CAP_FPS = int(os.getenv('CAP_FPS', '20'))

STREAM_W = int(os.getenv('STREAM_W', '480'))
STREAM_H = int(os.getenv('STREAM_H', '360'))
STREAM_FPS = int(os.getenv('STREAM_FPS', '8'))
JPEG_QUALITY = int(os.getenv('JPEG_QUALITY', '55'))

HTTP_HOST = os.getenv('HTTP_HOST', '0.0.0.0')
HTTP_PORT = int(os.getenv('HTTP_PORT', '8080'))


# ��������������������������������������������������������������������������������������������������������������������������
# Line tracer parameters
# ��������������������������������������������������������������������������������������������������������������������������

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


# ��������������������������������������������������������������������������������������������������������������������������
# RPM / PID parameters
# ��������������������������������������������������������������������������������������������������������������������������

BASE_RPM = int(os.getenv('BASE_RPM', '50'))
MIN_RPM = int(os.getenv('MIN_RPM', '15'))
MAX_RPM = int(os.getenv('MAX_RPM', '100'))

STEER_RPM_KP = float(os.getenv('STEER_RPM_KP', '0.20'))
MAX_STEER_RPM = int(os.getenv('MAX_STEER_RPM', '30'))

LINE_PID_KP = float(os.getenv('LINE_PID_KP', str(STEER_RPM_KP)))
LINE_PID_KI = float(os.getenv('LINE_PID_KI', '0.0'))
LINE_PID_KD = float(os.getenv('LINE_PID_KD', '0.08'))
LINE_PID_I_LIMIT = float(os.getenv('LINE_PID_I_LIMIT', '300.0'))


# ��������������������������������������������������������������������������������������������������������������������������
# Debug / serial parameters
# ��������������������������������������������������������������������������������������������������������������������������

DRAW_DEBUG_TEXT = os.getenv('DRAW_DEBUG_TEXT', '1') == '1'
DRAW_CONTOURS = os.getenv('DRAW_CONTOURS', '1') == '1'
SHOW_MASK_PREVIEW = os.getenv('SHOW_MASK_PREVIEW', '0') == '1'

SERIAL_ENABLED = os.getenv('SERIAL_ENABLED', '1') == '1'
SERIAL_PORT = os.getenv('SERIAL_PORT', '/dev/ttyUSB0')
SERIAL_BAUD = int(os.getenv('SERIAL_BAUD', '115200'))
SERIAL_TIMEOUT = float(os.getenv('SERIAL_TIMEOUT', '0.05'))

AUTO_ON_START = os.getenv('AUTO_ON_START', '1') == '1'
COMMAND_SEND_INTERVAL_MS = int(os.getenv('COMMAND_SEND_INTERVAL_MS', '80'))


# ��������������������������������������������������������������������������������������������������������������������������
# Shared state
# ��������������������������������������������������������������������������������������������������������������������������

latest_jpg = None

jpg_lock = threading.Lock()
result_lock = threading.Lock()
telemetry_lock = threading.Lock()
command_lock = threading.Lock()
mode_lock = threading.Lock()

requested_mode = 'AUTO' if AUTO_ON_START else 'MANUAL'

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
    'mode': requested_mode,
    'direction': 'STOP',
    'rpm_left': 0.0,
    'rpm_right': 0.0,
    'zone': '-',
    'jetson_age_ms': None,
    'manual_speed': None,
    'last_update_ts': 0.0,
    'raw': '',
}

command_history = deque(maxlen=60)
serial_log = deque(maxlen=80)
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


# ��������������������������������������������������������������������������������������������������������������������������
# Utility
# ��������������������������������������������������������������������������������������������������������������������������

def cmd_to_label(cmd: str) -> str:
    mapping = {
        'F': 'FORWARD',
        'L': 'LEFT',
        'R': 'RIGHT',
        'S': 'STOP',
        'A': 'AUTO',
        'M': 'MANUAL',
        '+': 'SPEED_UP',
        '-': 'SPEED_DOWN',
    }

    if cmd.startswith('T,'):
        return 'TARGET_RPM'

    return mapping.get(cmd, cmd)


def set_requested_mode(mode: str):
    global requested_mode

    mode = str(mode).upper()

    if mode not in {'AUTO', 'MANUAL', 'UNKNOWN'}:
        return

    with mode_lock:
        requested_mode = mode

    with telemetry_lock:
        latest_telemetry['mode'] = mode
        latest_telemetry['last_update_ts'] = time.time()

    diag.auto_mode_requested = (mode == 'AUTO')


def get_requested_mode() -> str:
    with mode_lock:
        return str(requested_mode).upper()


def get_telemetry_snapshot():
    with telemetry_lock:
        return dict(latest_telemetry)


def get_current_mode() -> str:
    """
    Jetson �쒖뼱 湲곗� 紐⑤뱶.
    ATmega�� STAT telemetry媛� ��쾶 �ㅻ뜑�쇰룄,
    /send/A �먮뒗 /send/M�쇰줈 �붿껌�� 紐⑤뱶瑜� �곗꽑 �ъ슜�쒕떎.
    """
    mode = get_requested_mode()

    if mode in {'AUTO', 'MANUAL'}:
        return mode

    tele_mode = str(get_telemetry_snapshot().get('mode', 'UNKNOWN')).upper()
    return tele_mode


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


# ��������������������������������������������������������������������������������������������������������������������������
# PID Controller
# ��������������������������������������������������������������������������������������������������������������������������

class PIDController:
    def __init__(self, kp=0.20, ki=0.0, kd=0.08, integral_limit=300.0):
        self.kp = float(kp)
        self.ki = float(ki)
        self.kd = float(kd)
        self.integral_limit = float(integral_limit)

        self.prev_err = 0.0
        self.integral = 0.0
        self.prev_ts = None

    def reset(self):
        self.prev_err = 0.0
        self.integral = 0.0
        self.prev_ts = None

    def update(self, err):
        now = time.time()
        err = float(err)

        if self.prev_ts is None:
            dt = 0.0
        else:
            dt = max(now - self.prev_ts, 1e-3)

        self.prev_ts = now

        if dt > 0.0:
            self.integral += err * dt
            self.integral = float(np.clip(
                self.integral,
                -self.integral_limit,
                self.integral_limit
            ))
            derivative = (err - self.prev_err) / dt
        else:
            derivative = 0.0

        self.prev_err = err

        control = (
            self.kp * err
            + self.ki * self.integral
            + self.kd * derivative
        )

        return control


line_pid = PIDController(
    kp=LINE_PID_KP,
    ki=LINE_PID_KI,
    kd=LINE_PID_KD,
    integral_limit=LINE_PID_I_LIMIT,
)


def compute_target_rpm_pid(err):
    """
    Jetson 移대찓�� �쇱씤 �ㅼ감 err瑜� 醫뚯슦 紐⑺몴 RPM�쇰줈 蹂���.
    err > 0�대㈃ �쇱씤�� �붾㈃ �ㅻⅨ履쎌뿉 �덉쓬.
    left_rpm 利앷�, right_rpm 媛먯냼濡� �ㅻⅨ履� �뚯쟾 �깅텇�� 留뚮뱺��.
    """
    if err is None:
        line_pid.reset()
        return 0, 0

    err = float(err)

    if abs(err) <= CENTER_DEADBAND:
        control = 0.0
        line_pid.prev_err = err
    else:
        control = line_pid.update(err)

    steer = int(np.clip(control, -MAX_STEER_RPM, MAX_STEER_RPM))

    left_rpm = BASE_RPM + steer
    right_rpm = BASE_RPM - steer

    left_rpm = int(np.clip(left_rpm, MIN_RPM, MAX_RPM))
    right_rpm = int(np.clip(right_rpm, MIN_RPM, MAX_RPM))

    return left_rpm, right_rpm


# ��������������������������������������������������������������������������������������������������������������������������
# MCU Serial Bridge
# ��������������������������������������������������������������������������������������������������������������������������

class MCUBridge:
    def __init__(self):
        self.ser = None
        self.last_sent_cmd = None
        self.last_sent_ts = 0.0
        self.reader_thread = None
        self.reader_stop = threading.Event()

    def connect(self):
        if not SERIAL_ENABLED:
            diag.serial_connected = False
            diag.last_serial_error = 'serial disabled'
            return

        if serial is None:
            diag.last_serial_error = 'pyserial not installed'
            diag.serial_connected = False
            return

        if self.ser is not None and self.ser.is_open:
            return

        try:
            self.ser = serial.Serial(
                SERIAL_PORT,
                SERIAL_BAUD,
                timeout=SERIAL_TIMEOUT
            )

            time.sleep(2.0)

            diag.serial_connected = True
            diag.last_serial_error = ''

            self.start_reader()

            if AUTO_ON_START:
                ok = self.send_raw('A')
                if ok:
                    set_requested_mode('AUTO')

        except Exception as e:
            self.ser = None
            diag.serial_connected = False
            diag.last_serial_error = str(e)

    def start_reader(self):
        if self.reader_thread and self.reader_thread.is_alive():
            return

        self.reader_stop.clear()
        self.reader_thread = threading.Thread(
            target=self.reader_loop,
            daemon=True
        )
        self.reader_thread.start()

    def close(self):
        self.reader_stop.set()

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

        if self.ser is None or not self.ser.is_open:
            self.connect()

            if self.ser is None or not self.ser.is_open:
                return False

        try:
            payload = str(text).encode('ascii', errors='ignore')
            self.ser.write(payload)
            self.ser.flush()

            diag.serial_connected = True
            diag.last_serial_write_ts = time.time()
            diag.last_serial_command = str(text)
            diag.last_serial_error = ''

            serial_log.appendleft({
                'ts': time.time(),
                'dir': 'TX',
                'text': str(text),
            })

            return True

        except Exception as e:
            diag.serial_connected = False
            diag.last_serial_error = str(e)

            serial_log.appendleft({
                'ts': time.time(),
                'dir': 'TX',
                'text': f'ERROR: {e}',
            })

            self.close()
            return False

    def send_command(self, cmd: str):
        """
        湲곗〈 ATmega 臾몄옄 紐낅졊��.
        F/L/R/S/A/M/+/-
        """
        now = time.time()
        min_interval = COMMAND_SEND_INTERVAL_MS / 1000.0

        if self.last_sent_cmd == cmd and (now - self.last_sent_ts) < min_interval:
            return False

        ok = self.send_raw(cmd)

        if ok:
            self.last_sent_cmd = cmd
            self.last_sent_ts = now

        return ok

    def send_target_rpm(self, left_rpm: int, right_rpm: int):
        """
        �먮룞 �쇱씤�몃젅�댁꽌�� 紐⑺몴 RPM 紐낅졊.
        ATmega �뚯썾�닿� 諛섎뱶�� T,left,right �뺤떇�� �뚯떛�댁빞 �쒕떎.
        """
        cmd = f"T,{int(left_rpm)},{int(right_rpm)}\n"

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
            if self.ser is None or not self.ser.is_open:
                time.sleep(0.1)
                continue

            try:
                b = self.ser.read(1)

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

                serial_log.appendleft({
                    'ts': time.time(),
                    'dir': 'RX',
                    'text': f'ERROR: {e}',
                })

                self.close()
                time.sleep(1.0)
                self.connect()

    def handle_line(self, line: str):
        diag.last_serial_line_ts = time.time()

        serial_log.appendleft({
            'ts': time.time(),
            'dir': 'RX',
            'text': line,
        })

        if not line.startswith('STAT,'):
            return

        fields = {}

        for chunk in line.split(',')[1:]:
            if '=' in chunk:
                k, v = chunk.split('=', 1)
                fields[k.strip().lower()] = v.strip()

        mode = fields.get('mode', None)

        with telemetry_lock:
            if mode:
                latest_telemetry['mode'] = mode.upper()

            latest_telemetry['direction'] = fields.get(
                'direction',
                latest_telemetry['direction']
            ).upper()

            latest_telemetry['zone'] = fields.get(
                'zone',
                latest_telemetry['zone']
            )

            latest_telemetry['raw'] = line
            latest_telemetry['last_update_ts'] = time.time()

            try:
                latest_telemetry['rpm_left'] = float(fields.get(
                    'rpm_l',
                    latest_telemetry['rpm_left']
                ))
            except Exception:
                pass

            try:
                latest_telemetry['rpm_right'] = float(fields.get(
                    'rpm_r',
                    latest_telemetry['rpm_right']
                ))
            except Exception:
                pass

            try:
                latest_telemetry['jetson_age_ms'] = int(fields.get(
                    'age_ms',
                    latest_telemetry['jetson_age_ms'] or 0
                ))
            except Exception:
                pass

            try:
                latest_telemetry['manual_speed'] = int(fields.get(
                    'speed',
                    latest_telemetry['manual_speed'] or 0
                ))
            except Exception:
                pass

        if mode and mode.upper() in {'AUTO', 'MANUAL'}:
            with mode_lock:
                global requested_mode
                requested_mode = mode.upper()


mcu = MCUBridge()


# ��������������������������������������������������������������������������������������������������������������������������
# Camera / image processing
# ��������������������������������������������������������������������������������������������������������������������������

def _open_camera() -> cv2.VideoCapture:
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
    out = cv2.findContours(
        bin_img,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_NONE
    )

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

        score = (
            (cy * 3.0)
            + (area * 0.01)
            + (elongation * 6.0)
            - (solidity * 2.0)
        )

        if score > best_score:
            best_score = score
            best = c
            best_meta = (x, y, w, h, area, cy, elongation, solidity)

    return best, best_meta


def process_line(frame_bgr):
    H, W = frame_bgr.shape[:2]

    annotated = frame_bgr.copy()

    y0 = int(H * ROI_START_RATIO)
    roi = annotated[y0:H, 0:W]
    roi_h = roi.shape[0]

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)

    auto_t = compute_auto_threshold(blur)

    _, th = cv2.threshold(
        blur,
        auto_t,
        255,
        cv2.THRESH_BINARY_INV
    )

    mask = cv2.erode(th, None, iterations=ERODE_IT)
    mask = cv2.dilate(mask, None, iterations=DILATE_IT)

    contours, _ = find_contours_compat(mask.copy())

    cx = None
    cy = None
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

                    cv2.line(
                        roi,
                        (center_x, 0),
                        (center_x, roi_h),
                        (255, 0, 0),
                        1
                    )

                    cv2.line(
                        roi,
                        (cx, 0),
                        (cx, roi_h),
                        (0, 0, 255),
                        1
                    )

                    x, y, bw, bh, _, _, _, _ = meta

                    cv2.rectangle(
                        roi,
                        (x, y),
                        (x + bw, y + bh),
                        (0, 255, 255),
                        1
                    )

    if SHOW_MASK_PREVIEW:
        mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        mh, mw = mask_bgr.shape[:2]

        small_w = W // 3
        small_h = max(1, int(mh * (small_w / mw)))

        mask_small = cv2.resize(
            mask_bgr,
            (small_w, small_h),
            interpolation=cv2.INTER_AREA
        )

        roi[0:small_h, 0:small_w] = mask_small

    return annotated, {
        'detected': cx is not None,
        'cx': cx,
        'cy': cy,
        'err': err,
        'threshold': auto_t,
    }


def draw_status(frame_bgr, result, decision, mode):
    annotated = frame_bgr

    H, W = annotated.shape[:2]
    y0 = int(H * ROI_START_RATIO)

    cv2.rectangle(
        annotated,
        (0, y0),
        (W - 1, H - 1),
        (255, 255, 0),
        2
    )

    tele = get_telemetry_snapshot()

    if mode != 'AUTO':
        msg1 = f'LINE TRACER PAUSED ({mode})'
        msg2 = f"ATmega dir={tele['direction']} location={tele['zone']}"
        msg3 = f"RPM L:{tele['rpm_left']:.1f} R:{tele['rpm_right']:.1f}"
    else:
        msg1 = f"TH={result['threshold']} CMD={decision} DB={CENTER_DEADBAND}"
        msg2 = f"det={result['detected']} cx={result['cx']} err={result['err']} location={tele['zone']}"
        msg3 = f"ATmega dir={tele['direction']} RPM L:{tele['rpm_left']:.1f} R:{tele['rpm_right']:.1f}"

    cv2.putText(
        annotated,
        msg1,
        (10, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (0, 255, 255),
        2
    )

    cv2.putText(
        annotated,
        msg2,
        (10, 50),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (0, 255, 255),
        2
    )

    cv2.putText(
        annotated,
        msg3,
        (10, 76),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (0, 255, 255),
        2
    )

    return annotated


def encode_stream_frame(frame_bgr):
    if (frame_bgr.shape[1], frame_bgr.shape[0]) != (STREAM_W, STREAM_H):
        frame_bgr = cv2.resize(
            frame_bgr,
            (STREAM_W, STREAM_H),
            interpolation=cv2.INTER_AREA
        )

    ok, buf = cv2.imencode(
        '.jpg',
        frame_bgr,
        [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
    )

    return buf.tobytes() if ok else None


def build_paused_frame(frame, mode='MANUAL'):
    result = {
        'detected': False,
        'cx': None,
        'cy': None,
        'err': None,
        'threshold': None,
        'decision': 'PAUSED',
        'vision_state': f'PAUSED_{mode}',
    }

    annotated = frame.copy()

    if DRAW_DEBUG_TEXT:
        annotated = draw_status(annotated, result, 'PAUSED', mode)

    return annotated, result


# ��������������������������������������������������������������������������������������������������������������������������
# Camera worker
# ��������������������������������������������������������������������������������������������������������������������������

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
    FAIL_REOPEN = 5

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
            mode = get_current_mode()

            # �섎룞 紐⑤뱶:
            # 移대찓�� �곸긽�� 怨꾩냽 留뚮뱾吏�留� �먮룞 �쒖뼱 紐낅졊�� �덈� 蹂대궡吏� �딅뒗��.
            if mode == 'MANUAL':
                lost_count = 0
                line_pid.reset()

                annotated, result = build_paused_frame(frame, 'MANUAL')

            # �먮룞 紐⑤뱶:
            # Jetson�� �쇱씤�� 寃�異쒗븯怨� PID濡� 醫뚯슦 紐⑺몴 RPM�� 怨꾩궛�� ��
            # ATmega濡� T,left_rpm,right_rpm 紐낅졊�� 蹂대궦��.
            elif mode == 'AUTO':
                annotated, result = process_line(frame)

                if result['detected'] and result['err'] is not None:
                    lost_count = 0

                    left_rpm, right_rpm = compute_target_rpm_pid(result['err'])

                    decision = f"T,{left_rpm},{right_rpm}"
                    sent = mcu.send_target_rpm(left_rpm, right_rpm)

                else:
                    lost_count += 1
                    line_pid.reset()

                    if lost_count >= LOST_LINE_STOP_FRAMES:
                        decision = "T,0,0"
                        sent = mcu.send_target_rpm(0, 0)
                    else:
                        decision = latest_command
                        sent = False

                result['decision'] = decision
                result['vision_state'] = 'ACTIVE'

                append_history(
                    decision,
                    sent,
                    result.get('err'),
                    'vision',
                    mode
                )

                if DRAW_DEBUG_TEXT:
                    annotated = draw_status(
                        annotated,
                        result,
                        decision,
                        mode
                    )

            # UNKNOWN ��:
            # �덉쟾�� �꾪빐 �먮룞 紐낅졊�� 蹂대궡吏� �딅뒗��.
            else:
                lost_count = 0
                line_pid.reset()

                annotated, result = build_paused_frame(frame, mode)
                result['vision_state'] = f'PAUSED_{mode}'

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


# ��������������������������������������������������������������������������������������������������������������������������
# Flask routes
# ��������������������������������������������������������������������������������������������������������������������������

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

        yield (
            b'--frame\r\n'
            b'Content-Type: image/jpeg\r\n'
            b'Cache-Control: no-cache\r\n\r\n'
            + jpg
            + b'\r\n'
        )


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/video_feed')
def video_feed():
    return Response(
        gen_frames(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )


@app.route('/snapshot')
def snapshot():
    with jpg_lock:
        jpg = latest_jpg

    if jpg is None:
        return Response(status=204)

    return Response(
        jpg,
        mimetype='image/jpeg',
        headers={'Cache-Control': 'no-store'}
    )


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

        'base_rpm': BASE_RPM,
        'min_rpm': MIN_RPM,
        'max_rpm': MAX_RPM,
        'max_steer_rpm': MAX_STEER_RPM,

        'line_pid_kp': LINE_PID_KP,
        'line_pid_ki': LINE_PID_KI,
        'line_pid_kd': LINE_PID_KD,

        'requested_mode': get_requested_mode(),
        'effective_mode': get_current_mode(),

        'latest_result': result,
        'latest_command': command,
        'latest_command_label': cmd_to_label(command),

        'command_history': history,
        'telemetry': tele,

        'serial_port': SERIAL_PORT,
        'serial_enabled': SERIAL_ENABLED,
        'serial_log': list(serial_log),

        'line_tracer_enabled': get_current_mode() == 'AUTO',
    })

    return jsonify(data)


@app.route('/send/<cmd>', methods=['POST'])
def send_manual(cmd):
    """
    Azure bridge �먮뒗 濡쒖뺄 �뱀뿉�� �ㅼ뼱�ㅻ뒗 紐낅졊 泥섎━.

    A: AUTO 紐⑤뱶 �꾪솚
    M: MANUAL 紐⑤뱶 �꾪솚
    S: �뺤�. �덉쟾�� �대뒓 紐⑤뱶�먯꽌�� �덉슜
    F/L/R: MANUAL 紐⑤뱶�먯꽌留� �덉슜
    """

    cmd = cmd.upper()

    if cmd not in {'F', 'L', 'R', 'S', 'A', 'M'}:
        return jsonify({
            'ok': False,
            'error': 'invalid command',
            'cmd': cmd,
        }), 400

    mode_before = get_current_mode()

    # AUTO 紐⑤뱶 �꾪솚
    if cmd == 'A':
        line_pid.reset()
        ok = mcu.send_command('A')

        if ok:
            set_requested_mode('AUTO')

        append_history(cmd, ok, None, 'web', mode_before)

        return jsonify({
            'ok': ok,
            'cmd': cmd,
            'mode_before': mode_before,
            'mode_after': get_current_mode(),
            'serial_error': diag.last_serial_error,
        })

    # MANUAL 紐⑤뱶 �꾪솚
    if cmd == 'M':
        line_pid.reset()

        # �먮룞 PID媛� �④릿 紐⑺몴 RPM�� 癒쇱� 0�쇰줈 留뚮뱾怨� �섎룞 �꾪솚
        mcu.send_target_rpm(0, 0)
        time.sleep(0.02)

        ok = mcu.send_command('M')

        if ok:
            set_requested_mode('MANUAL')

        append_history(cmd, ok, None, 'web', mode_before)

        return jsonify({
            'ok': ok,
            'cmd': cmd,
            'mode_before': mode_before,
            'mode_after': get_current_mode(),
            'serial_error': diag.last_serial_error,
        })

    # STOP�� �대뒓 紐⑤뱶�먯꽌�� �덉슜
    if cmd == 'S':
        line_pid.reset()

        ok = mcu.send_command('S')

        append_history(cmd, ok, None, 'web', mode_before)

        return jsonify({
            'ok': ok,
            'cmd': cmd,
            'mode': get_current_mode(),
            'serial_error': diag.last_serial_error,
        })

    # F/L/R�� MANUAL 紐⑤뱶�먯꽌留� �덉슜
    current_mode = get_current_mode()

    if current_mode != 'MANUAL':
        append_history(
            cmd,
            False,
            'ignored_not_manual',
            'web',
            current_mode
        )

        return jsonify({
            'ok': False,
            'cmd': cmd,
            'ignored': True,
            'reason': 'F/L/R commands are allowed only in MANUAL mode',
            'mode': current_mode,
        }), 409

    ok = mcu.send_command(cmd)

    append_history(cmd, ok, None, 'web', current_mode)

    return jsonify({
        'ok': ok,
        'cmd': cmd,
        'mode': current_mode,
        'serial_error': diag.last_serial_error,
    })


@app.route('/speed/<int:level>', methods=['POST'])
def set_speed(level):
    if level < 0 or level > 9:
        return jsonify({
            'ok': False,
            'error': 'level must be 0-9',
        }), 400

    current_mode = get_current_mode()

    if current_mode != 'MANUAL':
        append_history(
            str(level),
            False,
            'ignored_not_manual',
            'web',
            current_mode
        )

        return jsonify({
            'ok': False,
            'ignored': True,
            'reason': 'speed level command is allowed only in MANUAL mode',
            'mode': current_mode,
        }), 409

    cmd = str(level)
    ok = mcu.send_command(cmd)

    append_history(cmd, ok, None, 'web', current_mode)

    return jsonify({
        'ok': ok,
        'level': level,
        'mode': current_mode,
        'serial_error': diag.last_serial_error,
    })


@app.route('/speed/up', methods=['POST'])
def speed_up():
    current_mode = get_current_mode()

    if current_mode != 'MANUAL':
        append_history(
            '+',
            False,
            'ignored_not_manual',
            'web',
            current_mode
        )

        return jsonify({
            'ok': False,
            'ignored': True,
            'reason': 'speed up command is allowed only in MANUAL mode',
            'mode': current_mode,
        }), 409

    ok = mcu.send_command('+')

    append_history('+', ok, None, 'web', current_mode)

    return jsonify({
        'ok': ok,
        'mode': current_mode,
        'serial_error': diag.last_serial_error,
    })


@app.route('/speed/down', methods=['POST'])
def speed_down():
    current_mode = get_current_mode()

    if current_mode != 'MANUAL':
        append_history(
            '-',
            False,
            'ignored_not_manual',
            'web',
            current_mode
        )

        return jsonify({
            'ok': False,
            'ignored': True,
            'reason': 'speed down command is allowed only in MANUAL mode',
            'mode': current_mode,
        }), 409

    ok = mcu.send_command('-')

    append_history('-', ok, None, 'web', current_mode)

    return jsonify({
        'ok': ok,
        'mode': current_mode,
        'serial_error': diag.last_serial_error,
    })


# ��������������������������������������������������������������������������������������������������������������������������
# Main
# ��������������������������������������������������������������������������������������������������������������������������

if __name__ == '__main__':
    mcu.connect()

    threading.Thread(
        target=camera_worker,
        daemon=True
    ).start()

    app.run(
        host=HTTP_HOST,
        port=HTTP_PORT,
        debug=False,
        threaded=True
    )
