"""ui/sidebar.py — Left panel: Search, Watchlist, Opportunity, Portfolio tabs."""
import html
import sys
import os
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
from pathlib import Path
from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLineEdit,
    QListWidget, QListWidgetItem, QLabel, QPushButton, QProgressBar,
    QScrollArea, QFrame, QSizePolicy, QAbstractItemView, QTabBar,
    QDialog, QTextBrowser, QMessageBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize, QUrl
from PyQt6.QtGui import QIcon, QColor
from PyQt6.QtWebEngineWidgets import QWebEngineView
import config
from analysis.ai_portfolio_advice import (
    load_ai_advice_settings,
    resolve_ai_model,
    scan_market_opportunities_with_ai,
)
from data.fetcher import search_tickers
from analysis.opportunity import (
    OPPORTUNITY_PROFILES,
    describe_opportunity_rank_advantage,
    get_opportunity_display_state,
    get_opportunity_rank_breakdown,
    get_opportunity_rank_score,
    scan_universe,
)
from analysis.value_investing import scan_value_universe
from .portfolio_tab import PortfolioTab

WATCHLIST_FILE = Path.home() / ".analizator_rynkowy" / "watchlist.json"


def _load_watchlist() -> list[dict]:
    try:
        if WATCHLIST_FILE.exists():
            with open(WATCHLIST_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save_watchlist(items: list[dict]):
    WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(items, f, indent=2)


# ──────────────────────────────────────────────────────────────────────────────
class SearchWorker(QThread):
    results_ready = pyqtSignal(list)

    def __init__(self, query: str):
        super().__init__()
        self._query = query

    def run(self):
        results = search_tickers(self._query)
        self.results_ready.emit(results)


class ScanWorker(QThread):
    progress = pyqtSignal(int, int, str)   # i, total, ticker
    finished = pyqtSignal(dict)

    def __init__(self, profile: str = "balanced", horizon: str = "22d", mode: str = "classic"):
        super().__init__()
        self._profile = profile
        self._horizon = horizon
        self._mode = mode if mode in {"classic", "value", "mixed"} else "classic"

    def run(self):
        rank_horizon = self._horizon if self._horizon in {"5d", "22d", "66d"} else "all"
        total = len(config.SCANNER_UNIVERSE)

        def classic_cb(completed, _total, ticker):
            self.progress.emit(completed, total * 3, f"Technika · {ticker}")

        classic_results = scan_universe(
            config.SCANNER_UNIVERSE,
            progress_callback=classic_cb,
            horizon=rank_horizon,
            profile=self._profile,
        )

        def mixed_cb(completed, _total, ticker):
            self.progress.emit(total + completed, total * 3, f"Mieszany · {ticker}")

        mixed_source_results = scan_universe(
            config.SCANNER_UNIVERSE,
            progress_callback=mixed_cb,
            horizon=rank_horizon,
            profile=self._profile,
            setup_mode="mixed",
        )

        def value_cb(completed, _total, ticker):
            self.progress.emit(total * 2 + completed, total * 3, f"Value · {ticker}")

        value_results = scan_value_universe(config.SCANNER_UNIVERSE, progress_callback=value_cb)
        self.finished.emit({
            "mode": self._mode,
            "classic_results": classic_results,
            "mixed_source_results": mixed_source_results,
            "value_results": value_results,
        })


class AIOpportunityScanWorker(QThread):
    done = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, tickers: list[str], settings: dict):
        super().__init__()
        self._tickers = [str(item or "").upper().strip() for item in tickers if str(item or "").strip()]
        self._settings = dict(settings)

    def run(self):
        try:
            self.done.emit(scan_market_opportunities_with_ai(self._tickers, self._settings))
        except Exception as exc:
            import traceback

            self.error.emit(traceback.format_exc() if str(exc).strip() else repr(exc))


class ValueInvestingScanWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(list)

    def run(self):
        def cb(completed, total, ticker):
            self.progress.emit(completed, total, ticker)

        results = scan_value_universe(config.SCANNER_UNIVERSE, progress_callback=cb)
        self.finished.emit(results)


# ──────────────────────────────────────────────────────────────────────────────
class TickerListItem(QListWidgetItem):
    def __init__(self, ticker: str, name: str, badge: str = ""):
        label = f"{ticker}  —  {name}" if name and name != ticker else ticker
        if badge:
            label = f"{label}  [{badge}]"
        super().__init__(label)
        self.ticker = ticker
        self.setToolTip(f"{ticker}\n{name}")


def _render_score_stars(score: int, max_score: int = 9, star_slots: int = 5) -> str:
    clamped = max(0, min(int(score or 0), max_score))
    filled = int(round((clamped / max_score) * star_slots)) if max_score > 0 else 0
    filled = max(0, min(star_slots, filled))
    return "★" * filled + "☆" * (star_slots - filled)


