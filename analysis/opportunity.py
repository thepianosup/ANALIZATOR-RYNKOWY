"""analysis/opportunity.py — Score tickers and produce deep analysis reports."""
import sys
import os
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import numpy as np
import config
from data.fetcher import get_ohlcv_4h, get_info, get_latest_price
from analysis.company_analysis import get_company_snapshot
from analysis.indicators import add_indicators
from analysis.macro_analysis import get_macro_snapshot
from analysis.setup_matching import (
    build_pullback_setup_snapshot,
    matches_pullback_setup,
    pullback_setup_distance,
)

# 4H bars per trading period (US market ~2 bars/day)
_BARS_1W  = 10   # ~5 trading days × 2
_BARS_1M  = 44   # ~22 trading days × 2
_BARS_3M  = 132  # ~66 trading days × 2
_BARS_1Y  = 500  # ~252 trading days × 2
_RECENT_HISTORY_LOOKBACK_BARS = _BARS_1M

OPPORTUNITY_PROFILES = [
    ("balanced", "Standard"),
]

_RANK_HORIZON_WEIGHTS = {
    "all": {"22d": 0.55, "66d": 0.30, "5d": 0.15},
    "22d": {"22d": 0.70, "66d": 0.20, "5d": 0.10},
    "66d": {"66d": 0.60, "22d": 0.30, "5d": 0.10},
    "5d": {"5d": 0.55, "22d": 0.30, "66d": 0.15},
}

_PROFILE_COMPONENT_WEIGHTS = {
    "aggressive": {"history": 0.56, "financial": 0.11, "technical": 0.19, "event": 0.08, "macro": 0.06, "sample_floor": 0.55},
    "balanced": {"history": 0.65, "financial": 0.15, "technical": 0.10, "event": 0.06, "macro": 0.04, "sample_floor": 0.45},
    "conservative": {"history": 0.58, "financial": 0.22, "technical": 0.06, "event": 0.06, "macro": 0.08, "sample_floor": 0.35},
}

_PROFILE_SAMPLE_TARGETS = {
    "aggressive": {"5d": 8.0, "22d": 14.0, "66d": 12.0},
    "balanced": {"5d": 10.0, "22d": 20.0, "66d": 16.0},
    "conservative": {"5d": 12.0, "22d": 26.0, "66d": 22.0},
}


# ─────────────────────────────────────────────────────────────────────────────
def _safe(val, default=None):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    return val


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_or_none(value, digits: int) -> float | None:
    number = _safe_float(value)
    if number is None:
        return None
    return round(number, digits)


def _snapshot_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_price_snapshot(snapshot: dict | None) -> dict | None:
    if not isinstance(snapshot, dict):
        return None

    price = _safe_float(snapshot.get("price"))
    if price is None or price <= 0:
        return None

    captured_at = str(snapshot.get("captured_at") or snapshot.get("snapshot_time") or _snapshot_now_iso())
    source = str(snapshot.get("source") or "provided_snapshot")
    source_label = str(snapshot.get("source_label") or "snapshot z listy okazji")
    return {
        "price": float(price),
        "captured_at": captured_at,
        "source": source,
        "source_label": source_label,
    }


def _build_price_snapshot(
    ticker: str,
    fallback_price: float | None = None,
    *,
    force_refresh: bool = False,
    snapshot: dict | None = None,
) -> dict | None:
    normalized = _normalize_price_snapshot(snapshot)
    if normalized is not None:
        return normalized

    captured_at = _snapshot_now_iso()
    live_price = get_latest_price(ticker, force_refresh=force_refresh)
    if live_price and live_price > 0:
        return {
            "price": float(live_price),
            "captured_at": captured_at,
            "source": "live_refresh" if force_refresh else "live_price_cached",
            "source_label": "live refresh" if force_refresh else "live cena (cache do 15 min)",
        }

    fallback = _safe_float(fallback_price)
    if fallback is not None and fallback > 0:
        return {
            "price": float(fallback),
            "captured_at": captured_at,
            "source": "last_4h_close",
            "source_label": "fallback do ostatniej świecy 4H",
        }
    return None


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _label_for_profile(profile: str) -> str:
    return dict(OPPORTUNITY_PROFILES).get("balanced", "Standard")


def _normalize_profile(profile: str | None) -> str:
    """Opportunity ranking now uses one standard mode regardless of UI selections."""
    return "balanced"


def _label_for_confidence(score: float) -> str:
    if score >= 75:
        return "Wysoka"
    if score >= 45:
        return "Średnia"
    return "Niska"


def _direction(value: float | None, threshold: float = 0.25) -> int:
    if value is None or abs(value) < threshold:
        return 0
    return 1 if value > 0 else -1


def _history_sample_confidence(sample_count: float | None, target_samples: float = 24.0) -> float:
    samples = max(0.0, float(sample_count or 0.0))
    if samples <= 0:
        return 0.0
    return float(_clip(np.log1p(samples) / np.log1p(max(2.0, target_samples)), 0.0, 1.0))


def _history_horizon_rank(details: dict, suffix: str, profile: str = "balanced") -> float:
    win_rate = _safe_float(details.get(f"win_rate_{suffix}"))
    avg_return = _safe_float(details.get(f"avg_return_{suffix}"))
    median_return = _safe_float(details.get(f"median_return_{suffix}"))
    ev = _safe_float(details.get(f"ev_{suffix}"))
    sample_count = _safe_float(details.get(f"n_setups_{suffix}"))

    if win_rate is None and avg_return is None and median_return is None:
        return 0.0

    win_component = _clip(((win_rate or 50.0) - 50.0) / 18.0, -1.0, 1.0)
    median_component = _clip((median_return or 0.0) / 6.0, -1.0, 1.0)
    avg_component = _clip((avg_return or 0.0) / 8.0, -1.0, 1.0)
    ev_component = _clip((ev or 0.0) / 3.0, 0.0, 1.0)
    target_samples = _PROFILE_SAMPLE_TARGETS.get(profile, _PROFILE_SAMPLE_TARGETS["balanced"]).get(suffix, 20.0)
    confidence = _history_sample_confidence(sample_count, target_samples)

    raw_score = (
        win_component * 0.35
        + median_component * 0.35
        + avg_component * 0.15
        + ev_component * 0.15
    )
    return raw_score * confidence


def _technical_context_rank(opportunity: dict) -> float:
    """Small live-technical bias layered on top of the history-driven rank."""
    details = opportunity.get("details") or {}
    raw_score = _clip((((opportunity.get("score") or 0) - 4.0) / 3.5), -1.0, 1.0)

    optional_signal_weights = {
        "rsi_low": 0.30,
        "deep_dip": 0.18,
        "vol_high": 0.15,
        "stoch_oversold": 0.12,
        "roc_negative": 0.10,
        "adx_trending": 0.09,
        "willy_oversold": 0.06,
    }
    signal_ratio = sum(
        weight
        for key, weight in optional_signal_weights.items()
        if details.get(key)
    )
    optional_signal_score = _clip((signal_ratio - 0.25) / 0.50, -1.0, 1.0)
    return _clip(raw_score * 0.60 + optional_signal_score * 0.40, -1.0, 1.0)


