"""ui/main_window.py — Main application window."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QSplitter, QSplitterHandle, QComboBox, QLabel, QCheckBox, QPushButton,
    QFrame, QSizePolicy, QScrollArea,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QWheelEvent
import config
from .sidebar import Sidebar
from .chart_view import ChartView
from .stats_panel import StatsPanel
from data.fetcher import get_ohlcv, get_ohlcv_4h, get_info
from analysis.indicators import add_indicators


class _SplitterHandle(QSplitterHandle):
    """Custom handle that blocks wheel events so two-finger trackpad scroll
    never accidentally moves the divider. Single-finger drag works normally."""

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        event.accept()


class _Splitter(QSplitter):
    """QSplitter with a custom handle that blocks wheel scroll events."""

    def createHandle(self) -> QSplitterHandle:  # type: ignore[override]
        return _SplitterHandle(self.orientation(), self)

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        event.accept()


class _ShrinkableScrollArea(QScrollArea):
    """Scroll area whose minimum width does not constrain the splitter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._sync_widget_size()

    def setWidget(self, widget: QWidget) -> None:  # type: ignore[override]
        super().setWidget(widget)
        self._sync_widget_size()

    def _sync_widget_size(self) -> None:
        widget = self.widget()
        if widget is None:
            return
        hint = widget.sizeHint()
        widget.resize(max(self.viewport().width(), hint.width()), hint.height())


