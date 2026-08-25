"""Region-aware macro regime snapshot for US, EU, and Polish assets."""
import sys
import os

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import config
from data import cache
from data.fetcher import get_info, get_ohlcv

_EU_SUFFIXES = (
    ".AS", ".AT", ".BC", ".BE", ".BR", ".CO", ".DE", ".DU", ".F",
    ".HE", ".IR", ".L", ".LS", ".MC", ".MI", ".PA", ".SG", ".ST",
    ".SW", ".VI", ".WA",
)
_EU_EXCHANGES = (
    "XETRA", "EURONEXT", "MILAN", "PARIS", "AMSTERDAM", "BRUSSELS",
    "MADRID", "STOCKHOLM", "HELSINKI", "VIENNA", "LONDON", "NASDAQ OMX",
)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _tone_color(score: int) -> str:
    if score >= config.MACRO_SUPPORT_THRESHOLD:
        return config.COLOR_UP
    if score <= config.MACRO_HEADWIND_THRESHOLD:
        return config.COLOR_DOWN
    return config.COLOR_ACCENT


def _stance_for_score(score: int) -> str:
    if score >= 72:
        return "Wyraźnie wspierające"
    if score >= config.MACRO_SUPPORT_THRESHOLD:
        return "Lekko wspierające"
    if score <= 28:
        return "Wyraźny headwind"
    if score <= config.MACRO_HEADWIND_THRESHOLD:
        return "Hamujące"
    return "Neutralne"


def _bias_for_score(score: int) -> int:
    if score >= config.MACRO_SUPPORT_THRESHOLD:
        return 1
    if score <= config.MACRO_HEADWIND_THRESHOLD:
        return -1
    return 0


def detect_market_region(ticker: str, info: dict | None = None) -> str:
    info = info or {}
    ticker_upper = (ticker or "").upper()
    currency = (info.get("currency") or "").upper()
    exchange = (info.get("exchange") or "").upper()

    if ticker_upper.endswith(".WA") or currency == "PLN" or any(token in exchange for token in ("WARSAW", "WSE")):
        return "PL"
    if currency == "EUR" or ticker_upper.endswith(_EU_SUFFIXES) or any(token in exchange for token in _EU_EXCHANGES):
        return "EU"
    return "US"


def _pct_return(series, bars: int) -> float | None:
    if series is None or len(series) < 2:
        return None
    bars = min(bars, len(series) - 1)
    if bars <= 0:
        return None
    base = float(series.iloc[-bars - 1])
    last = float(series.iloc[-1])
    if base <= 0:
        return None
    return (last - base) / base * 100.0


def _series_snapshot(symbol: str, period: str = "1y") -> dict | None:
    df = get_ohlcv(symbol, period=period)
    if df is None or df.empty or len(df) < 40:
        return None

    closes = df["Close"].astype(float)
    close = float(closes.iloc[-1])
    sma50 = float(closes.rolling(50).mean().iloc[-1]) if len(closes) >= 50 else None
    sma200 = float(closes.rolling(200).mean().iloc[-1]) if len(closes) >= 200 else None
    return {
        "symbol": symbol,
        "close": close,
        "sma50": sma50,
        "sma200": sma200,
        "ret_1m": _pct_return(closes, 21),
        "ret_3m": _pct_return(closes, 63),
    }


def _neutral_component(key: str, label: str, signal: str) -> dict:
    return {
        "key": key,
        "label": label,
        "score": 50,
        "value": "Brak pełnych danych",
        "signal": signal,
        "color": config.COLOR_ACCENT,
    }


def _component(key: str, label: str, score: float, value: str, signal: str) -> dict:
    score_i = int(round(_clamp(score, 0, 100)))
    return {
        "key": key,
        "label": label,
        "score": score_i,
        "value": value,
        "signal": signal,
        "color": _tone_color(score_i),
    }


def _benchmark_trend_component(region_cfg: dict) -> dict:
    snap = _series_snapshot(region_cfg["benchmark"])
    if snap is None:
        return _neutral_component(
            "benchmark_trend",
            "Trend rynku bazowego",
            "Brak pełnej serii benchmarku dla otoczenia makro.",
        )

    score = 50.0
    messages = []
    close = snap["close"]
    sma50 = snap["sma50"]
    sma200 = snap["sma200"]

    if sma200 is not None:
        if close >= sma200:
            score += 24
            messages.append("benchmark utrzymuje się nad SMA200")
        else:
            score -= 24
            messages.append("benchmark spadł poniżej SMA200")
    if sma50 is not None:
        if close >= sma50:
            score += 10
            messages.append("krótkoterminowy trend pozostaje dodatni")
        else:
            score -= 10
            messages.append("krótkoterminowo rynek jest słabszy")

    sma50_str = f"{sma50:.1f}" if sma50 is not None else "—"
    sma200_str = f"{sma200:.1f}" if sma200 is not None else "—"
    value = f"{region_cfg['benchmark_name']}: {close:.1f} | SMA50 {sma50_str} | SMA200 {sma200_str}"
    signal = ". ".join(messages).capitalize() + "." if messages else "Brak wyraźnego trendu benchmarku."
    return _component("benchmark_trend", "Trend rynku bazowego", score, value, signal)


