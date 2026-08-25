"""analysis/value_investing.py — Fundamental value-investing screen and per-stock snapshot."""
from __future__ import annotations

import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import config
from analysis.company_analysis import get_company_snapshot
from data.fetcher import get_fundamental_history, get_info, get_latest_price, get_ownership_snapshot


def _safe_float(value, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int | None = None) -> int | None:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _weighted_average(parts: list[tuple[float | None, float]]) -> float:
    valid = [(score, weight) for score, weight in parts if score is not None and weight > 0]
    if not valid:
        return 50.0
    total_weight = sum(weight for _, weight in valid)
    if total_weight <= 0:
        return 50.0
    return sum(float(score) * weight for score, weight in valid) / total_weight


def _ratio_to_pct(value, digits: int = 1) -> float | None:
    number = _safe_float(value)
    if number is None:
        return None
    return round(number * 100.0, digits)


def _ratio_value(numerator, denominator, digits: int = 2) -> float | None:
    num = _safe_float(numerator)
    den = _safe_float(denominator)
    if num is None or den is None or den == 0:
        return None
    return round(num / den, digits)


def _ratio_pct(numerator, denominator, digits: int = 1) -> float | None:
    num = _safe_float(numerator)
    den = _safe_float(denominator)
    if num is None or den is None or den == 0:
        return None
    return round((num / den) * 100.0, digits)


def _margin_pct(fair_price: float | None, price: float | None) -> float | None:
    if fair_price is None or price is None or price <= 0:
        return None
    return round((fair_price - price) / price * 100.0, 1)


def _net_debt(info: dict) -> float | None:
    total_debt = _safe_float(info.get("total_debt"))
    total_cash = _safe_float(info.get("total_cash"))
    if total_debt is None and total_cash is None:
        return None
    return (total_debt or 0.0) - (total_cash or 0.0)


def _net_debt_to_ebitda(info: dict) -> float | None:
    ebitda = _safe_float(info.get("ebitda"))
    if ebitda is None or ebitda <= 0:
        return None
    return _ratio_value(_net_debt(info), ebitda, 2)


def _cash_conversion_ratio(info: dict) -> float | None:
    operating_cashflow = _safe_float(info.get("operating_cashflow"))
    net_income = _safe_float(info.get("net_income"))
    if operating_cashflow is None or net_income is None or net_income == 0:
        return None
    return _ratio_value(operating_cashflow, net_income, 2)


def _compact_number(value) -> str:
    number = _safe_float(value)
    if number is None:
        return "—"
    magnitude = abs(number)
    if magnitude >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"
    if magnitude >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if magnitude >= 1_000:
        return f"{number / 1_000:.1f}K"
    return f"{number:.0f}"


def _recent_series(records: list[dict], key: str, limit: int = 4) -> list[float]:
    if len(records) < limit:
        return []
    series: list[float] = []
    for record in records[:limit]:
        value = _safe_float(record.get(key))
        if value is None:
            return []
        series.append(value)
    return series


def _trend_signal(values: list[float], *, direction: str, minimum_move_pct: float) -> tuple[bool, float | None]:
    if len(values) < 4:
        return False, None
    ordered = list(reversed(values[:4]))
    if direction == "down":
        trend_ok = all(curr < prev for prev, curr in zip(ordered, ordered[1:]))
    else:
        trend_ok = all(curr > prev for prev, curr in zip(ordered, ordered[1:]))

    start = ordered[0]
    end = ordered[-1]
    pct_change = None if start == 0 else round(((end - start) / abs(start)) * 100.0, 1)
    if trend_ok and pct_change is not None:
        if direction == "down" and pct_change > -minimum_move_pct:
            trend_ok = False
        elif direction == "up" and pct_change < minimum_move_pct:
            trend_ok = False
    return trend_ok, pct_change


