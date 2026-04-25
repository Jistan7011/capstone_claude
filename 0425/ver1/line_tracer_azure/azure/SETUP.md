# Azure VM 배포 설정 가이드

## 전체 아키텍처

```
[원격 브라우저]
      │ HTTP + WebSocket
      ▼
[Azure VM]  20.196.194.107:80  ← azure/server.py (Docker)
      │ WebSocket (Socket.IO)
      ▼
[Jetson Nano]  ← jetson_nano/azure_bridge.py
      │ HTTP localhost:8080
      ▼
[jetson_nano/app.py + ATmega128]
```

| 파일 | 실행 위치 | 역할 |
|------|-----------|------|
| `azure/server.py` | Azure VM | 브라우저 ↔ Jetson 중계 서버 |
| `jetson_nano/azure_bridge.py` | Jetson Nano | 로컬 app.py ↔ Azure 연결 브릿지 |
| `jetson_nano/app.py` | Jetson Nano | 카메라 비전 + Flask 로컬 서버 |

---

## Azure VM 정보

| 항목 | 값 |
|------|-----|
| VM 이름 | streaming |
| 공용 IP | **20.196.194.107** |
| OS | Ubuntu 22.04 LTS |
| 크기 | Standard B1s (vCPU 1, RAM 1GiB) |
| 외부 포트 | **80** (HTTP) |

---

## 1단계 — VM 접속 (SSH)

```bash
ssh <username>@20.196.194.107
```

---

## 2단계 — VM에 Docker 설치 (최초 1회)

```bash
# Docker 설치
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# 로그아웃 후 재접속하여 그룹 적용
```

---

## 3단계 — 소스 코드 배포

```bash
# 저장소 클론 (또는 scp로 azure/ 폴더 복사)
git clone https://github.com/<your-repo>.git
cd <repo>/azure
```

또는 로컬에서 VM으로 파일 전송:

```bash
# 로컬 PC에서 실행
scp -r azure/ <username>@20.196.194.107:~/line_tracer/azure/
```

---

## 4단계 — 환경 변수 파일 생성

```bash
cd ~/line_tracer/azure
cp .env.example .env
# SECRET_KEY를 임의 문자열로 변경
nano .env
```

`.env` 내용:
```
PORT=8000
SECRET_KEY=여기에-랜덤-문자열-입력
```

---

## 5단계 — Docker 이미지 빌드 및 실행

```bash
cd ~/line_tracer/azure

# 이미지 빌드
docker build -t line-tracer-server .

# 컨테이너 실행 (외부 80 → 내부 8000, 재시작 자동)
docker run -d \
  --name line-tracer \
  --restart unless-stopped \
  -p 80:8000 \
  --env-file .env \
  line-tracer-server
```

브라우저에서 접속 확인:
```
http://20.196.194.107
```

---

## 6단계 — Jetson Nano에서 브릿지 실행

### 의존성 설치 (최초 1회)

```bash
pip install requests "python-socketio[client]>=5"
```

### 실행 순서

```bash
# 1. 로컬 서버 먼저 실행
SERIAL_ENABLED=1 python jetson_nano/app.py &

# 2. Azure 브릿지 실행
AZURE_URL=http://20.196.194.107 python jetson_nano/azure_bridge.py
```

### 환경 변수 전체 목록 (azure_bridge.py)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `AZURE_URL` | **(필수)** `http://20.196.194.107` | Azure VM 주소 |
| `LOCAL_URL` | `http://127.0.0.1:8080` | 로컬 app.py 주소 |
| `TELEMETRY_INTERVAL` | `1.0` | 텔레메트리 전송 간격(초) |
| `FRAME_INTERVAL` | `0.5` | 영상 프레임 전송 간격(초) |

---

## Docker 관리 명령어

```bash
# 실행 중인 컨테이너 확인
docker ps

# 로그 확인
docker logs -f line-tracer

# 서버 재시작
docker restart line-tracer

# 서버 중지
docker stop line-tracer

# 코드 업데이트 후 재배포
docker build -t line-tracer-server . && \
docker stop line-tracer && \
docker rm line-tracer && \
docker run -d --name line-tracer --restart unless-stopped -p 80:8000 --env-file .env line-tracer-server
```

---

## 동작 확인 체크리스트

1. `http://20.196.194.107` 브라우저 접속 → 대시보드 페이지 로드 확인
2. Jetson에서 `azure_bridge.py` 실행 → 콘솔에 `[Bridge] Azure 연결됨` 출력 확인
3. 대시보드 상단 배너에 **"Jetson 연결됨"** (초록) 표시 확인
4. Live View에 카메라 영상 표시 확인 (약 0.5초 딜레이)
5. AUTO/MANUAL 버튼 → ATmega128 모드 전환 확인

---

## 주의 사항

| 항목 | 내용 |
|------|------|
| 워커 수 | `-w 1` 고정 필수 — Socket.IO는 단일 프로세스에서만 공유 상태 유지 가능 |
| 영상 방식 | MJPEG 스트림 불가 → base64 스냅샷 (0.5초 간격) |
| 방화벽 | Azure VM의 네트워크 보안 그룹(NSG)에서 포트 80 인바운드 허용 필요 |
| VM 절전 | Standard B1s는 자동 중지 없음 — 필요 시 Azure Portal에서 수동 중지 |

### Azure NSG 포트 80 허용 (미설정 시)

```bash
# Azure CLI로 인바운드 규칙 추가
az network nsg rule create \
  --resource-group <리소스그룹명> \
  --nsg-name <NSG명> \
  --name allow-http \
  --protocol tcp \
  --priority 1000 \
  --destination-port-range 80 \
  --access Allow
```

또는 Azure Portal → 네트워크 보안 그룹 → 인바운드 보안 규칙 → 포트 80 추가.
