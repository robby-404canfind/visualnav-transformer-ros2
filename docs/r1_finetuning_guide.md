# r1 시뮬레이터 NoMaD 파인튜닝 가이드

이 문서는 `/r1/camera1/image_raw`와 `/r1/odom`을 사용해 r1 시뮬레이터 주행 데이터를 수집하고, pretrained NoMaD 모델을 직사각형 검정 트랙 환경에 맞게 파인튜닝하는 절차를 설명합니다.

Docker 실행 방식과 기본 ROS2 검증 순서는 저장소 루트의 [`README.md`](../README.md)를 기준으로 합니다. 이 문서에서는 **데이터 수집 → 품질 검사 → 학습 → 인퍼런스 검증**에 필요한 부분만 다룹니다.

---

## 1. 목표

pretrained NoMaD 모델은 일반적인 실내/실외 주행 데이터에 기반합니다. 직사각형 검정 트랙처럼 단순하지만 특수한 시뮬레이션 환경에서는 다음 문제가 나타날 수 있습니다.

- `explore.py`가 초반에는 트랙을 따라가다가 이후 트랙 밖으로 이탈
- topomap을 만들어 `navigate.py`를 실행해도 유사하게 이탈

파인튜닝의 목표는 해당 트랙에서 사람이 직접 잘 주행한 expert trajectory를 추가 학습해, 모델이 다음 waypoint를 더 안정적으로 예측하게 만드는 것입니다.

> 주의: 한두 에피소드는 파이프라인 smoke test에는 충분하지만, 성능을 판단하기에는 부족합니다. 실제 성능 검증용 모델은 충분한 clean expert trajectory로 다시 학습해야 합니다.

---

## 2. pose source 선택: `/r1/odom` vs `/r1/amcl_pose`

### 결론

학습 라벨에는 기본적으로 **`/r1/odom`을 사용합니다.**

### 이유

NoMaD/ViNT 계열 학습에서 `traj_data.pkl`의 `position`, `yaw`는 전역 지도상의 절대 위치라기보다, 시간에 따라 연속적인 로봇 궤적을 만들기 위한 기준입니다. 학습 시에는 현재 pose 기준 local waypoint를 계산하므로, pose가 부드럽고 연속적인 것이 중요합니다.

| 후보 | 장점 | 단점 | 판단 |
|---|---|---|---|
| `/r1/odom` | 연속적이고 부드러움, 주행 행동 라벨 생성에 적합 | 장시간 누적 drift 가능 | 기본 권장 |
| `/r1/amcl_pose` | map 기준 위치와 맞음 | localization correction 때문에 pose jump 가능 | 비교/옵션용 |

짧은 closed-loop 트랙 데이터 수집에는 `/r1/odom`이 더 안전합니다. `/r1/amcl_pose`를 사용할 경우 `inspect_dataset.py`로 pose jump를 반드시 확인하세요.

---

## 3. 권장 Docker 환경

파인튜닝 작업은 README의 bind mount 컨테이너를 사용합니다. 핵심은 host의 현재 브랜치를 컨테이너의 `/visualnav-transformer`에 마운트하는 것입니다.

```bash
cd ~/visualnav-transformer-ros2
mkdir -p datasets data_splits model_weights src/visualnav_transformer/train/logs

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

처음 한 번 dependency를 맞춥니다.

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

> Dockerfile은 빌드 시점 코드를 이미지 안에 `COPY`합니다. 따라서 이미지 단독 실행은 현재 브랜치 코드가 아니라 빌드 당시 코드가 보일 수 있습니다. 파인튜닝/실험 중에는 bind mount 컨테이너를 기준으로 통일합니다.

---

## 4. 좋은 학습 데이터 조건

`explore.py`나 실패한 navigation 결과를 그대로 학습시키면, 트랙을 벗어나는 행동도 같이 학습됩니다. 데이터는 반드시 사람이 수동으로 잘 주행한 expert trajectory여야 합니다.

추천 수집 조건:

- 시계방향/반시계방향을 구분해서 충분히 수집
- 시작 위치를 다양하게 변경
- 코너 구간을 충분히 많이 포함
- 살짝 흔들린 뒤 트랙으로 복귀하는 데이터 포함
- 충돌, 정지, 트랙 이탈, 긴 pose gap 구간은 제외
- 처음에는 한 방향 데이터만으로 pipeline을 검증하고, 이후 반대 방향을 별도 실험으로 추가

---

## 5. 데이터 수집

r1 시뮬레이터가 실행 중이고 `/r1/camera1/image_raw`, `/r1/odom`이 들어오는지 먼저 확인합니다.

```bash
ros2 topic hz /r1/camera1/image_raw
ros2 topic hz /r1/odom
```

trajectory 하나를 기록합니다. 데이터 파일이 root 소유로 생기지 않도록 host 사용자 UID/GID로 실행합니다.

```bash
docker exec -it --user "$(id -u):$(id -g)" visualnav_r1_ft bash -lc '
cd /visualnav-transformer
source /opt/ros/humble/setup.bash
python3 scripts/export_ros2_dataset.py \
  --image-topic /r1/camera1/image_raw \
  --pose-source odom \
  --pose-topic /r1/odom \
  --output-dir datasets/r1_track \
  --traj-name traj_000 \
  --rate 4.0 \
  --min-distance 0.05
