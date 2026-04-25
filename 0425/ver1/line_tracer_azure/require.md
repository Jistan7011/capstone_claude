Web-Jetson-atmega128 구조의 라인트레이서 제작


# 웹 서버에서 젯슨, atmega128제어
- 수동 모드-자동 모드(라인트레이서) 모드 전환(버튼)
- 전진, 좌회전 우회전 정지 버튼으로 제어(현재 제어 상태에 따라 버튼에 불 들어옴-토글)
- RFID는 현재 라인트레이서 자동차의 위치(A구역, B구역)/ 새로운 구역에 들어가면 구역 업데이트하여 웹에 표시
- 속도 버튼으로 조절
- 젯슨 127.0.0.1에서 테스트용, Azure에 서버 업로드하여 원격에서도 조작 가능하게
- 라인트레이서 영상 화면에 스트리밍



# Azure 
- 20.196.194.107:80
- Web(Azure)-Jetson-Atmega128구조
- Azure 폴더 만들어서 코드 및 환경설정 방법 md 파일로 작성
- 현재 Azure 상태
VM 이름: streaming
운영체제: Linux (Ubuntu 22.04)
VM 세대: V2
아키텍처: x64
상태: Ready

공용 IP 주소: 20.196.194.107
프라이빗 IP 주소: 10.0.0.4
가상 네트워크/서브넷: streaming-vnet/default

VM 크기: Standard B1s
vCPU: 1
RAM: 1 GiB

OS 이미지:
- Publisher: canonical
- Offer: ubuntu-22_04-lts
- Plan/SKU: server

디스크:
- OS 디스크 이름: streaming_OsDisk_1_1c852de066d14d078873b64dd8352fd0
- 호스트에서 암호화: 사용 안 함
- Azure Disk Encryption: 활성화되지 않음
- 임시 OS 디스크: 해당 없음
- 데이터 디스크: 0


