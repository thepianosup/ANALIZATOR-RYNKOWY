"""analysis/portfolio_suggestions.py — Portfolio-level action suggestions and swap ideas."""
from __future__ import annotations

from datetime import datetime

from analysis.opportunity import (
    describe_opportunity_rank_advantage,
    get_opportunity_rank_breakdown,
    get_opportunity_rank_score,
)
from analysis.portfolio_analysis import analyze_holding


_REC_SCORE = {
    "DOKUP WIĘCEJ": 3,
    "TRZYMAJ": 1,
    "ROZWAŻ SPRZEDAŻ": -2,
    "SPRZEDAJ": -4,
    "STOP LOSS": -5,
}

_REC_PRIORITY = {
    "STOP LOSS": 5,
    "SPRZEDAJ": 4,
    "ROZWAŻ SPRZEDAŻ": 3,
    "TRZYMAJ": 2,
    "DOKUP WIĘCEJ": 1,
}

_MIN_SHOWCASE_WIN_RATE_1M = 55.0
_MIN_SHOWCASE_AVG_RETURN_1M = 2.0
_MIN_SHOWCASE_EV_1M = 1.0
_MIN_SHOWCASE_SAMPLES_1M = 5
_MIN_SHOWCASE_CONFIDENCE = 40.0


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _pick_metric(*values) -> float | None:
    for value in values:
        parsed = _safe_float(value)
        if parsed is not None:
            return parsed
    return None


def _metric_1m(details: dict, metric_name: str) -> float | None:
    return _safe_float(details.get(f"{metric_name}_22d"))


def _is_showcase_opportunity(opportunity: dict, profile: str = "balanced") -> bool:
    details = opportunity.get("details") or {}
    win = _metric_1m(details, "win_rate")
    avg = _metric_1m(details, "avg_return")
    ev = _metric_1m(details, "ev")
    samples = int(details.get("n_setups", 0) or 0)
    confidence = float((get_opportunity_rank_breakdown(opportunity, "all", profile).get("confidence") or {}).get("score", 0.0) or 0.0)

    if win is None or avg is None or ev is None:
        return False
    if samples < _MIN_SHOWCASE_SAMPLES_1M:
        return False
    if confidence < _MIN_SHOWCASE_CONFIDENCE:
        return False
    return (
        win >= _MIN_SHOWCASE_WIN_RATE_1M
        and avg >= _MIN_SHOWCASE_AVG_RETURN_1M
        and ev >= _MIN_SHOWCASE_EV_1M
    )


def _top_reason_titles(analysis: dict, bad: bool | None = None, limit: int = 3) -> list[str]:
    reasons = analysis.get("reasons") or []
    out: list[str] = []
    for reason in reasons:
        if bad is not None and bool(reason.get("bad")) != bad:
            continue
        title = str(reason.get("title") or "").strip()
        if title:
            out.append(title)
        if len(out) >= limit:
            break
    return out


def _history_context(entry_bt: dict, traj_bt: dict) -> dict:
    entry_win = _pick_metric(entry_bt.get("win_rate_22d"), entry_bt.get("win_rate_1m"))
    entry_med = _pick_metric(entry_bt.get("median_return_22d"), entry_bt.get("median_return_1m"))
    traj_win = _pick_metric(traj_bt.get("win_rate_22d"), traj_bt.get("win_rate_1m"))
    traj_med = _pick_metric(traj_bt.get("median_return_22d"), traj_bt.get("median_return_1m"))
    return {
        "entry_win_rate_1m": round(entry_win, 1) if entry_win is not None else None,
        "entry_median_1m": round(entry_med, 2) if entry_med is not None else None,
        "trajectory_win_rate_1m": round(traj_win, 1) if traj_win is not None else None,
        "trajectory_median_1m": round(traj_med, 2) if traj_med is not None else None,
        "trajectory_samples": int(traj_bt.get("n_samples", 0) or 0),
        "entry_samples": int(entry_bt.get("n_samples", 0) or 0),
    }


