"""ui/portfolio_tab.py — Portfolio tab: holdings list, add dialog, detail analysis."""
import html
import re
import sys
import os
import tempfile
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import plotly.graph_objects as go
from plotly.subplots import make_subplots
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QDialog, QFormLayout, QLineEdit, QDoubleSpinBox,
    QDialogButtonBox, QMessageBox, QProgressBar, QSizePolicy, QTextBrowser,
    QComboBox,
    QAbstractItemView, QDateEdit, QStyledItemDelegate, QStyle, QTabWidget, QApplication,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl, QDate
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QBrush
import config
from data.portfolio import (
    load_portfolio,
    save_portfolio,
    add_holding,
    close_holding,
    load_closed_positions,
    update_closed_position,
    _resolve_close_metrics,
)
from analysis.ai_portfolio_advice import (
    AI_PROVIDERS,
    analyze_portfolio_with_ai,
    get_provider_models,
    load_ai_advice_settings,
    normalize_ai_settings,
    resolve_ai_model,
    save_ai_advice_settings,
)
from analysis.opportunity import OPPORTUNITY_PROFILES


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_snapshot_time(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return text[:16].replace("T", " ")


# ─────────────────────────────────────────────────────────────────────────────
# Workers
# ─────────────────────────────────────────────────────────────────────────────

class PriceRefreshWorker(QThread):
    """Fetches current prices for all holdings in background."""
    price_updated = pyqtSignal(str, float, str)   # ticker, current_price, currency
    done = pyqtSignal()

    def __init__(self, holdings: list[dict]):
        super().__init__()
        self._holdings = holdings

    def run(self):
        from data.fetcher import get_latest_price, get_ohlcv, get_info
        for h in self._holdings:
            ticker = h["ticker"]
            price = get_latest_price(ticker, force_refresh=True)
            if price is None or price <= 0:
                df = get_ohlcv(ticker, period="5d")
                if df is not None and len(df) > 0:
                    price = float(df["Close"].iloc[-1])

            if price is not None and price > 0:
                currency = str(h.get("currency") or "USD").upper() or "USD"
                try:
                    info = get_info(ticker)
                    currency = str(info.get("currency") or currency).upper() or currency
                except Exception:
                    pass
                self.price_updated.emit(ticker, float(price), currency)
        self.done.emit()


class HoldingAnalysisWorker(QThread):
    done = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, holding: dict):
        super().__init__()
        self._holding = holding

    def run(self):
        try:
            from analysis.portfolio_analysis import analyze_holding
            from data.fetcher import get_info, get_fundamental_history, get_fundamental_extended_data
            result = analyze_holding(self._holding)
            if result:
                try:
                    ticker = self._holding["ticker"]
                    info_data = get_info(ticker)
                    history_data = get_fundamental_history(ticker)
                    extended_data = get_fundamental_extended_data(ticker)
                    result["fundamental_data"] = {
                        "info": info_data,
                        "history": history_data,
                        "estimates": extended_data
                    }
                except Exception as fe:
                    print(f"[HoldingAnalysisWorker] Error fetching fundamental data: {fe}")
                self.done.emit(result)
            else:
                self.error.emit(f"Brak danych dla {self._holding['ticker']}")
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())


class HoldingRecommendationWorker(QThread):
    recommendation_ready = pyqtSignal(int, str, str)
    done = pyqtSignal(int)

    def __init__(self, holdings: list[dict], run_id: int):
        super().__init__()
        self._holdings = [dict(h) for h in holdings]
        self._run_id = run_id

    def run(self):
        from analysis.portfolio_analysis import get_holding_recommendation

        for holding in self._holdings:
            result = get_holding_recommendation(holding)
            if result:
                recommendation, _ = result
                self.recommendation_ready.emit(self._run_id, str(holding.get("holding_id") or ""), recommendation)
        self.done.emit(self._run_id)


class PortfolioHistoryWorker(QThread):
    done = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, holdings: list[dict]):
        super().__init__()
        self._holdings = [dict(h) for h in holdings]

    def run(self):
        try:
            from analysis.portfolio_history import build_portfolio_history

            result = {
                "history": build_portfolio_history(self._holdings),
                "holdings": self._holdings,
            }
            self.done.emit(result)
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc() if str(e).strip() else repr(e))


class AIPortfolioAdviceWorker(QThread):
    done = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, holdings: list[dict], settings: dict):
        super().__init__()
        self._holdings = [dict(h) for h in holdings]
        self._settings = dict(settings)

    def run(self):
        try:
            self.done.emit(analyze_portfolio_with_ai(self._holdings, self._settings))
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc() if str(e).strip() else repr(e))


class PortfolioScenarioWorker(QThread):
    done = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, holdings: list[dict], history_snapshot: dict | None = None):
        super().__init__()
        self._holdings = [dict(h) for h in holdings]
        self._history_snapshot = history_snapshot

    def run(self):
        try:
            from analysis.portfolio_simulation import simulate_portfolio_scenarios

            if not self._holdings:
                self.done.emit({})
                return

            self.done.emit(simulate_portfolio_scenarios(self._holdings, history_snapshot=self._history_snapshot))
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc() if str(e).strip() else repr(e))


class PortfolioSuggestionsWorker(QThread):
    done = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, holdings: list[dict], opportunity_results: list[dict] | None = None, opportunity_profile: str = "balanced"):
        super().__init__()
        self._holdings = [dict(h) for h in holdings]
        self._opportunity_results = [dict(item) for item in (opportunity_results or [])] if opportunity_results is not None else None
        self._opportunity_profile = opportunity_profile

    def run(self):
        try:
            from analysis.portfolio_suggestions import build_portfolio_suggestions

            self.done.emit(build_portfolio_suggestions(self._holdings, self._opportunity_results, self._opportunity_profile))
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc() if str(e).strip() else repr(e))


class PortfolioOutlookWorker(QThread):
    done = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, holdings: list[dict]):
        super().__init__()
        self._holdings = [dict(h) for h in holdings]

    def run(self):
        try:
            from analysis.portfolio_analysis import analyze_holding

            analyses = []
            for holding in self._holdings:
                result = analyze_holding(holding)
                if result:
                    analyses.append(result)

            self.done.emit(_build_portfolio_outlook_summary(analyses))
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc() if str(e).strip() else repr(e))


class PortfolioSignalQualityWorker(QThread):
    done = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, holdings: list[dict]):
        super().__init__()
        self._holdings = [dict(h) for h in holdings]

    def run(self):
        try:
            from analysis.portfolio_signal_quality import build_portfolio_signal_quality

            self.done.emit(build_portfolio_signal_quality(self._holdings))
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc() if str(e).strip() else repr(e))


class PortfolioOpportunityScanWorker(QThread):
    progress = pyqtSignal(int, int, str)
    done = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, opportunity_profile: str = "balanced"):
        super().__init__()
        self._opportunity_profile = opportunity_profile

    def run(self):
        try:
            from analysis.opportunity import scan_universe

            def _progress(index: int, total: int, ticker: str):
                self.progress.emit(index + 1, total, ticker)

            self.done.emit(scan_universe(list(config.SCANNER_UNIVERSE), progress_callback=_progress, profile=self._opportunity_profile))
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc() if str(e).strip() else repr(e))


class HoldingItemDelegate(QStyledItemDelegate):
    """Paint recommendation tint for portfolio rows without fighting the global QSS."""

    def paint(self, painter: QPainter, option, index):
        painter.save()

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if not selected:
            background = index.data(Qt.ItemDataRole.BackgroundRole)
            if isinstance(background, QBrush):
                rect = option.rect.adjusted(2, 1, -2, -1)
                path = QPainterPath()
                path.addRoundedRect(float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()), 6.0, 6.0)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.fillPath(path, background)

        painter.restore()
        super().paint(painter, option, index)


# ─────────────────────────────────────────────────────────────────────────────
# Instrument list helper — all known instruments with names
# ─────────────────────────────────────────────────────────────────────────────

def _build_instrument_list() -> list[dict]:
    """Returns [{ticker, name, group}, ...] for all known instruments."""
    result = []
    groups = [
        (config.STOCKS_US_DEFAULT, "US"),
        (config.STOCKS_EU,         "EU"),
        (config.ETFS_BROAD,        "ETF"),
        (config.ETFS_SECTOR,       "ETF"),
        (config.ETFS_OTHER,        "ETF"),
        (config.ETFS_THEMATIC,     "ETF"),
        (config.STOCKS_PL,         "PL"),
        (config.INDICES_US,        "IDX"),
        (config.INDICES_INTL,      "IDX"),
        (config.INDICES_PL,        "IDX"),
    ]
    seen = set()
    for lst, grp in groups:
        for entry in lst:
            t = entry["ticker"]
            if t not in seen:
                seen.add(t)
                result.append({"ticker": t, "name": entry.get("name", t), "group": grp})
    return result


_ALL_INSTRUMENTS: list[dict] = []  # lazy-loaded


def _get_instruments() -> list[dict]:
    global _ALL_INSTRUMENTS
    if not _ALL_INSTRUMENTS:
        _ALL_INSTRUMENTS = _build_instrument_list()
    return _ALL_INSTRUMENTS


# ─────────────────────────────────────────────────────────────────────────────
# Add / Edit dialog
# ─────────────────────────────────────────────────────────────────────────────