class _StretchTabBar(QTabBar):
    """Expand tabs to fill the sidebar width without increasing minimum width."""

    def sizeHint(self) -> QSize:  # type: ignore[override]
        hint = super().sizeHint()
        parent = self.parentWidget()
        if parent is None:
            return hint
        return QSize(max(hint.width(), parent.width()), hint.height())

    def tabSizeHint(self, index: int) -> QSize:  # type: ignore[override]
        hint = super().tabSizeHint(index)
        parent = self.parentWidget()
        if parent is None or self.count() == 0:
            return hint
        width = max(hint.width(), parent.width() // self.count())
        return QSize(width, hint.height())


def _fmt_snapshot_time(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return text[:16].replace("T", " ")


def _short_text(value: str | None, limit: int = 96) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


class AIOpportunityDetailDialog(QDialog):
    def __init__(self, result: dict, scan_result: dict, parent=None):
        super().__init__(parent)
        self._result = dict(result)
        self._scan_result = dict(scan_result)

        ticker = str(result.get("ticker") or "—").upper()
        self.setWindowTitle(f"Skan okazji AI — {ticker}")
        self.resize(900, 760)
        self.setMinimumSize(760, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        browser = QTextBrowser()
        browser.setReadOnly(True)
        browser.setOpenExternalLinks(True)
        browser.setOpenLinks(True)
        browser.setHtml(self._build_html())
        layout.addWidget(browser, 1)

        close_btn = QPushButton("Zamknij")
        close_btn.setObjectName("refreshBtn")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _list_html(self, items: list[str], empty_label: str) -> str:
        cleaned = [html.escape(str(item).strip()) for item in items if str(item).strip()]
        if not cleaned:
            return f"<div class='empty-note'>{html.escape(empty_label)}</div>"
        return "<ul class='detail-list'>" + "".join(f"<li>{item}</li>" for item in cleaned) + "</ul>"

    def _smart_money_html(self, rows: list[dict]) -> str:
        if not rows:
            return "<div class='empty-note'>Brak czytelnych sygnałów od dużych graczy w odpowiedzi AI.</div>"
        cards = []
        for row in rows:
            signal = str(row.get("signal") or "BRAK").upper()
            badge_class = {
                "KUPUJE": "badge-positive",
                "SPRZEDAJE": "badge-negative",
                "MIESZANE": "badge-mixed",
            }.get(signal, "badge-neutral")
            cards.append(
                "<div class='subcard'>"
                f"<div><span class='badge {badge_class}'>{html.escape(signal)}</span>"
                f" <strong>{html.escape(str(row.get('title') or 'Sygnał rynku'))}</strong></div>"
                f"<div class='subtext'>{html.escape(str(row.get('why') or ''))}</div>"
                "</div>"
            )
        return "".join(cards)

    def _events_html(self, rows: list[dict]) -> str:
        if not rows:
            return "<div class='empty-note'>Brak dodatkowych wydarzeń przypisanych przez AI.</div>"
        cards = []
        for row in rows:
            impact = str(row.get("impact") or "NEUTRALNA").upper()
            reaction = str(row.get("price_reaction") or "MIESZANY").upper()
            impact_class = {
                "POZYTYWNA": "badge-positive",
                "NEGATYWNA": "badge-negative",
            }.get(impact, "badge-neutral")
            reaction_class = {
                "WZROST": "badge-up",
                "SPADEK": "badge-down",
            }.get(reaction, "badge-mixed")
            cards.append(
                "<div class='subcard'>"
                f"<div><strong>{html.escape(str(row.get('title') or 'Wydarzenie'))}</strong></div>"
                f"<div style='margin-top:6px'><span class='badge {impact_class}'>{html.escape(impact)}</span>"
                f" <span class='badge {reaction_class}'>{html.escape(reaction)}</span></div>"
                f"<div class='subtext'>{html.escape(str(row.get('why') or ''))}</div>"
                "</div>"
            )
        return "".join(cards)

    def _sources_html(self, rows: list[dict]) -> str:
        if not rows:
            return "<div class='empty-note'>Brak linków źródłowych zwróconych przez dostawcę AI.</div>"
        links = []
        for row in rows:
            uri = str(row.get("uri") or "").strip()
            if not uri:
                continue
            title = str(row.get("title") or uri).strip()
            links.append(f"<li><a href='{html.escape(uri)}'>{html.escape(title)}</a></li>")
        if not links:
            return "<div class='empty-note'>Brak linków źródłowych zwróconych przez dostawcę AI.</div>"
        return "<ul class='detail-list'>" + "".join(links) + "</ul>"

    def _build_html(self) -> str:
        result = self._result
        scan_result = self._scan_result
        structured = scan_result.get("structured") or {}
        market_view = structured.get("market_view") or {}
        price = result.get("price")
        price_text = f"{float(price):.2f}" if price is not None else "—"
        internet_label = "TAK" if (structured.get("internet_checked") or scan_result.get("used_web_search")) else "NIEPOTWIERDZONE"
        score = int(result.get("conviction_score") or 0)
        sources = scan_result.get("sources") or []

        verdict = html.escape(str(result.get("verdict") or "OBSERWUJ"))
        confidence = html.escape(str(result.get("confidence") or "SREDNIA"))

        return f"""
        <html>
        <head><meta charset='utf-8'></head>
        <body class='ai-body'>
          <style>
            body.ai-body {{ background:#f4f6fb; color:#18212f; font-family:"Helvetica Neue",Arial,sans-serif; font-size:13px; margin:0; padding:18px; }}
            .hero {{ background:linear-gradient(135deg,#ffffff 0%,#eef4ff 100%); border:1px solid #d8e2f0; border-radius:18px; padding:18px; box-shadow:0 10px 26px rgba(15,23,42,.05); }}
            .hero h1 {{ margin:0 0 8px 0; font-size:24px; color:#0f172a; }}
            .hero p {{ margin:0; color:#526072; line-height:1.7; }}
            .summary-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-top:14px; }}
            .summary-chip {{ background:#fff; border:1px solid #dce3ef; border-radius:14px; padding:12px 14px; }}
            .summary-label {{ font-size:10px; text-transform:uppercase; letter-spacing:.06em; color:#64748b; margin-bottom:6px; font-weight:700; }}
            .summary-value {{ font-size:14px; font-weight:800; color:#0f172a; line-height:1.35; }}
            .section {{ margin-top:14px; background:#fff; border:1px solid #d8e2f0; border-radius:18px; padding:18px; box-shadow:0 10px 26px rgba(15,23,42,.05); }}
            .section h2 {{ margin:0 0 10px 0; font-size:18px; color:#0f172a; }}
            .section p {{ margin:0; color:#334155; line-height:1.75; }}
            .detail-list {{ margin:0; padding-left:18px; color:#334155; }}
            .detail-list li {{ margin:0 0 7px 0; line-height:1.7; }}
            .subcard {{ background:#f8fbff; border:1px solid #dce6f6; border-radius:12px; padding:12px 14px; margin:10px 0 0 0; }}
            .subtext {{ color:#475569; line-height:1.7; margin-top:6px; }}
            .badge {{ display:inline-block; padding:3px 8px; border-radius:999px; font-size:10px; font-weight:800; letter-spacing:.04em; margin-right:6px; vertical-align:middle; }}
            .badge-positive {{ background:#dcfce7; color:#166534; }}
            .badge-negative {{ background:#fee2e2; color:#b91c1c; }}
            .badge-neutral {{ background:#e2e8f0; color:#334155; }}
            .badge-up {{ background:#dbeafe; color:#1d4ed8; }}
            .badge-down {{ background:#ffedd5; color:#c2410c; }}
            .badge-mixed {{ background:#ede9fe; color:#6d28d9; }}
            .empty-note {{ color:#64748b; }}
            a {{ color:#2563eb; text-decoration:none; }}
          </style>
          <div class='hero'>
            <h1>{html.escape(str(result.get('ticker') or '—'))} · {html.escape(str(result.get('name') or '—'))}</h1>
            <p>{html.escape(str(result.get('summary') or 'Brak podsumowania od AI.'))}</p>
            <div class='summary-grid'>
              <div class='summary-chip'><div class='summary-label'>Werdykt AI</div><div class='summary-value'>{verdict}</div></div>
              <div class='summary-chip'><div class='summary-label'>Pewność</div><div class='summary-value'>{confidence}</div></div>
              <div class='summary-chip'><div class='summary-label'>Conviction</div><div class='summary-value'>{score}/100</div></div>
              <div class='summary-chip'><div class='summary-label'>Cena / waluta</div><div class='summary-value'>{price_text} {html.escape(str(result.get('currency') or 'USD'))}</div></div>
              <div class='summary-chip'><div class='summary-label'>Internet</div><div class='summary-value'>{internet_label}</div></div>
              <div class='summary-chip'><div class='summary-label'>Model</div><div class='summary-value'>{html.escape(str(scan_result.get('model') or '—'))}</div></div>
            </div>
          </div>

          <div class='section'>
            <h2>Dlaczego teraz</h2>
            <p>{html.escape(str(result.get('why_now') or 'Brak dodatkowego komentarza AI.'))}</p>
          </div>

          <div class='section'>
            <h2>Katalizatory</h2>
            {self._list_html(result.get('catalysts') or [], 'AI nie zwróciło dodatkowych katalizatorów.')}
          </div>

          <div class='section'>
            <h2>Ryzyka</h2>
            {self._list_html(result.get('risks') or [], 'Brak dodatkowych ryzyk wskazanych przez AI.')}
          </div>

          <div class='section'>
            <h2>Duzi gracze / smart money</h2>
            {self._smart_money_html(result.get('smart_money') or [])}
          </div>

          <div class='section'>
            <h2>Wydarzenia i możliwa reakcja rynku</h2>
            {self._events_html(result.get('events') or [])}
          </div>

          <div class='section'>
            <h2>Obraz rynku z tego skanu</h2>
            <p>{html.escape(str(market_view.get('summary') or 'Brak szerokiego podsumowania rynku.'))}</p>
            <div class='subcard'><strong>Gorące tematy</strong>{self._list_html(market_view.get('hot_themes') or [], 'Brak dodatkowych tematów.')}</div>
            <div class='subcard'><strong>Główne ryzyka</strong>{self._list_html(market_view.get('risk_themes') or [], 'Brak dodatkowych ryzyk na poziomie rynku.')}</div>
          </div>

          <div class='section'>
            <h2>Źródła wykryte przez AI dla całego skanu</h2>
            {self._sources_html(sources)}
          </div>
        </body>
        </html>
        """


class ValueInvestingDetailDialog(QDialog):
    def __init__(self, result: dict, parent=None):
        super().__init__(parent)
        self._result = dict(result)
        self._temp_file = None
        ticker = str(result.get("ticker") or "—").upper()
        self.setWindowTitle(f"Value Investing — {ticker}")
        self.resize(980, 820)
        self.setMinimumSize(820, 660)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._web = QWebEngineView()
        layout.addWidget(self._web, 1)

        self._render_html()

        close_btn = QPushButton("Zamknij")
        close_btn.setObjectName("refreshBtn")
        close_btn.clicked.connect(self.accept)
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(12, 8, 12, 8)
        btn_bar.addStretch()
        btn_bar.addWidget(close_btn)
        layout.addLayout(btn_bar)

    def _render_html(self):
        content = self._build_html()
        if self._temp_file and os.path.exists(self._temp_file):
            try:
                os.unlink(self._temp_file)
            except OSError:
                pass
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as handle:
            handle.write(content)
            self._temp_file = handle.name
        self._web.load(QUrl.fromLocalFile(self._temp_file))

    def _fmt_num(self, value, digits: int = 2, suffix: str = "") -> str:
        if value in (None, ""):
            return "—"
        try:
            return f"{float(value):,.{digits}f}{suffix}".replace(",", " ")
        except Exception:
            return "—"

    def _fmt_pct(self, value, digits: int = 1) -> str:
        if value in (None, ""):
            return "—"
        try:
            return f"{float(value):+,.{digits}f}%".replace(",", " ")
        except Exception:
            return "—"

    def _fmt_cap(self, value) -> str:
        if value in (None, ""):
            return "—"
        try:
            number = float(value)
        except Exception:
            return "—"
        if abs(number) >= 1e12:
            return f"{number / 1e12:.2f}T"
        if abs(number) >= 1e9:
            return f"{number / 1e9:.2f}B"
        if abs(number) >= 1e6:
            return f"{number / 1e6:.2f}M"
        return f"{number:,.0f}".replace(",", " ")

    def _list_html(self, items: list[str], empty_label: str, class_name: str = "detail-list") -> str:
        cleaned = [html.escape(str(item).strip()) for item in items if str(item).strip()]
        if not cleaned:
            return f"<div class='empty-note'>{html.escape(empty_label)}</div>"
        return f"<ul class='{class_name}'>" + "".join(f"<li>{item}</li>" for item in cleaned) + "</ul>"

    def _sources_html(self, rows: list[dict]) -> str:
        if not rows:
            return "<div class='empty-note'>Brak dodatkowych źródeł fair value; ranking opiera się na aktualnych fundamentach.</div>"
        cards = []
        for row in rows:
            label = html.escape(str(row.get("label") or "Źródło fair value"))
            fair_price = self._fmt_num(row.get("fair_price"), 2)
            note = html.escape(str(row.get("note") or ""))
            cards.append(
                "<div class='subcard'>"
                f"<div><strong>{label}</strong></div>"
                f"<div class='big-number'>{fair_price}</div>"
                f"<div class='subtext'>{note}</div>"
                "</div>"
            )
        return "".join(cards)

    def _events_html(self, rows: list[dict]) -> str:
        if not rows:
            return "<div class='empty-note'>Brak istotnych ostatnich eventów w warstwie fundamentalnej.</div>"
        cards = []
        for row in rows[:4]:
            cards.append(
                "<div class='subcard'>"
                f"<div><strong>{html.escape(str(row.get('title') or 'Wydarzenie'))}</strong></div>"
                f"<div class='subtext'>{html.escape(str(row.get('summary') or ''))}</div>"
                "</div>"
            )
        return "".join(cards)

    def _metrics_html(self, rows: list[tuple[str, str]]) -> str:
        return "".join(
            f"<div class='metric-row'><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>"
            for label, value in rows
        )

    def _parameter_guide_html(self) -> str:
        rows = [
            ("Value score", "Główny wynik 0-100. Łączy wycenę, jakość biznesu i siłę bilansu. Im wyżej, tym spółka bardziej pasuje do stylu value."),
            ("Fair value proxy", "Uproszczony szacunek wartości wewnętrznej. To nie pełny model DCF, tylko średnia ważona kilku kotwic wyceny."),
            ("Margines bezpieczeństwa", "Różnica między fair value proxy a obecną ceną. Dodatni oznacza potencjalne niedowartościowanie, ujemny mówi, że cena jest już ponad naszym proxy."),
            ("Ryzyko value trap", "Ryzyko, że spółka jest tania tylko pozornie. Wysoki wynik ostrzega, że słaba jakość, bilans lub eventy mogą tłumaczyć niską cenę."),
            ("Trend kwartalny", "Miękka ocena ostatnich 4 kwartałów. Seryjne pogarszanie przychodów, zysku netto albo OCF obniża wynik i podbija ryzyko value trap."),
            ("Akcjonariat / insiderzy", "Miękki overlay conviction. Pokazuje, czy insiderzy i instytucje wspierają case inwestycyjny, ale nie jest twardym filtrem zwłaszcza na GPW."),
            ("Konsensus analityków", "Średni target cenowy analityków. To jedna z kotwic fair value, ale nie jedyna."),
            ("Siła zysków", "Wycena oparta na EPS i oszacowanym fair P/E. Pokazuje, ile rynek mógłby płacić za zyski przy zdrowszej wycenie."),
            ("Free cash flow", "Kotwica wartości oparta na wolnej gotówce generowanej przez biznes. Im mocniejszy cash flow względem kapitalizacji, tym lepiej dla inwestora value."),
            ("Gotówka operacyjna", "Dodatkowa kotwica fair value oparta na operating cash flow. Ma pilnować, czy księgowy zysk faktycznie zamienia się w gotówkę."),
            ("EV/EBITDA", "Konserwatywna kotwica wartości przedsiębiorstwa. Łączy zysk operacyjny przed amortyzacją z korektą o gotówkę i dług netto."),
            ("P/E i Forward P/E", "Cena do zysku historycznego i oczekiwanego. Nisko może być atrakcyjne, ale samo w sobie nie wystarcza."),
            ("PEG", "P/E skorygowane o tempo wzrostu. Pomaga odróżnić drogie spółki wzrostowe od tych, które są drogie bez uzasadnienia."),
            ("P/B", "Cena do wartości księgowej. Niskie P/B może wspierać tezę value, ale przy słabym biznesie bywa pułapką."),
            ("Book value / akcję", "Wartość księgowa przypadająca na jedną akcję. To konserwatywna kotwica majątkowa."),
            ("P/S", "Cena do sprzedaży. Przydatne, gdy zyski są chwilowo zaburzone, ale sam przychód nie mówi jeszcze nic o jakości marży."),
            ("FCF yield", "Free cash flow jako procent kapitalizacji. Wyższy yield zwykle wzmacnia argument, że cena jest atrakcyjna."),
            ("OCF / zysk netto", "Pokazuje, czy raportowany zysk naprawdę materializuje się w gotówce. Niski wskaźnik to klasyczny sygnał value trap."),
            ("Net debt / EBITDA", "Mierzy obciążenie biznesu długiem względem operacyjnej zdolności generowania gotówki. Im niżej, tym bezpieczniej."),
            ("D/E", "Zadłużenie do kapitału własnego. Im wyżej, tym większe ryzyko, że taniość wynika z presji finansowej."),
            ("Current ratio / Quick ratio", "Miary płynności. Pokazują, czy spółka jest w stanie pokrywać zobowiązania krótkoterminowe bez stresu."),
            ("ROE", "Zwrot na kapitale własnym. Wysokie ROE wspiera tezę, że biznes dobrze wykorzystuje kapitał."),
            ("ROA", "Zwrot na aktywach. Lepszy od samego ROE do oddzielenia realnej jakości biznesu od efektu dźwigni finansowej."),
            ("Marża brutto", "Pierwszy test siły modelu biznesowego i pricing power. Wysoka marża brutto często wspiera tezę o przewadze konkurencyjnej."),
            ("Payout ratio", "Pokazuje, jaka część zysków lub gotówki jest oddawana akcjonariuszom. Zbyt wysoki payout zwiększa ryzyko cięcia dywidendy."),
            ("Marża netto", "Ile z każdej jednostki sprzedaży zostaje jako zysk netto. Niskie lub spadające marże często psują case value."),
            ("Wzrost przychodów / zysków", "Tempo wzrostu biznesu. W value nie musi być wysokie, ale trwały spadek zwiększa ryzyko value trap."),
        ]
        items = []
        for label, description in rows:
            items.append(
                "<div class='guide-row'>"
                f"<div class='guide-label'>{html.escape(label)}</div>"
                f"<div class='guide-text'>{html.escape(description)}</div>"
                "</div>"
            )
        return "".join(items)

    def _build_html(self) -> str:
        result = self._result
        currency = html.escape(str(result.get("currency") or "USD"))
        verdict = html.escape(str(result.get("verdict") or "Neutralna wycena"))
        price = self._fmt_num(result.get("price"), 2)
        fair_value = self._fmt_num(result.get("fair_value_price"), 2)
        margin = self._fmt_pct(result.get("margin_of_safety_pct"))
        score = float(result.get("value_score") or 0.0)
        trap_risk = float(result.get("value_trap_risk") or 0.0)
        quarterly_trend_summary = html.escape(str(result.get("quarterly_trend_summary") or "Brak 4 pełnych kwartałów do testu trendu."))
        ownership_summary = html.escape(str(result.get("ownership_summary") or "Brak stabilnych danych insider/holders dla tego rynku."))

        if score >= 72:
            verdict_bg = "#dcfce7"
            verdict_fg = "#166534"
        elif "trap" in verdict.lower():
            verdict_bg = "#fee2e2"
            verdict_fg = "#b91c1c"
        elif score >= 58:
            verdict_bg = "#fff7ed"
            verdict_fg = "#c2410c"
        else:
            verdict_bg = "#e2e8f0"
            verdict_fg = "#334155"

        metrics_left = [
            ("P/E", self._fmt_num(result.get("pe_ratio"), 1)),
            ("Forward P/E", self._fmt_num(result.get("forward_pe"), 1)),
            ("PEG", self._fmt_num(result.get("peg_ratio"), 2)),
            ("P/B", self._fmt_num(result.get("price_to_book"), 2)),
            ("Book value / akcję", self._fmt_num(result.get("book_value"), 2)),
            ("Dyskonto do book value", self._fmt_pct(result.get("book_discount_pct"))),
            ("P/S", self._fmt_num(result.get("price_to_sales"), 2)),
            ("Kapitalizacja", self._fmt_cap(result.get("market_cap"))),
        ]
        metrics_right = [
            ("FCF yield", self._fmt_pct(result.get("fcf_yield_pct"), 1)),
            ("D/E", self._fmt_num(result.get("debt_to_equity"), 1)),
            ("Current ratio", self._fmt_num(result.get("current_ratio"), 2)),
            ("Quick ratio", self._fmt_num(result.get("quick_ratio"), 2)),
            ("ROE", self._fmt_pct(result.get("roe_pct"), 1)),
            ("Marża netto", self._fmt_pct(result.get("profit_margin_pct"), 1)),
            ("Wzrost przychodów", self._fmt_pct(result.get("revenue_growth_pct"), 1)),
            ("Wzrost zysków", self._fmt_pct(result.get("earnings_growth_pct"), 1)),
        ]
        analyst_text = (
            f"{self._fmt_num(result.get('target_mean_price'), 2)} {currency} · {self._fmt_pct(result.get('target_upside_pct'))}"
            if result.get("target_mean_price") is not None and int(result.get("analyst_count") or 0) > 0
            else "Brak szerokiego konsensusu"
        )

        if "trap" in verdict.lower():
            value_bg = "#fff5f5"
            value_fg = "#dc2626"
            value_border = "#fecaca"
        elif score >= 72:
            value_bg = "#f0fdf4"
            value_fg = "#166534"
            value_border = "#bbf7d0"
        elif score >= 58:
            value_bg = "#fff7ed"
            value_fg = "#c2410c"
            value_border = "#fdba74"
        else:
            value_bg = "#f8fafc"
            value_fg = "#334155"
            value_border = "#cbd5e1"

        fair_sources_html = ""
        for source in result.get("fair_value_sources") or []:
            fair_sources_html += f"""
            <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px;margin-top:8px">
                <div style="font-size:11px;font-weight:700;color:#0f172a">{html.escape(str(source.get('label') or 'Źródło fair value'))}</div>
                <div style="font-size:15px;font-weight:800;color:#111827;margin-top:3px">{self._fmt_num(source.get('fair_price'))} {currency}</div>
                <div style="font-size:10px;color:#6c757d;line-height:1.6;margin-top:4px">{html.escape(str(source.get('note') or ''))}</div>
            </div>"""
        if not fair_sources_html:
            fair_sources_html = '<div style="font-size:10px;color:#6c757d;margin-top:8px">Brak dodatkowych źródeł fair value; sekcja opiera się na jakości biznesu i dostępnych mnożnikach.</div>'

        strengths_html = "".join(
            f"<li style='margin-left:18px;margin-top:6px;color:#166534;line-height:1.6'>{html.escape(str(item))}</li>"
            for item in (result.get("strengths") or [])[:4]
            if str(item).strip()
        ) or "<div style='font-size:10px;color:#6c757d;margin-top:8px'>Brak podbitych mocnych stron.</div>"

        risks_html = "".join(
            f"<li style='margin-left:18px;margin-top:6px;color:#b91c1c;line-height:1.6'>{html.escape(str(item))}</li>"
            for item in (result.get("risks") or [])[:4]
            if str(item).strip()
        ) or "<div style='font-size:10px;color:#6c757d;margin-top:8px'>Brak podbitych ryzyk.</div>"

        value_metrics_rows = ""
        for label, val in [
            ("Value score", f"{score:.0f}/100"),
            ("Ryzyko value trap", f"{trap_risk:.0f}/100"),
            ("Fair value proxy", f"{fair_value} {currency}" if fair_value != '—' else "—"),
            ("Margines bezpieczeństwa", margin),
            ("Trend kwartalny", f"{self._fmt_num(result.get('quarterly_trend_score'), 0)}/100" if result.get("quarterly_trend_score") is not None else "—"),
            ("Trend kwartalny - opis", quarterly_trend_summary),
            ("Akcjonariat / insiderzy", f"{self._fmt_num(result.get('ownership_score'), 0)}/100" if result.get("ownership_score") is not None else "—"),
            ("Akcjonariat / insiderzy - opis", ownership_summary),
            ("Target analityków", f"{self._fmt_num(result.get('target_mean_price'))} {currency}" if result.get("target_mean_price") is not None else "—"),
            ("Upside do targetu", self._fmt_pct(result.get("target_upside_pct"), 1)),
            ("Forward P/E", self._fmt_num(result.get("forward_pe"), 1)),
            ("P/B", self._fmt_num(result.get("price_to_book"), 2)),
            ("FCF yield", self._fmt_pct(result.get("fcf_yield_pct"), 1)),
            ("ROA", self._fmt_pct(result.get("roa_pct"), 1)),
            ("Marża brutto", self._fmt_pct(result.get("gross_margin_pct"), 1)),
            ("OCF / zysk netto", f"{self._fmt_num(result.get('cash_conversion_ratio'), 2)}x" if result.get("cash_conversion_ratio") is not None else "—"),
            ("Net debt / EBITDA", f"{self._fmt_num(result.get('net_debt_to_ebitda'), 2)}x" if result.get("net_debt_to_ebitda") is not None else "—"),
            ("Payout ratio", self._fmt_pct(result.get("payout_ratio_pct"), 1)),
            ("D/E", self._fmt_num(result.get("debt_to_equity"), 1)),
            ("Quick ratio", self._fmt_num(result.get("quick_ratio"), 2)),
            ("ROE", self._fmt_pct(result.get("roe_pct"), 1)),
        ]:
            value_metrics_rows += f"""
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;padding:4px 0;border-bottom:1px solid #f1f3f5;font-size:11px">
                <span style="color:#6c757d;flex:0 0 40%">{label}</span>
                <span style="font-weight:600;color:#212529;flex:1;text-align:right;line-height:1.45;white-space:normal">{val}</span>
            </div>"""

        company_summary = html.escape(str(result.get("company_summary") or result.get("summary") or "Brak dodatkowego komentarza jakościowego."))

        guide_html = self._parameter_guide_html()

        return f"""
        <html>
                <head><meta charset='utf-8'></head>
        <body class='value-body'>
          <style>
                        * {{ box-sizing:border-box; margin:0; padding:0; }}
                        body.value-body {{ font-family:"Helvetica Neue",Arial,sans-serif; background:#f8f9fa; color:#212529; font-size:12px; padding:0; }}
                        .container {{ max-width:960px; margin:0 auto; padding:14px; }}
                        .card {{ background:#fff; border:1px solid #dee2e6; border-radius:10px; padding:14px; margin-bottom:10px; }}
                        .section-title {{ font-size:10px; font-weight:700; color:#6c757d; text-transform:uppercase; letter-spacing:.5px; margin-bottom:10px; padding-bottom:5px; border-bottom:1px solid #f1f3f5; }}
                        .guide-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
                        .guide-row {{ background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; padding:10px 12px; }}
                        .guide-label {{ font-size:11px; font-weight:700; color:#111827; margin-bottom:5px; }}
                        .guide-text {{ font-size:10px; color:#6c757d; line-height:1.65; }}
                        .event-card {{ background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; padding:10px 12px; margin-top:8px; }}
                        .event-title {{ font-size:11px; font-weight:700; color:#0f172a; }}
                        .event-text {{ font-size:10px; color:#6c757d; line-height:1.7; margin-top:6px; }}
                        @media (max-width: 860px) {{
                            .guide-grid {{ grid-template-columns:1fr; }}
                        }}
          </style>
                    <div class='container'>
                        <div class='card'>
                            <div class='section-title'>💎 Value Investing</div>
                            <div style='background:{value_bg};border:1px solid {value_border};border-radius:10px;padding:12px 14px'>
                                <div style='display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap'>
                                    <div>
                                        <div style='font-size:16px;font-weight:800;color:{value_fg}'>{verdict}</div>
                                        <div style='font-size:11px;color:#6c757d;margin-top:4px;line-height:1.6'>{html.escape(str(result.get('summary') or ''))}</div>
                                    </div>
                                    <div style='display:grid;grid-template-columns:repeat(2,minmax(120px,1fr));gap:8px;min-width:280px;flex:1'>
                                        <div style='background:#ffffffcc;border-radius:8px;padding:8px 10px'>
                                            <div style='font-size:10px;color:#6c757d'>Value score</div>
                                            <div style='font-size:16px;font-weight:800;color:#111827'>{score:.0f}/100</div>
                                        </div>
                                        <div style='background:#ffffffcc;border-radius:8px;padding:8px 10px'>
                                            <div style='font-size:10px;color:#6c757d'>Ryzyko value trap</div>
                                            <div style='font-size:16px;font-weight:800;color:#111827'>{trap_risk:.0f}/100</div>
                                        </div>
                                        <div style='background:#ffffffcc;border-radius:8px;padding:8px 10px'>
                                            <div style='font-size:10px;color:#6c757d'>Fair value proxy</div>
                                            <div style='font-size:16px;font-weight:800;color:#111827'>{fair_value} {currency if fair_value != '—' else ''}</div>
                                        </div>
                                        <div style='background:#ffffffcc;border-radius:8px;padding:8px 10px'>
                                            <div style='font-size:10px;color:#6c757d'>Margines bezpieczeństwa</div>
                                            <div style='font-size:16px;font-weight:800;color:{value_fg}'>{margin}</div>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <div style='display:grid;grid-template-columns:1.05fr .95fr;gap:10px;margin-top:10px'>
                                <div>
                                    <div style='font-size:11px;font-weight:700;color:#6c757d;text-transform:uppercase;letter-spacing:.4px;margin-bottom:6px'>Źródła fair value</div>
                                    {fair_sources_html}
                                    <div style='background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px;margin-top:8px'>
                                        <div style='font-size:11px;font-weight:700;color:#0f172a'>Kontekst biznesu</div>
                                        <div style='font-size:10px;color:#6c757d;line-height:1.7;margin-top:6px'>{company_summary}</div>
                                    </div>
                                </div>
                                <div>
                                    <div style='font-size:11px;font-weight:700;color:#6c757d;text-transform:uppercase;letter-spacing:.4px;margin-bottom:6px'>Wskaźniki value</div>
                                    {value_metrics_rows}
                                </div>
                            </div>

                            <div style='display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px'>
                                <div style='background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:10px 12px'>
                                    <div style='font-size:11px;font-weight:700;color:#166534'>Mocne strony</div>
                                    <ul style='margin:0;padding:0'>{strengths_html}</ul>
                                </div>
                                <div style='background:#fff7ed;border:1px solid #fdba74;border-radius:10px;padding:10px 12px'>
                                    <div style='font-size:11px;font-weight:700;color:#c2410c'>Ryzyka</div>
                                    <ul style='margin:0;padding:0'>{risks_html}</ul>
                                </div>
                            </div>
                        </div>

                        <div class='card'>
                            <div class='section-title'>📘 Jak czytać parametry</div>
                            <div class='guide-grid'>
                                {guide_html}
                            </div>
                        </div>

                        <div class='card'>
                            <div class='section-title'>📰 Ostatnie wydarzenia</div>
                            {''.join(f"<div class='event-card'><div class='event-title'>{html.escape(str(row.get('title') or 'Wydarzenie'))}</div><div class='event-text'>{html.escape(str(row.get('summary') or ''))}</div></div>" for row in (result.get('events') or [])[:4]) or "<div style='font-size:10px;color:#6c757d'>Brak istotnych ostatnich eventów w warstwie fundamentalnej.</div>"}
                        </div>
                    </div>
        </body>
        </html>
        """

        def closeEvent(self, event):
                if self._temp_file and os.path.exists(self._temp_file):
                        try:
                                os.unlink(self._temp_file)
                        except OSError:
                                pass
                super().closeEvent(event)


# ──────────────────────────────────────────────────────────────────────────────
class SearchTab(QWidget):
    ticker_selected = pyqtSignal(str, str)   # ticker, name
    add_to_watchlist = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Szukaj (ticker lub nazwa)…")
        self._search_box.setObjectName("searchBox")
        layout.addWidget(self._search_box)

        # Quick categories
        cats_layout = QHBoxLayout()
        cats_layout.setSpacing(4)
        for label, key in [("US", "us"), ("EU", "eu"), ("ETF", "etf"), ("PL", "pl"), ("Indeksy", "idx")]:
            btn = QPushButton(label)
            btn.setObjectName("catBtn")
            btn.setCheckable(True)
            btn.setMinimumWidth(0)
            btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda checked, k=key: self._load_category(k))
            cats_layout.addWidget(btn)
        layout.addLayout(cats_layout)
        self._list = QListWidget()
        self._list.setObjectName("tickerList")
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self._list)

        actions_row = QHBoxLayout()
        actions_row.setSpacing(6)

        self._add_btn = QPushButton("+ Dodaj do obserwowanych")
        self._add_btn.setObjectName("addBtn")
        self._add_btn.setEnabled(False)
        self._add_btn.setMinimumWidth(0)
        self._add_btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        actions_row.addWidget(self._add_btn)

        self._portfolio_btn = QPushButton("💼 Dodaj do portfela")
        self._portfolio_btn.setObjectName("addBtn")
        self._portfolio_btn.setEnabled(False)
        self._portfolio_btn.setMinimumWidth(0)
        self._portfolio_btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        actions_row.addWidget(self._portfolio_btn)

        layout.addLayout(actions_row)

        # Connections
        self._search_box.textChanged.connect(self._on_text_changed)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._add_btn.clicked.connect(self._on_add_clicked)
        self._portfolio_btn.clicked.connect(self._on_add_portfolio_clicked)

        self._search_worker: SearchWorker | None = None
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._do_search)
        self._selected_ticker = ""

        # Pre-load default US stocks
        self._load_category("us")

    def _clear_selection(self):
        self._selected_ticker = ""
        self._add_btn.setEnabled(False)
        self._portfolio_btn.setEnabled(False)

    def _on_text_changed(self, text: str):
        if len(text) >= 1:
            self._timer.start(400)
        else:
            self._load_category("us")

    def _do_search(self):
        query = self._search_box.text().strip()
        if not query:
            return
        if self._search_worker and self._search_worker.isRunning():
            self._search_worker.terminate()
        self._search_worker = SearchWorker(query)
        self._search_worker.results_ready.connect(self._populate_results)
        self._search_worker.start()

    def _populate_results(self, results: list[dict]):
        self._list.clear()
        self._clear_selection()
        for r in results:
            item = TickerListItem(r["ticker"], r["name"], r.get("type", ""))
            self._list.addItem(item)

    def _load_category(self, key: str):
        self._list.clear()
        self._clear_selection()
        mapping = {
            "us":  config.STOCKS_US_DEFAULT,
            "eu":  config.STOCKS_EU,
            "etf": config.ETFS,
            "pl":  config.STOCKS_PL,
            "idx": config.INDICES_US + config.INDICES_INTL + config.INDICES_PL,
        }
        for entry in mapping.get(key, []):
            item = TickerListItem(entry["ticker"], entry["name"])
            self._list.addItem(item)

    def _on_item_clicked(self, item: TickerListItem):
        self._selected_ticker = item.ticker
        self._add_btn.setEnabled(True)
        self._portfolio_btn.setEnabled(True)
        parts = item.text().split("  —  ")
        ticker = item.ticker
        name = parts[1].split("  [")[0] if len(parts) > 1 else ticker
        self.ticker_selected.emit(ticker, name)

    def _on_item_double_clicked(self, item: TickerListItem):
        self._on_item_clicked(item)
        from .opportunity_detail import OpportunityDetailDialog

        dlg = OpportunityDetailDialog(item.ticker, parent=self, profile="balanced", horizon="all", source_context="search")
        dlg.exec()

    def _on_add_clicked(self):
        selected = self._list.currentItem()
        if not selected:
            return
        parts = selected.text().split("  —  ")
        ticker = selected.ticker
        name = parts[1].split("  [")[0] if len(parts) > 1 else ticker
        self.add_to_watchlist.emit(ticker, name)

    def _on_add_portfolio_clicked(self):
        if not self._selected_ticker:
            selected = self._list.currentItem()
            if selected is None:
                return
            self._selected_ticker = selected.ticker

        widget = self
        while widget is not None:
            if hasattr(widget, "_portfolio_tab"):
                widget._portfolio_tab.add_from_opportunity(self._selected_ticker, None)
                return
            widget = widget.parent()

        from .portfolio_tab import AddHoldingDialog
        from data.portfolio import add_holding

        dlg = AddHoldingDialog(parent=self, prefill_ticker=self._selected_ticker)
        if dlg.exec() == dlg.DialogCode.Accepted:
            values = dlg.get_values()
            add_holding(values["ticker"], values["shares"], values["avg_price"], values["notes"], values["purchase_date"])