def _history_alignment(history: dict) -> str:
    positive_votes = 0
    negative_votes = 0

    entry_samples = int(history.get("entry_samples", 0) or 0)
    entry_win = _safe_float(history.get("entry_win_rate_1m"))
    entry_med = _safe_float(history.get("entry_median_1m"))
    if entry_samples >= 5:
        if entry_win is not None:
            if entry_win >= 55:
                positive_votes += 1
            elif entry_win < 45:
                negative_votes += 1
        if entry_med is not None:
            if entry_med >= 2.0:
                positive_votes += 1
            elif entry_med <= -2.0:
                negative_votes += 1

    traj_samples = int(history.get("trajectory_samples", 0) or 0)
    traj_win = _safe_float(history.get("trajectory_win_rate_1m"))
    traj_med = _safe_float(history.get("trajectory_median_1m"))
    if traj_samples >= 10:
        if traj_win is not None:
            if traj_win >= 53:
                positive_votes += 1
            elif traj_win < 48:
                negative_votes += 1
        if traj_med is not None:
            if traj_med >= 1.0:
                positive_votes += 1
            elif traj_med <= -1.0:
                negative_votes += 1

    if negative_votes >= 2 and negative_votes > positive_votes:
        return "weak"
    if positive_votes >= 2 and positive_votes > negative_votes:
        return "supportive"
    return "mixed"


def _holding_score(analysis: dict) -> float:
    recommendation = str(analysis.get("recommendation") or "TRZYMAJ").upper()
    score = float(_REC_SCORE.get(recommendation, 0))

    entry_bt = analysis.get("entry_backtest") or {}
    traj_bt = analysis.get("trajectory_backtest") or {}
    entry_win = _pick_metric(entry_bt.get("win_rate_22d"), entry_bt.get("win_rate_1m"))
    entry_med = _pick_metric(entry_bt.get("median_return_22d"), entry_bt.get("median_return_1m"))
    traj_win = _pick_metric(traj_bt.get("win_rate_22d"), traj_bt.get("win_rate_1m"))
    traj_med = _pick_metric(traj_bt.get("median_return_22d"), traj_bt.get("median_return_1m"))
    pnl_pct = _safe_float(analysis.get("pnl_pct")) or 0.0
    setup_quality = _safe_float(analysis.get("setup_quality_score"))
    company_quality = _safe_float(analysis.get("company_quality_score"))
    macro_quality = _safe_float(analysis.get("macro_quality_score"))
    confidence = _safe_float((analysis.get("signal_confidence") or {}).get("score"))

    if entry_win is not None:
        score += _clip((entry_win - 50.0) / 12.0, -1.8, 1.8)
    if entry_med is not None:
        score += _clip(entry_med / 4.0, -1.5, 1.5)

    if (traj_bt.get("n_samples") or 0) >= 3:
        if traj_win is not None:
            score += _clip((traj_win - 50.0) / 10.0, -2.0, 2.0)
        if traj_med is not None:
            score += _clip(traj_med / 3.5, -2.0, 2.0)

    if pnl_pct <= -15:
        score -= 0.8
    elif pnl_pct >= 15:
        score += 0.4

    if setup_quality is not None:
        score += _clip((setup_quality - 50.0) / 18.0, -1.6, 1.6)
    if company_quality is not None:
        score += _clip((company_quality - 50.0) / 22.0, -1.1, 1.1)
    if macro_quality is not None:
        score += _clip((macro_quality - 50.0) / 28.0, -0.6, 0.6)
    if confidence is not None:
        score *= 0.55 + 0.45 * _clip(confidence / 100.0, 0.0, 1.0)

    return round(score, 2)


def _holding_action(score: float, recommendation: str, history: dict) -> tuple[str, str, str]:
    history_state = _history_alignment(history)

    if recommendation == "STOP LOSS" or score <= -4.0:
        return "Wyjście / szybka wymiana", "high", "Kapitał jest blokowany w słabym układzie; szukaj szybkiej rotacji lub cięcia pozycji."
    if history_state == "weak" and score < 0.0:
        return "Rozważ wymianę", "medium", "Sygnał techniczny wygląda jeszcze przyzwoicie, ale historia 1M i podobne ścieżki od zakupu nie potwierdzają dokładania kapitału."
    if recommendation in {"SPRZEDAJ", "ROZWAŻ SPRZEDAŻ"} or score <= -1.5:
        return "Rozważ wymianę", "medium", "Układ techniczny i rozwój od zakupu nie dają dziś przewagi względem lepszych setupów."
    if (recommendation == "DOKUP WIĘCEJ" and score >= 1.5 and history_state != "weak") or (score >= 3.0 and history_state == "supportive"):
        return "Można wzmacniać", "low", "Pozycja nadal ma wsparcie techniczne i historyczny kontekst sprzyjający dalszemu ruchowi."
    if recommendation == "DOKUP WIĘCEJ" and history_state == "weak":
        return "Trzymaj / obserwuj", "low", "Technika wygląda przyzwoicie, ale historia 1M nie potwierdza jeszcze dokładania kapitału."
    return "Trzymaj / obserwuj", "low", "Pozycja nie wymaga natychmiastowej akcji, ale warto pilnować momentu i jakości setupu."