class AddHoldingDialog(QDialog):
    """
    Add or edit a portfolio holding.

    Parameters
    ----------
    prefill_ticker : str | None
        When provided, the ticker selector is pre-filled and locked.
    prefill_price : float | None
        Pre-fill the price field (e.g. current market price from scanner).
    existing : dict | None
        When editing an existing holding — ticker locked, fields pre-filled.
    """

    def __init__(
        self,
        parent=None,
        existing: dict | None = None,
        prefill_ticker: str | None = None,
        prefill_price: float | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Dodaj / edytuj pozycję")
        self.setMinimumWidth(420)

        self._selected_ticker: str = ""
        self._ticker_locked = bool(existing or prefill_ticker)
        self.resize(420, 270 if self._ticker_locked else 560)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── Ticker selection ─────────────────────────────────────────────
        if self._ticker_locked:
            # Show as a read-only label
            t = existing["ticker"] if existing else (prefill_ticker or "").upper()
            self._selected_ticker = t
            instr = next((i for i in _get_instruments() if i["ticker"] == t), None)
            name_str = f" — {instr['name']}" if instr else ""
            ticker_lbl = QLabel(f"<b>{t}</b>{name_str}")
            ticker_lbl.setObjectName("infoLabel")
            layout.addWidget(QLabel("Instrument:"))
            layout.addWidget(ticker_lbl)
        else:
            # Searchable list
            layout.addWidget(QLabel("Wybierz instrument:"))
            self._filter_box = QLineEdit()
            self._filter_box.setPlaceholderText("Filtruj po tickerze lub nazwie…")
            self._filter_box.setObjectName("searchBox")
            self._filter_box.textChanged.connect(self._filter_instruments)
            layout.addWidget(self._filter_box)

            self._inst_list = QListWidget()
            self._inst_list.setObjectName("tickerList")
            self._inst_list.setFixedHeight(180)
            self._inst_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self._inst_list.itemClicked.connect(self._on_inst_selected)
            layout.addWidget(self._inst_list)
            self._populate_instruments("")

            self._ticker_label = QLabel("<i>Nie wybrano instrumentu</i>")
            self._ticker_label.setObjectName("statusLabel")
            layout.addWidget(self._ticker_label)

        # ── Details form ─────────────────────────────────────────────────
        form = QFormLayout()
        form.setSpacing(7)

        self._shares_spin = QDoubleSpinBox()
        self._shares_spin.setRange(0.0001, 1_000_000)
        self._shares_spin.setDecimals(4)
        self._shares_spin.setValue(1.0)

        self._price_spin = QDoubleSpinBox()
        self._price_spin.setRange(0.0001, 1_000_000)
        self._price_spin.setDecimals(4)
        self._price_spin.setValue(prefill_price if prefill_price else 100.0)

        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDate(QDate.currentDate())
        self._date_edit.setDisplayFormat("yyyy-MM-dd")

        self._notes_edit = QLineEdit()
        self._notes_edit.setPlaceholderText("opcjonalne notatki")
        self._notes_edit.setObjectName("searchBox")

        form.addRow("Liczba akcji / jednostek:", self._shares_spin)
        form.addRow("Cena zakupu (za szt.):",    self._price_spin)
        form.addRow("Data zakupu:",               self._date_edit)
        form.addRow("Notatki:",                   self._notes_edit)
        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        # Pre-fill from existing holding
        if existing:
            self._shares_spin.setValue(float(existing["shares"]))
            self._price_spin.setValue(float(existing["avg_price"]))
            self._notes_edit.setText(existing.get("notes", ""))
            pd = existing.get("purchase_date") or existing.get("added", "")
            if pd:
                self._date_edit.setDate(QDate.fromString(pd, "yyyy-MM-dd"))

    # ── Instrument list helpers ───────────────────────────────────────────

    def _populate_instruments(self, query: str):
        self._inst_list.clear()
        q = query.strip().lower()
        for instr in _get_instruments():
            if not q or q in instr["ticker"].lower() or q in instr["name"].lower():
                item = QListWidgetItem(f"{instr['ticker']}  —  {instr['name']}  [{instr['group']}]")
                item.setData(Qt.ItemDataRole.UserRole, instr["ticker"])
                self._inst_list.addItem(item)
        # If no match in list, show hint that custom ticker can be used
        if self._inst_list.count() == 0 and q:
            hint = QListWidgetItem(f"⌨  Użyj własnego tickera: {q.upper()}")
            hint.setData(Qt.ItemDataRole.UserRole, q.upper())
            hint.setForeground(Qt.GlobalColor.gray)
            self._inst_list.addItem(hint)

    def _filter_instruments(self, text: str):
        self._populate_instruments(text)
        self._selected_ticker = ""
        self._ticker_label.setText("<i>Nie wybrano instrumentu</i>")

    def _on_inst_selected(self, item: QListWidgetItem):
        self._selected_ticker = item.data(Qt.ItemDataRole.UserRole)
        self._ticker_label.setText(f"✓ Wybrano: <b>{self._selected_ticker}</b>")

    # ── Accept / values ───────────────────────────────────────────────────

    def _on_accept(self):
        if not self._selected_ticker:
            QMessageBox.warning(self, "Błąd", "Wybierz instrument z listy.")
            return
        self.accept()

    def get_values(self) -> dict:
        return {
            "ticker":        self._selected_ticker,
            "shares":        self._shares_spin.value(),
            "avg_price":     self._price_spin.value(),
            "purchase_date": self._date_edit.date().toString("yyyy-MM-dd"),
            "notes":         self._notes_edit.text().strip(),
        }


class CloseHoldingDialog(QDialog):
    """Dialog for closing or editing a closed portfolio position."""

    def __init__(self, position: dict, parent=None, prefill_exit_price: float | None = None):
        super().__init__(parent)
        self._position = dict(position)
        self._syncing_fields = False

        ticker = str(position.get("ticker") or "—").upper()
        shares = float(position.get("shares") or 0.0)
        avg_price = float(position.get("avg_price") or 0.0)
        currency = str(position.get("currency") or "USD").upper() or "USD"
        purchase_date = position.get("purchase_date") or position.get("added") or "—"

        self.setWindowTitle(
            f"Edytuj zamkniętą pozycję — {ticker}"
            if str(position.get("status") or "").lower() == "closed"
            else f"Zamknij pozycję — {ticker}"
        )
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        info = QLabel(
            f"<b>{ticker}</b> · {shares:.4g} szt. · wejście {avg_price:.4f} {currency} · kupiono {purchase_date}"
        )
        info.setObjectName("infoLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        form.setSpacing(7)

        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDisplayFormat("yyyy-MM-dd")
        initial_date = QDate.fromString(str(position.get("closed_date") or ""), "yyyy-MM-dd")
        self._date_edit.setDate(initial_date if initial_date.isValid() else QDate.currentDate())

        max_price = 1_000_000_000.0
        max_invested = max(0.0, shares * avg_price)

        self._exit_price_spin = QDoubleSpinBox()
        self._exit_price_spin.setRange(0.0, max_price)
        self._exit_price_spin.setDecimals(4)
        self._exit_price_spin.setKeyboardTracking(False)

        self._pnl_spin = QDoubleSpinBox()
        self._pnl_spin.setRange(-max_invested, max_price)
        self._pnl_spin.setDecimals(2)
        self._pnl_spin.setKeyboardTracking(False)

        self._pnl_pct = QLabel("—")
        self._pnl_pct.setObjectName("infoLabel")

        form.addRow("Data zamknięcia:", self._date_edit)
        form.addRow("Cena zamknięcia:", self._exit_price_spin)
        form.addRow("Zysk / strata:", self._pnl_spin)
        form.addRow("Wynik %:", self._pnl_pct)
        layout.addLayout(form)

        note = QLabel(
            "Zmiana ceny zamknięcia automatycznie przelicza zysk/stratę. "
            "Jeżeli wpiszesz zysk lub stratę, cena zamknięcia uzupełni się sama."
        )
        note.setObjectName("infoLabel")
        note.setWordWrap(True)
        layout.addWidget(note)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("Zapisz")
        cancel_btn = btns.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setText("Anuluj")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self._exit_price_spin.valueChanged.connect(self._on_exit_price_changed)
        self._pnl_spin.valueChanged.connect(self._on_pnl_changed)

        initial_metrics = _resolve_close_metrics(
            self._position,
            exit_price=position.get("exit_price") if position.get("exit_price") is not None else prefill_exit_price,
            realized_pnl_abs=position.get("realized_pnl_abs"),
        )
        if initial_metrics.get("exit_price") is None:
            initial_metrics = _resolve_close_metrics(self._position, exit_price=prefill_exit_price or avg_price)
        self._apply_metrics(initial_metrics)

    def _apply_metrics(self, metrics: dict):
        self._syncing_fields = True
        self._exit_price_spin.setValue(float(metrics.get("exit_price") or 0.0))
        self._pnl_spin.setValue(float(metrics.get("realized_pnl_abs") or 0.0))
        pnl_pct = metrics.get("realized_pnl_pct")
        if pnl_pct is None:
            self._pnl_pct.setText("—")
        else:
            self._pnl_pct.setText(
                f"<span style='font-weight:700;color:{_col_pnl(pnl_pct)}'>{pnl_pct:+.2f}%</span>"
            )
        self._syncing_fields = False

    def _on_exit_price_changed(self, value: float):
        if self._syncing_fields:
            return
        self._apply_metrics(_resolve_close_metrics(self._position, exit_price=value))

    def _on_pnl_changed(self, value: float):
        if self._syncing_fields:
            return
        self._apply_metrics(_resolve_close_metrics(self._position, realized_pnl_abs=value))

    def get_values(self) -> dict:
        self._exit_price_spin.interpretText()
        self._pnl_spin.interpretText()
        return {
            "closed_date": self._date_edit.date().toString("yyyy-MM-dd"),
            "exit_price": self._exit_price_spin.value(),
            "realized_pnl_abs": self._pnl_spin.value(),
        }


class PortfolioAIAdviceDialog(QDialog):
    def __init__(self, holdings: list[dict], parent=None):
        super().__init__(parent)
        self._holdings = [dict(h) for h in holdings]
        self._worker: AIPortfolioAdviceWorker | None = None

        self.setWindowTitle("PORADA AI")
        self.resize(1040, 820)
        self.setMinimumSize(860, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        info = QLabel("Wybierz model, uruchom analizę i odbierz gotowy raport AI dla portfela w czytelnych sekcjach.")
        info.setObjectName("infoLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        note = QLabel("Token zapisuje się lokalnie w ~/.analizator_rynkowy/ai_advice_settings.json. Dla opcji 'Inny' podaj kompatybilny Base URL, np. kończący się na /v1.")
        note.setObjectName("infoLabel")
        note.setWordWrap(True)
        layout.addWidget(note)

        self._form = QFormLayout()
        self._form.setSpacing(7)

        self._provider_combo = QComboBox()
        for provider_key, provider_label in AI_PROVIDERS:
            self._provider_combo.addItem(provider_label, provider_key)

        self._model_combo = QComboBox()

        self._custom_model_edit = QLineEdit()
        self._custom_model_edit.setPlaceholderText("np. my-model-1")
        self._custom_model_edit.setObjectName("searchBox")

        self._base_url_edit = QLineEdit()
        self._base_url_edit.setPlaceholderText("https://twoj-dostawca.example.com/v1")
        self._base_url_edit.setObjectName("searchBox")

        self._token_edit = QLineEdit()
        self._token_edit.setPlaceholderText("wklej token API")
        self._token_edit.setObjectName("searchBox")
        self._token_edit.setEchoMode(QLineEdit.EchoMode.Password)

        self._form.addRow("Dostawca:", self._provider_combo)
        self._form.addRow("Model:", self._model_combo)
        self._form.addRow("Własny model:", self._custom_model_edit)
        self._form.addRow("Base URL:", self._base_url_edit)
        self._form.addRow("Token API:", self._token_edit)
        layout.addLayout(self._form)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._save_btn = QPushButton("💾  Zapisz ustawienia")
        self._save_btn.setObjectName("addBtn")
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)

        self._analyze_btn = QPushButton("🤖  Analiza")
        self._analyze_btn.setObjectName("scanBtn")
        self._analyze_btn.clicked.connect(self._on_analyze)
        btn_row.addWidget(self._analyze_btn)

        self._close_btn = QPushButton("Zamknij")
        self._close_btn.setObjectName("removeBtn")
        self._close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._close_btn)

        layout.addLayout(btn_row)

        self._status = QLabel("Wybierz dostawcę, model i zapisz token API.")
        self._status.setObjectName("infoLabel")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._progress = QProgressBar()
        self._progress.setObjectName("scanProgress")
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._result_view = QTextBrowser()
        self._result_view.setReadOnly(True)
        self._result_view.setOpenExternalLinks(True)
        self._result_view.setOpenLinks(True)
        self._result_view.setMinimumHeight(360)
        self._result_view.setHtml(self._format_message_html(
            "PORADA AI",
            "Tutaj pojawi się czytelny raport AI dla portfela: sekcje, werdykty, ryzyka i źródła.",
        ))
        layout.addWidget(self._result_view, 1)

        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self._apply_settings(load_ai_advice_settings())

    def _set_row_visible(self, field: QWidget, visible: bool):
        label = self._form.labelForField(field)
        if label is not None:
            label.setVisible(visible)
        field.setVisible(visible)

    def _apply_settings(self, settings: dict):
        normalized = normalize_ai_settings(settings)
        provider = normalized.get("provider") or "openai"
        index = self._provider_combo.findData(provider)
        if index >= 0:
            self._provider_combo.setCurrentIndex(index)
        self._token_edit.setText(normalized.get("token") or "")
        self._base_url_edit.setText(normalized.get("base_url") or "")
        self._custom_model_edit.setText(normalized.get("custom_model") or "")
        self._refresh_model_options(normalized.get("model") or "")
        self._update_provider_rows()

    def _refresh_model_options(self, preferred_model: str = ""):
        provider = str(self._provider_combo.currentData() or "openai")
        models = get_provider_models(provider)
        current_text = preferred_model or self._model_combo.currentText().strip()
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.addItems(models)
        if current_text:
            index = self._model_combo.findText(current_text)
            if index >= 0:
                self._model_combo.setCurrentIndex(index)
        self._model_combo.blockSignals(False)

    def _update_provider_rows(self):
        provider = str(self._provider_combo.currentData() or "openai")
        is_builtin = provider in {"openai", "gemini"}
        self._set_row_visible(self._model_combo, is_builtin)
        self._set_row_visible(self._custom_model_edit, provider == "other")
        self._set_row_visible(self._base_url_edit, provider == "other")

    def _collect_settings(self) -> dict:
        provider = str(self._provider_combo.currentData() or "openai")
        custom_model = self._custom_model_edit.text().strip()
        return normalize_ai_settings({
            "provider": provider,
            "token": self._token_edit.text().strip(),
            "model": self._model_combo.currentText().strip() if provider in {"openai", "gemini"} else "",
            "custom_model": custom_model,
            "base_url": self._base_url_edit.text().strip(),
        })

    def _set_busy(self, busy: bool):
        self._provider_combo.setEnabled(not busy)
        self._model_combo.setEnabled(not busy)
        self._custom_model_edit.setEnabled(not busy)
        self._base_url_edit.setEnabled(not busy)
        self._token_edit.setEnabled(not busy)
        self._save_btn.setEnabled(not busy)
        self._analyze_btn.setEnabled(not busy)
        self._close_btn.setEnabled(not busy)
        self._progress.setVisible(busy)

    def _render_inline_html(self, text: str) -> str:
        value = str(text or "")
        token_re = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)|\*\*([^*]+)\*\*")
        chunks = []
        last_end = 0
        for match in token_re.finditer(value):
            chunks.append(html.escape(value[last_end:match.start()]))
            if match.group(1) is not None:
                label = html.escape(match.group(1))
                url = html.escape(match.group(2), quote=True)
                chunks.append(f'<a href="{url}">{label}</a>')
            else:
                chunks.append(f'<strong>{html.escape(match.group(3))}</strong>')
            last_end = match.end()
        chunks.append(html.escape(value[last_end:]))
        rendered = "".join(chunks)

        badges = {
            "POZYTYWNA": "badge-positive",
            "NEGATYWNA": "badge-negative",
            "NEUTRALNA": "badge-neutral",
            "WZROST": "badge-up",
            "SPADEK": "badge-down",
            "MIESZANY": "badge-mixed",
        }
        for token, css_class in badges.items():
            rendered = re.sub(
                rf"\b{token}\b",
                lambda match: f'<span class="badge {css_class}">{match.group(0)}</span>',
                rendered,
                flags=re.IGNORECASE,
            )
        return rendered

    def _render_ai_text_html(self, text: str) -> str:
        if not str(text or "").strip():
            return '<p class="empty-note">AI nie zwróciło treści odpowiedzi.</p>'

        blocks: list[str] = []
        list_open = False

        def close_list():
            nonlocal list_open
            if list_open:
                blocks.append("</ul>")
                list_open = False

        for raw_line in str(text).splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                close_list()
                continue

            heading3 = re.match(r"^###\s+(.+)$", stripped)
            heading2 = re.match(r"^##\s+(.+)$", stripped)
            numbered = re.match(r"^\d+\)\s+(.+)$", stripped)
            bullet = re.match(r"^[-*•]\s+(.+)$", stripped)

            if heading2:
                close_list()
                blocks.append(f'<h2 class="ai-section">{self._render_inline_html(heading2.group(1))}</h2>')
                continue
            if heading3:
                close_list()
                blocks.append(f'<h3 class="ai-subsection">{self._render_inline_html(heading3.group(1))}</h3>')
                continue
            if numbered:
                close_list()
                blocks.append(f'<h2 class="ai-section">{self._render_inline_html(numbered.group(1))}</h2>')
                continue
            if bullet:
                if not list_open:
                    blocks.append('<ul class="ai-list">')
                    list_open = True
                blocks.append(f'<li>{self._render_inline_html(bullet.group(1))}</li>')
                continue

            close_list()
            blocks.append(f'<p>{self._render_inline_html(stripped)}</p>')

        close_list()
        return "".join(blocks)

    def _format_message_html(self, title: str, body: str, tone: str = "info") -> str:
        tone_class = {
            "info": "message-info",
            "error": "message-error",
        }.get(tone, "message-info")
        return f"""
        <html><head><meta charset="utf-8"></head><body class="ai-body">
          <style>
            body.ai-body {{ background:#f4f6fb; color:#18212f; font-family:"Helvetica Neue",Arial,sans-serif; font-size:13px; margin:0; padding:18px; }}
            .message-card {{ border-radius:14px; padding:16px 18px; border:1px solid #d7deea; background:#ffffff; }}
            .message-info {{ border-left:5px solid #2563eb; }}
            .message-error {{ border-left:5px solid #dc2626; background:#fff5f5; border-color:#fecaca; }}
            .message-title {{ font-size:18px; font-weight:800; margin-bottom:8px; color:#0f172a; }}
            .message-body {{ color:#475569; line-height:1.7; }}
          </style>
          <div class="message-card {tone_class}">
            <div class="message-title">{html.escape(title)}</div>
            <div class="message-body">{self._render_inline_html(body)}</div>
          </div>
        </body></html>"""

    def _list_items_html(self, items, empty_text: str) -> str:
        rows = [str(item).strip() for item in (items or []) if str(item).strip()]
        if not rows:
            return f'<div class="empty-note">{html.escape(empty_text)}</div>'
        return f'<ul class="card-list">{"".join(f"<li>{self._render_inline_html(row)}</li>" for row in rows)}</ul>'

    def _verdict_class(self, verdict: str) -> str:
        value = str(verdict or "").upper()
        if "DOKUP" in value:
            return "verdict-buy"
        if "SPRZED" in value or "OGRANICZ" in value:
            return "verdict-sell"
        if "OBSERWUJ" in value:
            return "verdict-watch"
        return "verdict-hold"

    def _confidence_weight(self, confidence: str) -> int:
        value = str(confidence or "").upper()
        if value == "WYSOKA":
            return 18
        if value in {"SREDNIA", "ŚREDNIA"}:
            return 10
        if value == "NISKA":
            return 4
        return 0

    def _verdict_weight(self, verdict: str) -> int:
        value = str(verdict or "").upper()
        if "SPRZED" in value:
            return 120
        if "OGRANICZ" in value:
            return 112
        if "DOKUP" in value:
            return 95
        if "OBSERWUJ" in value:
            return 74
        return 56

    def _short_sentence(self, text: str, fallback: str) -> str:
        clean = " ".join(str(text or "").split())
        if not clean:
            return fallback
        match = re.match(r"(.+?[.!?])(?:\s|$)", clean)
        if match:
            return match.group(1).strip()
        return clean

    def _position_priority_note(self, position: dict, context_row: dict, size_rank: int | None, total_count: int) -> str:
        notes = []
        verdict = str(position.get("verdict") or "").upper()
        confidence = str(position.get("confidence") or "").upper()
        pnl_pct = abs(float(context_row.get("pnl_pct") or 0.0))

        if "SPRZED" in verdict or "OGRANICZ" in verdict:
            notes.append("wymaga decyzji")
        elif "DOKUP" in verdict:
            notes.append("moze byc kandydatem do ruchu")
        elif "OBSERWUJ" in verdict:
            notes.append("warto miec ja pod reka")

        if size_rank is not None and total_count > 0 and size_rank < min(3, total_count):
            notes.append("to jedna z wiekszych pozycji")

        if confidence == "WYSOKA":
            notes.append("AI ma tu wysoka pewnosc")

        if pnl_pct >= 12:
            notes.append("wynik pozycji juz mocno odchylil sie od wejscia")

        if not notes:
            return "ma zauwazalny wplyw na obraz portfela"
        return " • ".join(notes[:3])

    def _ordered_structured_positions(self, result: dict, structured: dict) -> list[dict]:
        context_holdings = ((result.get("context") or {}).get("holdings") or [])
        context_map: dict[str, dict] = {}
        value_rank_map: dict[str, int] = {}
        for index, row in enumerate(context_holdings):
            ticker = str(row.get("ticker") or "").upper()
            if not ticker:
                continue
            if ticker not in context_map:
                context_map[ticker] = dict(row)
                value_rank_map[ticker] = index

        total_count = len(context_holdings)
        ordered = []
        for position in (structured.get("positions") or []):
            ticker = str(position.get("ticker") or "").upper()
            context_row = dict(context_map.get(ticker) or {})
            size_rank = value_rank_map.get(ticker)
            size_bonus = max(0, total_count - size_rank) * 3 if size_rank is not None else 0
            pnl_pct = abs(float(context_row.get("pnl_pct") or 0.0))
            pnl_bonus = 8 if pnl_pct >= 12 else 4 if pnl_pct >= 6 else 0
            score = self._verdict_weight(position.get("verdict")) + self._confidence_weight(position.get("confidence")) + size_bonus + pnl_bonus

            enriched = dict(position)
            enriched["_context"] = context_row
            enriched["_priority_score"] = score
            enriched["_priority_note"] = self._position_priority_note(position, context_row, size_rank, total_count)
            ordered.append(enriched)

        ordered.sort(
            key=lambda item: (
                -float(item.get("_priority_score") or 0.0),
                value_rank_map.get(str(item.get("ticker") or "").upper(), 10**6),
                str(item.get("ticker") or ""),
            )
        )
        for index, item in enumerate(ordered, start=1):
            item["_priority_rank"] = index
        return ordered

    def _format_structured_result_html(self, result: dict, structured: dict) -> str:
        summary = (result.get("context") or {}).get("portfolio_summary") or {}
        provider = result.get("provider_label") or result.get("provider") or "—"
        model = result.get("model") or "—"
        internet_checked = structured.get("internet_checked")
        internet_label = "TAK" if internet_checked else "NIE"
        chips = [
            ("Provider", provider),
            ("Model", model),
            ("Internet", internet_label),
            ("Pozycje", str(summary.get("positions", 0))),
            ("Wygenerowano", result.get("generated_at") or "—"),
        ]
        chip_html = "".join(
            f'<div class="summary-chip"><div class="summary-label">{html.escape(label)}</div><div class="summary-value">{html.escape(value)}</div></div>'
            for label, value in chips
        )

        ordered_positions = self._ordered_structured_positions(result, structured)

        def _money_text(context_row: dict) -> str:
            current_value = context_row.get("current_value")
            if current_value in (None, ""):
                return "—"
            text = _fmt_money(current_value)
            currency = str(context_row.get("currency") or "").strip()
            return f"{text} {html.escape(currency)}" if text != "—" and currency else text

        def _pnl_text(context_row: dict) -> str:
            pnl_pct = context_row.get("pnl_pct")
            if pnl_pct in (None, ""):
                return "—"
            try:
                return f"{float(pnl_pct):+.1f}%"
            except Exception:
                return "—"

        overview = structured.get("portfolio_overview") or {}
        overview_html = f"""
        <div class="section-card">
            <h2>Szybki obraz portfela</h2>
            <div class="section-lead">{self._render_inline_html(overview.get('summary') or 'Brak podsumowania portfela.')}</div>
            <div class="subcard"><div class="mini-title">Najmocniejsze</div>{self._list_items_html(overview.get('strongest_positions'), 'Brak wyróżnionych pozycji.')}</div>
            <div class="subcard"><div class="mini-title">Najsłabsze</div>{self._list_items_html(overview.get('weakest_positions'), 'Brak wskazanych słabych pozycji.')}</div>
            <div class="subcard"><div class="mini-title">Główne ryzyka</div>{self._list_items_html(overview.get('main_risks'), 'Brak głównych ryzyk.')}</div>
        </div>"""

        focus_cards = []
        for position in ordered_positions[:4]:
            context_row = position.get("_context") or {}
            verdict = str(position.get("verdict") or "TRZYMAJ")
            verdict_class = self._verdict_class(verdict)
            short_summary = self._short_sentence(position.get("summary") or "", "Brak krótkiego podsumowania.")
            focus_cards.append(f"""
            <div class="focus-card">
                <div class="focus-kicker">Priorytet #{int(position.get('_priority_rank') or 0)}</div>
                <div class="focus-title">{self._render_inline_html(position.get('ticker') or 'Ticker')}</div>
                <div class="focus-subtitle">{self._render_inline_html(position.get('name') or '')}</div>
                <div class="pill-row">
                    <span class="verdict {verdict_class}">{html.escape(verdict)}</span>
                    <span class="soft-pill">Pewność: {html.escape(str(position.get('confidence') or '—'))}</span>
                    <span class="soft-pill">Wpływ: {_money_text(context_row)}</span>
                    <span class="soft-pill" style="color:{_col_pnl(context_row.get('pnl_pct'))}">P/L: {_pnl_text(context_row)}</span>
                </div>
                <div class="focus-note">Dlaczego wysoko: {html.escape(str(position.get('_priority_note') or ''))}</div>
                <div class="focus-summary">{self._render_inline_html(short_summary)}</div>
            </div>""")
        focus_html = f"""
        <div class="section-card focus-section">
            <h2>Od czego zacząć</h2>
            <div class="section-lead">Najpierw pokazuję spółki z największym wpływem na portfel albo z najbardziej pilnym werdyktem. Dalej kolejność schodzi do spokojniejszych pozycji.</div>
            {''.join(focus_cards) if focus_cards else '<div class="empty-note">AI nie zwróciło wyróżnionych pozycji.</div>'}
        </div>"""

        positions_cards = []
        for position in ordered_positions:
            context_row = position.get("_context") or {}
            verdict = str(position.get("verdict") or "TRZYMAJ")
            verdict_class = self._verdict_class(verdict)
            events_html = []
            for event in (position.get("events") or []):
                events_html.append(f"""
                <div class="event-card compact-event">
                    <div class="event-title">{self._render_inline_html(event.get('title') or 'Wydarzenie')}</div>
                    <div class="badge-row">{self._render_inline_html(event.get('impact') or 'NEUTRALNA')} {self._render_inline_html(event.get('price_reaction') or 'MIESZANY')}</div>
                    <div class="event-why">{self._render_inline_html(event.get('why') or '')}</div>
                </div>""")

            positions_cards.append(f"""
            <div class="position-card">
                <div class="position-rank">#{int(position.get('_priority_rank') or 0)} w kolejności ważności</div>
                <div class="position-title">{self._render_inline_html(position.get('ticker') or 'Ticker')}</div>
                <div class="position-subtitle">{self._render_inline_html(position.get('name') or '')}</div>
                <div class="pill-row">
                    <span class="verdict {verdict_class}">{html.escape(verdict)}</span>
                    <span class="soft-pill">Pewność: {html.escape(str(position.get('confidence') or '—'))}</span>
                    <span class="soft-pill">Wpływ: {_money_text(context_row)}</span>
                    <span class="soft-pill" style="color:{_col_pnl(context_row.get('pnl_pct'))}">P/L: {_pnl_text(context_row)}</span>
                </div>
                <div class="position-note">Dlaczego jest tak wysoko: {html.escape(str(position.get('_priority_note') or ''))}</div>
                <div class="position-summary"><strong>W skrócie:</strong> {self._render_inline_html(position.get('summary') or '')}</div>
                <div class="subcard"><div class="mini-title">Co wspiera</div>{self._list_items_html(position.get('positives'), 'Brak wyróżnionych argumentów.')}</div>
                <div class="subcard"><div class="mini-title">Ryzyka</div>{self._list_items_html(position.get('risks'), 'Brak wskazanych ryzyk.')}</div>
                <div class="subcard"><div class="mini-title">Na co uważać</div>{self._list_items_html(position.get('watchouts'), 'Brak dodatkowych punktów do obserwacji.')}</div>
                <div class="events-wrap">
                    <div class="mini-title">Najważniejsze informacje dla spółki</div>
                    {''.join(events_html) if events_html else '<div class="empty-note">Brak wyróżnionych wydarzeń.</div>'}
                </div>
            </div>""")

        positions_html = f"""
        <div class="section-card">
            <h2>Pozycje od najważniejszej do najmniej ważnej</h2>
            <div class="section-lead">Im wyżej widzisz spółkę, tym większy ma wpływ na portfel albo tym pilniejszy jest werdykt AI.</div>
            {''.join(positions_cards) if positions_cards else '<div class="empty-note">AI nie zwróciło szczegółów dla pozycji.</div>'}
        </div>"""

        internet_cards = []
        for item in (structured.get("internet_summary") or []):
            topic = item.get("topic") or "Informacja"
            impact = item.get("impact") or "NEUTRALNA"
            reaction = item.get("price_reaction") or "MIESZANY"
            why = item.get("why") or ""
            internet_cards.append(f"""
            <div class="event-card">
                <div class="event-title">{self._render_inline_html(topic)}</div>
                <div class="badge-row">{self._render_inline_html(impact)} {self._render_inline_html(reaction)}</div>
                <div class="event-why">{self._render_inline_html(why)}</div>
            </div>""")
        internet_html = f"""
        <div class="section-card">
            <h2>Na końcu: internet i nowe informacje</h2>
            <div class="section-lead">Tu trafiają dodatkowe newsy i sygnały z otoczenia rynkowego. Najważniejsze spółki są już pokazane wyżej.</div>
            {''.join(internet_cards) if internet_cards else '<div class="empty-note">Brak dodatkowych informacji wymagających wyróżnienia.</div>'}
        </div>"""

        watch_html = f"""
        <div class="section-card">
            <h2>Co obserwować dalej</h2>
            {self._list_items_html(structured.get('watch_now'), 'Brak dodatkowych punktów do obserwacji.')}
        </div>"""

        urgent_html = f"""
        <div class="section-card urgent-card">
            <h2>Pilne uwagi</h2>
            {self._list_items_html(structured.get('urgent_actions'), 'Brak pilnych uwag.')}
        </div>"""

        return f"""
        <html>
        <head><meta charset="utf-8"></head>
        <body class="ai-body">
            <style>
                body.ai-body {{ background:linear-gradient(180deg,#f4f7fb 0%,#eef2f9 100%); color:#18212f; font-family:"Helvetica Neue",Arial,sans-serif; font-size:13px; margin:0; padding:18px; }}
                .hero {{ background:#f7faff; border:1px solid #d8e2f0; border-radius:22px; padding:22px; box-shadow:0 12px 28px rgba(15,23,42,.06); color:#18212f; }}
                .hero h1 {{ margin:0 0 8px 0; font-size:24px; color:#0f172a; }}
                .hero p {{ margin:0; color:#475569; line-height:1.65; }}
                .summary-grid {{ margin-top:16px; }}
                .summary-chip {{ display:inline-block; vertical-align:top; min-width:150px; margin:0 10px 10px 0; background:#ffffff; border:1px solid #dce3ef; border-radius:16px; padding:12px 14px; }}
                .summary-label {{ font-size:10px; text-transform:uppercase; letter-spacing:.08em; color:#64748b; margin-bottom:6px; font-weight:700; }}
                .summary-value {{ font-size:14px; font-weight:800; color:#0f172a; line-height:1.35; }}
                .section-card {{ margin-top:16px; background:#fff; border:1px solid #d8e2f0; border-radius:20px; padding:18px; box-shadow:0 14px 30px rgba(15,23,42,.06); }}
                .section-card h2 {{ margin:0 0 12px 0; font-size:18px; color:#0f172a; }}
                .section-lead {{ margin:0 0 12px 0; color:#475569; line-height:1.7; }}
                .mini-title {{ font-size:11px; font-weight:800; text-transform:uppercase; letter-spacing:.06em; color:#30507a; margin-bottom:8px; }}
                .card-list {{ margin:0; padding-left:18px; color:#334155; }}
                .card-list li {{ margin-bottom:7px; line-height:1.65; }}
                .empty-note {{ color:#64748b; }}
                .focus-card, .position-card, .subcard {{ background:#f8fbff; border:1px solid #dce6f6; border-radius:16px; padding:14px; margin-top:12px; }}
                .position-card {{ background:linear-gradient(180deg,#ffffff 0%,#fbfdff 100%); border-color:#d9e5f3; }}
                .focus-kicker, .position-rank {{ font-size:10px; font-weight:900; letter-spacing:.06em; text-transform:uppercase; color:#30507a; margin-bottom:6px; }}
                .focus-title, .position-title {{ font-size:20px; font-weight:900; color:#0f172a; }}
                .focus-subtitle, .position-subtitle {{ font-size:12px; color:#64748b; margin-top:4px; }}
                .pill-row {{ margin-top:10px; }}
                .soft-pill {{ display:inline-block; padding:6px 10px; border-radius:999px; background:#f8fafc; border:1px solid #dbe5f1; font-size:11px; color:#334155; margin:0 8px 8px 0; }}
                .verdict {{ display:inline-block; padding:8px 12px; border-radius:999px; font-size:11px; font-weight:900; letter-spacing:.05em; margin:0 8px 8px 0; }}
                .verdict-buy {{ background:#dcfce7; color:#166534; }}
                .verdict-sell {{ background:#fee2e2; color:#b91c1c; }}
                .verdict-watch {{ background:#fef3c7; color:#92400e; }}
                .verdict-hold {{ background:#dbeafe; color:#1d4ed8; }}
                .focus-note, .position-note {{ font-size:12px; color:#475569; margin-top:4px; line-height:1.65; }}
                .focus-summary, .position-summary {{ font-size:14px; color:#334155; margin-top:10px; line-height:1.75; }}
                .events-wrap {{ margin-top:12px; }}
                .event-card {{ background:#fffdf8; border:1px solid #f5ddba; border-radius:14px; padding:12px 14px; margin-top:10px; }}
                .compact-event {{ background:#fff; border-color:#e4eaf2; }}
                .event-title {{ font-size:13px; font-weight:800; color:#0f172a; }}
                .event-why {{ margin-top:8px; color:#475569; line-height:1.7; }}
                .badge-row {{ margin-top:8px; }}
                .badge {{ display:inline-block; padding:3px 9px; border-radius:999px; font-size:10px; font-weight:900; letter-spacing:.04em; margin:0 8px 8px 0; }}
                .badge-positive {{ background:#dcfce7; color:#166534; }}
                .badge-negative {{ background:#fee2e2; color:#b91c1c; }}
                .badge-neutral {{ background:#e2e8f0; color:#334155; }}
                .badge-up {{ background:#dbeafe; color:#1d4ed8; }}
                .badge-down {{ background:#ffedd5; color:#c2410c; }}
                .badge-mixed {{ background:#ede9fe; color:#6d28d9; }}
                .urgent-card {{ border-color:#fecaca; background:linear-gradient(180deg,#fff 0%,#fff6f6 100%); }}
                a {{ color:#2563eb; text-decoration:none; }}
            </style>
            <div class="hero">
                <h1>Raport AI dla portfela</h1>
                <p>Najpierw pokazuję spółki najważniejsze dla portfela i te, przy których AI widzi pilniejszą decyzję. Dopiero niżej schodzę do tła, pozostałych uwag i newsów.</p>
                <div class="summary-grid">{chip_html}</div>
            </div>
            {focus_html}
            {urgent_html}
            {positions_html}
            {overview_html}
            {watch_html}
            {internet_html}
        </body>
        </html>"""

    def _format_result_html(self, result: dict) -> str:
        summary = (result.get("context") or {}).get("portfolio_summary") or {}
        structured = result.get("structured") or {}
        if structured:
            return self._format_structured_result_html(result, structured)

        provider = result.get("provider_label") or result.get("provider") or "—"
        model = result.get("model") or "—"
        internet_label = "Potwierdzone użycie wyszukiwania" if result.get("used_web_search") else "Brak potwierdzonego web search"
        generated = result.get("generated_at") or "—"
        rec_counts = summary.get("recommendation_counts") or {}
        chips = [
            ("Provider", provider),
            ("Model", model),
            ("Internet", internet_label),
            ("Pozycje", str(summary.get("positions", 0))),
            ("Wygenerowano", generated),
        ]
        summary_rows = []
        if rec_counts:
            summary_rows.append(
                "".join(
                    f'<span class="mini-chip"><strong>{html.escape(str(label))}</strong>: {int(value)}</span>'
                    for label, value in rec_counts.items()
                )
            )

        body_html = self._render_ai_text_html(str(result.get("text") or "").strip())
        chip_html = "".join(
            f'<div class="summary-chip"><div class="summary-label">{html.escape(label)}</div><div class="summary-value">{html.escape(value)}</div></div>'
            for label, value in chips
        )
        counts_html = f'<div class="count-row">{"".join(summary_rows)}</div>' if summary_rows else ""

        return f"""
        <html>
        <head><meta charset="utf-8"></head>
        <body class="ai-body">
          <style>
            body.ai-body {{ background:#f4f6fb; color:#18212f; font-family:"Helvetica Neue",Arial,sans-serif; font-size:13px; margin:0; padding:18px; }}
            .hero {{ background:linear-gradient(135deg,#ffffff 0%,#eef4ff 100%); border:1px solid #d8e2f0; border-radius:18px; padding:18px; box-shadow:0 10px 26px rgba(15,23,42,.05); }}
            .hero h1 {{ margin:0 0 12px 0; font-size:22px; color:#0f172a; }}
            .hero p {{ margin:0; color:#526072; line-height:1.7; }}
            .summary-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-top:14px; }}
            .summary-chip {{ background:#fff; border:1px solid #dce3ef; border-radius:14px; padding:12px 14px; }}
            .summary-label {{ font-size:10px; text-transform:uppercase; letter-spacing:.06em; color:#64748b; margin-bottom:6px; font-weight:700; }}
            .summary-value {{ font-size:14px; font-weight:800; color:#0f172a; line-height:1.35; }}
            .count-row {{ margin-top:12px; display:flex; flex-wrap:wrap; gap:8px; }}
            .mini-chip {{ display:inline-flex; align-items:center; gap:6px; padding:7px 10px; background:#f8fafc; border:1px solid #dbe5f1; border-radius:999px; font-size:11px; color:#334155; }}
            .report {{ margin-top:14px; background:#fff; border:1px solid #d8e2f0; border-radius:18px; padding:18px; box-shadow:0 10px 26px rgba(15,23,42,.05); }}
            .report p {{ margin:0 0 10px 0; color:#334155; line-height:1.75; }}
            .ai-section {{ margin:18px 0 10px 0; padding-bottom:8px; border-bottom:1px solid #e7edf5; font-size:18px; color:#0f172a; }}
            .ai-subsection {{ margin:14px 0 8px 0; padding:10px 12px; background:#f8fbff; border:1px solid #dce6f6; border-left:4px solid #2563eb; border-radius:12px; font-size:15px; color:#10243f; }}
            .ai-list {{ margin:0 0 12px 0; padding-left:18px; color:#334155; }}
            .ai-list li {{ margin:0 0 7px 0; line-height:1.7; }}
            .badge {{ display:inline-block; padding:2px 8px; border-radius:999px; font-size:10px; font-weight:800; letter-spacing:.04em; margin:0 2px; vertical-align:middle; }}
            .badge-positive {{ background:#dcfce7; color:#166534; }}
            .badge-negative {{ background:#fee2e2; color:#b91c1c; }}
            .badge-neutral {{ background:#e2e8f0; color:#334155; }}
            .badge-up {{ background:#dbeafe; color:#1d4ed8; }}
            .badge-down {{ background:#ffedd5; color:#c2410c; }}
            .badge-mixed {{ background:#ede9fe; color:#6d28d9; }}
            a {{ color:#2563eb; text-decoration:none; }}
            a:hover {{ text-decoration:underline; }}
            .empty-note {{ color:#64748b; }}
          </style>
          <div class="hero">
            <h1>Porada AI dla portfela</h1>
                        <p>Jeśli model nie zwróci jeszcze poprawnego JSON, ten widok pokaże czytelny fallback tekstowy. Docelowo AI ma oddawać dane pod łatwe formatowanie raportu.</p>
            <div class="summary-grid">{chip_html}</div>
            {counts_html}
          </div>
          <div class="report">{body_html}</div>
        </body>
        </html>"""
        sources = result.get("sources") or []
        if sources:
            lines.extend(["", "Źródła wykryte przez AI:"])
            for item in sources:
                title = item.get("title") or item.get("uri") or "Źródło"
                uri = item.get("uri") or ""
                lines.append(f"- {title} — {uri}")
        return "\n".join(line for line in lines if line is not None).strip()

    def _error_summary(self, message: str) -> str:
        for line in reversed(str(message or "").splitlines()):
            clean = line.strip()
            if clean:
                return clean
        return "Nieznany błąd AI."

    def _on_provider_changed(self):
        self._refresh_model_options()
        self._update_provider_rows()

    def _on_save(self):
        settings = save_ai_advice_settings(self._collect_settings())
        self._apply_settings(settings)
        provider_label = dict(AI_PROVIDERS).get(settings.get("provider"), settings.get("provider"))
        model = resolve_ai_model(settings) or "—"
        self._status.setText(f"Ustawienia AI zapisane lokalnie. Dostawca: {provider_label}. Model: {model}.")

    def _on_analyze(self):
        if not self._holdings:
            QMessageBox.information(self, "PORADA AI", "Dodaj pozycje do portfela, aby uruchomić analizę AI.")
            return
        if self._worker and self._worker.isRunning():
            return

        settings = self._collect_settings()
        provider = settings.get("provider") or "openai"
        model = resolve_ai_model(settings)
        if not settings.get("token"):
            QMessageBox.warning(self, "PORADA AI", "Wklej token API i zapisz ustawienia.")
            return
        if provider == "other" and not settings.get("base_url"):
            QMessageBox.warning(self, "PORADA AI", "Dla opcji 'Inny' podaj Base URL kompatybilny z OpenAI API.")
            return
        if not model:
            QMessageBox.warning(self, "PORADA AI", "Wybierz model AI lub wpisz własny model dla opcji 'Inny'.")
            return

        settings = save_ai_advice_settings(settings)
        provider_label = dict(AI_PROVIDERS).get(provider, provider)
        self._status.setText(f"Analiza w toku... {provider_label} / {model}")
        self._result_view.setHtml(self._format_message_html(
            "Analiza w toku",
            "Aplikacja zbiera dane o pozycjach, technice, makro, podobnych setupach i newsach, a potem wysyła je do wybranego modelu AI.",
        ))
        self._set_busy(True)

        self._worker = AIPortfolioAdviceWorker(self._holdings, settings)
        self._worker.done.connect(self._on_result_ready)
        self._worker.error.connect(self._on_result_error)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_result_ready(self, result: dict):
        self._set_busy(False)
        self._status.setText("Analiza AI gotowa.")
        self._result_view.setHtml(self._format_result_html(result))

    def _on_result_error(self, message: str):
        self._set_busy(False)
        self._status.setText("Nie udało się pobrać porady AI.")
        self._result_view.setHtml(self._format_message_html("Błąd porady AI", str(message or "").strip(), tone="error"))
        QMessageBox.warning(self, "PORADA AI", f"Nie udało się pobrać porady AI.\n\n{self._error_summary(message)}")

    def _on_worker_finished(self):
        self._worker = None

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "PORADA AI", "Poczekaj na zakończenie bieżącej analizy AI.")
            event.ignore()
            return
        super().closeEvent(event)


