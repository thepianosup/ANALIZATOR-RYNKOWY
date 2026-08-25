"""analysis/portfolio_signal_quality.py -- Verify purchase-time signal quality for portfolio holdings."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from analysis.indicators import add_indicators
from analysis.portfolio_analysis import _backtest_from_entry, _entry_recommendation_signal, _resolve_live_price_snapshot
from analysis.setup_matching import build_pullback_setup_snapshot
from data.fetcher import get_info, get_latest_price, get_ohlcv_4h


_BARS_1W = 10
_BARS_1M = 44
_BARS_3M = 132
_HORIZONS = {
    "5d": {"bars": _BARS_1W, "label": "1 tydzien", "short": "1T"},
    "22d": {"bars": _BARS_1M, "label": "1 miesiac", "short": "1M"},
    "66d": {"bars": _BARS_3M, "label": "3 miesiace", "short": "3M"},
}


def _safe_float(value, default: float | None = None) -> float | None:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_date(value: str) -> pd.Timestamp | None:
    try:
        parsed = pd.Timestamp(pd.to_datetime(value)).normalize()
        return parsed.tz_localize(None) if getattr(parsed, "tzinfo", None) is not None else parsed
    except Exception:
        return None


def _normalize_index(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    index = pd.DatetimeIndex(pd.to_datetime(out.index))
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    out.index = index
    return out.sort_index()


def _prediction_direction(avg_return: float | None, win_rate: float | None) -> int | None:
    if avg_return is None:
        return None
    if avg_return >= 1.0 and (win_rate is None or win_rate >= 52.0):
        return 1
    if avg_return <= -1.0 and win_rate is not None and win_rate < 48.0:
        return -1
    return 0


def _actual_direction(actual_return: float | None) -> int | None:
    if actual_return is None:
        return None
    if actual_return >= 0.75:
        return 1
    if actual_return <= -0.75:
        return -1
    return 0


def _verification_status(predicted_avg: float | None, predicted_win: float | None, actual_return: float | None) -> str:
    if predicted_avg is None:
        return "no_data"
    if actual_return is None:
        return "pending"

    predicted_direction = _prediction_direction(predicted_avg, predicted_win)
    actual_direction = _actual_direction(actual_return)
    if predicted_direction is None:
        return "no_data"
    if predicted_direction == 0:
        return "neutral"
    if actual_direction == predicted_direction:
        return "matched"
    return "missed"


def _purchase_setup_snapshot(df: pd.DataFrame, purchase_idx: int):
    row = df.iloc[purchase_idx]
    price = _safe_float(row.get("Close"))
    sma50 = _safe_float(row.get("SMA50"))
    sma200 = _safe_float(row.get("SMA200"))
    rsi14 = _safe_float(row.get("RSI14"))
    stoch_k = _safe_float(row.get("Stoch_K"))
    roc20 = _safe_float(row.get("ROC20"))
    adx14 = _safe_float(row.get("ADX14"))
    willi_r = _safe_float(row.get("WilliamsR"))
    vol50 = _safe_float(row.get("Vol_SMA50"))

    if price is None or sma50 is None or sma200 is None or rsi14 is None:
        return None

    high_window = df["High"].astype(float).iloc[max(0, purchase_idx - 499) : purchase_idx + 1]
    rolling_high = float(high_window.max()) if not high_window.empty else price
    drawdown = (rolling_high - price) / rolling_high if rolling_high > 0 else 0.0
    recent_volume = float(df["Volume"].astype(float).iloc[max(0, purchase_idx - 9) : purchase_idx + 1].mean())
    vol_ratio = recent_volume / vol50 if vol50 and vol50 > 0 else 0.0

    return build_pullback_setup_snapshot(
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


def _actual_path_pct(closes: pd.Series, purchase_idx: int, avg_price: float, current_price: float) -> list[float]:
    if avg_price <= 0:
        return [0.0]

    path = [0.0]
    tail = closes.iloc[purchase_idx + 1 :].astype(float).tolist()
    if tail:
        tail[-1] = current_price
    else:
        path.append(round((current_price - avg_price) / avg_price * 100.0, 2))
        return path

    for value in tail:
        path.append(round((float(value) - avg_price) / avg_price * 100.0, 2))
    return path


def _verification_row(holding: dict) -> dict | None:
    ticker = str(holding.get("ticker") or "").upper()
    if not ticker:
        return None

    avg_price = _safe_float(holding.get("avg_price"), 0.0) or 0.0
    shares = _safe_float(holding.get("shares"), 0.0) or 0.0
    purchase_date = holding.get("purchase_date") or holding.get("added") or ""
    purchase_dt = _parse_date(str(purchase_date))
    if purchase_dt is None or avg_price <= 0 or shares <= 0:
        return None

    df = get_ohlcv_4h(ticker, period="2y")
    if df is None or df.empty or len(df) < 40:
        return None

    df = _normalize_index(add_indicators(df))
    purchase_idx = int(df.index.searchsorted(purchase_dt, side="left"))
    if purchase_idx >= len(df):
        purchase_idx = len(df) - 1
    if purchase_idx < 0:
        return None

    purchase_setup = _purchase_setup_snapshot(df, purchase_idx)
    purchase_frame = df.iloc[: purchase_idx + 1].copy()
    entry_backtest = _backtest_from_entry(purchase_frame, purchase_setup) if purchase_setup is not None else {}
    signal = _entry_recommendation_signal(entry_backtest, min_samples=1)
    signal["low_sample"] = int(signal.get("samples") or 0) < 6

    closes = df["Close"].astype(float)
    current_price = _safe_float(_resolve_live_price_snapshot(holding, ticker, force_refresh=True), closes.iloc[-1]) or float(closes.iloc[-1])
    current_pnl_pct = ((current_price - avg_price) / avg_price * 100.0) if avg_price > 0 else 0.0
    current_pnl_abs = (current_price - avg_price) * shares
    bars_since_purchase = max(0, len(df) - purchase_idx - 1)
    days_held = max(0, int((df.index[-1] - purchase_dt).total_seconds() // 86400))

    verification = {}
    for suffix, meta in _HORIZONS.items():
        bars = meta["bars"]
        predicted_avg = _safe_float(entry_backtest.get(f"avg_return_{suffix}"))
        predicted_median = _safe_float(entry_backtest.get(f"median_return_{suffix}"))
        predicted_win = _safe_float(entry_backtest.get(f"win_rate_{suffix}"))
        samples = int(entry_backtest.get(f"n_setups_{suffix}") or entry_backtest.get("n_samples") or 0)
        actual_return = None
        actual_price = None
        if purchase_idx + bars < len(closes):
            actual_price = float(closes.iloc[purchase_idx + bars])
            actual_return = round((actual_price - avg_price) / avg_price * 100.0, 2)

        verification[suffix] = {
            "label": meta["label"],
            "short": meta["short"],
            "bars": bars,
            "predicted_avg_return": round(predicted_avg, 2) if predicted_avg is not None else None,
            "predicted_median_return": round(predicted_median, 2) if predicted_median is not None else None,
            "predicted_win_rate": round(predicted_win, 1) if predicted_win is not None else None,
            "samples": samples,
            "actual_return": actual_return,
            "actual_price": round(actual_price, 2) if actual_price is not None else None,
            "status": _verification_status(predicted_avg, predicted_win, actual_return),
        }

    try:
        info = get_info(ticker)
        name = str(info.get("name") or ticker)
        currency = str(info.get("currency") or holding.get("currency") or "USD").upper() or "USD"
    except Exception:
        name = ticker
        currency = str(holding.get("currency") or "USD").upper() or "USD"

    return {
        "ticker": ticker,
        "name": name,
        "currency": currency,
        "purchase_date": purchase_dt.strftime("%Y-%m-%d"),
        "avg_price": round(avg_price, 2),
        "shares": round(shares, 4),
        "current_price": round(current_price, 2),
        "current_pnl_pct": round(current_pnl_pct, 2),
        "current_pnl_abs": round(current_pnl_abs, 2),
        "days_held": days_held,
        "bars_since_purchase": bars_since_purchase,
        "entry_backtest": entry_backtest,
        "signal": signal,
        "verification": verification,
        "actual_path_pct": _actual_path_pct(closes, purchase_idx, avg_price, current_price),
    }


def build_portfolio_signal_quality(holdings: list[dict]) -> dict:
    rows = []
    for holding in holdings:
        row = _verification_row(holding)
        if row is not None:
            rows.append(row)

    rows.sort(key=lambda item: abs(float(item.get("current_pnl_abs") or 0.0)), reverse=True)

    summary = {
        "positions": len(rows),
        "current_winners": sum(1 for row in rows if float(row.get("current_pnl_pct") or 0.0) > 0),
        "current_losers": sum(1 for row in rows if float(row.get("current_pnl_pct") or 0.0) < 0),
        "current_flat": sum(1 for row in rows if abs(float(row.get("current_pnl_pct") or 0.0)) < 0.01),
        "horizons": {},
    }

    for suffix in _HORIZONS:
        matched = 0
        missed = 0
        neutral = 0
        pending = 0
        no_data = 0
        for row in rows:
            status = (((row.get("verification") or {}).get(suffix) or {}).get("status")) or "no_data"
            if status == "matched":
                matched += 1
            elif status == "missed":
                missed += 1
            elif status == "neutral":
                neutral += 1
            elif status == "pending":
                pending += 1
            else:
                no_data += 1

        evaluated = matched + missed
        summary["horizons"][suffix] = {
            "label": _HORIZONS[suffix]["label"],
            "matched": matched,
            "missed": missed,
            "neutral": neutral,
            "pending": pending,
            "no_data": no_data,
            "evaluated": evaluated,
            "accuracy": round(matched / evaluated * 100.0, 1) if evaluated > 0 else None,
        }

    notes = [
        "To nie jest zapisany oryginalny badge z dnia zakupu; widok odtwarza setup wejscia na podstawie historycznych swiec 4H.",
        "Jesli podobnych wejsc jest malo, karta dalej pokazuje kierunek, ale oznacza go jako mala probe.",
        "Dla 1T, 1M i 3M porownuje sie historyczna szanse wzrostu i sredni oczekiwany ruch z tym, co faktycznie wydarzylo sie po zakupie.",
        "Jesli pozycja jest zbyt mloda dla danego horyzontu, status pozostaje w toku.",
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "notes": notes,
        "positions": rows,
    }