"""analysis/company_analysis.py — Company-specific fundamentals and recent events bias."""
import sys
import os
import re
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import config
from data.fetcher import get_company_news, get_info


_NAME_STOPWORDS = {
    "inc", "corp", "corporation", "company", "co", "group", "holding", "holdings",
    "sa", "spa", "plc", "nv", "ag", "bank", "limited", "ltd", "class",
}

_POSITIVE_EVENT_KEYWORDS = (
    "beat", "beats", "raised guidance", "guidance raised", "buyback", "repurchase",
    "dividend hike", "dividend increase", "contract", "wins", "approval", "approved",
    "launch", "partnership", "acquisition", "record", "surge", "upgraded",
)

_NEGATIVE_EVENT_KEYWORDS = (
    "miss", "missed", "cuts guidance", "guidance cut", "warning", "downgrade",
    "lawsuit", "probe", "investigation", "delay", "recall", "layoffs", "resigns",
    "resignation", "antitrust", "plunge", "slump", "cuts forecast", "weak demand",
)


def _safe_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _score_color(score: float) -> str:
    if score >= config.COMPANY_SUPPORT_THRESHOLD:
        return config.COLOR_UP
    if score <= config.COMPANY_HEADWIND_THRESHOLD:
        return config.COLOR_DOWN
    return "#f97316"


def _bias_for_score(score: float) -> int:
    if score >= config.COMPANY_SUPPORT_THRESHOLD:
        return 1
    if score <= config.COMPANY_HEADWIND_THRESHOLD:
        return -1
    return 0


def _weighted_average(parts: list[tuple[float, float]]) -> float:
    valid = [(score, weight) for score, weight in parts if score is not None and weight > 0]
    if not valid:
        return 50.0
    total_weight = sum(weight for _, weight in valid)
    if total_weight <= 0:
        return 50.0
    weighted = sum(score * weight for score, weight in valid)
    return weighted / total_weight


def _fmt_pct(value) -> str:
    num = _safe_float(value)
    if num is None:
        return "brak"
    return f"{num * 100:+.1f}%"


def _fmt_num(value, decimals: int = 2) -> str:
    num = _safe_float(value)
    if num is None:
        return "brak"
    return f"{num:.{decimals}f}"


def _clean_name_tokens(name: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z0-9]{3,}", (name or "").lower())
    out = []
    for token in tokens:
        if token in _NAME_STOPWORDS:
            continue
        if len(token) < 4:
            continue
        if token not in out:
            out.append(token)
    return out[:4]


def _is_equity(info: dict) -> bool:
    quote_type = str(info.get("quote_type") or "").upper()
    return quote_type == "EQUITY"


def _component(label: str, score: float, value: str, signal: str) -> dict:
    safe_score = round(_clamp(score), 1)
    return {
        "label": label,
        "score": safe_score,
        "value": value,
        "signal": signal,
        "color": _score_color(safe_score),
    }


def _score_growth(info: dict) -> dict:
    revenue_growth = _safe_float(info.get("revenue_growth"))
    earnings_growth = _safe_float(info.get("earnings_growth"))
    score = 50.0
    signals = []

    if revenue_growth is not None:
        if revenue_growth >= 0.15:
            score += 18
            signals.append("przychody rosną mocno")
        elif revenue_growth >= 0.05:
            score += 10
            signals.append("przychody rosną")
        elif revenue_growth < -0.10:
            score -= 18
            signals.append("przychody wyraźnie spadają")
        elif revenue_growth < 0:
            score -= 8
            signals.append("przychody lekko spadają")

    if earnings_growth is not None:
        if earnings_growth >= 0.15:
            score += 20
            signals.append("zysk rośnie szybko")
        elif earnings_growth >= 0.05:
            score += 10
            signals.append("zysk rośnie")
        elif earnings_growth < -0.10:
            score -= 20
            signals.append("zysk wyraźnie spada")
        elif earnings_growth < 0:
            score -= 10
            signals.append("zysk lekko spada")

    signal = "; ".join(signals) if signals else "Brak jednoznacznego trendu wzrostu w dostępnych danych."
    value = f"Przychody {_fmt_pct(revenue_growth)} · Zysk {_fmt_pct(earnings_growth)}"
    return _component("Wzrost", score, value, signal)


