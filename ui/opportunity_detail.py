"""ui/opportunity_detail.py — Rich detail modal for a scanned opportunity."""
import html
import sys
import os
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSizePolicy, QTabWidget,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QFont
import config


# ─────────────────────────────────────────────────────────────────────────────
class DeepAnalysisWorker(QThread):
    done = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, ticker: str, profile: str = "balanced", horizon: str = "all", price_snapshot: dict | None = None):
        super().__init__()
        self._ticker = ticker
        self._profile = profile
        self._horizon = horizon
        self._price_snapshot = dict(price_snapshot) if isinstance(price_snapshot, dict) else None

    def run(self):
        try:
            from analysis.opportunity import analyze_ticker_deep
            from data.fetcher import get_info, get_fundamental_history, get_fundamental_extended_data

            result = analyze_ticker_deep(
                self._ticker,
                profile=self._profile,
                horizon=self._horizon,
                price_snapshot=self._price_snapshot,
            )
            if result:
                try:
                    info_data = get_info(self._ticker)
                    history_data = get_fundamental_history(self._ticker)
                    extended_data = get_fundamental_extended_data(self._ticker)
                    result["fundamental_data"] = {
                        "info": info_data,
                        "history": history_data,
                        "estimates": extended_data
                    }
                except Exception as fe:
                    print(f"[DeepAnalysisWorker] Error fetching fundamental data: {fe}")
                self.done.emit(result)
            else:
                self.error.emit(f"Brak danych dla {self._ticker}")
        except Exception as e:
            self.error.emit(str(e))


# ─────────────────────────────────────────────────────────────────────────────
def _fmt_cap(val) -> str:
    if not val:
        return "—"
    try:
        v = float(val)
        if v >= 1e12: return f"${v/1e12:.2f}T"
        if v >= 1e9:  return f"${v/1e9:.2f}B"
        if v >= 1e6:  return f"${v/1e6:.2f}M"
        return f"${v:,.0f}"
    except Exception:
        return "—"


def _fmt(val, dec=2, suffix="") -> str:
    if val is None: return "—"
    try: return f"{float(val):,.{dec}f}{suffix}"
    except Exception: return "—"


def _fmt_pct_signed(val, dec=1) -> str:
  if val is None:
    return "—"
  try:
    return f"{float(val):+,.{dec}f}%"
  except Exception:
    return "—"


def _fmt_snapshot_time(value: str | None) -> str:
  text = str(value or "").strip()
  if not text:
    return "—"
  try:
    normalized = text.replace("Z", "+00:00")
    dt = __import__("datetime").datetime.fromisoformat(normalized)
    return dt.strftime("%Y-%m-%d %H:%M")
  except Exception:
    return text[:16].replace("T", " ")


def _color_change(val) -> str:
    if val is None: return "#6c757d"
    return config.COLOR_UP if float(val) >= 0 else config.COLOR_DOWN


def _prob_color(prob) -> str:
    if prob is None: return "#6c757d"
    p = float(prob)
    if p >= 70: return "#16a34a"
    if p >= 55: return "#f97316"
    return "#dc2626"


def _render_score_stars(score: int, max_score: int = 9, star_slots: int = 5) -> str:
  clamped = max(0, min(int(score or 0), max_score))
  filled = int(round((clamped / max_score) * star_slots)) if max_score > 0 else 0
  filled = max(0, min(star_slots, filled))
  return "★" * filled + "☆" * (star_slots - filled)


def _build_macro_section(macro: dict, impact_key: str) -> str:
    if not macro or not macro.get("components"):
        return '<div style="color:#6c757d;font-size:11px">Brak danych makro.</div>'

    impact = int(macro.get(impact_key, 0) or 0)
    impact_label = f"{impact:+d}" if impact else "0"
    impact_color = macro.get("color", _prob_color(macro.get("score", 50)))

    rows = ""
    for component in macro.get("components", []):
        rows += f"""
        <div style="background:#fff;border:1px solid #dee2e6;border-radius:8px;padding:10px 12px;margin-top:8px">
          <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">
            <div style="font-size:11px;font-weight:600;color:#212529">{component.get('label', 'Makro')}</div>
            <div style="font-size:11px;font-weight:700;color:{component.get('color', '#6c757d')}">{component.get('score', '—')}/100</div>
          </div>
          <div style="font-size:10px;color:#6c757d;margin-top:4px">{component.get('value', '—')}</div>
          <div style="font-size:10px;color:{component.get('color', '#6c757d')};margin-top:4px;line-height:1.5">{component.get('signal', '')}</div>
        </div>"""

    return f"""
    <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">
      <div>
        <div style="font-size:20px;font-weight:700;color:{impact_color}">{macro.get('score', '—')}/100</div>
        <div style="font-size:10px;color:#6c757d;margin-top:2px">{macro.get('region_label', 'Region')} · {macro.get('stance', 'Neutralne')}</div>
      </div>
      <div style="text-align:right">
        <div style="font-size:10px;color:#6c757d">Wpływ na ranking</div>
        <div style="font-size:18px;font-weight:700;color:{impact_color}">{impact_label}</div>
      </div>
    </div>
    <div style="font-size:11px;color:#495057;line-height:1.6;margin-top:10px">{macro.get('summary', '')}</div>
    {rows}
    """


