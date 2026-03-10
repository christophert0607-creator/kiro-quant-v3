from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from v3_pipeline.rl.trading_env import TradingEnv


def run_rl_training(frame: pd.DataFrame, total_timesteps: int = 20000, report_every: int = 1000) -> dict:
    try:
        from stable_baselines3 import PPO
    except Exception as exc:
        return {"status": f"missing_dependency:{exc}"}

    env = TradingEnv(frame=frame, initial_cash=10000.0)
    model = PPO("MlpPolicy", env, verbose=0)

    reports = []
    remaining = total_timesteps
    while remaining > 0:
        step = min(report_every, remaining)
        model.learn(total_timesteps=step, reset_num_timesteps=False)
        remaining -= step

        obs, _ = env.reset()
        done = False
        total_reward = 0.0
        info = {}
        while not done:
            action, _state = model.predict(obs, deterministic=True)
            obs, reward, terminated, truncated, info = env.step(int(action))
            total_reward += reward
            done = terminated or truncated

        reports.append(
            {
                "trained_steps": total_timesteps - remaining,
                "episode_reward": float(total_reward),
                "equity": float(info.get("equity", 0.0)),
                "sharpe": float(info.get("sharpe", 0.0)),
                "max_drawdown": float(info.get("max_drawdown", 0.0)),
            }
        )

    return {"status": "ok", "reports": reports}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="v3_pipeline/reports/rl_training_data.parquet")
    parser.add_argument("--timesteps", type=int, default=5000)
    args = parser.parse_args()

    p = Path(args.data)
    if not p.exists():
        result = {"status": "no_data_file", "path": str(p)}
    else:
        df = pd.read_parquet(p)
        result = run_rl_training(df, total_timesteps=args.timesteps)

    out = Path("v3_pipeline/reports/rl_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"saved {out}")


if __name__ == "__main__":
    main()
