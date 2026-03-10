#!/usr/bin/env python3
"""Quick benchmark for launcher runtime profiles (dry-run path)."""

from __future__ import annotations

import argparse
import subprocess
import time
import tracemalloc
from statistics import mean

PROFILES = ("micro", "lite", "standard")


def run_once(profile: str) -> float:
    start = time.perf_counter()
    subprocess.run(
        ["python", "v3_launcher.py", "--dry-run", "--profile", profile],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return (time.perf_counter() - start) * 1000.0


def benchmark(profile: str, rounds: int) -> tuple[float, float, float]:
    samples = []
    tracemalloc.start()
    for _ in range(rounds):
        samples.append(run_once(profile))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return min(samples), mean(samples), peak / 1024.0 / 1024.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark launcher dry-run by profile")
    parser.add_argument("--rounds", type=int, default=5, help="Rounds per profile")
    args = parser.parse_args()

    print("| profile | min_ms | avg_ms | peak_mem_mb |")
    print("|---|---:|---:|---:|")
    for profile in PROFILES:
        min_ms, avg_ms, peak_mem = benchmark(profile, rounds=args.rounds)
        print(f"| {profile} | {min_ms:.2f} | {avg_ms:.2f} | {peak_mem:.4f} |")


if __name__ == "__main__":
    main()