def _build_company_section(company: dict, impact_key: str) -> str:
    if not company or not company.get("components"):
        return '<div style="color:#6c757d;font-size:11px">Brak danych o spółce.</div>'

    impact = int(company.get(impact_key, 0) or 0)
    impact_label = f"{impact:+d}" if impact else "0"
    impact_color = company.get("color", _prob_color(company.get("score", 50)))

    rows = ""
    for component in company.get("components", []):
        rows += f"""
        <div style="background:#fff;border:1px solid #dee2e6;border-radius:8px;padding:10px 12px;margin-top:8px">
          <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">
            <div style="font-size:11px;font-weight:600;color:#212529">{component.get('label', 'Spółka')}</div>
            <div style="font-size:11px;font-weight:700;color:{component.get('color', '#6c757d')}">{component.get('score', '—')}/100</div>
          </div>
          <div style="font-size:10px;color:#6c757d;margin-top:4px">{component.get('value', '—')}</div>
          <div style="font-size:10px;color:{component.get('color', '#6c757d')};margin-top:4px;line-height:1.5">{component.get('signal', '')}</div>
        </div>"""

    events_html = ""
    for event in company.get("events", []):
        published_at = (event.get("published_at") or "")[:10]
        provider = event.get("provider", "Yahoo Finance")
        events_html += f"""
        <div style="background:#fff;border:1px solid #dee2e6;border-radius:8px;padding:10px 12px;margin-top:8px">
          <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">
            <div style="font-size:11px;font-weight:600;color:#212529">{event.get('title', 'Wydarzenie')}</div>
            <div style="font-size:11px;font-weight:700;color:{event.get('color', '#6c757d')}">{event.get('sentiment', 'Neutralne')}</div>
          </div>
          <div style="font-size:10px;color:#6c757d;margin-top:4px">{provider}{' · ' + published_at if published_at else ''}</div>
        </div>"""
    if not events_html:
        events_html = '<div style="color:#6c757d;font-size:11px;margin-top:8px">Brak jednoznacznych ostatnich wydarzeń dla tej spółki.</div>'

    return f"""
    <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">
      <div>
        <div style="font-size:20px;font-weight:700;color:{impact_color}">{company.get('score', '—')}/100</div>
        <div style="font-size:10px;color:#6c757d;margin-top:2px">Spółka · {company.get('stance', 'Neutralne')}</div>
      </div>
      <div style="text-align:right">
        <div style="font-size:10px;color:#6c757d">Wpływ na ranking</div>
        <div style="font-size:18px;font-weight:700;color:{impact_color}">{impact_label}</div>
      </div>
    </div>
    <div style="font-size:11px;color:#495057;line-height:1.6;margin-top:10px">{company.get('summary', '')}</div>
    {rows}
    <div style="font-size:10px;font-weight:700;color:#6c757d;text-transform:uppercase;letter-spacing:0.5px;margin-top:12px">Ostatnie wydarzenia</div>
    {events_html}
    """


def _gauge_html(value: float, label: str, min_v=0, max_v=100, reverse=False) -> str:
    """Simple CSS progress bar gauge."""
    pct = max(0, min(100, (value - min_v) / (max_v - min_v) * 100))
    if reverse:
        pct = 100 - pct
    if pct >= 70:
        color = "#16a34a"
    elif pct >= 40:
        color = "#f97316"
    else:
        color = "#dc2626"
    return f"""
    <div style="margin:3px 0">
      <div style="display:flex;justify-content:space-between;font-size:10px;color:#6c757d;margin-bottom:2px">
        <span>{label}</span><span style="color:{color};font-weight:600">{value:.1f}</span>
      </div>
      <div style="background:#e9ecef;border-radius:4px;height:7px">
        <div style="width:{pct:.0f}%;background:{color};border-radius:4px;height:7px"></div>
      </div>
    </div>"""


