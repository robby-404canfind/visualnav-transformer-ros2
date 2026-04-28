# VisualNav Transformer ROS2 실습

이 저장소는 [visualnav-transformer](https://github.com/robodhruv/visualnav-transformer)를 ROS2 환경에서 실행하기 위한 실습용 포크입니다.

기본 목표는 카메라 영상만으로 Visual Navigation 모델(NoMaD / ViNT / GNM)을 실행하고, 모델이 예측한 waypoint를 ROS2 속도 명령으로 변환해 시뮬레이터 또는 로봇을 움직이는 것입니다.

---

## 1. 전체 구조

```text
카메라 이미지
  ↓
explore.py 또는 navigate.py
  ↓
/waypoint
  ↓
publish_cmd.py
  ↓
/cmd_vel 또는 /r1/cmd_vel
  ↓
로봇 이동
```

주요 스크립트는 다음과 같습니다.

| 파일 | 역할 |
|---|---|
| `explore.py` | 목표 위치 없이 현재 카메라 영상만 보고 이동 waypoint를 예측합니다. |
| `publish_cmd.py` | `/waypoint`를 `geometry_msgs/Twist` 속도 명령으로 변환합니다. |
| `visualize.py` | 모델이 예측한 여러 trajectory 후보를 이미지 위에 시각화합니다. |
| `create_topomap.py` | 수동 주행 중 카메라 이미지를 저장해 topomap을 만듭니다. |
| `navigate.py` | 저장된 topomap을 따라 목표 이미지까지 이동합니다. |
| `topic_names.py` | 카메라/속도 명령 등 ROS2 토픽 이름을 관리합니다. |

---

## 2. 저장소 클론

```bash
git clone https://github.com/robby-404canfind/visualnav-transformer-ros2.git
cd visualnav-transformer-ros2
```

---

## 3. Docker 이미지 빌드

```bash
docker build -t visualnav_transformer:latest .
```

이 Dockerfile은 현재 저장소 내용을 이미지 안의 `/visualnav-transformer`로 복사합니다. 따라서 fork에서 수정한 코드가 Docker 이미지에 반영됩니다.

> 참고: 빌드 중 `nomad.pth` 모델 가중치를 Google Drive에서 다운로드합니다.

---

## 4. ROS2 토픽 설정

기본 토픽은 다음과 같습니다.

```text
카메라 입력: /camera/camera/color/image_raw
속도 출력: /cmd_vel
```

실습 시뮬레이터의 r1 로봇을 사용할 경우 다음 토픽을 씁니다.

```text
카메라 입력: /r1/camera1/image_raw
속도 출력: /r1/cmd_vel
```

이 저장소는 환경변수로 토픽을 바꿀 수 있습니다.

```bash
VNT_IMAGE_TOPIC=/r1/camera1/image_raw
VNT_CMD_VEL_TOPIC=/r1/cmd_vel
```

---

## 5. 컨테이너 실행

r1 시뮬레이터 기준 실행 예시는 다음과 같습니다.

```bash
docker run -it --rm --gpus=all --net=host \
  --env ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-0} \
  --env VNT_IMAGE_TOPIC=/r1/camera1/image_raw \
  --env VNT_CMD_VEL_TOPIC=/r1/cmd_vel \
  visualnav_transformer:latest
```


토픽이 보이는지 확인합니다.

```bash
ros2 topic list
ros2 topic hz /r1/camera1/image_raw
```

---

## 6. 모델만 먼저 실행하기: `explore.py`

먼저 로봇을 움직이지 않고 모델 추론만 확인합니다.

```bash
poetry run python src/visualnav_transformer/deployment/src/explore.py
```

정상 동작하면 `/waypoint`와 `/sampled_actions`가 발행됩니다.

다른 터미널에서 확인할 수 있습니다.

```bash
ros2 topic echo /waypoint
```

> 중요: Poetry 2.x에서는 `poetry shell` 명령이 기본 제공되지 않습니다. 이 저장소에서는 `poetry run python ...` 형태로 실행합니다.

---

## 7. 로봇 움직이기: `publish_cmd.py`

`explore.py`는 waypoint만 발행합니다. 실제 속도 명령을 보내려면 별도 터미널 또는 별도 컨테이너에서 다음을 실행합니다.

```bash
poetry run python scripts/publish_cmd.py
```

`VNT_CMD_VEL_TOPIC=/r1/cmd_vel`로 실행한 경우 `/r1/cmd_vel`로 속도 명령이 나갑니다.

확인:

```bash
ros2 topic echo /r1/cmd_vel
```

정지 명령:

```bash
ros2 topic pub --once /r1/cmd_vel geometry_msgs/msg/Twist "{}"
```

> 주의: `publish_cmd.py`를 실행하면 시뮬레이터 또는 실제 로봇이 움직일 수 있습니다. 처음에는 넓은 공간/낮은 속도/즉시 정지 가능한 상태에서 테스트하세요.

---

## 8. 시각화: `visualize.py`

모델이 예측한 trajectory 후보를 이미지 위에 보고 싶으면 `visualize.py`를 실행합니다.

GUI가 필요한 경우 호스트에서 먼저 실행합니다.

```bash
xhost +local:root
```

GUI 지원 컨테이너 실행:

```bash
docker run -it --rm --gpus=all --net=host \
  --env ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-0} \
  --env VNT_IMAGE_TOPIC=/r1/camera1/image_raw \
  --env VNT_CMD_VEL_TOPIC=/r1/cmd_vel \
  --env DISPLAY=$DISPLAY \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  visualnav_transformer:latest
```

컨테이너 안에서:

```bash
poetry run python scripts/visualize.py
```

`explore.py` 또는 `navigate.py`가 실행 중이면 `/sampled_actions`를 받아 trajectory 후보를 그립니다.

---

## 9. Topomap 만들기

목표 지점까지 이동하려면 먼저 환경의 topomap을 만들어야 합니다.

컨테이너 안에서:

```bash
poetry run python src/visualnav_transformer/deployment/src/create_topomap.py \
  --dir r1_test \
  --dt 1.0
```

이 상태에서 로봇을 수동으로 움직이면 1초마다 카메라 이미지가 저장됩니다.

저장 위치:

```text
topomaps/images/r1_test/
```

종료는 `Ctrl+C`입니다.

> 같은 `--dir` 이름으로 다시 실행하면 기존 이미지가 삭제되고 새로 저장됩니다.

---

## 10. Topomap 기반 주행: `navigate.py`

저장한 topomap을 따라 목표 지점까지 이동합니다.

터미널 1: navigation waypoint 생성

```bash
poetry run python src/visualnav_transformer/deployment/src/navigate.py \
  --dir r1_test \
  --goal-node -1
```

터미널 2: waypoint를 속도 명령으로 변환

```bash
poetry run python scripts/publish_cmd.py
```

`--goal-node -1`은 topomap의 마지막 이미지를 목표로 사용한다는 뜻입니다.

특정 이미지를 목표로 쓰고 싶다면 예를 들어 다음처럼 실행합니다.

```bash
poetry run python src/visualnav_transformer/deployment/src/navigate.py \
  --dir r1_test \
  --goal-node 10
```

---

## 11. r1 시뮬레이터 빠른 검증 순서

1. 시뮬레이터 실행
2. 토픽 확인

```bash
ros2 topic list | grep /r1
ros2 topic hz /r1/camera1/image_raw
```

3. 컨테이너 실행

```bash
docker run -it --rm --gpus=all --net=host \
  --env ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-0} \
  --env VNT_IMAGE_TOPIC=/r1/camera1/image_raw \
  --env VNT_CMD_VEL_TOPIC=/r1/cmd_vel \
  visualnav_transformer:latest
```

4. 모델 실행

```bash
poetry run python src/visualnav_transformer/deployment/src/explore.py
```

5. 다른 터미널에서 속도 명령 실행

```bash
docker run -it --rm --gpus=all --net=host \
  --env ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-0} \
  --env VNT_IMAGE_TOPIC=/r1/camera1/image_raw \
  --env VNT_CMD_VEL_TOPIC=/r1/cmd_vel \
  visualnav_transformer:latest
```

컨테이너 안에서:

```bash
poetry run python scripts/publish_cmd.py
```

6. 움직임 확인

```bash
ros2 topic echo /r1/odom
```

---

## 12. 현재 시뮬레이션 실험 메모

실습 검증에 사용한 시뮬레이션 환경은 직사각형 검정색 트랙입니다.

현재 관찰된 결과는 다음과 같습니다.

1. `explore.py` 기반 주행
   - 초반에는 트랙을 어느 정도 따라가지만, 이후 트랙을 벗어나 다른 영역도 주행합니다.
   - `explore.py`는 목표 지점이나 topomap을 쓰지 않는 탐색 모드이므로, 트랙 주행을 보장하지 않습니다.

2. `create_topomap.py`로 트랙 topomap을 기록한 뒤 `navigate.py` 실행
   - 결과가 `explore.py`와 비슷하게 나타났습니다.
   - 현재 pretrained 모델만으로는 이 직사각형 검정 트랙을 안정적으로 따라가는 데 한계가 있을 수 있습니다.

따라서 이 실습에서는 먼저 “ROS2 토픽 연결 → 모델 추론 → waypoint 발행 → 속도 명령 변환” 흐름을 확인하고, 안정적인 트랙 추종 성능은 추가 튜닝 또는 데이터/환경 보정이 필요하다고 봅니다.

---

## 13. 문제 해결

### `poetry shell`이 안 됩니다

정상입니다. Poetry 2.x에서는 `poetry shell`이 기본 명령이 아닙니다.

대신 다음처럼 실행하세요.

```bash
poetry run python <script.py>
```

### `/waypoint`가 안 나옵니다

대부분 카메라 토픽이 안 들어오는 경우입니다.

```bash
ros2 topic hz /r1/camera1/image_raw
```

그리고 컨테이너 실행 시 환경변수가 맞는지 확인하세요.

```bash
echo $VNT_IMAGE_TOPIC
```

### 로봇이 안 움직입니다

`/waypoint`와 `/r1/cmd_vel`을 각각 확인합니다.

```bash
ros2 topic echo /waypoint
ros2 topic echo /r1/cmd_vel
```

`/waypoint`는 나오는데 `/r1/cmd_vel`이 안 나오면 `publish_cmd.py`가 실행 중인지 확인하세요.

### GUI 시각화 창이 안 뜹니다

호스트에서 X11 권한을 열고 컨테이너에 DISPLAY를 전달해야 합니다.

```bash
xhost +local:root
```

컨테이너 실행 시 다음 옵션이 필요합니다.

```bash
--env DISPLAY=$DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix
```

---

## 14. 안전 메모

- `explore.py`만 실행하면 로봇은 움직이지 않습니다.
- `publish_cmd.py`를 함께 실행하면 로봇이 움직입니다.
- 실제 로봇에서는 반드시 저속, 넓은 공간, 비상 정지 가능한 상태에서 테스트하세요.
- 시뮬레이션에서 성공해도 실제 로봇에서는 카메라 노이즈, 조명, 바닥 마찰, 지연 때문에 결과가 달라질 수 있습니다.