def _score_quarterly_trend(history: dict | None) -> tuple[float | None, list[str], list[str], list[str], dict]:
    quarterly = (history or {}).get("quarterly") or []
    if len(quarterly) < 4:
        return None, [], [], [], {"available_quarters": len(quarterly), "summary": "Brak 4 pełnych kwartałów do testu trendu."}

    score = 50.0
    strengths: list[str] = []
    risks: list[str] = []
    red_flags: list[str] = []
    notes: list[str] = []

    revenue_series = _recent_series(quarterly, "revenue")
    net_income_series = _recent_series(quarterly, "net_income")
    operating_cashflow_series = _recent_series(quarterly, "operating_cashflow")
    debt_series = _recent_series(quarterly, "total_debt")

    revenue_down, revenue_change = _trend_signal(revenue_series, direction="down", minimum_move_pct=8.0)
    revenue_up, revenue_rebound = _trend_signal(revenue_series, direction="up", minimum_move_pct=8.0)
    if revenue_down:
        score -= 12
        risks.append("przychody spadają 3 kwartały z rzędu")
        notes.append(f"przychody {revenue_change:+.1f}%")
        if revenue_change is not None and revenue_change <= -15.0:
            red_flags.append("3 kolejne słabsze kwartały przychodów")
    elif revenue_up:
        score += 8
        strengths.append("przychody rosną 3 kwartały z rzędu")
        notes.append(f"przychody {revenue_rebound:+.1f}%")

    earnings_down, earnings_change = _trend_signal(net_income_series, direction="down", minimum_move_pct=15.0)
    earnings_up, earnings_rebound = _trend_signal(net_income_series, direction="up", minimum_move_pct=15.0)
    if earnings_down:
        score -= 14
        risks.append("zysk netto pogarsza się 3 kwartały z rzędu")
        notes.append(f"zysk netto {earnings_change:+.1f}%")
        latest_net_income = net_income_series[0] if net_income_series else None
        if latest_net_income is not None and latest_net_income < 0:
            red_flags.append("zysk netto wszedł pod kreskę po serii słabszych kwartałów")
    elif earnings_up:
        score += 8
        strengths.append("zysk netto poprawia się od 3 kwartałów")
        notes.append(f"zysk netto {earnings_rebound:+.1f}%")

    cashflow_down, cashflow_change = _trend_signal(operating_cashflow_series, direction="down", minimum_move_pct=12.0)
    cashflow_up, cashflow_rebound = _trend_signal(operating_cashflow_series, direction="up", minimum_move_pct=12.0)
    if cashflow_down:
        score -= 16
        risks.append("operacyjna gotówka słabnie 3 kwartały z rzędu")
        notes.append(f"OCF {cashflow_change:+.1f}%")
        latest_operating_cashflow = operating_cashflow_series[0] if operating_cashflow_series else None
        if latest_operating_cashflow is not None and latest_operating_cashflow <= 0:
            red_flags.append("operacyjna gotówka zeszła poniżej zera po serii słabszych kwartałów")
    elif cashflow_up:
        score += 10
        strengths.append("operacyjna gotówka poprawia się od 3 kwartałów")
        notes.append(f"OCF {cashflow_rebound:+.1f}%")

    debt_up, debt_change = _trend_signal(debt_series, direction="up", minimum_move_pct=15.0)
    if debt_up:
        score -= 8
        risks.append("zadłużenie rośnie 3 kwartały z rzędu")
        notes.append(f"dług {debt_change:+.1f}%")

    if not strengths and not risks:
        notes.append("ostatnie 4 kwartały bez seryjnego pogorszenia")

    return round(_clip(score), 1), strengths, risks, red_flags, {
        "available_quarters": len(quarterly),
        "summary": " · ".join(notes[:3]),
        "latest_quarter": quarterly[0].get("period_end") if quarterly else None,
        "quarterly": quarterly[:4],
    }


def _score_ownership_overlay(ownership: dict | None) -> tuple[float | None, list[str], list[str], list[str], dict]:
    snapshot = ownership or {}
    insiders_pct = _safe_float(snapshot.get("insiders_pct"))
    institutions_pct = _safe_float(snapshot.get("institutions_pct"))
    institutions_float_pct = _safe_float(snapshot.get("institutions_float_pct"))
    top5_institutional_pct = _safe_float(snapshot.get("top5_institutional_pct"))
    purchase_trans = _safe_int(snapshot.get("insider_purchase_transactions_6m"))
    sale_trans = _safe_int(snapshot.get("insider_sale_transactions_6m"))
    insider_net_shares = _safe_float(snapshot.get("insider_net_shares_6m"))
    recent_insider_net_shares = _safe_float(snapshot.get("recent_insider_net_shares"))

    if all(value is None for value in [insiders_pct, institutions_pct, institutions_float_pct, top5_institutional_pct, insider_net_shares, recent_insider_net_shares]):
        return None, [], [], [], {"summary": "Brak wiarygodnych danych insider/holders dla tego rynku."}

    score = 50.0
    strengths: list[str] = []
    risks: list[str] = []
    red_flags: list[str] = []
    notes: list[str] = []

    if insiders_pct is not None:
        notes.append(f"insiderzy {insiders_pct * 100:.1f}%")
        if 0.03 <= insiders_pct <= 0.35:
            score += 8
            strengths.append("insiderzy mają realny udział w akcjonariacie")
        elif insiders_pct >= 0.60:
            score -= 4
            risks.append("akcjonariat jest bardzo skoncentrowany po stronie insiderów")

    if institutions_pct is not None:
        notes.append(f"instytucje {institutions_pct * 100:.1f}%")
        if institutions_pct >= 0.25:
            score += 6
            strengths.append("akcjonariat ma sensowne pokrycie instytucjonalne")
        elif institutions_pct <= 0.02 and (insiders_pct is None or insiders_pct <= 0.01):
            score -= 5
            risks.append("bardzo niskie pokrycie insiderów i instytucji")

    if top5_institutional_pct is not None and top5_institutional_pct >= 0.40:
        score -= 4
        risks.append("duża część free floatu siedzi w kilku funduszach")

    if insider_net_shares is not None and purchase_trans is not None and sale_trans is not None:
        if insider_net_shares > 0 and purchase_trans >= max(2, sale_trans):
            score += 8
            strengths.append("w ostatnich 6 miesiącach insiderzy netto dokupują")
            notes.append("insiderzy netto kupują")
        elif insider_net_shares < 0 and sale_trans > purchase_trans:
            score -= 8
            risks.append("w ostatnich 6 miesiącach insiderzy netto sprzedają")
            notes.append("insiderzy netto sprzedają")

    if recent_insider_net_shares is not None and recent_insider_net_shares < 0:
        score -= 4
        red_flags.append("świeże transakcje insiderów są netto podażowe")

    return round(_clip(score), 1), strengths, risks, red_flags, {
        "summary": " · ".join(notes[:3]) or "ownership overlay aktywny",
        "insiders_pct": insiders_pct,
        "institutions_pct": institutions_pct,
        "institutions_float_pct": institutions_float_pct,
        "top5_institutional_pct": top5_institutional_pct,
    }


