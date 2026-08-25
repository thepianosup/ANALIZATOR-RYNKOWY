"""data/fetcher.py — Fetch OHLCV and info data via yfinance with SQLite cache."""
import logging
import time
import random
import threading
import yfinance as yf
import pandas as pd
from typing import Optional
from datetime import datetime
from contextlib import contextmanager
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from data import cache

# ── yfinance API rate limiter ───────────────────────────────────────────────
# Prevents overwhelming Yahoo Finance with concurrent requests.
# Limits concurrent calls to _YF_MAX_CONCURRENT and enforces a minimum interval
# between each call to stay within undocumented API rate limits.
_YF_MAX_CONCURRENT = 3
_YF_MIN_INTERVAL = 0.25  # seconds between consecutive API calls
_yf_semaphore = threading.Semaphore(_YF_MAX_CONCURRENT)
_yf_last_call = 0.0
_yf_rate_lock = threading.Lock()


def _yf_throttle():
    """Throttle yfinance API calls: limit concurrency and enforce minimum interval."""
    _yf_semaphore.acquire()
    with _yf_rate_lock:
        global _yf_last_call
        now = time.time()
        elapsed = now - _yf_last_call
        if elapsed < _YF_MIN_INTERVAL:
            time.sleep(_YF_MIN_INTERVAL - elapsed + random.uniform(0, 0.05))
        _yf_last_call = time.time()


def _yf_release():
    """Release the yfinance concurrency slot."""
    _yf_semaphore.release()


def _yf_fetch_history(ticker: str, period: str = "1y", interval: str = "1d",
                      max_retries: int = 3) -> Optional[pd.DataFrame]:
    """Fetch yfinance history with rate limiting and retry on rate-limit errors."""
    for attempt in range(max_retries):
        _yf_throttle()
        try:
            tk = yf.Ticker(ticker)
            with _suppress_yfinance_history_noise():
                df = tk.history(period=period, interval=interval, auto_adjust=True)
            return df
        except Exception as e:
            err_str = str(e).lower()
            if "too many requests" in err_str or "rate limited" in err_str:
                wait = (2 ** attempt) + random.uniform(0.5, 1.5)
                print(f"[yf_retry] Rate limited for {ticker}, retry {attempt + 1}/{max_retries} "
                      f"in {wait:.1f}s...")
                time.sleep(wait)
            else:
                print(f"[fetcher] Error fetching history for {ticker} (attempt {attempt + 1}): {e}")
                return None
        finally:
            _yf_release()
    print(f"[fetcher] Error fetching {ticker}: Too Many Requests after {max_retries} retries")
    return None



@contextmanager
def _suppress_yfinance_history_noise():
    """Mute false-positive yfinance history error logs for tickers that still return data."""
    logger = logging.getLogger("yfinance")
    prev_disabled = logger.disabled
    prev_level = logger.level
    logger.disabled = False
    logger.setLevel(max(prev_level, logging.CRITICAL))
    try:
        yield
    finally:
        logger.setLevel(prev_level)
        logger.disabled = prev_disabled


def _clean_info_value(value, default=None):
    return value if value not in (None, "None", "") else default


def _safe_number(value):
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_count(value):
    number = _safe_number(value)
    return int(number) if number is not None else None


def _get_frame_attr(ticker_obj, attr_name: str) -> pd.DataFrame:
    try:
        frame = getattr(ticker_obj, attr_name, None)
        if isinstance(frame, pd.DataFrame):
            return frame
    except Exception:
        pass
    return pd.DataFrame()


def _coerce_statement_timestamp(column) -> pd.Timestamp | None:
    try:
        ts = pd.Timestamp(column)
        if ts.tzinfo is not None:
            ts = ts.tz_convert(None)
        return ts
    except Exception:
        return None


def _statement_sort_key(column):
    ts = _coerce_statement_timestamp(column)
    return ts.to_pydatetime() if ts is not None else datetime.min