# ──────────────────────────────────────────────────────────────────────────────
class WatchlistTab(QWidget):
    ticker_selected = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self._list = QListWidget()
        self._list.setObjectName("tickerList")
        layout.addWidget(self._list)

        remove_btn = QPushButton("Usuń zaznaczony")
        remove_btn.setObjectName("removeBtn")
        remove_btn.clicked.connect(self._remove_selected)
        layout.addWidget(remove_btn)

        self._list.itemClicked.connect(self._on_item_clicked)
        self._watchlist: list[dict] = _load_watchlist()
        self._refresh()

    def _refresh(self):
        self._list.clear()
        for entry in self._watchlist:
            item = TickerListItem(entry["ticker"], entry.get("name", ""))
            self._list.addItem(item)

    def add_ticker(self, ticker: str, name: str):
        if any(e["ticker"] == ticker for e in self._watchlist):
            return
        self._watchlist.append({"ticker": ticker, "name": name})
        _save_watchlist(self._watchlist)
        self._refresh()

    def _remove_selected(self):
        item = self._list.currentItem()
        if not item:
            return
        self._watchlist = [e for e in self._watchlist if e["ticker"] != item.ticker]
        _save_watchlist(self._watchlist)
        self._refresh()

    def _on_item_clicked(self, item: TickerListItem):
        parts = item.text().split("  —  ")
        ticker = item.ticker
        name = parts[1] if len(parts) > 1 else ticker
        self.ticker_selected.emit(ticker, name)


