"""analysis/portfolio_analysis.py — Per-holding sell/hold/buy-more analysis."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import pandas as pd
import config
from data.fetcher import get_ohlcv, get_ohlcv_4h, get_info, get_latest_price
from analysis.company_analysis import get_company_snapshot
from analysis.indicators import add_indicators
from analysis.macro_analysis import get_macro_snapshot
from analysis.setup_matching import (
    build_pullback_setup_snapshot,
    matches_pullback_setup,
    pullback_setup_distance,
)
from analysis.opportunity import _backtest_recent_history_path
from analysis.value_investing import get_value_investing_snapshot

# 4H bars per trading period
_BARS_1W  = 10
_BARS_1M  = 44
_BARS_3M  = 132
_BARS_1Y  = 500
_TRAJECTORY_RECOMMENDATION_WEIGHT = 0.45


def _safe(val, default=None):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    return val


def _safe_float(value, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_live_price_snapshot(holding: dict, ticker: str, *, force_refresh: bool = True) -> float | None:
    snapshot_price = _safe_float(holding.get("live_price_snapshot"))
    if snapshot_price is not None and snapshot_price > 0:
        return snapshot_price
    live_price = get_latest_price(ticker, force_refresh=force_refresh)
    if live_price and live_price > 0:
        return float(live_price)
    return None


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _sample_confidence(sample_count: float | None, target: float = 18.0) -> float:
    samples = max(0.0, float(sample_count or 0.0))
    if samples <= 0:
        return 0.0
    return float(_clip(np.log1p(samples) / np.log1p(max(2.0, target)), 0.0, 1.0))


def _direction(value: float | None, threshold: float = 0.25) -> int:
    if value is None or abs(value) < threshold:
        return 0
    return 1 if value > 0 else -1


def _history_score(backtest: dict, suffix: str, target_samples: float = 18.0) -> float:
    samples = backtest.get(f"n_setups_{suffix}")
    if samples is None:
        samples = backtest.get("n_samples")
    confidence = _sample_confidence(samples, target_samples)
    if confidence <= 0:
        return 0.0

    win_rate = _safe(backtest.get(f"win_rate_{suffix}"))
    median_return = _safe(backtest.get(f"median_return_{suffix}"))
    avg_return = _safe(backtest.get(f"avg_return_{suffix}"))

    win_component = _clip(((float(win_rate) - 50.0) / 18.0), -1.0, 1.0) if win_rate is not None else 0.0
    median_component = _clip((float(median_return) / 6.0), -1.0, 1.0) if median_return is not None else 0.0
    avg_component = _clip((float(avg_return) / 7.0), -1.0, 1.0) if avg_return is not None else 0.0
    return (win_component * 0.45 + median_component * 0.35 + avg_component * 0.20) * confidence


def _quality_label(score: float) -> str:
    if score >= 75:
        return "Wysoka"
    if score >= 45:
        return "Średnia"
    return "Niska"


def _reason_balance(reasons: list[dict]) -> float:
    positives = sum(1 for reason in reasons if not reason.get("bad"))
    negatives = sum(1 for reason in reasons if reason.get("bad"))
    return _clip((positives - negatives) / 6.0, -1.0, 1.0)


def _build_analysis_profile(
    entry_bt: dict,
    traj_bt: dict,
    reasons: list[dict],
    macro: dict,
    company: dict,
    trajectory_signal: dict,
    recommendation: str,
) -> dict:
    entry_score = _history_score(entry_bt, "22d", 18.0) * 0.65 + _history_score(entry_bt, "66d", 14.0) * 0.35
    trajectory_score = _history_score(traj_bt, "22d", 22.0) * 0.60 + _history_score(traj_bt, "66d", 18.0) * 0.40
    reason_score = _reason_balance(reasons)

    setup_raw = _clip(entry_score * 0.40 + trajectory_score * 0.40 + reason_score * 0.20, -1.0, 1.0)
    company_raw = _clip(
        (((float(company.get("financial_score", 50) or 50) - 50.0) / 30.0) * 0.75)
        + (((float(company.get("event_score", 50) or 50) - 50.0) / 35.0) * 0.25),
        -1.0,
        1.0,
    )
    macro_raw = _clip(((float(macro.get("score", 50) or 50) - 50.0) / 35.0), -1.0, 1.0)

    entry_conf = _sample_confidence(entry_bt.get("n_samples"), 18.0)
    traj_conf = _sample_confidence(traj_bt.get("n_samples"), 24.0)
    history_dir = _direction(entry_score + trajectory_score, 0.10)
    trajectory_dir = _direction(trajectory_signal.get("expectation_pct"), 0.50) if trajectory_signal.get("available") else 0
    recommendation_dir = 1 if recommendation == "DOKUP WIĘCEJ" else -1 if recommendation in {"SPRZEDAJ", "STOP LOSS", "ROZWAŻ SPRZEDAŻ"} else 0

    agreement = 0.55
    effective_dirs = [direction for direction in (history_dir, trajectory_dir) if direction != 0]
    if len(effective_dirs) >= 2:
        agreement = 1.0 if len(set(effective_dirs)) == 1 else 0.25
    elif effective_dirs:
        agreement = 0.70

    recommendation_alignment = 0.70
    if recommendation_dir and history_dir:
        recommendation_alignment = 1.0 if recommendation_dir == history_dir else 0.20

    confidence_score = round((entry_conf * 0.35 + traj_conf * 0.35 + agreement * 0.15 + recommendation_alignment * 0.15) * 100.0, 1)
    if max(entry_conf, traj_conf) < 0.35:
        confidence_note = "Mało podobnych przypadków w historii, więc rekomendacja jest ostrożniejsza."
    elif agreement < 0.4:
        confidence_note = "Podobne setupy i podobne ścieżki od zakupu nie mówią dziś jednym głosem."
    elif recommendation_alignment < 0.4:
        confidence_note = "Bieżąca rekomendacja jest bardziej defensywna niż sugerowałaby sama historia wejścia."
    else:
        confidence_note = "Setup, trajektoria od zakupu i jakość spółki są dziś względnie spójne."

    return {
        "setup_quality_score": round((setup_raw + 1.0) * 50.0, 1),
        "company_quality_score": round((company_raw + 1.0) * 50.0, 1),
        "macro_quality_score": round((macro_raw + 1.0) * 50.0, 1),
        "signal_confidence": {
            "score": confidence_score,
            "label": _quality_label(confidence_score),
            "note": confidence_note,
        },
    }


def _backtest_from_entry(df: pd.DataFrame, target_setup: dict | None, horizons=(_BARS_1W, _BARS_1M, _BARS_3M)) -> dict:
    """
    Find historical 4H bars matching the same canonical pullback setup used by the opportunity engine.
    Measures forward returns at 10/44/132 bars ≈ 1w/1m/3m and keeps the closest technical analogs.
    """
    if not target_setup:
        return {}

    _KEY_LABELS = ["5d", "22d", "66d"]
    closes = df["Close"].astype(float).values
    high_52w = df["High"].rolling(_BARS_1Y, min_periods=40).max()
    vol50_series = df["Vol_SMA50"] if "Vol_SMA50" in df.columns else df["Volume"].rolling(50, min_periods=10).mean()
    results = {h: [] for h in horizons}
    candidates: list[tuple[float, int]] = []

    max_horizon = max(horizons)
    for i in range(len(df) - max_horizon - 1):
        row = df.iloc[i]
        price = _safe(row.get("Close"))
        sma50 = _safe(row.get("SMA50"))
        sma200 = _safe(row.get("SMA200"))
        rsi14 = _safe(row.get("RSI14"))
        stoch_k = _safe(row.get("Stoch_K"))
        roc20 = _safe(row.get("ROC20"))
        adx14 = _safe(row.get("ADX14"))
        willi_r = _safe(row.get("WilliamsR"))
        rolling_high = _safe(high_52w.iloc[i])
        vol50 = _safe(vol50_series.iloc[i])

        if any(value is None for value in (price, sma50, sma200, rsi14, rolling_high)):
            continue

        drawdown = (float(rolling_high) - float(price)) / float(rolling_high) if float(rolling_high) > 0 else 0.0
        recent_volume = float(df["Volume"].iloc[max(0, i - 19) : i + 1].mean())
        vol_ratio = recent_volume / float(vol50) if vol50 and float(vol50) > 0 else 0.0

        candidate_setup = build_pullback_setup_snapshot(
            price=float(price),
            sma50=float(sma50),
            sma200=float(sma200),
            rsi14=float(rsi14),
            vol_ratio=vol_ratio,
            drawdown=drawdown,
            stoch_k=float(stoch_k) if stoch_k is not None else None,
            roc20=float(roc20) if roc20 is not None else None,
            adx14=float(adx14) if adx14 is not None else None,
            willi_r=float(willi_r) if willi_r is not None else None,
        )
        if not matches_pullback_setup(candidate_setup):
            continue

        distance = pullback_setup_distance(candidate_setup, target_setup)
        if distance > 4.6:
            continue
        candidates.append((distance, i))

    if not candidates:
        return {"n_samples": 0}

    candidates.sort(key=lambda item: item[0])
    selected = candidates[:80]
    if len(selected) < 8:
        relaxed = [item for item in candidates if item[0] <= 5.6][:40]
        if relaxed:
            selected = relaxed
    if len(selected) < 4:
        selected = candidates[: min(20, len(candidates))]

    for _, i in selected:
        price = closes[i]
        for h in horizons:
            fwd = (closes[i + h] - price) / price * 100
            results[h].append(fwd)

    out = {
        "n_samples": len(selected),
        "avg_distance": round(float(np.mean([distance for distance, _ in selected])), 2) if selected else None,
    }
    for idx, h in enumerate(horizons):
        lbl = _KEY_LABELS[idx] if idx < len(_KEY_LABELS) else f"{h}d"
        rets = results[h]
        out[f"n_setups_{lbl}"] = len(rets)
        if len(rets) >= 2:
            arr = np.array(rets)
            out[f"win_rate_{lbl}"]       = round(float((arr > 0).mean() * 100), 1)
            out[f"avg_return_{lbl}"]     = round(float(arr.mean()), 2)
            out[f"median_return_{lbl}"]  = round(float(np.median(arr)), 2)
        else:
            out[f"win_rate_{lbl}"]       = None
            out[f"avg_return_{lbl}"]     = None
            out[f"median_return_{lbl}"]  = None
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


def _backtest_trajectory(df: pd.DataFrame, avg_price: float, current_price: float, purchase_date: str) -> dict:
    """
    Finds historical windows with a similar entry setup and a similar price path to the current holding since purchase.
    Similarity uses the purchase setup, total return and the intermediate path shape over the same 4H-bar length.
    """
    if df.empty or not purchase_date or avg_price <= 0 or current_price <= 0:
        return {}

    try:
        purchase_dt = pd.Timestamp(pd.to_datetime(purchase_date)).normalize()
    except Exception:
        return {}

    index = pd.DatetimeIndex(pd.to_datetime(df.index))
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)

    purchase_idx = int(index.searchsorted(purchase_dt, side="left"))
    if purchase_idx >= len(df) - 1:
        return {"n_samples": 0, "msg": "Brak wystarczających notowań po dacie zakupu."}

    bars_held = len(df) - purchase_idx - 1
    if bars_held < 4:
        return {"n_samples": 0, "msg": "Zbyt krótki czas od pierwszego zakupu (wymagane > 2 dni)."}

    horizons = (_BARS_1W, _BARS_1M, _BARS_3M)
    max_horizon = max(horizons)
    max_start = purchase_idx - bars_held - max_horizon - 1
    if max_start < 0:
        return {"n_samples": 0, "msg": "Za mało starszej historii, by porównać pełną ścieżkę ceny od zakupu."}

    pnl_pct = (current_price - avg_price) / avg_price * 100
    closes = df["Close"].astype(float).to_numpy()
    high_52w = df["High"].rolling(_BARS_1Y, min_periods=40).max()
    vol50_series = df["Vol_SMA50"] if "Vol_SMA50" in df.columns else df["Volume"].rolling(50, min_periods=10).mean()

    def _build_setup_snapshot_at(idx: int, reference_price: float | None = None) -> dict | None:
        row = df.iloc[idx]
        price = _safe_float(reference_price)
        if price is None:
            price = _safe_float(row.get("Close"))

        sma50 = _safe_float(row.get("SMA50"))
        sma200 = _safe_float(row.get("SMA200"))
        rsi14 = _safe_float(row.get("RSI14"))
        stoch_k = _safe_float(row.get("Stoch_K"))
        roc20 = _safe_float(row.get("ROC20"))
        adx14 = _safe_float(row.get("ADX14"))
        willi_r = _safe_float(row.get("WilliamsR"))
        rolling_high = _safe_float(high_52w.iloc[idx])
        vol50 = _safe_float(vol50_series.iloc[idx])

        if any(value is None for value in (price, sma50, sma200, rsi14, rolling_high)):
            return None

        drawdown = (float(rolling_high) - float(price)) / float(rolling_high) if float(rolling_high) > 0 else 0.0
        recent_volume = float(df["Volume"].iloc[max(0, idx - 19) : idx + 1].mean())
        vol_ratio = recent_volume / float(vol50) if vol50 and float(vol50) > 0 else 0.0

        return build_pullback_setup_snapshot(
            price=float(price),
            sma50=float(sma50),
            sma200=float(sma200),
            rsi14=float(rsi14),
            vol_ratio=vol_ratio,
            drawdown=drawdown,
            stoch_k=stoch_k,
            roc20=roc20,
            adx14=adx14,
            willi_r=willi_r,
        )

    target_setup = _build_setup_snapshot_at(purchase_idx, avg_price)
    target_setup_is_classic = matches_pullback_setup(target_setup) if target_setup else False

    target_path = np.zeros(bars_held + 1, dtype=float)
    if bars_held > 0:
        target_path[1:] = _normalize_path(closes[purchase_idx + 1 : purchase_idx + bars_held + 1], avg_price)
    target_path[-1] = pnl_pct

    target_diff = np.diff(target_path)
    target_shape = _detrend_path(target_path)
    target_vol = float(np.std(target_diff)) if len(target_diff) else 0.0
    target_direction = 0 if abs(pnl_pct) < 0.2 else (1 if pnl_pct > 0 else -1)

    candidates = []
    strict_final_gap = max(3.0, abs(pnl_pct) * 0.40 + 1.0)
    strict_path_mae = max(3.4, target_vol * 0.95 + 1.0)
    strict_shape_mae = max(1.6, target_vol * 0.60 + 0.45)
    strict_step_mae = max(0.85, target_vol * 0.45 + 0.25)
    strict_setup_distance = 1.05
    loose_final_gap = max(4.8, abs(pnl_pct) * 0.60 + 1.5)
    loose_path_mae = max(4.8, target_vol * 1.30 + 1.5)
    loose_shape_mae = max(2.4, target_vol * 0.90 + 0.75)
    loose_step_mae = max(1.35, target_vol * 0.70 + 0.40)
    loose_setup_distance = 1.35

    _KEY_LABELS = ["5d", "22d", "66d"]
    results = {h: [] for h in horizons}

    for start in range(max_start + 1):
        end = start + bars_held
        window = closes[start : end + 1]
        if len(window) != bars_held + 1 or np.any(window <= 0):
            continue

        candidate_setup = _build_setup_snapshot_at(start)
        if target_setup and candidate_setup is None:
            continue
        if target_setup_is_classic and candidate_setup is not None and not matches_pullback_setup(candidate_setup):
            continue
        setup_distance = pullback_setup_distance(target_setup, candidate_setup) if target_setup and candidate_setup else None

        candidate_path = _normalize_path(window)
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

        corr_penalty = 1.0 - max(min(corr, 1.0), -1.0)
        setup_penalty = float(setup_distance or 0.0) * 0.85 if setup_distance is not None else 0.0
        score = shape_mae * 0.38 + step_mae * 0.22 + path_mae * 0.18 + final_gap * 0.12 + corr_penalty * 1.45 + setup_penalty
        candidates.append({
            "end": end,
            "score": score,
            "path": candidate_path,
            "final_gap": final_gap,
            "path_mae": path_mae,
            "shape_mae": shape_mae,
            "step_mae": step_mae,
            "corr": corr,
            "setup_distance": setup_distance,
        })

    if not candidates:
        return {"n_samples": 0, "msg": "Brak historycznych ścieżek ceny i setupu wejścia podobnych do obecnego przebiegu pozycji."}

    candidates.sort(key=lambda item: item["score"])
    selected = [
        item
        for item in candidates
        if item["final_gap"] <= strict_final_gap
        and item["path_mae"] <= strict_path_mae
        and item["shape_mae"] <= strict_shape_mae
        and item["step_mae"] <= strict_step_mae
        and item["corr"] >= 0.15
        and (item.get("setup_distance") is None or item.get("setup_distance") <= strict_setup_distance)
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
            and (item.get("setup_distance") is None or item.get("setup_distance") <= loose_setup_distance)
        ][:25]

    if len(selected) < 3:
        selected = candidates[: min(12, len(candidates))]

    for item in selected:
        end = item["end"]
        for h in horizons:
            fwd = (closes[end + h] - closes[end]) / closes[end] * 100
            results[h].append(fwd)

    selected_paths = np.vstack([item["path"] for item in selected]) if selected else None

    out = {
        "n_samples": len(results[horizons[0]]),
        "bars_held": bars_held,
        "days_held": max(1, int((index[-1] - index[purchase_idx]).total_seconds() // 86400)),
        "pnl_pct": round(pnl_pct, 2),
        "avg_final_gap": round(float(np.mean([item["final_gap"] for item in selected])), 2) if selected else None,
        "avg_path_mae": round(float(np.mean([item["path_mae"] for item in selected])), 2) if selected else None,
        "avg_shape_mae": round(float(np.mean([item["shape_mae"] for item in selected])), 2) if selected else None,
        "avg_step_mae": round(float(np.mean([item["step_mae"] for item in selected])), 2) if selected else None,
        "avg_corr": round(float(np.mean([item["corr"] for item in selected])), 2) if selected else None,
        "avg_setup_distance": round(float(np.mean([item["setup_distance"] for item in selected if item.get("setup_distance") is not None])), 2) if any(item.get("setup_distance") is not None for item in selected) else None,
        "current_path_pct": [round(float(value), 2) for value in target_path.tolist()],
        "matched_median_path_pct": [round(float(value), 2) for value in np.median(selected_paths, axis=0).tolist()] if selected_paths is not None else [],
        "matched_p25_path_pct": [round(float(value), 2) for value in np.percentile(selected_paths, 25, axis=0).tolist()] if selected_paths is not None else [],
        "matched_p75_path_pct": [round(float(value), 2) for value in np.percentile(selected_paths, 75, axis=0).tolist()] if selected_paths is not None else [],
        "entry_setup_matches_opportunity": target_setup_is_classic,
    }

    for idx, h in enumerate(horizons):
        lbl = _KEY_LABELS[idx] if idx < len(_KEY_LABELS) else f"{h}d"
        rets = results[h]
        if len(rets) >= 2:
            arr = np.array(rets)
            out[f"win_rate_{lbl}"]       = round(float((arr > 0).mean() * 100), 1)
            out[f"avg_return_{lbl}"]     = round(float(arr.mean()), 2)
            out[f"median_return_{lbl}"]  = round(float(np.median(arr)), 2)
        else:
            out[f"win_rate_{lbl}"]       = None
            out[f"avg_return_{lbl}"]     = None
            out[f"median_return_{lbl}"]  = None
    return out


def _trajectory_recommendation_signal(traj_bt: dict) -> dict:
    """Translate the post-entry path backtest into a strong buy/hold/sell bias."""
    if not traj_bt or int(traj_bt.get("n_samples", 0) or 0) < 3:
        return {
            "available": False,
            "bias": 0.0,
            "label": "neutral",
            "expectation_pct": None,
            "win_rate_pct": None,
            "samples": int(traj_bt.get("n_samples", 0) or 0) if traj_bt else 0,
        }

    weighted_returns = []
    weighted_wins = []
    for suffix, weight in (("5d", 0.20), ("22d", 0.50), ("66d", 0.30)):
        median_return = _safe(traj_bt.get(f"median_return_{suffix}"))
        win_rate = _safe(traj_bt.get(f"win_rate_{suffix}"))
        if median_return is not None:
            weighted_returns.append((weight, float(median_return)))
        if win_rate is not None:
            weighted_wins.append((weight, float(win_rate)))

    if not weighted_returns:
        return {
            "available": False,
            "bias": 0.0,
            "label": "neutral",
            "expectation_pct": None,
            "win_rate_pct": None,
            "samples": int(traj_bt.get("n_samples", 0) or 0),
        }

    total_ret_weight = sum(weight for weight, _ in weighted_returns)
    expectation_pct = sum(weight * value for weight, value in weighted_returns) / total_ret_weight if total_ret_weight > 0 else 0.0

    win_rate_pct = None
    if weighted_wins:
        total_win_weight = sum(weight for weight, _ in weighted_wins)
        win_rate_pct = sum(weight * value for weight, value in weighted_wins) / total_win_weight if total_win_weight > 0 else None

    sample_conf = _sample_confidence(traj_bt.get("n_samples"), 24.0)
    adjusted_expectation_pct = expectation_pct * (0.55 + 0.45 * sample_conf)
    adjusted_win_rate_pct = None
    if win_rate_pct is not None:
        adjusted_win_rate_pct = 50.0 + (win_rate_pct - 50.0) * (0.55 + 0.45 * sample_conf)

    if adjusted_expectation_pct < 0:
        label = "sell"
        bias = -1.0 * (0.55 + 0.45 * sample_conf)
    elif adjusted_expectation_pct > 5.0:
        label = "buy"
        bias = 1.0 * (0.55 + 0.45 * sample_conf)
    else:
        label = "hold"
        bias = 0.0

    if sample_conf < 0.35 and abs(adjusted_expectation_pct) < 4.0:
        label = "hold"
        bias = 0.0
    elif label == "buy" and adjusted_win_rate_pct is not None and adjusted_win_rate_pct < 50:
        label = "hold"
        bias = 0.0
    elif label == "hold" and adjusted_expectation_pct <= 1.0 and adjusted_win_rate_pct is not None and adjusted_win_rate_pct < 45:
        label = "sell"
        bias = -1.0

    return {
        "available": True,
        "bias": bias,
        "label": label,
        "expectation_pct": round(adjusted_expectation_pct, 2),
        "win_rate_pct": round(adjusted_win_rate_pct, 1) if adjusted_win_rate_pct is not None else None,
        "samples": int(traj_bt.get("n_samples", 0) or 0),
        "confidence": round(sample_conf * 100.0, 1),
    }


def _entry_recommendation_signal(entry_bt: dict, *, min_samples: int = 6) -> dict:
    """Translate similar-entry backtests into a softer buy/hold/sell bias for fresh holdings."""
    sample_count = int(entry_bt.get("n_samples", 0) or 0) if entry_bt else 0
    if not entry_bt or sample_count < max(0, int(min_samples)):
        return {
            "available": False,
            "bias": 0.0,
            "label": "neutral",
            "expectation_pct": None,
            "win_rate_pct": None,
            "samples": sample_count,
        }

    weighted_returns = []
    weighted_wins = []
    for suffix, weight in (("5d", 0.15), ("22d", 0.55), ("66d", 0.30)):
        median_return = _safe(entry_bt.get(f"median_return_{suffix}"))
        win_rate = _safe(entry_bt.get(f"win_rate_{suffix}"))
        if median_return is not None:
            weighted_returns.append((weight, float(median_return)))
        if win_rate is not None:
            weighted_wins.append((weight, float(win_rate)))

    if not weighted_returns:
        return {
            "available": False,
            "bias": 0.0,
            "label": "neutral",
            "expectation_pct": None,
            "win_rate_pct": None,
            "samples": sample_count,
        }

    total_ret_weight = sum(weight for weight, _ in weighted_returns)
    expectation_pct = sum(weight * value for weight, value in weighted_returns) / total_ret_weight if total_ret_weight > 0 else 0.0

    win_rate_pct = None
    if weighted_wins:
        total_win_weight = sum(weight for weight, _ in weighted_wins)
        win_rate_pct = sum(weight * value for weight, value in weighted_wins) / total_win_weight if total_win_weight > 0 else None

    sample_conf = _sample_confidence(entry_bt.get("n_samples"), 18.0)
    adjusted_expectation_pct = expectation_pct * (0.65 + 0.35 * sample_conf)
    adjusted_win_rate_pct = None
    if win_rate_pct is not None:
        adjusted_win_rate_pct = 50.0 + (win_rate_pct - 50.0) * (0.65 + 0.35 * sample_conf)

    if sample_conf < 0.35 and abs(adjusted_expectation_pct) < 4.0:
        label = "hold"
        bias = 0.0
    elif adjusted_expectation_pct >= 3.0 and (adjusted_win_rate_pct is None or adjusted_win_rate_pct >= 56.0):
        label = "buy"
        bias = 0.80 * (0.65 + 0.35 * sample_conf)
    elif adjusted_expectation_pct <= -2.0 and adjusted_win_rate_pct is not None and adjusted_win_rate_pct < 48.0:
        label = "sell"
        bias = -0.70 * (0.65 + 0.35 * sample_conf)
    else:
        label = "hold"
        bias = 0.0

    return {
        "available": True,
        "bias": round(bias, 3),
        "label": label,
        "expectation_pct": round(adjusted_expectation_pct, 2),
        "win_rate_pct": round(adjusted_win_rate_pct, 1) if adjusted_win_rate_pct is not None else None,
        "samples": sample_count,
        "confidence": round(sample_conf * 100.0, 1),
    }


def _make_recommendation(data: dict) -> tuple[str, str, list[dict]]:
    """
    Returns (action: str, color: str, reasons: list[dict]).
    Actions: STOP LOSS | SPRZEDAJ | TRZYMAJ | DOKUP WIĘCEJ
    """
    price     = data["price"]
    avg_price = data["avg_price"]
    pnl_pct   = data["pnl_pct"]
    rsi       = data.get("rsi14")
    sma200    = data.get("sma200")
    sma50     = data.get("sma50")
    bb_upper  = data.get("bb_upper")
    bb_lower  = data.get("bb_lower")
    stoch_k   = data.get("stoch_k")
    macd_hist = data.get("macd_hist")
    macro     = data.get("macro") or {}
    company   = data.get("company") or {}
    entry_signal = data.get("entry_signal") or {}
    trajectory_signal = data.get("trajectory_signal") or {}

    reasons = []
    sell_signals = 0
    buy_signals  = 0

    # ── STOP LOSS ─────────────────────────────────────────────────────────
    if pnl_pct <= -25:
        reasons.append({
            "icon": "🛑", "bad": True,
            "title": f"Strata przekracza próg stop loss ({pnl_pct:.1f}%)",
            "desc": (
                "Pozycja jest ponad 25% na minusie względem ceny zakupu. "
                "Dalsze trzymanie może pogłębić straty. "
                "Rozważ zamknięcie pozycji i ograniczenie kapitału pod stratą."
            ),
        })
        return "STOP LOSS", "#b91c1c", reasons

    # ── Trend (SMA200) ────────────────────────────────────────────────────
    if sma200 and price >= sma200:
        buy_signals += 2
        gap = (price - sma200) / sma200 * 100
        reasons.append({
            "icon": "📈", "bad": False,
            "title": "Trend długoterminowy wzrostowy zachowany",
            "desc": f"Cena {gap:.1f}% powyżej SMA200 ({sma200:.2f}) — fundament wzrostu nienaruszony.",
        })
    elif sma200:
        sell_signals += 2
        gap = (sma200 - price) / sma200 * 100
        reasons.append({
            "icon": "📉", "bad": True,
            "title": "Cena poniżej SMA200 — trend zagrożony",
            "desc": f"Cena {gap:.1f}% poniżej 200-dniowej średniej ({sma200:.2f}). Może sygnalizować zmianę trendu na spadkowy.",
        })

    # ── SMA50 (dip) ───────────────────────────────────────────────────────
    if sma50:
        if price <= sma50:
            buy_signals += 1
            gap = (sma50 - price) / sma50 * 100
            reasons.append({
                "icon": "🔽", "bad": False,
                "title": f"Korekta do SMA50 — dołek w trendzie (+{gap:.1f}% do SMA50)",
                "desc": f"Cena poniżej 50-dniowej średniej ({sma50:.2f}). Klasyczny pullback — potencjalne miejsce na dokuupienie.",
            })
        else:
            gap = (price - sma50) / sma50 * 100
            if gap > 15:
                sell_signals += 1
                reasons.append({
                    "icon": "⬆️", "bad": True,
                    "title": f"Cena mocno powyżej SMA50 (+{gap:.1f}%)",
                    "desc": f"Instrument rozciągnięty ponad 50-dniową średnią — możliwa korekta do SMA50.",
                })

    # ── RSI ───────────────────────────────────────────────────────────────
    if rsi is not None:
        if rsi < 30:
            buy_signals += 3
            reasons.append({
                "icon": "⚡", "bad": False,
                "title": f"Głęboka wyprzedaż — RSI={rsi:.1f}",
                "desc": "Ekstremalnie niski RSI — klasyczny sygnał technicznego dna. Statystycznie wysokie prawdopodobieństwo odbicia.",
            })
        elif rsi < 45:
            buy_signals += 1
            reasons.append({
                "icon": "🟡", "bad": False,
                "title": f"RSI w strefie wyprzedania ({rsi:.1f})",
                "desc": "RSI poniżej 45 — instrument wyprzedany, potencjał odbicia.",
            })
        elif rsi > 75:
            sell_signals += 2
            reasons.append({
                "icon": "🔴", "bad": True,
                "title": f"RSI mocno wykupiony ({rsi:.1f}) — ryzyko korekty",
                "desc": "RSI powyżej 75 — historycznie z takich poziomów często następuje korekta. Rozważ częściową realizację zysku.",
            })
        elif rsi > 65:
            sell_signals += 1
            reasons.append({
                "icon": "⚠️", "bad": True,
                "title": f"RSI podwyższony ({rsi:.1f})",
                "desc": "RSI zbliża się do strefy wykupienia. Monitoruj pozycję.",
            })

    # ── P&L status ────────────────────────────────────────────────────────
    if pnl_pct >= 30 and rsi is not None and rsi > 65:
        sell_signals += 2
        reasons.append({
            "icon": "💰", "bad": True,
            "title": f"Duży zysk + wykupienie — rozważ realizację ({pnl_pct:.1f}%)",
            "desc": f"Masz {pnl_pct:.1f}% zysku przy RSI={rsi:.1f}. Dobry moment na częściową lub pełną realizację zysku i zabezpieczenie kapitału.",
        })
    elif pnl_pct >= 15:
        buy_signals += 1
        reasons.append({
            "icon": "✅", "bad": False,
            "title": f"Pozycja na solidnym plusie (+{pnl_pct:.1f}%)",
            "desc": "Aktualna pozycja generuje znaczący niezrealizowany zysk.",
        })
    elif pnl_pct >= 0:
        buy_signals += 1
        reasons.append({
            "icon": "🟢", "bad": False,
            "title": f"Pozycja na plusie (+{pnl_pct:.1f}%)",
            "desc": "Wartość powyżej ceny zakupu — pozycja zyskowna.",
        })
    elif pnl_pct >= -15:
        reasons.append({
            "icon": "🟡", "bad": False,
            "title": f"Niewielka strata ({pnl_pct:.1f}%)",
            "desc": "Pozycja lekko na minusie — mieści się w normalnej zmienności.",
        })
    else:
        sell_signals += 1
        reasons.append({
            "icon": "🟠", "bad": True,
            "title": f"Pozycja na minusie ({pnl_pct:.1f}%)",
            "desc": "Wartość poniżej ceny zakupu. Oceń, czy zmienił się fundamentalny obraz spółki.",
        })

    # ── Bollinger Bands ───────────────────────────────────────────────────
    if bb_lower and price <= bb_lower * 1.03:
        buy_signals += 1
        reasons.append({
            "icon": "📉", "bad": False,
            "title": "Cena przy dolnym paśmie Bollingera — statystyczne dno",
            "desc": "Instrument przy dolnym BB — historycznie dobre miejsce zakupu/dokupu.",
        })
    elif bb_upper and price >= bb_upper * 0.98:
        sell_signals += 1
        reasons.append({
            "icon": "🔝", "bad": True,
            "title": "Cena przy górnym paśmie Bollingera — ryzyko korekty",
            "desc": "Instrument przy górnym BB — ryzyko cofnięcia do środka pasma.",
        })

    # ── MACD ─────────────────────────────────────────────────────────────
    if macd_hist is not None:
        if macd_hist > 0:
            buy_signals += 1
            reasons.append({
                "icon": "🔼", "bad": False,
                "title": "MACD — histogram dodatni, momentum rośnie",
                "desc": "MACD powyżej linii sygnału — krótkoterminowe momentum w górę.",
            })
        else:
            sell_signals += 1
            reasons.append({
                "icon": "🔽", "bad": True,
                "title": "MACD — histogram ujemny, momentum spada",
                "desc": "MACD poniżej linii sygnału — krótkoterminowe momentum w dół.",
            })

    # ── Stochastic ────────────────────────────────────────────────────────
    if stoch_k is not None:
        if stoch_k < 25:
            buy_signals += 1
            reasons.append({
                "icon": "🔁", "bad": False,
                "title": f"Stochastic w strefie wyprzedania (%K={stoch_k:.1f})",
                "desc": "Stochastyk w strefie <25 — potencjalny sygnał wejścia lub dokupu.",
            })
        elif stoch_k > 80:
            sell_signals += 1
            reasons.append({
                "icon": "⚠️", "bad": True,
                "title": f"Stochastic wykupiony (%K={stoch_k:.1f})",
                "desc": "Stochastyk >80 — możliwe krótkoterminowe odreagowanie.",
            })

    macro_bias = int(macro.get("portfolio_bias", 0) or 0)
    if macro_bias > 0:
        buy_signals += macro_bias
        reasons.append({
            "icon": "🌍", "bad": False,
            "title": f"Makro wspiera region {macro.get('region_label', 'instrumentu')}",
            "desc": f"{macro.get('summary', 'Otoczenie makro jest lekko wspierające dla akcji.')} Wpływ na rekomendację: +{macro_bias}.",
        })
    elif macro_bias < 0:
        sell_signals += abs(macro_bias)
        reasons.append({
            "icon": "🌍", "bad": True,
            "title": f"Makro ciąży regionowi {macro.get('region_label', 'instrumentu')}",
            "desc": f"{macro.get('summary', 'Otoczenie makro jest hamujące dla akcji.')} Wpływ na rekomendację: {macro_bias}.",
        })

    company_bias = int(company.get("portfolio_bias", 0) or 0)
    if company_bias > 0:
        buy_signals += company_bias
        reasons.append({
            "icon": "🏢", "bad": False,
            "title": "Dane spółki i wydarzenia wspierają pozycję",
            "desc": f"{company.get('summary', 'Obraz spółki jest wspierający.')} Wpływ na rekomendację: +{company_bias}.",
        })
    elif company_bias < 0:
        sell_signals += abs(company_bias)
        reasons.append({
            "icon": "🏢", "bad": True,
            "title": "Dane spółki lub wydarzenia osłabiają pozycję",
            "desc": f"{company.get('summary', 'Obraz spółki osłabia pozycję.')} Wpływ na rekomendację: {company_bias}.",
        })

    if entry_signal.get("available"):
        entry_expectation_pct = float(entry_signal.get("expectation_pct") or 0.0)
        entry_win_rate_pct = entry_signal.get("win_rate_pct")
        entry_win_text = f" przy trafności około {entry_win_rate_pct:.0f}%" if entry_win_rate_pct is not None else ""
        if entry_signal.get("label") == "sell":
            reasons.append({
                "icon": "📚", "bad": True,
                "title": f"Podobne wejścia historycznie były słabsze ({entry_expectation_pct:+.1f}%)",
                "desc": (
                    f"Podobne setupy od strony ceny wejścia dawały około {entry_expectation_pct:+.1f}% na ważonym horyzoncie 1W/1M/3M{entry_win_text}. "
                    "To osłabia argument za dokładaniem kapitału już teraz."
                ),
            })
        elif entry_signal.get("label") == "buy":
            reasons.append({
                "icon": "📚", "bad": False,
                "title": f"Podobne wejścia historycznie wspierają dokładanie ({entry_expectation_pct:+.1f}%)",
                "desc": (
                    f"Z perspektywy ceny wejścia podobne setupy dawały około {entry_expectation_pct:+.1f}% na ważonym horyzoncie 1W/1M/3M{entry_win_text}. "
                    "To ważny sygnał zwłaszcza dla świeżo otwartych pozycji, gdzie trajektoria od zakupu jest jeszcze za krótka."
                ),
            })

    if trajectory_signal.get("available"):
        expectation_pct = float(trajectory_signal.get("expectation_pct") or 0.0)
        win_rate_pct = trajectory_signal.get("win_rate_pct")
        win_text = f" przy trafności około {win_rate_pct:.0f}%" if win_rate_pct is not None else ""
        if trajectory_signal.get("label") == "sell":
            reasons.append({
                "icon": "📉", "bad": True,
                "title": f"Rozwój od zakupu nie wspiera dokładania ({expectation_pct:+.1f}% oczekiwania)",
                "desc": (
                    f"Podobne historyczne ścieżki od momentu wejścia dawały około {expectation_pct:+.1f}% na ważonym horyzoncie 1W/1M/3M{win_text}. "
                    "To jedna z najmocniejszych składowych decyzji, więc ujemny wynik wyraźnie ciąży rekomendacji."
                ),
            })
        elif trajectory_signal.get("label") == "hold":
            reasons.append({
                "icon": "🧭", "bad": False,
                "title": f"Rozwój od zakupu sugeruje raczej trzymanie ({expectation_pct:+.1f}%)",
                "desc": (
                    f"Podobne ścieżki od wejścia dają tylko umiarkowanie dodatnie oczekiwanie{win_text}. "
                    "To wspiera trzymanie pozycji, ale jeszcze nie daje mocnego sygnału do dokładania kapitału."
                ),
            })
        else:
            reasons.append({
                "icon": "🚀", "bad": False,
                "title": f"Rozwój od zakupu wspiera dokładanie ({expectation_pct:+.1f}%)",
                "desc": (
                    f"Podobne historyczne ścieżki od wejścia dawały ponad +5% oczekiwania{win_text}. "
                    "To jedna z najmocniejszych składowych decyzji i wyraźnie wspiera dokupowanie."
                ),
            })

    # ── Decision ─────────────────────────────────────────────────────────
    net = buy_signals - sell_signals
    base_signal = float(np.clip(net / 6.0, -1.0, 1.0))
    support_signal = trajectory_signal if trajectory_signal.get("available") else entry_signal
    support_bias = float(support_signal.get("bias", 0.0)) if support_signal.get("available") else 0.0
    support_weight = (
        _TRAJECTORY_RECOMMENDATION_WEIGHT if trajectory_signal.get("available")
        else 0.28 if entry_signal.get("available")
        else 0.0
    )
    combined_signal = base_signal * (1.0 - support_weight) + support_bias * support_weight

    if combined_signal >= 0.45 and support_bias > 0 and pnl_pct > -20 and sma200 and price >= sma200:
        return "DOKUP WIĘCEJ", "#15803d", reasons
    elif combined_signal >= 0.0:
        return "TRZYMAJ", "#1d4ed8", reasons
    elif pnl_pct > 5 or base_signal > 0:
        return "ROZWAŻ SPRZEDAŻ", "#d97706", reasons
    else:
        return "SPRZEDAJ", "#b91c1c", reasons


def get_holding_recommendation(holding: dict) -> tuple[str, str] | None:
    """Return only the action/color pair for portfolio list highlighting."""
    analysis = analyze_holding(holding)
    if not analysis:
        return None
    return analysis.get("recommendation"), analysis.get("rec_color")


# ─────────────────────────────────────────────────────────────────────────────
def analyze_holding(holding: dict) -> dict | None:
    """
    Full analysis for a portfolio holding.
    Returns comprehensive dict with recommendation, indicators, backtest, P&L.
    """
    ticker    = holding["ticker"]
    avg_price = float(holding["avg_price"])
    shares    = float(holding["shares"])
    notes     = holding.get("notes", "")
    purchase_date = holding.get("purchase_date") or holding.get("added", "")
    added     = holding.get("added", "")

    df = get_ohlcv_4h(ticker, period="2y")
    if df is None or len(df) < 30:
        return None

    df = add_indicators(df)
    info = get_info(ticker)
    macro = get_macro_snapshot(ticker, info=info)
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last

    price = float(last["Close"])
    live_price = _resolve_live_price_snapshot(holding, ticker, force_refresh=True)
    if live_price and live_price > 0:
        price = float(live_price)
    currency = str(info.get("currency") or holding.get("live_currency_snapshot") or holding.get("currency") or "USD").upper() or "USD"
    company = get_company_snapshot(ticker, info=info, price=price)
    value_investing = get_value_investing_snapshot(ticker, info=info, company=company, price=price)

    # P&L
    pnl_pct   = (price - avg_price) / avg_price * 100
    pnl_abs   = (price - avg_price) * shares
    cur_value = price * shares
    invested  = avg_price * shares

    # All indicators
    sma20     = _safe(last["SMA20"])
    sma50     = _safe(last["SMA50"])
    sma200    = _safe(last["SMA200"])
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
    vol       = last["Volume"]
    vol50     = _safe(last["Vol_SMA50"])

    atr_pct   = (atr14 / price * 100) if atr14 and price > 0 else None

    # 52W stats (4H: tail(500) ≈ 1 year)
    high_52w = float(df["High"].tail(_BARS_1Y).max())
    low_52w  = float(df["Low"].tail(_BARS_1Y).min())
    drawdown = (high_52w - price) / high_52w if high_52w > 0 else 0

    # Price changes (4H bar counts)
    change_1d = (price - float(prev["Close"])) / float(prev["Close"]) * 100
    change_5d = (price - float(df["Close"].iloc[-11])) / float(df["Close"].iloc[-11]) * 100 if len(df) >= 11 else 0
    change_1m = (price - float(df["Close"].iloc[-45])) / float(df["Close"].iloc[-45]) * 100 if len(df) >= 45 else 0

    # Volume
    vol_ratio = float(df["Volume"].tail(10).mean()) / float(vol50) if vol50 and vol50 > 0 else 0

    # Bollinger position
    bb_pos = ((price - bb_lower) / (bb_upper - bb_lower)) if bb_upper and bb_lower and (bb_upper - bb_lower) > 0 else 0.5

    # Suggested stop loss (1.5× ATR below SMA200 or entry price)
    stop_loss_atr = (price - 2.0 * atr14) if atr14 else None
    stop_loss_pct = ((price - stop_loss_atr) / price * 100) if stop_loss_atr else None

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

    # Entry-price backtest (4H)
    entry_bt = _backtest_from_entry(df, current_setup) if len(df) >= 90 else {}
    recent_history_backtest = _backtest_recent_history_path(df)
    traj_bt  = _backtest_trajectory(df, avg_price, price, purchase_date) if len(df) >= 90 else {}
    entry_signal = _entry_recommendation_signal(entry_bt)
    trajectory_signal = _trajectory_recommendation_signal(traj_bt)

    # Build intermediate dict for recommendation engine
    rec_data = {
        "price": price, "avg_price": avg_price, "pnl_pct": pnl_pct,
        "rsi14": rsi14, "sma200": sma200, "sma50": sma50,
        "bb_upper": bb_upper, "bb_lower": bb_lower,
        "stoch_k": stoch_k, "macd_hist": macd_hist,
        "entry_signal": entry_signal,
        "trajectory_signal": trajectory_signal,
        "macro": macro,
        "company": company,
    }
    recommendation, rec_color, reasons = _make_recommendation(rec_data)
    analysis_profile = _build_analysis_profile(
        entry_bt=entry_bt,
        traj_bt=traj_bt,
        reasons=reasons,
        macro=macro,
        company=company,
        trajectory_signal=trajectory_signal,
        recommendation=recommendation,
    )

    return {
        # Holdings metadata
        "ticker":         ticker,
        "name":           info.get("name", ticker),
        "sector":         info.get("sector", "—"),
        "industry":       info.get("industry", "—"),
        "currency":       currency,
        "exchange":       info.get("exchange", ""),
        "market_cap":     info.get("market_cap"),
        "pe_ratio":       info.get("pe_ratio"),
        "beta":           info.get("beta"),
        "shares":         shares,
        "avg_price":      round(avg_price, 2),
        "notes":          notes,
        "purchase_date":  purchase_date,
        "added":          added,
        # P&L
        "price":          round(price, 2),
        "pnl_pct":        round(pnl_pct, 2),
        "pnl_abs":        round(pnl_abs, 2),
        "current_value":  round(cur_value, 2),
        "invested":       round(invested, 2),
        # Changes
        "change_1d":      round(change_1d, 2),
        "change_5d":      round(change_5d, 2),
        "change_1m":      round(change_1m, 2),
        "high_52w":       round(high_52w, 2),
        "low_52w":        round(low_52w, 2),
        "drawdown_pct":   round(drawdown * 100, 1),
        # Technical indicators
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
        "adx14":          round(adx14, 1) if adx14 is not None else None,
        "williams_r":     round(willi_r, 1) if willi_r is not None else None,
        "roc20":          round(roc20, 2) if roc20 is not None else None,
        "vol_ratio":      round(vol_ratio, 2),
        "vol50":          int(vol50) if vol50 else None,
        "stop_loss_atr":  round(stop_loss_atr, 2) if stop_loss_atr else None,
        "stop_loss_pct":  round(stop_loss_pct, 1) if stop_loss_pct else None,
        # Recommendation
        "recommendation": recommendation,
        "rec_color":      rec_color,
        "reasons":        reasons,
        "macro":          macro,
        "company":        company,
        "value_investing": value_investing,
        # Historical context from entry price
        "entry_backtest": entry_bt,
        "entry_signal": entry_signal,
        "recent_history_backtest": recent_history_backtest,
        "trajectory_backtest": traj_bt,
        "trajectory_signal": trajectory_signal,
        "current_setup":  current_setup,
        "matches_opportunity_setup": matches_pullback_setup(current_setup),
        "setup_quality_score": analysis_profile["setup_quality_score"],
        "company_quality_score": analysis_profile["company_quality_score"],
        "macro_quality_score": analysis_profile["macro_quality_score"],
        "signal_confidence": analysis_profile["signal_confidence"],
    }


def get_quick_prices(holdings: list[dict]) -> dict[str, float]:
    """Fetch latest price for each holding using 15m-cached live price. Returns {ticker: price}."""
    prices = {}
    for h in holdings:
        ticker = h["ticker"]
        # Try live price first (1h TTL)
        price = get_latest_price(ticker)
        if price and price > 0:
            prices[ticker] = price
        else:
            # Fallback to daily OHLCV
            df = get_ohlcv(ticker, period="5d")
            if df is not None and len(df) > 0:
                prices[ticker] = float(df["Close"].iloc[-1])
    return prices
