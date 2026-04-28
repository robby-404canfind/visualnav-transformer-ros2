# r1 시뮬레이터 NoMaD 파인튜닝 가이드

이 문서는 `/r1/camera1/image_raw`와 `/r1/odom`을 사용해 r1 시뮬레이터 주행 데이터를 수집하고, NoMaD 모델을 직사각형 검정 트랙 환경에 맞게 파인튜닝하는 절차를 설명합니다.

---

## 1. 목표

현재 pretrained NoMaD 모델은 일반적인 실내/실외 주행 데이터에 기반합니다. 직사각형 검정 트랙처럼 단순하지만 특수한 시뮬레이션 환경에서는 다음 문제가 나타날 수 있습니다.

- `explore.py`가 초반에는 트랙을 따라가다가 이후 트랙 밖으로 이탈
- topomap을 만들어 `navigate.py`를 실행해도 유사하게 이탈

파인튜닝의 목표는 모델이 다음 행동을 더 잘 학습하도록, 해당 트랙에서 사람이 직접 잘 주행한 데이터를 추가로 학습시키는 것입니다.

---

## 2. pose source 선택: `/r1/odom` vs `/r1/amcl_pose`

### 결론

학습 라벨에는 기본적으로 **`/r1/odom`을 사용합니다.**

### 이유

NoMaD/ViNT 계열 학습에서 `traj_data.pkl`의 `position`, `yaw`는 전역 지도상의 절대 위치라기보다, 시간에 따라 연속적인 로봇 궤적을 만들기 위한 기준입니다. 학습 시에는 현재 pose 기준 local waypoint를 계산하므로, pose가 부드럽고 연속적인 것이 중요합니다.

| 후보 | 장점 | 단점 | 판단 |
|---|---|---|---|
| `/r1/odom` | 연속적이고 부드러움, 주행 행동 라벨 생성에 적합 | 장시간 누적 drift 가능 | **기본 권장** |
| `/r1/amcl_pose` | map 기준 위치와 맞음 | localization correction 때문에 pose jump 가능 | 선택 옵션 |

### 현업 관점 판단

- 짧은 closed-loop 트랙 데이터 수집에는 `/r1/odom`이 더 안전합니다.
- `/r1/amcl_pose`는 map frame 기준으로 정확해 보일 수 있지만, AMCL 재보정으로 pose가 튀면 학습 라벨에 비현실적인 순간 이동이 들어갑니다.
- 만약 장시간/대규모 맵에서 odom drift가 커진다면 `/r1/amcl_pose`를 후보로 비교하되, `inspect_dataset.py`로 jump를 반드시 검사해야 합니다.

---

## 3. 좋은 학습 데이터 조건

`explore.py`나 실패한 navigation 결과를 그대로 학습시키면, 트랙을 벗어나는 행동도 같이 학습됩니다. 따라서 데이터는 반드시 사람이 수동으로 잘 주행한 expert trajectory여야 합니다.

추천 수집 조건:

- 시계방향 20바퀴 이상
- 반시계방향 20바퀴 이상
- 시작 위치를 다양하게 변경
- 코너 구간을 충분히 많이 포함
- 일부러 살짝 흔들린 뒤 트랙으로 복귀하는 데이터 포함
- 충돌, 정지, 트랙 이탈 구간은 데이터셋에서 제외

---

## 4. 데이터 수집

컨테이너 실행 예시입니다. 학습 데이터와 결과가 컨테이너 종료 후에도 남도록 `datasets`, `data_splits`, `logs`, `model_weights`를 볼륨으로 연결합니다.

```bash
mkdir -p datasets data_splits src/visualnav_transformer/train/logs model_weights

docker run -it --rm --gpus=all --net=host \
  --env ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-0} \
  --env VNT_IMAGE_TOPIC=/r1/camera1/image_raw \
  --env VNT_CMD_VEL_TOPIC=/r1/cmd_vel \
  -v $(pwd)/datasets:/visualnav-transformer/datasets \
  -v $(pwd)/data_splits:/visualnav-transformer/data_splits \
  -v $(pwd)/src/visualnav_transformer/train/logs:/visualnav-transformer/src/visualnav_transformer/train/logs \
  -v $(pwd)/model_weights:/visualnav-transformer/model_weights \
  visualnav_transformer:latest
```

컨테이너 안에서 trajectory 하나를 기록합니다.

```bash
poetry run python scripts/export_ros2_dataset.py \
  --image-topic /r1/camera1/image_raw \
  --pose-source odom \
  --pose-topic /r1/odom \
  --output-dir datasets/r1_track \
  --traj-name traj_000 \
  --rate 4.0 \
  --min-distance 0.05
```

