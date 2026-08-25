"""ui/fundamental_dashboard.py — Dashboard for fundamental stock analysis with Plotly charts."""
import json
import os
import tempfile
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import html

import config


def _fmt_cap(val) -> str:
    if not val:
        return "—"
    try:
        v = float(val)
        if abs(v) >= 1e12: return f"${v/1e12:.2f}T"
        if abs(v) >= 1e9:  return f"${v/1e9:.2f}B"
        if abs(v) >= 1e6:  return f"${v/1e6:.2f}M"
        return f"${v:,.0f}"
    except Exception:
        return "—"


def _fmt(val, dec=2, suffix="") -> str:
    if val is None or pd.isna(val): return "—"
    try: return f"{float(val):,.{dec}f}{suffix}"
    except Exception: return "—"


def _fmt_pct_signed(val, dec=1) -> str:
    if val is None or pd.isna(val):
        return "—"
    try:
        return f"{float(val):+,.{dec}f}%" if float(val) != 0 else "0.0%"
    except Exception:
        return "—"


def _fmt_pct(val, dec=1) -> str:
    if val is None or pd.isna(val):
        return "—"
    try:
        return f"{float(val) * 100:.{dec}f}%"
    except Exception:
        return "—"


def _date_labels(stmts: list) -> list:
    """Return readable date labels for quarterly/annual statements (YYYY-MM)."""
    labels = []
    for s in stmts:
        pe = s.get("period_end", "")
        if len(pe) >= 7:
            labels.append(pe[:7])
        else:
            labels.append(pe[:4])
    return labels


def _quarterly_labels(stmts: list) -> list:
    """Return ordered datetime labels from quarterly statements, newest first."""
    labels = []
    for s in stmts:
        pe = s.get("period_end", "")
        if len(pe) >= 10:
            labels.append(pe[:10])
        else:
            labels.append(pe[:7])
    return labels


def _reverse_stmt(stmts: list) -> list:
    """Reverse chronological to oldest-first for plotting."""
    return list(reversed(stmts))


# ── CHART BUILDERS (quarterly, line-based, with forecasts) ───────────────────

def _chart_revenue_net_income(quarterly_stmt: list, estimates: dict, shares: float, currency: str) -> str:
    if not quarterly_stmt:
        return '<div class="no-data-chart">Brak danych historycznych przychodów i zysków</div>'

    stmt = _reverse_stmt(quarterly_stmt)
    dates = [s["period_end"][:7] for s in stmt]
    revenue = [s.get("revenue") for s in stmt]
    net_income = [s.get("net_income") for s in stmt]

    fig = go.Figure()

    # Historical Revenue (line)
    fig.add_trace(go.Scatter(
        x=dates, y=revenue,
        name="Przychody (kwartalne)",
        line=dict(color="#3b82f6", width=2.5),
        mode="lines+markers",
        marker=dict(size=6, symbol="circle"),
    ))

    # Historical Net Income (line)
    fig.add_trace(go.Scatter(
        x=dates, y=net_income,
        name="Zysk Netto (kwartalny)",
        line=dict(color="#10b981", width=2.5),
        mode="lines+markers",
        marker=dict(size=6, symbol="circle"),
    ))

    # Revenue estimates - quarterly breakdown
    rev_est_data = estimates.get("revenue_estimate", {})
    eps_est_data = estimates.get("earnings_estimate", {})

    est_labels = []
    est_revs = []
    est_incomes = []

    for period_key, label in [("0y", "Bież. rok"), ("+1y", "Przysz. rok")]:
        rev_avg = rev_est_data.get(period_key, {}).get("avg")
        eps_avg = eps_est_data.get(period_key, {}).get("avg")
        if rev_avg and shares:
            est_labels.append(label)
            est_revs.append(rev_avg)
            if eps_avg:
                est_incomes.append(eps_avg * shares)
            else:
                est_incomes.append(None)

    if est_revs:
        fig.add_trace(go.Scatter(
            x=est_labels, y=est_revs,
            name="Przychody (prognoza)",
            line=dict(color="#3b82f6", width=2, dash="dot"),
            mode="lines+markers",
            marker=dict(size=8, symbol="diamond", color="#3b82f6"),
        ))
        if any(v is not None for v in est_incomes):
            fig.add_trace(go.Scatter(
                x=est_labels, y=est_incomes,
                name="Zysk Netto (prognoza)",
                line=dict(color="#10b981", width=2, dash="dot"),
                mode="lines+markers",
                marker=dict(size=8, symbol="diamond", color="#10b981"),
            ))

    fig.update_layout(
        title="Przychody i Zysk Netto (kwartalnie)",
        xaxis_title="Okres",
        yaxis_title=f"Wartość ({currency})",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#f8fafc",
        margin=dict(l=10, r=10, t=40, b=10),
        height=320,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)


