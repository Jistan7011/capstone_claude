Claude Code로 캡스톤 날먹을 하고싶어



# 프로젝트 실행

### Jetson
터미널 3개 필요

1.Yolo AI
sudo docker run -it --rm \
--runtime nvidia \
--network host \
--ipc=host \
--privileged \
--device /dev/video0 \
-v /dev/bus/usb:/dev/bus/usb \
-v /home/capstone:/workspace \
ultralytics/ultralytics:latest-jetson-jetpack6 \
/bin/bash

2.라인 트레이서
CAM_INDEX=2 CAM_BACKEND=v4l2 CAP_W=320 CAP_H=240 CAP_FPS=20 HTTP_PORT=8080 python3 app.py

3.Azure랑 통신
AZURE_URL=http://20.196.194.107 LOCAL_URL=http://127.0.0.1:8080 python3 azure_bridge.py


### Azure
sudo PORT=80 python3 server.py
