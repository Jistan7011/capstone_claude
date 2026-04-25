# 라인 트레이서 사용 설명서

## 시스템 구성

### 로컬 접속 (Jetson 직접)

```
[웹 브라우저] ──HTTP──▶ [Jetson Nano : app.py + 카메라]
                                  │ UART0 (115200 baud)
                         [ATmega128 : 모터 제어]
                           ├─ UART1 ── 블루투스 모듈
                           ├─ I2C  ── PN532 RFID 리더
                           └─ PWM  ── 좌/우 모터
```

### 원격 접속 (Azure 경유)

```
[원격 브라우저] ──HTTPS/WebSocket──▶ [Azure : server.py]
                                              │ Socket.IO
                                    [Jetson : azure_bridge.py]
                                              │ HTTP localhost
                                    [Jetson : app.py + 카메라]
                                              │ UART0
                                         [ATmega128]
```

---

## 1. ATmega128 펌웨어 빌드 및 업로드

```bash
cd atmega128
make                          # 컴파일 → line_tracer.hex 생성
avrdude -p atmega128 -c usbtiny -U flash:w:line_tracer.hex
```

---

## 2. 로컬 서버 (Jetson Nano)

### 의존성 설치 (최초 1회)

```bash
pip install -r jetson_nano/requirements.txt
```

### 서버 시작

```bash
python jetson_nano/app.py
```

### 대시보드 접속

| 접속 방법 | 주소 |
|-----------|------|
| Jetson 로컬 | `http://127.0.0.1:8080` |
| 같은 네트워크 | `http://<Jetson_IP>:8080` |

### 주요 환경 변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `CAM_INDEX` | `0` | 카메라 디바이스 번호 |
| `HTTP_PORT` | `8080` | 웹 서버 포트 |
| `SERIAL_PORT` | `/dev/ttyUSB0` | ATmega 시리얼 포트 |
| `SERIAL_ENABLED` | `1` | `0` 설정 시 시리얼 없이 실행 (PC 테스트용) |
| `AUTO_ON_START` | `1` | 부팅 시 자동으로 AUTO 모드 전환 |

```bash
# PC에서 하드웨어 없이 테스트
SERIAL_ENABLED=0 python jetson_nano/app.py
```

---

## 3. Azure 원격 서버 배포

> 로컬 테스트만 사용한다면 이 항목은 건너뜀.

### 사전 준비