def _value_section_html(value_snapshot: dict, currency: str) -> str:
    if not value_snapshot:
        return ""

    verdict = html.escape(str(value_snapshot.get("verdict") or "Neutralna wycena"))
    summary = html.escape(str(value_snapshot.get("summary") or "Brak dodatkowego komentarza value."))
    score = float(value_snapshot.get("value_score") or 0.0)
    trap_risk = float(value_snapshot.get("value_trap_risk") or 0.0)
    fair_value = _fmt(value_snapshot.get("fair_value_price"), 2)
    margin = _fmt_pct_signed(value_snapshot.get("margin_of_safety_pct"), 1)
    pe = _fmt(
        value_snapshot.get("forward_pe") if value_snapshot.get("forward_pe") is not None else value_snapshot.get("pe_ratio"),
        1,
    )
    pb = _fmt(value_snapshot.get("price_to_book"), 2)
    fcf = _fmt_pct_signed(value_snapshot.get("fcf_yield_pct"), 1)
    quarterly_trend_score = value_snapshot.get("quarterly_trend_score")
    ownership_score = value_snapshot.get("ownership_score")
    quarterly_trend_summary = html.escape(str(value_snapshot.get("quarterly_trend_summary") or "Brak 4 pełnych kwartałów do testu trendu."))
    ownership_summary = html.escape(str(value_snapshot.get("ownership_summary") or "Brak stabilnych danych insider/holders dla tego rynku."))

    verdict_lower = verdict.lower()
    if "trap" in verdict_lower:
        card_bg = "#fff5f5"
        card_border = "#fecaca"
        verdict_color = "#b91c1c"
    elif score >= 72:
        card_bg = "#f0fdf4"
        card_border = "#bbf7d0"
        verdict_color = "#166534"
    elif score >= 58:
        card_bg = "#fff7ed"
        card_border = "#fdba74"
        verdict_color = "#c2410c"
    else:
        card_bg = "#f8fafc"
        card_border = "#cbd5e1"
        verdict_color = "#334155"

    fair_sources = value_snapshot.get("fair_value_sources") or []
    fair_sources_html = "".join(
        f"<div style='background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px;margin-top:8px'>"
        f"<div style='font-size:11px;font-weight:700;color:#0f172a'>{html.escape(str(source.get('label') or 'Źródło fair value'))}</div>"
        f"<div style='font-size:14px;font-weight:800;color:#111827;margin-top:3px'>{_fmt(source.get('fair_price'), 2)} {currency}</div>"
        f"<div style='font-size:10px;color:#6c757d;line-height:1.6;margin-top:4px'>{html.escape(str(source.get('note') or ''))}</div>"
        f"</div>"
        for source in fair_sources[:4]
    ) or "<div style='font-size:10px;color:#6c757d'>Brak dodatkowych źródeł fair value.</div>"

    strengths_html = "".join(
        f"<li style='margin-left:18px;margin-top:6px;color:#166534;line-height:1.6'>{html.escape(str(item))}</li>"
        for item in (value_snapshot.get("strengths") or [])[:4]
        if str(item).strip()
    ) or "<div style='font-size:10px;color:#6c757d'>Brak podbitych mocnych stron.</div>"

    risks_html = "".join(
        f"<li style='margin-left:18px;margin-top:6px;color:#b91c1c;line-height:1.6'>{html.escape(str(item))}</li>"
        for item in (value_snapshot.get("risks") or [])[:4]
        if str(item).strip()
    ) or "<div style='font-size:10px;color:#6c757d'>Brak podbitych ryzyk.</div>"

    metrics = [
        ("Value score", f"{score:.0f}/100"),
        ("Ryzyko value trap", f"{trap_risk:.0f}/100"),
        ("Fair value proxy", f"{fair_value} {currency}" if fair_value != "—" else "—"),
        ("Margines bezpieczeństwa", margin),
        ("Forward P/E", pe),
        ("P/B", pb),
        ("FCF yield", fcf),
        ("ROA", _fmt_pct_signed(value_snapshot.get("roa_pct"), 1)),
        ("Marża brutto", _fmt_pct_signed(value_snapshot.get("gross_margin_pct"), 1)),
        ("OCF / zysk netto", f"{_fmt(value_snapshot.get('cash_conversion_ratio'), 2)}x" if value_snapshot.get("cash_conversion_ratio") is not None else "—"),
        ("Net debt / EBITDA", f"{_fmt(value_snapshot.get('net_debt_to_ebitda'), 2)}x" if value_snapshot.get("net_debt_to_ebitda") is not None else "—"),
        ("Payout ratio", _fmt_pct_signed(value_snapshot.get("payout_ratio_pct"), 1)),
        ("Trend kwartalny", f"{_fmt(quarterly_trend_score, 0)}/100" if quarterly_trend_score is not None else "—"),
        ("Trend kwartalny - opis", quarterly_trend_summary),
        ("Akcjonariat / insiderzy", f"{_fmt(ownership_score, 0)}/100" if ownership_score is not None else "—"),
        ("Akcjonariat / insiderzy - opis", ownership_summary),
        ("Quick ratio", _fmt(value_snapshot.get("quick_ratio"), 2)),
    ]
    metrics_html = "".join(
      f"<div style='display:flex;justify-content:space-between;align-items:flex-start;gap:10px;padding:4px 0;border-bottom:1px solid #f1f3f5;font-size:11px'>"
      f"<span style='color:#6c757d;flex:0 0 40%'>{label}</span><strong style='color:#212529;flex:1;text-align:right;line-height:1.45;white-space:normal'>{value}</strong></div>"
        for label, value in metrics
    )

    return f"""
    <div class="card">
      <div class="section-title">💎 Value Investing</div>
      <div style="background:{card_bg};border:1px solid {card_border};border-radius:10px;padding:12px 14px">
        <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap">
          <div>
            <div style="font-size:16px;font-weight:800;color:{verdict_color}">{verdict}</div>
            <div style="font-size:11px;color:#6c757d;margin-top:4px;line-height:1.6">{summary}</div>
          </div>
          <div style="display:grid;grid-template-columns:repeat(2,minmax(120px,1fr));gap:8px;min-width:280px;flex:1">
            <div style="background:#ffffffcc;border-radius:8px;padding:8px 10px"><div style="font-size:10px;color:#6c757d">Value score</div><div style="font-size:16px;font-weight:800;color:#111827">{score:.0f}/100</div></div>
            <div style="background:#ffffffcc;border-radius:8px;padding:8px 10px"><div style="font-size:10px;color:#6c757d">Ryzyko value trap</div><div style="font-size:16px;font-weight:800;color:#111827">{trap_risk:.0f}/100</div></div>
            <div style="background:#ffffffcc;border-radius:8px;padding:8px 10px"><div style="font-size:10px;color:#6c757d">Fair value proxy</div><div style="font-size:16px;font-weight:800;color:#111827">{fair_value} {currency if fair_value != '—' else ''}</div></div>
            <div style="background:#ffffffcc;border-radius:8px;padding:8px 10px"><div style="font-size:10px;color:#6c757d">Margines bezpieczeństwa</div><div style="font-size:16px;font-weight:800;color:#111827">{margin}</div></div>
          </div>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:1.15fr .85fr;gap:12px;margin-top:12px">
        <div style="background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:12px 14px">
          <div style="font-size:11px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.4px">Źródła fair value</div>
          {fair_sources_html}
        </div>
        <div style="background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:12px 14px">
          <div style="font-size:11px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.4px;margin-bottom:8px">Kluczowe metryki</div>
          {metrics_html}
        </div>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px">
        <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:10px 12px"><div style="font-size:11px;font-weight:700;color:#166534">Mocne strony</div><ul style="margin:0;padding:0">{strengths_html}</ul></div>
        <div style="background:#fff7ed;border:1px solid #fdba74;border-radius:10px;padding:10px 12px"><div style="font-size:11px;font-weight:700;color:#c2410c">Ryzyka</div><ul style="margin:0;padding:0">{risks_html}</ul></div>
      </div>
    </div>"""


