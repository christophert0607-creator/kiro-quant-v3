from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import time


def _run(cmd: list[str]) -> None:
    print("[SCHED]", " ".join(cmd))
    subprocess.Popen(cmd)


def schedule_once() -> str:
    now = dt.datetime.now().time()
    h = now.hour

    # 00:00-08:00 -> A + B
    if 0 <= h < 8:
        _run(["python", "v3_pipeline/models/trainer_all_symbols.py", "--once"])
        _run(["python", "v3_pipeline/models/hyperparam_search.py", "--trials", "12"])
        return "A+B"

    # 08:00-16:00 -> A
    if 8 <= h < 16:
        _run(["python", "v3_pipeline/models/trainer_all_symbols.py", "--once"])
        return "A"

    # 16:00-00:00 -> A + C
    _run(["python", "v3_pipeline/models/trainer_all_symbols.py", "--once"])
    _run(["python", "v3_pipeline/rl/rl_trainer.py", "--timesteps", "5000"])
    return "A+C"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--every-minutes", type=int, default=30)
    args = parser.parse_args()

    if not args.loop:
        mode = schedule_once()
        print(f"Scheduled mode: {mode}")
        return

    while True:
        mode = schedule_once()
        print(f"Scheduled mode: {mode}")
        time.sleep(max(60, args.every_minutes * 60))


if __name__ == "__main__":
    main()