def _is_equity(info: dict) -> bool:
    return str(info.get("quote_type") or "").upper() == "EQUITY"


def _fair_pe(info: dict) -> float:
    earnings_growth = _safe_float(info.get("earnings_growth"), 0.0) or 0.0
    revenue_growth = _safe_float(info.get("revenue_growth"), 0.0) or 0.0
    roe = _safe_float(info.get("roe"), 0.0) or 0.0
    multiple = 14.0

    if max(earnings_growth, revenue_growth) >= 0.18:
        multiple += 3.5
    elif max(earnings_growth, revenue_growth) >= 0.08:
        multiple += 1.5
    elif max(earnings_growth, revenue_growth) < -0.05:
        multiple -= 2.5

    if roe >= 0.20:
        multiple += 1.0
    elif roe < 0.08:
        multiple -= 1.0

    return _clip(multiple, 10.0, 19.0)


def _fair_ev_ebitda_multiple(info: dict) -> float:
    growth = max(_safe_float(info.get("earnings_growth"), 0.0) or 0.0, _safe_float(info.get("revenue_growth"), 0.0) or 0.0)
    gross_margin = _safe_float(info.get("gross_margin"), 0.0) or 0.0
    roa = _safe_float(info.get("roa"), 0.0) or 0.0
    debt_to_equity = _safe_float(info.get("debt_to_equity"), 100.0) or 100.0

    multiple = 7.0
    if growth >= 0.12:
        multiple += 1.5
    elif growth < -0.05:
        multiple -= 1.0

    if gross_margin >= 0.45:
        multiple += 1.0
    elif gross_margin < 0.15:
        multiple -= 0.75

    if roa >= 0.07:
        multiple += 1.0
    elif roa < 0.02:
        multiple -= 0.75

    if debt_to_equity <= 60:
        multiple += 0.5
    elif debt_to_equity >= 180:
        multiple -= 1.0

    return _clip(multiple, 5.5, 11.5)


def _price_candidate(price: float, fair_price: float | None) -> float | None:
    if fair_price is None or fair_price <= 0 or price <= 0:
        return None
    if fair_price < price * 0.35 or fair_price > price * 4.5:
        return None
    return fair_price


def _fair_value_candidates(info: dict, price: float) -> list[dict]:
    candidates: list[dict] = []

    target_mean_price = _safe_float(info.get("target_mean_price"))
    analyst_count = _safe_int(info.get("analyst_count"), 0) or 0
    target_candidate = _price_candidate(price, target_mean_price)
    if target_candidate is not None and analyst_count >= 4:
        candidates.append({
            "label": "Konsensus analityków",
            "fair_price": round(target_candidate, 2),
            "weight": 1.25,
            "note": f"Średni target z {analyst_count} opinii analityków.",
        })

    earnings_basis = _safe_float(info.get("forward_eps"))
    earnings_label = "Forward EPS"
    if earnings_basis is None or earnings_basis <= 0:
        earnings_basis = _safe_float(info.get("eps"))
        earnings_label = "Trailing EPS"
    if earnings_basis is not None and earnings_basis > 0:
        fair_pe = _fair_pe(info)
        earnings_candidate = _price_candidate(price, earnings_basis * fair_pe)
        if earnings_candidate is not None:
            candidates.append({
                "label": "Siła zysków",
                "fair_price": round(earnings_candidate, 2),
                "weight": 1.0,
                "note": f"Uproszczona wartość z {earnings_label} × fair P/E {fair_pe:.1f}.",
            })

    market_cap = _safe_float(info.get("market_cap"))
    free_cashflow = _safe_float(info.get("free_cashflow"))
    operating_cashflow = _safe_float(info.get("operating_cashflow"))
    total_debt = _safe_float(info.get("total_debt"))
    total_cash = _safe_float(info.get("total_cash"))
    ebitda = _safe_float(info.get("ebitda"))
    shares_outstanding = _safe_float(info.get("shares_outstanding"))
    if market_cap is not None and market_cap > 0 and free_cashflow is not None and free_cashflow > 0:
        fcf_yield = free_cashflow / market_cap
        debt_to_equity = _safe_float(info.get("debt_to_equity"), 100.0) or 100.0
        target_yield = 0.06 if debt_to_equity <= 90 else 0.08 if debt_to_equity >= 180 else 0.07
        fcf_candidate = _price_candidate(price, price * (fcf_yield / target_yield))
        if fcf_candidate is not None:
            candidates.append({
                "label": "Free cash flow",
                "fair_price": round(fcf_candidate, 2),
                "weight": 0.9,
                "note": f"Przy założonym docelowym FCF yield {target_yield * 100:.1f}%.",
            })

    if market_cap is not None and market_cap > 0 and operating_cashflow is not None and operating_cashflow > 0:
        ocf_yield = operating_cashflow / market_cap
        debt_to_equity = _safe_float(info.get("debt_to_equity"), 100.0) or 100.0
        target_yield = 0.07 if debt_to_equity <= 90 else 0.085 if debt_to_equity >= 180 else 0.075
        ocf_candidate = _price_candidate(price, price * (ocf_yield / target_yield))
        if ocf_candidate is not None:
            candidates.append({
                "label": "Gotówka operacyjna",
                "fair_price": round(ocf_candidate, 2),
                "weight": 0.8,
                "note": f"Kotwica z operating cash flow przy docelowym cash yield {target_yield * 100:.1f}%.",
            })

    if ebitda is not None and ebitda > 0 and shares_outstanding is not None and shares_outstanding > 0:
        fair_multiple = _fair_ev_ebitda_multiple(info)
        fair_enterprise_value = ebitda * fair_multiple
        equity_value = fair_enterprise_value - (total_debt or 0.0) + (total_cash or 0.0)
        ev_ebitda_candidate = _price_candidate(price, equity_value / shares_outstanding)
        if ev_ebitda_candidate is not None:
            candidates.append({
                "label": "EV/EBITDA",
                "fair_price": round(ev_ebitda_candidate, 2),
                "weight": 1.05,
                "note": f"Konserwatywna wycena z EV/EBITDA {fair_multiple:.1f}x oraz korektą o gotówkę i dług.",
            })

    book_value = _safe_float(info.get("book_value"))
    book_candidate = _price_candidate(price, book_value)
    if book_candidate is not None:
        candidates.append({
            "label": "Wartość księgowa",
            "fair_price": round(book_candidate, 2),
            "weight": 0.45,
            "note": "Konserwatywna kotwica aktywów netto na akcję.",
        })

    return candidates