def get_opportunity_rank_breakdown(opportunity: dict, horizon: str = "all", profile: str = "balanced") -> dict:
    profile = _normalize_profile(profile)
    details = opportunity.get("details") or {}
    profile_cfg = _PROFILE_COMPONENT_WEIGHTS.get(profile, _PROFILE_COMPONENT_WEIGHTS["balanced"])
    weights = _RANK_HORIZON_WEIGHTS.get(horizon, _RANK_HORIZON_WEIGHTS["all"])

    history_components = {
        suffix: _history_horizon_rank(details, suffix, profile)
        for suffix in ("5d", "22d", "66d")
    }
    history_score = sum(weights.get(suffix, 0.0) * history_components[suffix] for suffix in history_components)

    technical_score = _technical_context_rank(opportunity)
    macro_score = _clip((((details.get("macro_score", opportunity.get("macro_score", 50)) or 50) - 50.0) / 35.0), -1.0, 1.0)
    financial_score = _clip((((details.get("financial_score", 50) or 50) - 50.0) / 30.0), -1.0, 1.0)
    event_score = _clip((((details.get("event_score", 50) or 50) - 50.0) / 35.0), -1.0, 1.0)

    weighted_sample_confidence = sum(
        weights.get(suffix, 0.0)
        * _history_sample_confidence(
            details.get(f"n_setups_{suffix}"),
            _PROFILE_SAMPLE_TARGETS.get(profile, _PROFILE_SAMPLE_TARGETS["balanced"]).get(suffix, 20.0),
        )
        for suffix in ("5d", "22d", "66d")
    )

    median_1m = _safe_float(details.get("median_return_22d"))
    median_3m = _safe_float(details.get("median_return_66d"))
    avg_1m = _safe_float(details.get("avg_return_22d"))
    avg_3m = _safe_float(details.get("avg_return_66d"))

    horizon_alignment = 0.55
    history_dirs = [_direction(median_1m), _direction(median_3m), _direction(avg_1m), _direction(avg_3m)]
    effective_dirs = [direction for direction in history_dirs if direction != 0]
    if len(effective_dirs) >= 2:
        horizon_alignment = 1.0 if len(set(effective_dirs)) == 1 else 0.20
    elif effective_dirs:
        horizon_alignment = 0.70

    company_alignment = 1.0 if history_score >= 0 and financial_score >= 0 else 0.65 if history_score >= 0 or financial_score >= 0 else 0.35
    confidence_score = round((weighted_sample_confidence * 0.55 + horizon_alignment * 0.25 + company_alignment * 0.20) * 100, 1)
    confidence_label = _label_for_confidence(confidence_score)

    if weighted_sample_confidence < 0.35:
        confidence_reason = "Mało podobnych historycznych setupów, więc ranking jest bardziej ostrożny."
    elif horizon_alignment < 0.4:
        confidence_reason = "Historia 1M i 3M nie jest dziś zgodna, więc przewaga setupu jest mniej stabilna."
    elif company_alignment < 0.5:
        confidence_reason = "Historia setupu wygląda lepiej niż sytuacja finansowa spółki, więc sygnał nie jest w pełni czysty."
    else:
        confidence_reason = "Historia setupów, liczba próbek i jakość spółki układają się dziś dość spójnie."

    raw_score = (
        history_score * profile_cfg["history"]
        + financial_score * profile_cfg["financial"]
        + technical_score * profile_cfg["technical"]
        + event_score * profile_cfg["event"]
        + macro_score * profile_cfg["macro"]
    )
    confidence_norm = confidence_score / 100.0
    shrink = profile_cfg.get("sample_floor", 0.45) + (1.0 - profile_cfg.get("sample_floor", 0.45)) * confidence_norm
    final_score = raw_score * shrink

    setup_quality_score = round((_clip(history_score * 0.78 + technical_score * 0.22, -1.0, 1.0) + 1.0) * 50.0, 1)
    company_quality_score = round((_clip(financial_score * 0.75 + event_score * 0.25, -1.0, 1.0) + 1.0) * 50.0, 1)
    macro_quality_score = round((macro_score + 1.0) * 50.0, 1)

    return {
        "score": round(final_score, 3),
        "raw_score": round(raw_score, 3),
        "history_score": round(history_score, 3),
        "history_components": {suffix: round(value, 3) for suffix, value in history_components.items()},
        "technical_score": round(technical_score, 3),
        "financial_score": round(financial_score, 3),
        "event_score": round(event_score, 3),
        "macro_score": round(macro_score, 3),
        "setup_quality_score": setup_quality_score,
        "company_quality_score": company_quality_score,
        "macro_quality_score": macro_quality_score,
        "confidence": {
            "score": confidence_score,
            "label": confidence_label,
            "reason": confidence_reason,
        },
        "profile": profile,
        "profile_label": _label_for_profile(profile),
    }


def describe_opportunity_rank_advantage(
    opportunity: dict,
    previous: dict | None,
    horizon: str = "all",
    profile: str = "balanced",
) -> str:
    profile = _normalize_profile(profile)
    if previous is None:
        return "To obecnie najmocniejszy kandydat w tym profilu i horyzoncie rankingu."

    current = get_opportunity_rank_breakdown(opportunity, horizon=horizon, profile=profile)
    baseline = get_opportunity_rank_breakdown(previous, horizon=horizon, profile=profile)
    deltas = {
        "historia 1M": current["history_components"].get("22d", 0.0) - baseline["history_components"].get("22d", 0.0),
        "historia 3M": current["history_components"].get("66d", 0.0) - baseline["history_components"].get("66d", 0.0),
        "sytuacja finansowa spółki": current["financial_score"] - baseline["financial_score"],
        "technika live": current["technical_score"] - baseline["technical_score"],
        "newsflow spółki": current["event_score"] - baseline["event_score"],
        "makro": current["macro_score"] - baseline["macro_score"],
    }
    ordered = sorted(deltas.items(), key=lambda item: item[1], reverse=True)
    positive = [label for label, delta in ordered if delta >= 0.045]

    if "historia 1M" in positive and "historia 3M" in positive:
        lead = "mocniejszą historię podobnych setupów na 1M i 3M"
        positive = [label for label in positive if label not in {"historia 1M", "historia 3M"}]
    elif positive:
        lead = positive.pop(0)
    else:
        lead = "bardziej zrównoważony miks jakości setupu i spółki"

    if positive:
        return f"Wyżej niż {previous.get('ticker', 'poprzedni kandydat')} jest głównie przez {lead} oraz lepszą składową: {positive[0]}."
    return f"Wyżej niż {previous.get('ticker', 'poprzedni kandydat')} jest głównie przez {lead}."


def get_opportunity_signal_confidence(opportunity: dict, horizon: str = "all", profile: str = "balanced") -> dict:
    return dict(_resolve_rank_breakdown(opportunity, horizon=horizon, profile=profile).get("confidence") or {})


def get_opportunity_rank_score(opportunity: dict, horizon: str = "all", profile: str = "balanced") -> float:
    """Canonical ranking for opportunities, centered on historical 1M/3M setup quality."""
    return float(_resolve_rank_breakdown(opportunity, horizon=horizon, profile=profile).get("score", 0.0))


def _resolve_rank_breakdown(opportunity: dict, horizon: str = "all", profile: str = "balanced") -> dict:
    cached = opportunity.get("rank_breakdown")
    if not opportunity.get("details") and isinstance(cached, dict):
        return dict(cached)
    return get_opportunity_rank_breakdown(opportunity, horizon=horizon, profile=profile)


def get_opportunity_display_state(opportunity: dict, horizon: str = "all", profile: str = "balanced") -> dict:
    """UI-friendly strength badge driven by the canonical opportunity rank, not the raw live criteria count."""
    breakdown = _resolve_rank_breakdown(opportunity, horizon=horizon, profile=profile)
    rank_score = float(breakdown.get("score", 0.0) or 0.0)

    if rank_score >= 0.45:
        label = "BARDZO SILNA OKAZJA"
        bg = "#dcfce7"
        color = "#15803d"
        stars_value = 5
    elif rank_score >= 0.18:
        label = "SILNA OKAZJA"
        bg = "#dbeafe"
        color = "#1d4ed8"
        stars_value = 4
    elif rank_score >= 0.08:
        label = "POTENCJALNA OKAZJA"
        bg = "#fef9c3"
        color = "#a16207"
        stars_value = 3
    elif rank_score >= -0.12:
        label = "MIESZANY SYGNAŁ"
        bg = "#fff7ed"
        color = "#c2410c"
        stars_value = 2
    else:
        label = "SŁABY SYGNAŁ"
        bg = "#fee2e2"
        color = "#b91c1c"
        stars_value = 1

    return {
        "label": label,
        "bg": bg,
        "color": color,
        "stars_value": stars_value,
        "rank_score": round(rank_score, 3),
    }