def _benchmark_momentum_component(region_cfg: dict) -> dict:
    snap = _series_snapshot(region_cfg["benchmark"])
    if snap is None:
        return _neutral_component(
            "benchmark_momentum",
            "Momentum rynku",
            "Brak pełnego momentum dla benchmarku regionu.",
        )

    ret_1m = snap["ret_1m"] or 0.0
    ret_3m = snap["ret_3m"] or 0.0
    score = 50.0 + _clamp(ret_1m * 1.5, -14, 14) + _clamp(ret_3m * 1.4, -22, 22)

    if ret_3m >= 8:
        signal = "Rynek ma solidnie dodatnie momentum w ostatnim kwartale."
    elif ret_3m >= 2:
        signal = "Momentum rynku jest dodatnie, ale bez euforii."
    elif ret_3m <= -8:
        signal = "Momentum kwartalne wyraźnie ciąży akcjom w tym regionie."
    elif ret_3m <= -2:
        signal = "Momentum rynku jest słabe i nie wspiera agresywnych wejść."
    else:
        signal = "Momentum rynku pozostaje mieszane."

    value = f"1M {ret_1m:+.1f}% | 3M {ret_3m:+.1f}%"
    return _component("benchmark_momentum", "Momentum rynku", score, value, signal)


def _risk_component() -> dict:
    snap = _series_snapshot("^VIX")
    if snap is None:
        return _neutral_component(
            "risk",
            "Globalny sentyment ryzyka",
            "Brak bieżącego odczytu VIX.",
        )

    level = snap["close"]
    ret_1m = snap["ret_1m"] or 0.0
    score = 88.0
    score -= _clamp(max(level - 14.0, 0.0) * 4.1, 0, 60)
    score -= _clamp(max(ret_1m, 0.0) * 1.3, 0, 16)
    if level < 18 and ret_1m <= 0:
        score += 4

    if level <= 18:
        signal = "VIX pozostaje niski, więc globalna awersja do ryzyka nie dominuje."
    elif level <= 24:
        signal = "VIX jest umiarkowany, więc rynek akcji działa w normalnym stresie."
    elif level <= 30:
        signal = "Podwyższony VIX zwiększa ryzyko gwałtownych ruchów i słabszych wejść."
    else:
        signal = "Wysoki VIX oznacza wyraźny reżim risk-off."

    value = f"VIX {level:.2f} | 1M {ret_1m:+.1f}%"
    return _component("risk", "Globalny sentyment ryzyka", score, value, signal)


def _us_conditions_component() -> dict:
    short_snap = _series_snapshot("^IRX")
    long_snap = _series_snapshot("^TNX")
    if short_snap is None or long_snap is None:
        return _neutral_component(
            "conditions",
            "Warunki finansowe",
            "Brak pełnego odczytu rentowności USA.",
        )

    short_rate = short_snap["close"]
    long_rate = long_snap["close"]
    spread = long_rate - short_rate
    score = 55.0

    if spread >= 0.8:
        score += 22
    elif spread >= 0.2:
        score += 12
    elif spread >= -0.3:
        score -= 4
    else:
        score -= 20

    if short_rate <= 4.0:
        score += 15
    elif short_rate <= 5.0:
        score += 6
    elif short_rate <= 5.5:
        score -= 5
    else:
        score -= 12

    if spread >= 0.2:
        signal = "Krzywa rentowności nie blokuje ryzyka tak mocno jak w fazie głębokiego odwrócenia."
    else:
        signal = "Płaska lub odwrócona krzywa rentowności ogranicza agresywne nastawienie do akcji."

    value = f"3M {short_rate:.2f}% | 10Y {long_rate:.2f}% | spread {spread:+.2f} pp"
    return _component("conditions", "Warunki finansowe", score, value, signal)


def _fetch_ecb_series(series_key: str) -> list[tuple[str, float]]:
    cache_key = f"macro:ecb:{series_key}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    url = f"https://data-api.ecb.europa.eu/service/data/{series_key}?format=jsondata"
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    payload = response.json()

    datasets = payload.get("dataSets") or []
    if not datasets:
        return []
    series_map = datasets[0].get("series") or {}
    if not series_map:
        return []

    series = next(iter(series_map.values()))
    observations = series.get("observations") or {}
    dimension_values = payload["structure"]["dimensions"]["observation"][0]["values"]

    points = []
    for idx_str, obs_value in observations.items():
        try:
            idx = int(str(idx_str).split(":")[0])
            value = obs_value[0]
        except (TypeError, ValueError, IndexError):
            continue
        if value is None or idx >= len(dimension_values):
            continue
        points.append((dimension_values[idx]["id"], float(value)))

    points.sort(key=lambda item: item[0])
    cache.set(cache_key, points, config.MACRO_CACHE_TTL_SECONDS)
    return points


