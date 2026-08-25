"""ui/chart_view.py — Interactive Plotly candlestick chart rendered in QWebEngineView."""
import sys
import os
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl
import config


def _build_chart_html(
    df: pd.DataFrame,
    ticker: str,
    show_sma: list[int] | None = None,
    interval: str = "1d",
) -> str:
    """Build full HTML with embedded Plotly chart."""
    show_sma = show_sma or [20, 50, 200]
    interval_label = "4H" if interval == "4h" else "1D"

    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.60, 0.20, 0.20],
        vertical_spacing=0.02,
        subplot_titles=[f"{ticker} · {interval_label}", "Volume", "RSI"],
    )

    # ── Candlestick ─────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"],
        name=ticker,
        increasing_line_color=config.COLOR_UP,
        decreasing_line_color=config.COLOR_DOWN,
        increasing_fillcolor=config.COLOR_UP,
        decreasing_fillcolor=config.COLOR_DOWN,
        line_width=1,
    ), row=1, col=1)

    # ── SMAs ────────────────────────────────────────────────
    sma_colors = {20: config.COLOR_SMA20, 50: config.COLOR_SMA50, 200: config.COLOR_SMA200}
    sma_cols   = {20: "SMA20",            50: "SMA50",            200: "SMA200"}
    for period in show_sma:
        col = sma_cols.get(period)
        if col and col in df.columns:
            fig.add_trace(go.Scatter(
                x=df.index, y=df[col],
                name=f"SMA{period}",
                line=dict(color=sma_colors[period], width=1.2),
                mode="lines",
            ), row=1, col=1)

    # ── Volume bars ─────────────────────────────────────────
    colors = [
        config.COLOR_UP if c >= o else config.COLOR_DOWN
        for c, o in zip(df["Close"], df["Open"])
    ]
    fig.add_trace(go.Bar(
        x=df.index, y=df["Volume"],
        name="Volume",
        marker_color=colors,
        showlegend=False,
    ), row=2, col=1)

    # ── RSI ─────────────────────────────────────────────────
    if "RSI14" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["RSI14"],
            name="RSI(14)",
            line=dict(color=config.COLOR_ACCENT, width=1.2),
            mode="lines",
        ), row=3, col=1)
        # Overbought/oversold bands
        for level, color in [(70, config.COLOR_DOWN), (30, config.COLOR_UP)]:
            fig.add_hline(y=level, line_dash="dot", line_color=color,
                          line_width=0.8, row=3, col=1)

    # ── Layout ───────────────────────────────────────────────
    fig.update_layout(
        paper_bgcolor=config.COLOR_BG2,
        plot_bgcolor=config.COLOR_BG,
        font=dict(color=config.COLOR_TEXT, family="Helvetica Neue, Arial, sans-serif", size=11),
        margin=dict(l=10, r=10, t=30, b=10),
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.01,
            xanchor="right", x=1,
            bgcolor="rgba(255,255,255,0.8)",
            bordercolor=config.COLOR_BORDER,
            borderwidth=1,
            font=dict(size=10),
        ),
        xaxis_rangeslider_visible=False,
    )

    # For 4H: hide weekends + overnight gaps so candles look evenly spaced
    if interval == "4h":
        fig.update_xaxes(
            rangebreaks=[
                dict(bounds=["sat", "mon"]),         # hide weekends
                dict(bounds=[20, 9], pattern="hour"), # hide 20:00–09:00 overnight
            ]
        )
        # Zoom in to last 60 days so 4H candles are clearly visible
        if len(df) > 0:
            x_end = df.index[-1]
            x_start = x_end - pd.Timedelta(days=60)
            fig.update_xaxes(range=[str(x_start), str(x_end)])

    fig.update_xaxes(
        showgrid=True, gridcolor=config.COLOR_GRID, gridwidth=0.5,
        showline=True, linecolor=config.COLOR_BORDER, zeroline=False,
        tickfont=dict(size=10),
    )
    fig.update_yaxes(
        showgrid=True, gridcolor=config.COLOR_GRID, gridwidth=0.5,
        showline=True, linecolor=config.COLOR_BORDER, zeroline=False,
        tickfont=dict(size=10),
    )
    # RSI y-axis range
    fig.update_yaxes(range=[0, 100], row=3, col=1)

    html = fig.to_html(
        full_html=True,
        include_plotlyjs=True,
        config={"displayModeBar": True, "scrollZoom": True},
    )
    return html


class ChartView(QWebEngineView):
    """QWebEngineView subclass that renders a Plotly candlestick chart."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._temp_file = None
        self._show_empty()

    def _html_page(self, body_style: str, message: str) -> str:
        return (
            f"<html><body style=\"{body_style}\">{message}</body></html>"
        )

    def _show_empty(self):
        self.setHtml(
            f"<html><body style='background:{config.COLOR_BG2};"
            f"display:flex;align-items:center;justify-content:center;height:100vh;"
            f"color:{config.COLOR_SUBTEXT};font-family:sans-serif;font-size:16px;'>"
            f"Wybierz instrument z panelu po lewej stronie</body></html>"
        )

    def load_chart(
        self,
        df: pd.DataFrame,
        ticker: str,
        show_sma: list[int] | None = None,
        interval: str = "1d",
    ):
        """Render chart for given OHLCV DataFrame via temp file (avoids 2MB setHtml limit)."""
        html = _build_chart_html(df, ticker, show_sma, interval=interval)
        # Write to temp file — bypasses QWebEngineView 2MB setHtml() limit
        if self._temp_file and os.path.exists(self._temp_file):
            try:
                os.unlink(self._temp_file)
            except OSError:
                pass
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html)
            self._temp_file = f.name
        self.load(QUrl.fromLocalFile(self._temp_file))

    def show_error(self, message: str):
        self.setHtml(
            f"<html><body style='background:{config.COLOR_BG2};"
            f"display:flex;align-items:center;justify-content:center;height:100vh;"
            f"color:{config.COLOR_DOWN};font-family:sans-serif;font-size:14px;'>"
            f"⚠ {message}</body></html>"
        )
