# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

딸기 농장 관리를 위한 IoT + 로봇 + AI 통합 시스템 (Capstone Project).  
ATmega128 마이컨트롤러, Jetson Nano 엣지 컴퓨팅, Azure 클라우드 백엔드, OpenAI 기반 RAG 챗봇으로 구성된 다층 아키텍처.

## System Architecture

```
[ATmega128]  ←UART 115200→  [Jetson Nano]  ←Socket.IO→  [Azure Server]  ←WebSocket→  [Web Browser]
 (모터/RFID)                  (라인추적/영상)               (중앙 허브/DB)                (대시보드)
                                                                ↕
                                                         [RAG Chatbot]
                                                       (ChromaDB + GPT-4)
```

**Data flow**: 카메라 → 라인감지 → ATmega 명령 → Jetson → Azure → SQLite + YOLO 요약 → RAG 분석

**Socket.IO 이벤트**:
- Jetson → Azure: `jetson_hello`, `telemetry`, `frame`
- Azure → Web: `service_status`, `jetson_status`, `yolo_status`, `telemetry_update`
- Web → Azure → Jetson: `command`, `speed`

## Build & Run Commands

### ATmega128 펌웨어
```bash
cd atmega128
make          # AVR-GCC 컴파일 → main.hex 생성
make clean    # 빌드 아티팩트 정리
```

### Jetson Nano (터미널 3개 필요)
```bash
# 터미널 1: 라인추적 서버
CAM_INDEX=2 CAM_BACKEND=v4l2 CAP_W=320 CAP_H=240 CAP_FPS=20 HTTP_PORT=8080 python3 app.py

# 터미널 2: Azure 브릿지 (app.py 실행 후)
AZURE_URL=http://20.196.194.107 LOCAL_URL=http://127.0.0.1:8080 python3 azure_bridge.py

# 터미널 3: YOLO 검출 서버 (Docker)
sudo docker run -it --rm --runtime nvidia --network host --ipc=host \
  --privileged --device /dev/video0 \
  -v /dev/bus/usb:/dev/bus/usb -v /home/capstone:/workspace \
  ultralytics/ultralytics:latest-jetson-jetpack6 /bin/bash
```

### Azure 클라우드 서버
```bash
cd azure
pip install -r requirements.txt

# RAG 지식베이스 ChromaDB에 인제스트 (최초 1회 또는 문서 변경 시)
OPENAI_API_KEY="sk-..." python3 rag_bot/ingest.py

# Flask 서버 실행
PORT=80 python3 server.py

# (선택) Streamlit RAG UI
streamlit run rag_bot/app.py
```

## Key Configuration

### Jetson Nano 환경변수 (`jetson_nano/app.py`)
| 변수 | 기본값 | 설명 |
|------|--------|------|
| `CAM_INDEX` | 0 | 카메라 디바이스 (0=/dev/video0, 2=/dev/video2) |
| `CENTER_DEADBAND` | 40 | 라인 오프셋 임계값 (px) |
| `SERIAL_PORT` | `/dev/ttyUSB0` | ATmega UART 포트 |
| `HTTP_PORT` | 8080 | Flask 서버 포트 |

### Azure 서버 환경변수 (`azure/server.py`)
| 변수 | 기본값 | 설명 |
|------|--------|------|
| `PORT` | 8000 | Flask 포트 (프로덕션: 80) |
| `YOLO_SUMMARY_INTERVAL_SECONDS` | 900 | YOLO 자동 요약 주기 (15분) |

### RAG 챗봇 (`azure/rag_bot/config.py`)
- `OPENAI_API_KEY` 필수 (환경변수로 설정)
- `EMBEDDING_MODEL`: `text-embedding-3-small`
- `CHAT_MODEL`: `gpt-4o-mini`
- 지식베이스: `azure/rag_bot/data/` (딸기 병해 관련 한국어 문서)
- 벡터DB: `azure/rag_bot/db/` (런타임 생성, git 미추적)

## Module Responsibilities

- **`atmega128/main.c`**: 라인추적(PWM 모터), RFID 존 감지(PN532 I2C), UART 명령 수신
- **`jetson_nano/app.py`**: 카메라 캡처 루프(스레드), CV2 라인감지, ATmega 명령 전송, MJPEG 스트리밍
- **`jetson_nano/azure_bridge.py`**: Jetson의 텔레메트리/프레임을 Azure로 Socket.IO 릴레이
- **`azure/server.py`**: Socket.IO 허브, SQLite YOLO 로그, 15분 요약 워커, 웹 대시보드
- **`azure/db.py`**: `detections`, `detection_summary` 테이블 CRUD
- **`azure/rag_bot/rag_core.py`**: LangChain 검색 체인 (ChromaDB → GPT-4)
- **`azure/rag_bot/ingest.py`**: 문서 → 청크 → 임베딩 → ChromaDB 저장

## ATmega128 하드웨어 상수
- UART 속도: 115200 baud
- PPR (Pulses Per Revolution): 95
- RFID I2C 주소: 0x24 (PN532)
- RFID 존: 4개 태그 → 위치 존 매핑
- 모터 PWM: Timer0(좌), Timer2(우) / 엔코더: Timer1(우), Timer3(좌)