class ClosedPositionsDialog(QDialog):
    """Simple manager for editing archived closed positions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._changed = False
        self.setWindowTitle("Zamknięte pozycje")
        self.resize(620, 420)
        self.setMinimumSize(520, 320)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        info = QLabel("Tutaj możesz poprawić datę zamknięcia, cenę wyjścia i zrealizowany wynik dla archiwalnych pozycji.")
        info.setObjectName("infoLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        self._list = QListWidget()
        self._list.setObjectName("tickerList")
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        self._list.itemDoubleClicked.connect(self._on_edit)
        layout.addWidget(self._list, 1)

        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(6)
        self._edit_btn = QPushButton("✏️  Edytuj")
        self._edit_btn.setObjectName("addBtn")
        self._edit_btn.setEnabled(False)
        self._edit_btn.clicked.connect(self._on_edit)
        btn_bar.addWidget(self._edit_btn)
        btn_bar.addStretch()

        close_btn = QPushButton("Zamknij")
        close_btn.setObjectName("refreshBtn")
        close_btn.clicked.connect(self.accept)
        btn_bar.addWidget(close_btn)
        layout.addLayout(btn_bar)

        self._refresh_list()

    @property
    def changed(self) -> bool:
        return self._changed

    def _refresh_list(self):
        self._list.clear()
        positions = load_closed_positions()
        ordered_positions = sorted(
            enumerate(positions),
            key=lambda item: (str(item[1].get("closed_date") or ""), str(item[1].get("ticker") or "")),
            reverse=True,
        )

        if not ordered_positions:
            item = QListWidgetItem("Brak zapisanych zamkniętych pozycji.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._list.addItem(item)
            self._edit_btn.setEnabled(False)
            return

        for index, position in ordered_positions:
            ticker = str(position.get("ticker") or "—").upper()
            currency = str(position.get("currency") or "USD").upper() or "USD"
            close_day = str(position.get("closed_date") or "—")
            pnl_abs = position.get("realized_pnl_abs")
            exit_price = position.get("exit_price")
            label = (
                f"{ticker}  ·  {close_day}  ·  "
                f"P/L {_fmt_money(pnl_abs, signed=True)} {currency}  ·  "
                f"wyjście {_fmt_money(exit_price) if exit_price is not None else '—'}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, index)
            if pnl_abs is not None:
                item.setForeground(QColor(_col_pnl(pnl_abs)))
            self._list.addItem(item)

    def _on_selection_changed(self):
        current = self._list.currentItem()
        index = current.data(Qt.ItemDataRole.UserRole) if current else None
        self._edit_btn.setEnabled(isinstance(index, int))

    def _on_edit(self, *_args):
        current = self._list.currentItem()
        index = current.data(Qt.ItemDataRole.UserRole) if current else None
        if not isinstance(index, int):
            return

        positions = load_closed_positions()
        if index < 0 or index >= len(positions):
            return

        dlg = CloseHoldingDialog(positions[index], parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        values = dlg.get_values()
        update_closed_position(
            index,
            closed_date=values["closed_date"],
            exit_price=values["exit_price"],
            realized_pnl_abs=values["realized_pnl_abs"],
        )
        self._changed = True
        self._refresh_list()


# ─────────────────────────────────────────────────────────────────────────────
# HTML builder for detail modal
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(val, dec=2, suffix="") -> str:
    if val is None: return "—"
    try: return f"{float(val):,.{dec}f}{suffix}"
    except Exception: return "—"


def _fmt_cap(val) -> str:
    if not val: return "—"
    try:
        v = float(val)
        if v >= 1e12: return f"${v/1e12:.2f}T"
        if v >= 1e9:  return f"${v/1e9:.2f}B"
        if v >= 1e6:  return f"${v/1e6:.2f}M"
        return f"${v:,.0f}"
    except Exception: return "—"


def _fmt_money(val, signed: bool = False) -> str:
    if val is None:
        return "—"
    try:
        number = float(val)
        text = f"{number:+,.2f}" if signed else f"{number:,.2f}"
        return text.replace(",", " ")
    except Exception:
        return "—"


def _col_pnl(val) -> str:
    if val is None: return "#6c757d"
    return config.COLOR_UP if float(val) >= 0 else config.COLOR_DOWN


def _gauge_html(value: float, label: str, min_v=0, max_v=100, reverse=False) -> str:
    pct = max(0, min(100, (value - min_v) / (max_v - min_v) * 100))
    if reverse: pct = 100 - pct
    color = "#16a34a" if pct >= 65 else "#f97316" if pct >= 35 else "#dc2626"
    return f"""
    <div style="margin:3px 0">
      <div style="display:flex;justify-content:space-between;font-size:10px;
                  color:#6c757d;margin-bottom:2px">
        <span>{label}</span>
        <span style="color:{color};font-weight:600">{value:.1f}</span>
      </div>
      <div style="background:#e9ecef;border-radius:4px;height:7px">
        <div style="width:{pct:.0f}%;background:{color};border-radius:4px;height:7px"></div>
      </div>
    </div>"""


def _build_macro_section(macro: dict, impact_key: str) -> str:
        if not macro or not macro.get("components"):
                return '<div style="color:#6c757d;font-size:11px">Brak danych makro.</div>'

        impact = int(macro.get(impact_key, 0) or 0)
        impact_label = f"{impact:+d}" if impact else "0"
        impact_color = macro.get("color", "#6c757d")

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
                <div style="font-size:10px;color:#6c757d">Wpływ na decyzję</div>
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
        impact_color = company.get("color", "#6c757d")

        # Parse snapshot time
        snapshot_str = ""
        if company.get("snapshot_time"):
                try:
                        from datetime import datetime
                        snap_dt = datetime.fromisoformat(company["snapshot_time"])
                        snapshot_str = snap_dt.strftime("%d.%m.%Y %H:%M")
                except:
                        snapshot_str = ""

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
                {f'<div style="font-size:9px;color:#adb5bd;margin-top:4px">dane z {snapshot_str}</div>' if snapshot_str else ''}
            </div>
            <div style="text-align:right">
                <div style="font-size:10px;color:#6c757d">Wpływ na decyzję</div>
                <div style="font-size:18px;font-weight:700;color:{impact_color}">{impact_label}</div>
            </div>
        </div>
        <div style="font-size:11px;color:#495057;line-height:1.6;margin-top:10px">{company.get('summary', '')}</div>
        {rows}
        <div style="font-size:10px;font-weight:700;color:#6c757d;text-transform:uppercase;letter-spacing:.5px;margin-top:12px">Ostatnie wydarzenia</div>
        {events_html}
        """


def _build_portfolio_html(data: dict) -> str:
    ticker    = data["ticker"]
    name      = data.get("name", ticker)
    currency  = data.get("currency", "USD")
    price     = data["price"]
    avg_price = data["avg_price"]
    shares    = data["shares"]
    pnl_pct   = data["pnl_pct"]
    pnl_abs   = data["pnl_abs"]
    cur_val   = data["current_value"]
    invested  = data["invested"]
    purchase_date = data.get("purchase_date", "")
    rec       = data["recommendation"]
    rec_color = data["rec_color"]
    reasons   = data.get("reasons", [])
    change_1d = data.get("change_1d", 0)
    rsi       = data.get("rsi14")
    stoch_k   = data.get("stoch_k")
    bb_pos    = data.get("bb_position", 0.5)
    entry_bt  = data.get("entry_backtest", {})
    macro     = data.get("macro") or {}
    company   = data.get("company") or {}
    matches_opportunity_setup = bool(data.get("matches_opportunity_setup", True))

    pnl_col   = _col_pnl(pnl_pct)
    sign      = "+" if pnl_pct >= 0 else ""
    sign_abs  = "+" if pnl_abs >= 0 else ""

    # Recommendation bg colors
    rec_bgs = {
        "STOP LOSS":        ("#fee2e2", "#b91c1c"),
        "SPRZEDAJ":         ("#fee2e2", "#b91c1c"),
        "ROZWAŻ SPRZEDAŻ":  ("#fff7ed", "#c2410c"),
        "TRZYMAJ":          ("#eff6ff", "#1d4ed8"),
        "DOKUP WIĘCEJ":     ("#f0fdf4", "#15803d"),
    }
    rec_bg, rec_fg = rec_bgs.get(rec, ("#f8f9fa", "#495057"))

    # Reasons HTML
    reasons_html = ""
    for r in reasons:
        bg     = "#f0fdf4" if not r["bad"] else "#fef2f2"
        border = "#86efac" if not r["bad"] else "#fca5a5"
        ic_bg  = "#22c55e" if not r["bad"] else "#ef4444"
        reasons_html += f"""
        <div style="background:{bg};border:1px solid {border};border-radius:8px;
                    padding:9px 12px;margin-bottom:7px;display:flex;gap:10px;align-items:flex-start">
          <div style="background:{ic_bg};color:#fff;border-radius:50%;width:24px;height:24px;
                      display:flex;align-items:center;justify-content:center;font-size:12px;
                      flex-shrink:0;margin-top:1px">{r['icon']}</div>
          <div>
            <div style="font-weight:600;font-size:11px;color:#212529;margin-bottom:2px">{r['title']}</div>
            <div style="font-size:10px;color:#495057;line-height:1.5">{r['desc']}</div>
          </div>
        </div>"""

    # Entry backtest cards
    def bt_card(label, sublabel, win_rate, avg_ret):
        if win_rate is None:
            return f"""<div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:8px;
                          padding:10px;text-align:center;flex:1">
                         <div style="font-size:10px;color:#6c757d">{label}</div>
                         <div style="font-size:11px;color:#adb5bd;margin-top:4px">Za mało danych</div>
                       </div>"""
        col = "#16a34a" if win_rate >= 60 else "#f97316" if win_rate >= 45 else "#dc2626"
        sign2 = "+" if avg_ret and avg_ret >= 0 else ""
        avg_str = f"{sign2}{avg_ret:.1f}%" if avg_ret is not None else "—"
        return f"""<div style="background:#fff;border:1px solid #dee2e6;border-radius:8px;
                      padding:10px;text-align:center;flex:1">
                     <div style="font-size:10px;font-weight:600;color:#495057">{label}</div>
                     <div style="font-size:9px;color:#adb5bd;margin-bottom:5px">{sublabel}</div>
                     <div style="font-size:20px;font-weight:700;color:{col}">{win_rate:.0f}%</div>
                     <div style="font-size:9px;color:#6c757d;margin-top:2px">wzrost w historii</div>
                     <div style="font-size:11px;font-weight:600;color:{col};margin-top:3px">avg: {avg_str}</div>
                   </div>"""

    def trajectory_chart_html(traj_bt: dict) -> str:
        current_path = traj_bt.get("current_path_pct") or []
        median_path = traj_bt.get("matched_median_path_pct") or []
        band_low = traj_bt.get("matched_p25_path_pct") or []
        band_high = traj_bt.get("matched_p75_path_pct") or []
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

        start_label = "0"
        end_label = str(traj_bt.get("days_held") or len(current_path) - 1)

        return f"""
        <div style="background:#fcfcfd;border:1px solid #e9ecef;border-radius:10px;padding:10px 12px;margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:8px">
            <div style="font-size:10px;font-weight:700;color:#495057">Ścieżka ceny od wejścia vs historyczne dopasowania</div>
            <div style="display:flex;gap:10px;flex-wrap:wrap;font-size:9px;color:#6c757d">
              <span><span style="display:inline-block;width:10px;height:2px;background:{current_color};vertical-align:middle;margin-right:4px"></span>obecna pozycja</span>
              <span><span style="display:inline-block;width:10px;height:2px;background:{median_color};vertical-align:middle;margin-right:4px"></span>mediana historii</span>
              <span><span style="display:inline-block;width:10px;height:10px;background:rgba(249,115,22,.12);border:1px solid rgba(249,115,22,.18);vertical-align:middle;margin-right:4px"></span>typowy zakres</span>
            </div>
          </div>
          <svg viewBox="0 0 {int(width)} {int(height)}" width="100%" height="168" preserveAspectRatio="none" role="img" aria-label="Porównanie ścieżki ceny od wejścia">
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
            <span>wejście · {start_label} dni</span>
            <span>dziś · {end_label} dni</span>
          </div>
        </div>"""

    def recent_history_chart_html(recent_bt: dict) -> str:
        current_path = recent_bt.get("current_path_pct") or []
        median_path = recent_bt.get("matched_median_path_pct") or []
        band_low = recent_bt.get("matched_p25_path_pct") or []
        band_high = recent_bt.get("matched_p75_path_pct") or []
        window_label = str(recent_bt.get("window_label") or "30 dni")
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
            <span>{window_label} temu</span>
            <span>dziś</span>
          </div>
        </div>"""

    n_samples = entry_bt.get("n_samples", 0)
    bt_info = (
        f"<div style='font-size:10px;color:#6c757d;margin-bottom:8px'>"
        f"Na podstawie <b>{n_samples}</b> historycznych momentów z podobnym setupem pullback. Rdzeń matchera: cena ≥ SMA200, cena ≤ SMA50, RSI ≤ 45, a jeśli dostępne także Stoch %K ≤ 35 i ROC20 ≤ −3%. Finalna próbka jest jeszcze zawężana przez podobieństwo RSI, odległości od średnich, drawdownu i wolumenu."
        f"</div>"
        if n_samples > 0
        else "<div style='font-size:10px;color:#6c757d;margin-bottom:8px'>Za mało historycznych setupów — brak danych porównawczych.</div>"
    )

    bt_html = f"""
    {bt_info}
    <div style="display:flex;gap:8px">
      {bt_card("1 tydzień", "~5 sesji", entry_bt.get("win_rate_5d"), entry_bt.get("avg_return_5d"))}
      {bt_card("1 miesiąc", "~22 sesje", entry_bt.get("win_rate_22d"), entry_bt.get("avg_return_22d"))}
      {bt_card("3 miesiące", "~66 sesji", entry_bt.get("win_rate_66d"), entry_bt.get("avg_return_66d"))}
    </div>"""

    recent_bt = data.get("recent_history_backtest") or {}
    recent_samples = int(recent_bt.get("n_samples", 0) or 0)
    recent_window_label = str(recent_bt.get("window_label") or "30 dni")
    recent_chart = recent_history_chart_html(recent_bt) if recent_samples > 0 else ""
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
            f"ten sam zafiksowany punkt startu (<b>{anchor_label}</b>) i zbliżona zmiana ceny <b>{recent_sign}{recent_pnl}%</b> do dziś, "
            f"ale też podobny kształt całej ścieżki po drodze, profil wolumenu i przebieg RSI — tempo ruchu, odbicia i cofnięcia między świecami.{similarity_html}"
            f"</div>"
        )

    recent_history_html = f"""
    {recent_info}
    {recent_chart}
    <div style="display:flex;gap:8px">
      {bt_card("1 tydzień", "po końcu podobnych okien", recent_bt.get("win_rate_5d"), recent_bt.get("avg_return_5d"))}
      {bt_card("1 miesiąc", "po końcu podobnych okien", recent_bt.get("win_rate_22d"), recent_bt.get("avg_return_22d"))}
      {bt_card("3 miesiące", "po końcu podobnych okien", recent_bt.get("win_rate_66d"), recent_bt.get("avg_return_66d"))}
    </div>"""

    traj_bt = data.get("trajectory_backtest", {})
    t_samples = traj_bt.get("n_samples", 0)
    bars_held = traj_bt.get("bars_held", 0)
    days_held = traj_bt.get("days_held", 0)
    t_pnl = traj_bt.get("pnl_pct", 0.0)
    t_sign = "+" if t_pnl > 0 else ""
    avg_shape_mae = traj_bt.get("avg_shape_mae")
    avg_corr = traj_bt.get("avg_corr")
    avg_setup_distance = traj_bt.get("avg_setup_distance")
    
    if "msg" in traj_bt:
        traj_info = f"<div style='font-size:10px;color:#6c757d;margin-bottom:8px'>{traj_bt['msg']}</div>"
    else:
        similarity_bits = []
        if avg_shape_mae is not None:
            similarity_bits.append(f"średnie odchylenie kształtu <b>{avg_shape_mae:.2f} p.p.</b>")
        if avg_corr is not None:
            similarity_bits.append(f"średnia zgodność kolejnych ruchów <b>{avg_corr:.2f}</b>")
        if avg_setup_distance is not None:
            similarity_bits.append(f"średnia odległość setupu wejścia <b>{avg_setup_distance:.2f}</b>")
        similarity_html = f" Dla wybranej próbki: {', '.join(similarity_bits)}." if similarity_bits else ""
        traj_info = (
            f"<div style='font-size:10px;color:#6c757d;margin-bottom:8px'>"
            f"Na podstawie <b>{t_samples}</b> historycznych okien z podobnym setupem wejścia i przebiegiem ceny od zakupu: ten sam horyzont trzymania (<b>{days_held}</b> dni, <b>{bars_held}</b> świec 4H), zbliżona zmiana <b>{t_sign}{t_pnl}%</b> do dziś, ale też podobny kształt całej ścieżki po drodze — tempo ruchu, odbicia i cofnięcia między świecami.{similarity_html}"
            f"</div>"
            if t_samples > 0
            else "<div style='font-size:10px;color:#6c757d;margin-bottom:8px'>Brak historycznych podobieństw dla obecnej trajektorii i setupu wejścia od zakupu.</div>"
        )

    traj_chart = trajectory_chart_html(traj_bt) if t_samples > 0 else ""
        
    traj_html = f"""
    {traj_info}
    {traj_chart}
    <div style="display:flex;gap:8px">
      {bt_card("1 tydzień", "~5 sesji", traj_bt.get("win_rate_5d"), traj_bt.get("avg_return_5d"))}
      {bt_card("1 miesiąc", "~22 sesje", traj_bt.get("win_rate_22d"), traj_bt.get("avg_return_22d"))}
      {bt_card("3 miesiące", "~66 sesji", traj_bt.get("win_rate_66d"), traj_bt.get("avg_return_66d"))}
    </div>"""

    # Indicators table
    def ind_row(label, val, signal="", signal_color="#6c757d"):
        return f"""<tr>
          <td style="padding:4px 8px;color:#6c757d;font-size:11px;white-space:nowrap">{label}</td>
          <td style="padding:4px 8px;font-weight:600;font-size:11px;color:#212529">{val}</td>
          <td style="padding:4px 8px;font-size:10px;color:{signal_color}">{signal}</td>
        </tr>"""

    rsi_sig = ("Wyprzedaż ▲" if rsi and rsi < 45 else
               "Wykupienie ▼" if rsi and rsi > 70 else "Neutralny")
    rsi_col = "#16a34a" if rsi and rsi < 45 else "#dc2626" if rsi and rsi > 70 else "#6c757d"

    macd_h  = data.get("macd_hist")
    macd_sig2 = "Byczy ▲" if macd_h and macd_h > 0 else "Niedźwiedzi ▼"
    macd_col  = "#16a34a" if macd_h and macd_h > 0 else "#dc2626"

    sma200  = data.get("sma200")
    sma50   = data.get("sma50")
    sma200_sig = "Powyżej ▲" if sma200 and price >= sma200 else "Poniżej ▼"
    sma200_col = "#16a34a" if sma200 and price >= sma200 else "#dc2626"
    sma50_sig  = "Korekta ▲" if sma50 and price <= sma50 else "Powyżej"
    sma50_col  = "#16a34a" if sma50 and price <= sma50 else "#6c757d"

    stk = data.get("stoch_k")
    std = data.get("stoch_d")
    st_sig = ("Wyprzedaż ▲" if stk and stk < 25 else "Wykupienie ▼" if stk and stk > 75 else "Neutralny")
    st_col = "#16a34a" if stk and stk < 25 else "#dc2626" if stk and stk > 75 else "#6c757d"

    # Stop loss suggestion
    stop_html = ""
    sl = data.get("stop_loss_atr")
    sl_pct = data.get("stop_loss_pct")
    if sl:
        sl_col = "#dc2626"
        stop_html = f"""
        <div style="background:#fff5f5;border:1px solid #fca5a5;border-radius:8px;padding:10px 12px">
          <div style="font-size:10px;font-weight:700;color:#dc2626;margin-bottom:4px">🛡️ Sugerowany stop loss (2×ATR)</div>
          <div style="font-size:13px;font-weight:700;color:{sl_col}">{sl:.2f} {currency}</div>
          <div style="font-size:10px;color:#6c757d;margin-top:3px">
            {sl_pct:.1f}% poniżej aktualnej ceny ·
            {((avg_price - sl) / avg_price * 100):.1f}% strata vs cena zakupu
          </div>
        </div>"""

    # Fundamental info
    fund_rows = ""
    for label, val in [
        ("Sektor", data.get("sector", "—")),
        ("Branża", data.get("industry", "—")),
        ("Kap. rynkowa", _fmt_cap(data.get("market_cap"))),
        ("P/E", _fmt(data.get("pe_ratio"), 1)),
        ("Beta", _fmt(data.get("beta"), 2)),
        ("Giełda", data.get("exchange", "—")),
    ]:
        fund_rows += f"""<div style="display:flex;justify-content:space-between;padding:3px 0;
                    border-bottom:1px solid #f1f3f5;font-size:11px">
                    <span style="color:#6c757d">{label}</span>
                    <span style="font-weight:600">{val}</span></div>"""

        value_snapshot = data.get("value_investing") or {}
        value_section_html = """
        <div class="card" style="margin-top:10px">
            <div class="section-title">💎 Value Investing</div>
            <div style="color:#6c757d;font-size:11px">Brak danych do oceny value dla tej pozycji.</div>
        </div>"""
        if value_snapshot:
                value_verdict = str(value_snapshot.get("verdict") or "Neutralna wycena")
                value_score = float(value_snapshot.get("value_score") or 0.0)
                value_trap_risk = float(value_snapshot.get("value_trap_risk") or 0.0)
                margin_of_safety = value_snapshot.get("margin_of_safety_pct")
                margin_text = _fmt(margin_of_safety, 1, "%") if margin_of_safety is not None else "—"
                fair_value_text = _fmt(value_snapshot.get("fair_value_price"))
                target_text = _fmt(value_snapshot.get("target_mean_price"))

                if "trap" in value_verdict.lower():
                        value_bg = "#fff5f5"
                        value_fg = "#dc2626"
                        value_border = "#fecaca"
                elif value_score >= 72:
                        value_bg = "#f0fdf4"
                        value_fg = "#166534"
                        value_border = "#bbf7d0"
                elif value_score >= 58:
                        value_bg = "#fff7ed"
                        value_fg = "#c2410c"
                        value_border = "#fdba74"
                else:
                        value_bg = "#f8fafc"
                        value_fg = "#334155"
                        value_border = "#cbd5e1"

                fair_sources_html = ""
                for source in value_snapshot.get("fair_value_sources") or []:
                        fair_sources_html += f"""
                        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px;margin-top:8px">
                            <div style="font-size:11px;font-weight:700;color:#0f172a">{html.escape(str(source.get('label') or 'Źródło fair value'))}</div>
                            <div style="font-size:15px;font-weight:800;color:#111827;margin-top:3px">{_fmt(source.get('fair_price'))} {currency}</div>
                            <div style="font-size:10px;color:#6c757d;line-height:1.6;margin-top:4px">{html.escape(str(source.get('note') or ''))}</div>
                        </div>"""
                if not fair_sources_html:
                        fair_sources_html = '<div style="font-size:10px;color:#6c757d;margin-top:8px">Brak dodatkowych źródeł fair value; sekcja opiera się na jakości biznesu i dostępnych mnożnikach.</div>'

                strengths_html = "".join(
                        f"<li style='margin-left:18px;margin-top:6px;color:#166534;line-height:1.6'>{html.escape(str(item))}</li>"
                        for item in (value_snapshot.get("strengths") or [])[:4]
                        if str(item).strip()
                ) or "<div style='font-size:10px;color:#6c757d;margin-top:8px'>Brak podbitych mocnych stron.</div>"
                risks_html = "".join(
                        f"<li style='margin-left:18px;margin-top:6px;color:#b91c1c;line-height:1.6'>{html.escape(str(item))}</li>"
                        for item in (value_snapshot.get("risks") or [])[:4]
                        if str(item).strip()
                ) or "<div style='font-size:10px;color:#6c757d;margin-top:8px'>Brak podbitych ryzyk.</div>"

                value_metrics_rows = ""
                for label, val in [
                        ("Value score", f"{value_score:.0f}/100"),
                        ("Ryzyko value trap", f"{value_trap_risk:.0f}/100"),
                        ("Fair value proxy", f"{fair_value_text} {currency}" if fair_value_text != '—' else "—"),
                        ("Margines bezpieczeństwa", margin_text),
                        ("Trend kwartalny", f"{_fmt(value_snapshot.get('quarterly_trend_score'), 0)}/100" if value_snapshot.get("quarterly_trend_score") is not None else "—"),
                        ("Trend kwartalny - opis", html.escape(str(value_snapshot.get("quarterly_trend_summary") or "Brak 4 pełnych kwartałów do testu trendu."))),
                        ("Akcjonariat / insiderzy", f"{_fmt(value_snapshot.get('ownership_score'), 0)}/100" if value_snapshot.get("ownership_score") is not None else "—"),
                        ("Akcjonariat / insiderzy - opis", html.escape(str(value_snapshot.get("ownership_summary") or "Brak stabilnych danych insider/holders dla tego rynku."))),
                        ("Target analityków", f"{target_text} {currency}" if target_text != '—' else "—"),
                        ("Upside do targetu", _fmt(value_snapshot.get("target_upside_pct"), 1, "%")),
                        ("Forward P/E", _fmt(value_snapshot.get("forward_pe"), 1)),
                        ("P/B", _fmt(value_snapshot.get("price_to_book"), 2)),
                        ("FCF yield", _fmt(value_snapshot.get("fcf_yield_pct"), 1, "%")),
                        ("ROA", _fmt(value_snapshot.get("roa_pct"), 1, "%")),
                        ("Marża brutto", _fmt(value_snapshot.get("gross_margin_pct"), 1, "%")),
                        ("OCF / zysk netto", f"{_fmt(value_snapshot.get('cash_conversion_ratio'), 2)}x" if value_snapshot.get("cash_conversion_ratio") is not None else "—"),
                        ("Net debt / EBITDA", f"{_fmt(value_snapshot.get('net_debt_to_ebitda'), 2)}x" if value_snapshot.get("net_debt_to_ebitda") is not None else "—"),
                        ("Payout ratio", _fmt(value_snapshot.get("payout_ratio_pct"), 1, "%")),
                        ("D/E", _fmt(value_snapshot.get("debt_to_equity"), 1)),
                        ("Quick ratio", _fmt(value_snapshot.get("quick_ratio"), 2)),
                        ("ROE", _fmt(value_snapshot.get("roe_pct"), 1, "%")),
                ]:
                        value_metrics_rows += f"""
                        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;padding:4px 0;border-bottom:1px solid #f1f3f5;font-size:11px">
                            <span style="color:#6c757d;flex:0 0 40%">{label}</span>
                            <span style="font-weight:600;color:#212529;flex:1;text-align:right;line-height:1.45;white-space:normal">{val}</span>
                        </div>"""

                company_summary = html.escape(str(value_snapshot.get("company_summary") or value_snapshot.get("summary") or "Brak dodatkowego komentarza jakościowego."))
                value_section_html = f"""
                <div class="card" style="margin-top:10px">
                    <div class="section-title">💎 Value Investing</div>
                    <div style="background:{value_bg};border:1px solid {value_border};border-radius:10px;padding:12px 14px">
                        <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap">
                            <div>
                                <div style="font-size:16px;font-weight:800;color:{value_fg}">{html.escape(value_verdict)}</div>
                                <div style="font-size:11px;color:#6c757d;margin-top:4px;line-height:1.6">{html.escape(str(value_snapshot.get('summary') or ''))}</div>
                            </div>
                            <div style="display:grid;grid-template-columns:repeat(2,minmax(120px,1fr));gap:8px;min-width:280px;flex:1">
                                <div style="background:#ffffffcc;border-radius:8px;padding:8px 10px">
                                    <div style="font-size:10px;color:#6c757d">Value score</div>
                                    <div style="font-size:16px;font-weight:800;color:#111827">{value_score:.0f}/100</div>
                                </div>
                                <div style="background:#ffffffcc;border-radius:8px;padding:8px 10px">
                                    <div style="font-size:10px;color:#6c757d">Ryzyko value trap</div>
                                    <div style="font-size:16px;font-weight:800;color:#111827">{value_trap_risk:.0f}/100</div>
                                </div>
                                <div style="background:#ffffffcc;border-radius:8px;padding:8px 10px">
                                    <div style="font-size:10px;color:#6c757d">Fair value proxy</div>
                                    <div style="font-size:16px;font-weight:800;color:#111827">{fair_value_text} {currency if fair_value_text != '—' else ''}</div>
                                </div>
                                <div style="background:#ffffffcc;border-radius:8px;padding:8px 10px">
                                    <div style="font-size:10px;color:#6c757d">Margines bezpieczeństwa</div>
                                    <div style="font-size:16px;font-weight:800;color:{value_fg}">{margin_text}</div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div style="display:grid;grid-template-columns:1.05fr .95fr;gap:10px;margin-top:10px">
                        <div>
                            <div style="font-size:11px;font-weight:700;color:#6c757d;text-transform:uppercase;letter-spacing:.4px;margin-bottom:6px">Źródła fair value</div>
                            {fair_sources_html}
                            <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px;margin-top:8px">
                                <div style="font-size:11px;font-weight:700;color:#0f172a">Kontekst biznesu</div>
                                <div style="font-size:10px;color:#6c757d;line-height:1.7;margin-top:6px">{company_summary}</div>
                            </div>
                        </div>
                        <div>
                            <div style="font-size:11px;font-weight:700;color:#6c757d;text-transform:uppercase;letter-spacing:.4px;margin-bottom:6px">Wskaźniki value</div>
                            {value_metrics_rows}
                        </div>
                    </div>

                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px">
                        <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:10px 12px">
                            <div style="font-size:11px;font-weight:700;color:#166534">Mocne strony</div>
                            <ul style="margin:0;padding:0">{strengths_html}</ul>
                        </div>
                        <div style="background:#fff7ed;border:1px solid #fdba74;border-radius:10px;padding:10px 12px">
                            <div style="font-size:11px;font-weight:700;color:#c2410c">Ryzyka</div>
                            <ul style="margin:0;padding:0">{risks_html}</ul>
                        </div>
                    </div>
                </div>"""

    # Notes badge
    notes_html = ""
    if data.get("notes"):
        notes_html = f"""<div style="background:#fef9c3;border:1px solid #fde047;border-radius:6px;
                          padding:5px 10px;font-size:11px;color:#713f12;margin-top:8px">
                          📝 {data['notes']}</div>"""

    opportunity_scope_note = ""
    if rec == "DOKUP WIĘCEJ" and not matches_opportunity_setup:
        opportunity_scope_note = """
        <div style="background:#fff7ed;border:1px solid #fdba74;border-radius:8px;padding:9px 12px;margin-top:10px">
            <div style="font-size:10px;font-weight:700;color:#9a3412;margin-bottom:3px">To nie jest świeży setup ze skanera</div>
            <div style="font-size:10px;color:#7c2d12;line-height:1.6">
                Ta rekomendacja dotyczy wzmacniania już otwartej pozycji na podstawie trendu i rozwoju od zakupu.
                Ticker może więc nie pojawić się w skanerze nowych okazji, który szuka świeżych wejść typu pullback.
            </div>
        </div>"""

    # Purchase / added date
    added_str = (
        f"Kupiono: {purchase_date}" if purchase_date else
        f"Dodano: {data.get('added','—')}" if data.get('added') else ""
    )

    page_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8">
<style>
  * {{ box-sizing:border-box;margin:0;padding:0 }}
  body {{ font-family:"Helvetica Neue",Arial,sans-serif;background:#f8f9fa;color:#212529;font-size:12px;padding:0 }}
  .container {{ max-width:960px;margin:0 auto;padding:14px }}
  .card {{ background:#fff;border:1px solid #dee2e6;border-radius:10px;padding:14px;margin-bottom:10px }}
  .section-title {{ font-size:10px;font-weight:700;color:#6c757d;text-transform:uppercase;
                    letter-spacing:.5px;margin-bottom:10px;padding-bottom:5px;border-bottom:1px solid #f1f3f5 }}
  .two-col {{ display:grid;grid-template-columns:1fr 1fr;gap:10px }}
</style>
</head>
<body>
<div class="container">

<!-- HEADER -->
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:flex-start">
    <div>
      <div style="font-size:20px;font-weight:800;color:#212529">{name}</div>
      <div style="font-size:12px;color:#6c757d;margin-top:2px">
        {ticker} · {data.get('sector','—')} · {data.get('exchange','')}
      </div>
      <div style="font-size:11px;color:#adb5bd;margin-top:1px">{added_str}</div>
    </div>
    <div style="text-align:right">
      <div style="background:{rec_bg};color:{rec_fg};border-radius:7px;padding:6px 14px;
                  font-size:12px;font-weight:800;letter-spacing:0.5px">{rec}</div>
    </div>
  </div>

  <!-- P&L row -->
  <div style="margin-top:14px;display:flex;gap:14px;flex-wrap:wrap;align-items:flex-end">
    <div>
      <div style="font-size:11px;color:#6c757d;margin-bottom:2px">Aktualna cena</div>
      <div style="font-size:24px;font-weight:700;color:#212529">{price:.2f} {currency}</div>
      <div style="font-size:12px;color:{_col_pnl(change_1d)}">
        {'↑' if change_1d >= 0 else '↓'} {abs(change_1d):.2f}% dziś
      </div>
    </div>
    <div style="background:{rec_bg};border-radius:8px;padding:10px 16px;text-align:center">
      <div style="font-size:11px;color:#6c757d">Niezrealizowany zysk/strata</div>
      <div style="font-size:22px;font-weight:800;color:{pnl_col}">
        {sign}{pnl_pct:.1f}%
      </div>
      <div style="font-size:12px;font-weight:600;color:{pnl_col}">{sign_abs}{pnl_abs:.2f} {currency}</div>
    </div>
    <div style="font-size:11px;color:#6c757d;line-height:1.8">
      <div>Liczba akcji: <b style="color:#212529">{shares}</b></div>
      <div>Cena zakupu (avg): <b style="color:#212529">{avg_price:.2f} {currency}</b></div>
      <div>Zainwestowano: <b style="color:#212529">{invested:.2f} {currency}</b></div>
      <div>Wartość aktualna: <b style="color:{pnl_col}">{cur_val:.2f} {currency}</b></div>
    </div>
  </div>
  {notes_html}
    {opportunity_scope_note}
</div>

<div class="two-col">
<!-- LEFT -->
<div>
    {value_section_html}

    <div class="card">
        <div class="section-title">🌍 Reżim makro dla regionu</div>
        {_build_macro_section(macro, 'portfolio_bias')}
    </div>

    <div class="card">
        <div class="section-title">🏢 Spółka i wydarzenia</div>
        {_build_company_section(company, 'portfolio_bias')}
    </div>
</div>

<!-- RIGHT -->
<div>
        <div class="card">
                <div class="section-title">📊 Szansa na wzrost (backtest historyczny)</div>
                {bt_html}
        </div>

        <!-- Recent history -->
    <div class="card" style="margin-top:10px">
                <div class="section-title">🧭 Analiza na podstawie historii</div>
                {recent_history_html}
  </div>

  <!-- Historical from purchase dynamics -->
  <div class="card" style="margin-top:10px">
    <div class="section-title">📈 Analiza rozwoju sytuacji od zakupu</div>
    {traj_html}
  </div>

  <!-- Gauges + indicators -->
  <div class="card" style="margin-top:10px">
    <div class="section-title">📈 Wskaźniki techniczne</div>
    {_gauge_html(rsi, "RSI(14)", 0, 100, reverse=True) if rsi is not None else ""}
    {_gauge_html(stoch_k, "Stochastic %K", 0, 100, reverse=True) if stoch_k is not None else ""}
    {_gauge_html(bb_pos * 100, "Pozycja w paśmie BB", 0, 100, reverse=True)}
    <table style="width:100%;border-collapse:collapse;margin-top:10px">
      <thead>
        <tr style="background:#f8f9fa;border-bottom:2px solid #dee2e6">
          <th style="padding:5px 8px;text-align:left;font-size:10px;color:#6c757d;font-weight:500">Wskaźnik</th>
          <th style="padding:5px 8px;text-align:left;font-size:10px;color:#6c757d;font-weight:500">Wartość</th>
          <th style="padding:5px 8px;text-align:left;font-size:10px;color:#6c757d;font-weight:500">Sygnał</th>
        </tr>
      </thead>
      <tbody>
        {ind_row("RSI(14)", _fmt(rsi, 1), rsi_sig, rsi_col)}
        {ind_row("MACD Hist", _fmt(data.get('macd_hist'), 3), macd_sig2, macd_col)}
        {ind_row("Stoch %K/%D", f"{_fmt(stoch_k,1)} / {_fmt(std,1)}", st_sig, st_col)}
        {ind_row("Williams %R", _fmt(data.get('williams_r'), 1), "", "#6c757d")}
        {ind_row("ROC(20)", _fmt(data.get('roc20'), 2, "%"), "", "#6c757d")}
        {ind_row("SMA50", _fmt(sma50), sma50_sig, sma50_col)}
        {ind_row("SMA200", _fmt(sma200), sma200_sig, sma200_col)}
        {ind_row("BB Lower/Mid/Upper", f"{_fmt(data.get('bb_lower'))} / {_fmt(data.get('bb_mid'))} / {_fmt(data.get('bb_upper'))}", "", "#6c757d")}
        {ind_row("ATR(14)", f"{_fmt(data.get('atr14'))} ({_fmt(data.get('atr_pct'), 1)}%)", "Dzienna zmienność", "#6c757d")}
        {ind_row("52W High/Low", f"{_fmt(data.get('high_52w'))} / {_fmt(data.get('low_52w'))}", f"-{data.get('drawdown_pct','?')}% od szczytu", "#dc2626" if (data.get('drawdown_pct') or 0) > 20 else "#6c757d")}
      </tbody>
    </table>
  </div>

  <!-- Stop loss -->
  {stop_html}

  <!-- Fundamentals -->
  <div class="card" style="margin-top:10px">
    <div class="section-title">🏢 Dane spółki</div>
    {fund_rows}
  </div>

    <!-- Recommendation reasons -->
    <div class="card" style="margin-top:10px">
        <div class="section-title">🧠 Analiza — co robić z tą pozycją?</div>
        {reasons_html if reasons_html else '<div style="color:#6c757d;font-size:11px">Brak danych do analizy.</div>'}
    </div>
</div>
</div>

</div>
</body>
</html>"""
    return page_html