def _score_quality(info: dict) -> dict:
    profit_margin = _safe_float(info.get("profit_margin"))
    operating_margin = _safe_float(info.get("operating_margin"))
    free_cashflow = _safe_float(info.get("free_cashflow"))
    operating_cashflow = _safe_float(info.get("operating_cashflow"))
    score = 50.0
    signals = []

    if profit_margin is not None:
        if profit_margin >= 0.20:
            score += 16
            signals.append("wysoka marża netto")
        elif profit_margin >= 0.08:
            score += 8
            signals.append("zdrowa marża netto")
        elif profit_margin < 0:
            score -= 18
            signals.append("ujemna marża netto")

    if operating_margin is not None:
        if operating_margin >= 0.20:
            score += 12
            signals.append("silna marża operacyjna")
        elif operating_margin < 0.05:
            score -= 8
            signals.append("słaba marża operacyjna")

    if free_cashflow is not None:
        if free_cashflow > 0:
            score += 10
            signals.append("dodatni free cash flow")
        else:
            score -= 12
            signals.append("ujemny free cash flow")
    elif operating_cashflow is not None:
        if operating_cashflow > 0:
            score += 6
            signals.append("dodatnie przepływy operacyjne")
        else:
            score -= 8
            signals.append("ujemne przepływy operacyjne")

    signal = "; ".join(signals) if signals else "Rentowność i przepływy są niejednoznaczne lub niekompletne."
    value = f"Marża netto {_fmt_pct(profit_margin)} · Operacyjna {_fmt_pct(operating_margin)}"
    return _component("Rentowność", score, value, signal)


def _score_balance_sheet(info: dict) -> dict:
    debt_to_equity = _safe_float(info.get("debt_to_equity"))
    current_ratio = _safe_float(info.get("current_ratio"))
    quick_ratio = _safe_float(info.get("quick_ratio"))
    roe = _safe_float(info.get("roe"))
    score = 50.0
    signals = []

    if debt_to_equity is not None:
        if debt_to_equity <= 60:
            score += 12
            signals.append("zadłużenie wygląda bezpiecznie")
        elif debt_to_equity >= 180:
            score -= 16
            signals.append("zadłużenie jest wysokie")

    liquidity = quick_ratio if quick_ratio is not None else current_ratio
    if liquidity is not None:
        if liquidity >= 1.0:
            score += 8
            signals.append("płynność jest bezpieczna")
        elif liquidity < 0.8:
            score -= 8
            signals.append("płynność jest napięta")

    if roe is not None:
        if roe >= 0.18:
            score += 14
            signals.append("wysoki zwrot na kapitale")
        elif roe < 0.08:
            score -= 8
            signals.append("niski zwrot na kapitale")

    signal = "; ".join(signals) if signals else "Bilans nie daje wyraźnej przewagi ani ostrzeżenia."
    value = f"D/E {_fmt_num(debt_to_equity, 1)} · Quick {_fmt_num(quick_ratio)} · ROE {_fmt_pct(roe)}"
    return _component("Bilans", score, value, signal)


def _score_market_view(info: dict, price: float | None) -> dict:
    pe_ratio = _safe_float(info.get("pe_ratio"))
    forward_pe = _safe_float(info.get("forward_pe"))
    recommendation_key = str(info.get("recommendation_key") or "").lower()
    target_mean_price = _safe_float(info.get("target_mean_price"))
    analyst_count = _safe_float(info.get("analyst_count"))
    score = 50.0
    signals = []

    if pe_ratio is not None and forward_pe is not None:
        if forward_pe < pe_ratio * 0.9:
            score += 10
            signals.append("forward P/E spada względem trailing P/E")
        elif forward_pe > pe_ratio * 1.1:
            score -= 8
            signals.append("forward P/E nie poprawia obrazu wyceny")

    if recommendation_key in {"strong_buy", "buy"}:
        score += 12
        signals.append("analitycy są pozytywnie nastawieni")
    elif recommendation_key in {"underperform", "sell", "strong_sell"}:
        score -= 14
        signals.append("analitycy są negatywnie nastawieni")

    if price and target_mean_price:
        upside = (target_mean_price - price) / price
        if analyst_count and analyst_count >= 5:
            if upside >= 0.12:
                score += 12
                signals.append("średni target daje sensowny upside")
            elif upside <= -0.08:
                score -= 12
                signals.append("średni target sugeruje ograniczony upside")

    signal = "; ".join(signals) if signals else "Rynek i wycena nie dają mocnego dodatkowego sygnału."
    value = f"P/E {_fmt_num(pe_ratio, 1)} · fwd P/E {_fmt_num(forward_pe, 1)} · konsensus {recommendation_key or 'brak'}"
    return _component("Oczekiwania rynku", score, value, signal)


def _is_relevant_news(text: str, ticker: str, company_name: str) -> bool:
    lowered = f" {text.lower()} "
    ticker_token = ticker.split(".")[0].lower()
    if ticker_token and f" {ticker_token} " in lowered:
        return True
    for token in _clean_name_tokens(company_name):
        if token in lowered:
            return True
    return False


def _event_sentiment(text: str) -> int:
    lowered = text.lower()
    score = 0
    for keyword in _POSITIVE_EVENT_KEYWORDS:
        if keyword in lowered:
            score += 1
    for keyword in _NEGATIVE_EVENT_KEYWORDS:
        if keyword in lowered:
            score -= 1
    return score


