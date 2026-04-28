#!/usr/bin/env python3
"""Record a ROS2 image + pose stream into the ViNT/NoMaD training dataset format.

The training code expects trajectories like:

    datasets/r1_track/traj_000/
      0.jpg
      1.jpg
      ...
      traj_data.pkl

`traj_data.pkl` contains at least:

    {
        "position": np.ndarray[N, 2],  # x, y in meters
        "yaw": np.ndarray[N],          # heading in radians
    }

For fine-tuning navigation policies, use a smooth local pose source by default.
In the r1 simulator that is `/r1/odom`. `/r1/amcl_pose` is supported, but it can
jump after localization corrections and should be used only with care.
"""

from __future__ import annotations

import argparse
import math
import os
import pickle
import shutil
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Optional, Tuple

import cv2
import numpy as np
import rclpy
from cv_bridge import CvBridge
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import Image


@dataclass
class PoseSample:
    stamp: float
    x: float
    y: float
    yaw: float


@dataclass
class ImageSample:
    stamp: float
    msg: Image


def stamp_to_sec(msg) -> float:
    stamp = msg.header.stamp
    if stamp.sec == 0 and stamp.nanosec == 0:
        return time.time()
    return float(stamp.sec) + float(stamp.nanosec) * 1e-9


def yaw_from_quaternion(q) -> float:
    # Standard yaw extraction from quaternion (x, y, z, w).
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


def pose_from_odometry(msg: Odometry) -> PoseSample:
    pose = msg.pose.pose
    return PoseSample(
        stamp=stamp_to_sec(msg),
        x=float(pose.position.x),
        y=float(pose.position.y),
        yaw=float(yaw_from_quaternion(pose.orientation)),
    )


def pose_from_amcl(msg: PoseWithCovarianceStamped) -> PoseSample:
    pose = msg.pose.pose
    return PoseSample(
        stamp=stamp_to_sec(msg),
        x=float(pose.position.x),
        y=float(pose.position.y),
        yaw=float(yaw_from_quaternion(pose.orientation)),
    )


