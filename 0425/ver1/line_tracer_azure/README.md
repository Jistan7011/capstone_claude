
# line_tracer_status_project_v2

수정 사항:
- 수동 모드에서는 Jetson 라인트레이서 명령 송신 중지
- 웹의 현재 방향은 ATmega 실제 텔레메트리 방향만 표시
- Location 영역 추가: RFID 구역 표시
- Jetson 웹 버튼으로 AUTO/MANUAL/F/L/R/S 전송 가능
- ATmega USART0도 A/M/F/L/R/S를 처리하도록 보강

## Jetson
- `jetson_nano/app.py`
- `jetson_nano/templates/index.html`

## ATmega128
- `atmega128/main.c`
- `atmega128/pn532.c`
- `atmega128/pn532.h`

## 변경된 핵심 동작
- ATmega 모드가 `MANUAL`이면 Jetson은 영상은 계속 보여주지만 라인트레이서 명령을 보내지 않는다.
- 웹 대시보드의 `Current Direction`은 항상 ATmega 텔레메트리 `direction` 기준이다.
- RFID 구역은 `Location` 카드에서 크게 표시된다.