def _score_balance_sheet(info: dict) -> tuple[float, list[str], list[str]]:
    debt_to_equity = _safe_float(info.get("debt_to_equity"))
    current_ratio = _safe_float(info.get("current_ratio"))
    quick_ratio = _safe_float(info.get("quick_ratio"))
    total_cash = _safe_float(info.get("total_cash"))
    total_debt = _safe_float(info.get("total_debt"))
    market_cap = _safe_float(info.get("market_cap"))

    score = 50.0
    strengths: list[str] = []
    risks: list[str] = []

    if debt_to_equity is not None:
        if debt_to_equity <= 60:
            score += 18
            strengths.append("zadłużenie wygląda bezpiecznie")
        elif debt_to_equity <= 120:
            score += 8
        elif debt_to_equity >= 200:
            score -= 18
            risks.append("wysokie zadłużenie może utrudnić odblokowanie wartości")

    liquidity = quick_ratio if quick_ratio is not None else current_ratio
    if liquidity is not None:
        if liquidity >= 1.2:
            score += 10
            strengths.append("spółka ma bezpieczną płynność")
        elif liquidity < 0.8:
            score -= 10
            risks.append("płynność jest napięta")

    if total_cash is not None and total_debt is not None and market_cap and market_cap > 0:
        net_cash_pct = ((total_cash - total_debt) / market_cap) * 100.0
        if net_cash_pct >= 10:
            score += 10
            strengths.append("bilans ma poduszkę netto gotówki")
        elif net_cash_pct <= -20:
            score -= 10
            risks.append("dług netto jest wysoki względem kapitalizacji")

    return round(_clip(score), 1), strengths, risks


def _score_cash_flow_quality(info: dict) -> tuple[float, list[str], list[str], list[str]]:
    operating_cashflow = _safe_float(info.get("operating_cashflow"))
    free_cashflow = _safe_float(info.get("free_cashflow"))
    net_income = _safe_float(info.get("net_income"))
    total_revenue = _safe_float(info.get("total_revenue"))
    payout_ratio = _safe_float(info.get("payout_ratio"))
    dividend_yield = _safe_float(info.get("dividend_yield"))

    cash_conversion = _cash_conversion_ratio(info)
    operating_cashflow_margin_pct = _ratio_pct(operating_cashflow, total_revenue)
    fcf_margin_pct = _ratio_pct(free_cashflow, total_revenue)

    score = 50.0
    strengths: list[str] = []
    risks: list[str] = []
    red_flags: list[str] = []

    if operating_cashflow is not None:
        if operating_cashflow > 0:
            score += 8
            strengths.append("operacyjna gotówka jest dodatnia")
        else:
            score -= 16
            risks.append("operacyjne przepływy są ujemne")
            red_flags.append("ujemny operating cash flow")

    if free_cashflow is not None:
        if free_cashflow > 0:
            score += 8
            strengths.append("wolne przepływy pieniężne są dodatnie")
        else:
            score -= 10
            risks.append("free cash flow jest ujemny")
            red_flags.append("ujemny free cash flow")

    if cash_conversion is not None:
        if net_income is not None and net_income > 0:
            if cash_conversion >= 1.1:
                score += 12
                strengths.append("zysk dobrze zamienia się w gotówkę")
            elif cash_conversion >= 0.8:
                score += 6
            elif cash_conversion < 0.5:
                score -= 16
                risks.append("zysk księgowy słabo konwertuje się do gotówki")
                red_flags.append("słaba konwersja zysku netto do gotówki")
            else:
                score -= 8
                risks.append("konwersja zysku do gotówki jest niska")
        elif net_income is not None and net_income < 0 and operating_cashflow and operating_cashflow > 0:
            score += 8
            strengths.append("mimo słabego wyniku księgowego biznes generuje gotówkę")

    if operating_cashflow_margin_pct is not None:
        if operating_cashflow_margin_pct >= 12:
            score += 6
            strengths.append("marża gotówkowa z działalności operacyjnej jest wysoka")
        elif operating_cashflow_margin_pct < 4:
            score -= 6
            risks.append("marża gotówkowa jest niska")

    if fcf_margin_pct is not None:
        if fcf_margin_pct >= 8:
            score += 6
        elif fcf_margin_pct < 2:
            score -= 6

    if payout_ratio is not None:
        if payout_ratio <= 0.60 and free_cashflow is not None and free_cashflow > 0:
            score += 6
            strengths.append("dywidenda wygląda na zabezpieczoną przez realne FCF")
        elif payout_ratio >= 0.80:
            score -= 10
            risks.append("wysoki payout ratio zostawia mały bufor bezpieczeństwa")
            if dividend_yield is not None and dividend_yield > 0:
                red_flags.append("wysoka wypłata dywidendy przy napiętej gotówce")
    elif dividend_yield is not None and dividend_yield > 0 and free_cashflow is not None and free_cashflow <= 0:
        score -= 10
        risks.append("dywidenda nie jest dziś wsparta dodatnim free cash flow")
        red_flags.append("dywidenda bez pokrycia w FCF")

    return round(_clip(score), 1), strengths, risks, red_flags