def _build_history_chart_html(data: dict) -> str:
                chart = data.get("chart", {})
                dates = chart.get("dates", [])
                total_pnl = chart.get("total_pnl", [])
                open_pnl = chart.get("open_pnl", [])
                realized_pnl = chart.get("realized_pnl", [])
                close_markers = chart.get("close_markers", [])
                chart_currency = chart.get("currency") or "USD"

                fig = go.Figure()
                fig.add_trace(go.Scatter(
                                x=dates,
                                y=realized_pnl,
                                mode="lines",
                                name="Zrealizowany wynik",
                                line=dict(color="#64748b", width=2, dash="dot"),
                                hovertemplate=f"%{{y:.2f}} {chart_currency}<extra>Zrealizowany</extra>",
                ))
                fig.add_trace(go.Scatter(
                                x=dates,
                                y=open_pnl,
                                mode="lines",
                                name="Otwarte pozycje",
                                line=dict(color="#2563eb", width=2),
                                hovertemplate=f"%{{y:.2f}} {chart_currency}<extra>Otwarte</extra>",
                ))
                fig.add_trace(go.Scatter(
                                x=dates,
                                y=total_pnl,
                                mode="lines",
                                name="Łączny wynik",
                                line=dict(color="#f97316", width=4),
                                fill="tozeroy",
                                fillcolor="rgba(249,115,22,0.12)",
                                hovertemplate=f"%{{y:.2f}} {chart_currency}<extra>Łączny wynik</extra>",
                ))

                if close_markers:
                                fig.add_trace(go.Scatter(
                                                x=[item.get("date") for item in close_markers],
                                                y=[item.get("value") for item in close_markers],
                                                mode="markers",
                                                name="Zamknięcia",
                                                marker=dict(size=9, color="#16a34a", line=dict(color="#ffffff", width=1.5)),
                                                text=[item.get("ticker", "") for item in close_markers],
                                                customdata=[[item.get("ticker", ""), item.get("pnl_abs", 0)] for item in close_markers],
                                                hovertemplate=f"%{{customdata[0]}}<br>Zrealizowane P/L: %{{customdata[1]:.2f}} {chart_currency}<br>Łączny wynik: %{{y:.2f}} {chart_currency}<extra></extra>",
                                ))

                fig.add_hline(y=0, line_color="#cbd5e1", line_width=1, line_dash="dot")
                fig.update_layout(
                                paper_bgcolor="#ffffff",
                                plot_bgcolor="#fffdf8",
                                font=dict(color="#1f2937", family="Helvetica Neue, Arial, sans-serif", size=11),
                                margin=dict(l=18, r=18, t=30, b=18),
                                height=430,
                                hovermode="x unified",
                                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
                )
                fig.update_xaxes(showgrid=False, fixedrange=True)
                axis_label = "Wynik nominalny"
                if chart.get("currency"):
                                axis_label += f" ({chart.get('currency')})"
                fig.update_yaxes(showgrid=True, gridcolor="#f1f5f9", zeroline=False, title_text=axis_label, fixedrange=True)
                return fig.to_html(full_html=False, include_plotlyjs=True, config={"displayModeBar": False, "scrollZoom": False, "doubleClick": False, "showTips": False})


def _history_currency_breakdown_html(values: dict) -> str:
                rows = []
                for currency, value in sorted((values or {}).items()):
                                amount = float(value or 0.0)
                                rows.append(
                                                f"<div style='font-size:10px;color:{_col_pnl(amount)};margin-top:4px'>{_fmt_money(amount, signed=True)} {html.escape(str(currency))}</div>"
                                )
                return "".join(rows) or "<div style='font-size:10px;color:#6b7280;margin-top:4px'>—</div>"