이 상태에서 로봇을 수동으로 트랙을 따라 주행합니다. 종료는 `Ctrl+C`입니다.

저장 결과:

```text
datasets/r1_track/traj_000/
  0.jpg
  1.jpg
  2.jpg
  ...
  traj_data.pkl
```

여러 trajectory를 수집할 때는 `--traj-name traj_001`, `traj_002`처럼 이름을 바꿔 반복합니다.

---

## 5. 데이터 품질 검사

수집 후 반드시 데이터 품질을 확인합니다.

```bash
poetry run python scripts/inspect_dataset.py datasets/r1_track
```

출력에서 확인할 것:

- `samples`: 각 trajectory 샘플 수
- `median_spacing`: 평균적인 프레임 간 이동거리
- `large_jumps`: 1m 이상 pose jump 개수
- `max_yaw_step`: yaw가 갑자기 튀는지

`large_jumps`가 0이 아니면 해당 trajectory는 학습에서 제외하거나 다시 수집하는 것이 좋습니다.

`recommended metric_waypoint_spacing` 값은 다음 파일의 `r1_track.metric_waypoint_spacing`에 반영합니다.

```text
src/visualnav_transformer/train/vint_train/data/data_config.yaml
```

---

## 6. train/test split 생성

저장소 루트에서 실행합니다.

```bash
poetry run python src/visualnav_transformer/train/data_split.py \
  --data-dir datasets/r1_track \
  --dataset-name r1_track \
  --data-splits-dir data_splits \
  --split 0.8
```

결과:

```text
data_splits/r1_track/train/traj_names.txt
data_splits/r1_track/test/traj_names.txt
```

---

## 7. pretrained NoMaD checkpoint 준비

`nomad_r1.yaml`은 `load_run: pretrained_nomad`를 사용합니다. 따라서 pretrained checkpoint를 다음 위치에 둡니다.

```bash
mkdir -p src/visualnav_transformer/train/logs/pretrained_nomad
cp model_weights/nomad.pth src/visualnav_transformer/train/logs/pretrained_nomad/latest.pth
```

---

## 8. 파인튜닝 실행

학습은 위 볼륨 마운트가 적용된 컨테이너 안에서 실행하는 것을 권장합니다. 학습 스크립트는 `src/visualnav_transformer/train` 디렉터리를 기준으로 실행합니다.

```bash
cd src/visualnav_transformer/train
poetry run python train.py --config config/nomad_r1.yaml
```

학습 결과는 다음 경로에 저장됩니다.

```text
src/visualnav_transformer/train/logs/nomad_r1/<run_name>/latest.pth
src/visualnav_transformer/train/logs/nomad_r1/<run_name>/ema_latest.pth
```

일반적으로 inference에는 `ema_latest.pth`를 우선 검토합니다. 단, 현재 deployment의 `load_model()`은 NoMaD state_dict를 그대로 읽을 수 있으므로, 둘 다 테스트해 볼 수 있습니다.

---

## 9. 파인튜닝 모델로 시뮬레이션 검증

학습된 checkpoint를 `model_weights` 아래로 복사합니다.

```bash
cp src/visualnav_transformer/train/logs/nomad_r1/<run_name>/ema_latest.pth \
  model_weights/nomad_r1_ema.pth
```

`config/models.yaml`에 새 항목을 추가하거나 기존 `nomad`의 `ckpt_path`를 바꿔 실행합니다.

예시:

```yaml
nomad_r1:
  config_path: "src/visualnav_transformer/train/config/nomad_r1.yaml"
  ckpt_path: "model_weights/nomad_r1_ema.pth"
```

실행:

```bash
poetry run python src/visualnav_transformer/deployment/src/explore.py --model nomad_r1
```

---

## 10. 흔한 실패 원인

### 데이터가 너무 적음

직사각형 트랙 한두 바퀴로는 코너/복귀 행동이 부족합니다. 최소 수십 바퀴를 권장합니다.

### 실패 주행을 학습함

트랙 이탈 데이터가 들어가면 이탈 행동이 강화됩니다.

### pose jump가 있음

AMCL 또는 시뮬레이터 리셋으로 pose가 튀면 local waypoint 라벨이 깨집니다. `inspect_dataset.py`로 확인하세요.

### metric_waypoint_spacing 불일치

`data_config.yaml`의 `metric_waypoint_spacing`이 실제 데이터 spacing과 다르면 action normalization이 틀어집니다.

### 시뮬레이션 성공을 실제 로봇 성공으로 착각

시뮬레이션은 카메라 노이즈, 조명, 바닥 마찰, 지연이 실제와 다릅니다. 실제 로봇 검증은 별도 안전 절차가 필요합니다.