class DataLoader(QThread):
    """Background thread: fetch OHLCV + info for a ticker/period/interval."""
    done = pyqtSignal(object, dict, dict, dict, str, str)   # df, info, macro, company, ticker, period
    error = pyqtSignal(str)

    # Extra data to fetch for SMA200 warmup, keyed by display period
    _FETCH_PERIOD = {
        "1mo": "1y",
        "3mo": "1y",
        "6mo": "2y",
        "1y":  "3y",
        "2y":  "5y",
        "5y":  "max",
    }
    # Approximate calendar days for display period (used for trimming)
    _DISPLAY_DAYS = {
        "1mo": 31,
        "3mo": 92,
        "6mo": 184,
        "1y":  366,
        "2y":  730,
        "5y":  1826,
    }

    def __init__(self, ticker: str, period: str, interval: str = "1d"):
        super().__init__()
        self._ticker = ticker
        self._period = period
        self._interval = interval

    def run(self):
        import pandas as pd
        from analysis.company_analysis import get_company_snapshot
        from analysis.macro_analysis import get_macro_snapshot

        fetch_period = self._FETCH_PERIOD.get(self._period, self._period)
        if self._interval == "4h":
            df = get_ohlcv_4h(self._ticker, fetch_period)
        else:
            df = get_ohlcv(self._ticker, fetch_period)
        if df is None or df.empty:
            self.error.emit(f"Brak danych dla {self._ticker}")
            return
        df = add_indicators(df)
        # Trim to the requested display period (SMA200 warmup already done above)
        if self._period in self._DISPLAY_DAYS:
            cutoff = df.index[-1] - pd.DateOffset(days=self._DISPLAY_DAYS[self._period])
            df = df[df.index > cutoff]
        info = get_info(self._ticker)
        macro = get_macro_snapshot(self._ticker, info=info)
        last_price = float(df["Close"].iloc[-1]) if not df.empty else None
        company = get_company_snapshot(self._ticker, info=info, price=last_price)
        self.done.emit(df, info, macro, company, self._ticker, self._period)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{config.APP_NAME} v{config.APP_VERSION}")
        self.resize(1400, 820)
        self.setMinimumSize(900, 600)

        self._current_ticker = ""
        self._current_period = config.DEFAULT_PERIOD
        self._loader: DataLoader | None = None

        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Splitter: sidebar | main ────────────────────────────────────────
        splitter = _Splitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("mainSplitter")
        splitter.setHandleWidth(10)

        # Sidebar
        self._sidebar = Sidebar()
        splitter.addWidget(self._sidebar)

        # Main area: toolbar + chart + stats
        right = QWidget()
        right.setObjectName("rightPanel")
        right.setMinimumWidth(0)
        right.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Toolbar
        toolbar = self._build_toolbar()
        right_layout.addWidget(toolbar)

        # Chart
        self._chart = ChartView()
        right_layout.addWidget(self._chart, stretch=1)

        # Stats panel
        self._stats = StatsPanel()
        right_layout.addWidget(self._stats)

        splitter.addWidget(right)
        splitter.setSizes([300, 1100])
        splitter.setStretchFactor(0, 0)  # sidebar: fixed width when window resizes
        splitter.setStretchFactor(1, 1)  # right panel: absorbs all extra space
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)

        root.addWidget(splitter)

        # Connect sidebar
        self._sidebar.ticker_selected.connect(self._on_ticker_selected)

    def _build_toolbar(self) -> QWidget:
        scroll = _ShrinkableScrollArea()
        scroll.setObjectName("toolbarScroll")
        scroll.setFixedHeight(48)

        bar = QFrame()
        bar.setObjectName("toolbar")
        bar.setFixedHeight(44)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        # Ticker label
        self._ticker_label = QLabel("—")
        self._ticker_label.setObjectName("tickerLabel")
        layout.addWidget(self._ticker_label)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setObjectName("toolbarSep")
        layout.addWidget(sep)

        # Period selector
        period_label = QLabel("Okres:")
        period_label.setObjectName("toolbarLabel")
        layout.addWidget(period_label)

        self._period_combo = QComboBox()
        self._period_combo.setObjectName("periodCombo")
        for period, label in zip(config.CHART_PERIODS, config.CHART_PERIOD_LABELS):
            self._period_combo.addItem(label, period)
        # Default to 1y
        idx = config.CHART_PERIODS.index(config.DEFAULT_PERIOD)
        self._period_combo.setCurrentIndex(idx)
        self._period_combo.currentIndexChanged.connect(self._on_period_changed)
        layout.addWidget(self._period_combo)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setObjectName("toolbarSep")
        layout.addWidget(sep2)

        # SMA toggles
        sma_label = QLabel("SMA:")
        sma_label.setObjectName("toolbarLabel")
        layout.addWidget(sma_label)

        self._sma_checks: dict[int, QCheckBox] = {}
        for period, checked in [(20, True), (50, True), (200, True)]:
            cb = QCheckBox(str(period))
            cb.setObjectName("smaCheck")
            cb.setMinimumWidth(50)
            cb.setChecked(checked)
            cb.stateChanged.connect(self._on_sma_changed)
            self._sma_checks[period] = cb
            layout.addWidget(cb)

        layout.addStretch()

        # Refresh button
        self._refresh_btn = QPushButton("⟳  Odśwież")
        self._refresh_btn.setObjectName("refreshBtn")
        self._refresh_btn.clicked.connect(self._reload_chart)
        layout.addWidget(self._refresh_btn)

        scroll.setWidget(bar)
        return scroll

    def _on_ticker_selected(self, ticker: str, name: str):
        self._current_ticker = ticker
        self._ticker_label.setText(f"  {ticker}  —  {name}")
        self._reload_chart()

    def _on_period_changed(self, _):
        self._current_period = self._period_combo.currentData()
        self._reload_chart()

    def _on_sma_changed(self, _):
        self._reload_chart()

    def _reload_chart(self):
        if not self._current_ticker:
            return
        if self._loader and self._loader.isRunning():
            self._loader.terminate()
        self._chart.setHtml(
            f"<html><body style='background:{config.COLOR_BG2};"
            f"display:flex;align-items:center;justify-content:center;height:100vh;"
            f"color:{config.COLOR_SUBTEXT};font-family:sans-serif;font-size:14px;'>"
            f"⏳ Ładowanie {self._current_ticker}…</body></html>"
        )
        self._loader = DataLoader(self._current_ticker, self._current_period)
        self._loader.done.connect(self._on_data_loaded)
        self._loader.error.connect(self._chart.show_error)
        self._loader.start()

    def _on_data_loaded(self, df, info: dict, macro: dict, company: dict, ticker: str, period: str):
        show_sma = [p for p, cb in self._sma_checks.items() if cb.isChecked()]
        self._chart.load_chart(df, ticker, show_sma=show_sma)
        last_price = float(df["Close"].iloc[-1]) if not df.empty else None
        self._stats.update_info(info, last_price, macro, company)