def _build_detail_html(data: dict) -> str:
    score     = data.get("score", 0)
    max_score = 9
    ticker    = data.get("ticker", "")
    name      = data.get("name", ticker)
    currency  = data.get("currency", "USD")
    price     = data.get("price", 0)
    ch1d      = data.get("change_1d", 0)
    ch5d      = data.get("change_5d", 0)
    ch1m      = data.get("change_1m", 0)
    ch3m      = data.get("change_3m", 0)
    macro     = data.get("macro") or {}
    value_snapshot = data.get("value_investing") or {}
    detail_context = str(data.get("detail_context") or "opportunities").strip().lower()
    show_setup_backtest = detail_context == "opportunities"
    matches_opportunity_setup = bool(data.get("matches_opportunity_setup", True))
    scanner_filter_failures = [
        html.escape(str(item))
        for item in (data.get("scanner_filter_failures") or [])
        if str(item).strip()
    ]

    from analysis.opportunity import get_opportunity_display_state

    display_state = get_opportunity_display_state(data, data.get("scan_horizon") or "all", "balanced")
    stars = _render_score_stars(display_state.get("stars_value", 0), max_score=5, star_slots=5)
    star_color = display_state.get("color", "#6c757d")
    score_label = display_state.get("label", "SŁABY SYGNAŁ")
    score_bg = display_state.get("bg", "#fee2e2")
    score_color = display_state.get("color", "#b91c1c")
    rank_caption = f"ranking {display_state.get('rank_score', 0.0):+.3f} · {score}/{max_score} kryteriów live"
    scanner_scope_note = ""
    if not matches_opportunity_setup:
        score_label = "POZA FILTREM TRYBU PODSTAWOWEGO"
        score_bg = "#fff7ed"
        score_color = "#c2410c"
        star_color = "#94a3b8"
        rank_caption = f"historyczny ranking {display_state.get('rank_score', 0.0):+.3f} · {score}/{max_score} kryteriów live"
    price_snapshot_time = _fmt_snapshot_time(data.get("snapshot_time"))
    bars_snapshot_time = _fmt_snapshot_time(data.get("bars_snapshot_time"))
    snapshot_source_label = data.get("snapshot_source_label") or "live cena (cache do 15 min)"

    # Criteria HTML
    criteria_html = ""
    for c in data.get("criteria", []):
        met   = c["met"]
        bg    = "#f0fdf4" if met else "#fafafa"
        border= "#86efac" if met else "#e5e7eb"
        icon_bg = "#22c55e" if met else "#d1d5db"
        icon_color = "#fff" if met else "#6b7280"
        criteria_html += f"""
        <div style="background:{bg};border:1px solid {border};border-radius:8px;
                    padding:10px 12px;margin-bottom:8px">
          <div style="display:flex;align-items:flex-start;gap:10px">
            <div style="background:{icon_bg};color:{icon_color};border-radius:50%;
                        width:26px;height:26px;display:flex;align-items:center;
                        justify-content:center;font-size:13px;flex-shrink:0;margin-top:1px">
              {c['icon']}
            </div>
            <div>
              <div style="font-weight:600;font-size:12px;color:#212529;margin-bottom:3px">
                {c['title']}
              </div>
              <div style="font-size:11px;color:#495057;line-height:1.5">{c['desc']}</div>
            </div>
          </div>
        </div>"""

    # Indicators table
    def ind_row(label, val, signal="", signal_color="#212529"):
        return f"""<tr>
          <td style="padding:5px 8px;color:#6c757d;font-size:11px;white-space:nowrap">{label}</td>
          <td style="padding:5px 8px;font-weight:600;font-size:11px;color:#212529">{val}</td>
          <td style="padding:5px 8px;font-size:10px;color:{signal_color}">{signal}</td>
        </tr>"""

    rsi = data.get("rsi14")
    rsi_sig   = ("Głęboka wyprzedaż" if rsi and rsi < 30 else
                 "Wyprzedaż ▲" if rsi and rsi < 45 else
                 "Wykupienie ▼" if rsi and rsi > 70 else
                 "Neutralny")
    rsi_col   = "#16a34a" if rsi and rsi < 45 else "#dc2626" if rsi and rsi > 70 else "#6c757d"

    macd_h    = data.get("macd_hist")
    macd_sig2 = "Byczy ▲" if macd_h and macd_h > 0 else "Niedźwiedzi ▼" if macd_h else "—"
    macd_col  = "#16a34a" if macd_h and macd_h > 0 else "#dc2626"

    stk = data.get("stoch_k")
    std = data.get("stoch_d")
    st_sig = ("Wyprzedaż ▲" if stk and stk < 25 else
              "Wykupienie ▼" if stk and stk > 75 else "Neutralny")
    st_col = "#16a34a" if stk and stk < 25 else "#dc2626" if stk and stk > 75 else "#6c757d"

    wr = data.get("williams_r")
    wr_sig = ("Wyprzedaż ▲" if wr and wr < -80 else
              "Wykupienie ▼" if wr and wr > -20 else "Neutralny")
    wr_col = "#16a34a" if wr and wr < -80 else "#dc2626" if wr and wr > -20 else "#6c757d"

    bb_pos = data.get("bb_position", 0.5)
    bb_sig = ("Przy dolnym paśmie ▲" if bb_pos < 0.2 else
              "Przy górnym paśmie ▼" if bb_pos > 0.8 else "Środek pasma")
    bb_col = "#16a34a" if bb_pos < 0.2 else "#dc2626" if bb_pos > 0.8 else "#6c757d"

    sma200 = data.get("sma200")
    sma50  = data.get("sma50")
    sma20  = data.get("sma20")
    sma200_sig = ("Cena powyżej ▲" if sma200 and price >= sma200 else "Cena poniżej ▼")
    sma50_sig  = ("Korekta w trendzie ▲" if sma50 and price <= sma50 else "Brak korekty")
    sma200_col = "#16a34a" if sma200 and price >= sma200 else "#dc2626"
    sma50_col  = "#16a34a" if sma50 and price <= sma50 else "#6c757d"

    roc = data.get("roc20")
    roc_sig = ("Momentum wzrostowe ▲" if roc and roc > 0 else "Momentum spadkowe ▼" if roc else "—")
    roc_col = "#16a34a" if roc and roc > 0 else "#dc2626"

    indicators_html = f"""
    <table style="width:100%;border-collapse:collapse">
      <thead>
        <tr style="background:#f8f9fa;border-bottom:2px solid #dee2e6">
          <th style="padding:6px 8px;text-align:left;font-size:10px;color:#6c757d;font-weight:500">Wskaźnik</th>
          <th style="padding:6px 8px;text-align:left;font-size:10px;color:#6c757d;font-weight:500">Wartość</th>
          <th style="padding:6px 8px;text-align:left;font-size:10px;color:#6c757d;font-weight:500">Interpretacja</th>
        </tr>
      </thead>
      <tbody>
        {ind_row("RSI(14)", _fmt(rsi, 1), rsi_sig, rsi_col)}
        {ind_row("MACD (12/26/9)", f"{_fmt(data.get('macd'),3)} / {_fmt(data.get('macd_signal'),3)}", macd_sig2, macd_col)}
        {ind_row("Stochastic %K/%D", f"{_fmt(stk,1)} / {_fmt(std,1)}", st_sig, st_col)}
        {ind_row("Williams %R", _fmt(wr, 1), wr_sig, wr_col)}
        {ind_row("ROC(20)", _fmt(roc, 2, "%"), roc_sig, roc_col)}
        {ind_row("SMA20 / EMA20", f"{_fmt(sma20)} / {_fmt(data.get('sma20'))}", "", "#6c757d")}
        {ind_row("SMA50", _fmt(sma50), sma50_sig, sma50_col)}
        {ind_row("SMA200", _fmt(sma200), sma200_sig, sma200_col)}
        {ind_row("Bollinger Upper/Mid/Lower", f"{_fmt(data.get('bb_upper'))} / {_fmt(data.get('bb_mid'))} / {_fmt(data.get('bb_lower'))}", bb_sig, bb_col)}
        {ind_row("ATR(14)", f"{_fmt(data.get('atr14'))} ({_fmt(data.get('atr_pct'), 2)}% ceny)", "Zmienność dzienna", "#6c757d")}
        {ind_row("52W High / Low", f"{_fmt(data.get('high_52w'))} / {_fmt(data.get('low_52w'))}", f"Drawdown od szczytu: -{data.get('drawdown_pct','?')}%", "#dc2626" if (data.get('drawdown_pct') or 0) > 20 else "#6c757d")}
        {ind_row("Wolumen (1D / avg50D)", f"{data.get('vol_ratio_1d',0):.2f}× / {_fmt(data.get('vol50'),0)}", f"10D avg: {data.get('vol_ratio_10d',0):.2f}× średniej 50D", "#16a34a" if data.get('vol_ratio_10d',0) >= 1.2 else "#6c757d")}
      </tbody>
    </table>"""

    # Gauge bars
    gauges_html = ""
    if rsi is not None:
        gauges_html += _gauge_html(rsi, "RSI(14)", 0, 100, reverse=True)
    if stk is not None:
        gauges_html += _gauge_html(stk, "Stochastic %K", 0, 100, reverse=True)
    if bb_pos is not None:
        gauges_html += _gauge_html(bb_pos * 100, "Pozycja w paśmie BB (%)", 0, 100, reverse=True)

    # Backtest section
    bt = data.get("backtest", {})
    n  = data.get("n_setups", 0)
    prob_5d  = data.get("prob_5d")
    prob_22d = data.get("prob_22d")
    prob_66d = data.get("prob_66d")
    ar5  = data.get("avg_return_5d")
    ar22 = data.get("avg_return_22d")
    ar66 = data.get("avg_return_66d")

    def prob_card(label, sublabel, prob, avg_ret):
        if prob is None:
            return ""
        col = _prob_color(prob)
        sign = "+" if avg_ret and avg_ret >= 0 else ""
        avg_str = f"{sign}{avg_ret:.1f}%" if avg_ret is not None else "—"
        return f"""
        <div style="background:#fff;border:1px solid #dee2e6;border-radius:8px;
                    padding:12px;text-align:center;flex:1">
          <div style="font-size:11px;font-weight:600;color:#495057;margin-bottom:1px">{label}</div>
          <div style="font-size:9px;color:#adb5bd;margin-bottom:6px">{sublabel}</div>
          <div style="font-size:24px;font-weight:700;color:{col}">{prob:.0f}%</div>
          <div style="font-size:10px;color:#6c757d;margin-top:4px">szansa wzrostu</div>
          <div style="font-size:11px;font-weight:600;color:{col};margin-top:2px">
            avg: {avg_str}
          </div>
        </div>"""

    def trajectory_chart_html(traj_bt: dict) -> str:
        current_path = traj_bt.get("current_path_pct") or []
        median_path = traj_bt.get("matched_median_path_pct") or []
        band_low = traj_bt.get("matched_p25_path_pct") or []
        band_high = traj_bt.get("matched_p75_path_pct") or []
        window_label = str(traj_bt.get("window_label") or "30 dni")
        if len(current_path) < 2 or len(median_path) != len(current_path):
            return ""

        width = 420.0
        height = 168.0
        pad_x = 18.0
        pad_y = 14.0

        series = [current_path, median_path, [0.0]]
        if len(band_low) == len(current_path) and len(band_high) == len(current_path):
            series.extend([band_low, band_high])

        min_v = min(min(path) for path in series)
        max_v = max(max(path) for path in series)
        if abs(max_v - min_v) < 1e-9:
            max_v = min_v + 1.0
        margin = (max_v - min_v) * 0.12 + 0.5
        min_v -= margin
        max_v += margin
        inner_w = width - pad_x * 2
        inner_h = height - pad_y * 2
        span = max_v - min_v

        def _point(index: int, value: float) -> tuple[float, float]:
            x = pad_x + (inner_w * index / max(1, len(current_path) - 1))
            y = height - pad_y - ((value - min_v) / span) * inner_h
            return x, y

        def _polyline(values: list[float]) -> str:
            return " ".join(f"{x:.1f},{y:.1f}" for x, y in (_point(idx, value) for idx, value in enumerate(values)))

        band_polygon = ""
        if len(band_low) == len(current_path) and len(band_high) == len(current_path):
            lower = [_point(idx, value) for idx, value in enumerate(band_low)]
            upper = [_point(idx, value) for idx, value in enumerate(band_high)]
            polygon_points = lower + list(reversed(upper))
            band_polygon = " ".join(f"{x:.1f},{y:.1f}" for x, y in polygon_points)

        zero_y = _point(0, 0.0)[1]
        current_color = "#2563eb"
        median_color = "#f97316"
        start_label = f"{window_label} temu"
        end_label = "dziś"

        return f"""
        <div style="background:#fcfcfd;border:1px solid #e9ecef;border-radius:10px;padding:10px 12px;margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:8px">
            <div style="font-size:10px;font-weight:700;color:#495057">Ścieżka ceny z ostatnich {window_label} vs historyczne dopasowania</div>
            <div style="display:flex;gap:10px;flex-wrap:wrap;font-size:9px;color:#6c757d">
              <span><span style="display:inline-block;width:10px;height:2px;background:{current_color};vertical-align:middle;margin-right:4px"></span>ostatnie {window_label}</span>
              <span><span style="display:inline-block;width:10px;height:2px;background:{median_color};vertical-align:middle;margin-right:4px"></span>mediana historii</span>
              <span><span style="display:inline-block;width:10px;height:10px;background:rgba(249,115,22,.12);border:1px solid rgba(249,115,22,.18);vertical-align:middle;margin-right:4px"></span>typowy zakres</span>
            </div>
          </div>
          <svg viewBox="0 0 {int(width)} {int(height)}" width="100%" height="168" preserveAspectRatio="none" role="img" aria-label="Porównanie ścieżki ceny z ostatnich {window_label}">
            <rect x="0" y="0" width="{int(width)}" height="{int(height)}" rx="8" fill="#fcfcfd"></rect>
            <line x1="{pad_x:.1f}" y1="{zero_y:.1f}" x2="{width - pad_x:.1f}" y2="{zero_y:.1f}" stroke="#d1d5db" stroke-width="1" stroke-dasharray="4 4"></line>
            <line x1="{pad_x:.1f}" y1="{pad_y:.1f}" x2="{pad_x:.1f}" y2="{height - pad_y:.1f}" stroke="#eef2f7" stroke-width="1"></line>
            <line x1="{pad_x:.1f}" y1="{height - pad_y:.1f}" x2="{width - pad_x:.1f}" y2="{height - pad_y:.1f}" stroke="#eef2f7" stroke-width="1"></line>
            {f'<polygon points="{band_polygon}" fill="rgba(249,115,22,0.14)" stroke="none"></polygon>' if band_polygon else ''}
            <polyline points="{_polyline(median_path)}" fill="none" stroke="{median_color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"></polyline>
            <polyline points="{_polyline(current_path)}" fill="none" stroke="{current_color}" stroke-width="2.6" stroke-linejoin="round" stroke-linecap="round"></polyline>
            <circle cx="{_point(len(current_path) - 1, current_path[-1])[0]:.1f}" cy="{_point(len(current_path) - 1, current_path[-1])[1]:.1f}" r="3.6" fill="{current_color}"></circle>
          </svg>
          <div style="display:flex;justify-content:space-between;gap:12px;font-size:9px;color:#6c757d;margin-top:4px">
            <span>{start_label}</span>
            <span>{end_label}</span>
          </div>
        </div>"""

    backtest_html = f"""
    <div style="display:flex;gap:8px">
      {prob_card("1 tydzień",  "~5 sesji",  prob_5d,  ar5)}
      {prob_card("1 miesiąc",  "~22 sesje", prob_22d, ar22)}
      {prob_card("3 miesiące", "~66 sesji", prob_66d, ar66)}
    </div>"""
    backtest_title = (
        "📊 Szansa na wzrost (backtest historyczny)"
        if matches_opportunity_setup
        else "📊 Szansa na wzrost względem podobnych setupów"
    )

    if bt.get("best_22d") is not None:
        backtest_html += f"""
        <div style="margin-top:8px;font-size:10px;color:#6c757d;display:flex;gap:16px;flex-wrap:wrap">
          <span>📈 Najlepszy wynik 1M: <b style="color:#16a34a">+{bt['best_22d']:.1f}%</b></span>
          <span>📉 Najgorszy wynik 1M: <b style="color:#dc2626">{bt['worst_22d']:.1f}%</b></span>
          <span>⚖️ Mediana 1M: <b>{bt.get('median_return_22d', 0):.1f}%</b></span>
          <span>📊 Liczba setupów: <b>{n}</b></span>
        </div>"""

    recent_bt = data.get("recent_history_backtest") or {}
    recent_samples = int(recent_bt.get("n_samples", 0) or 0)
    recent_window_label = str(recent_bt.get("window_label") or "30 dni")
    recent_chart = trajectory_chart_html(recent_bt) if recent_samples > 0 else ""
    recent_shape_mae = recent_bt.get("avg_shape_mae")
    recent_corr = recent_bt.get("avg_corr")
    recent_volume_mae = recent_bt.get("avg_volume_mae")
    recent_rsi_mae = recent_bt.get("avg_rsi_mae")
    recent_pnl = float(recent_bt.get("pnl_pct") or 0.0)
    recent_sign = "+" if recent_pnl > 0 else ""
    if "msg" in recent_bt:
        recent_info = f"<div style='font-size:10px;color:#6c757d;margin-bottom:8px'>{recent_bt['msg']}</div>"
    else:
        similarity_bits = []
        if recent_shape_mae is not None:
            similarity_bits.append(f"średnie odchylenie kształtu <b>{recent_shape_mae:.2f} p.p.</b>")
        if recent_corr is not None:
            similarity_bits.append(f"średnia zgodność kolejnych ruchów <b>{recent_corr:.2f}</b>")
        if recent_volume_mae is not None:
          similarity_bits.append(f"odchylenie profilu wolumenu <b>{recent_volume_mae:.2f}</b>")
        if recent_rsi_mae is not None:
          similarity_bits.append(f"odchylenie RSI <b>{recent_rsi_mae:.2f}</b>")
        similarity_html = f" Dla wybranej próbki: {', '.join(similarity_bits)}." if similarity_bits else ""
        anchor_label = _fmt_snapshot_time(recent_bt.get("anchor_time"))
        recent_info = (
            f"<div style='font-size:10px;color:#6c757d;margin-bottom:8px'>"
          f"Na podstawie <b>{recent_samples}</b> historycznych okien podobnych do ostatnich <b>{recent_window_label}</b>: "
            f"ten sam zafiksowany punkt startu (<b>{anchor_label}</b>) i zbliżona zmiana ceny <b>{recent_sign}{recent_pnl:.2f}%</b> do dziś, "
          f"ale też podobny kształt całej ścieżki po drodze, profil wolumenu i przebieg RSI.{similarity_html}"
            f"</div>"
        )

    recent_history_html = f"""
    {recent_info}
    {recent_chart}
    <div style="display:flex;gap:8px">
      {prob_card("1 tydzień", "po końcu podobnych okien", recent_bt.get("win_rate_5d"), recent_bt.get("avg_return_5d"))}
      {prob_card("1 miesiąc", "po końcu podobnych okien", recent_bt.get("win_rate_22d"), recent_bt.get("avg_return_22d"))}
      {prob_card("3 miesiące", "po końcu podobnych okien", recent_bt.get("win_rate_66d"), recent_bt.get("avg_return_66d"))}
    </div>"""
    history_primary_title = backtest_title if show_setup_backtest else "🧭 Analiza na podstawie historii"
    history_primary_html = backtest_html if show_setup_backtest else recent_history_html
    history_secondary_card = f"""
      <div class="card">
        <div class="section-title">🧭 Analiza na podstawie historii</div>
        {recent_history_html}
      </div>""" if show_setup_backtest else ""

    # Support / resistance
    sr = data.get("support", {})
    sr_html = ""
    if sr.get("resistance1"):
        sr_html += f"""
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <div style="background:#fef2f2;border:1px solid #fca5a5;border-radius:6px;
                      padding:6px 12px;font-size:11px">
            <span style="color:#6c757d">Opór 1:</span>
            <b style="color:#dc2626">{sr.get('resistance1','—')}</b>
          </div>
          <div style="background:#fef2f2;border:1px solid #fca5a5;border-radius:6px;
                      padding:6px 12px;font-size:11px">
            <span style="color:#6c757d">Opór 2:</span>
            <b style="color:#dc2626">{sr.get('resistance2','—')}</b>
          </div>
          <div style="background:#f0fdf4;border:1px solid #86efac;border-radius:6px;
                      padding:6px 12px;font-size:11px">
            <span style="color:#6c757d">Wsparcie 1:</span>
            <b style="color:#16a34a">{sr.get('support1','—')}</b>
          </div>
          <div style="background:#f0fdf4;border:1px solid #86efac;border-radius:6px;
                      padding:6px 12px;font-size:11px">
            <span style="color:#6c757d">Wsparcie 2:</span>
            <b style="color:#16a34a">{sr.get('support2','—')}</b>
          </div>
        </div>"""

    # Price changes
    def ch_badge(val, label):
        col = _color_change(val)
        sign = "+" if val and val >= 0 else ""
        return f"""<span style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:5px;
                    padding:4px 10px;font-size:11px;font-weight:600">
                    <span style="color:#6c757d">{label}:</span>
                    <span style="color:{col}">{sign}{_fmt(val, 2)}%</span></span>"""

    changes_html = f"""
    <div style="display:flex;gap:6px;flex-wrap:wrap">
      {ch_badge(ch1d, "1D")}
      {ch_badge(ch5d, "5D")}
      {ch_badge(ch1m, "1M")}
      {ch_badge(ch3m, "3M")}
    </div>"""

    # Composite probability big number
    comp_prob = data.get("composite_prob", 50)
    comp_col  = _prob_color(comp_prob)
    company = data.get("company") or {}
    rank_breakdown = data.get("rank_breakdown") or {}
    confidence = data.get("signal_confidence") or rank_breakdown.get("confidence") or {}
    confidence_score = float(confidence.get("score", 0) or 0)
    confidence_label = str(confidence.get("label") or "Niska")
    confidence_note = str(confidence.get("reason") or "")
    confidence_color = _prob_color(confidence_score)
    setup_quality = float(data.get("setup_quality_score", rank_breakdown.get("setup_quality_score", 50)) or 50)
    company_quality = float(data.get("company_quality_score", rank_breakdown.get("company_quality_score", 50)) or 50)
    macro_quality = float(data.get("macro_quality_score", rank_breakdown.get("macro_quality_score", 50)) or 50)
    company_score_raw = float((company.get("score", 50) or 50))
    macro_score_raw = float(((data.get("macro") or {}).get("score", 50) or 50))
    mismatch_note = ""
    if score >= 7 and setup_quality < 45:
      mismatch_note = (
        "Live technicznie układ spełnia wiele warunków wejścia, ale historia podobnych setupów była słaba. "
        "Dlatego całość nie jest dziś traktowana jako mocna okazja."
      )
    quality_cards_html = f"""
    <div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:14px">
      <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px">
        <div style="font-size:10px;color:#6c757d">Pewność sygnału</div>
        <div style="font-size:15px;font-weight:800;color:{confidence_color};margin-top:3px">{confidence_label} · {confidence_score:.0f}/100</div>
      </div>
      <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px">
        <div style="font-size:10px;color:#6c757d">Jakość setupu w rankingu</div>
        <div style="font-size:15px;font-weight:800;color:#111827;margin-top:3px">{setup_quality:.0f}/100</div>
        <div style="font-size:10px;color:#6c757d;margin-top:4px">technika + historia podobnych setupów</div>
      </div>
      <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px">
        <div style="font-size:10px;color:#6c757d">Score spółki / makro</div>
        <div style="font-size:15px;font-weight:800;color:#111827;margin-top:3px">{company_score_raw:.0f} / {macro_score_raw:.0f}</div>
        <div style="font-size:10px;color:#6c757d;margin-top:4px">w rankingu: {company_quality:.0f} / {macro_quality:.0f}</div>
      </div>
    </div>
    {f'<div style="font-size:11px;color:#9a3412;line-height:1.6;margin-top:8px">{mismatch_note}</div>' if mismatch_note else ''}
    <div style="font-size:10px;color:#6c757d;line-height:1.6;margin-top:8px">{confidence_note}</div>"""

    # Fundamental info
    fund_html = ""
    fields = [
        ("Sektor", data.get("sector", "—")),
        ("Branża", data.get("industry", "—")),
        ("Kap. rynkowa", _fmt_cap(data.get("market_cap"))),
        ("P/E", _fmt(data.get("pe_ratio"), 1)),
        ("Beta", _fmt(data.get("beta"), 2)),
        ("Giełda", data.get("exchange", "—")),
    ]
    for label, val in fields:
        fund_html += f"""
        <div style="display:flex;justify-content:space-between;padding:4px 0;
                    border-bottom:1px solid #f1f3f5;font-size:11px">
          <span style="color:#6c757d">{label}</span>
          <span style="font-weight:600;color:#212529">{val}</span>
        </div>"""

    page_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: "Helvetica Neue", Arial, sans-serif;
    background: #f8f9fa;
    color: #212529;
    font-size: 12px;
    padding: 0;
  }}
  .container {{ max-width: 900px; margin: 0 auto; padding: 16px; }}
  .card {{
    background: #ffffff;
    border: 1px solid #dee2e6;
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 12px;
  }}
  .section-title {{
    font-size: 11px;
    font-weight: 700;
    color: #6c757d;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 10px;
    padding-bottom: 6px;
    border-bottom: 1px solid #f1f3f5;
  }}
  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
