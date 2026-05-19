"""
LLM Risk Reasoning Layer — §9.7 研究指南

A reasoning layer over RiskController that adjusts position sizing based on
LLM assessment of current market conditions. Principles (§9.7):
  - NOT a replacement for risk engine — output only scales position allocation
  - Does NOT directly issue orders (preserves low-latency execution path)
  - Generates natural language audit trail to logs/llm_risk_audit.jsonl
  - Cached for 1 hour (TTL) to avoid excessive API calls

API priority: MINIMAX_API_KEY → ANTHROPIC_API_KEY → fallback 1.0 multiplier.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_AUDIT_LOG = Path(__file__).parent.parent.parent / "logs" / "llm_risk_audit.jsonl"
_CACHE_TTL_SEC = 3600  # refresh at most once per hour
_DEFAULT_MULTIPLIER = 1.0
_MULTIPLIER_FLOOR = 0.3
_MULTIPLIER_CAP = 1.3

_cached_multiplier: float = _DEFAULT_MULTIPLIER
_cache_expires: float = 0.0

_logger = logging.getLogger("kiro.llm_risk")


def _build_prompt(ctx: dict) -> str:
    return (
        "You are a risk manager for an algorithmic trading system (Kiro Quant V3).\n\n"
        "Current market conditions:\n"
        f"- VIX: {ctx.get('vix', 'N/A')} (regime: {ctx.get('regime', 'unknown')})\n"
        f"- Crude Oil: ${ctx.get('oil_price', 'N/A')}\n"
        f"- Portfolio drawdown: {ctx.get('drawdown_pct', 0):.1f}%\n"
        f"- Open positions: {ctx.get('open_positions', 0)}\n"
        f"- Daily P&L: {ctx.get('daily_pnl_pct', 0):.2f}%\n\n"
        "Recommend a position size multiplier where "
        "0.3 = very cautious, 1.0 = normal, 1.3 = aggressive.\n"
        "Respond ONLY with valid JSON:\n"
        '{"multiplier": <float 0.3-1.3>, "reasoning": "<one sentence>"}'
    )


def _call_minimax(prompt: str) -> Optional[tuple[float, str]]:
    """Call MiniMax abab6.5s-chat API."""
    api_key = os.getenv("MINIMAX_API_KEY")
    if not api_key:
        return None
    try:
        import urllib.request
        payload = json.dumps({
            "model": "abab6.5s-chat",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 120,
        }).encode()
        req = urllib.request.Request(
            "https://api.minimax.chat/v1/text/chatcompletion_v2",
            data=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"].strip()
        parsed = json.loads(text)
        mult = max(_MULTIPLIER_FLOOR, min(_MULTIPLIER_CAP, float(parsed["multiplier"])))
        return mult, str(parsed.get("reasoning", ""))
    except Exception:
        return None


def _call_anthropic(prompt: str) -> Optional[tuple[float, str]]:
    """Call Anthropic Claude Haiku as fallback."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        parsed = json.loads(text)
        mult = max(_MULTIPLIER_FLOOR, min(_MULTIPLIER_CAP, float(parsed["multiplier"])))
        return mult, str(parsed.get("reasoning", ""))
    except Exception:
        return None


def _write_audit(ctx: dict, multiplier: float, reasoning: str, source: str) -> None:
    try:
        _AUDIT_LOG.parent.mkdir(exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "multiplier": multiplier,
            "reasoning": reasoning,
            "source": source,
            **{k: v for k, v in ctx.items() if isinstance(v, (str, int, float, bool))},
        }
        with open(_AUDIT_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def get_cached_risk_multiplier() -> float:
    """Return cached LLM multiplier without blocking. Returns 1.0 if cache is stale."""
    if time.time() < _cache_expires:
        return _cached_multiplier
    return _DEFAULT_MULTIPLIER


def refresh_risk_multiplier(ctx: dict) -> float:
    """Fetch a fresh LLM risk multiplier and update the in-process cache.

    Designed to run in a background thread (asyncio.to_thread). Safe to call
    concurrently — worst case writes the cache twice with the same value.

    Args:
        ctx: dict with keys vix, oil_price, regime, drawdown_pct, open_positions

    Returns:
        multiplier float in [0.3, 1.3]
    """
    global _cached_multiplier, _cache_expires

    prompt = _build_prompt(ctx)
    result = _call_minimax(prompt) or _call_anthropic(prompt)

    if result is not None:
        multiplier, reasoning = result
        source = "minimax" if os.getenv("MINIMAX_API_KEY") else "anthropic"
    else:
        multiplier = _DEFAULT_MULTIPLIER
        reasoning = "no API key configured"
        source = "fallback"

    _cached_multiplier = multiplier
    _cache_expires = time.time() + _CACHE_TTL_SEC
    _write_audit(ctx, multiplier, reasoning, source)
    _logger.info(
        "[LLM_RISK] mult=%.2f source=%s | %s",
        multiplier, source, reasoning,
    )
    return multiplier