def _evaluate_opportunity_signals(
    price: float,
    sma50,
    sma200,
    rsi14,
    vol_ratio: float,
    drawdown: float,
    high_52w: float,
    stoch_k,
    stoch_d,
    roc20,
    adx14,
    willi_r,
) -> tuple[int, dict]:
    """Return the canonical opportunity score and signal details used in both scanner and detail view."""
    score = 0
    details: dict = {}

    above_sma200 = pd.notna(sma200) and price >= sma200
    if above_sma200:
        score += 1
    details["above_sma200"] = above_sma200

    below_sma50 = pd.notna(sma50) and price <= sma50
    if below_sma50:
        score += 1
    details["below_sma50"] = below_sma50

    rsi_low = pd.notna(rsi14) and rsi14 <= config.OPP_RSI_MAX
    if rsi_low:
        score += 1
    details["rsi_low"] = rsi_low
    details["rsi_value"] = round(rsi14, 1) if pd.notna(rsi14) else None

    vol_high = vol_ratio >= config.OPP_VOLUME_RATIO_MIN
    if vol_high:
        score += 1
    details["vol_high"] = vol_high
    details["vol_ratio"] = round(vol_ratio, 2)

    deep_dip = drawdown >= config.OPP_DRAWDOWN_MIN
    if deep_dip:
        score += 1
    details["deep_dip"] = deep_dip
    details["drawdown_pct"] = round(drawdown * 100, 1)
    details["price"] = round(price, 2)
    details["52w_high"] = round(high_52w, 2)

    stoch_oversold = stoch_k is not None and stoch_k <= 30
    if stoch_oversold:
        score += 1
    details["stoch_oversold"] = stoch_oversold
    details["stoch_k"] = round(stoch_k, 1) if stoch_k is not None else None
    details["stoch_d"] = round(stoch_d, 1) if stoch_d is not None else None

    roc_negative = roc20 is not None and roc20 <= -5
    if roc_negative:
        score += 1
    details["roc_negative"] = roc_negative
    details["roc20"] = round(roc20, 2) if roc20 is not None else None

    adx_trending = adx14 is not None and adx14 >= 20
    if adx_trending:
        score += 1
    details["adx_trending"] = adx_trending
    details["adx14"] = round(adx14, 1) if adx14 is not None else None

    willy_oversold = willi_r is not None and willi_r <= -80
    if willy_oversold:
        score += 1
    details["willy_oversold"] = willy_oversold
    details["williams_r"] = round(willi_r, 1) if willi_r is not None else None

    return score, details