</style>
</head>
<body>
<div class="container">

  <!-- ── HEADER ── -->
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:flex-start">
      <div>
        <div style="font-size:22px;font-weight:800;color:#212529">{name}</div>
        <div style="font-size:13px;color:#6c757d;margin-top:2px">{ticker} · {data.get('sector','—')} · {data.get('exchange','')}</div>
        <div style="font-size:11px;color:#6c757d;margin-top:8px;line-height:1.6">Snapshot ceny: {price_snapshot_time} · {snapshot_source_label}<br>Wskaźniki i układ setupu: ostatnia świeca 4H z {bars_snapshot_time}</div>
      </div>
      <div style="text-align:right">
        <div style="background:{score_bg};color:{score_color};border-radius:6px;
                    padding:5px 12px;font-size:11px;font-weight:700;letter-spacing:0.5px">
          {score_label}
        </div>
        <div style="margin-top:6px;font-size:20px;color:{star_color}">{stars}</div>
        <div style="font-size:10px;color:#6c757d">{rank_caption}</div>
      </div>
    </div>

    <div style="display:flex;align-items:baseline;gap:12px;margin-top:14px">
      <div style="font-size:28px;font-weight:700;color:#212529">{_fmt(price)} {currency}</div>
      {changes_html}
    </div>
    {quality_cards_html}
    {scanner_scope_note}
  </div>

  <div class="two-col">
    <!-- ── LEFT COLUMN ── -->
    <div>
      <!-- Probability -->
      <div class="card">
        <div class="section-title">{history_primary_title}</div>
        {history_primary_html}
      </div>

      {history_secondary_card}

      <div class="card">
        <div class="section-title">🌍 Reżim makro dla regionu</div>
        {_build_macro_section(macro, 'opportunity_adjustment')}
      </div>

      <div class="card">
        <div class="section-title">🏢 Spółka i wydarzenia</div>
        {_build_company_section(company, 'opportunity_adjustment')}
      </div>

      <!-- Criteria -->
      <div class="card">
        <div class="section-title">✅ Analiza kryteriów okazji</div>
        {criteria_html}
      </div>
    </div>

    <!-- ── RIGHT COLUMN ── -->
    <div>
      {_value_section_html(value_snapshot, currency)}

      <!-- Gauges -->
      <div class="card">
        <div class="section-title">📈 Wskaźniki techniczne — poziomy</div>
        {gauges_html}
        <div style="margin-top:12px">
          {indicators_html}
        </div>
      </div>

      <!-- Support / Resistance -->
      <div class="card">
        <div class="section-title">🎯 Wsparcia i opory (ostatnie 60 sesji)</div>
        {sr_html if sr_html else '<div style="color:#6c757d;font-size:11px">Brak danych</div>'}
      </div>

      <!-- Fundamentals -->
      <div class="card">
        <div class="section-title">🏢 Dane fundamentalne</div>
        {fund_html}
      </div>
    </div>
  </div>

