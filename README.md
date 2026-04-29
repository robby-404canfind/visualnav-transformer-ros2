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
| `navigate.py` | 저장된 topomap을 따라 목표 이미지까지 이동합니다. |
| `publish_cmd.py` | `/waypoint`를 `geometry_msgs/Twist` 속도 명령으로 변환합니다. |
| `visualize.py` | 모델이 예측한 여러 trajectory 후보를 이미지 위에 시각화합니다. |
| `create_topomap.py` | 수동 주행 중 카메라 이미지를 저장해 topomap을 만듭니다. |
| `export_ros2_dataset.py` | ROS2 카메라/pose 토픽을 학습 데이터셋 형식으로 기록합니다. |
| `inspect_dataset.py` | 기록된 trajectory의 spacing, pose jump 등을 점검합니다. |

---

## 2. 목적별 가이드

이 README는 ROS2 실행/검증 중심의 빠른 사용법을 다룹니다.

| 목적 | 볼 곳 |
|---|---|
| ROS2 토픽 연결과 모델 추론 확인 | 이 README |
| trajectory 후보 시각화 | 이 README의 `시각화` 섹션 |
| r1 시뮬레이터에서 모델 주행 테스트 | 이 README의 `r1 빠른 검증 순서` 섹션 |
| r1 트랙 데이터 수집과 NoMaD 파인튜닝 | [`docs/r1_finetuning_guide.md`](docs/r1_finetuning_guide.md) |

---

## 3. 저장소 클론과 이미지 빌드

```bash
git clone https://github.com/robby-404canfind/visualnav-transformer-ros2.git
cd visualnav-transformer-ros2

docker build -t visualnav_transformer:latest .
```

이 Dockerfile은 빌드 시점의 저장소 내용을 이미지 안의 `/visualnav-transformer`로 `COPY`합니다. 즉, **이미지만 단독 실행하면 빌드 당시 코드가 보입니다.**

개발 중인 브랜치나 방금 수정한 파일을 바로 테스트하려면 아래의 **bind mount 컨테이너**를 사용하세요.

---

## 4. 권장 Docker 실행 방식

### 4.1 개발/실습용 bind mount 컨테이너

현재 브랜치의 코드를 그대로 컨테이너에서 보려면 저장소 루트를 `/visualnav-transformer`로 마운트합니다.

```bash
cd ~/visualnav-transformer-ros2
mkdir -p datasets data_splits model_weights src/visualnav_transformer/train/logs

xhost +local:root  # visualize.py 등 GUI를 쓸 때 필요

docker run -dit \
  --name visualnav_r1_ft \
  --gpus=all \
  --net=host \
  --ipc=host \
  --env ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-0} \
  --env VNT_IMAGE_TOPIC=/r1/camera1/image_raw \
  --env VNT_CMD_VEL_TOPIC=/r1/cmd_vel \
  --env DISPLAY=${DISPLAY:-:0} \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v "$(pwd)":/visualnav-transformer \
  -w /visualnav-transformer \
  visualnav_transformer:latest \
  -lc 'git config --global --add safe.directory /visualnav-transformer; tail -f /dev/null'
```

컨테이너에 들어가려면:

```bash
docker exec -it visualnav_r1_ft bash
```

처음 한 번 dependency를 동기화합니다. 이미지 빌드 후 `pyproject.toml`이 바뀐 경우 특히 필요합니다.

```bash
docker exec -it visualnav_r1_ft bash -lc '
cd /visualnav-transformer
poetry install --no-interaction --no-ansi
'
```

확인:

```bash
docker exec -it visualnav_r1_ft bash -lc '
cd /visualnav-transformer
git status -sb
git branch --show-current
'
```

### 4.2 이미지 단독 실행

이미지에 포함된 스냅샷 코드만 사용할 때는 다음처럼 실행할 수 있습니다.

```bash
docker run -it --rm --gpus=all --net=host \
  --env ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-0} \
  --env VNT_IMAGE_TOPIC=/r1/camera1/image_raw \
  --env VNT_CMD_VEL_TOPIC=/r1/cmd_vel \
  visualnav_transformer:latest
```

