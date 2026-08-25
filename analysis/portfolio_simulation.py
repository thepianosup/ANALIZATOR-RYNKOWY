"""analysis/portfolio_simulation.py — Advanced historical scenario simulation for the full portfolio."""
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import pandas as pd

from data.fetcher import get_info, get_latest_price, get_ohlcv
from analysis.portfolio_analysis import analyze_holding, _resolve_live_price_snapshot
from analysis.portfolio_history import build_portfolio_history


_TRADING_DAYS_1M = 21
_TRADING_DAYS_3M = 63
_TRADING_DAYS_6M = 126


def _safe_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _load_price_panel(holdings: list[dict]) -> tuple[pd.DataFrame, dict[str, float], list[dict]]:
    series_map: dict[str, pd.Series] = {}
    latest_prices: dict[str, float] = {}
    active_holdings: list[dict] = []

    for holding in holdings:
        ticker = (holding.get("ticker") or "").strip().upper()
        shares = _safe_float(holding.get("shares"), 0.0)
        avg_price = _safe_float(holding.get("avg_price"), 0.0)
        if not ticker or shares <= 0 or avg_price <= 0:
            continue

        df = get_ohlcv(ticker, period="2y")
        if df is None or len(df) < 140 or "Close" not in df.columns:
            continue

        closes = df["Close"].astype(float).copy()
        info = get_info(ticker)
        currency = str((info or {}).get("currency") or "USD").upper() or "USD"
        latest_price_native = float(_resolve_live_price_snapshot(holding, ticker, force_refresh=True) or closes.iloc[-1])
        closes.name = ticker
        series_map[ticker] = closes
        latest_prices[ticker] = latest_price_native
        active_holdings.append({
            "ticker": ticker,
            "shares": shares,
            "avg_price": avg_price,
            "purchase_date": holding.get("purchase_date") or holding.get("added", ""),
            "notes": holding.get("notes", ""),
            "added": holding.get("added", ""),
            "currency": currency,
            "latest_price_native": latest_price_native,
        })

    if not series_map:
        raise ValueError("Brak wystarczających danych historycznych dla pozycji w portfelu.")

    prices = pd.concat(series_map.values(), axis=1).sort_index()
    first_valid_dates = [prices[col].first_valid_index() for col in prices.columns]
    first_valid_dates = [dt for dt in first_valid_dates if dt is not None]
    if not first_valid_dates:
        raise ValueError("Nie udało się zbudować wspólnej historii cen dla portfela.")

    prices = prices.loc[max(first_valid_dates):].ffill().dropna(how="any")
    if len(prices) < 120:
        raise ValueError("Za mało wspólnej historii cen, by policzyć zaawansowaną symulację.")

    return prices, latest_prices, active_holdings


def _winsorize_returns(returns: pd.DataFrame) -> pd.DataFrame:
    clipped = returns.copy()
    for col in clipped.columns:
        lower = float(clipped[col].quantile(0.01))
        upper = float(clipped[col].quantile(0.99))
        clipped[col] = clipped[col].clip(lower=lower, upper=upper)
    return clipped


def _portfolio_block_score(block: np.ndarray, weights: np.ndarray) -> float:
    asset_values = weights.copy()
    for daily_returns in block:
        asset_values *= (1.0 + daily_returns)
    return float(asset_values.sum() - 1.0)


def _blended_backtest_signal(backtest: dict) -> float:
    if not backtest:
        return 0.0

    samples = float(backtest.get("n_samples", 0) or 0)
    sample_conf = min(samples / 12.0, 1.0)
    if sample_conf <= 0:
        return 0.0

    components = []
    for label, weight in (("5d", 0.15), ("22d", 0.35), ("66d", 0.50)):
        avg_ret = backtest.get(f"avg_return_{label}")
        win_rate = backtest.get(f"win_rate_{label}")
        if avg_ret is None or win_rate is None:
            continue
        ret_score = float(np.clip(float(avg_ret) / 12.0, -1.0, 1.0))
        win_score = float(np.clip((float(win_rate) - 50.0) / 30.0, -1.0, 1.0))
        components.append(weight * (ret_score * 0.6 + win_score * 0.4))

    return float(sample_conf * sum(components)) if components else 0.0