</div>
</body>
</html>"""
    return page_html


# ─────────────────────────────────────────────────────────────────────────────
class OpportunityDetailDialog(QDialog):
  """Modal dialog showing full deep analysis for one ticker."""

  def __init__(self, ticker: str, parent=None, profile: str = "balanced", horizon: str = "all", price_snapshot: dict | None = None, source_context: str = "opportunities"):
    super().__init__(parent)
    self.setWindowTitle(f"Analiza okazji — {ticker}")
    self.resize(980, 780)
    self.setMinimumSize(800, 600)
    self._ticker = ticker
    self._profile = profile
    self._horizon = horizon
    self._price_snapshot = dict(price_snapshot) if isinstance(price_snapshot, dict) else None
    self._source_context = str(source_context or "opportunities").strip().lower() or "opportunities"
    self._temp_file = None
    self._fundamental_temp_file = None

    layout = QVBoxLayout(self)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    # Tabs
    self._tabs = QTabWidget()
    self._tabs.setStyleSheet("""
        QTabWidget::panel {
            border: none;
            background: #ffffff;
        }
        QTabBar::tab {
            background: #f1f5f9;
            color: #64748b;
            padding: 10px 20px;
            margin-right: 2px;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            font-size: 12px;
            font-weight: 600;
        }
        QTabBar::tab:selected {
            background: #ffffff;
            color: #2563eb;
            border-bottom: 2px solid #2563eb;
        }
        QTabBar::tab:hover {
            background: #e2e8f0;
            color: #0f172a;
        }
    """)
    layout.addWidget(self._tabs)

    # Tab 1: Główna Analiza
    self._web = QWebEngineView()
    self._tabs.addTab(self._web, "Podsumowanie i Wycena")

    # Tab 2: Analiza Fundamentalna
    self._fundamental_web = QWebEngineView()
    self._tabs.addTab(self._fundamental_web, "Analiza Fundamentalna")

    btn_bar = QHBoxLayout()
    btn_bar.setContentsMargins(12, 8, 12, 8)
    btn_bar.addStretch()
    close_btn = QPushButton("Zamknij")
    close_btn.setObjectName("refreshBtn")
    close_btn.clicked.connect(self.accept)
    btn_bar.addWidget(close_btn)
    layout.addLayout(btn_bar)

    self._show_loading()
    self._start_analysis()

  def _show_loading(self):
    loading_html = f"""<html><body style="background:#f8f9fa;
      display:flex;flex-direction:column;align-items:center;justify-content:center;
      height:100vh;font-family:sans-serif;color:#6c757d">
      <div style="font-size:32px;margin-bottom:16px">⏳</div>
      <div style="font-size:16px;font-weight:600">Ładowanie głębokiej analizy…</div>
      <div style="font-size:12px;margin-top:8px">{self._ticker}</div>
      <div style="font-size:11px;margin-top:12px;color:#adb5bd">Trwa backtest historyczny i obliczanie wskaźników</div>
    </body></html>"""
    self._web.setHtml(loading_html)
    self._fundamental_web.setHtml(loading_html)

  def _start_analysis(self):
    self._worker = DeepAnalysisWorker(self._ticker, self._profile, self._horizon, self._price_snapshot)
    self._worker.done.connect(self._on_done)
    self._worker.error.connect(self._on_error)
    self._worker.start()

  def _on_done(self, data: dict):
    payload = dict(data)
    payload["detail_context"] = self._source_context
    
    # Render Tab 1 HTML
    html_content = _build_detail_html(payload)
    if self._temp_file and os.path.exists(self._temp_file):
      try:
        os.unlink(self._temp_file)
      except OSError:
        pass
    with tempfile.NamedTemporaryFile(
      mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as f:
      f.write(html_content)
      self._temp_file = f.name
    self._web.load(QUrl.fromLocalFile(self._temp_file))

    # Render Tab 2 (Fundamental Dashboard) HTML
    fundamental_data = payload.get("fundamental_data")
    if fundamental_data:
      from ui.fundamental_dashboard import _build_fundamental_html
      fund_html = _build_fundamental_html(payload, self._ticker)
      
      if self._fundamental_temp_file and os.path.exists(self._fundamental_temp_file):
        try:
          os.unlink(self._fundamental_temp_file)
        except OSError:
          pass
      with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
      ) as f:
        f.write(fund_html)
        self._fundamental_temp_file = f.name
      self._fundamental_web.load(QUrl.fromLocalFile(self._fundamental_temp_file))
    else:
      self._fundamental_web.setHtml("""<html><body style="background:#f8f9fa;
        display:flex;align-items:center;justify-content:center;height:100vh;
        font-family:sans-serif;color:#6c757d">Brak danych analizy fundamentalnej</body></html>""")

  def _on_error(self, message: str):
    error_html = f"""<html><body style="background:#fff5f5;
      display:flex;flex-direction:column;align-items:center;justify-content:center;
      height:100vh;font-family:sans-serif;color:#dc2626">
      <div style="font-size:32px;margin-bottom:16px">⚠️</div>
      <div style="font-size:16px;font-weight:600">Błąd analizy</div>
      <div style="font-size:12px;margin-top:8px;color:#6c757d">{message}</div>
    </body></html>"""
    self._web.setHtml(error_html)
    self._fundamental_web.setHtml(error_html)

  def closeEvent(self, event):
    if self._temp_file and os.path.exists(self._temp_file):
      try:
        os.unlink(self._temp_file)
      except OSError:
        pass
    if self._fundamental_temp_file and os.path.exists(self._fundamental_temp_file):
      try:
        os.unlink(self._fundamental_temp_file)
      except OSError:
        pass
    super().closeEvent(event)
