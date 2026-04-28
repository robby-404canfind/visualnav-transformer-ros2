#!/usr/bin/env python3
"""Inspect trajectories exported for ViNT/NoMaD fine-tuning."""

from __future__ import annotations

import argparse
import pickle
from pathlib import Path

import numpy as np


def inspect_traj(traj_dir: Path) -> dict:
    with (traj_dir / "traj_data.pkl").open("rb") as f:
        data = pickle.load(f)
    positions = np.asarray(data["position"], dtype=np.float32)
    yaws = np.asarray(data["yaw"], dtype=np.float32)
    image_count = len(list(traj_dir.glob("*.jpg")))
    dists = np.linalg.norm(np.diff(positions, axis=0), axis=1) if len(positions) > 1 else np.array([])
    yaw_jumps = np.abs(np.diff(np.unwrap(yaws))) if len(yaws) > 1 else np.array([])
    return {
        "traj": traj_dir.name,
        "samples": len(positions),
        "images": image_count,
        "median_spacing": float(np.median(dists)) if len(dists) else 0.0,
        "mean_spacing": float(np.mean(dists)) if len(dists) else 0.0,
        "max_spacing": float(np.max(dists)) if len(dists) else 0.0,
        "large_jumps": int(np.sum(dists > 1.0)) if len(dists) else 0,
        "max_yaw_step": float(np.max(yaw_jumps)) if len(yaw_jumps) else 0.0,
        "pose_source": data.get("pose_source", "unknown"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect exported ViNT/NoMaD dataset trajectories.")
    parser.add_argument("data_dir", help="Dataset root containing trajectory folders")
    args = parser.parse_args()

    root = Path(args.data_dir).expanduser().resolve()
    traj_dirs = sorted(p for p in root.iterdir() if (p / "traj_data.pkl").exists())
    if not traj_dirs:
        raise SystemExit(f"No trajectories with traj_data.pkl found in {root}")

    rows = [inspect_traj(p) for p in traj_dirs]
    print("traj,samples,images,median_spacing,mean_spacing,max_spacing,large_jumps,max_yaw_step,pose_source")
    for r in rows:
        print(
            f"{r['traj']},{r['samples']},{r['images']},{r['median_spacing']:.4f},"
            f"{r['mean_spacing']:.4f},{r['max_spacing']:.4f},{r['large_jumps']},"
            f"{r['max_yaw_step']:.4f},{r['pose_source']}"
        )

    all_spacings = []
    total_samples = 0
    total_jumps = 0
    for p in traj_dirs:
        with (p / "traj_data.pkl").open("rb") as f:
            data = pickle.load(f)
        positions = np.asarray(data["position"], dtype=np.float32)
        total_samples += len(positions)
        if len(positions) > 1:
            d = np.linalg.norm(np.diff(positions, axis=0), axis=1)
            all_spacings.extend(d.tolist())
            total_jumps += int(np.sum(d > 1.0))

    if all_spacings:
        print("\nsummary")
        print(f"trajectories: {len(traj_dirs)}")
        print(f"samples: {total_samples}")
        print(f"recommended metric_waypoint_spacing: {np.median(all_spacings):.4f}")
        print(f"large jumps >1m: {total_jumps}")


if __name__ == "__main__":
    main()
