from ultralytics import YOLO
import cv2
import time
import requests
import json
from datetime import datetime

MODEL_PATH = "/workspace/capstone/best.pt"
CAMERA_INDEX = 0
IMG_SIZE = 640

AZURE_UPLOAD_URL = "http://20.196.194.107/upload_frame"
DEVICE_ID = "jetson_orin_01"

model = YOLO(MODEL_PATH)

cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
cap.set(cv2.CAP_PROP_FPS, 30)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

if not cap.isOpened():
    raise RuntimeError(f"카메라를 열 수 없습니다. CAMERA_INDEX={CAMERA_INDEX}")

prev_time = time.time()
frame_id = 0

while True:
    ok, frame = cap.read()
    if not ok:
        continue

    results = model.predict(
        source=frame,
        imgsz=IMG_SIZE,
        conf=0.25,
        verbose=False
    )

    result = results[0]
    annotated = result.plot()

    now = time.time()
    fps = 1.0 / (now - prev_time) if now != prev_time else 0.0
    prev_time = now
    frame_id += 1

    cv2.putText(
        annotated,
        f"FPS: {fps:.2f}",
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        (0, 255, 0),
        2
    )

    detections = []

    if result.boxes is not None:
        for box in result.boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())

            detections.append({
                "class_name": model.names[cls_id],
                "confidence": conf,
                "bbox": [x1, y1, x2, y2]
            })

    ret, buffer = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not ret:
        continue

    metadata = {
        "device_id": DEVICE_ID,
        "timestamp": datetime.now().isoformat(),
        "frame_id": frame_id,
        "fps": fps,
        "detections": detections
    }

    try:
        response = requests.post(
            AZURE_UPLOAD_URL,
            data={"metadata": json.dumps(metadata)},
            files={"frame": ("frame.jpg", buffer.tobytes(), "image/jpeg")},
            timeout=1.0
        )

        if response.status_code != 200:
            print(f"업로드 실패: {response.status_code}, {response.text}")
        else:
            print(f"[전송 성공] frame_id={frame_id}, detections={len(detections)}")

    except Exception as e:
        print(f"Azure 전송 오류: {e}")