def _eu_conditions_component(region_cfg: dict) -> dict:
    try:
        points = _fetch_ecb_series(region_cfg["ecb_series"])
    except Exception:
        points = []

    if not points:
        return _neutral_component(
            "conditions",
            "Warunki finansowe",
            "Brak świeżego odczytu stopy EUR 3M z ECB.",
        )

    level = points[-1][1]
    ref_level = points[-126][1] if len(points) > 126 else points[0][1]
    delta_6m = level - ref_level
    score = 60.0

    if level <= 2.5:
        score += 18
    elif level <= 3.0:
        score += 10
    elif level <= 3.5:
        score += 0
    else:
        score -= 14

    if delta_6m <= -0.35:
        score += 10
    elif delta_6m >= 0.35:
        score -= 10

    if level <= 3.0 and delta_6m <= 0:
        signal = "Koszt pieniądza w EUR nie zacieśnia dalej warunków finansowych dla akcji."
    elif level > 3.5 or delta_6m > 0.35:
        signal = "Wyższa stopa EUR 3M oznacza bardziej restrykcyjne otoczenie dla ryzyka."
    else:
        signal = "Warunki finansowe w EUR są umiarkowane i bez skrajności."

    value = f"{region_cfg['conditions_name']} {level:.2f}% | 6M Δ {delta_6m:+.2f} pp"
    return _component("conditions", "Warunki finansowe", score, value, signal)


def _pl_conditions_component(region_cfg: dict) -> dict:
    snap = _series_snapshot(region_cfg["fx_pair"])
    if snap is None:
        return _neutral_component(
            "conditions",
            "Presja walutowa",
            "Brak świeżego odczytu USD/PLN.",
        )

    level = snap["close"]
    ret_3m = snap["ret_3m"] or 0.0
    score = 60.0

    if level <= 3.80:
        score += 12
    elif level <= 4.10:
        score += 3
    elif level <= 4.40:
        score -= 8
    else:
        score -= 18

    if ret_3m <= -3.0:
        score += 14
    elif ret_3m <= 0:
        score += 6
    elif ret_3m >= 5.0:
        score -= 18
    elif ret_3m >= 2.0:
        score -= 8

    if ret_3m <= 0:
        signal = "Stabilny lub mocniejszy PLN zmniejsza presję na wyceny i importowaną inflację."
    else:
        signal = "Słabszy PLN pogarsza lokalne warunki makro i zwykle ciąży sentymentowi."

    value = f"{region_cfg['conditions_name']} {level:.3f} | 3M {ret_3m:+.1f}%"
    return _component("conditions", "Presja walutowa", score, value, signal)


def _build_summary(region_label: str, stance: str, components: list[dict]) -> str:
    positives = [c["label"] for c in components if c["score"] >= config.MACRO_SUPPORT_THRESHOLD]
    negatives = [c["label"] for c in components if c["score"] <= config.MACRO_HEADWIND_THRESHOLD]

    summary = f"Otoczenie makro dla regionu {region_label} jest obecnie {stance.lower()} dla akcji."
    if positives:
        summary += f" Wspierają głównie: {', '.join(positives[:2])}."
    if negatives:
        summary += f" Ciążą głównie: {', '.join(negatives[:2])}."
    return summary


def _build_region_snapshot(region: str) -> dict:
    region_cfg = config.MACRO_REGION_PROXIES.get(region, config.MACRO_REGION_PROXIES["US"])
    components = [
        _benchmark_trend_component(region_cfg),
        _benchmark_momentum_component(region_cfg),
        _risk_component(),
    ]

    if region == "EU":
        components.append(_eu_conditions_component(region_cfg))
    elif region == "PL":
        components.append(_pl_conditions_component(region_cfg))
    else:
        components.append(_us_conditions_component())

    score = int(round(sum(component["score"] for component in components) / len(components)))
    stance = _stance_for_score(score)
    bias = _bias_for_score(score)
    return {
        "region": region,
        "region_label": region_cfg["label"],
        "score": score,
        "stance": stance,
        "color": _tone_color(score),
        "opportunity_adjustment": bias,
        "portfolio_bias": bias,
        "summary": _build_summary(region_cfg["label"], stance, components),
        "components": components,
    }


def _get_region_snapshot(region: str) -> dict:
    cache_key = f"macro:region:{region}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    snapshot = _build_region_snapshot(region)
    cache.set(cache_key, snapshot, config.MACRO_CACHE_TTL_SECONDS)
    return snapshot


def get_macro_snapshot(ticker: str | None = None, info: dict | None = None, region: str | None = None) -> dict:
    resolved_info = dict(info or {})
    if ticker and not resolved_info:
        resolved_info = get_info(ticker)

    resolved_region = region or detect_market_region(ticker or "", resolved_info)
    snapshot = dict(_get_region_snapshot(resolved_region))
    snapshot["region"] = resolved_region
    snapshot["region_label"] = config.MACRO_REGION_PROXIES.get(resolved_region, config.MACRO_REGION_PROXIES["US"])["label"]
    return snapshot