def _score_recent_events(ticker: str, info: dict) -> tuple[dict, list[dict]]:
    company_name = info.get("name", ticker)
    lookback = timedelta(days=config.COMPANY_NEWS_LOOKBACK_DAYS)
    now = datetime.now(timezone.utc)
    recent_events = []
    raw_news = get_company_news(ticker, limit=config.COMPANY_NEWS_LIMIT)
    event_total = 0

    for item in raw_news:
        published_at = item.get("published_at") or ""
        published_dt = None
        if published_at:
            try:
                published_dt = datetime.fromisoformat(published_at)
            except ValueError:
                published_dt = None
        if published_dt and now - published_dt > lookback:
            continue

        title = item.get("title", "")
        summary = item.get("summary", "")
        text = f"{title} {summary}".strip()
        if not text:
            continue
        if not _is_relevant_news(text, ticker, company_name):
            continue

        sentiment = _event_sentiment(text)
        if sentiment == 0:
            continue

        event_total += sentiment
        recent_events.append({
            "title": title,
            "provider": item.get("provider", "Yahoo Finance"),
            "published_at": published_at,
            "sentiment": "Pozytywne" if sentiment > 0 else "Negatywne",
            "score": 1 if sentiment > 0 else -1,
            "color": config.COLOR_UP if sentiment > 0 else config.COLOR_DOWN,
        })
        if len(recent_events) >= 3:
            break

    score = 50.0 + max(-30, min(30, event_total * 12))
    if recent_events:
        signal = "W ostatnich nagłówkach widać pozytywny newsflow." if event_total > 0 else "W ostatnich nagłówkach widać ryzyka dla spółki."
        value = f"{len(recent_events)} istotne nagłówki · bilans {event_total:+d}"
    else:
        signal = "Brak jednoznacznych, świeżych wydarzeń dla tej spółki w dostępnych nagłówkach."
        value = "Brak czytelnego event bias"

    return _component("Wydarzenia", score, value, signal), recent_events


def _neutral_snapshot(ticker: str, info: dict, reason: str) -> dict:
    base = _component("Spółka", 50, str(info.get("quote_type") or "instrument"), reason)
    events = _component("Wydarzenia", 50, "Brak oceny", "Ta kategoria działa tylko dla pojedynczych spółek.")
    return {
        "ticker": ticker,
        "score": 50,
        "stance": "Neutralne",
        "color": _score_color(50),
        "opportunity_adjustment": 0,
        "portfolio_bias": 0,
        "financial_score": 50,
        "event_score": 50,
        "financial_bias": 0,
        "event_bias": 0,
        "summary": reason,
        "components": [base, events],
        "events": [],
        "is_equity": False,
    }


def get_company_snapshot(ticker: str, info: dict | None = None, price: float | None = None) -> dict:
    """
    Return a company-specific score based on fundamentals and recent events.
    Final bias mirrors macro semantics: -1 / 0 / +1.
    """
    info = info or get_info(ticker)
    if not _is_equity(info):
        return _neutral_snapshot(
            ticker,
            info,
            "Ocena spółkowa dotyczy tylko pojedynczych akcji. ETF-y i indeksy dostają wynik neutralny.",
        )

    growth = _score_growth(info)
    quality = _score_quality(info)
    balance = _score_balance_sheet(info)
    market_view = _score_market_view(info, price)
    event_component, recent_events = _score_recent_events(ticker, info)

    financial_score = _weighted_average([
        (growth["score"], 1.0),
        (quality["score"], 1.2),
        (balance["score"], 1.0),
        (market_view["score"], 0.8),
    ])
    event_score = event_component["score"]
    company_score = _weighted_average([
        (financial_score, 0.8),
        (event_score, 0.2),
    ])

    financial_bias = _bias_for_score(financial_score)
    event_bias = _bias_for_score(event_score)
    company_bias = _bias_for_score(company_score)

    if company_bias > 0:
        stance = "Wspiera spółkę"
        summary = "Fundamenty i ostatnie sygnały lekko wspierają tę spółkę."
    elif company_bias < 0:
        stance = "Ciąży spółce"
        summary = "Fundamenty lub ostatnie wydarzenia obniżają atrakcyjność tej spółki."
    else:
        stance = "Neutralne"
        summary = "Obraz spółki jest mieszany albo dostępne dane nie dają wyraźnej przewagi."

    return {
        "ticker": ticker,
        "score": round(company_score, 1),
        "stance": stance,
        "color": _score_color(company_score),
        "opportunity_adjustment": company_bias,
        "portfolio_bias": company_bias,
        "financial_score": round(financial_score, 1),
        "event_score": round(event_score, 1),
        "financial_bias": financial_bias,
        "event_bias": event_bias,
        "summary": summary,
        "components": [growth, quality, balance, market_view, event_component],
        "events": recent_events,
        "is_equity": True,
        "snapshot_time": datetime.now(timezone.utc).isoformat(),
    }