def _chart_cash_debt(quarterly_stmt: list, estimates: dict, currency: str) -> str:
    if not quarterly_stmt:
        return '<div class="no-data-chart">Brak danych historycznych bilansu</div>'

    stmt = _reverse_stmt(quarterly_stmt)
    dates = [s["period_end"][:7] for s in stmt]
    cash = [s.get("cash_balance") for s in stmt]
    debt = [s.get("total_debt") for s in stmt]
    equity = [s.get("equity") for s in stmt]

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=dates, y=cash,
        name="Gotówka i Ekwiwalenty",
        line=dict(color="#10b981", width=2.5),
        mode="lines+markers",
        marker=dict(size=6, symbol="circle"),
    ))

    fig.add_trace(go.Scatter(
        x=dates, y=debt,
        name="Dług Całkowity",
        line=dict(color="#ef4444", width=2.5),
        mode="lines+markers",
        marker=dict(size=6, symbol="circle"),
    ))

    if equity:
        fig.add_trace(go.Scatter(
            x=dates, y=equity,
            name="Kapitał Własny",
            line=dict(color="#f59e0b", width=2, dash="dot"),
            mode="lines+markers",
            marker=dict(size=5, symbol="diamond"),
        ))

    fig.update_layout(
        title="Pozycja gotówkowa, zadłużenie i kapitał (kwartalnie)",
        xaxis_title="Okres",
        yaxis_title=f"Wartość ({currency})",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#f8fafc",
        margin=dict(l=10, r=10, t=40, b=10),
        height=320,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)


def _chart_cash_flows(quarterly_stmt: list, estimates: dict, currency: str) -> str:
    if not quarterly_stmt:
        return '<div class="no-data-chart">Brak danych historycznych przepływów</div>'

    stmt = _reverse_stmt(quarterly_stmt)
    dates = [s["period_end"][:7] for s in stmt]
    ocf = [s.get("operating_cashflow") for s in stmt]
    fcf = [s.get("free_cashflow") for s in stmt]
    capex = [s.get("capital_expenditure") for s in stmt]

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=dates, y=ocf,
        name="Gotówka z Operacji (OCF)",
        line=dict(color="#06b6d4", width=2.5),
        mode="lines+markers",
        marker=dict(size=6, symbol="circle"),
    ))

    fig.add_trace(go.Scatter(
        x=dates, y=fcf,
        name="Wolne Przepływy (FCF)",
        line=dict(color="#f59e0b", width=2.5),
        mode="lines+markers",
        marker=dict(size=6, symbol="circle"),
    ))

    if any(c is not None for c in capex):
        fig.add_trace(go.Scatter(
            x=dates, y=capex,
            name="CAPEX",
            line=dict(color="#ef4444", width=1.5, dash="dot"),
            mode="lines+markers",
            marker=dict(size=5, symbol="x"),
        ))

    fig.update_layout(
        title="Przepływy pieniężne (kwartalnie)",
        xaxis_title="Okres",
        yaxis_title=f"Wartość ({currency})",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#f8fafc",
        margin=dict(l=10, r=10, t=40, b=10),
        height=320,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)


