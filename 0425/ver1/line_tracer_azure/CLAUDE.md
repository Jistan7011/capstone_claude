# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

라인 트레이서 로봇 + 웹 대시보드로 구성된 이중 마이크로컨트롤러 프로젝트.
- **ATmega128**: 실시간 모터 제어, RFID 감지, 엔코더 RPM 측정
- **Jetson Nano**: OpenCV 라인 감지(컴퓨터 비전) + Flask 웹 서버(모니터링/제어)
- **Azure VM**: 원격 브라우저 ↔ Jetson 간 Socket.IO 중계 서버 (Docker)

## 빌드 및 실행 명령어

### ATmega128 펌웨어 (`atmega128/`)
```bash
make              # 컴파일 → line_tracer.hex
make clean
avrdude -p atmega128 -c usbtiny -U flash:w:line_tracer.hex
```
툴체인: `avr-gcc`, `avr-objcopy`, MCU=atmega128, F_CPU=16MHz

### Jetson Nano 서버 (`jetson_nano/`)
```bash
pip install -r jetson_nano/requirements.txt
python jetson_nano/app.py          # Flask 서버 시작 (기본 포트 8080)
# 대시보드: http://<jetson_ip>:8080
```

환경 변수로 설정 (`jetson_nano/.env.example` 참조). 주요 변수:
- `SERIAL_ENABLED=0` — 시리얼 비활성화 (PC 테스트 시)
- `CAM_INDEX=0` — 카메라 디바이스 인덱스
- `HTTP_PORT=8080`
- `AUTO_ON_START=1` — 부팅 시 자동으로 'A' (AUTO 모드) 전송

### Azure 브릿지 (`jetson_nano/azure_bridge.py`)
```bash
pip install requests "python-socketio[client]>=5"

# 1. 로컬 서버 먼저 실행
SERIAL_ENABLED=1 python jetson_nano/app.py &

# 2. Azure 브릿지 실행
AZURE_URL=http://20.196.194.107 python jetson_nano/azure_bridge.py
```

환경 변수:
- `AZURE_URL` — Azure VM 주소 (필수)
- `LOCAL_URL` — 로컬 app.py 주소 (기본값: `http://127.0.0.1:8080`)
- `TELEMETRY_INTERVAL` — 텔레메트리 전송 간격 초 (기본값: `1.0`)
- `FRAME_INTERVAL` — 영상 프레임 전송 간격 초 (기본값: `0.25`, ~4fps)
- `HEARTBEAT_INTERVAL` — `jetson_hello` 재전송 주기 초 (기본값: `10.0`)

### Azure 중계 서버 (`azure/`)
```bash
cd azure
cp .env.example .env        # SECRET_KEY 변경 필수

# Docker 빌드 및 실행 (외부 80 → 내부 8000)
docker build -t line-tracer-server .
docker run -d --name line-tracer --restart unless-stopped \
  -p 80:8000 --env-file .env line-tracer-server

# 코드 업데이트 후 재배포
docker build -t line-tracer-server . && \
docker stop line-tracer && docker rm line-tracer && \
docker run -d --name line-tracer --restart unless-stopped \
  -p 80:8000 --env-file .env line-tracer-server

# 로그 확인
docker logs -f line-tracer
```
- **eventlet 워커 1개 고정 필수** — Socket.IO 공유 상태(`jetson_sid`, `latest_telemetry`)가 단일 프로세스에서만 유지됨
- Azure VM 방화벽: NSG에서 포트 80 인바운드 허용 필요

## 아키텍처

### 전체 통신 흐름

```
[원격 브라우저]
      │ HTTP + WebSocket (Socket.IO)
      ▼
[Azure VM: azure/server.py]  포트 80 (Docker, 내부 8000)
      │ WebSocket (Socket.IO)
      ▼
[Jetson Nano: jetson_nano/azure_bridge.py]
      │ HTTP localhost:8080
      ▼
[Jetson Nano: jetson_nano/app.py]
      │ UART0 115200 baud
      ▼
[ATmega128]
```

### ATmega ↔ Jetson 직렬 통신 프로토콜
- **Jetson → ATmega**: 단일 문자 명령 — `A` (AUTO), `M` (MANUAL), `F` (전진), `L` (좌회전), `R` (우회전), `S` (정지), `0`–`9` (속도 레벨), `+` / `-` (속도 증감)
- **ATmega → Jetson**: 텔레메트리 프레임 (약 1초마다, 또는 상태 변경 시):
  ```
  STAT,mode=AUTO,direction=FORWARD,rpm_l=12.3,rpm_r=15.1,zone=A구역,age_ms=40,speed=0\r\n
  ```