단, 이 방식은 현재 host 브랜치 수정사항을 자동으로 반영하지 않습니다.

---

## 5. ROS2 토픽 설정

기본 토픽은 다음과 같습니다.

```text
카메라 입력: /camera/camera/color/image_raw
속도 출력: /cmd_vel
```

r1 시뮬레이터에서는 다음 토픽을 사용합니다.

```text
카메라 입력: /r1/camera1/image_raw
속도 출력: /r1/cmd_vel
```

컨테이너 실행 시 환경변수로 토픽을 바꿉니다.

```bash
VNT_IMAGE_TOPIC=/r1/camera1/image_raw
VNT_CMD_VEL_TOPIC=/r1/cmd_vel
```

ROS2 스크립트를 실행할 때는 항상 ROS 환경을 먼저 source합니다.

```bash
source /opt/ros/humble/setup.bash
```

---

## 6. r1 빠른 검증 순서

### 6.1 시뮬레이터와 토픽 확인

r1 시뮬레이터를 실행한 뒤 토픽을 확인합니다.

```bash
ros2 topic list | grep /r1
ros2 topic hz /r1/camera1/image_raw
```

### 6.2 모델만 실행하기: 로봇은 움직이지 않음

`explore.py`는 `/waypoint`와 `/sampled_actions`만 발행합니다. 이 단계에서는 로봇이 움직이지 않습니다.

```bash
docker exec -it visualnav_r1_ft bash -lc '
cd /visualnav-transformer
source /opt/ros/humble/setup.bash
poetry run python src/visualnav_transformer/deployment/src/explore.py \
  --model nomad_r1 \
  --waypoint 2 \
  --num-samples 8
'
```

다른 터미널에서 확인:

```bash
ros2 topic hz /waypoint
ros2 topic echo /waypoint --once
```

pretrained 기본 모델을 테스트하려면 `--model nomad`를 사용합니다.

### 6.3 trajectory 후보 시각화

`explore.py`가 실행 중일 때 다른 터미널에서 실행합니다.

```bash
docker exec -it -e DISPLAY=${DISPLAY:-:0} visualnav_r1_ft bash -lc '
cd /visualnav-transformer
source /opt/ros/humble/setup.bash
poetry run python scripts/visualize.py
'
```

정상 동작하면 현재 카메라 이미지 위에 `/sampled_actions`의 후보 trajectory가 그려집니다.

### 6.4 waypoint를 `/r1/cmd_vel`로 변환하기: 로봇 움직임

`publish_cmd.py`를 실행하면 `/waypoint`를 받아 `/r1/cmd_vel`을 발행합니다. 이때부터 로봇이 움직일 수 있습니다.

짧은 안전 테스트:

```bash
docker exec -it visualnav_r1_ft bash -lc '
cd /visualnav-transformer
source /opt/ros/humble/setup.bash
timeout 10s poetry run python scripts/publish_cmd.py || true
ros2 topic pub --once /r1/cmd_vel geometry_msgs/msg/Twist "{}"
'
```

계속 주행하려면 `timeout` 없이 실행합니다.

```bash
docker exec -it visualnav_r1_ft bash -lc '
cd /visualnav-transformer
source /opt/ros/humble/setup.bash
poetry run python scripts/publish_cmd.py
'
```

정지 명령:

```bash
ros2 topic pub --once /r1/cmd_vel geometry_msgs/msg/Twist "{}"
```

확인:

```bash
ros2 topic echo /r1/cmd_vel --once
ros2 topic echo /r1/odom --once
```

> `timeout 10s`를 쓰면 10초 후 종료되는 것이 정상입니다. 짧은 테스트 후 반드시 zero Twist를 한 번 발행하세요.

---

## 7. Topomap 기반 주행

목표 지점까지 이동하려면 먼저 topomap을 만듭니다.