def _chart_margins(quarterly_stmt: list, estimates: dict) -> str:
    if not quarterly_stmt:
        return '<div class="no-data-chart">Brak danych historycznych marż</div>'

    stmt = _reverse_stmt(quarterly_stmt)
    dates = [s["period_end"][:7] for s in stmt]
    
    op_margin = []
    net_margin = []
    gross_margin = []
    for s in stmt:
        rev = s.get("revenue") or 0
        op_inc = s.get("operating_income") or 0
        net_inc = s.get("net_income") or 0
        cogs = s.get("cogs") or 0
        if rev > 0:
            op_margin.append((op_inc / rev) * 100)
            net_margin.append((net_inc / rev) * 100)
        else:
            op_margin.append(None)
            net_margin.append(None)

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=dates, y=op_margin,
        name="Marża Operacyjna",
        line=dict(color="#f59e0b", width=2.5),
        mode="lines+markers",
        marker=dict(size=6, symbol="circle"),
    ))

    fig.add_trace(go.Scatter(
        x=dates, y=net_margin,
        name="Marża Netto",
        line=dict(color="#10b981", width=2.5),
        mode="lines+markers",
        marker=dict(size=6, symbol="circle"),
    ))

    fig.update_layout(
        title="Marże rentowności (kwartalnie, %)",
        xaxis_title="Okres",
        yaxis_title="Procent (%)",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#f8fafc",
        margin=dict(l=10, r=10, t=40, b=10),
        height=320,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)


