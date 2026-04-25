# Azure 배포 설정 가이드

## 전체 아키텍처

```
[원격 브라우저]
      │ HTTPS + WebSocket
      ▼
[Azure App Service]  ← azure/server.py
      │ WebSocket (Socket.IO)
      ▼
[Jetson Nano]  ← jetson_nano/azure_bridge.py
      │ HTTP localhost
      ▼
[app.py + ATmega128]
```

- `azure/server.py` : Azure에 배포하는 중계 서버
- `jetson_nano/azure_bridge.py` : Jetson에서 실행하는 브릿지
- `jetson_nano/app.py` : 기존 로컬 서버 (변경 없이 그대로 사용)

---

## 사전 준비

1. [Azure CLI 설치](https://docs.microsoft.com/cli/azure/install-azure-cli)
2. Azure 계정 로그인

```bash
az login
```

---

## Azure App Service 배포 (방법 A — 권장)

### 1. 리소스 그룹 생성

```bash
az group create --name line-tracer-rg --location koreacentral
```

### 2. App Service 플랜 생성

```bash
# B1 (Basic): 월 ~$13, WebSocket 지원
az appservice plan create \
  --name line-tracer-plan \
  --resource-group line-tracer-rg \
  --sku B1 \
  --is-linux
```

> 무료(F1) 티어는 WebSocket을 지원하지 않으므로 **B1 이상** 필요.

### 3. 웹앱 생성

```bash
# <unique-name> 을 고유한 이름으로 변경 (예: line-tracer-홍길동)
az webapp create \
  --name line-tracer-<unique-name> \
  --resource-group line-tracer-rg \
  --plan line-tracer-plan \
  --runtime "PYTHON:3.11"
```

### 4. WebSocket 활성화

```bash
az webapp config set \
  --name line-tracer-<unique-name> \
  --resource-group line-tracer-rg \
  --web-sockets-enabled true
```

### 5. 시작 명령 설정

```bash
az webapp config set \
  --name line-tracer-<unique-name> \
  --resource-group line-tracer-rg \
  --startup-file "gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:8000 server:app"
```

### 6. 환경 변수 설정

```bash
az webapp config appsettings set \
  --name line-tracer-<unique-name> \
  --resource-group line-tracer-rg \
  --settings SECRET_KEY="랜덤-비밀키-여기에-입력"
```

### 7. 코드 배포 (`azure/` 폴더에서 실행)

```bash
cd azure
az webapp up \
  --name line-tracer-<unique-name> \
  --resource-group line-tracer-rg
```

배포 완료 후 접속 URL:
```
https://line-tracer-<unique-name>.azurewebsites.net
```

---

## Azure Container App 배포 (방법 B — Docker)

```bash
# 1. Container Registry 생성
az acr create \
  --name linetraceracr \
  --resource-group line-tracer-rg \
  --sku Basic \
  --admin-enabled true

# 2. Docker 이미지 빌드 및 푸시 (azure/ 폴더에서 실행)
cd azure
az acr build \
  --registry linetraceracr \
  --image line-tracer-server:latest .

# 3. Container App 배포
az containerapp create \
  --name line-tracer-app \
  --resource-group line-tracer-rg \
  --image linetraceracr.azurecr.io/line-tracer-server:latest \
  --target-port 8000 \
  --ingress external \
  --env-vars SECRET_KEY="랜덤-비밀키-여기에-입력"
```

---

## Jetson Nano에서 브릿지 실행

### 의존성 설치

```bash
pip install requests "python-socketio[client]>=5"
```

### 브릿지 실행

```bash
# app.py가 먼저 실행 중이어야 함
python jetson_nano/app.py &

# Azure URL을 환경 변수로 설정 후 브릿지 실행
AZURE_URL=https://line-tracer-<unique-name>.azurewebsites.net \
  python jetson_nano/azure_bridge.py
```

### 환경 변수 전체 목록

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `AZURE_URL` | (필수) | Azure 서버 주소 |
| `LOCAL_URL` | `http://127.0.0.1:8080` | 로컬 app.py 주소 |
| `TELEMETRY_INTERVAL` | `1.0` | 텔레메트리 전송 간격(초) |
| `FRAME_INTERVAL` | `0.5` | 영상 프레임 전송 간격(초) |

---

## 동작 확인

1. `https://line-tracer-<unique-name>.azurewebsites.net` 접속
2. 상단 배너에 **"Jetson 연결됨"** (초록) 표시 확인
3. Live View에 카메라 영상 표시 확인 (약 0.5초 딜레이)
4. AUTO/MANUAL 버튼으로 모드 전환 확인

---

## 주의 사항

| 항목 | 내용 |
|------|------|
| 워커 수 | `-w 1` 고정 (Socket.IO는 단일 프로세스 필요) |
| 영상 딜레이 | MJPEG 스트림 불가 → base64 스냅샷 방식 (0.5초 간격) |
| 무료 티어 | F1 플랜은 WebSocket 미지원 → B1 이상 사용 |
| 비용 절감 | 테스트 후 `az webapp stop` 으로 중지 가능 |

---

## 리소스 삭제 (테스트 종료 후)

```bash
az group delete --name line-tracer-rg --yes --no-wait
```