```bash
docker exec -it visualnav_r1_ft bash -lc '
cd /visualnav-transformer
source /opt/ros/humble/setup.bash
poetry run python src/visualnav_transformer/deployment/src/create_topomap.py \
  --dir r1_test \
  --dt 1.0
'
```

저장 위치:

```text
topomaps/images/r1_test/
```

저장한 topomap으로 navigation waypoint를 생성합니다.

```bash
docker exec -it visualnav_r1_ft bash -lc '
cd /visualnav-transformer
source /opt/ros/humble/setup.bash
poetry run python src/visualnav_transformer/deployment/src/navigate.py \
  --model nomad_r1 \
  --dir r1_test \
  --goal-node -1
'
```

별도 터미널에서 `publish_cmd.py`를 실행하면 실제 `/r1/cmd_vel`이 나갑니다.

---

## 8. 파인튜닝 개요

pretrained NoMaD가 직사각형 검정 트랙을 안정적으로 따라가지 못한다면, r1 시뮬레이터에서 사람이 직접 잘 주행한 데이터를 수집해 파인튜닝할 수 있습니다.

핵심 절차는 다음과 같습니다.

```text
수동 expert 주행 데이터 수집
  ↓
카메라 이미지 + /r1/odom pose 저장
  ↓
데이터 품질 검사
  ↓
깨끗한 trajectory만 train/test split 생성
  ↓
pretrained NoMaD checkpoint에서 fine-tuning
  ↓
nomad_r1 checkpoint로 시뮬레이션 재검증
```

자세한 절차는 아래 문서를 참고하세요.

```text
docs/r1_finetuning_guide.md
```

---

## 9. 문제 해결

### 컨테이너 안 코드가 main 또는 예전 코드처럼 보입니다

이미지 단독 실행은 Dockerfile의 `COPY . /visualnav-transformer` 때문에 빌드 시점 코드가 보입니다. 현재 브랜치 코드를 보려면 4장의 bind mount 컨테이너를 사용하세요.

### `ModuleNotFoundError: No module named 'rclpy'`

ROS2 환경을 source하지 않은 상태입니다.

```bash
source /opt/ros/humble/setup.bash
```

### `/waypoint`가 안 나옵니다

대부분 카메라 토픽이 안 들어오는 경우입니다.

```bash
ros2 topic hz /r1/camera1/image_raw
```

컨테이너 환경변수도 확인합니다.

```bash
docker exec -it visualnav_r1_ft bash -lc 'echo $VNT_IMAGE_TOPIC'
```

### `/waypoint`는 나오는데 로봇이 안 움직입니다

`publish_cmd.py`가 실행 중인지, `/r1/cmd_vel`이 나오는지 확인합니다.

```bash
ros2 topic echo /r1/cmd_vel --once
```

### 학습이 바로 죽고 `Unexpected bus error ... insufficient shared memory`가 나옵니다

Docker shared memory가 부족한 경우입니다. 권장 컨테이너 실행 옵션은 `--ipc=host`입니다. 또한 `nomad_r1.yaml`은 이 환경에서 안전하게 돌도록 `batch_size: 16`, `num_workers: 2`를 기본으로 사용합니다.

### GUI 시각화 창이 안 뜹니다

호스트에서 X11 권한을 열고, 컨테이너에 `DISPLAY`와 `/tmp/.X11-unix`를 전달해야 합니다.

```bash
xhost +local:root
```

컨테이너 실행 옵션:

```bash
--env DISPLAY=${DISPLAY:-:0} -v /tmp/.X11-unix:/tmp/.X11-unix
```

---

## 10. 안전 메모

- `explore.py`만 실행하면 로봇은 움직이지 않습니다.
- `publish_cmd.py`를 함께 실행하면 로봇이 움직입니다.
- 실제 로봇에서는 반드시 저속, 넓은 공간, 비상 정지 가능한 상태에서 테스트하세요.
- 시뮬레이션에서 성공해도 실제 로봇에서는 카메라 노이즈, 조명, 바닥 마찰, 지연 때문에 결과가 달라질 수 있습니다.