def _display_action(action_label: str, recommendation: str, history_state: str) -> tuple[str, str]:
    action = str(action_label or "Trzymaj / obserwuj")
    rec = str(recommendation or "TRZYMAJ").upper()

    if action == "Wyjście / szybka wymiana":
        if rec == "STOP LOSS":
            return (
                "Rozważ szybkie wyjście",
                "Tu oba sygnały są zgodne: sama analiza pozycji i kontekst portfela sugerują szybkie cięcie albo rotację kapitału.",
            )
        if rec in {"SPRZEDAJ", "ROZWAŻ SPRZEDAŻ"}:
            return (
                "Rozważ sprzedaż lub szybką wymianę",
                "Pozycja wygląda słabo sama w sobie, a dodatkowo przegrywa dziś z lepszymi miejscami na kapitał.",
            )
        return (
            "Rozważ szybką wymianę",
            "To nie musi być paniczna sprzedaż, ale ta pozycja słabo broni dziś miejsca w portfelu.",
        )

    if action == "Rozważ wymianę":
        if rec in {"SPRZEDAJ", "ROZWAŻ SPRZEDAŻ"}:
            return (
                "Rozważ sprzedaż lub wymianę",
                "Sama analiza pozycji jest słabsza, a na tle innych okazji w portfelu też nie widać przewagi.",
            )
        if rec == "DOKUP WIĘCEJ" or history_state == "weak":
            return (
                "Trzymaj, ale bez dokładania",
                "Technika może jeszcze wyglądać przyzwoicie, ale historia podobnych wejść i ścieżek nie potwierdza dziś dokładania kapitału.",
            )
        return (
            "Trzymaj, ale rozważ wymianę",
            "To nie jest twardy sygnał sprzedaży. Po prostu na tle innych okazji ta pozycja wygląda dziś słabiej niż część portfela.",
        )

    if action == "Można wzmacniać":
        if rec == "TRZYMAJ":
            return (
                "Trzymaj, można wzmacniać",
                "Bazowy sygnał nie wymusza agresywnego kupna, ale cały kontekst pozycji nadal wspiera dokładanie przy sensownym momencie.",
            )
        return (
            "Trzymaj, można dokupić",
            "To jedna z mocniejszych pozycji: sama analiza i historia nadal dają przestrzeń do wzmacniania.",
        )

    if rec == "DOKUP WIĘCEJ":
        return (
            "Trzymaj, ale jeszcze bez dokładania",
            "Sama technika wygląda nieźle, ale sygnał nie jest jeszcze na tyle czysty, żeby dokładać kapitał bez zastrzeżeń.",
        )
    if rec in {"SPRZEDAJ", "ROZWAŻ SPRZEDAŻ"}:
        return (
            "Trzymaj ostrożnie i obserwuj",
            "Nie ma jeszcze jasnego sygnału do wyjścia, ale ta pozycja wymaga bliższego pilnowania niż reszta portfela.",
        )
    return (
        "Trzymaj i obserwuj",
        "Na dziś bez wyraźnego ruchu w jedną stronę. Pozycja nie wymaga pilnej decyzji, ale warto ją monitorować.",
    )