- ATmega는 추가로 **UART1**(블루투스), **I2C/TWI**(PN532 RFID) 사용

### ATmega128 (`atmega128/main.c`, `pn532.c`)
- **메인 루프**: 10ms 틱; 제어 로직 50ms마다; 텔레메트리 1초마다 (dirty 플래그 시 최소 200ms 간격)
- **모터 제어**: Timer0(OCR0=좌측)·Timer2(OCR2=우측) PWM; 방향 핀 PB5/PA3
- **엔코더 RPM**: PE7/PE5(좌)·PD4/PD5(우) 타이머 캡처; 3점 이동 평균 필터; 200ms 동안 펄스 없으면 RPM=0
- **RFID**: I2C 200ms 폴링; 700ms 디바운스 홀드; UID→구역 매핑 하드코딩 (A/B/C/D 구역)
- **명령 타임아웃**: Jetson 명령이 300ms 이상 없으면 → STOP (연결 끊김 시 폭주 방지)
- **AUTO 속도**: BASE_SPEED=120, TURN_SPEED=60 / **MANUAL 속도**: 블루투스 수신값 (0–255)

### Jetson Nano (`jetson_nano/app.py`)
3개의 스레드로 동작:
1. **카메라 워커** — 프레임 캡처 → 라인 감지 → ATmega에 명령 전송; 30회 연속 실패 시 카메라 재오픈
2. **시리얼 리더** — `STAT,...` 텔레메트리 파싱 → `latest_telemetry` 갱신
3. **Flask 서버** — 대시보드 및 API 엔드포인트 제공

**비전 파이프라인** (프레임당):
1. 640×480 캡처; 하단 35% ROI 크롭 (`ROI_START_RATIO=0.65`)
2. 그레이스케일 → 가우시안 블러 (5×5)
3. 자동 임계값: 10th 퍼센타일 픽셀값 + 마진 20 → 이진 반전
4. Erode 1회 + Dilate 2회 → 컨투어 탐색
5. 컨투어 점수화: `(cy×3) + (area×0.01) + (elongation×6) - (solidity×2)`; 최소 면적=120
6. 오차 = 컨투어 cx − 프레임 중앙; 데드밴드 ±40px → F/L/R/S 명령 결정
7. 3프레임 연속 라인 미감지 → STOP

**HTTP 엔드포인트**:
- `GET /` — 대시보드 (index.html)
- `GET /video_feed` — MJPEG 스트림 (로컬 접속용)
- `GET /snapshot` — 최신 JPEG 프레임 1장 반환 (`azure_bridge`가 Azure로 전송하는 용도)
- `GET /health` — JSON: 텔레메트리, 비전 상태, 명령 이력, 시리얼 로그
- `POST /send/<cmd>` — 명령 직접 주입 (F/L/R/S/A/M)
- `POST /speed/<0-9>` — 속도 레벨 설정
- `POST /speed/up` / `POST /speed/down` — 속도 증감

**스레드 안전성**: `jpg_lock`, `result_lock`, `telemetry_lock`, `command_lock`으로 공유 상태 보호.

### Azure 중계 서버 (`azure/server.py`)
Socket.IO 이벤트 흐름:
- **Jetson → 서버**: `jetson_hello` (등록), `telemetry` (텔레메트리 Push), `frame` (base64 JPEG Push)
- **브라우저 → 서버 → Jetson**: `command` (`{cmd: 'F'|'L'|'R'|'S'|'A'|'M'}`), `speed` (`{level: 0-9}` 또는 `{dir: 'up'|'down'}`)
- **서버 → 브라우저**: `telemetry_update`, `frame_update`, `jetson_status ({connected: bool})`

`jetson_sid`로 현재 연결된 Jetson을 추적. `telemetry`/`frame` 수신 시 `jetson_hello`가 유실된 경우에도 자동 등록.

### 웹 대시보드
- **로컬** (`jetson_nano/templates/index.html`): `/health`를 1초마다 폴링; MJPEG 스트림 직접 수신
- **Azure** (`azure/templates/index.html`): Socket.IO로 실시간 텔레메트리·영상 수신; 버튼 명령은 `command`/`speed` 이벤트로 전송