'
```

이 상태에서 로봇을 수동으로 트랙을 따라 주행합니다. 종료는 `Ctrl+C`입니다.

저장 결과:

```text
datasets/r1_track/traj_000/
  0.jpg
  1.jpg
  ...
  traj_data.pkl
```

여러 trajectory를 수집할 때는 `--traj-name traj_001`, `traj_002`처럼 이름을 바꿔 반복합니다.

---

## 6. 데이터 품질 검사

수집 후 반드시 데이터 품질을 확인합니다.

```bash
docker exec -it visualnav_r1_ft bash -lc '
cd /visualnav-transformer
poetry run python scripts/inspect_dataset.py datasets/r1_track
'
```

출력에서 확인할 것:

- `samples`: 각 trajectory 샘플 수
- `median_spacing`: 평균적인 프레임 간 이동거리
- `large_jumps`: 1m 이상 pose jump 또는 긴 frame gap 개수
- `max_yaw_step`: yaw가 갑자기 튀는지
- `recommended metric_waypoint_spacing`: 현재 데이터 기준 권장 spacing

판단 기준:

- `large_jumps == 0`인 trajectory만 학습에 사용합니다.
- `large_jumps > 0`이면 해당 trajectory는 재수집하거나, 문제가 없는 구간만 따로 분리해서 사용합니다.
- 실패 주행/트랙 이탈/충돌 구간은 학습에 넣지 않습니다.

`recommended metric_waypoint_spacing` 값은 다음 파일의 `r1_track.metric_waypoint_spacing`에 반영합니다.

```text
src/visualnav_transformer/train/vint_train/data/data_config.yaml
```

현재 r1 smoke 데이터 기준 기본값은 약 `0.09`입니다. 새 데이터를 충분히 모으면 다시 계산해 갱신하세요.

---

## 7. train/test split 생성

`data_split.py`는 데이터 품질을 판단하지 않습니다. 따라서 실행 전에 `datasets/r1_track`에는 학습에 사용할 clean trajectory만 남겨두는 것을 권장합니다.

bad trajectory를 제외하는 예시:

```bash
mkdir -p datasets/r1_track_rejected
mv datasets/r1_track/traj_bad datasets/r1_track_rejected/
```

clean trajectory만 남긴 뒤 split을 생성합니다.

```bash
docker exec -it visualnav_r1_ft bash -lc '
cd /visualnav-transformer
poetry run python src/visualnav_transformer/train/data_split.py \
  --data-dir datasets/r1_track \
  --dataset-name r1_track \
  --data-splits-dir data_splits \
  --split 0.8
'
```

결과:

```text
data_splits/r1_track/train/traj_names.txt
data_splits/r1_track/test/traj_names.txt
```

split 파일을 열어 원하지 않는 trajectory가 들어가지 않았는지 확인합니다.

```bash
cat data_splits/r1_track/train/traj_names.txt
cat data_splits/r1_track/test/traj_names.txt
```

---

## 8. pretrained NoMaD checkpoint 준비

`nomad_r1.yaml`은 `load_run: pretrained_nomad`를 사용합니다. 따라서 pretrained checkpoint를 다음 위치에 둡니다.

```bash
mkdir -p model_weights src/visualnav_transformer/train/logs/pretrained_nomad
```

host의 `model_weights/nomad.pth`가 비어 있으면 이미지에 포함된 checkpoint를 복사합니다.

```bash
docker run --rm \
  -v "$(pwd)/model_weights":/out \
  visualnav_transformer:latest \
  -lc 'cp /visualnav-transformer/model_weights/nomad.pth /out/nomad.pth'