def _build_holding_note(analysis: dict, history: dict, action_label: str) -> str:
    recommendation = str(analysis.get("recommendation") or "TRZYMAJ")
    ticker = analysis.get("ticker") or "—"
    pnl_pct = _safe_float(analysis.get("pnl_pct")) or 0.0
    entry_med = history.get("entry_median_1m")
    traj_med = history.get("trajectory_median_1m")
    traj_win = history.get("trajectory_win_rate_1m")

    if action_label == "Wyjście / szybka wymiana":
        return (
            f"{ticker} ma dziś układ bardziej defensywny niż ofensywny. Rekomendacja bazowa to {recommendation.lower()}, "
            f"a od zakupu pozycja pokazuje {pnl_pct:+.1f}% i słaby profil kontynuacji."
        )

    if action_label == "Rozważ wymianę":
        detail = []
        if traj_win is not None:
            detail.append(f"podobne ścieżki od zakupu dawały około {traj_win:.0f}% trafień na 1M")
        if traj_med is not None:
            detail.append(f"mediana dalszego ruchu z podobnych ścieżek to {traj_med:+.1f}%")
        elif entry_med is not None:
            detail.append(f"podobne setupy techniczne dawały około {entry_med:+.1f}% mediany na 1M")
        detail_txt = "; ".join(detail) if detail else "historyczna przewaga nie jest dziś wyraźna"
        return f"{ticker} nadaje się do przeglądu pod kątem rotacji. {detail_txt}."

    if action_label == "Można wzmacniać":
        detail = []
        entry_win = history.get("entry_win_rate_1m")
        if entry_med is not None and entry_med > 0 and (entry_win is None or entry_win >= 50):
            detail.append(f"podobne setupy dawały około {entry_med:+.1f}% mediany na 1M")
        if traj_med is not None and traj_med > 0 and (traj_win is None or traj_win >= 50):
            detail.append(f"podobne ścieżki od zakupu sugerują {traj_med:+.1f}%")
        detail_txt = "; ".join(detail) if detail else "technika jest po stronie pozycji, ale historię 1M nadal warto monitorować"
        return f"{ticker} wygląda jak kandydat do utrzymania lub dokładania. {detail_txt}."

    return f"{ticker} nie daje dziś mocnego sygnału do agresywnej zmiany. Pilnuj jakości setupu i relacji do lepszych okazji."


def _summarize_holding(analysis: dict) -> dict:
    recommendation = str(analysis.get("recommendation") or "TRZYMAJ").upper()
    history = _history_context(analysis.get("entry_backtest") or {}, analysis.get("trajectory_backtest") or {})
    score = _holding_score(analysis)
    action_label, priority, action_note = _holding_action(score, recommendation, history)
    display_action, display_action_note = _display_action(action_label, recommendation, _history_alignment(history))
    positives = _top_reason_titles(analysis, bad=False, limit=3)
    watchouts = _top_reason_titles(analysis, bad=True, limit=3)

    return {
        "ticker": analysis.get("ticker"),
        "name": analysis.get("name") or analysis.get("ticker"),
        "currency": analysis.get("currency") or "USD",
        "recommendation": recommendation,
        "action_label": action_label,
        "display_action": display_action,
        "display_action_note": display_action_note,
        "priority": priority,
        "priority_rank": {"high": 0, "medium": 1, "low": 2}.get(priority, 2),
        "action_note": action_note,
        "summary": _build_holding_note(analysis, history, action_label),
        "quality_score": score,
        "pnl_pct": round(_safe_float(analysis.get("pnl_pct")) or 0.0, 2),
        "pnl_abs": round(_safe_float(analysis.get("pnl_abs")) or 0.0, 2),
        "current_value": round(_safe_float(analysis.get("current_value")) or 0.0, 2),
        "entry_price": round(_safe_float(analysis.get("avg_price")) or 0.0, 2),
        "price": round(_safe_float(analysis.get("price")) or 0.0, 2),
        "setup_quality_score": round(_safe_float(analysis.get("setup_quality_score")) or 0.0, 1),
        "company_quality_score": round(_safe_float(analysis.get("company_quality_score")) or 0.0, 1),
        "macro_quality_score": round(_safe_float(analysis.get("macro_quality_score")) or 0.0, 1),
        "confidence_score": round(_safe_float((analysis.get("signal_confidence") or {}).get("score")) or 0.0, 1),
        "confidence_label": str((analysis.get("signal_confidence") or {}).get("label") or "Niska"),
        "confidence_note": str((analysis.get("signal_confidence") or {}).get("note") or ""),
        "history": history,
        "history_alignment": _history_alignment(history),
        "positives": positives,
        "watchouts": watchouts,
        "reasons": analysis.get("reasons") or [],
    }


def _opportunity_score(opportunity: dict, profile: str = "balanced") -> float:
    return float(get_opportunity_rank_score(opportunity, "all", profile))


