"""ui/stats_panel.py — Bottom stats panel showing key metrics for selected instrument."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QFrame, QScrollArea, QSizePolicy
from PyQt6.QtCore import Qt
import config


def _fmt_large(val) -> str:
    if val is None:
        return "—"
    try:
        v = float(val)
        if v >= 1e12:
            return f"{v/1e12:.2f}T"
        if v >= 1e9:
            return f"{v/1e9:.2f}B"
        if v >= 1e6:
            return f"{v/1e6:.2f}M"
        return f"{v:,.0f}"
    except Exception:
        return "—"


def _fmt_pct(val) -> str:
    if val is None:
        return "—"
    try:
        return f"{float(val)*100:.2f}%"
    except Exception:
        return "—"


def _fmt_float(val, decimals=2) -> str:
    if val is None:
        return "—"
    try:
        return f"{float(val):,.{decimals}f}"
    except Exception:
        return "—"


class _Stat(QWidget):
    def __init__(self, label: str, value: str = "—"):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        lbl = QLabel(label)
        lbl.setObjectName("statLabel")
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight)

        self._val = QLabel(value)
        self._val.setObjectName("statValue")
        self._val.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._val.setMinimumWidth(70)

        layout.addWidget(lbl)
        layout.addWidget(self._val)

    def set_value(self, v: str):
        self._val.setText(v)


class StatsPanel(QScrollArea):
    """Horizontal scrollable bar showing key fundamentals + price stats."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StatsPanelContainer")
        self.setFixedHeight(54)
        self.setWidgetResizable(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        container.setObjectName("StatsPanel")
        self.setWidget(container)

        layout = QHBoxLayout(container)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(0)

        self._stats: dict[str, _Stat] = {}
        fields = [
            ("name",     "Nazwa"),
            ("price",    "Cena"),
            ("macro",    "Makro"),
            ("company",  "Spółka"),
            ("52w_high", "52W Max"),
            ("52w_low",  "52W Min"),
            ("pe",       "P/E"),
            ("eps",      "EPS"),
            ("div",      "Dywidenda"),
            ("mktcap",   "Kapitalizacja"),
            ("beta",     "Beta"),
            ("sector",   "Sektor"),
        ]
        for i, (key, label) in enumerate(fields):
            stat = _Stat(label)
            self._stats[key] = stat
            layout.addWidget(stat)
            if i < len(fields) - 1:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.VLine)
                sep.setObjectName("statSep")
                layout.addWidget(sep)

        layout.addStretch()

    def update_info(self, info: dict, last_price: float | None = None, macro: dict | None = None, company: dict | None = None):
        currency = info.get("currency", "")
        price_str = f"{_fmt_float(last_price)} {currency}" if last_price else "—"
        macro_str = "—"
        company_str = "—"
        if macro and macro.get("score") is not None:
            macro_str = f"{int(macro['score'])}/100"
        if company and company.get("score") is not None:
            company_str = f"{int(company['score'])}/100"

        self._stats["name"].set_value(info.get("name", "—")[:30])
        self._stats["price"].set_value(price_str)
        self._stats["macro"].set_value(macro_str)
        self._stats["company"].set_value(company_str)
        self._stats["52w_high"].set_value(_fmt_float(info.get("52w_high")))
        self._stats["52w_low"].set_value(_fmt_float(info.get("52w_low")))
        self._stats["pe"].set_value(_fmt_float(info.get("pe_ratio"), 1))
        self._stats["eps"].set_value(_fmt_float(info.get("eps")))
        self._stats["div"].set_value(_fmt_pct(info.get("dividend_yield")))
        self._stats["mktcap"].set_value(_fmt_large(info.get("market_cap")))
        self._stats["beta"].set_value(_fmt_float(info.get("beta")))
        self._stats["sector"].set_value(info.get("sector", "—")[:18])

    def clear(self):
        for stat in self._stats.values():
            stat.set_value("—")
