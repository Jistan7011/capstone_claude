# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

라인 트레이서 로봇 + 웹 대시보드로 구성된 이중 마이크로컨트롤러 프로젝트.
- **ATmega128**: 실시간 모터 제어, RFID 감지, 엔코더 RPM 측정
- **Jetson Nano**: OpenCV 라인 감지(컴퓨터 비전) + Flask 웹 서버(모니터링/제어)

## 빌드 및 실행 명령어

### ATmega128 펌웨어 (`atmega128/`)
```bash
make              # 컴파일 → line_tracer.hex
make clean        # 빌드 아티팩트 삭제
# 보드에 플래싱:
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
- `SERIAL_ENABLED=0` — 시리얼 비활성화 (PC 테스트 시 사용)
- `CAM_INDEX=0` — 카메라 디바이스 인덱스
- `HTTP_PORT=8080`
- `AUTO_ON_START=1` — 부팅 시 자동으로 'A' (AUTO 모드) 전송

## 아키텍처

### 통신 프로토콜
ATmega ↔ Jetson 간 **UART0 115200 baud** 통신:
- **Jetson → ATmega**: 단일 문자 명령 — `A` (AUTO), `M` (MANUAL), `F` (전진), `L` (좌회전), `R` (우회전), `S` (정지)
- **ATmega → Jetson**: 텔레메트리 프레임 (약 1초마다, 또는 상태 변경 시):
  ```
  STAT,mode=AUTO,direction=FORWARD,rpm_l=12.3,rpm_r=15.1,zone=A구역,age_ms=40,speed=0\r\n
  ```

ATmega는 추가로 **UART1**(블루투스 모듈, 폰 제어)과 **I2C/TWI**(PN532 RFID)를 사용한다.

### ATmega128 (`atmega128/main.c`, `pn532.c`)
- **메인 루프**: 10ms 틱; 제어 로직 50ms마다; 텔레메트리 1초마다 (dirty 플래그 시 최소 200ms 간격)
- **모터 제어**: Timer0(OCR0=좌측)·Timer2(OCR2=우측) PWM; 방향 핀 PB5/PA3
- **엔코더 RPM**: PE7/PE5(좌)·PD4/PD5(우) 타이머 캡처; 3점 이동 평균 필터; 200ms 동안 펄스 없으면 RPM=0
- **RFID**: I2C 200ms 폴링; 700ms 디바운스 홀드; UID→구역 매핑 하드코딩 (A/B/C/D 구역)
- **명령 타임아웃**: Jetson 명령이 300ms 이상 없으면 → STOP (연결 끊김 시 폭주 방지)
- **AUTO 속도**: BASE_SPEED=120, TURN_SPEED=60 / **MANUAL 속도**: 블루투스 수신값 (0–255)

### Jetson Nano (`jetson_nano/app.py`)
3개의 스레드로 동작:
1. **카메라 워커** — 프레임 캡처 → 라인 감지 → ATmega에 명령 전송
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
- `GET /video_feed` — MJPEG 스트림
- `GET /health` — JSON: 텔레메트리, 비전 상태, 명령 이력, 시리얼 로그
- `POST /send/<cmd>` — 명령 직접 주입 (F/L/R/S/A/M)

**스레드 안전성**: `jpg_lock`, `result_lock`, `telemetry_lock`, `command_lock`으로 공유 상태 보호.

### 웹 대시보드 (`jetson_nano/templates/index.html`)
`/health`를 1초마다 폴링. 표시 내용: 실시간 영상, 모드/방향 버튼, ATmega 상태 카드, RFID 구역, 명령 이력, 시리얼 로그.
