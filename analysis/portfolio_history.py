"""analysis/portfolio_history.py — Historical open/closed position timeline for the portfolio."""
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd

from analysis.portfolio_analysis import _resolve_live_price_snapshot
from data.fetcher import get_fx_rate, get_info, get_latest_price, get_ohlcv, get_ohlcv_4h
from data.portfolio import load_closed_positions


_HOURS_4H = 4


def _safe_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _parse_date(value: str, fallback: pd.Timestamp) -> pd.Timestamp:
    try:
        return pd.Timestamp(pd.to_datetime(value)).normalize()
    except Exception:
        return fallback


def _resolve_currency(position: dict, ticker: str) -> str:
    currency = str(position.get("currency") or "").strip().upper()
    if currency:
        return currency
    try:
        info = get_info(ticker)
        currency = str(info.get("currency") or "").strip().upper()
    except Exception:
        currency = ""
    return currency or "USD"


def _sum_pnl_by_currency(rows: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for row in rows:
        currency = str(row.get("currency") or "USD").strip().upper() or "USD"
        totals[currency] = totals.get(currency, 0.0) + float(row.get("pnl_abs") or 0.0)
    return {
        currency: round(value, 2)
        for currency, value in sorted(totals.items())
    }


def _merge_currency_totals(*groups: dict[str, float]) -> dict[str, float]:
    merged: dict[str, float] = {}
    for group in groups:
        for currency, value in (group or {}).items():
            merged[currency] = merged.get(currency, 0.0) + float(value)
    return {
        currency: round(value, 2)
        for currency, value in sorted(merged.items())
    }


def _history_period(start_date: pd.Timestamp, end_date: pd.Timestamp) -> str:
    span_days = max(1, int((end_date - start_date).days))
    if span_days <= 180:
        return "6mo"
    if span_days <= 370:
        return "1y"
    return "2y"


def _load_daily_close_series(ticker: str, start_date: pd.Timestamp, end_date: pd.Timestamp) -> pd.Series | None:
    df = get_ohlcv(ticker, period=_history_period(start_date, end_date))
    if df is None or df.empty or "Close" not in df.columns:
        return None

    closes = df["Close"].astype(float).copy()
    index = pd.DatetimeIndex(pd.to_datetime(closes.index))
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    index = index.normalize().as_unit("ns")
    closes.index = index

    start_norm = start_date.normalize() - pd.Timedelta(days=3)
    end_norm = end_date.normalize() + pd.Timedelta(days=3)
    sliced = closes.loc[(closes.index >= start_norm) & (closes.index <= end_norm)]
    if sliced.empty:
        return None
    return sliced[~sliced.index.duplicated(keep="last")].sort_index()


def _load_fx_series(base_currency: str, quote_currency: str, start_date: pd.Timestamp, end_date: pd.Timestamp) -> pd.Series | None:
    base = str(base_currency or "").strip().upper()
    quote = str(quote_currency or "").strip().upper()
    if not base or not quote:
        return None
    if base == quote:
        daily_index = pd.date_range(start=start_date.normalize(), end=end_date.normalize(), freq="D")
        if daily_index.empty:
            daily_index = pd.DatetimeIndex([start_date.normalize()])
        return pd.Series(1.0, index=daily_index.as_unit("ns"), dtype=float)

    direct = _load_daily_close_series(f"{base}{quote}=X", start_date, end_date)
    if direct is not None and not direct.empty:
        return direct.astype(float)

    inverse = _load_daily_close_series(f"{quote}{base}=X", start_date, end_date)
    if inverse is not None and not inverse.empty:
        inverse = inverse.replace(0.0, pd.NA).dropna()
        if not inverse.empty:
            return (1.0 / inverse.astype(float)).sort_index()

    fallback_rate = get_fx_rate(base, quote)
    if fallback_rate and fallback_rate > 0:
        return pd.Series(
            [float(fallback_rate)],
            index=pd.DatetimeIndex([start_date.normalize()]).as_unit("ns"),
            dtype=float,
        )
    return None


def _convert_amount_series_to_usd(series: pd.Series, currency: str) -> pd.Series:
    clean = series.astype(float).sort_index()
    base_currency = str(currency or "USD").strip().upper() or "USD"
    if base_currency == "USD":
        return clean

    fx_series = _load_fx_series(base_currency, "USD", clean.index.min(), clean.index.max())
    if fx_series is None or fx_series.empty:
        return clean

    fx_aligned = fx_series.reindex(clean.index).ffill().bfill()
    return (clean * fx_aligned.astype(float)).astype(float)


def _load_price_series(ticker: str, start_date: pd.Timestamp, end_date: pd.Timestamp) -> pd.Series | None:
    df = get_ohlcv_4h(ticker, period=_history_period(start_date, end_date))
    if df is None or df.empty or "Close" not in df.columns:
        return None

    closes = df["Close"].astype(float).copy()
    index = pd.DatetimeIndex(pd.to_datetime(closes.index))
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    index = index.as_unit("ns")
    closes.index = index

    sliced = closes.loc[(closes.index >= start_date) & (closes.index <= end_date)]
    if sliced.empty:
        return None
    return sliced.sort_index()


def _align_series(series: pd.Series, full_index: pd.DatetimeIndex) -> pd.Series:
    aligned = series.reindex(full_index).ffill().fillna(0.0)
    first_idx = series.index.min()
    if first_idx is not None:
        aligned.loc[aligned.index < first_idx] = 0.0
    return aligned


def _build_position_snapshot(position: dict, status: str, today: pd.Timestamp) -> dict | None:
    ticker = (position.get("ticker") or "").strip().upper()
    shares = _safe_float(position.get("shares"), 0.0)
    avg_price = _safe_float(position.get("avg_price"), 0.0)
    if not ticker or shares <= 0 or avg_price <= 0:
        return None
    currency = _resolve_currency(position, ticker)

    start_date = _parse_date(position.get("purchase_date") or position.get("added", ""), today)
    if status == "open":
        end_date = today
    else:
        end_seed = position.get("closed_date")
        end_date = _parse_date(end_seed or str(today.date()), today)
    if end_date < start_date:
        end_date = start_date

    price_series = _load_price_series(ticker, start_date, end_date)
    final_price = None
    if status == "closed":
        final_price = _safe_float(position.get("exit_price"), 0.0)
    else:
        final_price = _safe_float(_resolve_live_price_snapshot(position, ticker, force_refresh=True), 0.0)

    if price_series is None or price_series.empty:
        if final_price <= 0:
            return None
        price_series = pd.Series([final_price], index=pd.DatetimeIndex([end_date]).as_unit("ns"), dtype=float)
    elif final_price <= 0:
        final_price = float(price_series.iloc[-1])

    if status == "open":
        live_idx = max(end_date, today)
        end_date = live_idx
        price_series.loc[end_date] = final_price
    else:
        close_idx = max(end_date, price_series.index.max()) if not price_series.empty else end_date
        price_series.loc[close_idx] = final_price
        end_date = close_idx

    pnl_path = (price_series - avg_price) * shares
    pnl_path.loc[start_date] = 0.0
    pnl_path.loc[end_date] = (final_price - avg_price) * shares
    pnl_path = pnl_path[~pnl_path.index.duplicated(keep="last")].sort_index()

    carry_path = pnl_path.copy()
    if status == "closed" and end_date < today:
        carry_path.loc[today] = float(pnl_path.iloc[-1])
        carry_path = carry_path[~carry_path.index.duplicated(keep="last")].sort_index()

    pnl_path_usd = _convert_amount_series_to_usd(pnl_path, currency)
    carry_path_usd = _convert_amount_series_to_usd(carry_path, currency)

    final_pnl_abs_native = float((final_price - avg_price) * shares)
    final_pnl_abs = float(carry_path_usd.iloc[-1]) if not carry_path_usd.empty else final_pnl_abs_native
    final_pnl_pct = ((final_price - avg_price) / avg_price * 100.0) if avg_price > 0 else 0.0
    held_days = max(0, int((end_date - start_date).total_seconds() // 86400))
    held_bars = max(1, len(pnl_path_usd))

    return {
        "ticker": ticker,
        "currency": currency,
        "status": status,
        "shares": round(shares, 4),
        "avg_price": round(avg_price, 2),
        "final_price": round(final_price, 2),
        "purchase_date": start_date.strftime("%Y-%m-%d"),
        "close_date": end_date.strftime("%Y-%m-%d") if status == "closed" else "",
        "held_days": held_days,
        "held_bars": held_bars,
        "invested": round(avg_price * shares, 2),
        "pnl_abs_native": round(final_pnl_abs_native, 2),
        "pnl_abs": round(final_pnl_abs, 2),
        "pnl_pct": round(final_pnl_pct, 2),
        "path": pnl_path_usd,
        "carry_path": carry_path_usd,
    }


def build_portfolio_history(holdings: list[dict]) -> dict:
    today = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
    closed_positions = load_closed_positions()

    open_rows = []
    closed_rows = []
    open_paths = []
    closed_paths = []

    for holding in holdings:
        snapshot = _build_position_snapshot(holding, "open", today)
        if snapshot:
            open_rows.append(snapshot)
            open_paths.append(snapshot["path"])

    for holding in closed_positions:
        snapshot = _build_position_snapshot(holding, "closed", today)
        if snapshot:
            closed_rows.append(snapshot)
            closed_paths.append(snapshot["carry_path"])

    if not open_rows and not closed_rows:
        raise ValueError("Brak danych do zbudowania historii pozycji.")

    currencies = sorted({str(row.get("currency") or "USD").strip().upper() or "USD" for row in (open_rows + closed_rows)})
    multi_currency = len(currencies) > 1

    all_series = open_paths + closed_paths
    full_index = pd.DatetimeIndex(sorted(set().union(*[series.index for series in all_series])))
    if full_index.empty:
        raise ValueError("Brak wystarczających danych historycznych dla historii pozycji.")

    open_total = pd.Series(0.0, index=full_index)
    realized_total = pd.Series(0.0, index=full_index)
    for series in open_paths:
        open_total = open_total.add(_align_series(series, full_index), fill_value=0.0)
    for series in closed_paths:
        realized_total = realized_total.add(_align_series(series, full_index), fill_value=0.0)
    total_series = open_total + realized_total

    close_markers = []
    for row in closed_rows:
        close_dt = pd.Timestamp(row["close_date"])
        search_idx = total_series.index.searchsorted(close_dt, side="right") - 1
        if search_idx >= 0:
            marker_dt = total_series.index[search_idx]
            close_markers.append({
                "ticker": row["ticker"],
                "date": marker_dt.strftime("%Y-%m-%d %H:%M"),
                "value": round(float(total_series.iloc[search_idx]), 2),
                "pnl_abs": row["pnl_abs"],
            })

    open_rows.sort(key=lambda item: item["purchase_date"])
    closed_rows.sort(key=lambda item: item["close_date"], reverse=True)

    summary = {
        "open_count": len(open_rows),
        "closed_count": len(closed_rows),
        "multi_currency": multi_currency,
        "currencies": currencies,
        "base_currency": "USD",
        "fx_granularity": "D1",
        "open_pnl_abs": round(sum(row["pnl_abs"] for row in open_rows), 2),
        "closed_pnl_abs": round(sum(row["pnl_abs"] for row in closed_rows), 2),
        "total_pnl_abs": round(float(total_series.iloc[-1]), 2),
        "open_winners": sum(1 for row in open_rows if row["pnl_abs"] > 0),
        "closed_winners": sum(1 for row in closed_rows if row["pnl_abs"] > 0),
    }

    notes = []
    if multi_currency:
        notes.append("Portfel jest wielowalutowy, więc wszystkie ścieżki P/L są przeliczone do USD na bazie dziennych kursów FX (D1). Dzięki temu łączny wynik nie miesza już EUR, PLN i USD jako jednej liczby.")
    elif currencies and currencies[0] != "USD":
        notes.append(f"Pozycje są pokazane w USD po przeliczeniu z waluty {currencies[0]} na bazie dziennych kursów FX (D1).")
    if not closed_rows:
        notes.append("Brak zapisanych zamkniętych pozycji. Pozycje usuwane od tej wersji będą archiwizowane i pokażą się tutaj.")
    notes.append("Wykres jest liczony z notowań 4H od momentu zakupu każdej pozycji, a dla walut innych niż USD kwoty są przeliczane do USD po dziennym kursie FX.")
    notes.append("Zamknięte pozycje zostają jako zrealizowany wynik, a otwarte aktualizują się wraz z kolejnymi świecami 4H i bieżącą ceną końcową.")

    def _serialize_rows(rows: list[dict]) -> list[dict]:
        return [
            {
                key: value
                for key, value in row.items()
                if key not in {"path", "carry_path"}
            }
            for row in rows
        ]

    return {
        "snapshot_time": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "notes": notes,
        "chart": {
            "dates": [dt.strftime("%Y-%m-%d %H:%M") for dt in full_index],
            "total_pnl": total_series.round(2).tolist(),
            "open_pnl": open_total.round(2).tolist(),
            "realized_pnl": realized_total.round(2).tolist(),
            "close_markers": close_markers,
            "interval": "4H",
            "currency": "USD",
            "multi_currency": False,
            "source_currencies": currencies,
        },
        "open_positions": _serialize_rows(open_rows),
        "closed_positions": _serialize_rows(closed_rows),
    }