def _build_history_html(data: dict) -> str:
                summary = data.get("summary", {})
                multi_currency = bool(summary.get("multi_currency"))
                snapshot = (data.get("snapshot_time") or "")[:16].replace("T", " ")
                chart_html = _build_history_chart_html(data)
                currencies_text = ", ".join(summary.get("currencies") or []) or "—"
                base_currency = html.escape(str(summary.get("base_currency") or "USD"))
                open_summary_html = f"<div style='font-size:10px;color:{_col_pnl(summary.get('open_pnl_abs'))};margin-top:4px'>{_fmt_money(summary.get('open_pnl_abs'), signed=True)} {base_currency}</div>"
                closed_summary_html = f"<div style='font-size:10px;color:{_col_pnl(summary.get('closed_pnl_abs'))};margin-top:4px'>{_fmt_money(summary.get('closed_pnl_abs'), signed=True)} {base_currency}</div>"
                total_summary_html = f"<div style='font-size:10px;color:{_col_pnl(summary.get('total_pnl_abs'))};margin-top:4px'>{_fmt_money(summary.get('total_pnl_abs'), signed=True)} {base_currency}</div>"

                notes_html = "".join(
                                f"<li style='margin-left:18px;margin-top:6px;color:#4b5563'>{note}</li>" for note in data.get("notes", [])
                )

                open_rows_html = ""
                for row in data.get("open_positions", []):
                                open_rows_html += f"""
                                <tr>
                                        <td style="padding:8px 6px;font-weight:700;color:#111827">{row.get('ticker', '—')}</td>
                                        <td style="padding:8px 6px;text-align:right">{row.get('shares', 0):.4g}</td>
                                        <td style="padding:8px 6px;text-align:right">{row.get('purchase_date', '—')}</td>
                                        <td style="padding:8px 6px;text-align:right">{row.get('avg_price', 0):.2f}</td>
                                        <td style="padding:8px 6px;text-align:right">{row.get('final_price', 0):.2f}</td>
                                    <td style="padding:8px 6px;text-align:right">{row.get('currency', '—')}</td>
                                        <td style="padding:8px 6px;text-align:right">{row.get('held_days', 0)}</td>
                                    <td style="padding:8px 6px;text-align:right;color:{_col_pnl(row.get('pnl_abs'))}">{_fmt_money(row.get('pnl_abs', 0), signed=True)} {base_currency}</td>
                                        <td style="padding:8px 6px;text-align:right;color:{_col_pnl(row.get('pnl_pct'))}">{row.get('pnl_pct', 0):+.2f}%</td>
                                </tr>"""
                if not open_rows_html:
                                open_rows_html = '<tr><td colspan="9" style="padding:18px 6px;text-align:center;color:#6b7280">Brak otwartych pozycji.</td></tr>'

                closed_rows_html = ""
                for row in data.get("closed_positions", []):
                                closed_rows_html += f"""
                                <tr>
                                        <td style="padding:8px 6px;font-weight:700;color:#111827">{row.get('ticker', '—')}</td>
                                        <td style="padding:8px 6px;text-align:right">{row.get('shares', 0):.4g}</td>
                                        <td style="padding:8px 6px;text-align:right">{row.get('purchase_date', '—')}</td>
                                        <td style="padding:8px 6px;text-align:right">{row.get('close_date', '—')}</td>
                                        <td style="padding:8px 6px;text-align:right">{row.get('avg_price', 0):.2f}</td>
                                        <td style="padding:8px 6px;text-align:right">{row.get('final_price', 0):.2f}</td>
                                        <td style="padding:8px 6px;text-align:right">{row.get('currency', '—')}</td>
                                        <td style="padding:8px 6px;text-align:right">{row.get('held_days', 0)}</td>
                                        <td style="padding:8px 6px;text-align:right;color:{_col_pnl(row.get('pnl_abs'))}">{_fmt_money(row.get('pnl_abs', 0), signed=True)} {base_currency}</td>
                                        <td style="padding:8px 6px;text-align:right;color:{_col_pnl(row.get('pnl_pct'))}">{row.get('pnl_pct', 0):+.2f}%</td>
                                </tr>"""
                if not closed_rows_html:
                                    closed_rows_html = '<tr><td colspan="10" style="padding:18px 6px;text-align:center;color:#6b7280">Brak zapisanych zamkniętych pozycji.</td></tr>'

                return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        * {{ box-sizing:border-box }}
        body {{ margin:0;padding:0;background:#f8fafc;color:#111827;font-family:"Helvetica Neue",Arial,sans-serif }}
        .container {{ max-width:1320px;margin:0 auto;padding:18px }}
        .card {{ background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:14px }}
    </style>
</head>
<body>
    <div class="container">
        <div class="card" style="background:linear-gradient(180deg,#fffaf0 0%,#ffffff 100%)">
            <div style="display:flex;justify-content:space-between;gap:18px;align-items:flex-start;flex-wrap:wrap">
                <div>
                    <div style="font-size:26px;font-weight:900;color:#111827">Historia pozycji</div>
                    <div style="font-size:12px;color:#6b7280;margin-top:6px">Snapshot: {snapshot or '—'} · wynik i wykres są przeliczone do {base_currency} na bazie kursów FX D1</div>
                </div>
                <div style="display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:10px;flex:1;min-width:620px">
                    <div style="background:#ffffffcc;border-radius:12px;padding:12px 14px">
                        <div style="font-size:10px;color:#6b7280">Otwarte pozycje</div>
                        <div style="font-size:22px;font-weight:800;color:#111827">{summary.get('open_count', 0)}</div>
                        {open_summary_html}
                    </div>
                    <div style="background:#ffffffcc;border-radius:12px;padding:12px 14px">
                        <div style="font-size:10px;color:#6b7280">Zamknięte pozycje</div>
                        <div style="font-size:22px;font-weight:800;color:#111827">{summary.get('closed_count', 0)}</div>
                        {closed_summary_html}
                    </div>
                    <div style="background:#ffffffcc;border-radius:12px;padding:12px 14px">
                        <div style="font-size:10px;color:#6b7280">{('Waluty źródłowe' if multi_currency else 'Wygrane otwarte')}</div>
                        <div style="font-size:{('18px' if multi_currency else '22px')};font-weight:800;color:#111827">{(html.escape(currencies_text) if multi_currency else summary.get('open_winners', 0))}</div>
                        {("<div style='font-size:10px;color:#6b7280;margin-top:4px'>Do agregacji wszystko przeliczam na USD z D1 FX.</div>" if multi_currency else "")}
                    </div>
                    <div style="background:#ffffffcc;border-radius:12px;padding:12px 14px">
                        <div style="font-size:10px;color:#6b7280">Łączny wynik</div>
                        {total_summary_html}
                    </div>
                </div>
            </div>
            <ul style="margin:12px 0 0 0;padding:0">{notes_html}</ul>
        </div>

        <div class="card">{chart_html}</div>

        <div class="card">
            <div style="font-size:13px;font-weight:800;color:#374151;margin-bottom:12px">Pozycje otwarte</div>
            <table style="width:100%;border-collapse:collapse;font-size:11px">
                <thead>
                    <tr style="background:#f8fafc;border-bottom:1px solid #e5e7eb">
                        <th style="padding:8px 6px;text-align:left;color:#6b7280">Ticker</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">Liczba</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">Kupiono</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">Wejście</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">Aktualna</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">Waluta</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">Dni</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">P/L USD</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">P/L %</th>
                    </tr>
                </thead>
                <tbody>{open_rows_html}</tbody>
            </table>
        </div>

        <div class="card">
            <div style="font-size:13px;font-weight:800;color:#374151;margin-bottom:12px">Pozycje zamknięte</div>
            <table style="width:100%;border-collapse:collapse;font-size:11px">
                <thead>
                    <tr style="background:#f8fafc;border-bottom:1px solid #e5e7eb">
                        <th style="padding:8px 6px;text-align:left;color:#6b7280">Ticker</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">Liczba</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">Kupiono</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">Zamknięto</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">Wejście</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">Wyjście</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">Waluta</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">Dni</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">P/L USD</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">P/L %</th>
                    </tr>
                </thead>
                <tbody>{closed_rows_html}</tbody>
            </table>
        </div>
    </div>
</body>
</html>"""


def _signal_quality_status_meta(status: str) -> tuple[str, str, str]:
                mapping = {
                                "matched": ("zgodne", "#dcfce7", "#166534"),
                                "missed": ("rozjazd", "#fee2e2", "#b91c1c"),
                                "neutral": ("neutralne", "#fef3c7", "#92400e"),
                                "pending": ("w toku", "#e0f2fe", "#0369a1"),
                                "no_data": ("brak danych", "#e5e7eb", "#6b7280"),
                }
                return mapping.get(status or "", mapping["no_data"])


def _build_signal_quality_chart_html(position: dict) -> str:
                verification = position.get("verification") or {}
                actual_path = [float(value) for value in (position.get("actual_path_pct") or []) if value is not None]
                if not actual_path:
                                actual_path = [0.0]

                forecast_points = []
                for suffix in ("5d", "22d", "66d"):
                                row = verification.get(suffix) or {}
                                predicted = row.get("predicted_avg_return")
                                if predicted is None:
                                                continue
                                forecast_points.append({
                                                "bars": int(row.get("bars") or 0),
                                                "label": str(row.get("short") or suffix.upper()),
                                                "predicted": float(predicted),
                                                "actual": None if row.get("actual_return") is None else float(row.get("actual_return")),
                                                "status": str(row.get("status") or "no_data"),
                                })

                if len(actual_path) < 2 and not forecast_points:
                                return ""

                width = 332
                height = 128
                pad_left = 18
                pad_right = 18
                pad_top = 16
                pad_bottom = 24
                max_step = max(len(actual_path) - 1, max((point["bars"] for point in forecast_points), default=1), 1)

                all_values = list(actual_path)
                all_values.extend(point["predicted"] for point in forecast_points)
                all_values.extend(point["actual"] for point in forecast_points if point["actual"] is not None)
                min_value = min(all_values) if all_values else 0.0
                max_value = max(all_values) if all_values else 0.0
                if abs(max_value - min_value) < 1.0:
                                max_value += 0.6
                                min_value -= 0.6
                else:
                                padding = (max_value - min_value) * 0.18
                                max_value += padding
                                min_value -= padding

                def _coords(step: int, value: float) -> tuple[float, float]:
                                usable_width = width - pad_left - pad_right
                                usable_height = height - pad_top - pad_bottom
                                x = pad_left + (step / max_step) * usable_width
                                y = pad_top + (max_value - value) / (max_value - min_value) * usable_height
                                return x, y

                actual_points = [_coords(index, value) for index, value in enumerate(actual_path)]
                actual_polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in actual_points)
                actual_area = " ".join(
                                [f"{pad_left:.1f},{height - pad_bottom:.1f}"]
                                + [f"{x:.1f},{y:.1f}" for x, y in actual_points]
                                + [f"{actual_points[-1][0]:.1f},{height - pad_bottom:.1f}"]
                )

                predicted_points = [(0, 0.0)] + [(point["bars"], point["predicted"]) for point in forecast_points]
                predicted_polyline = " ".join(
                                f"{x:.1f},{y:.1f}" for x, y in (_coords(step, value) for step, value in predicted_points)
                )

                horizon_grid = "".join(
                                f"<line x1='{_coords(point['bars'], 0.0)[0]:.1f}' y1='{pad_top}' x2='{_coords(point['bars'], 0.0)[0]:.1f}' y2='{height - pad_bottom}' stroke='#e2e8f0' stroke-dasharray='3 3'/>"
                                for point in forecast_points
                )
                zero_y = _coords(0, 0.0)[1]

                markers_html = ""
                for point in forecast_points:
                                x_pred, y_pred = _coords(point["bars"], point["predicted"])
                                status_label, _, status_fg = _signal_quality_status_meta(point["status"])
                                markers_html += (
                                                f"<circle cx='{x_pred:.1f}' cy='{y_pred:.1f}' r='4.2' fill='#ffffff' stroke='#f97316' stroke-width='2'/>"
                                                f"<text x='{x_pred:.1f}' y='{height - 6}' text-anchor='middle' font-size='9' fill='#64748b'>{html.escape(point['label'])}</text>"
                                )
                                if point["actual"] is not None:
                                                _, _, actual_fg = _signal_quality_status_meta(point["status"])
                                                x_act, y_act = _coords(point["bars"], point["actual"])
                                                markers_html += (
                                                                f"<circle cx='{x_act:.1f}' cy='{y_act:.1f}' r='4.3' fill='{actual_fg}' stroke='#ffffff' stroke-width='1.4'/>"
                                                                f"<title>{html.escape(point['label'])}: {status_label}</title>"
                                                )

                current_x, current_y = actual_points[-1]
                current_label = actual_path[-1]

                return f"""
                <div style="background:linear-gradient(180deg,#fffdf7 0%,#ffffff 100%);border:1px solid #f1f5f9;border-radius:14px;padding:12px 12px 10px 12px">
                    <div style="display:flex;justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px">
                        <div style="font-size:11px;font-weight:800;color:#334155">Mini przebieg od zakupu</div>
                        <div style="display:flex;gap:10px;flex-wrap:wrap;font-size:10px;color:#64748b">
                            <span><span style="display:inline-block;width:8px;height:8px;border-radius:999px;background:#2563eb;margin-right:4px"></span>realny kurs</span>
                            <span><span style="display:inline-block;width:8px;height:8px;border-radius:999px;border:2px solid #f97316;background:#fff;margin-right:4px"></span>prognoza</span>
                            <span><span style="display:inline-block;width:8px;height:8px;border-radius:999px;background:#16a34a;margin-right:4px"></span>weryfikacja</span>
                        </div>
                    </div>
                    <svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img" aria-label="Przebieg pozycji od zakupu">
                        <line x1="{pad_left}" y1="{zero_y:.1f}" x2="{width - pad_right}" y2="{zero_y:.1f}" stroke="#cbd5e1" stroke-width="1" stroke-dasharray="4 4"/>
                        {horizon_grid}
                        <polygon points="{actual_area}" fill="rgba(37,99,235,0.10)"/>
                        <polyline points="{predicted_polyline}" fill="none" stroke="#f97316" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="5 4"/>
                        <polyline points="{actual_polyline}" fill="none" stroke="#2563eb" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"/>
                        {markers_html}
                        <circle cx="{current_x:.1f}" cy="{current_y:.1f}" r="4.5" fill="#2563eb" stroke="#ffffff" stroke-width="1.6"/>
                        <text x="{current_x:.1f}" y="{max(11.0, current_y - 9):.1f}" text-anchor="middle" font-size="9" fill="#1d4ed8">{current_label:+.1f}%</text>
                    </svg>
                </div>"""


def _build_signal_quality_html(data: dict) -> str:
                summary = data.get("summary") or {}
                notes = data.get("notes") or []
                positions = data.get("positions") or []
                generated_at = (data.get("generated_at") or "")[:16].replace("T", " ")

                if not positions:
                                return _build_placeholder_html(
                                                "Brak danych do weryfikacji",
                                                "Nie udało się odtworzyć pozycji z wystarczającą historią cen, więc ta zakładka nie ma jeszcze czego porównać.",
                                )

                notes_html = "".join(
                                f"<li style='margin-left:18px;margin-top:6px;color:#475569'>{html.escape(note)}</li>"
                                for note in notes
                )

                horizon_cards_html = ""
                for suffix in ("5d", "22d", "66d"):
                                horizon = (summary.get("horizons") or {}).get(suffix) or {}
                                accuracy = horizon.get("accuracy")
                                accuracy_color = "#15803d" if (accuracy or 0) >= 60 else "#c2410c" if (accuracy or 0) >= 40 else "#b91c1c"
                                accuracy_text = f"{accuracy:.1f}%" if accuracy is not None else "—"
                                horizon_cards_html += f"""
                                <div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:14px;padding:14px 16px">
                                    <div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start">
                                        <div>
                                            <div style="font-size:11px;color:#64748b">{html.escape(str(horizon.get('label') or suffix.upper()))}</div>
                                            <div style="font-size:24px;font-weight:900;color:{accuracy_color};margin-top:2px">{accuracy_text}</div>
                                            <div style="font-size:10px;color:#94a3b8;margin-top:3px">skuteczność tam, gdzie okno już się domknęło</div>
                                        </div>
                                        <div style="text-align:right;font-size:10px;color:#64748b;line-height:1.55">
                                            <div>zgodne: <b style="color:#166534">{horizon.get('matched', 0)}</b></div>
                                            <div>rozjazd: <b style="color:#b91c1c">{horizon.get('missed', 0)}</b></div>
                                            <div>w toku: <b style="color:#0369a1">{horizon.get('pending', 0)}</b></div>
                                        </div>
                                    </div>
                                </div>"""

                positions_html = ""
                for position in positions:
                                signal = position.get("signal") or {}
                                verification = position.get("verification") or {}
                                currency = html.escape(str(position.get("currency") or "USD"))
                                pnl_pct = float(position.get("current_pnl_pct") or 0.0)
                                pnl_abs = float(position.get("current_pnl_abs") or 0.0)
                                pnl_label = "na plusie" if pnl_pct > 0.01 else "na minusie" if pnl_pct < -0.01 else "blisko zera"
                                signal_available = bool(signal.get("available"))
                                signal_low_sample = bool(signal.get("low_sample"))
                                signal_samples = int(signal.get("samples") or 0)
                                signal_win_rate = signal.get("win_rate_pct")
                                signal_expectation = signal.get("expectation_pct")
                                signal_win_rate_text = f"{float(signal_win_rate):.0f}%" if signal_win_rate is not None else "—"
                                signal_expectation_text = f"{float(signal_expectation):+.2f}%" if signal_expectation is not None else "—"
                                signal_win_rate_sub = "trafność podobnych wejść"
                                signal_expectation_sub = f"dni w pozycji: {int(position.get('days_held') or 0)}"

                                if signal_available:
                                                signal_label_map = {
                                                                "buy": ("Wejście: przewaga wzrostu", "#dcfce7", "#166534"),
                                                                "hold": ("Wejście: bez wyraźnej przewagi", "#eff6ff", "#1d4ed8"),
                                                                "sell": ("Wejście: słaby układ", "#fee2e2", "#b91c1c"),
                                                }
                                                signal_label, signal_bg, signal_fg = signal_label_map.get(
                                                                str(signal.get("label") or "").lower(),
                                                                ("Wejście: niejednoznaczne", "#f3f4f6", "#4b5563"),
                                                )
                                                if signal_low_sample and signal_samples > 0:
                                                                signal_label = f"{signal_label} · mała próba"
                                                                signal_win_rate_sub = f"tylko {signal_samples} podobne wejścia"
                                elif signal_samples > 0:
                                                signal_label, signal_bg, signal_fg = ("Za mało podobnych wejść", "#f8fafc", "#475569")
                                                signal_win_rate_text = str(signal_samples)
                                                signal_win_rate_sub = "liczba podobnych wejść"
                                                signal_expectation_text = "za mało danych"
                                else:
                                                signal_label, signal_bg, signal_fg = ("Brak danych historycznych", "#f8fafc", "#475569")
                                                signal_expectation_text = "brak danych"

                                verification_html = ""
                                for suffix in ("5d", "22d", "66d"):
                                                row = verification.get(suffix) or {}
                                                status_label, status_bg, status_fg = _signal_quality_status_meta(str(row.get("status") or "no_data"))
                                                predicted_win_rate = row.get("predicted_win_rate")
                                                predicted_avg_return = row.get("predicted_avg_return")
                                                actual_return = row.get("actual_return")
                                                predicted_win_rate_text = f"{predicted_win_rate:.0f}%" if predicted_win_rate is not None else "—"
                                                predicted_avg_text = f"{float(predicted_avg_return):+.2f}%" if predicted_avg_return is not None else "—"
                                                actual_return_text = f"{float(actual_return):+.2f}%" if actual_return is not None else "w toku"
                                                samples = row.get("samples")
                                                samples_text = str(int(samples)) if samples is not None else "—"
                                                verification_html += f"""
                                                <div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;padding:12px 12px 11px 12px">
                                                    <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start;flex-wrap:wrap">
                                                        <div>
                                                            <div style="font-size:11px;font-weight:800;color:#334155">{html.escape(str(row.get('label') or suffix.upper()))}</div>
                                                            <div style="font-size:10px;color:#64748b;margin-top:2px">probka podobnych wejść: {samples_text}</div>
                                                        </div>
                                                        <div style="background:{status_bg};color:{status_fg};border-radius:999px;padding:4px 10px;font-size:10px;font-weight:800">{status_label}</div>
                                                    </div>
                                                    <div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:10px">
                                                        <div style="background:#f8fafc;border-radius:10px;padding:9px 10px">
                                                            <div style="font-size:10px;color:#64748b">szansa wzrostu</div>
                                                            <div style="font-size:15px;font-weight:800;color:#111827;margin-top:2px">{predicted_win_rate_text}</div>
                                                        </div>
                                                        <div style="background:#f8fafc;border-radius:10px;padding:9px 10px">
                                                            <div style="font-size:10px;color:#64748b">prognoza średnia</div>
                                                            <div style="font-size:15px;font-weight:800;color:{_col_pnl(predicted_avg_return)};margin-top:2px">{predicted_avg_text}</div>
                                                        </div>
                                                        <div style="background:#f8fafc;border-radius:10px;padding:9px 10px">
                                                            <div style="font-size:10px;color:#64748b">faktyczny wynik</div>
                                                            <div style="font-size:15px;font-weight:800;color:{_col_pnl(actual_return)};margin-top:2px">{actual_return_text}</div>
                                                        </div>
                                                    </div>
                                                </div>"""

                                chart_html = _build_signal_quality_chart_html(position)
                                positions_html += f"""
                                <div class="position-card">
                                    <div style="display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap">
                                        <div>
                                            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                                                <div style="font-size:22px;font-weight:900;color:#0f172a">{html.escape(str(position.get('ticker') or '—'))}</div>
                                                <div style="background:{signal_bg};color:{signal_fg};border-radius:999px;padding:4px 10px;font-size:10px;font-weight:800">{signal_label}</div>
                                            </div>
                                            <div style="font-size:12px;color:#64748b;margin-top:5px">{html.escape(str(position.get('name') or position.get('ticker') or '—'))}</div>
                                        </div>
                                        <div style="text-align:right">
                                            <div style="font-size:10px;color:#64748b">Aktualny stan pozycji</div>
                                            <div style="font-size:24px;font-weight:900;color:{_col_pnl(pnl_pct)}">{pnl_pct:+.2f}%</div>
                                            <div style="font-size:11px;color:{_col_pnl(pnl_abs)}">{_fmt_money(pnl_abs, signed=True)} {currency} · {pnl_label}</div>
                                        </div>
                                    </div>

                                    <div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:14px">
                                        <div class="metric-box">
                                            <div class="metric-label">Kupiono</div>
                                            <div class="metric-value">{html.escape(str(position.get('purchase_date') or '—'))}</div>
                                            <div class="metric-sub">{float(position.get('shares') or 0.0):.4g} szt.</div>
                                        </div>
                                        <div class="metric-box">
                                            <div class="metric-label">Wejście / teraz</div>
                                            <div class="metric-value">{float(position.get('avg_price') or 0.0):.2f} / {float(position.get('current_price') or 0.0):.2f}</div>
                                            <div class="metric-sub">{currency}</div>
                                        </div>
                                        <div class="metric-box">
                                            <div class="metric-label">Odtworzony sygnał wejścia</div>
                                            <div class="metric-value" style="color:{signal_fg}">{signal_win_rate_text}</div>
                                            <div class="metric-sub">{signal_win_rate_sub}</div>
                                        </div>
                                        <div class="metric-box">
                                            <div class="metric-label">Oczekiwany ruch</div>
                                            <div class="metric-value" style="color:{_col_pnl(signal_expectation)}">{signal_expectation_text}</div>
                                            <div class="metric-sub">{signal_expectation_sub}</div>
                                        </div>
                                    </div>

                                    {chart_html}

                                    <div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:12px">
                                        {verification_html}
                                    </div>
                                </div>"""

                return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        * {{ box-sizing:border-box }}
        body {{ margin:0;padding:0;background:#f8fafc;color:#111827;font-family:"Helvetica Neue",Arial,sans-serif }}
        .container {{ max-width:1280px;margin:0 auto;padding:18px }}
        .hero {{ background:linear-gradient(135deg,#fff7ed 0%,#ffffff 45%,#eff6ff 100%);border:1px solid #e5e7eb;border-radius:18px;padding:18px;margin-bottom:14px }}
        .summary-grid {{ display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:14px }}
        .summary-box {{ background:#ffffffcc;border:1px solid #e5e7eb;border-radius:14px;padding:12px 14px }}
        .summary-label {{ font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.5px }}
        .summary-value {{ font-size:24px;font-weight:900;color:#0f172a;margin-top:4px }}
        .card-grid {{ display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-bottom:14px }}
        .position-card {{ background:#ffffff;border:1px solid #e5e7eb;border-radius:18px;padding:16px;margin-bottom:14px;box-shadow:0 10px 24px rgba(15,23,42,0.04) }}
        .metric-box {{ background:#f8fafc;border-radius:12px;padding:11px 12px }}
        .metric-label {{ font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.45px }}
        .metric-value {{ font-size:16px;font-weight:800;color:#111827;margin-top:4px }}
        .metric-sub {{ font-size:10px;color:#64748b;margin-top:3px }}
        @media (max-width: 1100px) {{
            .summary-grid, .card-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
        }}
        @media (max-width: 760px) {{
            .summary-grid, .card-grid {{ grid-template-columns:1fr; }}
            .position-card > div[style*='grid-template-columns:repeat(4'] {{ grid-template-columns:repeat(2,minmax(0,1fr)) !important; }}
            .position-card > div[style*='grid-template-columns:repeat(3'] {{ grid-template-columns:1fr !important; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="hero">
            <div style="display:flex;justify-content:space-between;gap:18px;align-items:flex-start;flex-wrap:wrap">
                <div>
                    <div style="font-size:28px;font-weight:900;color:#0f172a">Weryfikacja jakości sygnałów</div>
                    <div style="font-size:12px;color:#64748b;line-height:1.7;margin-top:6px">Tu widać, co system "myślał" w dniu zakupu i czy rynek później to potwierdził. Prognoza jest rekonstruowana z historycznego setupu, a wynik liczony na bazie realnej ścieżki pozycji od wejścia.</div>
                </div>
                <div style="text-align:right;font-size:11px;color:#64748b;line-height:1.65">
                    <div>Aktualizacja: <b style="color:#0f172a">{generated_at or '—'}</b></div>
                    <div>Pozycje: <b style="color:#0f172a">{summary.get('positions', 0)}</b></div>
                    <div>Interpretacja: zgodne = prognoza i rzeczywisty kierunek były spójne</div>
                </div>
            </div>
            <div class="summary-grid">
                <div class="summary-box">
                    <div class="summary-label">Na plusie teraz</div>
                    <div class="summary-value" style="color:#166534">{summary.get('current_winners', 0)}</div>
                </div>
                <div class="summary-box">
                    <div class="summary-label">Na minusie teraz</div>
                    <div class="summary-value" style="color:#b91c1c">{summary.get('current_losers', 0)}</div>
                </div>
                <div class="summary-box">
                    <div class="summary-label">Płasko</div>
                    <div class="summary-value" style="color:#475569">{summary.get('current_flat', 0)}</div>
                </div>
                <div class="summary-box">
                    <div class="summary-label">Notatki do interpretacji</div>
                    <div style="font-size:11px;color:#475569;line-height:1.65;margin-top:6px">wynik 1T/1M/3M dotyczy jakości sygnału przy wejściu, a nie dzisiejszej atrakcyjności pozycji</div>
                </div>
            </div>
            <ul style="margin:12px 0 0 0;padding:0">{notes_html}</ul>
        </div>

        <div class="card-grid">{horizon_cards_html}</div>

        {positions_html}
    </div>
</body>
</html>"""


def _build_placeholder_html(title: str, message: str) -> str:
                return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;background:#f8fafc;color:#6b7280;font-family:Helvetica Neue,Arial,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh">
    <div style="text-align:center;max-width:560px;padding:24px">
        <div style="font-size:28px;margin-bottom:14px">📘</div>
        <div style="font-size:22px;font-weight:800;color:#111827">{title}</div>
        <div style="font-size:13px;line-height:1.7;margin-top:10px">{message}</div>
    </div>
</body>
</html>"""


def _build_suggestions_html(data: dict) -> str:
                                overview = data.get("overview") or []
                                holdings = data.get("holdings") or []
                                replacements = data.get("replacements") or []
                                opportunities = data.get("opportunities") or []
                                coverage = data.get("coverage") or {}
                                scan = data.get("opportunity_scan") or {}
                                generated_at = data.get("generated_at") or "—"

                                replacement_map = {row.get("sell_ticker"): row for row in replacements}
                                actionable = [
                                                                row for row in holdings
                                                                if row.get("action_label") in {"Wyjście / szybka wymiana", "Rozważ wymianę", "Można wzmacniać"}
                                ]
                                watch_only = [row for row in holdings if row.get("action_label") == "Trzymaj / obserwuj"]
                                shown_holdings = actionable + watch_only[:3]
                                hidden_watch = max(0, len(watch_only) - 3)

                                priority_meta = {
                                                                "high": ("#fee2e2", "#b91c1c"),
                                                                "medium": ("#fff7ed", "#c2410c"),
                                                                "low": ("#ecfeff", "#0f766e"),
                                }
                                recommendation_meta = {
                                                                "STOP LOSS": ("#fee2e2", "#b91c1c"),
                                                                "SPRZEDAJ": ("#fee2e2", "#b91c1c"),
                                                                "ROZWAŻ SPRZEDAŻ": ("#fff7ed", "#c2410c"),
                                                                "TRZYMAJ": ("#eff6ff", "#1d4ed8"),
                                                                "DOKUP WIĘCEJ": ("#f0fdf4", "#15803d"),
                                }

                                def _history_text(win_rate, median):
                                                                if win_rate is None and median is None:
                                                                                                return "brak"
                                                                left = f"{win_rate:.0f}%" if win_rate is not None else "—"
                                                                right = f"{median:+.1f}%" if median is not None else "—"
                                                                return f"{left} / {right}"

                                overview_html = "".join(
                                                                f"<div style='background:#ffffff;border:1px solid #dbeafe;border-radius:12px;padding:12px 14px;font-size:12px;color:#334155;line-height:1.6'>{text}</div>"
                                                                for text in overview
                                ) or "<div style='background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;padding:12px 14px;font-size:12px;color:#64748b'>Brak dodatkowych wniosków.</div>"

                                replacements_html = "".join(
                                                                f"""
                                <div style="background:linear-gradient(135deg,#fff7ed,#ffffff);border:1px solid #fed7aa;border-radius:14px;padding:14px 16px;margin-top:12px">
                                        <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap">
                                                <div>
                                                        <div style="font-size:11px;color:#9a3412;text-transform:uppercase;letter-spacing:.5px">Rotacja kapitału</div>
                                                        <div style="font-size:18px;font-weight:800;color:#111827;margin-top:2px">{row.get('sell_ticker', '—')} → {row.get('buy_ticker', '—')}</div>
                                                        <div style="font-size:11px;color:#475569;margin-top:6px;line-height:1.6">{row.get('reason', '')}</div>
                                                </div>
                                                <div style="text-align:right">
                                                        <div style="font-size:10px;color:#9a3412">Kandydat ze skanera</div>
                                                        <div style="font-size:17px;font-weight:800;color:#15803d">{row.get('buy_ticker', '—')}</div>
                                                        <div style="font-size:11px;color:#6b7280">{row.get('buy_currency', '—')}</div>
                                                </div>
                                        </div>
                                        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px">
                                                <div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;padding:10px 12px">
                                                        <div style="font-size:10px;color:#6b7280">Oddawana pozycja</div>
                                                        <div style="font-size:13px;font-weight:700;color:#111827;margin-top:4px">{row.get('sell_ticker', '—')} · {row.get('sell_action', '—')}</div>
                                                        <div style="font-size:11px;color:#475569;line-height:1.55;margin-top:6px">{row.get('sell_summary', '')}</div>
                                                </div>
                                                <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:10px 12px">
                                                        <div style="font-size:10px;color:#166534">Nowy setup</div>
                                                        <div style="font-size:13px;font-weight:700;color:#166534;margin-top:4px">{row.get('buy_ticker', '—')}</div>
                                                        <div style="font-size:11px;color:#166534;line-height:1.55;margin-top:6px">{row.get('buy_summary', '')}</div>
                                                </div>
                                        </div>
                                </div>"""
                                                                for row in replacements
                                ) or "<div style='background:#ffffff;border:1px dashed #cbd5e1;border-radius:12px;padding:14px 16px;font-size:12px;color:#64748b;margin-top:12px'>Brak wyraźnej zamiany kapitału do zasugerowania na podstawie obecnych danych.</div>"

                                holdings_html = ""
                                for row in shown_holdings:
                                                                history = row.get("history") or {}
                                                                replacement = replacement_map.get(row.get("ticker"))
                                                                priority_bg, priority_fg = priority_meta.get(row.get("priority"), ("#f8fafc", "#334155"))
                                                                pnl_pct = row.get("pnl_pct", 0.0)
                                                                entry_text = _history_text(history.get("entry_win_rate_1m"), history.get("entry_median_1m"))
                                                                traj_text = _history_text(history.get("trajectory_win_rate_1m"), history.get("trajectory_median_1m"))
                                                                confidence_score = row.get("confidence_score", 0.0)
                                                                confidence_label = row.get("confidence_label", "Niska")
                                                                confidence_color = "#15803d" if confidence_score >= 75 else "#c2410c" if confidence_score >= 45 else "#b91c1c"
                                                                display_action = row.get("display_action") or row.get("action_label") or "—"
                                                                display_action_note = row.get("display_action_note") or ""
                                                                positives_html = "".join(
                                                                                                f"<div style='background:#ecfdf5;border:1px solid #bbf7d0;border-radius:999px;padding:4px 9px;font-size:10px;color:#166534'>{text}</div>"
                                                                                                for text in (row.get("positives") or [])[:3]
                                                                )
                                                                watchouts_html = "".join(
                                                                                                f"<div style='background:#fff7ed;border:1px solid #fdba74;border-radius:999px;padding:4px 9px;font-size:10px;color:#9a3412'>{text}</div>"
                                                                                                for text in (row.get("watchouts") or [])[:3]
                                                                )
                                                                holdings_html += f"""
                                <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:14px;padding:14px 16px;margin-top:12px">
                                        <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap">
                                                <div>
                                                        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                                                                <div style="font-size:19px;font-weight:800;color:#111827">{row.get('ticker', '—')}</div>
                                                <div style="background:{priority_bg};color:{priority_fg};border-radius:999px;padding:4px 10px;font-size:10px;font-weight:700">{display_action}</div>
                                                            <div style="background:{confidence_color}18;color:{confidence_color};border-radius:999px;padding:4px 10px;font-size:10px;font-weight:700">Pewność: {confidence_label}</div>
                                                        </div>
                                                        <div style="font-size:11px;color:#64748b;margin-top:5px">{row.get('name', row.get('ticker', '—'))} · {row.get('currency', '—')}</div>
                                            {f"<div style='font-size:11px;color:#64748b;line-height:1.6;margin-top:7px'>Dlaczego tak: {display_action_note}</div>" if display_action_note else ""}
                                                </div>
                                                <div style="text-align:right">
                                                        <div style="font-size:10px;color:#6b7280">P/L od zakupu</div>
                                                        <div style="font-size:18px;font-weight:800;color:{_col_pnl(pnl_pct)}">{pnl_pct:+.1f}%</div>
                                                        <div style="font-size:11px;color:{_col_pnl(row.get('pnl_abs', 0.0))}">{_fmt_money(row.get('pnl_abs', 0.0), signed=True)} {row.get('currency', '')}</div>
                                                </div>
                                        </div>
                                        <div style="font-size:12px;color:#334155;line-height:1.65;margin-top:10px">{row.get('summary', '')}</div>
                                        <div style="display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-top:12px">
                                                <div style="background:#f8fafc;border-radius:10px;padding:10px 12px">
                                                        <div style="font-size:10px;color:#6b7280">Podobne setupy 1M</div>
                                                        <div style="font-size:15px;font-weight:800;color:#111827;margin-top:3px">{entry_text}</div>
                                                        <div style="font-size:10px;color:#64748b;margin-top:3px">trafność / mediana ruchu</div>
                                                </div>
                                                <div style="background:#f8fafc;border-radius:10px;padding:10px 12px">
                                                        <div style="font-size:10px;color:#6b7280">Rozwój od zakupu 1M</div>
                                                        <div style="font-size:15px;font-weight:800;color:#111827;margin-top:3px">{traj_text}</div>
                                                        <div style="font-size:10px;color:#64748b;margin-top:3px">na podobnych ścieżkach</div>
                                                </div>
                                                <div style="background:#f8fafc;border-radius:10px;padding:10px 12px">
                                                    <div style="font-size:10px;color:#6b7280">Ocena łączna</div>
                                                        <div style="font-size:15px;font-weight:800;color:{priority_fg};margin-top:3px">{row.get('quality_score', 0.0):+.1f}</div>
                                                        <div style="font-size:10px;color:#64748b;margin-top:3px">łączna ocena setupu</div>
                                                </div>
                                                <div style="background:#f8fafc;border-radius:10px;padding:10px 12px">
                                                    <div style="font-size:10px;color:#6b7280">Jakość setupu</div>
                                                    <div style="font-size:15px;font-weight:800;color:#111827;margin-top:3px">{row.get('setup_quality_score', 0.0):.0f}/100</div>
                                                    <div style="font-size:10px;color:#64748b;margin-top:3px">technika + historia wejść</div>
                                                </div>
                                                <div style="background:#f8fafc;border-radius:10px;padding:10px 12px">
                                                    <div style="font-size:10px;color:#6b7280">Jakość spółki</div>
                                                    <div style="font-size:15px;font-weight:800;color:#111827;margin-top:3px">{row.get('company_quality_score', 0.0):.0f}/100</div>
                                                    <div style="font-size:10px;color:#64748b;margin-top:3px">fundamenty / wydarzenia</div>
                                                </div>
                                        </div>
                                        <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:12px">{positives_html or ''}</div>
                                        <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px">{watchouts_html or ''}</div>
                                            <div style="background:{confidence_color}10;border:1px solid {confidence_color}33;border-radius:10px;padding:10px 12px;margin-top:12px">
                                                <div style="font-size:10px;color:{confidence_color};text-transform:uppercase;letter-spacing:.5px">Pewność sygnału</div>
                                                <div style="font-size:12px;color:#334155;line-height:1.6;margin-top:6px">{row.get('confidence_note', '')}</div>
                                            </div>
                                        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px;margin-top:12px">
                                                <div style="font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.5px">Wniosek</div>
                                                <div style="font-size:12px;color:#334155;line-height:1.6;margin-top:6px">{row.get('action_note', '')}</div>
                                        </div>
                                        {f"<div style='background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;padding:10px 12px;margin-top:12px'><div style='font-size:10px;color:#9a3412;text-transform:uppercase;letter-spacing:.5px'>Sugestia zamiany</div><div style='font-size:12px;color:#7c2d12;line-height:1.6;margin-top:6px'>Rozważ przeniesienie części kapitału z <b>{row.get('ticker', '—')}</b> do <b>{replacement.get('buy_ticker', '—')}</b>. {replacement.get('reason', '')}</div></div>" if replacement else ''}
                                </div>"""

                                opportunities_html = ""
                                if not scan.get("loaded"):
                                                                opportunities_html = "<div style='background:#ffffff;border:1px dashed #cbd5e1;border-radius:12px;padding:14px 16px;font-size:12px;color:#64748b;margin-top:12px'>Uruchom skan okazji z tej zakładki, a ta sekcja pokaże najlepsze setupy spoza obecnego portfela i ewentualne pomysły na zamiany.</div>"
                                elif opportunities:
                                                                opportunities_html = "".join(
                                                                                                f"""
                                <div style="background:#ffffff;border:1px solid #d1fae5;border-radius:12px;padding:12px 14px;margin-top:10px">
                                        <div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap">
                                                <div>
                                            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                                                <div style="font-size:17px;font-weight:800;color:#111827">{row.get('ticker', '—')}</div>
                                                <div style="background:{'#15803d18' if row.get('confidence_score', 0.0) >= 75 else '#c2410c18' if row.get('confidence_score', 0.0) >= 45 else '#b91c1c18'};color:{'#15803d' if row.get('confidence_score', 0.0) >= 75 else '#c2410c' if row.get('confidence_score', 0.0) >= 45 else '#b91c1c'};border-radius:999px;padding:4px 9px;font-size:10px;font-weight:700">Pewność: {row.get('confidence_label', 'Niska')}</div>
                                            </div>
                                                        <div style="font-size:11px;color:#64748b;margin-top:4px">{row.get('currency', '—')} · cena {row.get('price', 0.0):.2f}</div>
                                                </div>
                                                <div style="text-align:right">
                                                        <div style="font-size:10px;color:#047857">Setup 1M</div>
                                                        <div style="font-size:16px;font-weight:800;color:#047857">{row.get('win_rate_1m', 0.0):.0f}% / {row.get('avg_return_1m', 0.0):+.1f}%</div>
                                                        <div style="font-size:10px;color:#64748b">EV {row.get('ev_1m', 0.0):.3f}</div>
                                                </div>
                                        </div>
                                        <div style="font-size:12px;color:#334155;line-height:1.6;margin-top:8px">{row.get('summary', '')}</div>
                                    <div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:10px">
                                        <div style="background:#f8fafc;border-radius:10px;padding:10px 12px">
                                            <div style="font-size:10px;color:#6b7280">Jakość setupu</div>
                                            <div style="font-size:15px;font-weight:800;color:#111827;margin-top:3px">{row.get('setup_quality_score', 0.0):.0f}/100</div>
                                            <div style="font-size:10px;color:#64748b;margin-top:3px">historia + technika</div>
                                        </div>
                                        <div style="background:#f8fafc;border-radius:10px;padding:10px 12px">
                                            <div style="font-size:10px;color:#6b7280">Jakość spółki</div>
                                            <div style="font-size:15px;font-weight:800;color:#111827;margin-top:3px">{row.get('company_quality_score', 0.0):.0f}/100</div>
                                            <div style="font-size:10px;color:#64748b;margin-top:3px">fundamenty / newsflow</div>
                                        </div>
                                        <div style="background:#f8fafc;border-radius:10px;padding:10px 12px">
                                            <div style="font-size:10px;color:#6b7280">Makro</div>
                                            <div style="font-size:15px;font-weight:800;color:#111827;margin-top:3px">{row.get('macro_quality_score', 0.0):.0f}/100</div>
                                            <div style="font-size:10px;color:#64748b;margin-top:3px">otoczenie rynku</div>
                                        </div>
                                    </div>
                                    <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;padding:10px 12px;margin-top:10px">
                                        <div style="font-size:10px;color:#1d4ed8;text-transform:uppercase;letter-spacing:.5px">Dlaczego tak wysoko</div>
                                        <div style="font-size:12px;color:#334155;line-height:1.6;margin-top:6px">{row.get('rank_note', '')}</div>
                                    </div>
                                    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:10px 12px;margin-top:10px">
                                        <div style="font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.5px">Komentarz do pewności</div>
                                        <div style="font-size:12px;color:#334155;line-height:1.6;margin-top:6px">{row.get('confidence_note', '')}</div>
                                    </div>
                                </div>"""
                                                                                                for row in opportunities[:5]
                                                                )
                                else:
                                                                opportunities_html = "<div style='background:#ffffff;border:1px dashed #cbd5e1;border-radius:12px;padding:14px 16px;font-size:12px;color:#64748b;margin-top:12px'>Skan okazji nie wskazał dziś mocnych kandydatów spoza obecnego portfela.</div>"

                                return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>
    * {{ box-sizing:border-box;margin:0;padding:0 }}
    body {{ font-family:"Helvetica Neue",Arial,sans-serif;background:#f8fafc;color:#111827;font-size:12px;padding:0 }}
    .container {{ max-width:1120px;margin:0 auto;padding:16px }}
    .card {{ background:#fff;border:1px solid #dbe3ef;border-radius:16px;padding:16px;margin-bottom:14px }}
    .section-title {{ font-size:11px;font-weight:800;color:#64748b;text-transform:uppercase;letter-spacing:.6px;margin-bottom:12px }}
    .grid-two {{ display:grid;grid-template-columns:1.15fr .85fr;gap:14px }}
</style></head>
<body><div class="container">
    <div class="card" style="background:linear-gradient(135deg,#eff6ff,#ffffff)">
        <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap">
            <div>
                <div style="font-size:28px;font-weight:900;color:#0f172a">Sugestie dla portfela</div>
                <div style="font-size:12px;color:#64748b;margin-top:6px">Łączę rekomendacje pozycji, historię podobnych setupów technicznych, rozwój sytuacji od zakupu i wyniki skanera okazji.</div>
            </div>
            <div style="text-align:right;font-size:11px;color:#64748b;line-height:1.7">
                <div>Aktualizacja: <b style="color:#0f172a">{generated_at}</b></div>
                <div>Oceniono: <b style="color:#0f172a">{coverage.get('evaluated', 0)}</b> pozycji</div>
                <div>Skan okazji: <b style="color:#0f172a">{'tak' if scan.get('loaded') else 'nie'}</b>{f" · kandydaci {scan.get('count', 0)}" if scan.get('loaded') else ''}</div>
            </div>
        </div>
    </div>

    <div class="grid-two">
        <div class="card">
            <div class="section-title">Najważniejsze wnioski</div>
            <div style="display:grid;gap:10px">{overview_html}</div>
            {f"<div style='font-size:11px;color:#94a3b8;margin-top:12px'>Nie pokazano {hidden_watch} spokojnych pozycji typu 'trzymaj / obserwuj', żeby skupić widok na ruchach mających większy wpływ na potencjalny zysk.</div>" if hidden_watch else ''}
        </div>
        <div class="card">
            <div class="section-title">Najlepsze okazje spoza portfela</div>
            {opportunities_html}
        </div>
    </div>

    <div class="card">
        <div class="section-title">Pomysły na wymianę pozycji</div>
        {replacements_html}
    </div>

    <div class="card">
        <div class="section-title">Pozycje warte decyzji</div>
        {holdings_html or '<div style="font-size:12px;color:#64748b">Brak pozycji z wystarczającą ilością danych do sugestii.</div>'}
    </div>
</div></body></html>"""


def _ordered_simulation_scenarios(data: dict) -> list[dict]:
                scenario_map = data.get("scenarios") or {}
                return [scenario_map[key] for key in ("likely", "neutral", "negative", "positive") if key in scenario_map]


def _currency_rows_html(rows: list[dict], value_key: str, signed: bool = False) -> str:
                if not rows:
                                return '<div style="font-size:11px;color:#9ca3af;margin-top:6px">brak danych</div>'

                parts = []
                for row in rows:
                                value = row.get(value_key)
                                color = _col_pnl(value) if signed else "#111827"
                                parts.append(
                                                f"<div style=\"display:flex;justify-content:space-between;gap:12px;font-size:11px;margin-top:5px\">"
                                                f"<span style=\"color:#6b7280;font-weight:700\">{row.get('currency', '—')}</span>"
                                                f"<span style=\"color:{color};font-weight:800\">{_fmt_money(value, signed=signed)}</span>"
                                                f"</div>"
                                )
                return "".join(parts)


def _build_simulation_chart_html(data: dict) -> str:
                scenarios = _ordered_simulation_scenarios(data)
                if not scenarios:
                                return ""

                dates = data.get("forecast_dates", [])
                current_value = float(data.get("current_value", 0) or 0)
                row_count = len(scenarios)

                fig = make_subplots(
                    rows=row_count,
                    cols=1,
                    vertical_spacing=0.04,
                    row_heights=[1.0 / row_count] * row_count,
                    subplot_titles=[scenario["label"] for scenario in scenarios],
                )

                palette = {
                    "likely": ("#1d4ed8", "rgba(59,130,246,0.14)", "rgba(59,130,246,0.24)"),
                    "negative": ("#b91c1c", "rgba(239,68,68,0.16)", "rgba(239,68,68,0.28)"),
                    "neutral": ("#b45309", "rgba(245,158,11,0.16)", "rgba(245,158,11,0.28)"),
                    "positive": ("#15803d", "rgba(34,197,94,0.16)", "rgba(34,197,94,0.28)"),
                }

                for row_idx, scenario in enumerate(scenarios, start=1):
                                line_color, outer_fill, inner_fill = palette[scenario["key"]]
                                bands = scenario["path_percentiles"]
                                median_values = [current_value * (1.0 + value / 100.0) for value in bands["p50"]]
                                median_pnl = [value - current_value for value in median_values]

                                fig.add_trace(go.Scatter(
                                    x=dates,
                                    y=bands["p10"],
                                    mode="lines",
                                    line=dict(width=0),
                                    hoverinfo="skip",
                                    showlegend=False,
                                ), row=row_idx, col=1)
                                fig.add_trace(go.Scatter(
                                    x=dates,
                                    y=bands["p90"],
                                    mode="lines",
                                    line=dict(width=0),
                                    fill="tonexty",
                                    fillcolor=outer_fill,
                                    hovertemplate="%{y:.1f}%<extra>p10-p90</extra>",
                                    showlegend=False,
                                ), row=row_idx, col=1)

                                fig.add_trace(go.Scatter(
                                    x=dates,
                                    y=bands["p25"],
                                    mode="lines",
                                    line=dict(width=0),
                                    hoverinfo="skip",
                                    showlegend=False,
                                ), row=row_idx, col=1)
                                fig.add_trace(go.Scatter(
                                    x=dates,
                                    y=bands["p75"],
                                    mode="lines",
                                    line=dict(width=0),
                                    fill="tonexty",
                                    fillcolor=inner_fill,
                                    hovertemplate="%{y:.1f}%<extra>p25-p75</extra>",
                                    showlegend=False,
                                ), row=row_idx, col=1)

                                fig.add_trace(go.Scatter(
                                    x=dates,
                                    y=bands["p50"],
                                    mode="lines",
                                    name=scenario["label"],
                                    line=dict(color=line_color, width=2.3),
                                    customdata=list(zip(median_values, median_pnl)),
                                    hovertemplate="%{y:.1f}%<br>wartość %{customdata[0]:.2f}<br>P/L %{customdata[1]:+.2f}<extra>mediana</extra>",
                                    showlegend=False,
                                ), row=row_idx, col=1)

                                fig.add_hline(y=0, line_color="#cbd5e1", line_width=1, line_dash="dot", row=row_idx, col=1)

                fig.update_layout(
                    paper_bgcolor="#ffffff",
                    plot_bgcolor="#ffffff",
                    font=dict(color="#1f2937", family="Helvetica Neue, Arial, sans-serif", size=11),
                    margin=dict(l=20, r=20, t=50, b=10),
                    height=220 * row_count + 80,
                    hovermode="x unified",
                )
                for row_idx in range(1, row_count + 1):
                                fig.update_xaxes(
                                    showgrid=True,
                                    gridcolor="#eef2f7",
                                    row=row_idx,
                                    col=1,
                                    fixedrange=True,
                                )
                                fig.update_yaxes(
                                    title_text="P/L %",
                                    showgrid=True,
                                    gridcolor="#eef2f7",
                                    zeroline=False,
                                    fixedrange=True,
                                    row=row_idx,
                                    col=1,
                                )

                return fig.to_html(
                    full_html=False,
                    include_plotlyjs=True,
                    config={"displayModeBar": False, "scrollZoom": False, "doubleClick": False, "showTips": False},
                )


def _build_simulation_html(data: dict) -> str:
    chart_html = _build_simulation_chart_html(data)
    pnl_color = _col_pnl(data.get("current_pnl_pct"))
    snapshot = (data.get("snapshot_time") or "")[:16].replace("T", " ")
    history_profile = data.get("history_profile") or {}
    currency_context = data.get("currency_context") or {}
    currency_breakdown = data.get("currency_breakdown") or []
    currency_title = "Waluty" if currency_context.get("is_mixed") else "Waluta"
    currency_label = currency_context.get("label") or "—"
    currency_detail = currency_context.get("detail") or ""
    scenarios = _ordered_simulation_scenarios(data)
    current_breakdown_html = _currency_rows_html(currency_breakdown, "current_value")
    invested_breakdown_html = _currency_rows_html(currency_breakdown, "invested_value")
    pnl_breakdown_html = _currency_rows_html(currency_breakdown, "current_pnl_abs", signed=True)

    cards_html = ""
    border_map = {
        "likely": "#bfdbfe",
        "neutral": "#fde68a",
        "negative": "#fecaca",
        "positive": "#bbf7d0",
    }
    badge_map = {
        "likely": "#dbeafe",
        "neutral": "#fef3c7",
        "negative": "#fee2e2",
        "positive": "#dcfce7",
    }
    for scenario in scenarios:
        horizons = scenario.get("horizons", {})
        currency_horizons = scenario.get("currency_horizons", {})
        h1 = horizons.get("1m", {})
        h3 = horizons.get("3m", {})
        h6 = horizons.get("6m", {})
        h1_currency_html = _currency_rows_html(currency_horizons.get("1m", []), "median_pnl_abs", signed=True)
        h3_currency_html = _currency_rows_html(currency_horizons.get("3m", []), "median_pnl_abs", signed=True)
        h6_currency_html = _currency_rows_html(currency_horizons.get("6m", []), "median_pnl_abs", signed=True)
        best = scenario.get("contributors", [{}])[0] if scenario.get("contributors") else {}
        worst = scenario.get("contributors", [{}])[-1] if scenario.get("contributors") else {}

        thresholds_rows = ""
        for threshold in scenario.get("thresholds", []):
            target = threshold.get("target_pct", 0)
            sign = "+" if target > 0 else ""
            days = threshold.get("median_days")
            days_str = f"{days} dni" if days is not None else "brak trafień"
            thresholds_rows += f"""
            <tr>
                <td style="padding:4px 0;color:#6b7280">{sign}{target:.0f}%</td>
                <td style="padding:4px 0;text-align:right;font-weight:700;color:#111827">{threshold.get('hit_probability', 0):.1f}%</td>
                <td style="padding:4px 0;text-align:right;color:#111827">{days_str}</td>
            </tr>"""

        cards_html += f"""
        <div style="background:#fff;border:1px solid {border_map.get(scenario['key'], '#e5e7eb')};border-radius:14px;padding:16px;display:flex;flex-direction:column;gap:12px">
            <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">
                <div>
                    <div style="font-size:18px;font-weight:800;color:#111827">{scenario['label']}</div>
                    <div style="font-size:11px;color:#6b7280;line-height:1.6;margin-top:4px">{scenario['description']}</div>
                </div>
                <div style="background:{badge_map.get(scenario['key'], '#f3f4f6')};border-radius:999px;padding:6px 10px;font-size:10px;font-weight:800;color:#111827;white-space:nowrap">{scenario.get('source_blocks', 0)} bloków</div>
            </div>
            <div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px">
                <div style="background:#f8fafc;border-radius:10px;padding:10px 12px">
                    <div style="font-size:10px;color:#6b7280">1 miesiąc</div>
                    <div style="font-size:19px;font-weight:800;color:{_col_pnl(h1.get('median_return_pct'))}">{h1.get('median_return_pct', 0):+.1f}%</div>
                    <div style="font-size:10px;color:#6b7280;margin-top:4px">mediana P/L wg walut</div>
                    {h1_currency_html}
                    <div style="font-size:10px;color:#6b7280">szansa zysku: {h1.get('profit_probability', 0):.1f}%</div>
                </div>
                <div style="background:#f8fafc;border-radius:10px;padding:10px 12px">
                    <div style="font-size:10px;color:#6b7280">3 miesiące</div>
                    <div style="font-size:19px;font-weight:800;color:{_col_pnl(h3.get('median_return_pct'))}">{h3.get('median_return_pct', 0):+.1f}%</div>
                    <div style="font-size:10px;color:#6b7280;margin-top:4px">mediana P/L wg walut</div>
                    {h3_currency_html}
                    <div style="font-size:10px;color:#6b7280">przedział: {h3.get('p10_return_pct', 0):+.1f}% do {h3.get('p90_return_pct', 0):+.1f}%</div>
                </div>
                <div style="background:#f8fafc;border-radius:10px;padding:10px 12px">
                    <div style="font-size:10px;color:#6b7280">6 miesięcy</div>
                    <div style="font-size:19px;font-weight:800;color:{_col_pnl(h6.get('median_return_pct'))}">{h6.get('median_return_pct', 0):+.1f}%</div>
                    <div style="font-size:10px;color:#6b7280;margin-top:4px">mediana P/L wg walut</div>
                    {h6_currency_html}
                    <div style="font-size:10px;color:#6b7280">tempo mediany: {h6.get('monthlyized_return_pct', 0):+.2f}% / mies.</div>
                </div>
            </div>
            <div style="display:grid;grid-template-columns:1.2fr .8fr;gap:12px">
                <div style="background:#fcfcfd;border:1px solid #eef2f7;border-radius:10px;padding:12px">
                    <div style="font-size:11px;font-weight:700;color:#374151;margin-bottom:8px">Jak szybko portfel może dojść do progów</div>
                    <table style="width:100%;border-collapse:collapse;font-size:11px">
                        <thead>
                            <tr style="border-bottom:1px solid #e5e7eb">
                                <th style="padding:0 0 6px 0;text-align:left;color:#9ca3af;font-weight:600">Próg</th>
                                <th style="padding:0 0 6px 0;text-align:right;color:#9ca3af;font-weight:600">Szansa</th>
                                <th style="padding:0 0 6px 0;text-align:right;color:#9ca3af;font-weight:600">Mediana czasu</th>
                            </tr>
                        </thead>
                        <tbody>{thresholds_rows}</tbody>
                    </table>
                </div>
                <div style="background:#fcfcfd;border:1px solid #eef2f7;border-radius:10px;padding:12px">
                    <div style="font-size:11px;font-weight:700;color:#374151;margin-bottom:8px">Profil ryzyka</div>
                    <div style="font-size:11px;color:#6b7280">Medianowe max obsunięcie</div>
                    <div style="font-size:18px;font-weight:800;color:#b91c1c;margin-top:2px">{scenario['max_drawdown'].get('median_pct', 0):.1f}%</div>
                    <div style="font-size:10px;color:#6b7280;margin-top:6px">Stress 10 percentyla: {scenario['max_drawdown'].get('stress_pct', 0):.1f}%</div>
                    <div style="font-size:10px;color:#6b7280;margin-top:6px">Dolny zakres 6M: {h6.get('p10_return_pct', 0):+.1f}%</div>
                    <div style="font-size:10px;color:#6b7280;margin-top:12px">Najmocniejszy wkład: <b style="color:#111827">{best.get('ticker', '—')}</b> {_fmt_money(best.get('median_pnl_abs'), signed=True)} {best.get('currency', '')}</div>
                    <div style="font-size:10px;color:#6b7280;margin-top:4px">Największy hamulec: <b style="color:#111827">{worst.get('ticker', '—')}</b> {_fmt_money(worst.get('median_pnl_abs'), signed=True)} {worst.get('currency', '')}</div>
                </div>
            </div>
        </div>"""

    insight_tones = {
        "info": ("#eff6ff", "#1d4ed8"),
        "warn": ("#fff7ed", "#c2410c"),
        "good": ("#ecfdf5", "#15803d"),
    }
    insights_html = ""
    for item in data.get("insights", []):
        bg, accent = insight_tones.get(item.get("tone"), ("#f8fafc", "#475569"))
        insights_html += f"""
        <div style="background:{bg};border:1px solid {accent}22;border-radius:12px;padding:14px">
            <div style="font-size:12px;font-weight:800;color:{accent}">{item.get('title', 'Wniosek')}</div>
            <div style="font-size:12px;line-height:1.7;color:#334155;margin-top:6px">{item.get('text', '')}</div>
        </div>"""

    holdings_rows = ""
    for row in data.get("weights", []):
        holdings_rows += f"""
        <tr>
            <td style="padding:8px 6px;font-weight:700;color:#111827">{row.get('ticker', '—')}</td>
            <td style="padding:8px 6px;text-align:right">{row.get('weight_pct', 0):.1f}%</td>
            <td style="padding:8px 6px;text-align:right">{_fmt_money(row.get('current_value'))} {row.get('currency', '')}</td>
            <td style="padding:8px 6px;text-align:right">{row.get('risk_contribution_pct', 0):.1f}%</td>
            <td style="padding:8px 6px;text-align:right">{row.get('vol_annual_pct', 0):.1f}%</td>
            <td style="padding:8px 6px;text-align:right;color:{_col_pnl(row.get('return_3m_pct'))}">{row.get('return_3m_pct', 0):+.1f}%</td>
            <td style="padding:8px 6px;text-align:right;color:{_col_pnl(row.get('return_1y_pct'))}">{row.get('return_1y_pct', 0):+.1f}%</td>
            <td style="padding:8px 6px;text-align:right;color:{_col_pnl(row.get('blended_context'))}">{row.get('blended_context', 0):+.2f}</td>
        </tr>"""

    notes_html = "".join(
        f"<li style='margin-left:18px;margin-top:6px;color:#4b5563'>{note}</li>" for note in data.get("notes", [])
    )

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        * {{ box-sizing:border-box }}
        body {{ margin:0;padding:0;background:#f8fafc;color:#111827;font-family:"Helvetica Neue",Arial,sans-serif }}
        .container {{ max-width:1320px;margin:0 auto;padding:18px }}
        .card {{ background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:14px }}
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <div style="display:flex;justify-content:space-between;gap:18px;align-items:flex-start;flex-wrap:wrap">
                <div>
                    <div style="font-size:26px;font-weight:900;color:#111827">Symulacja portfela</div>
                    <div style="font-size:12px;color:#6b7280;margin-top:6px">Snapshot: {snapshot or '—'} · {data.get('n_assets', 0)} pozycji · {data.get('history_days', 0)} dni wspólnej historii · {data.get('n_sims', 0)} ścieżek</div>
                </div>
                <div style="display:grid;grid-template-columns:repeat(5,minmax(140px,1fr));gap:10px;flex:1;min-width:760px">
                    <div style="background:#f8fafc;border-radius:12px;padding:12px 14px">
                        <div style="font-size:10px;color:#6b7280">Aktualna wartość wg walut</div>
                        {current_breakdown_html}
                        <div style="font-size:11px;color:#334155;font-weight:700;margin-top:4px">{currency_title}: {currency_label}</div>
                        <div style="font-size:10px;color:#6b7280;margin-top:2px">{currency_detail}</div>
                    </div>
                    <div style="background:#f8fafc;border-radius:12px;padding:12px 14px">
                        <div style="font-size:10px;color:#6b7280">Zainwestowano wg walut</div>
                        {invested_breakdown_html}
                    </div>
                    <div style="background:#f8fafc;border-radius:12px;padding:12px 14px">
                        <div style="font-size:10px;color:#6b7280">Obecny P/L</div>
                        <div style="font-size:22px;font-weight:800;color:{pnl_color}">{data.get('current_pnl_pct', 0):+.2f}%</div>
                        {pnl_breakdown_html}
                    </div>
                    <div style="background:#f8fafc;border-radius:12px;padding:12px 14px">
                        <div style="font-size:10px;color:#6b7280">Historia pozycji</div>
                        <div style="font-size:22px;font-weight:800;color:{_col_pnl(history_profile.get('recent_change_pct'))}">{history_profile.get('win_rate', 0):.1f}%</div>
                        <div style="font-size:10px;color:#6b7280">skuteczność · ostatnie 4H {history_profile.get('recent_change_pct', 0):+.2f}%</div>
                    </div>
                    <div style="background:#f8fafc;border-radius:12px;padding:12px 14px">
                        <div style="font-size:10px;color:#6b7280">Model</div>
                        <div style="font-size:15px;font-weight:800;color:#111827">bootstrap + kontekst wejścia</div>
                        <div style="font-size:10px;color:#6b7280;margin-top:4px">bloki {data.get('block_size', 0)} sesji · setup i trajektoria od zakupu</div>
                    </div>
                </div>
            </div>
            <ul style="margin:12px 0 0 0;padding:0">{notes_html}</ul>
        </div>

        <div class="card" style="background:linear-gradient(180deg,#eff6ff 0%,#ffffff 100%)">
            <div style="display:flex;justify-content:space-between;gap:18px;align-items:flex-start;flex-wrap:wrap">
                <div>
                    <div style="font-size:16px;font-weight:900;color:#111827">Najbardziej prawdopodobna ścieżka</div>
                    <div style="font-size:12px;color:#475569;margin-top:6px">Ten wariant korzysta z historii Twoich pozycji z pierwszej zakładki i dopasowuje pulę fragmentów do realnej ścieżki portfela.</div>
                </div>
                <div style="display:grid;grid-template-columns:repeat(3,minmax(150px,1fr));gap:10px;flex:1;min-width:480px">
                    <div style="background:#ffffffcc;border-radius:12px;padding:12px 14px">
                        <div style="font-size:10px;color:#6b7280">Bias historii</div>
                        <div style="font-size:22px;font-weight:800;color:{_col_pnl(history_profile.get('bias'))}">{history_profile.get('bias', 0):+.2f}</div>
                    </div>
                    <div style="background:#ffffffcc;border-radius:12px;padding:12px 14px">
                        <div style="font-size:10px;color:#6b7280">Ostatni ruch 4H</div>
                        <div style="font-size:22px;font-weight:800;color:{_col_pnl(history_profile.get('recent_change_pct'))}">{history_profile.get('recent_change_pct', 0):+.2f}%</div>
                        <div style="font-size:10px;color:#6b7280">{history_profile.get('recent_change_pct', 0):+.2f}% całego portfela</div>
                    </div>
                    <div style="background:#ffffffcc;border-radius:12px;padding:12px 14px">
                        <div style="font-size:10px;color:#6b7280">Udział zamkniętego wyniku</div>
                        <div style="font-size:22px;font-weight:800;color:#111827">{history_profile.get('closed_share', 0) * 100:.0f}%</div>
                    </div>
                </div>
            </div>
        </div>

        <div class="card">{chart_html}</div>

        <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px">{cards_html}</div>

        <div class="card" style="margin-top:14px">
            <div style="font-size:13px;font-weight:800;color:#374151;margin-bottom:12px">Na co zwrócić uwagę</div>
            <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px">{insights_html}</div>
        </div>

        <div class="card" style="margin-top:14px">
            <div style="font-size:13px;font-weight:800;color:#374151;margin-bottom:12px">Skład portfela a ryzyko symulacji</div>
            <table style="width:100%;border-collapse:collapse;font-size:11px">
                <thead>
                    <tr style="background:#f8fafc;border-bottom:1px solid #e5e7eb">
                        <th style="padding:8px 6px;text-align:left;color:#6b7280">Ticker</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">Udział</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">Wartość</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">Wkład w ryzyko</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">Vol 1R</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">3M</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">1R</th>
                        <th style="padding:8px 6px;text-align:right;color:#6b7280">Kontekst</th>
                    </tr>
                </thead>
                <tbody>{holdings_rows}</tbody>
            </table>
        </div>
    </div>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# Detail dialog
# ─────────────────────────────────────────────────────────────────────────────

class PortfolioDetailDialog(QDialog):
    """Modal showing full analysis for a portfolio holding."""

    def __init__(self, holding: dict, parent=None):
        super().__init__(parent)
        self._holding = holding
        self._temp_file = None
        self.setWindowTitle(f"Analiza pozycji — {holding['ticker']}")
        self.resize(1000, 780)
        self.setMinimumSize(800, 600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._web = QWebEngineView()
        layout.addWidget(self._web)

        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(12, 8, 12, 8)
        btn_bar.addStretch()
        close_btn = QPushButton("Zamknij")
        close_btn.setObjectName("refreshBtn")
        close_btn.clicked.connect(self.accept)
        btn_bar.addWidget(close_btn)
        layout.addLayout(btn_bar)

        self._show_loading()
        self._worker = HoldingAnalysisWorker(holding)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _show_loading(self):
        ticker = self._holding["ticker"]
        self._web.setHtml(f"""<html><body style="background:#f8f9fa;display:flex;
          flex-direction:column;align-items:center;justify-content:center;height:100vh;
          font-family:sans-serif;color:#6c757d">
          <div style="font-size:32px;margin-bottom:16px">⏳</div>
          <div style="font-size:16px;font-weight:600">Analizuję Twoją pozycję…</div>
          <div style="font-size:12px;margin-top:8px">{ticker}</div>
          <div style="font-size:11px;margin-top:12px;color:#adb5bd">
            Obliczam wskaźniki i sprawdzam historię ceny zakupu
          </div>
        </body></html>""")

    def _on_done(self, data: dict):
        html = _build_portfolio_html(data)
        if self._temp_file and os.path.exists(self._temp_file):
            try: os.unlink(self._temp_file)
            except OSError: pass
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write(html)
            self._temp_file = f.name
        self._web.load(QUrl.fromLocalFile(self._temp_file))

    def _on_error(self, message: str):
        self._web.setHtml(f"""<html><body style="background:#fff5f5;display:flex;
          flex-direction:column;align-items:center;justify-content:center;height:100vh;
          font-family:sans-serif;color:#dc2626">
          <div style="font-size:32px;margin-bottom:16px">⚠️</div>
          <div style="font-size:16px;font-weight:600">Błąd analizy</div>
          <pre style="font-size:10px;margin-top:12px;color:#6c757d;max-width:80%;white-space:pre-wrap">{message}</pre>
        </body></html>""")

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.wait()
        if self._temp_file and os.path.exists(self._temp_file):
            try: os.unlink(self._temp_file)
            except OSError: pass
        super().closeEvent(event)


class PortfolioHistoryDialog(QDialog):
    """Modal with tabs for position history, scenario simulation, and portfolio suggestions."""

    def __init__(self, payload: dict, parent=None):
        super().__init__(parent)
        self._temp_files: list[str] = []
        self._payload = payload
        self._history_reload_worker: PortfolioHistoryWorker | None = None
        self._quality_worker: PortfolioSignalQualityWorker | None = None
        self._scenario_worker: PortfolioScenarioWorker | None = None
        self._suggestions_worker: PortfolioSuggestionsWorker | None = None
        self._opportunity_worker: PortfolioOpportunityScanWorker | None = None
        self._quality_loaded = False
        self._scenario_loaded = False
        self._suggestions_loaded = False
        self._opportunity_results: list[dict] | None = None
        self._pending_opportunity_refresh = False
        self._opportunity_profile = "balanced"
        self.setWindowTitle("Historia pozycji")
        self.resize(1240, 940)
        self.setMinimumSize(980, 740)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        self._history_web = QWebEngineView()
        self._quality_web = QWebEngineView()
        self._simulation_web = QWebEngineView()
        self._suggestions_tab = QWidget()
        suggestions_layout = QVBoxLayout(self._suggestions_tab)
        suggestions_layout.setContentsMargins(12, 12, 12, 12)
        suggestions_layout.setSpacing(8)
        suggestions_top = QHBoxLayout()
        suggestions_top.setSpacing(8)
        self._suggestions_status = QLabel("Bez skanu okazji pokażę najpierw sugestie dla obecnych pozycji.")
        self._suggestions_status.setObjectName("infoLabel")
        self._suggestions_status.setWordWrap(True)
        suggestions_top.addWidget(self._suggestions_status, 1)
        self._suggestions_profile_btns: dict[str, QPushButton] = {}
        self._suggestions_scan_btn = QPushButton("🔍  Analizuj okazje")
        self._suggestions_scan_btn.setObjectName("scanBtn")
        self._suggestions_scan_btn.clicked.connect(self._start_opportunity_scan)
        suggestions_top.addWidget(self._suggestions_scan_btn)
        suggestions_layout.addLayout(suggestions_top)
        self._suggestions_progress = QProgressBar()
        self._suggestions_progress.setObjectName("scanProgress")
        self._suggestions_progress.setVisible(False)
        suggestions_layout.addWidget(self._suggestions_progress)
        self._suggestions_web = QWebEngineView()
        suggestions_layout.addWidget(self._suggestions_web, 1)

        self._tabs.addTab(self._history_web, "Historia pozycji")
        self._tabs.addTab(self._quality_web, "Weryfikacja sygnałów")
        self._tabs.addTab(self._simulation_web, "Scenariusze")
        self._tabs.addTab(self._suggestions_tab, "Sugestie")
        self._tabs.currentChanged.connect(self._on_tab_changed)

        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(12, 8, 12, 8)
        self._closed_editor_btn = QPushButton("✏️  Zamknięte")
        self._closed_editor_btn.setObjectName("addBtn")
        self._closed_editor_btn.clicked.connect(self._on_edit_closed_positions)
        btn_bar.addWidget(self._closed_editor_btn)
        btn_bar.addStretch()
        close_btn = QPushButton("Zamknij")
        close_btn.setObjectName("refreshBtn")
        close_btn.clicked.connect(self.accept)
        btn_bar.addWidget(close_btn)
        layout.addLayout(btn_bar)

        history_html = _build_history_html(payload.get("history", {}))
        self._load_html(self._history_web, history_html)
        self._quality_web.setHtml(_build_placeholder_html(
            "Weryfikacja jakości sygnałów",
            "Ta zakładka odtworzy prognozy widziane w dniu zakupu i porówna je z rzeczywistym zachowaniem kursu po 1 tygodniu, 1 miesiącu i 3 miesiącach.",
        ))
        self._simulation_web.setHtml(_build_placeholder_html(
            "Scenariusze portfela",
            "Przejście do tej zakładki uruchamia osobne liczenie scenariuszy, żeby otwarcie historii pozycji było lżejsze i stabilniejsze.",
        ))
        self._suggestions_web.setHtml(_build_placeholder_html(
            "Sugestie dla portfela",
            "Ta zakładka połączy aktualne rekomendacje pozycji z historią podobnych setupów i, opcjonalnie, ze skanem okazji spoza portfela.",
        ))

    def _load_html(self, webview: QWebEngineView, html: str):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write(html)
            self._temp_files.append(f.name)
            webview.load(QUrl.fromLocalFile(f.name))

    def _on_tab_changed(self, index: int):
        holdings = self._payload.get("holdings") or []

        if index == 1:
            if self._quality_loaded or self._quality_worker is not None:
                return
            if not holdings:
                self._quality_web.setHtml(_build_placeholder_html(
                    "Brak aktywnych pozycji do weryfikacji",
                    "Dodaj aktywne pozycje do portfela, aby porównać sygnały widziane w dniu zakupu z realnym zachowaniem kursu.",
                ))
                self._quality_loaded = True
                return

            self._quality_web.setHtml(_build_placeholder_html(
                "Liczę weryfikację sygnałów",
                "Odtwarzam setup widziany w dniu zakupu i zestawiam historyczne szanse 1T, 1M i 3M z rzeczywistym wynikiem pozycji.",
            ))
            self._quality_worker = PortfolioSignalQualityWorker(holdings)
            self._quality_worker.done.connect(self._on_quality_done)
            self._quality_worker.error.connect(self._on_quality_error)
            self._quality_worker.finished.connect(self._on_quality_thread_finished)
            self._quality_worker.start()
            return

        if index == 2:
            if self._scenario_loaded or self._scenario_worker is not None:
                return
            if not holdings:
                self._simulation_web.setHtml(_build_placeholder_html(
                    "Brak aktywnych pozycji do symulacji",
                    "Zakładka scenariuszy liczy projekcję tylko dla aktualnie otwartych pozycji. Historia zamkniętych pozycji jest dostępna w pierwszej zakładce.",
                ))
                self._scenario_loaded = True
                return

            self._simulation_web.setHtml(_build_placeholder_html(
                "Liczę scenariusze",
                "Analizuję historyczne ścieżki portfela i buduję scenariusze negatywne, najbardziej prawdopodobne, neutralne oraz pozytywne.",
            ))
            self._scenario_worker = PortfolioScenarioWorker(holdings, self._payload.get("history") or None)
            self._scenario_worker.done.connect(self._on_scenarios_done)
            self._scenario_worker.error.connect(self._on_scenarios_error)
            self._scenario_worker.finished.connect(self._on_scenario_thread_finished)
            self._scenario_worker.start()
            return

        if index == 3:
            if not holdings:
                self._suggestions_scan_btn.setEnabled(False)
                self._suggestions_web.setHtml(_build_placeholder_html(
                    "Brak aktywnych pozycji do sugestii",
                    "Sugestie działają tylko dla aktualnie otwartych pozycji. Gdy dodasz instrumenty lub będziesz mieć aktywne pozycje, ta zakładka pokaże rekomendacje i możliwe zamiany.",
                ))
                self._suggestions_loaded = True
                return
            self._run_suggestions_load()

    def _on_quality_done(self, quality_data: dict):
        self._load_html(self._quality_web, _build_signal_quality_html(quality_data))
        self._quality_loaded = True

    def _on_quality_error(self, message: str):
        self._quality_web.setHtml(_build_placeholder_html(
            "Błąd weryfikacji sygnałów",
            f"Nie udało się porównać sygnałów z dnia zakupu z późniejszym zachowaniem kursu.<br><br>{html.escape(message[:1200])}",
        ))
        self._quality_loaded = True

    def _on_quality_thread_finished(self):
        self._quality_worker = None

    def _on_scenarios_done(self, simulation_data: dict):
        if not simulation_data:
            self._simulation_web.setHtml(_build_placeholder_html(
                "Brak aktywnych pozycji do symulacji",
                "Zakładka scenariuszy liczy projekcję tylko dla aktualnie otwartych pozycji. Historia zamkniętych pozycji jest dostępna w pierwszej zakładce.",
            ))
            self._scenario_loaded = True
            return

        self._load_html(self._simulation_web, _build_simulation_html(simulation_data))
        self._scenario_loaded = True

    def _on_scenarios_error(self, message: str):
        self._simulation_web.setHtml(_build_placeholder_html(
            "Błąd scenariuszy",
            f"Nie udało się policzyć scenariuszy portfela.<br><br>{message}",
        ))
        self._scenario_loaded = True

    def _run_suggestions_load(self, force: bool = False, loading_title: str = "Buduję sugestie", loading_message: str | None = None):
        if self._suggestions_worker is not None:
            return
        if self._suggestions_loaded and not force:
            return

        holdings = self._payload.get("holdings") or []
        if not holdings:
            return

        message = loading_message or "Analizuję każdą pozycję pod kątem setupu technicznego, historii podobnych wejść i rozwoju sytuacji od zakupu."
        self._suggestions_web.setHtml(_build_placeholder_html(loading_title, message))
        self._suggestions_status.setText("Buduję sugestie dla obecnych pozycji…")
        self._suggestions_loaded = False
        self._suggestions_worker = PortfolioSuggestionsWorker(holdings, self._opportunity_results, self._opportunity_profile)
        self._suggestions_worker.done.connect(self._on_suggestions_done)
        self._suggestions_worker.error.connect(self._on_suggestions_error)
        self._suggestions_worker.finished.connect(self._on_suggestions_thread_finished)
        self._suggestions_worker.start()

    def _on_suggestions_done(self, suggestions_data: dict):
        self._load_html(self._suggestions_web, _build_suggestions_html(suggestions_data))
        scan = suggestions_data.get("opportunity_scan") or {}
        if scan.get("loaded"):
            self._suggestions_status.setText(
                f"Sugestie uwzględniają skan okazji. Kandydaci poza portfelem: {scan.get('count', 0)}."
            )
            self._suggestions_scan_btn.setText("🔁  Przelicz okazje")
        else:
            self._suggestions_status.setText("Sugestie policzone bez skanu okazji.")
        self._suggestions_loaded = True

    def _on_suggestions_error(self, message: str):
        self._suggestions_web.setHtml(_build_placeholder_html(
            "Błąd sugestii",
            f"Nie udało się przygotować sugestii dla portfela.<br><br>{message}",
        ))
        self._suggestions_status.setText("Nie udało się policzyć sugestii dla portfela.")
        self._suggestions_loaded = True

    def _on_suggestions_thread_finished(self):
        self._suggestions_worker = None
        if self._pending_opportunity_refresh:
            self._pending_opportunity_refresh = False
            self._run_suggestions_load(
                force=True,
                loading_title="Łączę sugestie z okazjami",
                loading_message="Porównuję słabsze pozycje z najlepszymi setupami ze skanera okazji i szukam sensownych zamian.",
            )

    def _set_opportunity_profile(self, profile: str):
        self._opportunity_profile = profile
        for key, btn in self._suggestions_profile_btns.items():
            btn.setChecked(key == profile)
        if self._suggestions_worker is not None or self._opportunity_worker is not None:
            self._pending_opportunity_refresh = True
            self._suggestions_status.setText("Profil zmieniony. Po zakończeniu bieżącej analizy odświeżę sugestie z nowym rankingiem.")
            return
        if self._opportunity_results is not None or self._suggestions_loaded:
            self._run_suggestions_load(
                force=True,
                loading_title="Przeliczam sugestie",
                loading_message="Zmienił się profil okazji, więc aktualizuję ranking kandydatów i pomysły na zamiany.",
            )

    def _start_opportunity_scan(self):
        holdings = self._payload.get("holdings") or []
        if not holdings or self._opportunity_worker is not None:
            return

        self._suggestions_scan_btn.setEnabled(False)
        self._suggestions_scan_btn.setText("⏳  Szukam okazji...")
        self._suggestions_progress.setVisible(True)
        self._suggestions_progress.setRange(0, len(config.SCANNER_UNIVERSE))
        self._suggestions_progress.setValue(0)
        self._suggestions_status.setText("Skanuję rynek i szukam setupów, które mogą pobić słabsze pozycje z portfela…")
        self._opportunity_worker = PortfolioOpportunityScanWorker(self._opportunity_profile)
        self._opportunity_worker.progress.connect(self._on_opportunity_scan_progress)
        self._opportunity_worker.done.connect(self._on_opportunity_scan_done)
        self._opportunity_worker.error.connect(self._on_opportunity_scan_error)
        self._opportunity_worker.finished.connect(self._on_opportunity_scan_finished)
        self._opportunity_worker.start()

    def _on_opportunity_scan_progress(self, index: int, total: int, ticker: str):
        self._suggestions_progress.setValue(index)
        self._suggestions_status.setText(f"Analiza okazji: {ticker} ({index}/{total})")

    def _on_opportunity_scan_done(self, results: list[dict]):
        self._opportunity_results = results
        if self._suggestions_worker is not None:
            self._pending_opportunity_refresh = True
            self._suggestions_status.setText("Skan gotowy. Po zakończeniu bieżącej analizy odświeżę sugestie o nowe okazje.")
            return

        self._suggestions_status.setText(f"Skan gotowy. Porównuję portfel z {len(results)} kandydatami spoza obecnych pozycji…")
        self._run_suggestions_load(
            force=True,
            loading_title="Łączę sugestie z okazjami",
            loading_message="Porównuję słabsze pozycje z najlepszymi setupami ze skanera okazji i szukam sensownych zamian.",
        )

    def _on_opportunity_scan_error(self, message: str):
        self._suggestions_status.setText("Nie udało się przeskanować okazji. Spróbuj ponownie.")
        QMessageBox.warning(self, "Skan okazji", f"Nie udało się przeskanować okazji.\n\n{message}")

    def _on_opportunity_scan_finished(self):
        self._suggestions_progress.setVisible(False)
        self._suggestions_scan_btn.setEnabled(True)
        self._suggestions_scan_btn.setText("🔁  Przelicz okazje" if self._opportunity_results is not None else "🔍  Analizuj okazje")
        self._opportunity_worker = None

    def _on_edit_closed_positions(self):
        if self._history_reload_worker is not None:
            return
        if not load_closed_positions():
            QMessageBox.information(self, "Zamknięte pozycje", "Brak zapisanych zamkniętych pozycji do edycji.")
            return

        dlg = ClosedPositionsDialog(parent=self)
        dlg.exec()
        if dlg.changed:
            self._reload_history()

    def _reload_history(self):
        if self._history_reload_worker is not None:
            return
        self._closed_editor_btn.setEnabled(False)
        self._history_web.setHtml(_build_placeholder_html(
            "Odświeżam historię pozycji",
            "Wczytuję ponownie historię po zmianach w zamkniętych pozycjach.",
        ))
        self._history_reload_worker = PortfolioHistoryWorker(self._payload.get("holdings") or [])
        self._history_reload_worker.done.connect(self._on_history_reload_done)
        self._history_reload_worker.error.connect(self._on_history_reload_error)
        self._history_reload_worker.finished.connect(self._on_history_reload_finished)
        self._history_reload_worker.start()

    def _on_history_reload_done(self, data: dict):
        self._payload["history"] = data.get("history") or {}
        self._payload["holdings"] = data.get("holdings") or (self._payload.get("holdings") or [])
        self._load_html(self._history_web, _build_history_html(self._payload.get("history") or {}))

    def _on_history_reload_error(self, message: str):
        QMessageBox.warning(self, "Historia pozycji", f"Nie udało się odświeżyć historii po zmianach w zamkniętych pozycjach.\n\n{message}")
        self._load_html(self._history_web, _build_history_html(self._payload.get("history") or {}))

    def _on_history_reload_finished(self):
        self._closed_editor_btn.setEnabled(True)
        self._history_reload_worker = None

    def _on_scenario_thread_finished(self):
        self._scenario_worker = None

    def closeEvent(self, event):
        if self._history_reload_worker and self._history_reload_worker.isRunning():
            self._history_reload_worker.wait()
        if self._quality_worker and self._quality_worker.isRunning():
            self._quality_worker.wait()
        if self._scenario_worker and self._scenario_worker.isRunning():
            self._scenario_worker.wait()
        if self._suggestions_worker and self._suggestions_worker.isRunning():
            self._suggestions_worker.wait()
        if self._opportunity_worker and self._opportunity_worker.isRunning():
            self._opportunity_worker.wait()
        for path in self._temp_files:
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
        super().closeEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
# Portfolio list item
# ─────────────────────────────────────────────────────────────────────────────

class HoldingItem(QListWidgetItem):
    def __init__(self, holding: dict, current_price: float | None = None, currency: str | None = None, recommendation: str | None = None):
        self._holding = holding
        self.holding_id = str(holding.get("holding_id") or "")
        ticker    = holding["ticker"]
        shares    = holding["shares"]
        avg_price = holding["avg_price"]
        purchase_date = str(holding.get("purchase_date") or holding.get("added") or "").strip()
        currency_code = str(currency or holding.get("currency") or "USD").upper() or "USD"

        if current_price is not None:
            pnl_pct = (current_price - avg_price) / avg_price * 100
            pnl_abs = (current_price - avg_price) * shares
            label   = (
                f"{ticker:<8}  {shares:.4g} szt  ▷  kup: {avg_price:.2f}"
                f"   {_fmt_money(pnl_pct, signed=True)}% / {_fmt_money(pnl_abs, signed=True)} {currency_code}"
            )
        else:
            label = f"{ticker:<8}  {shares:.4g} szt  ▷  kup: {avg_price:.2f}"

        super().__init__(label)
        self.ticker = ticker
        self._current_price = current_price
        self._currency = currency_code
        self._pnl_pct = ((current_price - avg_price) / avg_price * 100) if current_price else None
        self._base_tooltip = f"{ticker} — {shares:.4g} akcji @ {avg_price:.2f} {currency_code}"
        if purchase_date:
            self._base_tooltip += f"\nKupiono: {purchase_date}"
        self.apply_recommendation(recommendation)

        if current_price is not None:
            pnl = (current_price - avg_price) / avg_price * 100
            if pnl >= 5:
                self.setForeground(QColor(config.COLOR_UP))
            elif pnl <= -5:
                self.setForeground(QColor(config.COLOR_DOWN))

    def apply_recommendation(self, recommendation: str | None):
        rec = (recommendation or "").upper()
        background_map = {
            "DOKUP WIĘCEJ": QColor(34, 197, 94, 60),
            "TRZYMAJ": QColor(250, 204, 21, 56),
            "ROZWAŻ SPRZEDAŻ": QColor(239, 68, 68, 52),
            "SPRZEDAJ": QColor(239, 68, 68, 66),
            "STOP LOSS": QColor(239, 68, 68, 84),
        }
        self.setBackground(background_map.get(rec, QColor(0, 0, 0, 0)))
        if rec:
            self.setToolTip(f"{self._base_tooltip}\nRekomendacja: {rec}")
        else:
            self.setToolTip(self._base_tooltip)


def _portfolio_outlook_metric(analysis: dict, suffix: str) -> dict | None:
    trajectory = analysis.get("trajectory_backtest") or {}
    entry = analysis.get("entry_backtest") or {}

    for source_name, source_data in (("trajectory", trajectory), ("entry", entry)):
        win_rate = source_data.get(f"win_rate_{suffix}")
        avg_return = source_data.get(f"avg_return_{suffix}")
        if win_rate is None or avg_return is None:
            continue
        return {
            "source": source_name,
            "win_rate": float(win_rate),
            "avg_return": float(avg_return),
            "samples": int(source_data.get(f"n_setups_{suffix}") or source_data.get("n_samples") or 0),
        }
    return None


def _build_portfolio_outlook_summary(analyses: list[dict]) -> dict:
    horizons = {
        "5d": {"label": "1 tydzień", "sub": "~5 sesji"},
        "22d": {"label": "1 miesiąc", "sub": "~22 sesje"},
        "66d": {"label": "3 miesiące", "sub": "~66 sesji"},
    }
    total_value = sum(max(float(item.get("current_value") or 0.0), 0.0) for item in analyses)
    positions_total = len(analyses)
    out = {
        "positions_total": positions_total,
        "total_value": round(total_value, 2),
        "horizons": {},
    }

    for suffix, meta in horizons.items():
        weighted_win = 0.0
        weighted_return = 0.0
        weighted_value = 0.0
        covered_positions = 0
        source_counts = {"trajectory": 0, "entry": 0}
        sample_sum = 0

        for analysis in analyses:
            current_value = max(float(analysis.get("current_value") or 0.0), 0.0)
            if current_value <= 0:
                continue

            metric = _portfolio_outlook_metric(analysis, suffix)
            if metric is None:
                continue

            covered_positions += 1
            weighted_value += current_value
            weighted_win += current_value * metric["win_rate"]
            weighted_return += current_value * metric["avg_return"]
            source_counts[metric["source"]] += 1
            sample_sum += metric["samples"]

        if weighted_value > 0:
            out["horizons"][suffix] = {
                "label": meta["label"],
                "sub": meta["sub"],
                "win_rate": round(weighted_win / weighted_value, 1),
                "avg_return": round(weighted_return / weighted_value, 2),
                "coverage_weight_pct": round((weighted_value / total_value) * 100.0, 1) if total_value > 0 else 0.0,
                "covered_positions": covered_positions,
                "source_counts": source_counts,
                "avg_samples": round(sample_sum / covered_positions, 1) if covered_positions else 0.0,
            }
        else:
            out["horizons"][suffix] = {
                "label": meta["label"],
                "sub": meta["sub"],
                "win_rate": None,
                "avg_return": None,
                "coverage_weight_pct": 0.0,
                "covered_positions": 0,
                "source_counts": source_counts,
                "avg_samples": 0.0,
            }

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Main Portfolio Tab widget
# ─────────────────────────────────────────────────────────────────────────────

class PortfolioTab(QWidget):
    ticker_selected = pyqtSignal(str, str)   # ticker, name

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Summary label
        self._summary = QLabel("Dodaj swoje instrumenty i analizuj je.")
        self._summary.setObjectName("infoLabel")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        # Holdings list
        self._list = QListWidget()
        self._list.setObjectName("tickerList")
        self._list.setItemDelegate(HoldingItemDelegate(self._list))
        layout.addWidget(self._list)

        # Hint
        hint = QLabel("Kliknij raz → wykres  ·  Dwa razy → pełna analiza")
        hint.setObjectName("infoLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._outlook_intro = QLabel("Łączna szansa portfela: najpierw biorę analizę ceny od zakupu, a tam gdzie jej jeszcze brakuje, uzupełniam wynik historią podobnych setupów technicznych.")
        self._outlook_intro.setObjectName("infoLabel")
        self._outlook_intro.setWordWrap(True)
        layout.addWidget(self._outlook_intro)

        outlook_row = QHBoxLayout()
        outlook_row.setSpacing(6)
        self._outlook_cards: dict[str, QLabel] = {}
        for suffix in ("5d", "22d", "66d"):
            card = QLabel()
            card.setTextFormat(Qt.TextFormat.RichText)
            card.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card.setMinimumHeight(108)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            card.setStyleSheet(
                "QLabel {"
                "background:#ffffff;"
                "border:1px solid #d8e2f0;"
                "border-radius:12px;"
                "padding:10px 8px;"
                "}"
            )
            outlook_row.addWidget(card)
            self._outlook_cards[suffix] = card
        layout.addLayout(outlook_row)

        self._outlook_note = QLabel("")
        self._outlook_note.setObjectName("infoLabel")
        self._outlook_note.setWordWrap(True)
        layout.addWidget(self._outlook_note)
        self._set_portfolio_outlook_loading()

        # Buttons row 1
        row1 = QHBoxLayout()
        row1.setSpacing(4)
        self._add_btn = QPushButton("➕  Dodaj pozycję")
        self._add_btn.setObjectName("scanBtn")
        self._add_btn.setMinimumWidth(0)
        self._add_btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._add_btn.clicked.connect(self._on_add)
        row1.addWidget(self._add_btn)
        self._edit_btn = QPushButton("✏️  Edytuj")
        self._edit_btn.setObjectName("addBtn")
        self._edit_btn.setEnabled(False)
        self._edit_btn.setMinimumWidth(0)
        self._edit_btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._edit_btn.clicked.connect(self._on_edit)
        row1.addWidget(self._edit_btn)
        layout.addLayout(row1)

        # Buttons row 2
        row2 = QHBoxLayout()
        row2.setSpacing(4)
        self._refresh_btn = QPushButton("🔄  Odśwież")
        self._refresh_btn.setObjectName("refreshBtn")
        self._refresh_btn.setMinimumWidth(0)
        self._refresh_btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._refresh_btn.clicked.connect(self._on_refresh)
        row2.addWidget(self._refresh_btn)
        self._history_btn = QPushButton("📚  Analiza")
        self._history_btn.setObjectName("scanBtn")
        self._history_btn.setMinimumWidth(0)
        self._history_btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._history_btn.clicked.connect(self._on_history)
        row2.addWidget(self._history_btn)
        self._remove_btn = QPushButton("📤  Zamknij")
        self._remove_btn.setObjectName("removeBtn")
        self._remove_btn.setEnabled(False)
        self._remove_btn.setMinimumWidth(0)
        self._remove_btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._remove_btn.clicked.connect(self._on_remove)
        row2.addWidget(self._remove_btn)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(4)
        self._ai_btn = QPushButton("🤖  PORADA AI")
        self._ai_btn.setObjectName("scanBtn")
        self._ai_btn.setMinimumWidth(0)
        self._ai_btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._ai_btn.clicked.connect(self._on_ai_advice)
        row3.addWidget(self._ai_btn)
        layout.addLayout(row3)

        # Progress (used during refresh)
        self._progress = QProgressBar()
        self._progress.setObjectName("scanProgress")
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # Connections
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)

        # State
        self._holdings: list[dict] = load_portfolio()
        self._prices: dict[str, float] = {}
        self._currencies: dict[str, str] = {}
        self._recommendations: dict[str, str] = {}
        self._refresh_worker: PriceRefreshWorker | None = None
        self._recommendation_worker: HoldingRecommendationWorker | None = None
        self._history_worker: PortfolioHistoryWorker | None = None
        self._outlook_worker: PortfolioOutlookWorker | None = None
        self._recommendation_run_id = 0
        self._pending_outlook_refresh = False
        self._last_price_snapshot_time = ""

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown_workers)

        self._refresh_list()
        self._refresh_recommendations()
        self._refresh_portfolio_outlook()

    # ── Data helpers ─────────────────────────────────────────────────────────

    def _refresh_list(self):
        self._list.clear()
        total_invested = 0.0
        total_current  = 0.0
        currency_totals: dict[str, float] = {}
        currency_invested: dict[str, float] = {}
        currency_current: dict[str, float] = {}
        for h in self._holdings:
            ticker = h["ticker"]
            cur = self._prices.get(ticker)
            currency = self._currencies.get(ticker) or str(h.get("currency") or "USD").upper() or "USD"
            item = HoldingItem(h, cur, currency, self._recommendations.get(str(h.get("holding_id") or "")))
            self._list.addItem(item)
            if cur is not None:
                invested_value = h["avg_price"] * h["shares"]
                current_value  = cur * h["shares"]
                total_invested += invested_value
                total_current  += current_value
                currency_invested[currency] = currency_invested.get(currency, 0.0) + invested_value
                currency_current[currency] = currency_current.get(currency, 0.0) + current_value
                currency_totals[currency] = currency_totals.get(currency, 0.0) + (current_value - invested_value)

        if self._holdings:
            if currency_totals:
                mixed_currencies = len(currency_totals) > 1
                summary_bits = [
                    f"<b>{len(self._holdings)}</b> pozycji",
                ]
                if mixed_currencies:
                    summary_bits.append("<span style='color:#6b7280;font-weight:700'>Portfel wielowalutowy - wspólny % ukryty</span>")
                    for code, pnl_abs in sorted(currency_totals.items()):
                        invested_value = currency_invested.get(code, 0.0)
                        current_value = currency_current.get(code, 0.0)
                        pnl_pct = ((current_value - invested_value) / invested_value * 100.0) if invested_value > 0 else None
                        pct_text = f"{_fmt_money(pnl_pct, signed=True)}%" if pnl_pct is not None else "—"
                        summary_bits.append(
                            f"<b style='color:{_col_pnl(pnl_abs)}'>{code} {pct_text} / {_fmt_money(pnl_abs, signed=True)}</b>"
                        )
                elif total_invested > 0 and total_current > 0:
                    total_pnl = (total_current - total_invested) / total_invested * 100
                    col  = config.COLOR_UP if total_pnl >= 0 else config.COLOR_DOWN
                    summary_bits.append(f"<b style='color:{col}'>Łącznie {_fmt_money(total_pnl, signed=True)}%</b>")
                    for code, pnl_abs in sorted(currency_totals.items()):
                        summary_bits.append(
                            f"<b style='color:{_col_pnl(pnl_abs)}'>{_fmt_money(pnl_abs, signed=True)} {code}</b>"
                        )
                if self._last_price_snapshot_time:
                    summary_bits.append(
                        f"<span style='color:#6b7280'>Snapshot cen: {self._last_price_snapshot_time}</span>"
                    )
                self._summary.setText(
                    " · ".join(summary_bits)
                )
            else:
                self._summary.setText(f"<b>{len(self._holdings)}</b> pozycji · Kliknij 'Odśwież' by zobaczyć P&L")
        else:
            self._summary.setText("Brak pozycji. Dodaj instrumenty przyciskiem poniżej.")

    def _holding_with_live_snapshot(self, holding: dict) -> dict:
        enriched = dict(holding)
        ticker = str(holding.get("ticker") or "").upper()
        price = self._prices.get(ticker)
        currency = self._currencies.get(ticker)
        if price is not None and price > 0:
            enriched["live_price_snapshot"] = float(price)
        if currency:
            enriched["live_currency_snapshot"] = str(currency).upper()
        return enriched

    def _holdings_with_live_snapshot(self) -> list[dict]:
        return [self._holding_with_live_snapshot(holding) for holding in self._holdings]

    def _find_holding_with_live_snapshot(self, holding_id: str) -> dict | None:
        holding = next((item for item in self._holdings if str(item.get("holding_id") or "") == holding_id), None)
        if holding is None:
            return None
        return self._holding_with_live_snapshot(holding)

    def _refresh_recommendations(self):
        current_holding_ids = {str(holding.get("holding_id") or "") for holding in self._holdings}
        self._recommendations = {
            holding_id: recommendation
            for holding_id, recommendation in self._recommendations.items()
            if holding_id in current_holding_ids
        }
        if not self._holdings:
            return

        self._recommendation_run_id += 1
        run_id = self._recommendation_run_id
        self._recommendation_worker = HoldingRecommendationWorker(self._holdings_with_live_snapshot(), run_id)
        self._recommendation_worker.recommendation_ready.connect(self._on_recommendation_ready)
        self._recommendation_worker.done.connect(self._on_recommendation_done)
        self._recommendation_worker.finished.connect(self._on_recommendation_worker_finished)
        self._recommendation_worker.start()

    def _clear_recommendation_state(self):
        self._recommendations = {}
        for index in range(self._list.count()):
            item = self._list.item(index)
            if isinstance(item, HoldingItem):
                item.apply_recommendation(None)
        self._list.viewport().update()

    def _set_portfolio_outlook_loading(self, message: str = "Liczę łączną szansę i średni ruch całego portfela na podstawie historii pozycji…"):
        templates = {
            "5d": ("1 tydzień", "~5 sesji"),
            "22d": ("1 miesiąc", "~22 sesje"),
            "66d": ("3 miesiące", "~66 sesji"),
        }
        for suffix, card in self._outlook_cards.items():
            label, sub = templates[suffix]
            card.setText(
                f"<div style='font-size:11px;font-weight:700;color:#475569'>{label}</div>"
                f"<div style='font-size:9px;color:#94a3b8;margin-top:2px'>{sub}</div>"
                f"<div style='font-size:18px;font-weight:900;color:#94a3b8;margin-top:12px'>…</div>"
                f"<div style='font-size:10px;color:#94a3b8;margin-top:4px'>liczę wynik</div>"
            )
        self._outlook_note.setText(message)

    def _render_portfolio_outlook(self, summary: dict):
        if not summary.get("positions_total"):
            for card in self._outlook_cards.values():
                card.setText(
                    "<div style='font-size:12px;font-weight:700;color:#64748b'>Brak pozycji</div>"
                    "<div style='font-size:10px;color:#94a3b8;margin-top:6px'>Dodaj instrumenty, aby policzyć zbiorczy wskaźnik portfela.</div>"
                )
            self._outlook_note.setText("Brak danych do zbiorczej analizy portfela.")
            return

        for suffix, card in self._outlook_cards.items():
            horizon = (summary.get("horizons") or {}).get(suffix) or {}
            label = horizon.get("label") or "—"
            sub = horizon.get("sub") or ""
            win_rate = horizon.get("win_rate")
            avg_return = horizon.get("avg_return")
            coverage = horizon.get("coverage_weight_pct", 0.0)
            sources = horizon.get("source_counts") or {}
            if win_rate is None or avg_return is None:
                card.setText(
                    f"<div style='font-size:11px;font-weight:700;color:#475569'>{label}</div>"
                    f"<div style='font-size:9px;color:#94a3b8;margin-top:2px'>{sub}</div>"
                    "<div style='font-size:18px;font-weight:900;color:#94a3b8;margin-top:12px'>brak</div>"
                    "<div style='font-size:10px;color:#94a3b8;margin-top:4px'>za mało danych</div>"
                )
                continue

            color = "#16a34a" if avg_return >= 1.0 else "#f97316" if avg_return >= 0 else "#dc2626"
            avg_sign = "+" if avg_return >= 0 else ""
            card.setText(
                f"<div style='font-size:11px;font-weight:700;color:#475569'>{label}</div>"
                f"<div style='font-size:9px;color:#94a3b8;margin-top:2px'>{sub}</div>"
                f"<div style='font-size:24px;font-weight:900;color:{color};margin-top:10px'>{win_rate:.0f}%</div>"
                "<div style='font-size:10px;color:#64748b;margin-top:3px'>szansa wzrostu całego portfela</div>"
                f"<div style='font-size:12px;font-weight:800;color:{color};margin-top:5px'>avg: {avg_sign}{avg_return:.2f}%</div>"
                f"<div style='font-size:9px;color:#94a3b8;margin-top:7px'>pokrycie wartości {coverage:.0f}% · traj {sources.get('trajectory', 0)} · setup {sources.get('entry', 0)}</div>"
            )

        covered_counts = [
            str(((summary.get("horizons") or {}).get(suffix) or {}).get("covered_positions", 0))
            for suffix in ("5d", "22d", "66d")
        ]
        self._outlook_note.setText(
            f"Wynik jest ważony obecną wartością pozycji. Najpierw używam analizy rozwoju od zakupu, a brakujące pozycje uzupełniam historią podobnych setupów. Pokrycie pozycji: 1T {covered_counts[0]}/{summary.get('positions_total', 0)}, 1M {covered_counts[1]}/{summary.get('positions_total', 0)}, 3M {covered_counts[2]}/{summary.get('positions_total', 0)}."
        )

    def _refresh_portfolio_outlook(self):
        if not self._holdings:
            self._render_portfolio_outlook({"positions_total": 0, "horizons": {}})
            return

        if self._outlook_worker is not None:
            self._pending_outlook_refresh = True
            return

        self._set_portfolio_outlook_loading()
        self._outlook_worker = PortfolioOutlookWorker(self._holdings_with_live_snapshot())
        self._outlook_worker.done.connect(self._on_portfolio_outlook_done)
        self._outlook_worker.error.connect(self._on_portfolio_outlook_error)
        self._outlook_worker.finished.connect(self._on_portfolio_outlook_finished)
        self._outlook_worker.start()

    def _on_portfolio_outlook_done(self, summary: dict):
        self._render_portfolio_outlook(summary)

    def _on_portfolio_outlook_error(self, message: str):
        self._set_portfolio_outlook_loading("Nie udało się policzyć łącznego wskaźnika portfela. Spróbuj odświeżyć dane lub otworzyć analizę pojedynczej pozycji, aby sprawdzić brakujące tickery.")

    def _on_portfolio_outlook_finished(self):
        self._outlook_worker = None
        if self._pending_outlook_refresh:
            self._pending_outlook_refresh = False
            self._refresh_portfolio_outlook()

    def _on_recommendation_ready(self, run_id: int, holding_id: str, recommendation: str):
        if run_id != self._recommendation_run_id:
            return
        self._recommendations[holding_id] = recommendation
        for index in range(self._list.count()):
            item = self._list.item(index)
            if isinstance(item, HoldingItem) and item.holding_id == holding_id:
                item.apply_recommendation(recommendation)
                self._list.viewport().update()
                break

    def _on_recommendation_done(self, run_id: int):
        if run_id != self._recommendation_run_id:
            return

    def _on_recommendation_worker_finished(self):
        self._recommendation_worker = None

    # ── Button handlers ──────────────────────────────────────────────────────

    def _on_add(self):
        dlg = AddHoldingDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            v = dlg.get_values()
            self._holdings = add_holding(
                v["ticker"], v["shares"], v["avg_price"],
                v["notes"], v["purchase_date"],
            )
            self._refresh_list()
            self._refresh_recommendations()
            self._refresh_portfolio_outlook()

    def _on_edit(self):
        item = self._list.currentItem()
        if not item:
            return
        holding = next((h for h in self._holdings if str(h.get("holding_id") or "") == item.holding_id), None)
        if not holding:
            return
        dlg = AddHoldingDialog(parent=self, existing=holding)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            v = dlg.get_values()
            self._holdings = add_holding(
                v["ticker"], v["shares"], v["avg_price"],
                v["notes"], v["purchase_date"], item.holding_id,
            )
            self._refresh_list()
            self._refresh_recommendations()
            self._refresh_portfolio_outlook()

    def add_from_opportunity(self, ticker: str, price: float | None = None):
        """Open add-holding dialog pre-filled with ticker (called from scanner)."""
        dlg = AddHoldingDialog(parent=self, prefill_ticker=ticker, prefill_price=price)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            v = dlg.get_values()
            self._holdings = add_holding(
                v["ticker"], v["shares"], v["avg_price"],
                v["notes"], v["purchase_date"],
            )
            self._refresh_list()
            self._refresh_recommendations()
            self._refresh_portfolio_outlook()

    def _on_remove(self):
        item = self._list.currentItem()
        if not item:
            return
        holding = next((h for h in self._holdings if str(h.get("holding_id") or "") == item.holding_id), None)
        if not holding:
            return

        exit_price = self._prices.get(item.ticker)
        if exit_price is None:
            from data.fetcher import get_latest_price
            exit_price = get_latest_price(item.ticker, force_refresh=True)

        dlg = CloseHoldingDialog(holding, parent=self, prefill_exit_price=exit_price)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        values = dlg.get_values()
        self._holdings = close_holding(
            item.ticker,
            holding_id=item.holding_id,
            exit_price=values["exit_price"],
            closed_date=values["closed_date"],
            realized_pnl_abs=values["realized_pnl_abs"],
        )
        if not any(str(h.get("ticker") or "").upper() == item.ticker for h in self._holdings):
            self._prices.pop(item.ticker, None)
            self._currencies.pop(item.ticker, None)
        self._recommendations.pop(item.holding_id, None)
        self._refresh_list()
        self._refresh_recommendations()
        self._refresh_portfolio_outlook()
        self._edit_btn.setEnabled(False)
        self._remove_btn.setEnabled(False)

    def _on_refresh(self):
        if not self._holdings:
            return
        self._refresh_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setRange(0, len(self._holdings))
        self._progress.setValue(0)
        self._refresh_worker = PriceRefreshWorker(self._holdings)
        self._refresh_worker.price_updated.connect(self._on_price_updated)
        self._refresh_worker.done.connect(self._on_refresh_done)
        self._refresh_worker.finished.connect(self._on_refresh_worker_finished)
        self._refresh_worker.start()

    def _on_history(self):
        if not self._holdings and not load_closed_positions():
            QMessageBox.information(self, "Historia pozycji", "Dodaj lub zamknij pozycje w portfelu, aby zbudować historię.")
            return
        if self._history_worker and self._history_worker.isRunning():
            return

        self._history_btn.setEnabled(False)
        self._history_btn.setText("⏳  Analiza...")
        self._history_worker = PortfolioHistoryWorker(self._holdings_with_live_snapshot())
        self._history_worker.done.connect(self._on_history_done)
        self._history_worker.error.connect(self._on_history_error)
        self._history_worker.finished.connect(self._on_history_worker_finished)
        self._history_worker.start()

    def _on_history_done(self, data: dict):
        self._history_btn.setEnabled(True)
        self._history_btn.setText("📚  Analiza")
        dlg = PortfolioHistoryDialog(data, parent=self)
        dlg.exec()

    def _on_history_error(self, message: str):
        self._history_btn.setEnabled(True)
        self._history_btn.setText("📚  Analiza")
        QMessageBox.warning(self, "Historia pozycji", f"Nie udało się zbudować historii pozycji.\n\n{message}")

    def _on_ai_advice(self):
        if not self._holdings:
            QMessageBox.information(self, "PORADA AI", "Dodaj pozycje do portfela, aby uruchomić analizę AI.")
            return
        dlg = PortfolioAIAdviceDialog(self._holdings_with_live_snapshot(), parent=self)
        dlg.exec()

    def _on_price_updated(self, ticker: str, price: float, currency: str):
        self._prices[ticker] = price
        self._currencies[ticker] = currency
        self._progress.setValue(self._progress.value() + 1)

    def _on_refresh_done(self):
        self._progress.setVisible(False)
        self._refresh_btn.setEnabled(True)
        self._last_price_snapshot_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._clear_recommendation_state()
        self._refresh_list()
        self._refresh_recommendations()
        self._refresh_portfolio_outlook()

    def _on_refresh_worker_finished(self):
        self._refresh_worker = None

    def _on_history_worker_finished(self):
        self._history_worker = None

    def _on_item_clicked(self, item):
        self._edit_btn.setEnabled(True)
        self._remove_btn.setEnabled(True)
        self.ticker_selected.emit(item.ticker, item.ticker)

    def _on_item_double_clicked(self, item):
        holding = self._find_holding_with_live_snapshot(item.holding_id)
        if holding:
            dlg = PortfolioDetailDialog(holding, parent=self)
            dlg.exec()

    def _shutdown_workers(self):
        for worker in (self._history_worker, self._refresh_worker, self._recommendation_worker, self._outlook_worker):
            if worker and worker.isRunning():
                worker.wait()

    def closeEvent(self, event):
        self._shutdown_workers()
        super().closeEvent(event)