def _summarize_opportunity(opportunity: dict, previous: dict | None = None, profile: str = "balanced") -> dict:
    details = opportunity.get("details") or {}
    rank_breakdown = get_opportunity_rank_breakdown(opportunity, "all", profile)
    win_1m = _metric_1m(details, "win_rate")
    avg_1m = _metric_1m(details, "avg_return")
    ev_1m = _metric_1m(details, "ev")
    win_3m = _safe_float(details.get("win_rate_66d"))
    avg_3m = _safe_float(details.get("avg_return_66d"))
    summary = (
        f"Setup skanera daje około {(win_1m or 0.0):.0f}% trafień "
        f"na 1M przy średnim ruchu {(avg_1m or 0.0):+.1f}%."
    )
    if win_3m is not None and avg_3m is not None:
        summary += f" Na 3M podobne setupy dawały około {win_3m:.0f}% i {avg_3m:+.1f}%."
    return {
        "ticker": opportunity.get("ticker"),
        "currency": opportunity.get("currency") or details.get("currency") or "USD",
        "score": _opportunity_score(opportunity, profile),
        "scanner_score": int(opportunity.get("score", 0) or 0),
        "rank_score": round(_pick_metric(opportunity.get("rank_score"), rank_breakdown.get("score"), 0.0) or 0.0, 3),
        "win_rate_1m": round(win_1m or 0.0, 1),
        "avg_return_1m": round(avg_1m or 0.0, 2),
        "ev_1m": round(ev_1m or 0.0, 3),
        "win_rate_3m": round(win_3m or 0.0, 1),
        "avg_return_3m": round(avg_3m or 0.0, 2),
        "samples_1m": int(details.get("n_setups", 0) or 0),
        "setup_quality_score": round(_safe_float(rank_breakdown.get("setup_quality_score")) or 0.0, 1),
        "company_quality_score": round(_safe_float(rank_breakdown.get("company_quality_score")) or 0.0, 1),
        "macro_quality_score": round(_safe_float(rank_breakdown.get("macro_quality_score")) or 0.0, 1),
        "confidence_score": round(_safe_float((rank_breakdown.get("confidence") or {}).get("score")) or 0.0, 1),
        "confidence_label": str((rank_breakdown.get("confidence") or {}).get("label") or "Niska"),
        "confidence_note": str((rank_breakdown.get("confidence") or {}).get("reason") or ""),
        "macro_score": int(_pick_metric(details.get("macro_score"), opportunity.get("macro_score"), 50.0) or 50.0),
        "company_score": int(_pick_metric(details.get("company_score"), opportunity.get("company_score"), 50.0) or 50.0),
        "price": round(_pick_metric(details.get("price"), 0.0) or 0.0, 2),
        "summary": summary,
        "profile": profile,
        "profile_label": str(rank_breakdown.get("profile_label") or "Zbalansowany"),
        "rank_note": describe_opportunity_rank_advantage(opportunity, previous, "all", profile),
    }


def _build_replacement_ideas(holdings_summary: list[dict], opportunities: list[dict], current_tickers: set[str]) -> list[dict]:
    ideas: list[dict] = []
    used_replacements: set[str] = set()

    weak_holdings = [
        item for item in holdings_summary
        if item.get("action_label") in {"Wyjście / szybka wymiana", "Rozważ wymianę"}
    ]
    weak_holdings.sort(key=lambda item: (item.get("priority_rank", 2), item.get("quality_score", 0.0)))

    for holding in weak_holdings:
        current_hist = holding.get("history") or {}
        current_traj = _pick_metric(current_hist.get("trajectory_median_1m"), current_hist.get("entry_median_1m"), -99.0) or -99.0
        current_win = _pick_metric(current_hist.get("trajectory_win_rate_1m"), current_hist.get("entry_win_rate_1m"), 0.0) or 0.0
        preferred_currency = holding.get("currency")

        ordered_pool = sorted(
            opportunities,
            key=lambda item: (
                0 if item.get("currency") == preferred_currency else 1,
                -(item.get("score") or 0.0),
            ),
        )

        for candidate in ordered_pool:
            ticker = candidate.get("ticker")
            if not ticker or ticker in current_tickers or ticker in used_replacements:
                continue

            candidate_win = _pick_metric(candidate.get("win_rate_1m"), 0.0) or 0.0
            candidate_avg = _pick_metric(candidate.get("avg_return_1m"), 0.0) or 0.0
            candidate_confidence = _pick_metric(candidate.get("confidence_score"), 0.0) or 0.0

            if candidate_win < max(58.0, current_win + 8.0):
                continue
            if candidate_avg < max(3.0, current_traj + 2.5):
                continue
            if candidate_confidence < 45.0:
                continue

            used_replacements.add(ticker)
            ideas.append({
                "sell_ticker": holding.get("ticker"),
                "sell_currency": holding.get("currency"),
                "sell_action": holding.get("display_action") or holding.get("action_label"),
                "sell_summary": holding.get("summary"),
                "buy_ticker": ticker,
                "buy_currency": candidate.get("currency"),
                "buy_summary": candidate.get("summary"),
                "buy_score": candidate.get("score"),
                "reason": (
                    f"{holding.get('ticker')} ma słabszy profil dalszego ruchu, a {ticker} przechodzi skaner okazji z "
                    f"trafnością około {candidate_win:.0f}% i średnim ruchem {candidate_avg:+.1f}% na 1M."
                ),
            })
            break

    return ideas