def _score_capital_efficiency(info: dict) -> tuple[float, list[str], list[str], list[str]]:
    roa = _safe_float(info.get("roa"))
    roe = _safe_float(info.get("roe"))
    gross_margin = _safe_float(info.get("gross_margin"))
    debt_to_equity = _safe_float(info.get("debt_to_equity"))
    net_debt_to_ebitda = _net_debt_to_ebitda(info)

    score = 50.0
    strengths: list[str] = []
    risks: list[str] = []
    red_flags: list[str] = []

    if roa is not None:
        if roa >= 0.07:
            score += 12
            strengths.append("aktywa generują wysoki zwrot gotówkowy")
        elif roa >= 0.03:
            score += 6
        elif roa < 0.01:
            score -= 10
            risks.append("rentowność aktywów jest bardzo niska")

    if roe is not None:
        if roe >= 0.15:
            if debt_to_equity is not None and debt_to_equity >= 180:
                score += 2
                risks.append("wysokie ROE jest częściowo podbite dźwignią finansową")
            else:
                score += 8
                strengths.append("kapitał własny pracuje efektywnie")
        elif roe < 0.08:
            score -= 6
            risks.append("zwrot na kapitale własnym jest niski")

    if gross_margin is not None:
        if gross_margin >= 0.45:
            score += 10
            strengths.append("wysoka marża brutto sugeruje przewagę operacyjną")
        elif gross_margin >= 0.25:
            score += 4
        elif gross_margin < 0.15:
            score -= 8
            risks.append("niska marża brutto ogranicza odporność biznesu")

    if net_debt_to_ebitda is not None:
        if net_debt_to_ebitda <= 1.5:
            score += 10
            strengths.append("dług netto do EBITDA pozostaje pod kontrolą")
        elif net_debt_to_ebitda <= 3.0:
            score += 4
        elif net_debt_to_ebitda >= 4.0:
            score -= 16
            risks.append("relacja długu netto do EBITDA jest wysoka")
            red_flags.append("rosnące ryzyko zadłużenia względem EBITDA")

    return round(_clip(score), 1), strengths, risks, red_flags


def _score_valuation(info: dict, price: float, fair_value_price: float | None, margin_of_safety_pct: float | None) -> tuple[float, list[str], list[str]]:
    price_to_book = _safe_float(info.get("price_to_book"))
    forward_pe = _safe_float(info.get("forward_pe"))
    pe_ratio = _safe_float(info.get("pe_ratio"))
    price_to_sales = _safe_float(info.get("price_to_sales"))
    market_cap = _safe_float(info.get("market_cap"))
    free_cashflow = _safe_float(info.get("free_cashflow"))
    fcf_yield_pct = ((free_cashflow / market_cap) * 100.0) if market_cap and market_cap > 0 and free_cashflow is not None else None

    score = 50.0
    strengths: list[str] = []
    risks: list[str] = []

    if margin_of_safety_pct is not None:
        if margin_of_safety_pct >= 25:
            score += 24
            strengths.append("duży margines bezpieczeństwa do fair value proxy")
        elif margin_of_safety_pct >= 10:
            score += 14
            strengths.append("wycena jest niższa od szacowanej wartości")
        elif margin_of_safety_pct <= -12:
            score -= 16
            risks.append("rynek nie pokazuje dziś dyskonta do fair value proxy")

    if price_to_book is not None:
        if price_to_book <= 1.2:
            score += 14
            strengths.append("cena jest niska względem wartości księgowej")
        elif price_to_book <= 2.0:
            score += 6
        elif price_to_book >= 4.0:
            score -= 10
            risks.append("wskaźnik P/B jest wysoki jak na styl value")

    valuation_pe = forward_pe if forward_pe is not None else pe_ratio
    if valuation_pe is not None:
        if valuation_pe <= 12:
            score += 12
            strengths.append("mnożnik zysków wygląda konserwatywnie")
        elif valuation_pe <= 18:
            score += 5
        elif valuation_pe >= 30:
            score -= 10
            risks.append("wycena na zyskach jest wymagająca")

    if price_to_sales is not None:
        if price_to_sales <= 2.0:
            score += 6
        elif price_to_sales >= 8.0:
            score -= 6

    if fcf_yield_pct is not None:
        if fcf_yield_pct >= 8.0:
            score += 14
            strengths.append("free cash flow yield jest atrakcyjny")
        elif fcf_yield_pct >= 5.0:
            score += 7
        elif fcf_yield_pct < 0:
            score -= 14
            risks.append("spółka nie generuje dodatniego free cash flow")

    return round(_clip(score), 1), strengths, risks