```

학습 코드가 읽는 위치로 복사합니다.

```bash
cp model_weights/nomad.pth \
  src/visualnav_transformer/train/logs/pretrained_nomad/latest.pth
```

---

## 9. 파인튜닝 실행

학습 스크립트는 `src/visualnav_transformer/train` 디렉터리를 기준으로 실행합니다.

```bash
docker exec -it visualnav_r1_ft bash -lc '
cd /visualnav-transformer/src/visualnav_transformer/train
poetry run python train.py --config config/nomad_r1.yaml
'
```

학습 결과는 다음 경로에 저장됩니다.

```text
src/visualnav_transformer/train/logs/nomad_r1/<run_name>/latest.pth
src/visualnav_transformer/train/logs/nomad_r1/<run_name>/ema_latest.pth
```

현재 r1 설정은 일반 Docker 환경에서도 안정적으로 돌도록 다음 값을 기본으로 사용합니다.

```yaml
batch_size: 16
eval_batch_size: 16
num_workers: 2
```

더 큰 batch를 쓰려면 컨테이너를 `--ipc=host` 또는 충분한 `--shm-size`로 실행해야 합니다.

---

## 10. 파인튜닝 checkpoint 배포 위치로 복사

일반적으로 inference에는 `ema_latest.pth`를 우선 사용합니다.

```bash
docker exec -it visualnav_r1_ft bash -lc '
cd /visualnav-transformer/src/visualnav_transformer/train
RUN_DIR=$(ls -td logs/nomad_r1/nomad_r1_finetune_* | head -1)
cp "$RUN_DIR/ema_latest.pth" /visualnav-transformer/model_weights/nomad_r1_ema.pth
echo "$RUN_DIR"
'
```

`config/models.yaml`에는 다음 항목이 있어야 합니다.

```yaml
nomad_r1:
  config_path: "src/visualnav_transformer/train/config/nomad_r1.yaml"
  ckpt_path: "model_weights/nomad_r1_ema.pth"
```

---

## 11. 파인튜닝 모델 인퍼런스 검증

### 11.1 모델만 실행

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

확인:

```bash
ros2 topic hz /waypoint
ros2 topic echo /waypoint --once
```

### 11.2 시각화

`explore.py`가 실행 중일 때 다른 터미널에서 실행합니다.

```bash
xhost +local:root

docker exec -it -e DISPLAY=${DISPLAY:-:0} visualnav_r1_ft bash -lc '
cd /visualnav-transformer
source /opt/ros/humble/setup.bash
poetry run python scripts/visualize.py
'
```

### 11.3 짧은 주행 테스트

```bash
docker exec -it visualnav_r1_ft bash -lc '
cd /visualnav-transformer
source /opt/ros/humble/setup.bash
timeout 10s poetry run python scripts/publish_cmd.py || true
ros2 topic pub --once /r1/cmd_vel geometry_msgs/msg/Twist "{}"
'
```

`timeout 10s`를 쓰면 10초 후 종료되는 것이 정상입니다. 계속 주행하려면 `timeout` 없이 `publish_cmd.py`를 실행하세요.

---

## 12. 흔한 실패 원인

### 데이터가 너무 적음

직사각형 트랙 한두 바퀴로는 코너/복귀 행동이 부족합니다. 최소 수십 바퀴 수준의 clean expert trajectory를 권장합니다.

### 실패 주행을 학습함

트랙 이탈 데이터가 들어가면 이탈 행동이 강화됩니다.

### pose jump 또는 긴 frame gap이 있음

AMCL correction, 시뮬레이터 리셋, 일시적인 topic 지연 등으로 local waypoint 라벨이 깨질 수 있습니다. `inspect_dataset.py`로 확인하세요.

### `metric_waypoint_spacing` 불일치

`data_config.yaml`의 `metric_waypoint_spacing`이 실제 데이터 spacing과 다르면 action normalization이 틀어집니다.

### 학습이 shared memory 에러로 종료됨

`Unexpected bus error encountered in worker`가 보이면 Docker shared memory 부족입니다. `--ipc=host`를 사용하거나 `batch_size`, `num_workers`를 낮추세요.

### 시뮬레이션 성공을 실제 로봇 성공으로 착각

시뮬레이션은 카메라 노이즈, 조명, 바닥 마찰, 지연이 실제와 다릅니다. 실제 로봇 검증은 별도 안전 절차가 필요합니다.