def _chart_ps_ratio(ticker: str, quarterly_stmt: list, estimates: dict, shares: float, currency: str, info: dict) -> str:
    """Build a P/S (Price-to-Sales) ratio chart over historical quarters with forecasts."""
    if not quarterly_stmt:
        return '<div class="no-data-chart">Brak danych historycznych do wyliczenia P/S</div>'

    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        df_hist = tk.history(period="6y")
        if df_hist is None or df_hist.empty:
            raise ValueError("No historical price data")
        df_hist.index = pd.to_datetime(df_hist.index).tz_localize(None)
    except Exception as e:
        return f'<div class="no-data-chart">Nie udało się pobrać historycznych cen: {e}</div>'

    stmt = _reverse_stmt(quarterly_stmt)
    years = []
    ps_values = []
    rev_values = []
    ps_current = info.get("price_to_sales")

    for s in stmt:
        period_end = s.get("period_end", "")
        rev = s.get("revenue")
        if not period_end or not rev or rev <= 0:
            continue
        try:
            date = pd.Timestamp(period_end)
        except Exception:
            continue

        mask = df_hist.index <= date
        if not mask.any():
            continue
        closest_idx = df_hist.index[mask][-1]
        price = float(df_hist.loc[closest_idx, "Close"])

        if shares and shares > 0:
            market_cap = price * shares
            ps = market_cap / rev
            years.append(period_end[:7])
            ps_values.append(ps)
            rev_values.append(rev / 1e9)

    if not years:
        return '<div class="no-data-chart">Nie można obliczyć P/S dla żadnego okresu</div>'

    fig = go.Figure()

    # Revenue bars (transparent, secondary axis for context)
    fig.add_trace(go.Scatter(
        x=years,
        y=rev_values,
        name=f"Przychody (mlrd {currency})",
        yaxis="y2",
        line=dict(color="#3b82f6", width=1.5, dash="dot"),
        mode="lines+markers",
        marker=dict(size=5, symbol="diamond", color="#3b82f6"),
        opacity=0.5,
    ))

    # P/S line
    fig.add_trace(go.Scatter(
        x=years,
        y=ps_values,
        name="P/S (Cena/Sprzedaż)",
        line=dict(color="#f59e0b", width=3),
        mode="lines+markers",
        marker=dict(size=8, symbol="circle"),
    ))

    # Current TTM P/S as horizontal dashed line
    if ps_current is not None and ps_current > 0:
        fig.add_hline(
            y=ps_current,
            line_dash="dash",
            line_color="#ef4444",
            opacity=0.7,
            annotation_text=f"P/S TTM: {ps_current:.2f}x",
            annotation_position="top left",
        )

    # Forward P/S estimates
    rev_est_data = estimates.get("revenue_estimate", {})
    est_labels = []
    est_ps_values = []
    for period_key, label in [("0y", "Bież. rok"), ("+1y", "Przysz. rok")]:
        rev_avg = rev_est_data.get(period_key, {}).get("avg")
        if rev_avg and shares and rev_avg > 0:
            try:
                last_price = float(df_hist["Close"].iloc[-1])
                ps_est = (last_price * shares) / rev_avg
                est_labels.append(label)
                est_ps_values.append(ps_est)
            except Exception:
                pass

    if est_labels:
        fig.add_trace(go.Scatter(
            x=est_labels,
            y=est_ps_values,
            name="Prognozowane P/S",
            line=dict(color="#f59e0b", width=2.5, dash="dot"),
            mode="lines+markers",
            marker=dict(size=10, symbol="diamond", color="#f59e0b"),
        ))

    fig.update_layout(
        title="Historyczny i prognozowany wskaźnik P/S (kwartalnie)",
        xaxis_title="Okres",
        yaxis=dict(
            title="P/S (x)",
            side="left",
            showgrid=True,
            zeroline=True,
        ),
        yaxis2=dict(
            title=f"Przychody (mlrd {currency})",
            side="right",
            overlaying="y",
            showgrid=False,
            zeroline=False,
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#f8fafc",
        margin=dict(l=10, r=10, t=40, b=10),
        height=320,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)


def _chart_targets(targets: dict, current_price: float, currency: str) -> str:
    if not targets or "mean" not in targets:
        return '<div class="no-data-chart">Brak prognoz analityków / celów cenowych dla tego instrumentu</div>'

    low = targets.get("low")
    high = targets.get("high")
    mean = targets.get("mean")
    median = targets.get("median") or mean

    fig = go.Figure()

    # Price range as area
    if low and high:
        fig.add_trace(go.Scatter(
            x=["Zakres"],
            y=[high],
            name="Max Target",
            mode="markers",
            marker=dict(color="rgba(59, 130, 246, 0.3)", size=20, symbol="square"),
            showlegend=False,
        ))
        fig.add_trace(go.Scatter(
            x=["Zakres"],
            y=[low],
            name="Min Target",
            mode="markers",
            marker=dict(color="rgba(59, 130, 246, 0.3)", size=20, symbol="square"),
            fill="tonexty",
            fillcolor="rgba(59, 130, 246, 0.15)",
            line=dict(color="rgba(59, 130, 246, 0.5)", width=1),
        ))

    # Mean Target
    fig.add_trace(go.Scatter(
        x=["Średni"], y=[mean],
        name="Średni Target",
        mode="markers",
        marker=dict(color="#3b82f6", size=14, symbol="diamond"),
    ))

    # Median Target
    fig.add_trace(go.Scatter(
        x=["Mediana"], y=[median],
        name="Mediana Targetu",
        mode="markers",
        marker=dict(color="#f59e0b", size=12, symbol="x"),
    ))

    # Current Price
    fig.add_trace(go.Scatter(
        x=["Obecna"], y=[current_price],
        name=f"Obecna Cena ({_fmt(current_price)} {currency})",
        mode="markers",
        marker=dict(color="#10b981" if current_price <= mean else "#ef4444", size=16, symbol="circle"),
    ))

    fig.update_layout(
        title="Prognozy analityków vs Obecna Cena",
        yaxis_title=f"Cena ({currency})",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#f8fafc",
        margin=dict(l=10, r=10, t=40, b=10),
        height=320,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)


# ── DASHBOARD COMPILER ───────────────────────────────────────────────────────

def _build_fundamental_html(data: dict, ticker: str, plotly_path: str = "") -> str:
    """Build a complete fundamental analysis dashboard HTML."""
    # Load Plotly.js inline from local file if not passed as param
    if not plotly_path:
        plotly_path = os.path.join(os.path.dirname(__file__), "..", "assets", "plotly.min.js")
    if os.path.isfile(plotly_path):
        try:
            with open(plotly_path, "r") as f:
                plotly_js = f.read()
        except Exception:
            plotly_js = ""
    else:
        plotly_js = ""
    
    fundamental_data = data.get("fundamental_data") or {}
    info = fundamental_data.get("info") or data.get("info") or {}
    history = fundamental_data.get("history") or data.get("history") or {}
    estimates = fundamental_data.get("estimates") or data.get("estimates") or {}

    currency = html.escape(str(info.get("currency") or "USD"))
    name = html.escape(str(info.get("name") or ticker))

    # Use quarterly data (newest first), reverse for plotting
    quarterly_stmt = list(reversed(history.get("quarterly", [])))

    price = data.get("price") or data.get("live_price_snapshot") or info.get("currentPrice")
    if not price:
        holding = data.get("holding") or {}
        price = holding.get("live_price_snapshot") or holding.get("avg_price")

    price_val = float(price) if price else 0.0
    shares = info.get("shares_outstanding") or 0.0

    # 1. P/S analysis
    ps_trailing = info.get("price_to_sales")
    est_rev_0y = estimates.get("revenue_estimate", {}).get("0y", {}).get("avg")
    est_rev_1y = estimates.get("revenue_estimate", {}).get("+1y", {}).get("avg")
    ps_0y_val = (price_val * shares / est_rev_0y) if (price_val and shares and est_rev_0y) else None
    ps_1y_val = (price_val * shares / est_rev_1y) if (price_val and shares and est_rev_1y) else None

    # 2. Analyst targets
    targets = estimates.get("targets") or {}
    target_mean = targets.get("mean")
    target_upside = ((target_mean - price_val) / price_val * 100) if (target_mean and price_val) else None
    analyst_opinion_count = info.get("analyst_count")

    # 3. Cash & Debt Net
    cash_val = info.get("total_cash")
    debt_val = info.get("total_debt")
    net_cash_debt = (cash_val - debt_val) if (cash_val is not None and debt_val is not None) else None

    # 4. Generate Charts (quarterly, line-based)
    chart_rev_html = _chart_revenue_net_income(quarterly_stmt, estimates, shares, currency)
    chart_cash_debt_html = _chart_cash_debt(quarterly_stmt, estimates, currency)
    chart_cash_flows_html = _chart_cash_flows(quarterly_stmt, estimates, currency)
    chart_margins_html = _chart_margins(quarterly_stmt, estimates)
    chart_targets_html = _chart_targets(targets, price_val, currency)
    chart_ps_html = _chart_ps_ratio(ticker, quarterly_stmt, estimates, shares, currency, info)

    # Compile the HTML page
    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Analiza Fundamentalna — {ticker}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script id="plotly-inline-js">
{plotly_js}
</script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f8fafc;
    color: #0f172a;
    font-size: 13px;
    line-height: 1.5;
    padding: 16px;
  }}
  .container {{ max-width: 960px; margin: 0 auto; }}
  
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 16px;
    margin-bottom: 20px;
  }}
  
  .plotly-inline {{
    display: none;
  }}
  
  .card {{
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 16px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.02);
  }}
  
  .card-title {{
    font-size: 11px;
    font-weight: 700;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }}
  
  .card-value {{
    font-size: 20px;
    font-weight: 800;
    color: #0f172a;
    margin-bottom: 6px;
  }}
  
  .card-subtext {{
    font-size: 11px;
    color: #64748b;
  }}
  
  .metric-row {{
    display: flex;
    justify-content: space-between;
    padding: 6px 0;
    border-bottom: 1px solid #f1f5f9;
  }}
  .metric-row:last-child {{
    border-bottom: none;
  }}
  .metric-label {{
    color: #64748b;
  }}
  .metric-value {{
    font-weight: 600;
    color: #0f172a;
  }}
  
  .section-header {{
    margin-bottom: 16px;
    padding-bottom: 8px;
    border-bottom: 2px solid #e2e8f0;
  }}
  .section-title {{
    font-size: 16px;
    font-weight: 700;
    color: #0f172a;
  }}
  
  .chart-selector-wrapper {{
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 20px;
  }}
  .chart-controls {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 16px;
  }}
  .chart-controls label {{
    font-weight: 600;
    color: #334155;
  }}
  .chart-controls select {{
    padding: 8px 12px;
    font-family: inherit;
    font-size: 13px;
    font-weight: 500;
    color: #0f172a;
    background: #f8fafc;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    outline: none;
    cursor: pointer;
  }}
  .chart-controls select:focus {{
    border-color: #3b82f6;
  }}
  
  .chart-container {{
    min-height: 340px;
    background: #f8fafc;
    border-radius: 8px;
    padding: 8px;
    border: 1px dashed #e2e8f0;
  }}
  
  .no-data-chart {{
    display: flex;
    align-items: center;
    justify-content: center;
    height: 320px;
    color: #64748b;
    font-weight: 500;
    font-style: italic;
  }}
  
  .badge {{
    display: inline-block;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 700;
  }}
  .badge-positive {{
    background: #dcfce7;
    color: #15803d;
  }}
  .badge-negative {{
    background: #fee2e2;
    color: #b91c1c;
  }}