def _backtest_setup(
    df: pd.DataFrame,
    target_setup: dict | None = None,
    horizons=(_BARS_1W, _BARS_1M, _BARS_3M),
) -> dict:
    """
    Find all historical bars where the same setup occurred on 4H data:
      - price >= SMA200 (uptrend)
      - price <= SMA50  (short-term dip)
      - RSI14 <= configured scanner threshold
      - Stochastic %K <= 35  (independent oversold confirmation)
      - ROC(20) <= -3%        (real price momentum decline)
    Horizons: 10/44/132 4H bars ≈ 1 tydzień / 1 miesiąc / 3 miesiące.
    Keys always use canonical names: win_rate_5d / win_rate_22d / win_rate_66d.
    """
    _KEY_LABELS = ["5d", "22d", "66d"]
    results = {h: [] for h in horizons}
    closes = df["Close"].values
    rolling_high_52w = df["High"].rolling(_BARS_1Y, min_periods=_BARS_1M).max()
    rolling_vol20 = df["Volume"].rolling(20, min_periods=5).mean()
    matches: list[tuple[int, float]] = []

    for i in range(len(df) - max(horizons) - 1):
        row = df.iloc[i]
        price   = row["Close"]
        sma50   = row["SMA50"]
        sma200  = row["SMA200"]
        rsi14   = row["RSI14"]
        stoch_k = row.get("Stoch_K") if "Stoch_K" in row.index else float("nan")
        roc20   = row.get("ROC20")   if "ROC20"   in row.index else float("nan")
        adx14   = row.get("ADX14")   if "ADX14"   in row.index else float("nan")
        willi_r = row.get("WilliamsR") if "WilliamsR" in row.index else float("nan")
        vol50   = row.get("Vol_SMA50") if "Vol_SMA50" in row.index else float("nan")

        ref_high = rolling_high_52w.iloc[i]
        drawdown = (ref_high - price) / ref_high if pd.notna(ref_high) and ref_high > 0 else None
        vol_ratio = None
        vol20 = rolling_vol20.iloc[i]
        if pd.notna(vol20) and pd.notna(vol50) and vol50 > 0:
            vol_ratio = float(vol20) / float(vol50)

        candidate_setup = build_pullback_setup_snapshot(
            price=price,
            sma50=sma50,
            sma200=sma200,
            rsi14=rsi14,
            vol_ratio=vol_ratio,
            drawdown=drawdown,
            stoch_k=None if pd.isna(stoch_k) else stoch_k,
            roc20=None if pd.isna(roc20) else roc20,
            adx14=None if pd.isna(adx14) else adx14,
            willi_r=None if pd.isna(willi_r) else willi_r,
        )
        if not matches_pullback_setup(candidate_setup):
            continue

        distance = pullback_setup_distance(target_setup, candidate_setup) if target_setup else 0.0
        matches.append((i, distance))

    selected_matches = matches
    if target_setup and matches:
        selected_matches = sorted(matches, key=lambda item: item[1])
        tight_matches = [item for item in selected_matches if item[1] <= 0.85][:60]
        if len(tight_matches) >= 6:
            selected_matches = tight_matches
        else:
            fallback_size = max(10, min(40, len(selected_matches) // 3 if len(selected_matches) >= 12 else len(selected_matches)))
            selected_matches = selected_matches[:fallback_size]

    for i, _distance in selected_matches:
        entry_price = closes[i]
        for h in horizons:
            future_price = closes[i + h]
            fwd_return = (future_price - entry_price) / entry_price * 100
            results[h].append(fwd_return)

    out = {}
    for idx, h in enumerate(horizons):
        lbl = _KEY_LABELS[idx] if idx < len(_KEY_LABELS) else f"{h}d"
        rets = results[h]
        if len(rets) >= 3:
            arr = np.array(rets)
            out[f"win_rate_{lbl}"]    = round(float((arr > 0).mean() * 100), 1)
            out[f"avg_return_{lbl}"]  = round(float(arr.mean()), 2)
            out[f"median_return_{lbl}"] = round(float(np.median(arr)), 2)
            out[f"best_{lbl}"]        = round(float(arr.max()), 2)
            out[f"worst_{lbl}"]       = round(float(arr.min()), 2)
            out[f"n_setups_{lbl}"]    = len(rets)
        else:
            out[f"win_rate_{lbl}"]    = None
            out[f"avg_return_{lbl}"]  = None
            out[f"median_return_{lbl}"] = None
            out[f"best_{lbl}"]        = None
            out[f"worst_{lbl}"]       = None
            out[f"n_setups_{lbl}"]    = len(rets)
    return out


def _normalize_path(prices: np.ndarray, base_price: float | None = None) -> np.ndarray:
    base = float(base_price) if base_price is not None else float(prices[0])
    if base <= 0:
        return np.zeros(len(prices), dtype=float)
    return (prices / base - 1.0) * 100.0


def _detrend_path(path: np.ndarray) -> np.ndarray:
    if len(path) <= 1:
        return np.zeros(len(path), dtype=float)
    trend = np.linspace(0.0, float(path[-1]), len(path), dtype=float)
    return path - trend


def _recent_history_window_label(lookback_bars: int) -> str:
    if lookback_bars == _BARS_1W:
        return "5 dni"
    if lookback_bars == _BARS_1M:
        return "30 dni"
    approx_days = max(1, int(round(lookback_bars / 2.0)))
    return f"{approx_days} dni"


def _backtest_recent_history_path(df: pd.DataFrame, lookback_bars: int = _RECENT_HISTORY_LOOKBACK_BARS) -> dict:
    """Compare the last fixed 4H path against historical windows of the same length."""
    window_label = _recent_history_window_label(lookback_bars)
    if df.empty or len(df) <= lookback_bars + max(_BARS_1W, _BARS_1M, _BARS_3M) + 2:
        return {"n_samples": 0, "window_label": window_label, "msg": f"Za mało historii, by porównać stałe okno z ostatnich {window_label}."}

    closes = df["Close"].astype(float).to_numpy()
    volumes = df["Volume"].astype(float).to_numpy()
    vol_baseline = (
        df["Vol_SMA50"].astype(float).to_numpy()
        if "Vol_SMA50" in df.columns
        else pd.Series(volumes).rolling(50, min_periods=10).mean().to_numpy()
    )
    rsi_values = (
        df["RSI14"].astype(float).to_numpy()
        if "RSI14" in df.columns
        else np.full(len(df), np.nan, dtype=float)
    )
    index = pd.DatetimeIndex(pd.to_datetime(df.index))
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)

    def _volume_profile(volume_window: np.ndarray, baseline_window: np.ndarray) -> np.ndarray:
        denom = np.where(np.isfinite(baseline_window) & (baseline_window > 0), baseline_window, np.nan)
        rel = np.divide(
            volume_window,
            denom,
            out=np.full(len(volume_window), np.nan, dtype=float),
            where=np.isfinite(denom),
        )
        rel = np.clip(rel, 0.25, 4.0)
        return np.log(rel)

    def _rsi_profile(rsi_window: np.ndarray) -> np.ndarray:
        return np.clip((rsi_window - 50.0) / 25.0, -2.0, 2.0)

    def _feature_mae(left: np.ndarray, right: np.ndarray) -> float | None:
        valid = np.isfinite(left) & np.isfinite(right)
        if int(valid.sum()) < max(6, len(left) // 2):
            return None
        return float(np.mean(np.abs(left[valid] - right[valid])))

    def _feature_corr(left: np.ndarray, right: np.ndarray) -> float | None:
        valid = np.isfinite(left) & np.isfinite(right)
        if int(valid.sum()) < max(6, len(left) // 2):
            return None
        left_valid = left[valid]
        right_valid = right[valid]
        if np.std(left_valid) > 1e-9 and np.std(right_valid) > 1e-9:
            return float(np.corrcoef(left_valid, right_valid)[0, 1])
        mae = float(np.mean(np.abs(left_valid - right_valid)))
        return 1.0 if mae <= 0.12 else 0.0

    anchor_idx = len(df) - 1 - lookback_bars
    if anchor_idx < 1:
        return {"n_samples": 0, "window_label": window_label, "msg": f"Brak wystarczających notowań do zbudowania okna {window_label}."}

    horizons = (_BARS_1W, _BARS_1M, _BARS_3M)
    max_horizon = max(horizons)
    max_start = anchor_idx - lookback_bars - max_horizon - 1
    if max_start < 0:
        return {"n_samples": 0, "window_label": window_label, "msg": f"Za mało starszej historii, by porównać ostatnie {window_label} do przeszłości."}

    base_price = float(closes[anchor_idx])
    current_price = float(closes[-1])
    if base_price <= 0 or current_price <= 0:
        return {"n_samples": 0, "window_label": window_label, "msg": "Brak poprawnych danych cenowych do porównania okna historycznego."}

    pnl_pct = (current_price - base_price) / base_price * 100.0
    target_path = _normalize_path(closes[anchor_idx : anchor_idx + lookback_bars + 1], base_price)
    target_diff = np.diff(target_path)
    target_shape = _detrend_path(target_path)
    target_volume_profile = _volume_profile(
        volumes[anchor_idx : anchor_idx + lookback_bars + 1],
        vol_baseline[anchor_idx : anchor_idx + lookback_bars + 1],
    )
    target_rsi_profile = _rsi_profile(rsi_values[anchor_idx : anchor_idx + lookback_bars + 1])
    target_vol = float(np.std(target_diff)) if len(target_diff) else 0.0
    target_direction = 0 if abs(pnl_pct) < 0.2 else (1 if pnl_pct > 0 else -1)

    strict_final_gap = max(3.0, abs(pnl_pct) * 0.40 + 1.0)
    strict_path_mae = max(3.4, target_vol * 0.95 + 1.0)
    strict_shape_mae = max(1.6, target_vol * 0.60 + 0.45)
    strict_step_mae = max(0.85, target_vol * 0.45 + 0.25)
    strict_volume_mae = 0.55
    strict_rsi_mae = 0.65
    loose_final_gap = max(4.8, abs(pnl_pct) * 0.60 + 1.5)
    loose_path_mae = max(4.8, target_vol * 1.30 + 1.5)
    loose_shape_mae = max(2.4, target_vol * 0.90 + 0.75)
    loose_step_mae = max(1.35, target_vol * 0.70 + 0.40)
    loose_volume_mae = 0.85
    loose_rsi_mae = 0.95

    candidates: list[dict] = []
    results = {h: [] for h in horizons}
    key_labels = ["5d", "22d", "66d"]

    for start in range(max_start + 1):
        end = start + lookback_bars
        window = closes[start : end + 1]
        if len(window) != lookback_bars + 1 or np.any(window <= 0):
            continue

        candidate_path = _normalize_path(window)
        candidate_volume_profile = _volume_profile(
            volumes[start : end + 1],
            vol_baseline[start : end + 1],
        )
        candidate_rsi_profile = _rsi_profile(rsi_values[start : end + 1])
        candidate_final = float(candidate_path[-1])
        candidate_direction = 0 if abs(candidate_final) < 0.2 else (1 if candidate_final > 0 else -1)
        if target_direction and candidate_direction != target_direction:
            continue

        final_gap = abs(candidate_final - pnl_pct)
        path_mae = float(np.mean(np.abs(candidate_path - target_path)))
        candidate_shape = _detrend_path(candidate_path)
        shape_mae = float(np.mean(np.abs(candidate_shape - target_shape)))
        candidate_diff = np.diff(candidate_path)
        step_mae = float(np.mean(np.abs(candidate_diff - target_diff))) if len(candidate_diff) == len(target_diff) and len(target_diff) else path_mae
        if len(candidate_diff) and np.std(candidate_diff) > 1e-9 and np.std(target_diff) > 1e-9:
            corr = float(np.corrcoef(candidate_diff, target_diff)[0, 1])
        else:
            corr = 1.0 if path_mae <= 0.75 else 0.0

        volume_mae = _feature_mae(candidate_volume_profile, target_volume_profile)
        volume_corr = _feature_corr(candidate_volume_profile, target_volume_profile)
        rsi_mae = _feature_mae(candidate_rsi_profile, target_rsi_profile)
        rsi_corr = _feature_corr(candidate_rsi_profile, target_rsi_profile)

        corr_penalty = 1.0 - max(min(corr, 1.0), -1.0)
        volume_penalty = 0.0
        if volume_mae is not None:
            volume_penalty += volume_mae * 0.55
        if volume_corr is not None:
            volume_penalty += (1.0 - max(min(volume_corr, 1.0), -1.0)) * 0.18

        rsi_penalty = 0.0
        if rsi_mae is not None:
            rsi_penalty += rsi_mae * 0.85
        if rsi_corr is not None:
            rsi_penalty += (1.0 - max(min(rsi_corr, 1.0), -1.0)) * 0.28

        score = (
            shape_mae * 0.34
            + step_mae * 0.20
            + path_mae * 0.18
            + final_gap * 0.12
            + corr_penalty * 1.35
            + volume_penalty
            + rsi_penalty
        )
        candidates.append(
            {
                "end": end,
                "score": score,
                "path": candidate_path,
                "final_gap": final_gap,
                "path_mae": path_mae,
                "shape_mae": shape_mae,
                "step_mae": step_mae,
                "corr": corr,
                "volume_mae": volume_mae,
                "volume_corr": volume_corr,
                "rsi_mae": rsi_mae,
                "rsi_corr": rsi_corr,
            }
        )

    if not candidates:
        return {"n_samples": 0, "window_label": window_label, "msg": f"Brak historycznych ścieżek podobnych do ostatnich {window_label} notowań."}

    candidates.sort(key=lambda item: item["score"])
    selected = [
        item
        for item in candidates
        if item["final_gap"] <= strict_final_gap
        and item["path_mae"] <= strict_path_mae
        and item["shape_mae"] <= strict_shape_mae
        and item["step_mae"] <= strict_step_mae
        and item["corr"] >= 0.15
        and item.get("volume_mae") is not None
        and item.get("volume_mae") <= strict_volume_mae
        and item.get("rsi_mae") is not None
        and item.get("rsi_mae") <= strict_rsi_mae
    ][:60]

    if len(selected) < 3:
        selected = [
            item
            for item in candidates
            if item["final_gap"] <= loose_final_gap
            and item["path_mae"] <= loose_path_mae
            and item["shape_mae"] <= loose_shape_mae
            and item["step_mae"] <= loose_step_mae
            and item["corr"] >= -0.15
            and item.get("volume_mae") is not None
            and item.get("volume_mae") <= loose_volume_mae
            and item.get("rsi_mae") is not None
            and item.get("rsi_mae") <= loose_rsi_mae
        ][:25]

    if len(selected) < 3:
        selected = candidates[: min(12, len(candidates))]

    for item in selected:
        end = item["end"]
        for h in horizons:
            fwd = (closes[end + h] - closes[end]) / closes[end] * 100.0
            results[h].append(fwd)

    selected_paths = np.vstack([item["path"] for item in selected]) if selected else None
    out = {
        "n_samples": len(results[horizons[0]]),
        "window_label": window_label,
        "bars_held": lookback_bars,
        "days_held": max(1, int((index[-1] - index[anchor_idx]).total_seconds() // 86400)),
        "pnl_pct": round(pnl_pct, 2),
        "anchor_time": pd.Timestamp(index[anchor_idx]).isoformat(),
        "current_time": pd.Timestamp(index[-1]).isoformat(),
        "avg_final_gap": round(float(np.mean([item["final_gap"] for item in selected])), 2) if selected else None,
        "avg_path_mae": round(float(np.mean([item["path_mae"] for item in selected])), 2) if selected else None,
        "avg_shape_mae": round(float(np.mean([item["shape_mae"] for item in selected])), 2) if selected else None,
        "avg_step_mae": round(float(np.mean([item["step_mae"] for item in selected])), 2) if selected else None,
        "avg_corr": round(float(np.mean([item["corr"] for item in selected])), 2) if selected else None,
        "avg_volume_mae": round(float(np.mean([item["volume_mae"] for item in selected if item.get("volume_mae") is not None])), 2) if any(item.get("volume_mae") is not None for item in selected) else None,
        "avg_volume_corr": round(float(np.mean([item["volume_corr"] for item in selected if item.get("volume_corr") is not None])), 2) if any(item.get("volume_corr") is not None for item in selected) else None,
        "avg_rsi_mae": round(float(np.mean([item["rsi_mae"] for item in selected if item.get("rsi_mae") is not None])), 2) if any(item.get("rsi_mae") is not None for item in selected) else None,
        "avg_rsi_corr": round(float(np.mean([item["rsi_corr"] for item in selected if item.get("rsi_corr") is not None])), 2) if any(item.get("rsi_corr") is not None for item in selected) else None,
        "current_path_pct": [round(float(value), 2) for value in target_path.tolist()],
        "matched_median_path_pct": [round(float(value), 2) for value in np.median(selected_paths, axis=0).tolist()] if selected_paths is not None else [],
        "matched_p25_path_pct": [round(float(value), 2) for value in np.percentile(selected_paths, 25, axis=0).tolist()] if selected_paths is not None else [],
        "matched_p75_path_pct": [round(float(value), 2) for value in np.percentile(selected_paths, 75, axis=0).tolist()] if selected_paths is not None else [],
    }

    for idx, h in enumerate(horizons):
        lbl = key_labels[idx] if idx < len(key_labels) else f"{h}d"
        rets = results[h]
        if len(rets) >= 2:
            arr = np.array(rets)
            out[f"win_rate_{lbl}"] = round(float((arr > 0).mean() * 100), 1)
            out[f"avg_return_{lbl}"] = round(float(arr.mean()), 2)
            out[f"median_return_{lbl}"] = round(float(np.median(arr)), 2)
        else:
            out[f"win_rate_{lbl}"] = None
            out[f"avg_return_{lbl}"] = None
            out[f"median_return_{lbl}"] = None
    return out


def _support_resistance(df: pd.DataFrame, lookback: int = 60) -> dict:
    """Find recent support and resistance levels."""
    recent = df.tail(lookback)
    lows  = recent["Low"].nsmallest(3).values
    highs = recent["High"].nlargest(3).values
    return {
        "support1": round(float(lows[0]), 2) if len(lows) > 0 else None,
        "support2": round(float(lows[1]), 2) if len(lows) > 1 else None,
        "resistance1": round(float(highs[0]), 2) if len(highs) > 0 else None,
        "resistance2": round(float(highs[1]), 2) if len(highs) > 1 else None,
    }


def _consecutive_down_days(df: pd.DataFrame, n: int = 5) -> int:
    """Count consecutive down-close days ending at last bar."""
    closes = df["Close"].values
    count = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] < closes[i - 1]:
            count += 1
        else:
            break
    return count


def _bb_position(price: float, bb_upper: float, bb_lower: float) -> float:
    """Return position within Bollinger Bands: 0=lower, 1=upper, 0.5=mid."""
    band_width = bb_upper - bb_lower
    if band_width <= 0:
        return 0.5
    return (price - bb_lower) / band_width


# ─────────────────────────────────────────────────────────────────────────────
def score_ticker(ticker: str, setup_mode: str = "classic") -> dict | None:
    """
    Quick score (0–9 points) used by the scanner. Returns compact dict.
    Uses 4H candles for timely intraday signals.
    """
    df = get_ohlcv_4h(ticker, period="2y")
    if df is None or len(df) < 200:
        return None

    df = add_indicators(df)
    info = get_info(ticker)
    last = df.iloc[-1]
    bars_snapshot_time = pd.Timestamp(df.index[-1]).isoformat()

    price_snapshot = _build_price_snapshot(ticker, fallback_price=_safe_float(last["Close"]), force_refresh=False)
    if price_snapshot is None:
        return None
    price = float(price_snapshot["price"])

    sma50   = last["SMA50"]
    sma200  = last["SMA200"]
    rsi14   = last["RSI14"]
    vol50   = last["Vol_SMA50"]
    stoch_k = _safe(last.get("Stoch_K"))
    stoch_d = _safe(last.get("Stoch_D"))
    roc20   = _safe(last.get("ROC20"))

    high_52w  = df["High"].tail(_BARS_1Y).max()
    drawdown  = (high_52w - price) / high_52w if high_52w > 0 else 0
    vol_ratio = df["Volume"].tail(20).mean() / vol50 if vol50 > 0 else 0

    adx14     = _safe(last.get("ADX14"))
    willi_r   = _safe(last.get("WilliamsR"))

    current_setup = build_pullback_setup_snapshot(
        price=price,
        sma50=sma50,
        sma200=sma200,
        rsi14=rsi14,
        vol_ratio=vol_ratio,
        drawdown=drawdown,
        stoch_k=stoch_k,
        roc20=roc20,
        adx14=adx14,
        willi_r=willi_r,
    )
    matches_classic_setup = matches_pullback_setup(current_setup, mode="classic")
    matches_mixed_setup = matches_pullback_setup(current_setup, mode="mixed")

    # ── Liquidity guard — skip very illiquid instruments ──────────────────
    if vol50 is not None and vol50 < 50_000:
        return None

    # ── HARD GATE: live setup must match the same canonical definition used in the backtest ──
    if not matches_pullback_setup(current_setup, mode=setup_mode):
        return None

    score, details = _evaluate_opportunity_signals(
        price=price,
        sma50=sma50,
        sma200=sma200,
        rsi14=rsi14,
        vol_ratio=vol_ratio,
        drawdown=drawdown,
        high_52w=high_52w,
        stoch_k=stoch_k,
        stoch_d=stoch_d,
        roc20=roc20,
        adx14=adx14,
        willi_r=willi_r,
    )
    # ── Backtest: primary ranking criterion ───────────────────────────────
    backtest = _backtest_setup(df, target_setup=current_setup) if len(df) >= 270 else {}
    recent_history_backtest = _backtest_recent_history_path(df)
    details["win_rate_5d"]    = backtest.get("win_rate_5d")
    details["win_rate_22d"]   = backtest.get("win_rate_22d")
    details["win_rate_66d"]   = backtest.get("win_rate_66d")
    details["avg_return_5d"]  = backtest.get("avg_return_5d")
    details["avg_return_22d"] = backtest.get("avg_return_22d")
    details["avg_return_66d"] = backtest.get("avg_return_66d")
    details["median_return_5d"]  = backtest.get("median_return_5d")
    details["median_return_22d"] = backtest.get("median_return_22d")
    details["median_return_66d"] = backtest.get("median_return_66d")
    details["n_setups"]       = backtest.get("n_setups_22d", 0)
    details["n_setups_5d"]    = backtest.get("n_setups_5d", 0)
    details["n_setups_22d"]   = backtest.get("n_setups_22d", 0)
    details["n_setups_66d"]   = backtest.get("n_setups_66d", 0)
    # EV = expected value per trade = win_rate/100 * avg_return (when both positive)
    for sfx in ("5d", "22d", "66d"):
        wr = backtest.get(f"win_rate_{sfx}")
        ar = backtest.get(f"avg_return_{sfx}")
        if wr is not None and ar is not None and wr > 0 and ar > 0:
            details[f"ev_{sfx}"] = round(wr / 100 * ar, 3)
        else:
            details[f"ev_{sfx}"] = None

    macro = get_macro_snapshot(ticker, info=info)
    company = get_company_snapshot(ticker, info=info, price=price)
    currency = info.get("currency", "USD")
    details["macro_score"] = macro.get("score", 50)
    details["macro_stance"] = macro.get("stance", "Neutralne")
    details["macro_adjustment"] = macro.get("opportunity_adjustment", 0)
    details["company_score"] = company.get("score", 50)
    details["financial_score"] = company.get("financial_score", 50)
    details["event_score"] = company.get("event_score", 50)
    details["company_stance"] = company.get("stance", "Neutralne")
    details["company_adjustment"] = company.get("opportunity_adjustment", 0)
    details["currency"] = currency
    details["price"] = _round_or_none(price, 2)
    details["bars_snapshot_time"] = bars_snapshot_time

    opportunity_payload = {
        "score": score,
        "details": details,
        "macro_score": macro.get("score", 50),
        "company_score": company.get("score", 50),
    }
    rank_breakdown = get_opportunity_rank_breakdown(opportunity_payload, horizon="all", profile="balanced")

    return {
        "ticker": ticker,
        "score": score,
        "currency": currency,
        "matches_classic_setup": matches_classic_setup,
        "matches_mixed_setup": matches_mixed_setup,
        "price_snapshot": price_snapshot,
        "snapshot_time": price_snapshot.get("captured_at"),
        "snapshot_source_label": price_snapshot.get("source_label"),
        "bars_snapshot_time": bars_snapshot_time,
        "details": details,
        "recent_history_backtest": recent_history_backtest,
        "macro": macro,
        "company": company,
        "macro_score": macro.get("score", 50),
        "company_score": company.get("score", 50),
        "rank_score": round(rank_breakdown.get("score", 0.0), 3),
        "rank_breakdown": rank_breakdown,
        "signal_confidence": rank_breakdown.get("confidence", {}),
        "setup_quality_score": rank_breakdown.get("setup_quality_score", 50.0),
        "company_quality_score": rank_breakdown.get("company_quality_score", 50.0),
        "macro_quality_score": rank_breakdown.get("macro_quality_score", 50.0),
    }


# ─────────────────────────────────────────────────────────────────────────────
def analyze_ticker_deep(
    ticker: str,
    profile: str = "balanced",
    horizon: str = "all",
    price_snapshot: dict | None = None,
) -> dict | None:
    """
    Full deep analysis for the detail modal.
    Uses 4H candles for timely intraday signals.
    Returns rich dict with all indicators, backtest results, and explanations.
    """
    df = get_ohlcv_4h(ticker, period="2y")
    if df is None or len(df) < 60:
        return None

    df = add_indicators(df)
    info = get_info(ticker)
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last
    bars_snapshot_time = pd.Timestamp(df.index[-1]).isoformat()

    resolved_price_snapshot = _build_price_snapshot(
        ticker,
        fallback_price=_safe_float(last["Close"]),
        force_refresh=False,
        snapshot=price_snapshot,
    )
    if resolved_price_snapshot is None:
        return None
    price = float(resolved_price_snapshot["price"])
    macro = get_macro_snapshot(ticker, info=info)
    company = get_company_snapshot(ticker, info=info, price=price)
    from analysis.value_investing import get_value_investing_snapshot

    value_snapshot = get_value_investing_snapshot(ticker, info=info, company=company, price=price)
    sma20     = _safe(last["SMA20"])
    sma50     = _safe(last["SMA50"])
    sma200    = _safe(last["SMA200"])
    ema20     = _safe(last["EMA20"])
    rsi14     = _safe(last["RSI14"])
    macd_val  = _safe(last["MACD"])
    macd_sig  = _safe(last["MACD_Signal"])
    macd_hist = _safe(last["MACD_Hist"])
    bb_upper  = _safe(last["BB_Upper"])
    bb_lower  = _safe(last["BB_Lower"])
    bb_mid    = _safe(last["BB_Mid"])
    atr14     = _safe(last["ATR14"])
    stoch_k   = _safe(last["Stoch_K"])
    stoch_d   = _safe(last["Stoch_D"])
    roc20     = _safe(last["ROC20"])
    adx14     = _safe(last.get("ADX14"))
    willi_r   = _safe(last["WilliamsR"])
    vol       = _safe_float(last["Volume"]) or 0.0
    vol50     = _safe(last["Vol_SMA50"])
    vol20     = _safe(last["Vol_SMA20"])

    # 52W stats (4H: tail(500) ≈ 1 year)
    high_52w_raw = _safe_float(df["High"].tail(_BARS_1Y).max())
    low_52w_raw = _safe_float(df["Low"].tail(_BARS_1Y).min())
    high_52w = high_52w_raw or price
    low_52w = low_52w_raw or price
    drawdown  = (high_52w - price) / high_52w if high_52w > 0 else 0

    # Volume ratios (4H: 20 bars ≈ 10 trading days, 2 bars ≈ 1 day)
    vol_ratio_10d = df["Volume"].tail(20).mean() / vol50 if vol50 and vol50 > 0 else 0
    vol_ratio_1d  = vol / vol50 if vol50 and vol50 > 0 else 0

    # Price changes (4H bar counts: 1d≈3bars, 5d≈11bars, 1m≈45bars, 3m≈133bars)
    prev_close = _safe_float(prev["Close"])
    close_5d = _safe_float(df["Close"].iloc[-11]) if len(df) >= 11 else None
    close_1m = _safe_float(df["Close"].iloc[-45]) if len(df) >= 45 else None
    close_3m = _safe_float(df["Close"].iloc[-133]) if len(df) >= 133 else None
    change_1d = ((price - prev_close) / prev_close * 100) if prev_close and prev_close > 0 else None
    change_5d = ((price - close_5d) / close_5d * 100) if close_5d and close_5d > 0 else None
    change_1m = ((price - close_1m) / close_1m * 100) if close_1m and close_1m > 0 else None
    change_3m = ((price - close_3m) / close_3m * 100) if close_3m and close_3m > 0 else None

    # ATR as % of price
    atr_pct    = (atr14 / price * 100) if atr14 and price > 0 else None

    # Bollinger position
    bb_pos     = _bb_position(price, bb_upper, bb_lower) if bb_upper and bb_lower else 0.5

    # Consecutive down days
    consec_down = _consecutive_down_days(df)

    # Support / resistance
    sr = _support_resistance(df, lookback=60)

    # Backtest (may be slow first time but cached after)
    # needs at least SMA200 + 66 days forward → ~270 bars; 2y ≈ 500 bars is sufficient
    current_setup = build_pullback_setup_snapshot(
        price=price,
        sma50=sma50,
        sma200=sma200,
        rsi14=rsi14,
        vol_ratio=vol_ratio_10d,
        drawdown=drawdown,
        stoch_k=stoch_k,
        roc20=roc20,
        adx14=adx14,
        willi_r=willi_r,
    )
    matches_opportunity_setup = matches_pullback_setup(current_setup, mode="classic")
    backtest = _backtest_setup(df, target_setup=current_setup) if len(df) >= 270 else {}
    recent_history_backtest = (
        _backtest_recent_history_path(df, lookback_bars=_RECENT_HISTORY_LOOKBACK_BARS)
        if len(df) >= (_RECENT_HISTORY_LOOKBACK_BARS + _BARS_3M + 3)
        else {}
    )

    # ── Score criteria ────────────────────────────────────────────────────────
    score, signal_details = _evaluate_opportunity_signals(
        price=price,
        sma50=sma50,
        sma200=sma200,
        rsi14=rsi14,
        vol_ratio=vol_ratio_10d,
        drawdown=drawdown,
        high_52w=high_52w,
        stoch_k=stoch_k,
        stoch_d=stoch_d,
        roc20=roc20,
        adx14=adx14,
        willi_r=willi_r,
    )
    scanner_filter_failures: list[str] = []
    if not signal_details.get("above_sma200"):
        scanner_filter_failures.append("cena jest poniżej SMA200, więc długoterminowy trend wzrostowy nie jest dziś potwierdzony")
    if not signal_details.get("below_sma50"):
        scanner_filter_failures.append("cena nie jest już w cofnięciu pod SMA50, więc nie ma świeżego pullbacku")
    if not signal_details.get("rsi_low"):
        scanner_filter_failures.append(f"RSI(14) nie jest już wystarczająco nisko dla filtra wejścia (próg: {config.OPP_RSI_MAX:.0f})")
    if stoch_k is not None and not signal_details.get("stoch_oversold"):
        scanner_filter_failures.append("Stochastic %K nie jest już w strefie wyprzedania wymaganej przez filtr")
    if roc20 is not None and not signal_details.get("roc_negative"):
        scanner_filter_failures.append("ROC(20) nie pokazuje jeszcze korekty wystarczająco głębokiej dla skanera podstawowego")
    criteria = []

    # 1. Long-term uptrend
    if signal_details["above_sma200"]:
        gap_pct = (price - sma200) / sma200 * 100
        criteria.append({
            "met": True,
            "icon": "📈",
            "title": "Trend długoterminowy wzrostowy",
            "desc": (
                f"Cena ({price:.2f}) jest <b>{gap_pct:.1f}% powyżej SMA200</b> ({sma200:.2f}). "
                f"Wielomiesięczny trend wzrostowy jest zachowany — to fundament każdej dobrej okazji. "
                f"Korekta następuje w trakcie trendu wzrostowego, nie jego odwrócenia."
            ),
        })
    else:
        criteria.append({
            "met": False,
            "icon": "📉",
            "title": "Brak trendu długoterminowego",
            "desc": (
                f"Cena ({price:.2f}) jest "
                + (f"poniżej SMA200 ({sma200:.2f})" if sma200 else "zbyt krótka historia")
                + ". Ryzyko kontynuacji trendu spadkowego — brak klasycznej okazji kupna."
            ),
        })

    # 2. Short-term dip
    if signal_details["below_sma50"]:
        gap_pct = (sma50 - price) / sma50 * 100
        criteria.append({
            "met": True,
            "icon": "🔽",
            "title": "Krótkoterminowy dołek (korekta)",
            "desc": (
                f"Cena jest <b>{gap_pct:.1f}% poniżej SMA50</b> ({sma50:.2f}). "
                f"Instrument koryguje się w trendzie wzrostowym — klasyczny setup 'pullback'. "
                f"Historycznie powrót do SMA50 oznacza potencjalny zysk ~{gap_pct:.1f}%."
            ),
        })
    else:
        criteria.append({
            "met": False,
            "icon": "➡️",
            "title": "Brak korekty do SMA50",
            "desc": (
                f"Cena ({price:.2f}) jest "
                + (f"powyżej lub blisko SMA50 ({sma50:.2f})" if sma50 else "zbyt krótka historia")
                + ". Możliwy potencjał wzrostu, ale brak sygnału kupna 'na dołku'."
            ),
        })

    # 3. RSI oversold
    if signal_details["rsi_low"]:
        criteria.append({
            "met": True,
            "icon": "⚡",
            "title": "Strefa wyprzedania — RSI(14)",
            "desc": (
                f"RSI(14) = <b>{rsi14:.1f}</b> "
                f"({'głęboka strefa wyprzedania' if rsi14 < 30 else 'strefa wyprzedania'}). "
                f"Poniżej 45 historycznie pojawia się wysoka szansa na odreagowanie. "
                f"Sygnał tym silniejszy, im bardziej RSI zbliża się do 30."
            ),
        })
    elif rsi14 is not None:
        criteria.append({
            "met": False,
            "icon": "⚡",
            "title": "RSI neutralny",
            "desc": f"RSI(14) = {rsi14:.1f}. Nie ma sygnału wyprzedania (próg: 45). Brak impetu do odbicia.",
        })

    # 4. Volume spike
    if signal_details["vol_high"]:
        criteria.append({
            "met": True,
            "icon": "📊",
            "title": "Podwyższony wolumen — sygnał aktywności",
            "desc": (
                f"Średni wolumen ostatnich 10 dni to <b>{vol_ratio_10d:.2f}× wyżej</b> niż średnia 50-dniowa. "
                f"Podwyższony wolumen w dołku często oznacza, że duże podmioty (smart money) "
                f"akumulują pozycję. Wczorajszy wolumen: {vol:,.0f} (50d avg: {vol50:,.0f})."
            ),
        })
    else:
        criteria.append({
            "met": False,
            "icon": "📊",
            "title": "Wolumen normalny lub niski",
            "desc": (
                f"Średni wolumen 10d = {vol_ratio_10d:.2f}× średniej 50d. "
                f"Brak sygnału akumulacji przez duże podmioty."
            ),
        })

    # 5. Deep drawdown
    if signal_details["deep_dip"]:
        criteria.append({
            "met": True,
            "icon": "🏔️",
            "title": "Głęboki dołek od szczytu 52-tygodniowego",
            "desc": (
                f"Instrument jest <b>{drawdown*100:.1f}% poniżej szczytu 52-tygodniowego</b> "
                f"({high_52w:.2f}). Taki poziom korekty przy zachowanym trendzie "
                f"wzrostowym historycznie oferuje atrakcyjną relację zysk/ryzyko."
            ),
        })
    else:
        criteria.append({
            "met": False,
            "icon": "🏔️",
            "title": "Mały drawdown od szczytu",
            "desc": (
                f"Korekta od szczytu 52W tylko {drawdown*100:.1f}% (próg: {config.OPP_DRAWDOWN_MIN*100:.0f}%). "
                f"Instrument nie jest wystarczająco 'w dołku' względem szczytu."
            ),
        })

    # 6. Stochastic oversold
    if signal_details["stoch_oversold"]:
        criteria.append({
            "met": True,
            "icon": "🔃",
            "title": "Stochastyk w strefie wyprzedania",
            "desc": (
                f"Stochastic %K = <b>{stoch_k:.1f}</b>, czyli poniżej progu 30. "
                f"To niezależne potwierdzenie wyprzedania obok RSI."
            ),
        })
    else:
        criteria.append({
            "met": False,
            "icon": "🔃",
            "title": "Stochastyk bez wyprzedania",
            "desc": (
                f"Stochastic %K = {stoch_k:.1f}. Sygnał wyprzedania pojawia się dopiero poniżej 30."
                if stoch_k is not None else
                "Brak odczytu stochastyka."
            ),
        })

    # 7. ROC negative momentum
    if signal_details["roc_negative"]:
        criteria.append({
            "met": True,
            "icon": "📉",
            "title": "ROC(20) pokazuje mocną korektę",
            "desc": (
                f"ROC(20) = <b>{roc20:.2f}%</b>, czyli poniżej progu -5%. "
                f"Momentum spadkowe jest wystarczająco mocne, by szukać odbicia kontrariańskiego."
            ),
        })
    else:
        criteria.append({
            "met": False,
            "icon": "📉",
            "title": "ROC(20) nie potwierdza dołka",
            "desc": (
                f"ROC(20) = {roc20:.2f}%. Brak wymaganej skali korekty (próg: -5%)."
                if roc20 is not None else
                "Brak odczytu ROC(20)."
            ),
        })

    # 8. ADX trend strength
    if signal_details["adx_trending"]:
        criteria.append({
            "met": True,
            "icon": "📐",
            "title": "ADX potwierdza siłę trendu",
            "desc": (
                f"ADX(14) = <b>{adx14:.1f}</b>, czyli powyżej progu 20. "
                f"Trend jest wystarczająco silny, by korektę traktować jako okazję, a nie chaos boczny."
            ),
        })
    else:
        criteria.append({
            "met": False,
            "icon": "📐",
            "title": "ADX nie potwierdza siły trendu",
            "desc": (
                f"ADX(14) = {adx14:.1f}. To za mało, by mówić o wyraźnym trendzie (próg: 20)."
                if adx14 is not None else
                "Brak odczytu ADX(14)."
            ),
        })

    # 9. Williams %R oversold
    if signal_details["willy_oversold"]:
        criteria.append({
            "met": True,
            "icon": "🧭",
            "title": "Williams %R potwierdza wyprzedanie",
            "desc": (
                f"Williams %R = <b>{willi_r:.1f}</b>, czyli w strefie poniżej -80. "
                f"To kolejne niezależne potwierdzenie, że kurs jest mocno cofnięty."
            ),
        })
    else:
        criteria.append({
            "met": False,
            "icon": "🧭",
            "title": "Williams %R bez mocnego wyprzedania",
            "desc": (
                f"Williams %R = {willi_r:.1f}. Nie osiąga progu -80 dla silnego wyprzedania."
                if willi_r is not None else
                "Brak odczytu Williams %R."
            ),
        })

    # ── Probability estimate (backtest-based) ────────────────────────────────
    prob_5d  = backtest.get("win_rate_5d")
    prob_22d = backtest.get("win_rate_22d")
    prob_66d = backtest.get("win_rate_66d")
    n_setups = backtest.get("n_setups_22d", 0)

    # Composite probability score (weighted)
    if prob_22d is not None:
        # Blend historical win rate with score
        base_prob = prob_22d
        score_bonus = (score / 9) * 15  # up to +15% for perfect score
        composite_prob = min(95, max(25, base_prob + score_bonus - 5))
    else:
        # Fallback: score-based estimate
        composite_prob = 35 + score * 7

    opportunity_payload = {
        "score": score,
        "details": {
            **signal_details,
            **backtest,
            "macro_score": macro.get("score", 50),
            "company_score": company.get("score", 50),
            "financial_score": company.get("financial_score", 50),
            "event_score": company.get("event_score", 50),
            "macro_stance": macro.get("stance", "Neutralne"),
            "macro_adjustment": macro.get("opportunity_adjustment", 0),
            "company_stance": company.get("stance", "Neutralne"),
            "company_adjustment": company.get("opportunity_adjustment", 0),
            "price": _round_or_none(price, 2),
            "currency": info.get("currency", "USD"),
        },
        "macro_score": macro.get("score", 50),
        "company_score": company.get("score", 50),
    }
    rank_breakdown = get_opportunity_rank_breakdown(opportunity_payload, horizon=horizon, profile=profile)

    return {
        "ticker":         ticker,
        "name":           info.get("name", ticker),
        "sector":         info.get("sector", "—"),
        "industry":       info.get("industry", "—"),
        "currency":       info.get("currency", "USD"),
        "exchange":       info.get("exchange", ""),
        "market_cap":     info.get("market_cap"),
        "pe_ratio":       info.get("pe_ratio"),
        "beta":           info.get("beta"),
        "score":          score,
        "price":          _round_or_none(price, 2),
        "change_1d":      _round_or_none(change_1d, 2),
        "change_5d":      _round_or_none(change_5d, 2),
        "change_1m":      _round_or_none(change_1m, 2),
        "change_3m":      _round_or_none(change_3m, 2),
        "high_52w":       _round_or_none(high_52w, 2),
        "low_52w":        _round_or_none(low_52w, 2),
        "drawdown_pct":   round(drawdown * 100, 1),
        "sma20":          round(sma20, 2) if sma20 else None,
        "sma50":          round(sma50, 2) if sma50 else None,
        "sma200":         round(sma200, 2) if sma200 else None,
        "rsi14":          round(rsi14, 1) if rsi14 is not None else None,
        "macd":           round(macd_val, 3) if macd_val is not None else None,
        "macd_signal":    round(macd_sig, 3) if macd_sig is not None else None,
        "macd_hist":      round(macd_hist, 3) if macd_hist is not None else None,
        "bb_upper":       round(bb_upper, 2) if bb_upper else None,
        "bb_mid":         round(bb_mid, 2) if bb_mid else None,
        "bb_lower":       round(bb_lower, 2) if bb_lower else None,
        "bb_position":    round(bb_pos, 2),
        "atr14":          round(atr14, 2) if atr14 else None,
        "atr_pct":        round(atr_pct, 2) if atr_pct else None,
        "stoch_k":        round(stoch_k, 1) if stoch_k is not None else None,
        "stoch_d":        round(stoch_d, 1) if stoch_d is not None else None,
        "williams_r":     round(willi_r, 1) if willi_r is not None else None,
        "roc20":          round(roc20, 2) if roc20 is not None else None,
        "vol_ratio_10d":  _round_or_none(vol_ratio_10d, 2) or 0.0,
        "vol_ratio_1d":   _round_or_none(vol_ratio_1d, 2) or 0.0,
        "vol50":          int(vol50) if vol50 else None,
        "consec_down":    consec_down,
        "support":        sr,
        "backtest":       backtest,
        "n_setups":       n_setups,
        "prob_5d":        _round_or_none(_safe_float(backtest.get("win_rate_5d")) if backtest.get("win_rate_5d") is not None else composite_prob * 0.88, 1),
        "prob_22d":       _round_or_none(prob_22d if prob_22d is not None else composite_prob, 1),
        "prob_66d":       _round_or_none(_safe_float(backtest.get("win_rate_66d")) if backtest.get("win_rate_66d") is not None else min(95, composite_prob * 1.08), 1),
        "avg_return_5d":  backtest.get("avg_return_5d"),
        "avg_return_22d": backtest.get("avg_return_22d"),
        "avg_return_66d": backtest.get("avg_return_66d"),
        "composite_prob": round(composite_prob, 1),
        "macro":          macro,
        "company":        company,
        "value_investing": value_snapshot,
        "criteria":       criteria,
        "rank_breakdown": rank_breakdown,
        "rank_score": round(rank_breakdown.get("score", 0.0), 3),
        "signal_confidence": rank_breakdown.get("confidence", {}),
        "recent_history_backtest": recent_history_backtest,
        "setup_quality_score": rank_breakdown.get("setup_quality_score", 50.0),
        "company_quality_score": rank_breakdown.get("company_quality_score", 50.0),
        "macro_quality_score": rank_breakdown.get("macro_quality_score", 50.0),
        "matches_opportunity_setup": matches_opportunity_setup,
        "scanner_filter_failures": scanner_filter_failures,
        "price_snapshot": resolved_price_snapshot,
        "snapshot_time": resolved_price_snapshot.get("captured_at"),
        "snapshot_source_label": resolved_price_snapshot.get("source_label"),
        "bars_snapshot_time": bars_snapshot_time,
        "scan_profile": profile,
        "scan_profile_label": _label_for_profile(profile),
        "scan_horizon": horizon,
    }


# ─────────────────────────────────────────────────────────────────────────────
def scan_universe(
    tickers: list[str],
    progress_callback=None,
    horizon: str = "all",
    profile: str = "balanced",
    setup_mode: str = "classic",
) -> list[dict]:
    """
    Score all tickers using concurrent ThreadPoolExecutor.
    Returns list sorted by win-rate + expected value descending.
    Only instruments passing hard gates (uptrend + dip + not overbought) are included.
    """
    results: list[dict] = []
    total = len(tickers)
    concurrency = max(1, int(getattr(config, "SCANNER_CONCURRENCY", 8)))

    def _score_one(ticker: str) -> dict | None:
        """Score a single ticker. Returns None on failure or if filtered out."""
        try:
            result = score_ticker(ticker, setup_mode=setup_mode)
            if result and result.get("score", 0) >= 2:
                breakdown = get_opportunity_rank_breakdown(result, horizon=horizon, profile=profile)
                result["rank_score"] = round(breakdown.get("score", 0.0), 3)
                result["rank_breakdown"] = breakdown
                result["signal_confidence"] = breakdown.get("confidence", {})
                result["setup_quality_score"] = breakdown.get("setup_quality_score", 50.0)
                result["company_quality_score"] = breakdown.get("company_quality_score", 50.0)
                result["macro_quality_score"] = breakdown.get("macro_quality_score", 50.0)
                return result
            return None
        except Exception:
            tb = traceback.format_exc()
            print(f"[scan_universe] Error scoring {ticker}: {tb}", file=sys.stderr)
            return None

    completed = 0
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_map = {executor.submit(_score_one, ticker): ticker for ticker in tickers}
        for future in as_completed(future_map):
            completed += 1
            ticker = future_map[future]
            if progress_callback:
                progress_callback(completed, total, ticker)
            try:
                result = future.result()
                if result is not None:
                    results.append(result)
            except Exception:
                tb = traceback.format_exc()
                print(f"[scan_universe] Future error for {ticker}: {tb}", file=sys.stderr)

    scan_completed_at = _snapshot_now_iso()
    for result in results:
        result["scan_completed_at"] = scan_completed_at
    results.sort(
        key=lambda x: (
            get_opportunity_rank_score(x, horizon, profile),
            x.get("macro_score", 50),
            x.get("company_score", 50),
        ),
        reverse=True,
    )
    return results