# ──────────────────────────────────────────────────────────────────────────────
_HORIZONS = [
    ("all",  "Wszystkie",  None),
    ("5d",   "1 tydzień",  "win_rate_5d"),
    ("22d",  "1 miesiąc",  "win_rate_22d"),
    ("66d",  "3 miesiące", "win_rate_66d"),
]

_CURRENCIES = [
    ("ALL", "Wszystkie"),
    ("USD", "USD"),
    ("PLN", "PLN"),
    ("EUR", "EUR"),
]

_PROFILE_LABELS = dict(OPPORTUNITY_PROFILES)

_OPPORTUNITY_SCAN_MODES = [
    ("classic", "Podstawowy"),
    ("value", "Value Investing"),
    ("mixed", "Mieszany"),
]


class OpportunityTab(QWidget):
    ticker_selected = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self._info = QLabel("")
        self._info.setWordWrap(True)
        self._info.setObjectName("infoLabel")
        layout.addWidget(self._info)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(3)
        mode_label = QLabel("Tryb:")
        mode_label.setObjectName("infoLabel")
        mode_row.addWidget(mode_label)
        self._mode_btns: dict[str, QPushButton] = {}
        for key, label in _OPPORTUNITY_SCAN_MODES:
            btn = QPushButton(label)
            btn.setObjectName("catBtn")
            btn.setCheckable(True)
            btn.setMinimumWidth(0)
            btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda checked, m=key: self._set_selected_mode(m))
            self._mode_btns[key] = btn
            mode_row.addWidget(btn)
        layout.addLayout(mode_row)

        # ── Horizon filter row ───────────────────────────────────────────
        self._horizon_row_widget = QWidget()
        horizon_row = QHBoxLayout(self._horizon_row_widget)
        horizon_row.setContentsMargins(0, 0, 0, 0)
        horizon_row.setSpacing(3)
        self._horizon_btns: dict[str, QPushButton] = {}
        for key, label, _ in _HORIZONS:
            btn = QPushButton(label)
            btn.setObjectName("catBtn")
            btn.setCheckable(True)
            btn.setMinimumWidth(0)
            btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda checked, k=key: self._set_horizon(k))
            self._horizon_btns[key] = btn
            horizon_row.addWidget(btn)
        layout.addWidget(self._horizon_row_widget)

        currency_row = QHBoxLayout()
        currency_row.setSpacing(3)
        currency_label = QLabel("Waluta:")
        currency_label.setObjectName("infoLabel")
        currency_row.addWidget(currency_label)
        self._currency_btns: dict[str, QPushButton] = {}
        for key, label in _CURRENCIES:
            btn = QPushButton(label)
            btn.setObjectName("catBtn")
            btn.setCheckable(True)
            btn.setMinimumWidth(0)
            btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda checked, c=key: self._set_currency(c))
            self._currency_btns[key] = btn
            currency_row.addWidget(btn)
        layout.addLayout(currency_row)

        self._profile_btns: dict[str, QPushButton] = {}

        self._scan_btn = QPushButton("🔍  Skanuj okazje")
        self._scan_btn.setObjectName("scanBtn")
        self._scan_btn.clicked.connect(self._start_scan)
        layout.addWidget(self._scan_btn)

        self._ai_scan_btn = QPushButton("🧠  Skan okazji AI")
        self._ai_scan_btn.setObjectName("scanBtn")
        self._ai_scan_btn.clicked.connect(self._start_ai_scan)
        layout.addWidget(self._ai_scan_btn)

        self._progress = QProgressBar()
        self._progress.setObjectName("scanProgress")
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setObjectName("statusLabel")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._list = QListWidget()
        self._list.setObjectName("tickerList")
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setWordWrap(True)
        self._list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._list.setUniformItemSizes(False)
        layout.addWidget(self._list)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)

        self._hint = QLabel("")
        self._hint.setObjectName("infoLabel")
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        self._add_portfolio_btn = QPushButton("💼  Dodaj do portfela")
        self._add_portfolio_btn.setObjectName("addBtn")
        self._add_portfolio_btn.setEnabled(False)
        self._add_portfolio_btn.setMinimumWidth(0)
        self._add_portfolio_btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._add_portfolio_btn.clicked.connect(self._on_add_to_portfolio)
        layout.addWidget(self._add_portfolio_btn)

        self._worker: QThread | None = None
        self._classic_results: list[dict] = []
        self._value_results: list[dict] = []
        self._mixed_source_results: list[dict] = []
        self._mixed_results: list[dict] = []
        self._ai_results: list[dict] = []
        self._ai_scan_result: dict = {}
        self._horizon = "22d"
        self._currency = "ALL"
        self._profile = "balanced"
        self._selected_mode = "classic"
        self._active_result_mode = "classic"
        self._classic_scan_ready = False
        self._value_scan_ready = False
        self._mixed_scan_ready = False
        self._classic_scan_completed_at = ""
        self._mixed_scan_completed_at = ""
        self._horizon_btns["22d"].setChecked(True)
        self._currency_btns["ALL"].setChecked(True)
        self._selected_ticker: str = ""
        self._selected_price: float | None = None
        self._set_selected_mode("classic")

    # ── Horizon switching ─────────────────────────────────────────────────

    def _set_horizon(self, key: str):
        self._horizon = key
        for k, btn in self._horizon_btns.items():
            btn.setChecked(k == key)
        if self._active_result_mode == "classic" and self._classic_scan_ready:
            self._render_results(self._classic_results)
        elif self._active_result_mode == "mixed" and self._mixed_scan_ready:
            self._render_mixed_results(self._mixed_results)

    def _set_currency(self, code: str):
        self._currency = code
        for key, btn in self._currency_btns.items():
            btn.setChecked(key == code)
        if self._active_result_mode == "ai" and self._ai_results:
            self._render_ai_results(self._ai_results)
        elif self._active_result_mode == "value" and self._value_scan_ready:
            self._render_value_results(self._value_results)
        elif self._active_result_mode == "mixed" and self._mixed_scan_ready:
            self._render_mixed_results(self._mixed_results)
        elif self._active_result_mode == "classic" and self._classic_scan_ready:
            self._render_results(self._classic_results)

    def _set_profile(self, profile: str):
        self._profile = profile
        for key, btn in self._profile_btns.items():
            btn.setChecked(key == profile)
        if self._active_result_mode == "mixed" and self._mixed_scan_ready:
            self._mixed_results = self._build_mixed_results(self._mixed_source_results, self._value_results)
            self._render_mixed_results(self._mixed_results)
        elif self._active_result_mode == "classic" and self._classic_scan_ready:
            self._render_results(self._classic_results)

    def _clear_selection(self):
        self._selected_ticker = ""
        self._selected_price = None
        self._add_portfolio_btn.setEnabled(False)

    def _is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _mode_info_text(self, mode: str) -> str:
        if mode == "value":
            return (
                "Tryb Value Investing filtruje spółki po niedowartościowaniu, jakości biznesu i ryzyku value trap. "
                "To tryb fundamentalny, bez wymogu technicznego setupu."
            )
        if mode == "mixed":
            return (
                "Tryb mieszany nadal szuka luźniejszego pullbacku, ale ranking liczy teraz 50% Value Investing, "
                "30% Szansa na wzrost z backtestu setupu i 20% wskaźniki techniczne live."
            )
        return (
            "Tryb podstawowy pokazuje okazje jak wcześniej: technika live + historyczny backtest podobnych setupów + "
            "kontekst makro i jakości spółki."
        )

    def _mode_hint_text(self, mode: str) -> str:
        if mode == "value":
            return "Kliknij raz → wykres  ·  Kliknij dwa razy → pełna analiza okazji z techniką i Value Investing"
        if mode == "mixed":
            return "Kliknij raz → wykres  ·  Kliknij dwa razy → analiza okazji; ranking 50% value, 30% backtest setupu, 20% wskaźniki"
        return "Kliknij raz → wykres  ·  Kliknij dwa razy → szczegółowa analiza techniczna lub raport AI"

    def _mode_scan_button_text(self, mode: str) -> str:
        if mode == "value":
            return "💎  Skanuj okazje Value"
        if mode == "mixed":
            return "⚖️  Skanuj okazje mieszane"
        return "🔍  Skanuj okazje"

    def _mode_ready_text(self, mode: str) -> str:
        label = dict(_OPPORTUNITY_SCAN_MODES).get(mode, "Podstawowy")
        return f"Wybrano tryb: {label}. Uruchom skan, aby odświeżyć listę okazji."

    def _refresh_mode_controls(self):
        busy = self._is_busy()
        for btn in self._mode_btns.values():
            btn.setEnabled(not busy)

        self._horizon_row_widget.setVisible(self._selected_mode == "classic")
        horizon_enabled = self._selected_mode == "classic" and self._active_result_mode != "ai" and not busy
        disabled_tooltip = "Tryb value i mieszany nie używają podziału na horyzont techniczny w rankingu listy."
        for btn in self._horizon_btns.values():
            btn.setEnabled(horizon_enabled)
            btn.setToolTip("" if horizon_enabled else disabled_tooltip)

    def _show_cached_mode(self, mode: str):
        self._active_result_mode = mode
        if mode == "classic" and self._classic_scan_ready:
            self._render_results(self._classic_results)
            return
        if mode == "value" and self._value_scan_ready:
            self._render_value_results(self._value_results)
            return
        if mode == "mixed" and self._mixed_scan_ready:
            self._render_mixed_results(self._mixed_results)
            return
        self._list.clear()
        self._clear_selection()
        self._status.setText(self._mode_ready_text(mode))
        self._refresh_mode_controls()

    def _set_selected_mode(self, mode: str):
        if self._is_busy():
            return
        self._selected_mode = mode if mode in {"classic", "value", "mixed"} else "classic"
        for key, btn in self._mode_btns.items():
            btn.setChecked(key == self._selected_mode)
        self._info.setText(self._mode_info_text(self._selected_mode))
        self._hint.setText(self._mode_hint_text(self._selected_mode))
        self._scan_btn.setText(self._mode_scan_button_text(self._selected_mode))
        self._show_cached_mode(self._selected_mode)

    def _classic_rank_to_pct(self, rank_score: float | None) -> float:
        value = float(rank_score or 0.0)
        return max(0.0, min(100.0, round(50.0 + value * 70.0, 1)))

    def _indicator_score_pct(self, row: dict) -> float:
        live_score = float(row.get("score") or 0.0)
        return max(0.0, min(100.0, round((live_score / 9.0) * 100.0, 1)))

    def _backtest_growth_score_pct(self, row: dict) -> float:
        details = row.get("details") or {}
        if not details:
            return 0.0

        horizon_weights = {"5d": 0.15, "22d": 0.55, "66d": 0.30}
        blended_score = 0.0
        used_weight = 0.0
        sample_score = 0.0
        for suffix, weight in horizon_weights.items():
            win_rate = details.get(f"win_rate_{suffix}")
            avg_return = details.get(f"avg_return_{suffix}")
            samples = float(details.get(f"n_setups_{suffix}") or details.get("n_setups") or 0.0)
            if win_rate is None and avg_return is None:
                continue

            win_component = max(0.0, min(1.0, (float(win_rate or 0.0) - 35.0) / 35.0))
            return_component = max(0.0, min(1.0, (float(avg_return or 0.0) + 8.0) / 16.0))
            blended_score += weight * (0.72 * win_component + 0.28 * return_component)
            used_weight += weight
            sample_score += weight * min(samples / 18.0, 1.0)

        if used_weight <= 0:
            return 0.0

        sample_factor = 0.45 + 0.55 * min((sample_score / used_weight), 1.0)
        total_score = (blended_score / used_weight) * sample_factor * 100.0
        return max(0.0, min(100.0, round(total_score, 1)))

    def _build_mixed_results(self, classic_results: list[dict], value_results: list[dict]) -> list[dict]:
        value_map = {
            str(row.get("ticker") or "").upper(): row
            for row in value_results
            if str(row.get("ticker") or "").strip()
        }
        merged: list[dict] = []
        for row in classic_results:
            ticker = str(row.get("ticker") or "").upper()
            value_snapshot = value_map.get(ticker)
            value_score = float((value_snapshot or {}).get("value_score") or 0.0)
            growth_score = self._backtest_growth_score_pct(row)
            indicator_score = self._indicator_score_pct(row)
            margin = (value_snapshot or {}).get("margin_of_safety_pct")
            mixed_score = round(value_score * 0.50 + growth_score * 0.30 + indicator_score * 0.20, 1)
            merged.append({
                **row,
                "value_snapshot": value_snapshot,
                "growth_score_pct": round(growth_score, 1),
                "indicator_score_pct": round(indicator_score, 1),
                "value_score_component": round(value_score, 1),
                "mixed_margin_of_safety_pct": margin,
                "mixed_score": mixed_score,
            })

        merged.sort(
            key=lambda item: (
                float(item.get("mixed_score") or 0.0),
                float(item.get("value_score_component") or 0.0),
                float(item.get("growth_score_pct") or 0.0),
                float(item.get("indicator_score_pct") or 0.0),
                float(item.get("mixed_margin_of_safety_pct") or -999.0),
            ),
            reverse=True,
        )
        return merged

    def _result_currency(self, result: dict) -> str:
        return str(result.get("currency") or result["details"].get("currency") or "").upper()

    def _ai_result_color(self, result: dict) -> str:
        verdict = str(result.get("verdict") or "OBSERWUJ").upper()
        score = int(result.get("conviction_score") or 0)
        if verdict == "OKAZJA" and score >= 75:
            return config.COLOR_UP
        if verdict == "OKAZJA":
            return config.COLOR_ACCENT
        if verdict == "POMIN":
            return "#94a3b8"
        return "#2563eb"

    def _result_color(self, result: dict) -> str:
        verdict = str(result.get("verdict") or "").lower()
        score = float(result.get("value_score") or 0.0)
        if "trap" in verdict:
            return "#dc2626"
        if score >= 72:
            return config.COLOR_UP
        if score >= 58:
            return config.COLOR_ACCENT
        return "#64748b"

    # ── Scan ──────────────────────────────────────────────────────────────

    def _start_scan(self):
        if self._worker and self._worker.isRunning():
            return
        scan_mode = self._selected_mode
        self._active_result_mode = scan_mode
        self._scan_btn.setEnabled(False)
        self._ai_scan_btn.setEnabled(False)
        self._list.clear()
        self._clear_selection()
        self._status.setText("Skanowanie klasyka + trybu mieszanego + Value Investing…")
        self._progress.setVisible(True)
        total_steps = len(config.SCANNER_UNIVERSE) * 3
        self._progress.setRange(0, total_steps)
        self._progress.setValue(0)
        self._refresh_mode_controls()

        self._worker = ScanWorker(self._profile, self._horizon, scan_mode)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _start_ai_scan(self):
        if self._worker and self._worker.isRunning():
            return

        settings = load_ai_advice_settings()
        provider = settings.get("provider") or "openai"
        model = resolve_ai_model(settings)
        if not settings.get("token"):
            QMessageBox.warning(self, "Skan okazji AI", "Najpierw ustaw token API w Portfel → Porada AI.")
            return
        if provider == "other" and not settings.get("base_url"):
            QMessageBox.warning(self, "Skan okazji AI", "Dla dostawcy 'Inny' uzupełnij Base URL w Portfel → Porada AI.")
            return
        if not model:
            QMessageBox.warning(self, "Skan okazji AI", "Najpierw wybierz model AI w Portfel → Porada AI.")
            return

        self._active_result_mode = "ai"
        self._scan_btn.setEnabled(False)
        self._ai_scan_btn.setEnabled(False)
        self._list.clear()
        self._clear_selection()
        self._ai_results = []
        self._ai_scan_result = {}
        self._status.setText(f"AI skanuje uniwersum bez techniki… model: {model}")
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._refresh_mode_controls()

        self._worker = AIOpportunityScanWorker(config.SCANNER_UNIVERSE, settings)
        self._worker.done.connect(self._on_ai_finished)
        self._worker.error.connect(self._on_ai_error)
        self._worker.start()

    def _on_progress(self, i: int, total: int, ticker: str):
        if total > 0 and total != self._progress.maximum():
            self._progress.setRange(0, total)
        self._progress.setValue(i)
        self._status.setText(f"Sprawdzam: {ticker}  ({i}/{total})")

    def _on_finished(self, payload: dict):
        self._progress.setVisible(False)
        self._scan_btn.setEnabled(True)
        self._ai_scan_btn.setEnabled(True)
        mode = str(payload.get("mode") or self._selected_mode)

        self._classic_results = list(payload.get("classic_results") or payload.get("results") or [])
        self._value_results = list(payload.get("value_results") or [])
        self._mixed_source_results = list(payload.get("mixed_source_results") or [])
        self._classic_scan_completed_at = str(self._classic_results[0].get("scan_completed_at") or "") if self._classic_results else ""
        self._mixed_scan_completed_at = str(self._mixed_source_results[0].get("scan_completed_at") or "") if self._mixed_source_results else ""
        self._mixed_results = self._build_mixed_results(self._mixed_source_results, self._value_results)
        self._classic_scan_ready = True
        self._value_scan_ready = True
        self._mixed_scan_ready = True

        if mode == "value":
            self._active_result_mode = "value"
            self._render_value_results(self._value_results)
        elif mode == "mixed":
            self._active_result_mode = "mixed"
            self._render_mixed_results(self._mixed_results)
        else:
            self._active_result_mode = "classic"
            self._render_results(self._classic_results)
        self._refresh_mode_controls()

    def _on_ai_finished(self, result: dict):
        self._progress.setVisible(False)
        self._scan_btn.setEnabled(True)
        self._ai_scan_btn.setEnabled(True)
        self._ai_scan_result = result
        self._ai_results = ((result.get("structured") or {}).get("opportunities") or [])
        self._active_result_mode = "ai"
        self._render_ai_results(self._ai_results)
        self._refresh_mode_controls()

    def _on_ai_error(self, message: str):
        self._progress.setVisible(False)
        self._scan_btn.setEnabled(True)
        self._ai_scan_btn.setEnabled(True)
        self._show_cached_mode(self._selected_mode)
        self._status.setText("Nie udało się wykonać skanu okazji AI.")
        QMessageBox.warning(self, "Skan okazji AI", str(message or "Nieznany błąd AI.").strip())

    # ── Rendering ─────────────────────────────────────────────────────────

    def _render_results(self, results: list[dict]):
        self._list.clear()
        self._clear_selection()
        h = self._horizon
        if self._currency != "ALL":
            results = [r for r in results if self._result_currency(r) == self._currency]

        if not results:
            currency_label = next(label for key, label in _CURRENCIES if key == self._currency)
            snapshot_text = _fmt_snapshot_time(self._classic_scan_completed_at)
            suffix = f" · ostatni snapshot listy: {snapshot_text}" if self._classic_scan_completed_at else ""
            self._status.setText(f"0 okazji · waluta: {currency_label}{suffix}")
            return

        # Sort by one canonical opportunity rank, with a strong bias toward the selected historical horizon.
        win_key = next((wk for k, _, wk in _HORIZONS if k == h), None)
        rank_horizon = h if h in {"5d", "22d", "66d"} else "all"

        def _sort_key(x):
            d = x["details"]
            wr = d.get(win_key) if win_key else None
            avg = d.get(win_key.replace("win_rate", "avg_return")) if win_key else None
            return (
                get_opportunity_rank_score(x, rank_horizon, self._profile),
                wr if wr is not None else -1,
                avg if avg is not None else -99,
                d.get("macro_score", x.get("macro_score", 50)),
                d.get("company_score", x.get("company_score", 50)),
            )

        sorted_r = sorted(results, key=_sort_key, reverse=True)
        first_snapshot = min((str(item.get("snapshot_time") or "") for item in sorted_r if item.get("snapshot_time")), default="")
        last_snapshot = max((str(item.get("snapshot_time") or "") for item in sorted_r if item.get("snapshot_time")), default="")
        scan_window = ""
        if first_snapshot and last_snapshot:
            first_label = _fmt_snapshot_time(first_snapshot)
            last_label = _fmt_snapshot_time(last_snapshot)
            scan_window = first_label if first_label == last_label else f"{first_label} → {last_label}"

        # Status text
        count = len(sorted_r)
        if win_key and sorted_r:
            best = sorted_r[0]
            bw = best["details"].get(win_key)
            horizon_label = next(lbl for k, lbl, wk in _HORIZONS if k == h)
            if bw is not None:
                status_text = (
                    f"{count} okazji · {horizon_label}: "
                    f"najlepsza → {best['ticker']} ({bw:.0f}% wzrost w hist.)"
                )
                if scan_window:
                    status_text += f" · snapshot cen: {scan_window}"
                self._status.setText(status_text)
            else:
                status_text = f"{count} okazji · brak danych backtestowych"
                if scan_window:
                    status_text += f" · snapshot cen: {scan_window}"
                self._status.setText(status_text)
        else:
            status_text = f"{count} okazji · technika + historia + spółka"
            if scan_window:
                status_text += f" · snapshot cen: {scan_window}"
            self._status.setText(status_text)

        for idx, r in enumerate(sorted_r):
            d = r["details"]
            ticker = r["ticker"]
            currency = self._result_currency(r) or "—"
            breakdown = get_opportunity_rank_breakdown(r, rank_horizon, self._profile)
            confidence = breakdown.get("confidence") or {}
            confidence_label = str(confidence.get("label") or "Niska")
            setup_quality = breakdown.get("setup_quality_score", 50.0)
            company_quality = breakdown.get("company_quality_score", 50.0)
            rank_note = describe_opportunity_rank_advantage(r, sorted_r[idx - 1] if idx > 0 else None, rank_horizon, self._profile)

            if not win_key:
                # "Wszystkie" — use the canonical rank display, not the raw live criteria count.
                score = r["score"]
                display_state = get_opportunity_display_state(r, rank_horizon, self._profile)
                stars = _render_score_stars(display_state.get("stars_value", 0), max_score=5, star_slots=5)
                price = d.get("price", "?")
                rsi   = d.get("rsi_value", "?")
                stoch = d.get("stoch_k")
                roc   = d.get("roc20")
                dd    = d.get("drawdown_pct", "?")
                macro_score = d.get("macro_score")
                company_score = d.get("company_score")
                line2_parts = [f"Wal:{currency}", f"P:{price}", f"RSI:{rsi}"]
                if stoch is not None:
                    line2_parts.append(f"Stoch:{stoch:.0f}")
                if roc is not None:
                    line2_parts.append(f"ROC:{roc:+.1f}%")
                if macro_score is not None:
                    line2_parts.append(f"Makro:{macro_score}")
                if company_score is not None:
                    line2_parts.append(f"Spółka:{company_score}")
                line2_parts.append(f"Pew:{confidence_label}")
                line2_parts.append(f"Setup:{setup_quality:.0f}")
                line2_parts.append(f"Firma:{company_quality:.0f}")
                line2_parts.append(f"DD:-{dd}%")
                label = f"{stars}  {ticker}\n" + "  ".join(line2_parts)
                color = display_state.get("color", config.COLOR_TEXT)
            else:
                # Horizon view — backtest-first display
                win_rate = d.get(win_key)
                avg_key2 = win_key.replace("win_rate", "avg_return")
                avg_ret  = d.get(avg_key2)
                n_setups = d.get("n_setups", 0)
                price    = d.get("price", "?")
                rsi      = d.get("rsi_value", "?")
                macro_score = d.get("macro_score")
                company_score = d.get("company_score")

                if win_rate is not None:
                    sign    = "+" if avg_ret and avg_ret >= 0 else ""
                    avg_str = f"  avg {sign}{avg_ret:.1f}%" if avg_ret is not None else ""
                    stoch   = d.get("stoch_k")
                    roc     = d.get("roc20")
                    line2_parts = [f"Wal:{currency}", f"P:{price}", f"RSI:{rsi}"]
                    if stoch is not None:
                        line2_parts.append(f"Stoch:{stoch:.0f}")
                    if roc is not None:
                        line2_parts.append(f"ROC:{roc:+.1f}%")
                    if macro_score is not None:
                        line2_parts.append(f"Makro:{macro_score}")
                    if company_score is not None:
                        line2_parts.append(f"Spółka:{company_score}")
                    line2_parts.append(f"Pew:{confidence_label}")
                    line2_parts.append(f"Setup:{setup_quality:.0f}")
                    line2_parts.append(f"Firma:{company_quality:.0f}")
                    line2_parts.append(f"({n_setups}×)")
                    label = (
                        f"Win: {win_rate:.0f}%{avg_str}  │  {ticker}\n"
                        + "  ".join(line2_parts)
                    )
                    color = (config.COLOR_UP if win_rate >= 65
                             else config.COLOR_ACCENT if win_rate >= 50
                             else "#adb5bd")
                else:
                    label = f"Win: ?%  │  {ticker}\nWal:{currency}  P:{price}  RSI:{rsi}  (za mało danych)"
                    color = "#adb5bd"

            item = TickerListItem(ticker, "")
            item.setText(label)
            price_snapshot_time = _fmt_snapshot_time(r.get("snapshot_time"))
            bars_snapshot_time = _fmt_snapshot_time(r.get("bars_snapshot_time"))
            snapshot_source = str(r.get("snapshot_source_label") or "live cena (cache do 15 min)")
            item.setToolTip(
                f"{ticker}\n"
                f"Snapshot ceny: {price_snapshot_time} · {snapshot_source}\n"
                f"Świeca 4H dla wskaźników: {bars_snapshot_time}\n"
                f"Pewność: {confidence_label} ({confidence.get('score', 0):.0f}/100)\n"
                f"{rank_note}"
            )
            item.setSizeHint(QSize(0, 54))
            item.setForeground(QColor(color))
            self._list.addItem(item)

        self._refresh_mode_controls()

    def _render_ai_results(self, results: list[dict]):
        self._list.clear()
        self._clear_selection()
        filtered = list(results)
        if self._currency != "ALL":
            filtered = [row for row in filtered if str(row.get("currency") or "").upper() == self._currency]

        structured = self._ai_scan_result.get("structured") or {}
        internet_used = bool(self._ai_scan_result.get("used_web_search") or structured.get("internet_checked"))
        internet_label = "web search potwierdzony" if internet_used else "web search niepotwierdzony"
        model = str(self._ai_scan_result.get("model") or "—")

        if not filtered:
            suffix = f" · {internet_label} · model: {model}" if self._ai_scan_result else ""
            self._status.setText(f"0 okazji AI w bieżącym filtrze walutowym{suffix}")
            self._refresh_mode_controls()
            return

        self._status.setText(f"{len(filtered)} okazji AI · {internet_label} · model: {model}")

        for row in filtered:
            ticker = str(row.get("ticker") or "—").upper()
            currency = str(row.get("currency") or "USD").upper() or "USD"
            price = row.get("price")
            price_text = f"{float(price):.2f}" if price is not None else "?"
            verdict = str(row.get("verdict") or "OBSERWUJ").upper()
            confidence = str(row.get("confidence") or "SREDNIA").upper()
            score = int(row.get("conviction_score") or 0)
            label = (
                f"AI {score:02d}/100  │  {ticker}\n"
                f"Wal:{currency}  P:{price_text}  Werdykt:{verdict}  Pew:{confidence}  {_short_text(row.get('summary'), 88)}"
            )
            tooltip_lines = [
                ticker,
                f"Werdykt AI: {verdict} · Pewność: {confidence} · Conviction: {score}/100",
                f"Dlaczego teraz: {str(row.get('why_now') or row.get('summary') or '—').strip()}",
            ]
            catalysts = row.get("catalysts") or []
            if catalysts:
                tooltip_lines.append("Katalizatory: " + "; ".join(str(item) for item in catalysts[:3]))
            smart_money = row.get("smart_money") or []
            if smart_money:
                tooltip_lines.append(
                    "Duzi gracze: " + "; ".join(
                        f"{str(item.get('signal') or 'BRAK').upper()} · {str(item.get('title') or '').strip()}"
                        for item in smart_money[:2]
                    )
                )
            risks = row.get("risks") or []
            if risks:
                tooltip_lines.append("Ryzyka: " + "; ".join(str(item) for item in risks[:2]))

            item = TickerListItem(ticker, "")
            item.setText(label)
            item.setToolTip("\n".join(line for line in tooltip_lines if line))
            item.setSizeHint(QSize(0, 66))
            item.setForeground(QColor(self._ai_result_color(row)))
            self._list.addItem(item)

        self._refresh_mode_controls()

    def _render_value_results(self, results: list[dict]):
        self._list.clear()
        self._clear_selection()
        filtered = sorted(
            list(results),
            key=lambda row: (
                float(row.get("value_score") or 0.0),
                float(row.get("margin_of_safety_pct") or -999.0),
                float(row.get("fair_value_gap_pct") or row.get("upside_pct") or -999.0),
            ),
            reverse=True,
        )
        if self._currency != "ALL":
            filtered = [row for row in filtered if str(row.get("currency") or "").upper() == self._currency]

        if not filtered:
            currency_label = next(label for key, label in _CURRENCIES if key == self._currency)
            self._status.setText(f"0 okazji value · waluta: {currency_label}")
            self._refresh_mode_controls()
            return

        first_snapshot = min((str(item.get("snapshot_time") or "") for item in filtered if item.get("snapshot_time")), default="")
        last_snapshot = max((str(item.get("snapshot_time") or "") for item in filtered if item.get("snapshot_time")), default="")
        scan_window = ""
        if first_snapshot and last_snapshot:
            first_label = _fmt_snapshot_time(first_snapshot)
            last_label = _fmt_snapshot_time(last_snapshot)
            scan_window = first_label if first_label == last_label else f"{first_label} → {last_label}"
        status = f"{len(filtered)} okazji value"
        if scan_window:
            status += f" · snapshot: {scan_window}"
        self._status.setText(status)

        for row in filtered:
            ticker = str(row.get("ticker") or "—").upper()
            currency = str(row.get("currency") or "USD").upper() or "USD"
            price = row.get("price")
            price_text = f"{float(price):.2f}" if price is not None else "?"
            score = float(row.get("value_score") or 0.0)
            margin = row.get("margin_of_safety_pct")
            margin_text = f"{margin:+.1f}%" if margin is not None else "—"
            pe = row.get("forward_pe") if row.get("forward_pe") is not None else row.get("pe_ratio")
            pe_text = f"{float(pe):.1f}" if pe is not None else "—"
            pb = row.get("price_to_book")
            pb_text = f"{float(pb):.2f}" if pb is not None else "—"
            fcf = row.get("fcf_yield_pct")
            fcf_text = f"{float(fcf):+.1f}%" if fcf is not None else "—"
            verdict = str(row.get("verdict") or "Neutralna wycena")

            label = (
                f"Value {score:02.0f}/100  │  {ticker}\n"
                f"Wal:{currency}  P:{price_text}  MOS:{margin_text}  P/E:{pe_text}  P/B:{pb_text}  FCF:{fcf_text}  {_short_text(verdict, 46)}"
            )
            tooltip_lines = [
                ticker,
                f"Werdykt: {verdict}",
                f"Cena: {price_text} {currency} · Fair value: {row.get('fair_value_price', '—')} · Margines bezpieczeństwa: {margin_text}",
                f"Value score: {score:.0f}/100 · Ryzyko value trap: {float(row.get('value_trap_risk') or 0.0):.0f}/100",
                str(row.get("summary") or "").strip(),
            ]
            strengths = row.get("strengths") or []
            if strengths:
                tooltip_lines.append("Mocne strony: " + "; ".join(str(item) for item in strengths[:3]))
            risks = row.get("risks") or []
            if risks:
                tooltip_lines.append("Ryzyka: " + "; ".join(str(item) for item in risks[:3]))

            item = TickerListItem(ticker, "")
            item.setText(label)
            item.setToolTip("\n".join(line for line in tooltip_lines if line))
            item.setSizeHint(QSize(0, 66))
            item.setForeground(QColor(self._result_color(row)))
            self._list.addItem(item)

        self._refresh_mode_controls()

    def _mixed_result_color(self, result: dict) -> str:
        value_snapshot = result.get("value_snapshot") or {}
        verdict = str(value_snapshot.get("verdict") or "").lower()
        mixed_score = float(result.get("mixed_score") or 0.0)
        if "trap" in verdict and mixed_score < 60:
            return "#dc2626"
        if mixed_score >= 74:
            return config.COLOR_UP
        if mixed_score >= 60:
            return config.COLOR_ACCENT
        return "#64748b"

    def _render_mixed_results(self, results: list[dict]):
        self._list.clear()
        self._clear_selection()
        filtered = list(results)
        if self._currency != "ALL":
            filtered = [row for row in filtered if self._result_currency(row) == self._currency]

        if not filtered:
            currency_label = next(label for key, label in _CURRENCIES if key == self._currency)
            self._status.setText(f"0 okazji mieszanych · waluta: {currency_label}")
            self._refresh_mode_controls()
            return

        scan_window = _fmt_snapshot_time(self._mixed_scan_completed_at) if self._mixed_scan_completed_at else ""
        status = f"{len(filtered)} okazji mieszanych · ranking: 50% value / 30% backtest / 20% wskaźniki"
        if scan_window:
            status += f" · scan: {scan_window}"
        self._status.setText(status)

        for row in filtered:
            ticker = str(row.get("ticker") or "—").upper()
            details = row.get("details") or {}
            currency = self._result_currency(row) or "—"
            price = details.get("price", "?")
            confidence = (row.get("signal_confidence") or {}).get("label") or "Średnia"
            value_snapshot = row.get("value_snapshot") or {}
            value_verdict = str(value_snapshot.get("verdict") or "Brak potwierdzenia value")
            mixed_score = float(row.get("mixed_score") or 0.0)
            growth_score = float(row.get("growth_score_pct") or 0.0)
            indicator_score = float(row.get("indicator_score_pct") or 0.0)
            value_score = float(row.get("value_score_component") or 0.0)
            margin = row.get("mixed_margin_of_safety_pct")
            margin_text = f"{margin:+.1f}%" if margin is not None else "—"
            backtest_samples = int((details.get("n_setups_22d") or details.get("n_setups") or 0))

            label = (
                f"Mix {mixed_score:02.0f}/100  │  {ticker}\n"
                f"Wal:{currency}  P:{price}  Value:{value_score:.0f}  Backtest:{growth_score:.0f}  Wsk:{indicator_score:.0f}  MOS:{margin_text}  Pew:{confidence}"
            )

            tooltip_lines = [
                ticker,
                f"Tryb mieszany: ranking = 50% Value Investing ({value_score:.0f}/100) + 30% Szansa na wzrost z backtestu setupu ({growth_score:.0f}/100) + 20% wskaźniki techniczne ({indicator_score:.0f}/100).",
                f"Backtest setupu: próbka {backtest_samples} podobnych historycznych wejść dla horyzontu 1M.",
                f"Value verdict: {value_verdict} · MOS: {margin_text} · Ryzyko trap: {float(value_snapshot.get('value_trap_risk') or 0.0):.0f}/100",
                str(value_snapshot.get("summary") or "Brak dodatkowego komentarza value.").strip(),
            ]
            strengths = value_snapshot.get("strengths") or []
            if strengths:
                tooltip_lines.append("Mocne strony: " + "; ".join(str(item) for item in strengths[:3]))
            risks = value_snapshot.get("risks") or []
            if risks:
                tooltip_lines.append("Ryzyka: " + "; ".join(str(item) for item in risks[:3]))

            item = TickerListItem(ticker, "")
            item.setText(label)
            item.setToolTip("\n".join(line for line in tooltip_lines if line))
            item.setSizeHint(QSize(0, 66))
            item.setForeground(QColor(self._mixed_result_color(row)))
            self._list.addItem(item)

        self._refresh_mode_controls()

    # ── Interactions ──────────────────────────────────────────────────────

    def _on_item_clicked(self, item: TickerListItem):
        self._selected_ticker = item.ticker
        if self._active_result_mode == "ai":
            result = next((r for r in self._ai_results if r["ticker"] == item.ticker), None)
            self._selected_price = result.get("price") if result else None
        elif self._active_result_mode == "value":
            result = next((r for r in self._value_results if r["ticker"] == item.ticker), None)
            self._selected_price = result.get("price") if result else None
        elif self._active_result_mode == "mixed":
            result = next((r for r in self._mixed_results if r["ticker"] == item.ticker), None)
            self._selected_price = (result.get("details") or {}).get("price") if result else None
        else:
            result = next((r for r in self._classic_results if r["ticker"] == item.ticker), None)
            self._selected_price = result["details"].get("price") if result else None
        self._add_portfolio_btn.setEnabled(True)
        self.ticker_selected.emit(item.ticker, item.ticker)

    def _on_item_double_clicked(self, item: TickerListItem):
        if self._active_result_mode == "ai":
            result = next((r for r in self._ai_results if r["ticker"] == item.ticker), None)
            if result is None:
                return
            dlg = AIOpportunityDetailDialog(result, self._ai_scan_result, parent=self)
            dlg.exec()
            return

        if self._active_result_mode == "value":
            result = next((r for r in self._value_results if r["ticker"] == item.ticker), None)
            if result is None:
                return
            from .opportunity_detail import OpportunityDetailDialog

            dlg = OpportunityDetailDialog(
                item.ticker,
                parent=self,
                profile=self._profile,
                horizon=self._horizon,
            )
            dlg.exec()
            return

        from .opportunity_detail import OpportunityDetailDialog
        source_results = self._mixed_results if self._active_result_mode == "mixed" else self._classic_results
        result = next((r for r in source_results if r["ticker"] == item.ticker), None)
        dlg = OpportunityDetailDialog(
            item.ticker,
            parent=self,
            profile=self._profile,
            horizon=self._horizon,
            price_snapshot=(result or {}).get("price_snapshot"),
        )
        dlg.exec()

    def _on_add_to_portfolio(self):
        if not self._selected_ticker:
            return
        # Find the portfolio tab via the parent Sidebar
        sidebar = self.parent()
        # Walk up to find Sidebar which has _portfolio_tab
        widget = self
        while widget is not None:
            if hasattr(widget, '_portfolio_tab'):
                widget._portfolio_tab.add_from_opportunity(
                    self._selected_ticker, self._selected_price
                )
                return
            widget = widget.parent()
        # Fallback: open dialog standalone and save directly
        from .portfolio_tab import AddHoldingDialog
        from data.portfolio import add_holding
        dlg = AddHoldingDialog(
            parent=self,
            prefill_ticker=self._selected_ticker,
            prefill_price=self._selected_price,
        )
        if dlg.exec() == dlg.DialogCode.Accepted:
            v = dlg.get_values()
            add_holding(v["ticker"], v["shares"], v["avg_price"], v["notes"], v["purchase_date"])