def build_portfolio_suggestions(
    holdings: list[dict],
    opportunity_results: list[dict] | None = None,
    opportunity_profile: str = "balanced",
) -> dict:
    analyses: list[dict] = []
    skipped: list[str] = []

    for holding in holdings:
        try:
            analysis = analyze_holding(holding)
        except Exception:
            analysis = None
        if analysis:
            analyses.append(analysis)
        else:
            skipped.append(str(holding.get("ticker") or "—"))

    holdings_summary = [_summarize_holding(analysis) for analysis in analyses]
    holdings_summary.sort(
        key=lambda item: (
            item.get("priority_rank", 2),
            _REC_PRIORITY.get(item.get("recommendation") or "TRZYMAJ", 99),
            item.get("quality_score", 0.0),
        )
    )

    current_tickers = {str(holding.get("ticker") or "").upper() for holding in holdings}
    opportunities_summary = []
    if opportunity_results is not None:
        filtered_opportunities = []
        for opportunity in opportunity_results:
            ticker = str(opportunity.get("ticker") or "").upper()
            if not ticker or ticker in current_tickers:
                continue
            if not _is_showcase_opportunity(opportunity, opportunity_profile):
                continue
            filtered_opportunities.append(opportunity)
        filtered_opportunities.sort(key=lambda item: _opportunity_score(item, opportunity_profile), reverse=True)
        opportunities_summary = [
            _summarize_opportunity(
                opportunity,
                previous=filtered_opportunities[idx - 1] if idx > 0 else None,
                profile=opportunity_profile,
            )
            for idx, opportunity in enumerate(filtered_opportunities)
        ]

    replacements = _build_replacement_ideas(holdings_summary, opportunities_summary, current_tickers) if opportunities_summary else []

    urgent = [item for item in holdings_summary if item.get("priority") == "high"]
    review = [item for item in holdings_summary if item.get("action_label") == "Rozważ wymianę"]
    strengthen = [item for item in holdings_summary if item.get("action_label") == "Można wzmacniać"]

    overview = []
    if urgent:
        overview.append(f"{len(urgent)} pozycj{'a' if len(urgent) == 1 else 'e' if len(urgent) < 5 else 'i'} wymaga szybkiej decyzji lub rotacji kapitału.")
    if review:
        overview.append(f"{len(review)} pozycj{'a' if len(review) == 1 else 'e' if len(review) < 5 else 'i'} wygląda dziś słabiej niż reszta portfela i nadaje się do porównania z okazjami.")
    if strengthen:
        overview.append(f"{len(strengthen)} pozycj{'a' if len(strengthen) == 1 else 'e' if len(strengthen) < 5 else 'i'} nadal ma sensowny setup do trzymania lub wzmacniania.")
    if opportunity_results is None:
        overview.append("Aby zasugerować realne zamiany pozycji, uruchom skan okazji z tej zakładki.")
    elif not opportunities_summary:
        overview.append("Skan okazji nie znalazł dziś lepszych kandydatów poza tym, co już masz w portfelu.")
    elif replacements:
        overview.append(f"Znaleziono {len(replacements)} sensownych pomysłów na wymianę słabszych pozycji na mocniejsze setupy.")
    else:
        overview.append("Skan okazji działa, ale obecnie nie widać oczywistej wymiany, która dawałaby wyraźną przewagę nad obecnymi pozycjami.")

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "overview": overview,
        "holdings": holdings_summary,
        "replacements": replacements,
        "opportunities": opportunities_summary[:8],
        "opportunity_scan": {
            "loaded": opportunity_results is not None,
            "count": len(opportunities_summary),
            "universe_count": len(opportunity_results or []),
            "profile": opportunity_profile,
        },
        "coverage": {
            "evaluated": len(holdings_summary),
            "skipped": skipped,
        },
    }