def get_value_investing_snapshot(
    ticker: str,
    *,
    info: dict | None = None,
    company: dict | None = None,
    price: float | None = None,
    financial_history: dict | None = None,
    ownership: dict | None = None,
) -> dict:
    resolved_info = info or get_info(ticker)
    resolved_price = _safe_float(price)
    if resolved_price is None or resolved_price <= 0:
        resolved_price = _safe_float(get_latest_price(ticker, force_refresh=False))

    company_snapshot = company or get_company_snapshot(ticker, info=resolved_info, price=resolved_price)
    is_equity = _is_equity(resolved_info)

    if resolved_price is None or resolved_price <= 0:
        return {
            "ticker": ticker,
            "name": resolved_info.get("name", ticker),
            "is_equity": is_equity,
            "verdict": "Brak danych ceny",
            "summary": "Brakuje aktualnej ceny, więc nie da się policzyć wyceny value.",
            "snapshot_time": datetime.now(timezone.utc).isoformat(),
        }

    resolved_financial_history = financial_history if financial_history is not None else get_fundamental_history(ticker)
    resolved_ownership = ownership if ownership is not None else get_ownership_snapshot(ticker)

    candidates = _fair_value_candidates(resolved_info, resolved_price)
    fair_value_price = None
    if candidates:
        fair_value_price = _weighted_average([(item.get("fair_price"), float(item.get("weight") or 0.0)) for item in candidates])
    fair_value_price = round(fair_value_price, 2) if fair_value_price is not None else None
    margin_of_safety_pct = _margin_pct(fair_value_price, resolved_price)

    valuation_score, valuation_strengths, valuation_risks = _score_valuation(resolved_info, resolved_price, fair_value_price, margin_of_safety_pct)
    balance_score, balance_strengths, balance_risks = _score_balance_sheet(resolved_info)
    cash_flow_score, cash_flow_strengths, cash_flow_risks, cash_flow_red_flags = _score_cash_flow_quality(resolved_info)
    capital_efficiency_score, capital_efficiency_strengths, capital_efficiency_risks, capital_red_flags = _score_capital_efficiency(resolved_info)
    quarterly_trend_score, quarterly_trend_strengths, quarterly_trend_risks, quarterly_trend_red_flags, quarterly_trend_meta = _score_quarterly_trend(resolved_financial_history)
    ownership_score, ownership_strengths, ownership_risks, ownership_red_flags, ownership_meta = _score_ownership_overlay(resolved_ownership)
    quality_score = float((company_snapshot or {}).get("financial_score", 50) or 50)
    event_score = float((company_snapshot or {}).get("event_score", 50) or 50)
    value_score = round(_weighted_average([
        (valuation_score, 0.28),
        (quality_score, 0.16),
        (balance_score, 0.14),
        (cash_flow_score, 0.18),
        (capital_efficiency_score, 0.14),
        (quarterly_trend_score, 0.10),
        (ownership_score, 0.08),
    ]), 1)
    red_flags = list(dict.fromkeys(item for item in (cash_flow_red_flags + capital_red_flags + quarterly_trend_red_flags + ownership_red_flags) if item))
    base_trap_risk = 100.0 - _weighted_average([
        (quality_score, 0.22),
        (balance_score, 0.18),
        (event_score, 0.14),
        (cash_flow_score, 0.22),
        (capital_efficiency_score, 0.14),
        (quarterly_trend_score, 0.10),
        (ownership_score, 0.08),
    ])
    value_trap_risk = round(_clip(base_trap_risk + min(20.0, len(red_flags) * 8.0)), 1)

    if not is_equity:
        verdict = "Nie dotyczy"
        summary = "Value investing w tym widoku działa dla pojedynczych akcji; ETF-y i indeksy dostają neutralny wynik."
    elif len(red_flags) >= 2 or value_trap_risk >= 62 or (margin_of_safety_pct is not None and margin_of_safety_pct <= -12):
        verdict = "Ryzyko value trap"
        summary = "Niska wycena może być pułapką: przepływy pieniężne, zadłużenie albo jakość biznesu nie potwierdzają dziś bezpiecznego case'u value."
    elif value_score >= 72 and ((margin_of_safety_pct or 0) >= 10 or valuation_score >= 72):
        verdict = "Mocny kandydat value"
        summary = "Spółka wygląda jak sensowny kandydat value: wycena jest relatywnie niska, a gotówka, bilans i rentowność kapitału wspierają tezę inwestycyjną."
    elif value_score >= 58 and (margin_of_safety_pct is None or margin_of_safety_pct >= -3):
        verdict = "Kandydat value"
        summary = "Wycena wygląda rozsądnie, ale potrzeba jeszcze potwierdzenia w gotówce, zadłużeniu i trwałości przewagi operacyjnej."
    else:
        verdict = "Neutralna wycena"
        summary = "Na dziś spółka nie daje wyraźnej przewagi dla czystego stylu value investing."

    strengths = quarterly_trend_strengths + ownership_strengths + cash_flow_strengths + capital_efficiency_strengths + balance_strengths + valuation_strengths
    risks = quarterly_trend_risks + ownership_risks + cash_flow_risks + capital_efficiency_risks + balance_risks + valuation_risks
    company_summary = str((company_snapshot or {}).get("summary") or "").strip()
    if quality_score >= 60 and company_summary:
        strengths.append(company_summary)
    elif quality_score <= 45 and company_summary:
        risks.append(company_summary)

    target_mean_price = _safe_float(resolved_info.get("target_mean_price"))
    analyst_count = _safe_int(resolved_info.get("analyst_count"), 0) or 0
    market_cap = _safe_float(resolved_info.get("market_cap"))
    free_cashflow = _safe_float(resolved_info.get("free_cashflow"))
    total_cash = _safe_float(resolved_info.get("total_cash"))
    total_debt = _safe_float(resolved_info.get("total_debt"))
    ebitda = _safe_float(resolved_info.get("ebitda"))
    total_revenue = _safe_float(resolved_info.get("total_revenue"))
    operating_cashflow = _safe_float(resolved_info.get("operating_cashflow"))
    net_income = _safe_float(resolved_info.get("net_income"))
    fcf_yield_pct = round((free_cashflow / market_cap) * 100.0, 1) if market_cap and market_cap > 0 and free_cashflow is not None else None
    operating_cashflow_yield_pct = round((operating_cashflow / market_cap) * 100.0, 1) if market_cap and market_cap > 0 and operating_cashflow is not None else None
    net_cash_pct = round(((total_cash - total_debt) / market_cap) * 100.0, 1) if market_cap and market_cap > 0 and total_cash is not None and total_debt is not None else None
    target_upside_pct = _margin_pct(target_mean_price, resolved_price)
    book_value = _safe_float(resolved_info.get("book_value"))
    book_discount_pct = _margin_pct(book_value, resolved_price)
    payout_ratio_pct = _ratio_to_pct(resolved_info.get("payout_ratio"))
    roa_pct = _ratio_to_pct(resolved_info.get("roa"))
    gross_margin_pct = _ratio_to_pct(resolved_info.get("gross_margin"))
    operating_cashflow_margin_pct = _ratio_pct(operating_cashflow, total_revenue)
    fcf_margin_pct = _ratio_pct(free_cashflow, total_revenue)
    cash_conversion_ratio = _cash_conversion_ratio(resolved_info)
    net_debt_to_ebitda = _net_debt_to_ebitda(resolved_info)

    components = [
        {
            "label": "Wycena vs fair value",
            "score": valuation_score,
            "value": (
                f"fair value {fair_value_price:.2f} / cena {resolved_price:.2f} / margines {margin_of_safety_pct:+.1f}%"
                if fair_value_price is not None and margin_of_safety_pct is not None
                else "Brak pełnego fair value proxy"
            ),
            "signal": "Im wyższy margines bezpieczeństwa i niższe mnożniki, tym lepiej dla stylu value.",
        },
        {
            "label": "Konwersja gotówki",
            "score": cash_flow_score,
            "value": (
                f"OCF/NI {cash_conversion_ratio:.2f}x · OCF margin {operating_cashflow_margin_pct:+.1f}% · FCF margin {fcf_margin_pct:+.1f}%"
                if cash_conversion_ratio is not None and operating_cashflow_margin_pct is not None and fcf_margin_pct is not None
                else "Przepływy operacyjne, FCF i ich pokrycie zysku netto"
            ),
            "signal": "W value liczy się nie tylko zysk księgowy, ale to, czy naprawdę zamienia się w gotówkę.",
        },
        {
            "label": "Rentowność kapitału",
            "score": capital_efficiency_score,
            "value": (
                f"ROA {roa_pct:+.1f}% · ROE {_ratio_to_pct(resolved_info.get('roe')):+.1f}% · Net debt/EBITDA {net_debt_to_ebitda:.2f}x"
                if roa_pct is not None and _ratio_to_pct(resolved_info.get('roe')) is not None and net_debt_to_ebitda is not None
                else "ROA, ROE, gross margin i obciążenie zadłużeniem"
            ),
            "signal": "Trwała przewaga operacyjna powinna bronić zwrotu z aktywów i kapitału bez nadmiernej dźwigni.",
        },
        {
            "label": "Jakość biznesu",
            "score": round(quality_score, 1),
            "value": f"score finansowy {quality_score:.0f}/100 · eventy {event_score:.0f}/100",
            "signal": company_summary or "Jakość biznesu pochodzi z istniejącej analizy spółki i eventów.",
        },
        {
            "label": "Trend kwartalny",
            "score": quarterly_trend_score,
            "value": quarterly_trend_meta.get("summary") or "Brak pełnej historii kwartalnej",
            "signal": "Seryjne pogarszanie przychodów, zysków albo OCF zwiększa ryzyko, że taniość jest tylko pułapką.",
        },
        {
            "label": "Akcjonariat i insiderzy",
            "score": ownership_score,
            "value": ownership_meta.get("summary") or "Brak stabilnych danych insider/holders",
            "signal": "To miękki overlay: wzmacnia conviction, ale nie zastępuje fundamentów ani gotówki.",
        },
        {
            "label": "Bilans i płynność",
            "score": balance_score,
            "value": f"D/E {(_safe_float(resolved_info.get('debt_to_equity')) if _safe_float(resolved_info.get('debt_to_equity')) is not None else 'brak')} · quick {(_safe_float(resolved_info.get('quick_ratio')) if _safe_float(resolved_info.get('quick_ratio')) is not None else 'brak')}",
            "signal": "Silny bilans obniża ryzyko, że tania spółka okaże się pułapką wartości.",
        },
    ]

    return {
        "ticker": ticker,
        "name": resolved_info.get("name", ticker),
        "currency": str(resolved_info.get("currency") or "USD").upper() or "USD",
        "sector": resolved_info.get("sector", "—"),
        "industry": resolved_info.get("industry", "—"),
        "price": round(resolved_price, 2),
        "is_equity": is_equity,
        "verdict": verdict,
        "summary": summary,
        "value_score": value_score,
        "value_trap_risk": value_trap_risk,
        "valuation_score": valuation_score,
        "quality_score": round(quality_score, 1),
        "balance_score": balance_score,
        "cash_flow_score": cash_flow_score,
        "capital_efficiency_score": capital_efficiency_score,
        "quarterly_trend_score": quarterly_trend_score,
        "ownership_score": ownership_score,
        "fair_value_price": fair_value_price,
        "margin_of_safety_pct": margin_of_safety_pct,
        "fair_value_sources": candidates,
        "target_mean_price": round(target_mean_price, 2) if target_mean_price is not None else None,
        "target_upside_pct": target_upside_pct,
        "analyst_count": analyst_count,
        "pe_ratio": _safe_float(resolved_info.get("pe_ratio")),
        "forward_pe": _safe_float(resolved_info.get("forward_pe")),
        "peg_ratio": _safe_float(resolved_info.get("peg_ratio")),
        "price_to_book": _safe_float(resolved_info.get("price_to_book")),
        "book_value": book_value,
        "book_discount_pct": book_discount_pct,
        "price_to_sales": _safe_float(resolved_info.get("price_to_sales")),
        "enterprise_value": _safe_float(resolved_info.get("enterprise_value")),
        "enterprise_to_ebitda": _safe_float(resolved_info.get("enterprise_to_ebitda")),
        "enterprise_to_revenue": _safe_float(resolved_info.get("enterprise_to_revenue")),
        "market_cap": market_cap,
        "ebitda": ebitda,
        "total_revenue": total_revenue,
        "net_income": net_income,
        "fcf_yield_pct": fcf_yield_pct,
        "operating_cashflow_yield_pct": operating_cashflow_yield_pct,
        "free_cashflow": free_cashflow,
        "operating_cashflow": operating_cashflow,
        "total_cash": total_cash,
        "total_debt": total_debt,
        "net_cash_pct": net_cash_pct,
        "net_debt_to_ebitda": net_debt_to_ebitda,
        "debt_to_equity": _safe_float(resolved_info.get("debt_to_equity")),
        "current_ratio": _safe_float(resolved_info.get("current_ratio")),
        "quick_ratio": _safe_float(resolved_info.get("quick_ratio")),
        "roe_pct": _ratio_to_pct(resolved_info.get("roe")),
        "roa_pct": roa_pct,
        "profit_margin_pct": _ratio_to_pct(resolved_info.get("profit_margin")),
        "operating_margin_pct": _ratio_to_pct(resolved_info.get("operating_margin")),
        "gross_margin_pct": gross_margin_pct,
        "revenue_growth_pct": _ratio_to_pct(resolved_info.get("revenue_growth")),
        "earnings_growth_pct": _ratio_to_pct(resolved_info.get("earnings_growth")),
        "operating_cashflow_margin_pct": operating_cashflow_margin_pct,
        "fcf_margin_pct": fcf_margin_pct,
        "cash_conversion_ratio": cash_conversion_ratio,
        "payout_ratio_pct": payout_ratio_pct,
        "dividend_rate": _safe_float(resolved_info.get("dividend_rate")),
        "company_summary": company_summary,
        "company_score": round(float((company_snapshot or {}).get("score", 50) or 50), 1),
        "events": (company_snapshot or {}).get("events") or [],
        "components": [component for component in components if component.get("score") is not None],
        "quarterly_history": (resolved_financial_history or {}).get("quarterly") or [],
        "quarterly_trend_summary": quarterly_trend_meta.get("summary"),
        "ownership_summary": ownership_meta.get("summary"),
        "ownership_snapshot": resolved_ownership,
        "strengths": list(dict.fromkeys(item for item in strengths if item))[:6],
        "risks": list(dict.fromkeys(item for item in risks if item))[:6],
        "red_flags": red_flags,
        "snapshot_time": datetime.now(timezone.utc).isoformat(),
    }


def scan_value_universe(tickers: list[str], progress_callback=None) -> list[dict]:
    results: list[dict] = []
    total = len(tickers)
    concurrency = max(1, int(getattr(config, "SCANNER_CONCURRENCY", 8)))

    def _score_one(ticker: str) -> dict | None:
        """Score a single ticker for value investing. Returns None on failure or if filtered out."""
        try:
            snapshot = get_value_investing_snapshot(str(ticker or "").upper().strip())
            if not snapshot.get("is_equity"):
                return None
            score = float(snapshot.get("value_score") or 0.0)
            margin = snapshot.get("margin_of_safety_pct")
            if score < 50 and (margin is None or margin < 5.0):
                return None
            return snapshot
        except Exception:
            tb = traceback.format_exc()
            print(f"[scan_value_universe] Error scoring {ticker}: {tb}", file=sys.stderr)
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
                print(f"[scan_value_universe] Future error for {ticker}: {tb}", file=sys.stderr)

    results.sort(
        key=lambda item: (
            float(item.get("value_score") or 0.0),
            float(item.get("margin_of_safety_pct") or -999.0),
            float(item.get("quality_score") or 0.0),
        ),
        reverse=True,
    )
    return results