</style>
</head>
<body>
<div class="container">

  <!-- TOP HEADER METRICS -->
  <div class="grid">
    <div class="card">
      <div class="card-title">
        <span>Wycena & P/S</span>
        <span class="badge badge-positive">Mnożniki</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">P/S (TTM)</span>
        <span class="metric-value">{_fmt(ps_trailing)}x</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">Prog. P/S (Bież. rok)</span>
        <span class="metric-value">{_fmt(ps_0y_val)}x</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">Prog. P/S (Przysz. rok)</span>
        <span class="metric-value">{_fmt(ps_1y_val)}x</span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">
        <span>Targety analityków</span>
        <span class="badge badge-positive">Konsensus</span>
      </div>
      <div class="card-value">
        {_fmt(target_mean)} {currency}
      </div>
      <div class="card-subtext">
        Upside: <span style="font-weight:700; color:{'#15803d' if (target_upside or 0) >= 0 else '#b91c1c'}">{_fmt_pct_signed(target_upside, 1)}</span> 
        ({_fmt(analyst_opinion_count, 0)} opinii)
      </div>
    </div>

    <div class="card">
      <div class="card-title">
        <span>Płynność netto</span>
        <span class="badge {'badge-positive' if (net_cash_debt or 0) >= 0 else 'badge-negative'}">Bilans</span>
      </div>
      <div class="card-value" style="color: {'#15803d' if (net_cash_debt or 0) >= 0 else '#b91c1c'}">
        {_fmt_cap(net_cash_debt)}
      </div>
      <div class="card-subtext">
        Gotówka: {_fmt_cap(cash_val)} · Dług: {_fmt_cap(debt_val)}
      </div>
    </div>
  </div>

  <!-- INTERACTIVE CHART BLOCK -->
  <div class="chart-selector-wrapper">
    <div class="chart-controls">
      <label for="chart-select">Wykres:</label>
      <select id="chart-select" onchange="switchChart(this.value)">
        <option value="revenue_net_income">Przychody i Zysk Netto</option>
        <option value="cash_debt">Gotówka i Zadłużenie</option>
        <option value="cash_flows">Przepływy Pieniężne (Cash Flow)</option>
        <option value="margins">Marże Rentowności</option>
        <option value="targets">Cel Cenowy Analityków</option>
        <option value="ps_ratio">Wskaźnik P/S (Cena/Sprzedaż)</option>
      </select>
    </div>
    
    <div class="chart-container">
      <div id="chart-revenue_net_income" class="fundamental-chart" style="display: block;">
        {chart_rev_html}
      </div>
      <div id="chart-cash_debt" class="fundamental-chart" style="display: none;">
        {chart_cash_debt_html}
      </div>
      <div id="chart-cash_flows" class="fundamental-chart" style="display: none;">
        {chart_cash_flows_html}
      </div>
      <div id="chart-margins" class="fundamental-chart" style="display: none;">
        {chart_margins_html}
      </div>
      <div id="chart-targets" class="fundamental-chart" style="display: none;">
        {chart_targets_html}
      </div>
      <div id="chart-ps_ratio" class="fundamental-chart" style="display: none;">
        {chart_ps_html}
      </div>
    </div>
  </div>

  <!-- EXPANDED METRICS DETAILS GRID -->
  <div class="section-header">
    <span class="section-title">Wskaźniki Szczegółowe</span>
  </div>
  
  <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));">
    <div class="card">
      <div class="card-title">Mnożniki wyceny</div>
      <div class="metric-row"><span class="metric-label">Kapitalizacja rynkowa</span><span class="metric-value">{_fmt_cap(info.get("market_cap"))}</span></div>
      <div class="metric-row"><span class="metric-label">Wartość firmy (EV)</span><span class="metric-value">{_fmt_cap(info.get("enterprise_value"))}</span></div>
      <div class="metric-row"><span class="metric-label">Cena / Zysk (P/E)</span><span class="metric-value">{_fmt(info.get("pe_ratio"))}x</span></div>
      <div class="metric-row"><span class="metric-label">Prognozowane P/E</span><span class="metric-value">{_fmt(info.get("forward_pe"))}x</span></div>
      <div class="metric-row"><span class="metric-label">Cena / Wartość Księgowa (P/B)</span><span class="metric-value">{_fmt(info.get("price_to_book"))}x</span></div>
      <div class="metric-row"><span class="metric-label">Wskaźnik PEG</span><span class="metric-value">{_fmt(info.get("peg_ratio"))}x</span></div>
      <div class="metric-row"><span class="metric-label">EV / EBITDA</span><span class="metric-value">{_fmt(info.get("enterprise_to_ebitda"))}x</span></div>
      <div class="metric-row"><span class="metric-label">EV / Przychody</span><span class="metric-value">{_fmt(info.get("enterprise_to_revenue"))}x</span></div>
    </div>

    <div class="card">
      <div class="card-title">Rentowność i Wzrost</div>
      <div class="metric-row"><span class="metric-label">Marża Brutto</span><span class="metric-value">{_fmt_pct_signed(info.get("gross_margin") * 100 if info.get("gross_margin") is not None else None, 1)}</span></div>
      <div class="metric-row"><span class="metric-label">Marża Operacyjna</span><span class="metric-value">{_fmt_pct_signed(info.get("operating_margin") * 100 if info.get("operating_margin") is not None else None, 1)}</span></div>
      <div class="metric-row"><span class="metric-label">Marża Zysku Netto</span><span class="metric-value">{_fmt_pct_signed(info.get("profit_margin") * 100 if info.get("profit_margin") is not None else None, 1)}</span></div>
      <div class="metric-row"><span class="metric-label">Zwrot z Kapitału (ROE)</span><span class="metric-value">{_fmt_pct_signed(info.get("roe") * 100 if info.get("roe") is not None else None, 1)}</span></div>
      <div class="metric-row"><span class="metric-label">Zwrot z Aktywów (ROA)</span><span class="metric-value">{_fmt_pct_signed(info.get("roa") * 100 if info.get("roa") is not None else None, 1)}</span></div>
      <div class="metric-row"><span class="metric-label">Wzrost Przychodu (r/r)</span><span class="metric-value">{_fmt_pct_signed(info.get("revenue_growth") * 100 if info.get("revenue_growth") is not None else None, 1)}</span></div>
      <div class="metric-row"><span class="metric-label">Wzrost Zysku (r/r)</span><span class="metric-value">{_fmt_pct_signed(info.get("earnings_growth") * 100 if info.get("earnings_growth") is not None else None, 1)}</span></div>
    </div>

    <div class="card">
      <div class="card-title">Pozycja bilansowa i Gotówka</div>
      <div class="metric-row"><span class="metric-label">Gotówka Operacyjna (OCF)</span><span class="metric-value">{_fmt_cap(info.get("operating_cashflow"))}</span></div>
      <div class="metric-row"><span class="metric-label">Wolne Przepływy (FCF)</span><span class="metric-value">{_fmt_cap(info.get("free_cashflow"))}</span></div>
      <div class="metric-row"><span class="metric-label">Current Ratio (Płynność)</span><span class="metric-value">{_fmt(info.get("current_ratio"), 2)}</span></div>
      <div class="metric-row"><span class="metric-label">Quick Ratio (Szybka płynność)</span><span class="metric-value">{_fmt(info.get("quick_ratio"), 2)}</span></div>
      <div class="metric-row"><span class="metric-label">Wskaźnik zadłużenia (D/E)</span><span class="metric-value">{_fmt(info.get("debt_to_equity"), 1)}</span></div>
      <div class="metric-row"><span class="metric-label">Stopa Dywidendy</span><span class="metric-value">{_fmt_pct(info.get("dividend_yield"), 2)}</span></div>
      <div class="metric-row"><span class="metric-label">Payout Ratio (Wypłata zysku)</span><span class="metric-value">{_fmt_pct(info.get("payout_ratio"), 1)}</span></div>
      <div class="metric-row"><span class="metric-label">Wycena/Akcja (Book Value)</span><span class="metric-value">{_fmt(info.get("book_value"))} {currency}</span></div>
    </div>
  </div>

</div>

<script>
function switchChart(chartId) {{
  var charts = document.getElementsByClassName('fundamental-chart');
  for (var i = 0; i < charts.length; i++) {{
    charts[i].style.display = 'none';
  }}
  document.getElementById('chart-' + chartId).style.display = 'block';
  setTimeout(function() {{
    window.dispatchEvent(new Event('resize'));
  }}, 10);
}}
</script>
</body>
</html>
"""
    return html_content