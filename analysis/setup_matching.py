"""analysis/setup_matching.py — Canonical pullback setup definition and similarity helpers."""
from __future__ import annotations

import config


_PULLBACK_STOCH_MAX = 35.0
_PULLBACK_ROC_MAX = -3.0
_RELAXED_PULLBACK_PRICE_BUFFER_PCT = 3.0
_RELAXED_PULLBACK_RSI_MAX = 52.0
_RELAXED_PULLBACK_STOCH_MAX = 45.0
_RELAXED_PULLBACK_ROC_MAX = -1.0


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build_pullback_setup_snapshot(
    *,
    price,
    sma50,
    sma200,
    rsi14,
    vol_ratio=None,
    drawdown=None,
    stoch_k=None,
    roc20=None,
    adx14=None,
    willi_r=None,
) -> dict:
    price = _safe_float(price)
    sma50 = _safe_float(sma50)
    sma200 = _safe_float(sma200)
    rsi14 = _safe_float(rsi14)
    vol_ratio = _safe_float(vol_ratio)
    drawdown = _safe_float(drawdown)
    stoch_k = _safe_float(stoch_k)
    roc20 = _safe_float(roc20)
    adx14 = _safe_float(adx14)
    willi_r = _safe_float(willi_r)

    price_vs_sma50_pct = None
    if price is not None and sma50 is not None and sma50 > 0:
        price_vs_sma50_pct = (sma50 - price) / sma50 * 100.0

    price_vs_sma200_pct = None
    if price is not None and sma200 is not None and sma200 > 0:
        price_vs_sma200_pct = (price - sma200) / sma200 * 100.0

    drawdown_pct = None
    if drawdown is not None:
        drawdown_pct = drawdown * 100.0 if abs(drawdown) <= 1.0 else drawdown

    return {
        "price": price,
        "sma50": sma50,
        "sma200": sma200,
        "rsi14": rsi14,
        "vol_ratio": vol_ratio,
        "drawdown_pct": drawdown_pct,
        "stoch_k": stoch_k,
        "roc20": roc20,
        "adx14": adx14,
        "williams_r": willi_r,
        "price_vs_sma50_pct": price_vs_sma50_pct,
        "price_vs_sma200_pct": price_vs_sma200_pct,
    }


def matches_pullback_setup(snapshot: dict, mode: str = "classic") -> bool:
    price = _safe_float(snapshot.get("price"))
    sma50 = _safe_float(snapshot.get("sma50"))
    sma200 = _safe_float(snapshot.get("sma200"))
    rsi14 = _safe_float(snapshot.get("rsi14"))
    stoch_k = _safe_float(snapshot.get("stoch_k"))
    roc20 = _safe_float(snapshot.get("roc20"))
    selected_mode = str(mode or "classic").strip().lower()

    if None in (price, sma50, sma200, rsi14):
        return False

    if selected_mode == "mixed":
        allowed_price = sma50 * (1.0 + _RELAXED_PULLBACK_PRICE_BUFFER_PCT / 100.0)
        if not (price >= sma200 and price <= allowed_price and rsi14 <= _RELAXED_PULLBACK_RSI_MAX):
            return False
        if stoch_k is not None and stoch_k > _RELAXED_PULLBACK_STOCH_MAX:
            return False
        if roc20 is not None and roc20 > _RELAXED_PULLBACK_ROC_MAX:
            return False
        return True

    if not (price >= sma200 and price <= sma50 and rsi14 <= config.OPP_RSI_MAX):
        return False
    if stoch_k is not None and stoch_k > _PULLBACK_STOCH_MAX:
        return False
    if roc20 is not None and roc20 > _PULLBACK_ROC_MAX:
        return False
    return True


def pullback_setup_distance(target: dict, candidate: dict) -> float:
    feature_defs = (
        ("rsi14", 6.0, 0.24),
        ("price_vs_sma50_pct", 3.0, 0.20),
        ("price_vs_sma200_pct", 5.0, 0.12),
        ("drawdown_pct", 8.0, 0.14),
        ("vol_ratio", 0.60, 0.10),
        ("stoch_k", 12.0, 0.10),
        ("roc20", 4.0, 0.06),
        ("adx14", 10.0, 0.02),
        ("williams_r", 18.0, 0.02),
    )

    weighted_gap = 0.0
    total_weight = 0.0

    for feature, scale, weight in feature_defs:
        target_value = _safe_float(target.get(feature))
        candidate_value = _safe_float(candidate.get(feature))
        if target_value is None or candidate_value is None:
            continue
        gap = _clip(abs(target_value - candidate_value) / scale, 0.0, 1.75)
        weighted_gap += gap * weight
        total_weight += weight

    if total_weight <= 0:
        return 1.0
    return weighted_gap / total_weight