class DatasetRecorder(Node):
    def __init__(self, args: argparse.Namespace):
        super().__init__("vnt_dataset_recorder")
        self.args = args
        self.bridge = CvBridge()
        self.pose_buffer: Deque[PoseSample] = deque(maxlen=args.pose_buffer_size)
        self.image_buffer: Deque[ImageSample] = deque(maxlen=args.image_buffer_size)
        self.last_saved_pose: Optional[PoseSample] = None
        self.last_saved_wall_time = 0.0
        self.sample_count = 0
        self.positions = []
        self.yaws = []
        self.timestamps = []

        self.traj_dir = self._prepare_output_dir(args)
        self.get_logger().info(f"Recording trajectory to: {self.traj_dir}")
        self.get_logger().info(
            f"image_topic={args.image_topic}, pose_topic={args.pose_topic}, pose_source={args.pose_source}"
        )

        self.create_subscription(Image, args.image_topic, self.image_callback, 10)
        if args.pose_source == "odom":
            self.create_subscription(Odometry, args.pose_topic, self.odom_callback, 50)
        elif args.pose_source == "amcl":
            self.create_subscription(
                PoseWithCovarianceStamped, args.pose_topic, self.amcl_callback, 50
            )
        else:
            raise ValueError(f"Unsupported pose source: {args.pose_source}")

        self.timer = self.create_timer(1.0 / args.rate, self.timer_callback)

    def _prepare_output_dir(self, args: argparse.Namespace) -> Path:
        output_dir = Path(args.output_dir).expanduser().resolve()
        traj_name = args.traj_name or time.strftime("traj_%Y%m%d_%H%M%S")
        traj_dir = output_dir / traj_name
        if traj_dir.exists():
            if not args.overwrite:
                raise FileExistsError(
                    f"{traj_dir} already exists. Use --overwrite or another --traj-name."
                )
            shutil.rmtree(traj_dir)
        traj_dir.mkdir(parents=True, exist_ok=True)
        return traj_dir

    def image_callback(self, msg: Image) -> None:
        self.image_buffer.append(ImageSample(stamp=stamp_to_sec(msg), msg=msg))

    def odom_callback(self, msg: Odometry) -> None:
        self.pose_buffer.append(pose_from_odometry(msg))

    def amcl_callback(self, msg: PoseWithCovarianceStamped) -> None:
        self.pose_buffer.append(pose_from_amcl(msg))

    def timer_callback(self) -> None:
        if not self.image_buffer or not self.pose_buffer:
            self.get_logger().info("Waiting for both image and pose topics...", throttle_duration_sec=2.0)
            return

        image = self.image_buffer[-1]
        pose = self._nearest_pose(image.stamp)
        if pose is None:
            self.get_logger().warn("No synchronized pose found for latest image.", throttle_duration_sec=2.0)
            return

        sync_dt = abs(image.stamp - pose.stamp)
        if sync_dt > self.args.max_sync_dt:
            self.get_logger().warn(
                f"Image/pose sync gap too large: {sync_dt:.3f}s > {self.args.max_sync_dt:.3f}s",
                throttle_duration_sec=2.0,
            )
            return

        if self.last_saved_pose is not None:
            dist = math.hypot(pose.x - self.last_saved_pose.x, pose.y - self.last_saved_pose.y)
            if dist < self.args.min_distance:
                return

        if time.time() - self.last_saved_wall_time < 1.0 / self.args.rate * 0.5:
            return

        self._save_sample(image, pose)

    def _nearest_pose(self, stamp: float) -> Optional[PoseSample]:
        if not self.pose_buffer:
            return None
        return min(self.pose_buffer, key=lambda p: abs(p.stamp - stamp))

    def _save_sample(self, image: ImageSample, pose: PoseSample) -> None:
        cv_img = self.bridge.imgmsg_to_cv2(image.msg, desired_encoding="bgr8")
        img_path = self.traj_dir / f"{self.sample_count}.jpg"
        ok = cv2.imwrite(str(img_path), cv_img, [int(cv2.IMWRITE_JPEG_QUALITY), self.args.jpeg_quality])
        if not ok:
            raise RuntimeError(f"Failed to write image: {img_path}")

        self.positions.append([pose.x, pose.y])
        self.yaws.append(pose.yaw)
        self.timestamps.append(image.stamp)
        self.last_saved_pose = pose
        self.last_saved_wall_time = time.time()
        self.sample_count += 1

        if self.sample_count % self.args.save_every == 0:
            self.write_traj_data()

        if self.sample_count % 20 == 0:
            spacing = self.estimated_spacing()
            self.get_logger().info(
                f"saved {self.sample_count} samples; estimated spacing={spacing:.3f} m"
            )

    def estimated_spacing(self) -> float:
        if len(self.positions) < 2:
            return 0.0
        arr = np.asarray(self.positions, dtype=np.float32)
        d = np.linalg.norm(np.diff(arr, axis=0), axis=1)
        if len(d) == 0:
            return 0.0
        return float(np.median(d))

    def write_traj_data(self) -> None:
        data = {
            "position": np.asarray(self.positions, dtype=np.float32),
            "yaw": np.asarray(self.yaws, dtype=np.float32),
            "timestamps": np.asarray(self.timestamps, dtype=np.float64),
            "image_topic": self.args.image_topic,
            "pose_topic": self.args.pose_topic,
            "pose_source": self.args.pose_source,
            "metric_waypoint_spacing_estimate": self.estimated_spacing(),
            "min_distance": self.args.min_distance,
            "rate": self.args.rate,
        }
        tmp_path = self.traj_dir / "traj_data.pkl.tmp"
        final_path = self.traj_dir / "traj_data.pkl"
        with tmp_path.open("wb") as f:
            pickle.dump(data, f)
        os.replace(tmp_path, final_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record ROS2 image + pose topics into ViNT/NoMaD training dataset format."
    )
    parser.add_argument(
        "--image-topic",
        default=os.environ.get("VNT_IMAGE_TOPIC", "/r1/camera1/image_raw"),
        help="Image topic to record. Default: env VNT_IMAGE_TOPIC or /r1/camera1/image_raw",
    )
    parser.add_argument(
        "--pose-source",
        choices=["odom", "amcl"],
        default="odom",
        help="Pose message type/source. Use odom for smooth training labels; amcl only if you intentionally want map-localized poses.",
    )
    parser.add_argument(
        "--pose-topic",
        default=None,
        help="Pose topic. Default: /r1/odom for odom, /r1/amcl_pose for amcl.",
    )
    parser.add_argument(
        "--output-dir",
        default="datasets/r1_track",
        help="Dataset root. Trajectory folder is created under this directory.",
    )
    parser.add_argument("--traj-name", default=None, help="Trajectory folder name. Default: timestamped traj_YYYYmmdd_HHMMSS")
    parser.add_argument("--rate", type=float, default=4.0, help="Maximum saved sample rate in Hz.")
    parser.add_argument(
        "--min-distance",
        type=float,
        default=0.05,
        help="Minimum robot translation between saved frames in meters. Prevents many near-duplicate frames.",
    )
    parser.add_argument("--max-sync-dt", type=float, default=0.2, help="Maximum allowed image/pose timestamp gap in seconds.")
    parser.add_argument("--jpeg-quality", type=int, default=95, help="JPEG quality for saved images.")
    parser.add_argument("--save-every", type=int, default=10, help="Rewrite traj_data.pkl every N saved samples.")
    parser.add_argument("--pose-buffer-size", type=int, default=300)
    parser.add_argument("--image-buffer-size", type=int, default=30)
    parser.add_argument("--overwrite", action="store_true", help="Overwrite trajectory directory if it already exists.")
    args = parser.parse_args()
    if args.pose_topic is None:
        args.pose_topic = "/r1/odom" if args.pose_source == "odom" else "/r1/amcl_pose"
    if args.rate <= 0:
        raise ValueError("--rate must be positive")
    if args.min_distance < 0:
        raise ValueError("--min-distance must be non-negative")
    return args


def main() -> None:
    args = parse_args()
    rclpy.init()
    node = DatasetRecorder(args)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.write_traj_data()
        node.get_logger().info(
            f"Finished. Saved {node.sample_count} samples to {node.traj_dir}. "
            f"Estimated spacing={node.estimated_spacing():.3f} m"
        )
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