def _context_snapshot(holding: dict) -> dict:
    try:
        analysis = analyze_holding(holding)
    except Exception:
        analysis = None

    if not analysis:
        return {
            "entry_signal": 0.0,
            "trajectory_signal": 0.0,
            "blended_signal": 0.0,
            "days_held": 0,
            "entry_samples": 0,
            "trajectory_samples": 0,
        }

    entry_bt = analysis.get("entry_backtest") or {}
    traj_bt = analysis.get("trajectory_backtest") or {}
    entry_signal = _blended_backtest_signal(entry_bt)
    trajectory_signal = _blended_backtest_signal(traj_bt)
    blended_signal = float(np.clip(entry_signal * 0.45 + trajectory_signal * 0.55, -1.0, 1.0))
    return {
        "entry_signal": round(entry_signal, 3),
        "trajectory_signal": round(trajectory_signal, 3),
        "blended_signal": round(blended_signal, 3),
        "days_held": int((traj_bt.get("days_held") or 0)),
        "entry_samples": int(entry_bt.get("n_samples", 0) or 0),
        "trajectory_samples": int(traj_bt.get("n_samples", 0) or 0),
    }


def _context_drift_vector(active_holdings: list[dict]) -> tuple[np.ndarray, dict[str, dict]]:
    contexts: dict[str, dict] = {}
    drift = []
    for holding in active_holdings:
        ctx = _context_snapshot(holding)
        contexts[holding["ticker"]] = ctx
        drift.append(float(np.clip(ctx["blended_signal"] * 0.0006, -0.0012, 0.0012)))
    return np.array(drift, dtype=float), contexts


def _adjust_block(block: np.ndarray, context_drift: np.ndarray) -> np.ndarray:
    adjusted = block + context_drift.reshape(1, -1)
    return np.clip(adjusted, -0.85, 0.85)


def _portfolio_currency_context(active_holdings: list[dict]) -> tuple[dict, dict[str, str]]:
    source_currency_map = {
        holding["ticker"]: str(holding.get("currency") or "USD").upper() or "USD"
        for holding in active_holdings
    }
    codes = sorted({code for code in source_currency_map.values() if code and code != "—"})
    if not codes:
        return {
            "label": "—",
            "detail": "Brak danych o walutach pozycji.",
            "is_mixed": False,
            "codes": [],
        }, source_currency_map

    if len(codes) == 1:
        code = codes[0]
        return {
            "label": code,
            "detail": f"Otwarte pozycje są notowane w {code}.",
            "is_mixed": False,
            "codes": codes,
        }, source_currency_map

    return {
        "label": " / ".join(codes),
        "detail": "Kwoty przykładowe są rozdzielone osobno dla każdej waluty otwartych pozycji.",
        "is_mixed": True,
        "codes": codes,
    }, source_currency_map


def _currency_sort_key(code: str) -> tuple[int, str]:
    priority = {"USD": 0, "PLN": 1, "EUR": 2}
    return priority.get(code, 99), code