[Azure CLI 설치](https://docs.microsoft.com/cli/azure/install-azure-cli) 후 로그인:

```bash
az login
```

### App Service 배포 (권장)

```bash
# 1. 리소스 그룹
az group create --name line-tracer-rg --location koreacentral

# 2. App Service 플랜 (B1 이상 필수 — F1 무료 티어는 WebSocket 미지원)
az appservice plan create \
  --name line-tracer-plan \
  --resource-group line-tracer-rg \
  --sku B1 --is-linux

# 3. 웹앱 생성 (<unique-name>은 전 세계 고유값으로 변경)
az webapp create \
  --name line-tracer-<unique-name> \
  --resource-group line-tracer-rg \
  --plan line-tracer-plan \
  --runtime "PYTHON:3.11"

# 4. WebSocket 활성화
az webapp config set \
  --name line-tracer-<unique-name> \
  --resource-group line-tracer-rg \
  --web-sockets-enabled true

# 5. 시작 명령 설정 (eventlet 워커 1개 고정)
az webapp config set \
  --name line-tracer-<unique-name> \
  --resource-group line-tracer-rg \
  --startup-file "gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:8000 server:app"

# 6. 환경 변수
az webapp config appsettings set \
  --name line-tracer-<unique-name> \
  --resource-group line-tracer-rg \
  --settings SECRET_KEY="랜덤-비밀키-입력"

# 7. 코드 배포 (azure/ 폴더에서 실행)
cd azure
az webapp up --name line-tracer-<unique-name> --resource-group line-tracer-rg
```

배포 완료 후 Azure URL:
```
https://line-tracer-<unique-name>.azurewebsites.net
```

### Docker 배포 (대안)

```bash
az acr create --name linetraceracr --resource-group line-tracer-rg --sku Basic --admin-enabled true
az acr build --registry linetraceracr --image line-tracer-server:latest ./azure
az containerapp create \
  --name line-tracer-app --resource-group line-tracer-rg \
  --image linetraceracr.azurecr.io/line-tracer-server:latest \
  --target-port 8000 --ingress external \
  --env-vars SECRET_KEY="랜덤-비밀키-입력"
```

---

## 4. Azure 브릿지 실행 (Jetson)

Azure에 배포한 뒤, Jetson에서 브릿지를 실행해야 원격 제어가 활성화된다.

### 의존성 설치 (최초 1회)

```bash
pip install requests "python-socketio[client]>=5"
```

### 실행 순서

```bash
# 터미널 1: 로컬 서버 먼저 시작
python jetson_nano/app.py

# 터미널 2: Azure 브릿지 시작
AZURE_URL=https://line-tracer-<unique-name>.azurewebsites.net \
  python jetson_nano/azure_bridge.py
```

### 브릿지 환경 변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `AZURE_URL` | **(필수)** | Azure 서버 주소 |
| `LOCAL_URL` | `http://127.0.0.1:8080` | 로컬 app.py 주소 |
| `TELEMETRY_INTERVAL` | `1.0` | 텔레메트리 전송 간격(초) |
| `FRAME_INTERVAL` | `0.5` | 영상 프레임 전송 간격(초) |

### Azure 대시보드 연결 상태 배너

| 배너 색상 | 의미 |
|-----------|------|
| 초록 | Jetson 연결됨 — 원격 제어 활성 |
| 노랑 | Azure 연결됨, Jetson 대기 중 |
| 빨강 | Jetson 또는 Azure 연결 끊김 |

---

## 5. 웹 대시보드 사용법

로컬(`http://127.0.0.1:8080`)과 Azure(`https://...azurewebsites.net`) 대시보드는 동일한 UI 구성을 가진다.

### 화면 구성

```
┌───────────────────────────────────┬──────────────────────┐
│  [연결 상태 배너]                   │                      │
│  Live View (실시간 영상)            │  ATmega Live Status  │
│                                   │  (상태 카드)           │
│  Mode:  [AUTO]  [MANUAL]          ├──────────────────────┤
│                                   │  Location (RFID 구역) │
│  Direction:                       │                      │
│  [◀ LEFT] [▲ FORWARD] [RIGHT ▶]  │                      │
│           [■ STOP]                │                      │
│                                   │                      │
│  Speed: 현재 [125]                 │                      │
│  [저속 75] [중속 125] [고속 175] [최대 255]               │
│  [− 10]                [+ 10]    │                      │
├───────────────────────────────────┴──────────────────────┤
│  Recent Commands                                          │
├───────────────────────────────────────────────────────────┤
│  Serial Log                                               │
└───────────────────────────────────────────────────────────┘
```

### 연결 상태 배너 (로컬)

| 배너 색상 | 의미 |
|-----------|------|
| 빨강 (err) | ATmega 시리얼 연결 끊김 또는 카메라 연결 실패 |
| 노랑 (warn) | 카메라만 연결 실패 (시리얼은 정상) |
| 숨김 | 모두 정상 |

### 모드 버튼

| 버튼 | 전송 명령 | 동작 |
|------|-----------|------|
| **AUTO** | `A` | 자동 모드: Jetson 카메라로 라인 감지 후 자동 주행 |
| **MANUAL** | `M` | 수동 모드: 웹 버튼·블루투스로 직접 제어 |

활성 모드 버튼은 **파란색**으로 표시된다.

### 방향 버튼 (D-pad 배치)

| 버튼 | 전송 명령 | 동작 |
|------|-----------|------|
| **▲ FORWARD** | `F` | 양쪽 모터 전진 |
| **◀ LEFT** | `L` | 좌회전 (왼쪽 절반 속도) |
| **RIGHT ▶** | `R` | 우회전 (오른쪽 절반 속도) |
| **■ STOP** | `S` | 모터 정지 (FORWARD 아래 중앙) |

현재 방향 버튼은 ATmega 텔레메트리 기준으로 파란색으로 표시된다.

### 속도 버튼 (수동 모드 전용)

AUTO 모드에서는 흐리게 표시되며 클릭되지 않는다.

| 버튼 | 속도값 | API |
|------|--------|-----|
| **저속** | 75 | `POST /speed/3` |
| **중속** | 125 | `POST /speed/5` |
| **고속** | 175 | `POST /speed/7` |
| **최대** | 255 | `POST /speed/0` |
| **− 10** | 현재−10 | `POST /speed/down` |
| **+ 10** | 현재+10 | `POST /speed/up` |

현재 속도는 레이블 옆 숫자로 실시간 표시된다.

---

## 6. 블루투스 제어

ATmega128 UART1(블루투스) 터미널에서 직접 제어할 수 있다.

### 모드 전환

| 키 | 동작 |
|----|------|
| `A` / `a` | AUTO 모드 전환 |
| `M` / `m` | MANUAL 모드 전환 |
| `스페이스` | 긴급 정지 (MANUAL 강제 전환 + 즉시 정지) |

### 방향 제어 (MANUAL 모드)

| 키 | 동작 |
|----|------|
| `w` / `W` | 전진 |
| `s` / `S` | 후진 |
| `l` / `L` | 좌회전 |
| `r` / `R` | 우회전 |
| `x` / `X` | 정지 |

### 속도 제어 (MANUAL 모드)

| 키 | 속도 |
|----|------|
| `1`–`9` | 25–225 (25 단위) |
| `0` | 255 (최대) |
| `+` | 현재 +10 |
| `-` | 현재 −10 |

> 웹 속도 버튼(`/speed/up`, `/speed/down`)도 동일한 프로토콜로 동작한다.

---

## 7. 상태 카드 설명

### ATmega Live Status

| 항목 | 설명 |
|------|------|
| `camera_opened` | 카메라 정상 연결 여부 (OK / FAIL) |
| `serial_connected` | ATmega 시리얼 연결 여부 (OK / FAIL) |
| `current_direction` | 현재 모터 방향 (FORWARD / LEFT / RIGHT / STOP) |
| `mode` | 현재 모드 (AUTO / MANUAL) |
| `line_tracer` | 라인 감지 명령 전송 상태 (ACTIVE / PAUSED) |
| `rpm_left` | 왼쪽 모터 RPM |
| `rpm_right` | 오른쪽 모터 RPM |
| `manual_speed` | 수동 모드 현재 속도 (0–255) |
| `location` | RFID로 감지된 현재 구역 |
| `telemetry_age` | 마지막 텔레메트리 수신 경과 시간 |
| `vision_state` | 라인 감지 상태 (ACTIVE / IDLE) |
| `vision_err` | 라인 중심과 화면 중앙의 픽셀 오차 |

### Location 카드

RFID 태그를 차량이 통과할 때 구역명이 크게 표시된다.

| 구역명 | 비고 |
|--------|------|
| A구역 | RFID 태그 A (UID 하드코딩, `main.c` `uid_to_zone()` 참조) |
| B구역 | RFID 태그 B |
| C구역 | RFID 태그 C |
| D구역 | RFID 태그 D |
| `-` | 감지된 구역 없음 |

---

## 8. 모드별 동작 정리

### AUTO 모드

1. Jetson 카메라가 하단 35%(ROI) 영역에서 검은 라인을 감지한다.
2. 라인 중심과 화면 중앙의 오차를 계산해 80ms 간격으로 명령을 전송한다.
3. ATmega는 명령이 300ms 이상 없으면 자동 정지한다.
4. 속도: 직진 120, 회전 안쪽 모터 60 (펌웨어 고정값)

| 상황 | 명령 |
|------|------|
| 오차 ±40px 이내 | `F` 직진 |
| 라인이 왼쪽 | `L` 좌회전 |
| 라인이 오른쪽 | `R` 우회전 |
| 3프레임 연속 미감지 | `S` 정지 |

### MANUAL 모드

- Jetson은 영상 스트리밍을 유지하되 라인 감지 명령 전송을 중지한다.
- 웹 방향 버튼 또는 블루투스로 직접 제어한다.
- 속도는 웹 속도 버튼 또는 블루투스 숫자키로 조절한다.

---

## 9. API 엔드포인트

### 로컬 서버 (`jetson_nano/app.py`)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/` | 대시보드 HTML |
| `GET` | `/video_feed` | MJPEG 영상 스트림 |
| `GET` | `/snapshot` | 최신 JPEG 1장 (azure_bridge 전용) |
| `GET` | `/health` | 전체 상태 JSON |
| `POST` | `/send/<cmd>` | 명령 전송 (F/L/R/S/A/M) |
| `POST` | `/speed/<level>` | 속도 설정 (level: 0–9) |
| `POST` | `/speed/up` | 속도 +10 |
| `POST` | `/speed/down` | 속도 −10 |

```bash
# curl 예시
curl -X POST http://127.0.0.1:8080/send/A       # AUTO 모드
curl -X POST http://127.0.0.1:8080/send/M       # MANUAL 모드
curl -X POST http://127.0.0.1:8080/speed/5      # 속도 125
curl -X POST http://127.0.0.1:8080/speed/up     # 속도 +10
curl         http://127.0.0.1:8080/health       # 상태 조회
```

### Azure 서버 (`azure/server.py`)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/` | Azure 대시보드 HTML |
| `GET` | `/health` | Jetson 연결 여부 + 최신 텔레메트리 JSON |
| Socket.IO | `command` | 명령 전달 `{cmd: 'F'|'L'|'R'|'S'|'A'|'M'}` |
| Socket.IO | `speed` | 속도 전달 `{level: 0-9}` 또는 `{dir: 'up'|'down'}` |

---

## 10. 문제 해결

| 증상 | 확인 사항 |
|------|-----------|
| 로컬 `serial_connected: FAIL` | USB 케이블 확인, `SERIAL_PORT` 환경 변수 확인 (`ls /dev/ttyUSB*`) |
| 로컬 `camera_opened: FAIL` | 카메라 연결 확인, `CAM_INDEX` 값 변경 시도 (0, 1, 2) |
| 영상이 안 보임 (로컬) | 브라우저에서 `/video_feed` 직접 접속해 스트림 확인 |
| 영상이 안 보임 (Azure) | `azure_bridge.py` 실행 중인지 확인, `FRAME_INTERVAL` 값 확인 |
| 모터 무반응 | ATmega 전원·UART0 연결 확인, Serial Log 오류 메시지 확인 |
| AUTO 모드인데 계속 정지 | 카메라 ROI(하단 35%) 내에 라인이 있는지 확인, 조명 확인 |
| RFID 구역 표시 안 됨 | PN532 I2C 연결 확인, 태그 UID가 `main.c`에 등록됐는지 확인 |
| Azure 배너가 계속 노랑 | `azure_bridge.py` 미실행 또는 `AZURE_URL` 오타 확인 |
| Azure 배포 후 WebSocket 오류 | `az webapp config set --web-sockets-enabled true` 실행 여부 확인 |
| Azure 비용 절감 | 사용 안 할 때 `az webapp stop --name line-tracer-<name> --resource-group line-tracer-rg` |