def _statement_period_label(column) -> str:
    ts = _coerce_statement_timestamp(column)
    if ts is not None:
        return ts.date().isoformat()
    return str(column)


def _statement_value(frame: pd.DataFrame, row_names: tuple[str, ...], column):
    if frame.empty or column not in frame.columns:
        return None
    for row_name in row_names:
        if row_name in frame.index:
            return _safe_number(frame.at[row_name, column])
    return None


def _build_statement_records(income: pd.DataFrame, cashflow: pd.DataFrame, balance: pd.DataFrame) -> list[dict]:
    columns = sorted(
        set(income.columns).union(cashflow.columns).union(balance.columns),
        key=_statement_sort_key,
        reverse=True,
    )
    records: list[dict] = []
    for column in columns:
        record = {
            "period_end": _statement_period_label(column),
            "revenue": _statement_value(income, ("Total Revenue", "Operating Revenue"), column),
            "net_income": _statement_value(income, ("Net Income Common Stockholders", "Net Income"), column),
            "operating_income": _statement_value(income, ("Total Operating Income As Reported", "Operating Income"), column),
            "ebitda": _statement_value(income, ("EBITDA", "Normalized EBITDA"), column),
            "diluted_eps": _statement_value(income, ("Diluted EPS", "Basic EPS"), column),
            "operating_cashflow": _statement_value(cashflow, ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities"), column),
            "free_cashflow": _statement_value(cashflow, ("Free Cash Flow",), column),
            "capital_expenditure": _statement_value(cashflow, ("Capital Expenditure",), column),
            "cash_balance": _statement_value(balance, ("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"), column),
            "total_debt": _statement_value(balance, ("Total Debt",), column),
            "current_assets": _statement_value(balance, ("Current Assets",), column),
            "current_liabilities": _statement_value(balance, ("Current Liabilities",), column),
            "equity": _statement_value(balance, ("Stockholders Equity", "Common Stock Equity"), column),
        }
        if any(record[key] is not None for key in record if key != "period_end"):
            records.append(record)
    return records


def _parse_major_holders(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {}
    value_col = frame.columns[0]
    raw = frame[value_col].to_dict()
    return {
        "insiders_pct": _safe_number(raw.get("insidersPercentHeld")),
        "institutions_pct": _safe_number(raw.get("institutionsPercentHeld")),
        "institutions_float_pct": _safe_number(raw.get("institutionsFloatPercentHeld")),
        "institutions_count": _safe_count(raw.get("institutionsCount")),
    }


def _parse_institutional_holders(frame: pd.DataFrame) -> dict:
    if frame.empty or "pctHeld" not in frame.columns:
        return {}
    ordered = frame.sort_values(by="pctHeld", ascending=False)
    return {
        "institutional_holders_count": int(len(frame.index)),
        "top5_institutional_pct": round(float(ordered["pctHeld"].fillna(0).head(5).sum()), 4),
    }


def _parse_insider_purchases(frame: pd.DataFrame) -> dict:
    if frame.empty or frame.shape[1] < 2:
        return {}
    label_col = frame.columns[0]
    parsed: dict = {}
    for _, row in frame.iterrows():
        label = str(row.get(label_col) or "").strip().lower()
        if not label:
            continue
        if label == "purchases":
            parsed["insider_purchase_transactions_6m"] = _safe_count(row.get("Trans"))
            parsed["insider_purchase_shares_6m"] = _safe_number(row.get("Shares"))
        elif label == "sales":
            parsed["insider_sale_transactions_6m"] = _safe_count(row.get("Trans"))
            parsed["insider_sale_shares_6m"] = _safe_number(row.get("Shares"))
        elif "net shares purchased" in label:
            parsed["insider_net_shares_6m"] = _safe_number(row.get("Shares"))
        elif "total insider shares held" in label:
            parsed["insider_total_shares_held"] = _safe_number(row.get("Shares"))
    return parsed


def _parse_insider_transactions(frame: pd.DataFrame) -> dict:
    if frame.empty or "Text" not in frame.columns or "Shares" not in frame.columns:
        return {}
    text_series = frame["Text"].fillna("").astype(str).str.lower()
    shares_series = frame["Shares"].apply(_safe_number)
    buy_mask = text_series.str.contains("purchase|buy", regex=True)
    sale_mask = text_series.str.contains("sale|sell", regex=True)
    recent_buy_shares = shares_series[buy_mask].dropna().sum() if buy_mask.any() else 0.0
    recent_sale_shares = shares_series[sale_mask].dropna().sum() if sale_mask.any() else 0.0
    return {
        "recent_insider_purchase_count": int(buy_mask.sum()),
        "recent_insider_sale_count": int(sale_mask.sum()),
        "recent_insider_net_shares": round(float(recent_buy_shares - recent_sale_shares), 0),
    }


def get_ohlcv(ticker: str, period: str = "1y") -> Optional[pd.DataFrame]:
    """
    Return OHLCV DataFrame for ticker/period (daily candles).
    Columns: Open, High, Low, Close, Volume (DatetimeIndex).
    Uses cache with TTL = CACHE_TTL_SECONDS.
    """
    cache_key = f"ohlcv:{ticker}:{period}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        df = _yf_fetch_history(ticker, period=period, interval="1d")
        if df is None or df.empty:
            return None
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        cache.set(cache_key, df, config.CACHE_TTL_SECONDS)
        return df
    except Exception as e:
        print(f"[fetcher] Error fetching {ticker}: {e}")
        return None


def get_ohlcv_4h(ticker: str, period: str = "6mo") -> Optional[pd.DataFrame]:
    """
    Return OHLCV DataFrame with 4-hour candles.
    Fetches 1h data from yfinance and resamples to 4h.
    yfinance 1h interval max is ~730 days; periods beyond that return 2y of data.
    Cache TTL = 4 hours.
    """
    # Cap period to 2y since yfinance 1h limit is 730 days
    safe_period = period if period not in ("5y", "max") else "2y"
    cache_key = f"ohlcv_4h:{ticker}:{safe_period}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        df = _yf_fetch_history(ticker, period=safe_period, interval="1h")
        if df is None or df.empty:
            return None
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        # Resample 1h → 4h
        df_4h = df.resample("4h").agg({
            "Open":   "first",
            "High":   "max",
            "Low":    "min",
            "Close":  "last",
            "Volume": "sum",
        }).dropna(subset=["Open", "Close"])
        if df_4h.empty:
            return None
        cache.set(cache_key, df_4h, config.CACHE_TTL_4H_SECONDS)
        return df_4h
    except Exception as e:
        print(f"[fetcher] Error fetching 4h data for {ticker}: {e}")
        return None


def get_info(ticker: str) -> dict:
    """
    Return key fundamental info for ticker.
    Cached for TTL seconds.
    """
    cache_key = f"info:v3:{ticker}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = {}
    try:
        _yf_throttle()
        try:
            tk = yf.Ticker(ticker)
            info = tk.info or {}
        finally:
            _yf_release()

        result = {
            "name":               _clean_info_value(info.get("longName")) or _clean_info_value(info.get("shortName")) or ticker,
            "currency":           _clean_info_value(info.get("currency"), "USD"),
            "sector":             _clean_info_value(info.get("sector"), "—"),
            "industry":           _clean_info_value(info.get("industry"), "—"),
            "market_cap":         _clean_info_value(info.get("marketCap")),
            "enterprise_value":   _clean_info_value(info.get("enterpriseValue")),
            "pe_ratio":           _clean_info_value(info.get("trailingPE")),
            "forward_pe":         _clean_info_value(info.get("forwardPE")),
            "peg_ratio":          _clean_info_value(info.get("pegRatio")) if _clean_info_value(info.get("pegRatio")) is not None else _clean_info_value(info.get("trailingPegRatio")),
            "eps":                _clean_info_value(info.get("trailingEps")),
            "forward_eps":        _clean_info_value(info.get("forwardEps")),
            "book_value":         _clean_info_value(info.get("bookValue")),
            "price_to_book":      _clean_info_value(info.get("priceToBook")),
            "price_to_sales":     _clean_info_value(info.get("priceToSalesTrailing12Months")),
            "enterprise_to_ebitda": _clean_info_value(info.get("enterpriseToEbitda")),
            "enterprise_to_revenue": _clean_info_value(info.get("enterpriseToRevenue")),
            "dividend_yield":     _clean_info_value(info.get("dividendYield")),
            "52w_high":           _clean_info_value(info.get("fiftyTwoWeekHigh")),
            "52w_low":            _clean_info_value(info.get("fiftyTwoWeekLow")),
            "avg_volume":         _clean_info_value(info.get("averageVolume")),
            "beta":               _clean_info_value(info.get("beta")),
            "exchange":           _clean_info_value(info.get("exchange"), ""),
            "quote_type":         _clean_info_value(info.get("quoteType"), ""),
            "revenue_growth":     _clean_info_value(info.get("revenueGrowth")),
            "earnings_growth":    _clean_info_value(info.get("earningsGrowth")),
            "profit_margin":      _clean_info_value(info.get("profitMargins")),
            "operating_margin":   _clean_info_value(info.get("operatingMargins")),
            "gross_margin":       _clean_info_value(info.get("grossMargins")),
            "roe":                _clean_info_value(info.get("returnOnEquity")),
            "roa":                _clean_info_value(info.get("returnOnAssets")),
            "debt_to_equity":     _clean_info_value(info.get("debtToEquity")),
            "current_ratio":      _clean_info_value(info.get("currentRatio")),
            "quick_ratio":        _clean_info_value(info.get("quickRatio")),
            "total_cash":         _clean_info_value(info.get("totalCash")),
            "total_debt":         _clean_info_value(info.get("totalDebt")),
            "ebitda":             _clean_info_value(info.get("ebitda")),
            "total_revenue":      _clean_info_value(info.get("totalRevenue")),
            "net_income":         _clean_info_value(info.get("netIncomeToCommon")),
            "shares_outstanding": _clean_info_value(info.get("sharesOutstanding")),
            "free_cashflow":      _clean_info_value(info.get("freeCashflow")),
            "operating_cashflow": _clean_info_value(info.get("operatingCashflow")),
            "dividend_rate":      _clean_info_value(info.get("dividendRate")),
            "payout_ratio":       _clean_info_value(info.get("payoutRatio")),
            "held_percent_insiders": _clean_info_value(info.get("heldPercentInsiders")),
            "held_percent_institutions": _clean_info_value(info.get("heldPercentInstitutions")),
            "recommendation_key": _clean_info_value(info.get("recommendationKey")),
            "target_mean_price":  _clean_info_value(info.get("targetMeanPrice")),
            "analyst_count":      _clean_info_value(info.get("numberOfAnalystOpinions")),
        }
        cache.set(cache_key, result, config.CACHE_TTL_SECONDS)
    except Exception as e:
        print(f"[fetcher] Error fetching info for {ticker}: {e}")
        result = {"name": ticker}

    return result


def get_fundamental_history(ticker: str) -> dict:
    """Return cached quarterly and annual financial statement history normalized to plain dicts."""
    cache_key = f"fundamental_history:v1:{ticker}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = {"quarterly": [], "annual": [], "quarterly_periods": 0, "annual_periods": 0}
    try:
        _yf_throttle()
        try:
            tk = yf.Ticker(ticker)
            quarterly = _build_statement_records(
                _get_frame_attr(tk, "quarterly_income_stmt"),
                _get_frame_attr(tk, "quarterly_cashflow"),
                _get_frame_attr(tk, "quarterly_balance_sheet"),
            )
            annual = _build_statement_records(
                _get_frame_attr(tk, "income_stmt"),
                _get_frame_attr(tk, "cashflow"),
                _get_frame_attr(tk, "balance_sheet"),
            )
        finally:
            _yf_release()
        result = {
            "quarterly": quarterly[:8],
            "annual": annual[:6],
            "quarterly_periods": len(quarterly),
            "annual_periods": len(annual),
        }
        cache.set(cache_key, result, config.CACHE_TTL_SECONDS)
    except Exception as e:
        print(f"[fetcher] Error fetching financial history for {ticker}: {e}")
    return result


def get_ownership_snapshot(ticker: str) -> dict:
    """Return cached ownership and insider activity snapshot when Yahoo provides the data."""
    cache_key = f"ownership_snapshot:v1:{ticker}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result: dict = {}
    try:
        _yf_throttle()
        try:
            tk = yf.Ticker(ticker)
            result = {
                **_parse_major_holders(_get_frame_attr(tk, "major_holders")),
                **_parse_institutional_holders(_get_frame_attr(tk, "institutional_holders")),
                **_parse_insider_purchases(_get_frame_attr(tk, "insider_purchases")),
                **_parse_insider_transactions(_get_frame_attr(tk, "insider_transactions")),
            }
        finally:
            _yf_release()
        cache.set(cache_key, result, config.CACHE_TTL_SECONDS)
    except Exception as e:
        print(f"[fetcher] Error fetching ownership snapshot for {ticker}: {e}")
    return result


def get_company_news(ticker: str, limit: int = config.COMPANY_NEWS_LIMIT) -> list[dict]:
    """
    Return a compact list of recent Yahoo Finance news entries for ticker.
    Cached for the standard info TTL.
    """
    safe_limit = max(1, int(limit or config.COMPANY_NEWS_LIMIT))
    cache_key = f"company_news:v1:{ticker}:{safe_limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    items: list[dict] = []
    try:
        _yf_throttle()
        try:
            tk = yf.Ticker(ticker)
            raw_items = tk.news or []
        finally:
            _yf_release()

        for raw in raw_items:
            content = raw.get("content") or {}
            provider = content.get("provider") or {}
            title = _clean_info_value(content.get("title"))
            summary = _clean_info_value(content.get("summary"), "")
            if not title and not summary:
                continue

            pub_date = _clean_info_value(content.get("pubDate"), "")
            parsed_at = None
            if pub_date:
                try:
                    parsed_at = datetime.fromisoformat(pub_date.replace("Z", "+00:00")).isoformat()
                except ValueError:
                    parsed_at = pub_date

            items.append({
                "title": title or "Bez tytułu",
                "summary": summary,
                "provider": provider.get("displayName") or "Yahoo Finance",
                "published_at": parsed_at or "",
                "content_type": _clean_info_value(content.get("contentType"), "ARTICLE"),
            })
            if len(items) >= safe_limit:
                break

        cache.set(cache_key, items, config.CACHE_TTL_SECONDS)
    except Exception as e:
        print(f"[fetcher] Error fetching news for {ticker}: {e}")
        items = []

    return items


def get_latest_price(ticker: str, force_refresh: bool = False) -> Optional[float]:
    """
    Return the latest price for a ticker with a 15-minute cache TTL.
    Uses 1-minute intraday data (last 1 day) for maximum freshness.
    Falls back to last close from daily data if intraday is unavailable.
    """
    cache_key = f"price_15m:{ticker}"
    if not force_refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return float(cached)

    price = None
    try:
        _yf_throttle()
        try:
            tk = yf.Ticker(ticker)
            # Try fast_info first (no network roundtrip beyond metadata)
            fi = tk.fast_info
            p = getattr(fi, "last_price", None)
            if p and p > 0:
                price = float(p)
            else:
                # Fallback: fetch last hour of 1m data
                with _suppress_yfinance_history_noise():
                    df = tk.history(period="1d", interval="5m", auto_adjust=True)
                if df is not None and not df.empty:
                    price = float(df["Close"].iloc[-1])
        finally:
            _yf_release()
    except Exception as e:
        print(f"[fetcher] Error fetching latest price for {ticker}: {e}")

    if price is not None:
        cache.set(cache_key, price, config.CACHE_TTL_PRICE_SECONDS)
    return price


def get_fx_rate(base_currency: str, quote_currency: str = "USD") -> Optional[float]:
    """Return the latest FX rate for base/quote using Yahoo symbols like EURUSD=X."""
    base = str(base_currency or "").strip().upper()
    quote = str(quote_currency or "").strip().upper()
    if not base or not quote:
        return None
    if base == quote:
        return 1.0

    direct = get_latest_price(f"{base}{quote}=X")
    if direct and direct > 0:
        return float(direct)

    inverse = get_latest_price(f"{quote}{base}=X")
    if inverse and inverse > 0:
        return float(1.0 / inverse)

    return None


def get_fundamental_extended_data(ticker: str) -> dict:
    """
    Return analyst price targets, revenue estimates, earnings estimates,
    and growth estimates for a ticker. Cached for standard TTL.
    """
    cache_key = f"fundamental_extended:v2:{ticker}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = {
        "targets": {},
        "revenue_estimate": {},
        "earnings_estimate": {},
        "growth_estimates": {}
    }
    
    try:
        _yf_throttle()
        try:
            tk = yf.Ticker(ticker)
            
            # Targets
            try:
                targets = tk.analyst_price_targets
                if isinstance(targets, dict):
                    result["targets"] = targets
            except Exception as e:
                print(f"[fetcher] Error getting analyst_price_targets for {ticker}: {e}")
                
            # Revenue estimate
            try:
                rev_est = tk.revenue_estimate
                if isinstance(rev_est, pd.DataFrame) and not rev_est.empty:
                    result["revenue_estimate"] = rev_est.to_dict(orient="index")
            except Exception as e:
                print(f"[fetcher] Error getting revenue_estimate for {ticker}: {e}")
                
            # Earnings estimate
            try:
                eps_est = tk.earnings_estimate
                if isinstance(eps_est, pd.DataFrame) and not eps_est.empty:
                    result["earnings_estimate"] = eps_est.to_dict(orient="index")
            except Exception as e:
                print(f"[fetcher] Error getting earnings_estimate for {ticker}: {e}")
                
            # Growth estimates
            try:
                growth_est = tk.growth_estimates
                if isinstance(growth_est, pd.DataFrame) and not growth_est.empty:
                    result["growth_estimates"] = growth_est.to_dict(orient="index")
            except Exception as e:
                print(f"[fetcher] Error getting growth_estimates for {ticker}: {e}")
        finally:
            _yf_release()
        cache.set(cache_key, result, config.CACHE_TTL_SECONDS)
    except Exception as e:
        print(f"[fetcher] Error fetching extended fundamentals for {ticker}: {e}")
        
    return result


def search_tickers(query: str) -> list[dict]:
    """
    Search for tickers by name or symbol using yfinance search.
    Returns list of {ticker, name, exchange, type}.
    """
    if not query or len(query) < 1:
        return []
    try:
        _yf_throttle()
        try:
            results = yf.Search(query, max_results=15)
        finally:
            _yf_release()
        quotes = results.quotes if hasattr(results, "quotes") else []
        out = []
        for q in quotes:
            quoteType = q.get("quoteType", "").upper()
            exchange = q.get("exchange", "")
            symbol = q.get("symbol", "")
            name = q.get("longname") or q.get("shortname") or symbol
            if not symbol:
                continue
            out.append({
                "ticker":   symbol,
                "name":     name,
                "exchange": exchange,
                "type":     quoteType,
            })
        return out
    except Exception as e:
        print(f"[fetcher] Search error: {e}")
        return []
