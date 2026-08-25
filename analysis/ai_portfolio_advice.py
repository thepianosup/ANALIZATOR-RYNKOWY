"""analysis/ai_portfolio_advice.py — External AI advice for portfolio review."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import requests


AI_ADVICE_SETTINGS_FILE = Path.home() / ".analizator_rynkowy" / "ai_advice_settings.json"

AI_PROVIDERS = [
    ("openai", "OpenAI"),
    ("gemini", "Gemini"),
    ("other", "Inny / OpenAI-compatible"),
]

OPENAI_MODELS = [
    "gpt-5.5",
    "gpt-5.5-pro",
    "gpt-5.4",
    "gpt-5.4-pro",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.3-chat-latest",
    "gpt-5.2",
    "gpt-5.2-pro",
    "gpt-5.1",
    "gpt-5",
    "gpt-5-pro",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "o4-mini",
]

GEMINI_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-3-pro",
    "gemini-3-flash",
    "gemini-3.1-flash-lite",
]

DEFAULT_AI_SETTINGS = {
    "provider": "openai",
    "token": "",
    "model": OPENAI_MODELS[0],
    "custom_model": "",
    "base_url": "",
}


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


def _ratio_to_pct(value, digits: int = 1) -> float | None:
    number = _safe_float(value)
    if number is None:
        return None
    return round(number * 100.0, digits)


def _upside_pct(target_price, current_price, digits: int = 1) -> float | None:
    target = _safe_float(target_price)
    current = _safe_float(current_price)
    if target is None or current is None or current <= 0:
        return None
    return round((target - current) / current * 100.0, digits)


def _compact_money_rows(items: dict[str, float]) -> list[dict]:
    rows = []
    for currency, value in sorted(items.items()):
        rows.append({"currency": currency, "value": round(float(value), 2)})
    return rows


def _truncate_text(value, limit: int = 220) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def get_provider_models(provider: str) -> list[str]:
    provider_key = str(provider or "").strip().lower()
    if provider_key == "gemini":
        return list(GEMINI_MODELS)
    if provider_key == "openai":
        return list(OPENAI_MODELS)
    return []


def normalize_ai_settings(settings: dict | None = None) -> dict:
    merged = dict(DEFAULT_AI_SETTINGS)
    merged.update(settings or {})

    provider = str(merged.get("provider") or "openai").strip().lower()
    if provider not in {"openai", "gemini", "other"}:
        provider = "openai"
    merged["provider"] = provider
    merged["token"] = str(merged.get("token") or "").strip()
    merged["custom_model"] = str(merged.get("custom_model") or "").strip()
    merged["base_url"] = str(merged.get("base_url") or "").strip().rstrip("/")

    allowed_models = get_provider_models(provider)
    if provider in {"openai", "gemini"}:
        preferred = str(merged.get("model") or "").strip()
        merged["model"] = preferred if preferred in allowed_models else (allowed_models[0] if allowed_models else "")
    else:
        merged["model"] = str(merged.get("model") or merged.get("custom_model") or "").strip()

    return merged


def load_ai_advice_settings() -> dict:
    try:
        if AI_ADVICE_SETTINGS_FILE.exists():
            with open(AI_ADVICE_SETTINGS_FILE, "r", encoding="utf-8") as handle:
                return normalize_ai_settings(json.load(handle))
    except Exception:
        pass
    return normalize_ai_settings()


def save_ai_advice_settings(settings: dict) -> dict:
    normalized = normalize_ai_settings(settings)
    AI_ADVICE_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(AI_ADVICE_SETTINGS_FILE, "w", encoding="utf-8") as handle:
        json.dump(normalized, handle, indent=2, ensure_ascii=False)
    return normalized


def resolve_ai_model(settings: dict) -> str:
    normalized = normalize_ai_settings(settings)
    if normalized["provider"] == "other":
        return normalized.get("custom_model") or normalized.get("model") or ""
    return normalized.get("model") or ""


def build_portfolio_ai_context(holdings: list[dict]) -> dict:
    from analysis.portfolio_analysis import analyze_holding
    from data.fetcher import get_company_news, get_info

    analyzed = []
    invested_by_currency: dict[str, float] = {}
    current_by_currency: dict[str, float] = {}
    pnl_by_currency: dict[str, float] = {}
    recommendation_counts: dict[str, int] = {}

    for holding in holdings:
        analysis = analyze_holding(holding)
        if not analysis:
            continue

        currency = str(analysis.get("currency") or holding.get("currency") or "USD").upper() or "USD"
        invested = float(analysis.get("invested") or 0.0)
        current_value = float(analysis.get("current_value") or 0.0)
        pnl_abs = float(analysis.get("pnl_abs") or 0.0)
        invested_by_currency[currency] = invested_by_currency.get(currency, 0.0) + invested
        current_by_currency[currency] = current_by_currency.get(currency, 0.0) + current_value
        pnl_by_currency[currency] = pnl_by_currency.get(currency, 0.0) + pnl_abs

        recommendation = str(analysis.get("recommendation") or "TRZYMAJ")
        recommendation_counts[recommendation] = recommendation_counts.get(recommendation, 0) + 1

        macro = analysis.get("macro") or {}
        company = analysis.get("company") or {}
        info = get_info(str(analysis.get("ticker") or ""))
        news_items = get_company_news(analysis.get("ticker", ""), limit=5)
        target_mean_price = _safe_float(info.get("target_mean_price"))

        analyzed.append({
            "ticker": analysis.get("ticker"),
            "name": analysis.get("name"),
            "currency": currency,
            "shares": round(float(analysis.get("shares") or 0.0), 4),
            "avg_price": round(float(analysis.get("avg_price") or 0.0), 4),
            "price": round(float(analysis.get("price") or 0.0), 4),
            "pnl_pct": round(float(analysis.get("pnl_pct") or 0.0), 2),
            "pnl_abs": round(pnl_abs, 2),
            "current_value": round(current_value, 2),
            "invested": round(invested, 2),
            "purchase_date": analysis.get("purchase_date"),
            "recommendation": recommendation,
            "recommendation_color": analysis.get("rec_color"),
            "setup_quality_score": round(float(analysis.get("setup_quality_score") or 0.0), 1),
            "company_quality_score": round(float(analysis.get("company_quality_score") or 0.0), 1),
            "macro_quality_score": round(float(analysis.get("macro_quality_score") or 0.0), 1),
            "signal_confidence": analysis.get("signal_confidence") or {},
            "matches_new_entry_setup": bool(analysis.get("matches_opportunity_setup", True)),
            "notes": analysis.get("notes") or "",
            "technical": {
                "change_1d": _safe_float(analysis.get("change_1d"), 0.0),
                "change_5d": _safe_float(analysis.get("change_5d"), 0.0),
                "change_1m": _safe_float(analysis.get("change_1m"), 0.0),
                "drawdown_pct": _safe_float(analysis.get("drawdown_pct"), 0.0),
                "rsi14": _safe_float(analysis.get("rsi14")),
                "macd_hist": _safe_float(analysis.get("macd_hist")),
                "stoch_k": _safe_float(analysis.get("stoch_k")),
                "stoch_d": _safe_float(analysis.get("stoch_d")),
                "roc20": _safe_float(analysis.get("roc20")),
                "sma20": _safe_float(analysis.get("sma20")),
                "sma50": _safe_float(analysis.get("sma50")),
                "sma200": _safe_float(analysis.get("sma200")),
                "atr14": _safe_float(analysis.get("atr14")),
                "atr_pct": _safe_float(analysis.get("atr_pct")),
                "bb_position": _safe_float(analysis.get("bb_position")),
                "vol_ratio": _safe_float(analysis.get("vol_ratio")),
                "williams_r": _safe_float(analysis.get("williams_r")),
                "adx14": _safe_float(analysis.get("adx14")),
            },
            "fundamentals": {
                "sector": info.get("sector") or "—",
                "industry": info.get("industry") or "—",
                "market_cap": _safe_float(info.get("market_cap")),
                "pe_ratio": _safe_float(info.get("pe_ratio")),
                "forward_pe": _safe_float(info.get("forward_pe")),
                "eps": _safe_float(info.get("eps")),
                "forward_eps": _safe_float(info.get("forward_eps")),
                "dividend_yield_pct": _ratio_to_pct(info.get("dividend_yield")),
                "revenue_growth_pct": _ratio_to_pct(info.get("revenue_growth")),
                "earnings_growth_pct": _ratio_to_pct(info.get("earnings_growth")),
                "profit_margin_pct": _ratio_to_pct(info.get("profit_margin")),
                "operating_margin_pct": _ratio_to_pct(info.get("operating_margin")),
                "gross_margin_pct": _ratio_to_pct(info.get("gross_margin")),
                "roe_pct": _ratio_to_pct(info.get("roe")),
                "debt_to_equity": _safe_float(info.get("debt_to_equity")),
                "current_ratio": _safe_float(info.get("current_ratio")),
                "quick_ratio": _safe_float(info.get("quick_ratio")),
                "free_cashflow": _safe_float(info.get("free_cashflow")),
                "operating_cashflow": _safe_float(info.get("operating_cashflow")),
                "recommendation_key": info.get("recommendation_key"),
                "target_mean_price": target_mean_price,
                "target_upside_pct": _upside_pct(target_mean_price, analysis.get("price")),
                "analyst_count": _safe_int(info.get("analyst_count")),
            },
            "entry_backtest": analysis.get("entry_backtest") or {},
            "trajectory_backtest": analysis.get("trajectory_backtest") or {},
            "entry_signal": analysis.get("entry_signal") or {},
            "trajectory_signal": analysis.get("trajectory_signal") or {},
            "macro": {
                "score": macro.get("score"),
                "stance": macro.get("stance"),
                "summary": macro.get("summary"),
                "components": macro.get("components") or [],
            },
            "company": {
                "score": company.get("score"),
                "financial_score": company.get("financial_score"),
                "event_score": company.get("event_score"),
                "stance": company.get("stance"),
                "summary": company.get("summary"),
                "components": company.get("components") or [],
                "events": company.get("events") or [],
            },
            "reasons": [
                {
                    "title": item.get("title", ""),
                    "desc": item.get("desc", ""),
                    "bad": bool(item.get("bad", False)),
                }
                for item in (analysis.get("reasons") or [])
            ],
            "recent_news": news_items,
        })

    analyzed.sort(key=lambda item: abs(float(item.get("current_value") or 0.0)), reverse=True)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "portfolio_summary": {
            "positions": len(analyzed),
            "invested_by_currency": _compact_money_rows(invested_by_currency),
            "current_value_by_currency": _compact_money_rows(current_by_currency),
            "open_pnl_by_currency": _compact_money_rows(pnl_by_currency),
            "recommendation_counts": recommendation_counts,
        },
        "holdings": analyzed,
    }


def _build_system_prompt() -> str:
    return (
        "Jestes doswiadczonym analitykiem portfela akcji. Oceniasz pozycje po polsku, konkretnie i bez marketingowego stylu. "
        "Jesli masz narzedzia do wyszukiwania w internecie, uzyj ich do sprawdzenia najnowszych informacji o spolkach, sektorach, wynikach, guidance, makro i ryzykach. "
        "Jesli zewnetrzne przeszukanie internetu nie jest dostepne w tej sesji, napisz to wprost na poczatku odpowiedzi. "
        "Twoja odpowiedz ma byc praktyczna: co trzymac, co obserwowac, gdzie rozwazyc dokupowanie, gdzie uwazac, a gdzie ograniczac pozycje lub rozwazyc sprzedaz. "
        "Nie uciekaj w ogolniki. Dla kazdej pozycji podaj decyzje i 2-4 najwazniejsze argumenty. "
        "Opieraj decyzje przede wszystkim na danych spolki: trendzie przychodow i zyskow, marzach, cash flow, bilansie, zadluzeniu, plynnosci, wycenie, komentarzach zarzadu, decyzjach spolek, wynikach kwartalnych, guidance oraz newsflow. "
        "Dane techniczne traktuj tylko jako drugorzedne tlo i potwierdzenie, a nie glowny powod decyzji. Nie buduj werdyktu glownie z RSI, MACD, stoch czy krotkiego price action. "
        "Pisz bardzo przystepnie: najpierw najwazniejszy wniosek w prostym zdaniu, dopiero potem argumenty. "
        "Ukladaj odpowiedz od najwazniejszych i najbardziej pilnych spolek do mniej waznych. Za najwazniejsze uznawaj pozycje z najwiekszym wplywem na portfel albo wymagajace decyzji. "
        "Kazda wazna informacja, wydarzenie lub news ma byc oceniona jawnie przez dwa pola: impact = POZYTYWNA / NEGATYWNA / NEUTRALNA oraz price_reaction = WZROST / SPADEK / MIESZANY. "
        "Masz zwrocic tylko jeden poprawny obiekt JSON, bez markdownu, bez tabel, bez kodu, bez komentarza przed lub po JSON i bez zrodel. "
        "Jesli cos jest niepewne, wpisz to w polu why albo summary, ale nadal podaj najbardziej prawdopodobny kierunek reakcji rynku."
    )


def _build_prompt_context(context: dict) -> dict:
    prompt_holdings = []
    for item in context.get("holdings") or []:
        signal_confidence = item.get("signal_confidence") or {}
        company = item.get("company") or {}
        macro = item.get("macro") or {}
        fundamentals = item.get("fundamentals") or {}
        technical = item.get("technical") or {}
        price = _safe_float(item.get("price"))
        sma50 = _safe_float(technical.get("sma50"))
        sma200 = _safe_float(technical.get("sma200"))

        prompt_holdings.append({
            "ticker": item.get("ticker"),
            "name": item.get("name"),
            "currency": item.get("currency"),
            "shares": item.get("shares"),
            "avg_price": item.get("avg_price"),
            "price": item.get("price"),
            "current_value": item.get("current_value"),
            "invested": item.get("invested"),
            "pnl_pct": item.get("pnl_pct"),
            "pnl_abs": item.get("pnl_abs"),
            "purchase_date": item.get("purchase_date"),
            "recommendation": item.get("recommendation"),
            "matches_new_entry_setup": item.get("matches_new_entry_setup"),
            "scores": {
                "setup": item.get("setup_quality_score"),
                "company": item.get("company_quality_score"),
                "macro": item.get("macro_quality_score"),
                "confidence": {
                    "score": signal_confidence.get("score"),
                    "label": signal_confidence.get("label"),
                },
            },
            "fundamentals": {
                "sector": fundamentals.get("sector"),
                "industry": fundamentals.get("industry"),
                "market_cap": fundamentals.get("market_cap"),
                "pe_ratio": fundamentals.get("pe_ratio"),
                "forward_pe": fundamentals.get("forward_pe"),
                "eps": fundamentals.get("eps"),
                "forward_eps": fundamentals.get("forward_eps"),
                "dividend_yield_pct": fundamentals.get("dividend_yield_pct"),
                "revenue_growth_pct": fundamentals.get("revenue_growth_pct"),
                "earnings_growth_pct": fundamentals.get("earnings_growth_pct"),
                "profit_margin_pct": fundamentals.get("profit_margin_pct"),
                "operating_margin_pct": fundamentals.get("operating_margin_pct"),
                "gross_margin_pct": fundamentals.get("gross_margin_pct"),
                "roe_pct": fundamentals.get("roe_pct"),
                "debt_to_equity": fundamentals.get("debt_to_equity"),
                "current_ratio": fundamentals.get("current_ratio"),
                "quick_ratio": fundamentals.get("quick_ratio"),
                "free_cashflow": fundamentals.get("free_cashflow"),
                "operating_cashflow": fundamentals.get("operating_cashflow"),
                "recommendation_key": fundamentals.get("recommendation_key"),
                "target_mean_price": fundamentals.get("target_mean_price"),
                "target_upside_pct": fundamentals.get("target_upside_pct"),
                "analyst_count": fundamentals.get("analyst_count"),
            },
            "technical_secondary": {
                "change_1m": technical.get("change_1m"),
                "drawdown_pct": technical.get("drawdown_pct"),
                "price_vs_sma50_pct": _upside_pct(price, sma50),
                "price_vs_sma200_pct": _upside_pct(price, sma200),
            },
            "company": {
                "score": company.get("score"),
                "stance": company.get("stance"),
                "summary": _truncate_text(company.get("summary"), 180),
                "components": [_truncate_text(component, 120) for component in (company.get("components") or [])[:5]],
                "events": [
                    {
                        "title": _truncate_text(event.get("title"), 120),
                        "signal": event.get("signal"),
                        "score": event.get("score"),
                    }
                    for event in (company.get("events") or [])[:3]
                ],
            },
            "macro": {
                "score": macro.get("score"),
                "stance": macro.get("stance"),
                "summary": _truncate_text(macro.get("summary"), 180),
            },
            "reasons": [
                {
                    "title": _truncate_text(reason.get("title"), 90),
                    "bad": reason.get("bad"),
                    "desc": _truncate_text(reason.get("desc"), 140),
                }
                for reason in (item.get("reasons") or [])[:4]
            ],
            "recent_news": [
                {
                    "title": _truncate_text(news.get("title"), 120),
                    "provider": news.get("provider"),
                    "published_at": news.get("published_at"),
                    "summary": _truncate_text(news.get("summary"), 140),
                }
                for news in (item.get("recent_news") or [])[:3]
            ],
        })

    return {
        "generated_at": context.get("generated_at"),
        "portfolio_summary": context.get("portfolio_summary") or {},
        "holdings": prompt_holdings,
    }


def _build_user_prompt(context: dict, *, structured_output: bool = False) -> str:
    compact_context = _build_prompt_context(context)
    if structured_output:
        return (
            "Przeanalizuj portfel na podstawie danych wewnetrznych aplikacji i, jesli narzedzia to wspieraja, dodatkowego przeszukania internetu. "
            "Bierz pod uwage przede wszystkim fundamenty spolki, jakosc biznesu, wyniki, przychody, zyski, marze, cash flow, zadluzenie, plynnosc, guidance, decyzje spolki, analitykow i newsflow. "
            "Technike i zachowanie kursu traktuj tylko jako drugorzedne tlo. "
            "Jesli pozycja nie spelnia dzis setupu nowego wejscia, ale wspiera dalsze dokupowanie jako istniejaca pozycja, wyjasnij to. "
            "Wypelnij caly obiekt zgodnie ze schema. W polach tekstowych pisz po polsku, konkretnie i praktycznie. "
            "W polu summary dla pozycji zacznij od prostego zdania co robic teraz. "
            "Uloz tablice positions od najwazniejszej i najbardziej pilnej pozycji do najmniej waznej. "
            "W positives, risks i watchouts stawiaj glownie na dane spolki, wyniki i wydarzenia. "
            "Jesli nie ma pilnych uwag lub wydarzen, zwroc puste listy zamiast wymyslac tresc. "
            "Ustaw internet_checked=true tylko wtedy, gdy rzeczywiscie wykorzystales aktualne informacje z internetu lub narzedzia web search.\n\n"
            f"Dane portfela (JSON compact):\n{json.dumps(compact_context, ensure_ascii=False, separators=(',', ':'))}"
        )

    return (
        "Przeanalizuj portfel na podstawie danych wewnetrznych aplikacji i, jesli narzedzia to wspieraja, dodatkowego przeszukania internetu. "
        "Bierz pod uwage przede wszystkim fundamenty spolki, jakosc biznesu, wyniki, przychody, zyski, marze, cash flow, zadluzenie, plynnosc, guidance, decyzje spolki, analitykow i newsflow. "
        "Technike i historyczne setupy traktuj tylko jako tlo i potwierdzenie. "
        "Jesli pozycja nie spelnia dzis setupu nowego wejscia, ale wspiera dalsze dokupowanie jako istniejaca pozycja, wyjasnij to. "
        "Pisz bardzo przystepnie i ukladaj pozycje od najwazniejszej do najmniej waznej. "
        "Nie dawaj ogolnego disclaimera inwestycyjnego jako glownej tresci. Skup sie na konkretach.\n\n"
        "Zwroc tylko jeden obiekt JSON w tym schemacie i nie dodawaj nic poza JSON:\n"
        "{\n"
        "  \"internet_checked\": true,\n"
        "  \"internet_summary\": [\n"
        "    {\"topic\": \"...\", \"impact\": \"POZYTYWNA\", \"price_reaction\": \"WZROST\", \"why\": \"...\"}\n"
        "  ],\n"
        "  \"portfolio_overview\": {\n"
        "    \"summary\": \"...\",\n"
        "    \"strongest_positions\": [\"...\"],\n"
        "    \"weakest_positions\": [\"...\"],\n"
        "    \"main_risks\": [\"...\"]\n"
        "  },\n"
        "  \"positions\": [\n"
        "    {\n"
        "      \"ticker\": \"...\",\n"
        "      \"name\": \"...\",\n"
        "      \"verdict\": \"TRZYMAJ\",\n"
        "      \"confidence\": \"WYSOKA\",\n"
        "      \"summary\": \"...\",\n"
        "      \"positives\": [\"...\"],\n"
        "      \"risks\": [\"...\"],\n"
        "      \"watchouts\": [\"...\"],\n"
        "      \"events\": [\n"
        "        {\"title\": \"...\", \"impact\": \"POZYTYWNA\", \"price_reaction\": \"WZROST\", \"why\": \"...\"}\n"
        "      ]\n"
        "    }\n"
        "  ],\n"
        "  \"watch_now\": [\"...\"],\n"
        "  \"urgent_actions\": [\"...\"]\n"
        "}\n\n"
        "Wymagania dodatkowe: \n"
        "- nie podawaj zrodel ani linkow\n"
        "- nie uzywaj markdownu\n"
        "- w polach impact uzywaj tylko: POZYTYWNA / NEGATYWNA / NEUTRALNA\n"
        "- w polach price_reaction uzywaj tylko: WZROST / SPADEK / MIESZANY\n"
        "- w verdict uzywaj tylko: TRZYMAJ / OBSERWUJ / ROZWAZ DOKUPIENIE / OGRANICZ / ROZWAZ SPRZEDAZ\n\n"
        f"Dane portfela (JSON compact):\n{json.dumps(compact_context, ensure_ascii=False, separators=(',', ':'))}"
    )


def _portfolio_ai_output_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "internet_checked": {"type": "boolean"},
            "internet_summary": {
                "type": "array",
                "items": {"$ref": "#/$defs/internet_item"},
            },
            "portfolio_overview": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "strongest_positions": {"type": "array", "items": {"type": "string"}},
                    "weakest_positions": {"type": "array", "items": {"type": "string"}},
                    "main_risks": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["summary", "strongest_positions", "weakest_positions", "main_risks"],
                "additionalProperties": False,
            },
            "positions": {
                "type": "array",
                "items": {"$ref": "#/$defs/position_item"},
            },
            "watch_now": {"type": "array", "items": {"type": "string"}},
            "urgent_actions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["internet_checked", "internet_summary", "portfolio_overview", "positions", "watch_now", "urgent_actions"],
        "additionalProperties": False,
        "$defs": {
            "internet_item": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "impact": {"type": "string", "enum": ["POZYTYWNA", "NEGATYWNA", "NEUTRALNA"]},
                    "price_reaction": {"type": "string", "enum": ["WZROST", "SPADEK", "MIESZANY"]},
                    "why": {"type": "string"},
                },
                "required": ["topic", "impact", "price_reaction", "why"],
                "additionalProperties": False,
            },
            "position_event": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "impact": {"type": "string", "enum": ["POZYTYWNA", "NEGATYWNA", "NEUTRALNA"]},
                    "price_reaction": {"type": "string", "enum": ["WZROST", "SPADEK", "MIESZANY"]},
                    "why": {"type": "string"},
                },
                "required": ["title", "impact", "price_reaction", "why"],
                "additionalProperties": False,
            },
            "position_item": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "name": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["TRZYMAJ", "OBSERWUJ", "ROZWAZ DOKUPIENIE", "OGRANICZ", "ROZWAZ SPRZEDAZ"]},
                    "confidence": {"type": "string", "enum": ["WYSOKA", "SREDNIA", "NISKA"]},
                    "summary": {"type": "string"},
                    "positives": {"type": "array", "items": {"type": "string"}},
                    "risks": {"type": "array", "items": {"type": "string"}},
                    "watchouts": {"type": "array", "items": {"type": "string"}},
                    "events": {"type": "array", "items": {"$ref": "#/$defs/position_event"}},
                },
                "required": ["ticker", "name", "verdict", "confidence", "summary", "positives", "risks", "watchouts", "events"],
                "additionalProperties": False,
            },
        },
    }


def parse_ai_structured_response(text: str) -> dict | None:
    raw = str(text or "").strip()
    if not raw:
        return None

    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None

    candidate = raw[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_openai_text(payload: dict) -> str:
    direct = str(payload.get("output_text") or "").strip()
    if direct:
        return direct

    parts = []
    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            text = str(content.get("text") or "").strip()
            if text:
                parts.append(text)
    if parts:
        return "\n\n".join(parts)

    choices = payload.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            text_parts = []
            for entry in content:
                text = str(entry.get("text") or entry.get("content") or "").strip()
                if text:
                    text_parts.append(text)
            if text_parts:
                return "\n\n".join(text_parts)
    return ""


def _extract_openai_sources(payload: dict) -> list[dict]:
    sources = []
    for item in payload.get("output") or []:
        if item.get("type") == "web_search_call":
            action = item.get("action") or {}
            for source in action.get("sources") or []:
                url = str(source.get("url") or source.get("uri") or "").strip()
                title = str(source.get("title") or url).strip()
                if url:
                    sources.append({"title": title, "uri": url})

        for content in item.get("content") or []:
            for annotation in content.get("annotations") or []:
                if annotation.get("type") != "url_citation":
                    continue
                url = str(annotation.get("url") or "").strip()
                title = str(annotation.get("title") or url).strip()
                if url:
                    sources.append({"title": title, "uri": url})

    unique = []
    seen = set()
    for item in sources:
        key = item["uri"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique[:20]


def _extract_gemini_text(payload: dict) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""
    parts = []
    for part in (candidates[0].get("content") or {}).get("parts") or []:
        text = str(part.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _extract_gemini_sources(payload: dict) -> list[dict]:
    candidates = payload.get("candidates") or []
    if not candidates:
        return []
    grounding = candidates[0].get("groundingMetadata") or {}
    sources = []
    for chunk in grounding.get("groundingChunks") or []:
        web = chunk.get("web") or {}
        uri = str(web.get("uri") or "").strip()
        title = str(web.get("title") or "").strip() or uri
        if uri:
            sources.append({"title": title, "uri": uri})
    unique = []
    seen = set()
    for item in sources:
        key = item["uri"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique[:12]


def _request_json(method: str, url: str, *, headers: dict | None = None, json_body: dict | None = None, timeout: int = 120) -> dict:
    response = requests.request(method, url, headers=headers, json=json_body, timeout=timeout)
    if response.status_code >= 400:
        detail = response.text.strip()
        raise RuntimeError(f"HTTP {response.status_code}: {detail[:800]}")
    return response.json()


def _call_openai_responses(
    model: str,
    token: str,
    system_prompt: str,
    user_prompt: str,
    *,
    with_web_search: bool,
    json_schema: dict | None = None,
) -> dict:
    payload = {
        "model": model,
        "instructions": system_prompt,
        "input": user_prompt,
        "max_output_tokens": 7000 if json_schema else 3200,
        "tool_choice": "auto",
        "store": False,
    }
    if json_schema:
        payload["text"] = {
            "format": {
                "type": "json_schema",
                "name": "portfolio_ai_report",
                "strict": True,
                "schema": json_schema,
            }
        }
    if with_web_search:
        payload["tools"] = [{"type": "web_search", "search_context_size": "medium"}]
        payload["include"] = ["web_search_call.action.sources"]

    return _request_json(
        "POST",
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json_body=payload,
    )


def _call_openai_chat(base_url: str, model: str, token: str, system_prompt: str, user_prompt: str) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    return _request_json(
        "POST",
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json_body=payload,
    )


def _call_openai(model: str, token: str, system_prompt: str, user_prompt: str, *, json_schema: dict | None = None) -> dict:
    errors = []

    try:
        raw = _call_openai_responses(
            model,
            token,
            system_prompt,
            user_prompt,
            with_web_search=True,
            json_schema=json_schema,
        )
        text = _extract_openai_text(raw)
        if text:
            return {"text": text, "sources": _extract_openai_sources(raw), "used_web_search": True}
    except Exception as exc:
        errors.append(f"responses+web_search+schema: {exc}")

    try:
        raw = _call_openai_responses(
            model,
            token,
            system_prompt,
            user_prompt,
            with_web_search=False,
            json_schema=json_schema,
        )
        text = _extract_openai_text(raw)
        if text:
            return {"text": text, "sources": [], "used_web_search": False}
    except Exception as exc:
        errors.append(f"responses+schema: {exc}")

    if json_schema:
        try:
            raw = _call_openai_responses(model, token, system_prompt, user_prompt, with_web_search=True)
            text = _extract_openai_text(raw)
            if text:
                return {"text": text, "sources": _extract_openai_sources(raw), "used_web_search": True}
        except Exception as exc:
            errors.append(f"responses+web_search: {exc}")

        try:
            raw = _call_openai_responses(model, token, system_prompt, user_prompt, with_web_search=False)
            text = _extract_openai_text(raw)
            if text:
                return {"text": text, "sources": [], "used_web_search": False}
        except Exception as exc:
            errors.append(f"responses: {exc}")

    try:
        raw = _call_openai_chat("https://api.openai.com/v1", model, token, system_prompt, user_prompt)
        text = _extract_openai_text(raw)
        if not text:
            raise RuntimeError("OpenAI zwrocil pusta odpowiedz.")
        return {"text": text, "sources": [], "used_web_search": False}
    except Exception as exc:
        errors.append(f"chat_completions: {exc}")

    raise RuntimeError("; ".join(errors) if errors else "OpenAI zwrocil pusta odpowiedz.")


def _call_gemini(model: str, token: str, system_prompt: str, user_prompt: str) -> dict:
    base_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={token}"
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2600},
        "tools": [{"googleSearch": {}}],
    }

    try:
        raw = _request_json("POST", base_url, headers={"Content-Type": "application/json"}, json_body=payload)
    except Exception:
        payload["tools"] = [{"google_search": {}}]
        try:
            raw = _request_json("POST", base_url, headers={"Content-Type": "application/json"}, json_body=payload)
        except Exception:
            payload.pop("tools", None)
            raw = _request_json("POST", base_url, headers={"Content-Type": "application/json"}, json_body=payload)

    text = _extract_gemini_text(raw)
    if not text:
        raise RuntimeError("Gemini zwrocil pusta odpowiedz.")
    return {
        "text": text,
        "sources": _extract_gemini_sources(raw),
        "used_web_search": bool(_extract_gemini_sources(raw)),
    }


def _call_openai_compatible(base_url: str, model: str, token: str, system_prompt: str, user_prompt: str) -> dict:
    if not base_url:
        raise ValueError("Dla dostawcy 'Inny' musisz podac Base URL.")
    raw = _call_openai_chat(base_url, model, token, system_prompt, user_prompt)
    text = _extract_openai_text(raw)
    if not text:
        raise RuntimeError("Zewnetrzny dostawca zwrocil pusta odpowiedz.")
    return {"text": text, "sources": [], "used_web_search": False}


def analyze_portfolio_with_ai(holdings: list[dict], settings: dict) -> dict:
    normalized = normalize_ai_settings(settings)
    token = normalized.get("token") or ""
    if not token:
        raise ValueError("Brak tokena API. Uzupelnij token i zapisz ustawienia.")

    model = resolve_ai_model(normalized)
    if not model:
        raise ValueError("Brak modelu AI. Wybierz model lub wpisz wlasny.")

    context = build_portfolio_ai_context(holdings)
    if not context.get("holdings"):
        raise ValueError("Brak aktywnych pozycji z wystarczajacymi danymi do analizy AI.")

    provider = normalized["provider"]
    system_prompt = _build_system_prompt()

    if provider == "gemini":
        user_prompt = _build_user_prompt(context)
        answer = _call_gemini(model, token, system_prompt, user_prompt)
    elif provider == "other":
        user_prompt = _build_user_prompt(context)
        answer = _call_openai_compatible(normalized.get("base_url") or "", model, token, system_prompt, user_prompt)
    else:
        user_prompt = _build_user_prompt(context, structured_output=True)
        answer = _call_openai(model, token, system_prompt, user_prompt, json_schema=_portfolio_ai_output_schema())

    return {
        "provider": provider,
        "provider_label": dict(AI_PROVIDERS).get(provider, provider),
        "model": model,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "context": context,
        "text": answer.get("text", "").strip(),
        "structured": parse_ai_structured_response(answer.get("text", "")),
        "sources": answer.get("sources") or [],
        "used_web_search": bool(answer.get("used_web_search", False)),
    }


def _build_universe_name_map() -> dict[str, dict]:
    import config

    groups = [
        ("US", config.STOCKS_US_DEFAULT),
        ("EU", config.STOCKS_EU),
        ("ETF", config.ETFS),
        ("PL", config.STOCKS_PL),
        ("IDX", config.INDICES_US + config.INDICES_INTL + config.INDICES_PL),
    ]
    mapping: dict[str, dict] = {}
    for group_label, entries in groups:
        for entry in entries:
            ticker = str(entry.get("ticker") or "").upper().strip()
            if not ticker or ticker in mapping:
                continue
            mapping[ticker] = {
                "name": str(entry.get("name") or ticker).strip() or ticker,
                "group": group_label,
            }
    return mapping


def build_market_ai_context(tickers: list[str]) -> dict:
    from data.fetcher import get_company_news, get_info, get_latest_price

    name_map = _build_universe_name_map()
    instruments = []

    for raw_ticker in tickers:
        ticker = str(raw_ticker or "").upper().strip()
        if not ticker:
            continue

        fallback = name_map.get(ticker) or {"name": ticker, "group": "INNE"}
        try:
            info = get_info(ticker)
        except Exception:
            info = {}

        try:
            news_items = get_company_news(ticker, limit=2)
        except Exception:
            news_items = []

        try:
            live_price = _safe_float(get_latest_price(ticker, force_refresh=False))
        except Exception:
            live_price = None

        target_mean_price = _safe_float(info.get("target_mean_price"))
        instruments.append({
            "ticker": ticker,
            "name": str(info.get("name") or fallback["name"] or ticker).strip() or ticker,
            "group": fallback.get("group") or "INNE",
            "currency": str(info.get("currency") or "USD").upper() or "USD",
            "price": round(live_price, 4) if live_price is not None else None,
            "sector": str(info.get("sector") or "—").strip() or "—",
            "industry": str(info.get("industry") or "—").strip() or "—",
            "market_cap": _safe_float(info.get("market_cap")),
            "beta": _safe_float(info.get("beta")),
            "recommendation_key": str(info.get("recommendation_key") or "").strip(),
            "analyst_count": _safe_int(info.get("analyst_count")),
            "target_upside_pct": _upside_pct(target_mean_price, live_price),
            "revenue_growth_pct": _ratio_to_pct(info.get("revenue_growth")),
            "earnings_growth_pct": _ratio_to_pct(info.get("earnings_growth")),
            "profit_margin_pct": _ratio_to_pct(info.get("profit_margin")),
            "operating_margin_pct": _ratio_to_pct(info.get("operating_margin")),
            "gross_margin_pct": _ratio_to_pct(info.get("gross_margin")),
            "roe_pct": _ratio_to_pct(info.get("roe")),
            "debt_to_equity": _safe_float(info.get("debt_to_equity")),
            "current_ratio": _safe_float(info.get("current_ratio")),
            "quick_ratio": _safe_float(info.get("quick_ratio")),
            "free_cashflow": _safe_float(info.get("free_cashflow")),
            "operating_cashflow": _safe_float(info.get("operating_cashflow")),
            "institutional_ownership_pct": _ratio_to_pct(info.get("held_percent_institutions")),
            "insider_ownership_pct": _ratio_to_pct(info.get("held_percent_insiders")),
            "short_float_pct": _ratio_to_pct(info.get("short_percent_of_float")),
            "recent_news": [
                {
                    "title": _truncate_text(item.get("title"), 100),
                    "provider": str(item.get("provider") or "").strip(),
                    "published_at": str(item.get("published_at") or "").strip(),
                    "summary": _truncate_text(item.get("summary"), 120),
                }
                for item in news_items[:2]
            ],
        })

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "universe_size": len(instruments),
        "instruments": instruments,
    }


def _build_market_scan_system_prompt() -> str:
    return (
        "Jestes analitykiem okazji rynkowych. Masz bezwzgledny zakaz opierania oceny na analizie technicznej. "
        "Nie wolno Ci uzywac RSI, MACD, stoch, srednich kroczacych, price action, formacji swiecowych ani historycznych setupow jako powodow rekomendacji. "
        "Masz przejrzec caly dostarczony wszechswiat instrumentow i, jesli masz narzedzia do wyszukiwania w internecie, sprawdzic aktualny newsflow, wyniki, guidance, kontrakty, M&A, regulacje, filingi, ruchy insiderow, ruchy funduszy, zmiany w pozycjach duzych graczy, buybacki, emisje, opoznienia, pozwy i inne katalizatory. "
        "Szukaj okazji wynikajacych z fundamentow, zmiany narracji, wyraznych katalizatorow i sygnalow od duzych graczy. "
        "Jesli internet nie jest dostepny, napisz to tylko poprzez pole internet_checked=false i nadal ocen instrumenty na podstawie przekazanego kontekstu fundamentalnego i newsowego. "
        "Zwracaj tylko poprawny JSON, bez markdownu, bez komentarza przed lub po JSON. "
        "Wynik ma byc praktyczny: krotka lista najlepszych okazji i nazw do bliskiej obserwacji, z konkretnym uzasadnieniem dlaczego teraz."
    )


def _build_market_scan_user_prompt(context: dict, *, structured_output: bool = False) -> str:
    compact_context = {
        "generated_at": context.get("generated_at"),
        "universe_size": context.get("universe_size"),
        "instruments": context.get("instruments") or [],
    }
    common = (
        "Przejrzyj wszystkie instrumenty z przekazanego uniwersum. "
        "Wybierz maksymalnie 15 nazw, ktore po sprawdzeniu fundamentow i newsflow wygladaja na najlepsze okazje lub wymagaja bliskiej obserwacji. "
        "Werdykt OKAZJA dawaj tylko tam, gdzie widzisz realny katalizator, argument fundamentalny i prawdopodobny trigger zainteresowania rynku. "
        "Werdykt OBSERWUJ dawaj tam, gdzie historia jest jeszcze niepelna albo sygnal jest ciekawy, ale nie dosc mocny. "
        "Jesli ruch duzych graczy, funduszy albo insiderow jest istotny, wpisz go do smart_money. "
        "Nie uzywaj analizy technicznej ani jezyka technicznego. "
        "Kolejnosc opportunities ma isc od najlepszej okazji do najslabszej. "
        "Uzywaj tylko tickerow z dostarczonego uniwersum. "
        "internet_checked ustaw na true tylko wtedy, gdy rzeczywiscie wykorzystales web search lub inne aktualne wyszukiwanie online."
    )

    if structured_output:
        return (
            f"{common} Wypelnij caly obiekt zgodnie ze schema. conviction_score ustawiaj w skali 0-100. "
            "Jesli dla danej spolki brak czytelnych ruchow duzych graczy, smart_money ma byc puste. "
            "Jesli nie widzisz sensownych okazji, zwroc pusta tablice opportunities zamiast dopisywac slabe pomysly.\n\n"
            f"Dane uniwersum (JSON compact):\n{json.dumps(compact_context, ensure_ascii=False, separators=(',', ':'))}"
        )

    return (
        f"{common}\n\n"
        "Zwroc tylko jeden obiekt JSON w tym schemacie i nic poza JSON:\n"
        "{\n"
        "  \"internet_checked\": true,\n"
        "  \"market_view\": {\n"
        "    \"summary\": \"...\",\n"
        "    \"hot_themes\": [\"...\"],\n"
        "    \"risk_themes\": [\"...\"]\n"
        "  },\n"
        "  \"opportunities\": [\n"
        "    {\n"
        "      \"ticker\": \"...\",\n"
        "      \"verdict\": \"OKAZJA\",\n"
        "      \"confidence\": \"WYSOKA\",\n"
        "      \"conviction_score\": 81,\n"
        "      \"summary\": \"...\",\n"
        "      \"why_now\": \"...\",\n"
        "      \"catalysts\": [\"...\"],\n"
        "      \"risks\": [\"...\"],\n"
        "      \"smart_money\": [\n"
        "        {\"title\": \"...\", \"signal\": \"KUPUJE\", \"why\": \"...\"}\n"
        "      ],\n"
        "      \"events\": [\n"
        "        {\"title\": \"...\", \"impact\": \"POZYTYWNA\", \"price_reaction\": \"WZROST\", \"why\": \"...\"}\n"
        "      ]\n"
        "    }\n"
        "  ],\n"
        "  \"watchlist\": [\"...\"]\n"
        "}\n\n"
        "Dopuszczalne wartosci:\n"
        "- verdict: OKAZJA / OBSERWUJ / POMIN\n"
        "- confidence: WYSOKA / SREDNIA / NISKA\n"
        "- smart_money.signal: KUPUJE / SPRZEDAJE / MIESZANE / BRAK\n"
        "- events.impact: POZYTYWNA / NEGATYWNA / NEUTRALNA\n"
        "- events.price_reaction: WZROST / SPADEK / MIESZANY\n\n"
        f"Dane uniwersum (JSON compact):\n{json.dumps(compact_context, ensure_ascii=False, separators=(',', ':'))}"
    )


def _market_scan_output_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "internet_checked": {"type": "boolean"},
            "market_view": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "hot_themes": {"type": "array", "items": {"type": "string"}},
                    "risk_themes": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["summary", "hot_themes", "risk_themes"],
                "additionalProperties": False,
            },
            "opportunities": {
                "type": "array",
                "items": {"$ref": "#/$defs/opportunity_item"},
            },
            "watchlist": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["internet_checked", "market_view", "opportunities", "watchlist"],
        "additionalProperties": False,
        "$defs": {
            "smart_money_item": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "signal": {"type": "string", "enum": ["KUPUJE", "SPRZEDAJE", "MIESZANE", "BRAK"]},
                    "why": {"type": "string"},
                },
                "required": ["title", "signal", "why"],
                "additionalProperties": False,
            },
            "event_item": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "impact": {"type": "string", "enum": ["POZYTYWNA", "NEGATYWNA", "NEUTRALNA"]},
                    "price_reaction": {"type": "string", "enum": ["WZROST", "SPADEK", "MIESZANY"]},
                    "why": {"type": "string"},
                },
                "required": ["title", "impact", "price_reaction", "why"],
                "additionalProperties": False,
            },
            "opportunity_item": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["OKAZJA", "OBSERWUJ", "POMIN"]},
                    "confidence": {"type": "string", "enum": ["WYSOKA", "SREDNIA", "NISKA"]},
                    "conviction_score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "summary": {"type": "string"},
                    "why_now": {"type": "string"},
                    "catalysts": {"type": "array", "items": {"type": "string"}},
                    "risks": {"type": "array", "items": {"type": "string"}},
                    "smart_money": {"type": "array", "items": {"$ref": "#/$defs/smart_money_item"}},
                    "events": {"type": "array", "items": {"$ref": "#/$defs/event_item"}},
                },
                "required": ["ticker", "verdict", "confidence", "conviction_score", "summary", "why_now", "catalysts", "risks", "smart_money", "events"],
                "additionalProperties": False,
            },
        },
    }


def _normalize_market_scan_structured(structured: dict | None, context: dict) -> dict | None:
    if not structured:
        return None

    instrument_map = {
        str(item.get("ticker") or "").upper(): item
        for item in (context.get("instruments") or [])
        if item.get("ticker")
    }

    market_view = structured.get("market_view") or {}
    normalized = {
        "internet_checked": bool(structured.get("internet_checked", False)),
        "market_view": {
            "summary": str(market_view.get("summary") or "").strip(),
            "hot_themes": [str(item).strip() for item in (market_view.get("hot_themes") or []) if str(item).strip()],
            "risk_themes": [str(item).strip() for item in (market_view.get("risk_themes") or []) if str(item).strip()],
        },
        "opportunities": [],
        "watchlist": [str(item).strip() for item in (structured.get("watchlist") or []) if str(item).strip()],
    }

    seen = set()
    for item in structured.get("opportunities") or []:
        ticker = str(item.get("ticker") or "").upper().strip()
        if not ticker or ticker in seen or ticker not in instrument_map:
            continue
        seen.add(ticker)

        instrument = instrument_map[ticker]
        verdict = str(item.get("verdict") or "OBSERWUJ").strip().upper()
        if verdict not in {"OKAZJA", "OBSERWUJ", "POMIN"}:
            verdict = "OBSERWUJ"

        confidence = str(item.get("confidence") or "SREDNIA").strip().upper()
        if confidence not in {"WYSOKA", "SREDNIA", "NISKA"}:
            confidence = "SREDNIA"

        conviction_score = _safe_int(item.get("conviction_score"), 0)
        conviction_score = max(0, min(100, int(conviction_score or 0)))

        normalized["opportunities"].append({
            "ticker": ticker,
            "name": instrument.get("name") or ticker,
            "group": instrument.get("group") or "INNE",
            "currency": instrument.get("currency") or "USD",
            "price": instrument.get("price"),
            "sector": instrument.get("sector") or "—",
            "industry": instrument.get("industry") or "—",
            "verdict": verdict,
            "confidence": confidence,
            "conviction_score": conviction_score,
            "summary": str(item.get("summary") or "").strip(),
            "why_now": str(item.get("why_now") or "").strip(),
            "catalysts": [str(value).strip() for value in (item.get("catalysts") or []) if str(value).strip()],
            "risks": [str(value).strip() for value in (item.get("risks") or []) if str(value).strip()],
            "smart_money": [
                {
                    "title": str(entry.get("title") or "").strip(),
                    "signal": str(entry.get("signal") or "BRAK").strip().upper(),
                    "why": str(entry.get("why") or "").strip(),
                }
                for entry in (item.get("smart_money") or [])
                if str(entry.get("title") or "").strip() or str(entry.get("why") or "").strip()
            ],
            "events": [
                {
                    "title": str(entry.get("title") or "").strip(),
                    "impact": str(entry.get("impact") or "NEUTRALNA").strip().upper(),
                    "price_reaction": str(entry.get("price_reaction") or "MIESZANY").strip().upper(),
                    "why": str(entry.get("why") or "").strip(),
                }
                for entry in (item.get("events") or [])
                if str(entry.get("title") or "").strip() or str(entry.get("why") or "").strip()
            ],
        })

    return normalized


def scan_market_opportunities_with_ai(tickers: list[str], settings: dict) -> dict:
    normalized = normalize_ai_settings(settings)
    token = normalized.get("token") or ""
    if not token:
        raise ValueError("Brak tokena API. Uzupelnij token w ustawieniach 'Porady AI' w portfelu.")

    model = resolve_ai_model(normalized)
    if not model:
        raise ValueError("Brak modelu AI. Wybierz model lub wpisz wlasny model w ustawieniach 'Porady AI'.")

    context = build_market_ai_context(tickers)
    if not context.get("instruments"):
        raise ValueError("Brak instrumentow z danymi do skanu AI okazji.")

    provider = normalized["provider"]
    system_prompt = _build_market_scan_system_prompt()
    if provider == "gemini":
        user_prompt = _build_market_scan_user_prompt(context)
        answer = _call_gemini(model, token, system_prompt, user_prompt)
    elif provider == "other":
        user_prompt = _build_market_scan_user_prompt(context)
        answer = _call_openai_compatible(normalized.get("base_url") or "", model, token, system_prompt, user_prompt)
    else:
        user_prompt = _build_market_scan_user_prompt(context, structured_output=True)
        answer = _call_openai(model, token, system_prompt, user_prompt, json_schema=_market_scan_output_schema())

    structured = _normalize_market_scan_structured(parse_ai_structured_response(answer.get("text", "")), context)
    if structured is None:
        raise RuntimeError("Model AI nie zwrocil poprawnego JSON dla skanu okazji AI.")

    return {
        "provider": provider,
        "provider_label": dict(AI_PROVIDERS).get(provider, provider),
        "model": model,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "context": context,
        "text": answer.get("text", "").strip(),
        "structured": structured,
        "sources": answer.get("sources") or [],
        "used_web_search": bool(answer.get("used_web_search", False)),
    }