class ValueInvestingTab(QWidget):
    ticker_selected = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        info = QLabel(
            "Value Investing: skaner szuka biznesów, których uproszczona wartość wewnętrzna wygląda wyżej od ceny rynkowej, "
            "ale jednocześnie pilnuje jakości biznesu i ryzyka value trap."
        )
        info.setWordWrap(True)
        info.setObjectName("infoLabel")
        layout.addWidget(info)

        currency_row = QHBoxLayout()
        currency_row.setSpacing(3)
        currency_label = QLabel("Waluta:")
        currency_label.setObjectName("infoLabel")
        currency_row.addWidget(currency_label)
        self._currency_btns: dict[str, QPushButton] = {}
        for key, label in _CURRENCIES:
            btn = QPushButton(label)
            btn.setObjectName("catBtn")
            btn.setCheckable(True)
            btn.setMinimumWidth(0)
            btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda checked, c=key: self._set_currency(c))
            self._currency_btns[key] = btn
            currency_row.addWidget(btn)
        layout.addLayout(currency_row)

        self._scan_btn = QPushButton("💎  Skanuj Value Investing")
        self._scan_btn.setObjectName("scanBtn")
        self._scan_btn.clicked.connect(self._start_scan)
        layout.addWidget(self._scan_btn)

        self._progress = QProgressBar()
        self._progress.setObjectName("scanProgress")
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setObjectName("statusLabel")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._list = QListWidget()
        self._list.setObjectName("tickerList")
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setWordWrap(True)
        self._list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._list.setUniformItemSizes(False)
        layout.addWidget(self._list)
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)

        hint = QLabel("Kliknij raz → wykres  ·  Kliknij dwa razy → pełny raport Value Investing")
        hint.setObjectName("infoLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._add_portfolio_btn = QPushButton("💼  Dodaj do portfela")
        self._add_portfolio_btn.setObjectName("addBtn")
        self._add_portfolio_btn.setEnabled(False)
        self._add_portfolio_btn.setMinimumWidth(0)
        self._add_portfolio_btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._add_portfolio_btn.clicked.connect(self._on_add_to_portfolio)
        layout.addWidget(self._add_portfolio_btn)

        self._worker: QThread | None = None
        self._results: list[dict] = []
        self._currency = "ALL"
        self._selected_ticker = ""
        self._selected_price: float | None = None
        self._currency_btns["ALL"].setChecked(True)

    def _set_currency(self, code: str):
        self._currency = code
        for key, btn in self._currency_btns.items():
            btn.setChecked(key == code)
        if self._results:
            self._render_results(self._results)

    def _clear_selection(self):
        self._selected_ticker = ""
        self._selected_price = None
        self._add_portfolio_btn.setEnabled(False)

    def _result_color(self, result: dict) -> str:
        verdict = str(result.get("verdict") or "").lower()
        score = float(result.get("value_score") or 0.0)
        if "trap" in verdict:
            return "#dc2626"
        if score >= 72:
            return config.COLOR_UP
        if score >= 58:
            return config.COLOR_ACCENT
        return "#64748b"

    def _start_scan(self):
        if self._worker and self._worker.isRunning():
            return
        self._scan_btn.setEnabled(False)
        self._list.clear()
        self._clear_selection()
        self._results = []
        self._status.setText("Skanuję spółki pod kątem niedowartościowania…")
        self._progress.setVisible(True)
        self._progress.setRange(0, len(config.SCANNER_UNIVERSE))
        self._progress.setValue(0)

        self._worker = ValueInvestingScanWorker()
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_progress(self, i: int, total: int, ticker: str):
        self._progress.setValue(i)
        self._status.setText(f"Value scan: {ticker} ({i}/{total})")

    def _on_finished(self, results: list[dict]):
        self._progress.setVisible(False)
        self._scan_btn.setEnabled(True)
        self._results = results
        self._render_results(results)

    def _render_results(self, results: list[dict]):
        self._list.clear()
        self._clear_selection()
        filtered = list(results)
        if self._currency != "ALL":
            filtered = [row for row in filtered if str(row.get("currency") or "").upper() == self._currency]

        if not filtered:
            currency_label = next(label for key, label in _CURRENCIES if key == self._currency)
            self._status.setText(f"0 kandydatów value · waluta: {currency_label}")
            return

        first_snapshot = min((str(item.get("snapshot_time") or "") for item in filtered if item.get("snapshot_time")), default="")
        last_snapshot = max((str(item.get("snapshot_time") or "") for item in filtered if item.get("snapshot_time")), default="")
        scan_window = ""
        if first_snapshot and last_snapshot:
            first_label = _fmt_snapshot_time(first_snapshot)
            last_label = _fmt_snapshot_time(last_snapshot)
            scan_window = first_label if first_label == last_label else f"{first_label} → {last_label}"
        status = f"{len(filtered)} kandydatów value"
        if scan_window:
            status += f" · snapshot: {scan_window}"
        self._status.setText(status)

        for row in filtered:
            ticker = str(row.get("ticker") or "—").upper()
            currency = str(row.get("currency") or "USD").upper() or "USD"
            price = row.get("price")
            price_text = f"{float(price):.2f}" if price is not None else "?"
            score = float(row.get("value_score") or 0.0)
            margin = row.get("margin_of_safety_pct")
            margin_text = f"{margin:+.1f}%" if margin is not None else "—"
            pe = row.get("forward_pe") if row.get("forward_pe") is not None else row.get("pe_ratio")
            pe_text = f"{float(pe):.1f}" if pe is not None else "—"
            pb = row.get("price_to_book")
            pb_text = f"{float(pb):.2f}" if pb is not None else "—"
            fcf = row.get("fcf_yield_pct")
            fcf_text = f"{float(fcf):+.1f}%" if fcf is not None else "—"
            verdict = str(row.get("verdict") or "Neutralna wycena")

            label = (
                f"Value {score:02.0f}/100  │  {ticker}\n"
                f"Wal:{currency}  P:{price_text}  MOS:{margin_text}  P/E:{pe_text}  P/B:{pb_text}  FCF:{fcf_text}  {_short_text(verdict, 46)}"
            )
            tooltip_lines = [
                ticker,
                f"Werdykt: {verdict}",
                f"Cena: {price_text} {currency} · Fair value: {row.get('fair_value_price', '—')} · Margines bezpieczeństwa: {margin_text}",
                f"Value score: {score:.0f}/100 · Ryzyko value trap: {float(row.get('value_trap_risk') or 0.0):.0f}/100",
                str(row.get("summary") or "").strip(),
            ]
            strengths = row.get("strengths") or []
            if strengths:
                tooltip_lines.append("Mocne strony: " + "; ".join(str(item) for item in strengths[:3]))
            risks = row.get("risks") or []
            if risks:
                tooltip_lines.append("Ryzyka: " + "; ".join(str(item) for item in risks[:3]))

            item = TickerListItem(ticker, "")
            item.setText(label)
            item.setToolTip("\n".join(line for line in tooltip_lines if line))
            item.setSizeHint(QSize(0, 66))
            item.setForeground(QColor(self._result_color(row)))
            self._list.addItem(item)

    def _on_item_clicked(self, item: TickerListItem):
        self._selected_ticker = item.ticker
        result = next((r for r in self._results if r["ticker"] == item.ticker), None)
        self._selected_price = result.get("price") if result else None
        self._add_portfolio_btn.setEnabled(True)
        self.ticker_selected.emit(item.ticker, item.ticker)

    def _on_item_double_clicked(self, item: TickerListItem):
        result = next((r for r in self._results if r["ticker"] == item.ticker), None)
        if result is None:
            return
        from .opportunity_detail import OpportunityDetailDialog

        dlg = OpportunityDetailDialog(item.ticker, parent=self, profile="balanced", horizon="all")
        dlg.exec()

    def _on_add_to_portfolio(self):
        if not self._selected_ticker:
            return
        widget = self
        while widget is not None:
            if hasattr(widget, '_portfolio_tab'):
                widget._portfolio_tab.add_from_opportunity(self._selected_ticker, self._selected_price)
                return
            widget = widget.parent()
        from .portfolio_tab import AddHoldingDialog
        from data.portfolio import add_holding

        dlg = AddHoldingDialog(
            parent=self,
            prefill_ticker=self._selected_ticker,
            prefill_price=self._selected_price,
        )
        if dlg.exec() == dlg.DialogCode.Accepted:
            values = dlg.get_values()
            add_holding(values["ticker"], values["shares"], values["avg_price"], values["notes"], values["purchase_date"])


# ──────────────────────────────────────────────────────────────────────────────
class Sidebar(QWidget):
    ticker_selected = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setMinimumWidth(280)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("sidebarTabs")
        self._tabs.setTabBar(_StretchTabBar())
        self._tabs.tabBar().setUsesScrollButtons(True)

        self._search_tab = SearchTab()
        self._watchlist_tab = WatchlistTab()
        self._opp_tab = OpportunityTab()
        self._portfolio_tab = PortfolioTab()

        self._tabs.addTab(self._search_tab, "Szukaj")
        self._tabs.addTab(self._watchlist_tab, "Obserwowane")
        self._tabs.addTab(self._opp_tab, "Okazje 🔥")
        self._tabs.addTab(self._portfolio_tab, "Portfel 💼")

        layout.addWidget(self._tabs)

        # Forward signals
        self._search_tab.ticker_selected.connect(self.ticker_selected)
        self._watchlist_tab.ticker_selected.connect(self.ticker_selected)
        self._opp_tab.ticker_selected.connect(self.ticker_selected)
        self._portfolio_tab.ticker_selected.connect(self.ticker_selected)
        self._search_tab.add_to_watchlist.connect(self._watchlist_tab.add_ticker)