def _currency_breakdown(active_holdings: list[dict], latest_prices: dict[str, float]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for holding in active_holdings:
        ticker = holding["ticker"]
        currency = str(holding.get("currency") or "USD").upper() or "USD"
        current_value = float(latest_prices[ticker] * holding["shares"])
        invested_value = float(holding["avg_price"] * holding["shares"])
        bucket = grouped.setdefault(currency, {
            "currency": currency,
            "current_value": 0.0,
            "invested_value": 0.0,
            "current_pnl_abs": 0.0,
            "current_pnl_pct": 0.0,
            "n_positions": 0,
        })
        bucket["current_value"] += current_value
        bucket["invested_value"] += invested_value
        bucket["n_positions"] += 1

    rows = []
    for currency, bucket in grouped.items():
        pnl_abs = float(bucket["current_value"] - bucket["invested_value"])
        pnl_pct = ((float(bucket["current_value"]) / float(bucket["invested_value"])) - 1.0) * 100.0 if bucket["invested_value"] > 0 else 0.0
        rows.append({
            "currency": currency,
            "current_value": round(float(bucket["current_value"]), 2),
            "invested_value": round(float(bucket["invested_value"]), 2),
            "current_pnl_abs": round(pnl_abs, 2),
            "current_pnl_pct": round(float(pnl_pct), 2),
            "n_positions": int(bucket["n_positions"]),
        })

    return sorted(rows, key=lambda row: _currency_sort_key(row["currency"]))


def _scenario_pool(blocks: list[np.ndarray], scores: np.ndarray, scenario: str) -> list[np.ndarray]:
    if len(blocks) <= 20:
        return blocks

    q30 = float(np.quantile(scores, 0.30))
    q70 = float(np.quantile(scores, 0.70))

    if scenario == "negative":
        selected = [block for block, score in zip(blocks, scores) if score <= q30]
    elif scenario == "positive":
        selected = [block for block, score in zip(blocks, scores) if score >= q70]
    else:
        selected = blocks

    return selected if len(selected) >= max(12, len(blocks) // 10) else blocks


def _history_profile(history_snapshot: dict | None, current_value: float) -> dict:
    if not history_snapshot:
        return {
            "bias": 0.0,
            "target_quantile": 0.50,
            "recent_change_abs": 0.0,
            "recent_change_pct": 0.0,
            "win_rate": 50.0,
            "closed_share": 0.0,
        }

    summary = history_snapshot.get("summary") or {}
    chart = history_snapshot.get("chart") or {}
    total = np.array(chart.get("total_pnl") or [], dtype=float)
    if len(total) >= 3:
        lookback = min(24, len(total) - 1)
        recent_change_abs = float(total[-1] - total[-1 - lookback])
    else:
        recent_change_abs = 0.0

    recent_change_pct = (recent_change_abs / current_value * 100.0) if current_value > 0 else 0.0
    open_count = max(1, int(summary.get("open_count", 0) or 0))
    closed_count = int(summary.get("closed_count", 0) or 0)
    open_win_rate = float(summary.get("open_winners", 0) or 0) / open_count
    closed_win_rate = float(summary.get("closed_winners", 0) or 0) / closed_count if closed_count else open_win_rate
    mix_weight = min(closed_count / 6.0, 1.0)
    blended_win_rate = (open_win_rate * (1.0 - mix_weight) + closed_win_rate * mix_weight) * 100.0
    closed_share = abs(float(summary.get("closed_pnl_abs", 0) or 0)) / max(abs(float(summary.get("total_pnl_abs", 0) or 0)), 1.0)

    bias = float(np.clip(
        np.clip(recent_change_pct / 6.0, -1.0, 1.0) * 0.45
        + np.clip((blended_win_rate - 50.0) / 25.0, -1.0, 1.0) * 0.35
        + np.clip(float(summary.get("total_pnl_abs", 0) or 0) / max(current_value * 0.10, 1.0), -1.0, 1.0) * 0.20,
        -1.0,
        1.0,
    ))

    return {
        "bias": round(bias, 3),
        "target_quantile": float(np.clip(0.50 + bias * 0.18, 0.25, 0.75)),
        "recent_change_abs": round(recent_change_abs, 2),
        "recent_change_pct": round(recent_change_pct, 2),
        "win_rate": round(blended_win_rate, 1),
        "closed_share": round(float(np.clip(closed_share, 0.0, 1.0)), 2),
    }


def _likely_scenario_pool(blocks: list[np.ndarray], scores: np.ndarray, history_profile: dict) -> list[np.ndarray]:
    if len(blocks) <= 20:
        return blocks

    target_quantile = float(history_profile.get("target_quantile", 0.50))
    bias = abs(float(history_profile.get("bias", 0.0)))
    band = 0.10 + (0.05 if bias < 0.25 else 0.0)
    low_q = max(0.0, target_quantile - band)
    high_q = min(1.0, target_quantile + band)
    low_score = float(np.quantile(scores, low_q))
    high_score = float(np.quantile(scores, high_q))
    selected = [block for block, score in zip(blocks, scores) if low_score <= score <= high_score]
    if len(selected) >= max(16, len(blocks) // 8):
        return selected

    target_score = float(np.quantile(scores, target_quantile))
    ranked = sorted(zip(blocks, scores), key=lambda item: abs(float(item[1]) - target_score))
    return [block for block, _ in ranked[: max(18, len(blocks) // 6)]]


def _simulate_paths(
    block_pool: list[np.ndarray],
    current_values: np.ndarray,
    context_drift: np.ndarray,
    horizon_days: int,
    n_sims: int,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    n_assets = len(current_values)
    paths = np.empty((n_sims, horizon_days + 1), dtype=float)
    final_assets = np.empty((n_sims, n_assets), dtype=float)
    checkpoint_days = {
        "1m": min(_TRADING_DAYS_1M, horizon_days),
        "3m": min(_TRADING_DAYS_3M, horizon_days),
        "6m": min(_TRADING_DAYS_6M, horizon_days),
    }
    checkpoint_assets = {
        label: np.empty((n_sims, n_assets), dtype=float)
        for label in checkpoint_days
    }

    for sim_idx in range(n_sims):
        asset_values = current_values.astype(float).copy()
        paths[sim_idx, 0] = float(asset_values.sum())
        day = 0
        while day < horizon_days:
            block = block_pool[int(rng.integers(0, len(block_pool)))]
            steps = min(len(block), horizon_days - day)
            adjusted_block = _adjust_block(block[:steps], context_drift)
            for daily_returns in adjusted_block:
                asset_values *= (1.0 + daily_returns)
                day += 1
                paths[sim_idx, day] = float(asset_values.sum())
                for label, checkpoint_day in checkpoint_days.items():
                    if day == checkpoint_day:
                        checkpoint_assets[label][sim_idx] = asset_values.copy()
        final_assets[sim_idx] = asset_values
        for label, checkpoint_day in checkpoint_days.items():
            if checkpoint_day >= day:
                checkpoint_assets[label][sim_idx] = asset_values.copy()

    return paths, final_assets, checkpoint_assets


def _threshold_stats(paths: np.ndarray, current_value: float, target_pct: float) -> dict:
    target_value = current_value * (1.0 + target_pct / 100.0)
    if target_pct >= 0:
        hits = paths[:, 1:] >= target_value
    else:
        hits = paths[:, 1:] <= target_value

    hit_any = hits.any(axis=1)
    hit_probability = float(hit_any.mean() * 100.0)
    if not hit_any.any():
        return {
            "target_pct": target_pct,
            "hit_probability": round(hit_probability, 1),
            "median_days": None,
        }

    first_hit = np.argmax(hits, axis=1) + 1
    return {
        "target_pct": target_pct,
        "hit_probability": round(hit_probability, 1),
        "median_days": int(np.median(first_hit[hit_any])),
    }


def _path_percentiles(paths: np.ndarray, current_value: float) -> dict[str, list[float]]:
    pct_paths = (paths / current_value - 1.0) * 100.0
    return {
        "p10": np.percentile(pct_paths, 10, axis=0).round(2).tolist(),
        "p25": np.percentile(pct_paths, 25, axis=0).round(2).tolist(),
        "p50": np.percentile(pct_paths, 50, axis=0).round(2).tolist(),
        "p75": np.percentile(pct_paths, 75, axis=0).round(2).tolist(),
        "p90": np.percentile(pct_paths, 90, axis=0).round(2).tolist(),
    }


def _horizon_stats(paths: np.ndarray, current_value: float, horizon_days: int) -> dict[str, dict]:
    pct_paths = (paths / current_value - 1.0) * 100.0
    out: dict[str, dict] = {}
    for label, idx in (("1m", _TRADING_DAYS_1M), ("3m", _TRADING_DAYS_3M), ("6m", _TRADING_DAYS_6M)):
        use_idx = min(idx, horizon_days)
        arr = pct_paths[:, use_idx]
        median_return = float(np.median(arr))
        median_value = float(np.median(paths[:, use_idx]))
        p10_value = float(np.percentile(paths[:, use_idx], 10))
        p90_value = float(np.percentile(paths[:, use_idx], 90))
        median_pnl_abs = median_value - current_value
        monthlyized = ((1.0 + median_return / 100.0) ** (21 / max(use_idx, 1)) - 1.0) * 100.0
        out[label] = {
            "days": use_idx,
            "median_return_pct": round(median_return, 2),
            "avg_return_pct": round(float(arr.mean()), 2),
            "p10_return_pct": round(float(np.percentile(arr, 10)), 2),
            "p90_return_pct": round(float(np.percentile(arr, 90)), 2),
            "profit_probability": round(float((arr > 0).mean() * 100.0), 1),
            "median_value": round(median_value, 2),
            "median_pnl_abs": round(median_pnl_abs, 2),
            "p10_value": round(p10_value, 2),
            "p90_value": round(p90_value, 2),
            "monthlyized_return_pct": round(float(monthlyized), 2),
        }
    return out


def _currency_horizon_stats(
    checkpoint_assets: dict[str, np.ndarray],
    current_values: np.ndarray,
    active_holdings: list[dict],
) -> dict[str, list[dict]]:
    groups: dict[str, list[int]] = {}
    for idx, holding in enumerate(active_holdings):
        currency = str(holding.get("currency") or "USD").upper() or "USD"
        groups.setdefault(currency, []).append(idx)

    out: dict[str, list[dict]] = {}
    for label, assets in checkpoint_assets.items():
        rows = []
        for currency, indices in groups.items():
            idx_arr = np.array(indices, dtype=int)
            current_bucket = float(current_values[idx_arr].sum())
            simulated_values = assets[:, idx_arr].sum(axis=1)
            median_value = float(np.median(simulated_values))
            rows.append({
                "currency": currency,
                "median_value": round(median_value, 2),
                "median_pnl_abs": round(median_value - current_bucket, 2),
                "current_value": round(current_bucket, 2),
            })
        out[label] = sorted(rows, key=lambda row: _currency_sort_key(row["currency"]))
    return out


def _build_insights(
    scenarios: dict,
    holdings_meta: list[dict],
    current_value: float,
    history_profile: dict,
) -> list[dict]:
    likely = scenarios.get("likely") or scenarios.get("neutral") or {}
    likely_3m = (likely.get("horizons") or {}).get("3m", {})
    likely_6m = (likely.get("horizons") or {}).get("6m", {})
    weighted_context = float(np.mean([row.get("blended_context", 0.0) for row in holdings_meta])) if holdings_meta else 0.0
    top_risk = max(holdings_meta, key=lambda row: row.get("risk_contribution_pct", 0.0), default={})
    weak_context = min(holdings_meta, key=lambda row: row.get("blended_context", 0.0), default={})

    direction = "sprzyja dalszemu wzrostowi" if history_profile.get("bias", 0.0) > 0.15 else "jest mieszana" if history_profile.get("bias", 0.0) > -0.15 else "ostatnio osuwa się w słabszy reżim"
    setup_note = "Setup wejścia i rozwój od zakupu wspierają portfel." if weighted_context > 0.12 else "Setup wejścia i rozwój od zakupu są raczej neutralne." if weighted_context > -0.12 else "Część pozycji ma słaby kontekst techniczny od momentu wejścia."

    insights = [
        {
            "title": "Najbardziej prawdopodobna ścieżka",
            "tone": "info",
            "text": f"Na bazie historii Twoich pozycji najbardziej prawdopodobny przebieg {direction}. Mediana na 3 miesiące to {likely_3m.get('median_return_pct', 0):+.1f}%. Kwoty przykładowe są niżej rozdzielone na waluty otwartych pozycji.",
        },
        {
            "title": "Na co uważać",
            "tone": "warn",
            "text": f"W scenariuszu najbardziej prawdopodobnym portfel może po drodze zejść do około {likely.get('max_drawdown', {}).get('median_pct', 0):.1f}% obsunięcia. Największy wkład w ryzyko daje {top_risk.get('ticker', '—')} ({top_risk.get('risk_contribution_pct', 0):.1f}% ryzyka).",
        },
        {
            "title": "Kontekst wejścia",
            "tone": "good" if weighted_context >= 0 else "warn",
            "text": f"{setup_note} Średni sygnał kontekstowy dla portfela wynosi {weighted_context:+.2f}. Najsłabszy układ ma {weak_context.get('ticker', '—')} ({weak_context.get('blended_context', 0):+.2f}).",
        },
        {
            "title": "Ciekawostka z historii",
            "tone": "info",
            "text": f"Ostatnie 4H z historii pozycji zmieniły łączny wynik o {history_profile.get('recent_change_pct', 0):+.2f}% i dają skuteczność pozycji około {history_profile.get('win_rate', 50):.1f}%.",
        },
        {
            "title": "Sygnał do obserwacji",
            "tone": "warn" if likely_6m.get('p10_return_pct', 0) < 0 else "good",
            "text": f"Jeśli portfel nie utrzyma ścieżki najbardziej prawdopodobnej, dolny 10 percentyl na 6 miesięcy schodzi do {likely_6m.get('p10_return_pct', 0):+.1f}% względem teraz.",
        },
    ]
    return insights


def _max_drawdown_stats(paths: np.ndarray) -> dict[str, float]:
    running_max = np.maximum.accumulate(paths, axis=1)
    drawdowns = (paths / np.maximum(running_max, 1e-9) - 1.0) * 100.0
    worst = np.min(drawdowns, axis=1)
    return {
        "median_pct": round(float(np.median(worst)), 2),
        "stress_pct": round(float(np.percentile(worst, 10)), 2),
    }


def _contribution_stats(
    active_holdings: list[dict],
    current_values: np.ndarray,
    final_assets: np.ndarray,
) -> list[dict]:
    median_final = np.median(final_assets, axis=0)
    rows = []
    for idx, holding in enumerate(active_holdings):
        base_value = float(current_values[idx])
        pnl_abs = float(median_final[idx] - base_value)
        pnl_pct = ((float(median_final[idx]) / base_value) - 1.0) * 100.0 if base_value > 0 else 0.0
        rows.append({
            "ticker": holding["ticker"],
            "currency": str(holding.get("currency") or "USD").upper() or "USD",
            "median_pnl_abs": round(pnl_abs, 2),
            "median_return_pct": round(pnl_pct, 2),
        })
    rows.sort(key=lambda row: row["median_pnl_abs"], reverse=True)
    return rows


def simulate_portfolio_scenarios(
    holdings: list[dict],
    horizon_days: int = _TRADING_DAYS_6M,
    n_sims: int = 1200,
    block_size: int = 5,
    seed: int = 42,
    history_snapshot: dict | None = None,
) -> dict:
    if not holdings:
        raise ValueError("Portfel jest pusty.")

    prices, latest_prices, active_holdings = _load_price_panel(holdings)
    returns = prices.pct_change(fill_method=None).fillna(0.0)
    returns = _winsorize_returns(returns.iloc[-520:]).dropna(how="any")
    if len(returns) < 120:
        raise ValueError("Za mało dziennych stóp zwrotu do symulacji portfela.")

    current_values = []
    invested_values = []
    for holding in active_holdings:
        price = latest_prices[holding["ticker"]]
        current_values.append(price * holding["shares"])
        invested_values.append(holding["avg_price"] * holding["shares"])

    current_values_arr = np.array(current_values, dtype=float)
    invested_arr = np.array(invested_values, dtype=float)
    current_value = float(current_values_arr.sum())
    invested_value = float(invested_arr.sum())
    if current_value <= 0:
        raise ValueError("Nie udało się policzyć aktualnej wartości portfela.")

    history_snapshot = history_snapshot or build_portfolio_history(holdings)
    history_profile = _history_profile(history_snapshot, current_value)
    currency_context, currency_map = _portfolio_currency_context(active_holdings)
    currency_breakdown = _currency_breakdown(active_holdings, latest_prices)

    weights = current_values_arr / current_value
    context_drift, holding_contexts = _context_drift_vector(active_holdings)
    block_returns: list[np.ndarray] = []
    block_scores = []
    for start in range(0, len(returns) - block_size + 1):
        block = returns.iloc[start : start + block_size].to_numpy(dtype=float)
        block_returns.append(block)
        block_scores.append(_portfolio_block_score(_adjust_block(block, context_drift), weights))

    if len(block_returns) < 25:
        raise ValueError("Za mało historycznych okien, by zbudować scenariusze symulacji.")

    block_scores_arr = np.array(block_scores, dtype=float)
    cov_annual = returns.cov().to_numpy(dtype=float) * 252.0
    port_var = float(weights @ cov_annual @ weights)
    if port_var > 0:
        marginal = cov_annual @ weights
        risk_contrib = weights * marginal / port_var
    else:
        risk_contrib = np.zeros_like(weights)

    holdings_meta = []
    recent_3m = ((prices.iloc[-1] / prices.iloc[max(0, len(prices) - _TRADING_DAYS_3M)] - 1.0) * 100.0).to_dict()
    recent_1y = ((prices.iloc[-1] / prices.iloc[max(0, len(prices) - 252)] - 1.0) * 100.0).to_dict()
    annual_vol = (returns.std() * np.sqrt(252.0) * 100.0).to_dict()

    for idx, holding in enumerate(active_holdings):
        ticker = holding["ticker"]
        holdings_meta.append({
            "ticker": ticker,
            "shares": round(holding["shares"], 4),
            "avg_price": round(holding["avg_price"], 2),
            "current_price": round(latest_prices[ticker], 2),
            "currency": currency_map.get(ticker, "—"),
            "current_value": round(float(current_values_arr[idx]), 2),
            "invested": round(float(invested_arr[idx]), 2),
            "weight_pct": round(float(weights[idx] * 100.0), 1),
            "risk_contribution_pct": round(float(risk_contrib[idx] * 100.0), 1),
            "vol_annual_pct": round(float(annual_vol.get(ticker, 0.0)), 1),
            "return_3m_pct": round(float(recent_3m.get(ticker, 0.0)), 2),
            "return_1y_pct": round(float(recent_1y.get(ticker, 0.0)), 2),
            "entry_context": holding_contexts[ticker]["entry_signal"],
            "trajectory_context": holding_contexts[ticker]["trajectory_signal"],
            "blended_context": holding_contexts[ticker]["blended_signal"],
            "days_held": holding_contexts[ticker]["days_held"],
        })

    scenario_defs = {
        "negative": {
            "label": "Scenariusz negatywny",
            "description": "Losowanie z historycznie słabszych 5-dniowych fragmentów portfela. Zakłada gorszą sekwencję sesji i szybsze narastanie obsunięcia.",
        },
        "likely": {
            "label": "Scenariusz najbardziej prawdopodobny",
            "description": "Budowany z historii Twoich pozycji i ostatniej ścieżki wyniku portfela. To bazowy przebieg najbardziej zbliżony do tego, co już pokazał Twój portfel.",
        },
        "neutral": {
            "label": "Scenariusz neutralny",
            "description": "Losowanie z całej historii 5-dniowych fragmentów portfela. Pokazuje bazowy rozkład wyników bez dodatkowego przechylenia.",
        },
        "positive": {
            "label": "Scenariusz pozytywny",
            "description": "Losowanie z historycznie mocniejszych 5-dniowych fragmentów portfela. Zakłada korzystniejsze momentum i łagodniejsze cofnięcia.",
        },
    }

    scenarios = {}
    for offset, (scenario_key, meta) in enumerate(scenario_defs.items()):
        pool = _likely_scenario_pool(block_returns, block_scores_arr, history_profile) if scenario_key == "likely" else _scenario_pool(block_returns, block_scores_arr, scenario_key)
        rng = np.random.default_rng(seed + offset)
        paths, final_assets, checkpoint_assets = _simulate_paths(pool, current_values_arr, context_drift, horizon_days, n_sims, rng)
        percentiles = _path_percentiles(paths, current_value)
        scenarios[scenario_key] = {
            "key": scenario_key,
            "label": meta["label"],
            "description": meta["description"],
            "source_blocks": len(pool),
            "path_percentiles": percentiles,
            "horizons": _horizon_stats(paths, current_value, horizon_days),
            "currency_horizons": _currency_horizon_stats(checkpoint_assets, current_values_arr, active_holdings),
            "thresholds": [
                _threshold_stats(paths, current_value, 5.0),
                _threshold_stats(paths, current_value, 10.0),
                _threshold_stats(paths, current_value, -5.0),
                _threshold_stats(paths, current_value, -10.0),
            ],
            "max_drawdown": _max_drawdown_stats(paths),
            "contributors": _contribution_stats(active_holdings, current_values_arr, final_assets),
        }

    forecast_dates = pd.bdate_range(
        start=pd.Timestamp(datetime.now(timezone.utc).date()),
        periods=horizon_days + 1,
    )

    return {
        "snapshot_time": datetime.now(timezone.utc).isoformat(),
        "history_days": int(len(returns)),
        "n_assets": len(active_holdings),
        "n_sims": int(n_sims),
        "block_size": int(block_size),
        "current_value": round(current_value, 2),
        "invested_value": round(invested_value, 2),
        "current_pnl_abs": round(current_value - invested_value, 2),
        "current_pnl_pct": round(((current_value / invested_value) - 1.0) * 100.0, 2) if invested_value > 0 else 0.0,
        "currency_context": currency_context,
        "currency_breakdown": currency_breakdown,
        "forecast_dates": [dt.strftime("%Y-%m-%d") for dt in forecast_dates],
        "weights": holdings_meta,
        "history_profile": history_profile,
        "scenarios": scenarios,
        "insights": _build_insights(scenarios, holdings_meta, current_value, history_profile),
        "notes": [
            "Symulacja używa blokowego bootstrapu z 2 lat dziennych stóp zwrotu portfela.",
            "Kwoty przykładowe są rozdzielone osobno dla walut otwartych pozycji; model nie przelicza ich do jednej waluty.",
            "Scenariusze różnią się pulą losowanych historycznych fragmentów, a nie ręcznie wpisanym mnożnikiem.",
            "Każda pozycja dostaje dodatkowy kontekst z historii podobnego setupu technicznego i z analizy rozwoju sytuacji od zakupu.",
        ],
    }