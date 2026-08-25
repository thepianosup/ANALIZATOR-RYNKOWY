"""analysis/indicators.py — Technical analysis indicators computed on OHLCV DataFrames."""
import pandas as pd
import numpy as np


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Returns (macd_line, signal_line, histogram) as pd.Series."""
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0):
    """Returns (upper, middle, lower) Bollinger Bands as pd.Series."""
    middle = sma(series, period)
    std = series.rolling(window=period, min_periods=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range — measures volatility."""
    high = df["High"]
    low  = df["Low"]
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3):
    """Returns (%K, %D) Stochastic Oscillator."""
    low_min  = df["Low"].rolling(k_period).min()
    high_max = df["High"].rolling(k_period).max()
    denom = (high_max - low_min).replace(0, np.nan)
    k = 100 * (df["Close"] - low_min) / denom
    d = k.rolling(d_period).mean()
    return k, d


def rate_of_change(series: pd.Series, period: int = 20) -> pd.Series:
    """Rate of Change (momentum) — percentage change over `period` bars."""
    return series.pct_change(periods=period) * 100


def williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Williams %R oscillator."""
    high_max = df["High"].rolling(period).max()
    low_min  = df["Low"].rolling(period).min()
    denom = (high_max - low_min).replace(0, np.nan)
    return -100 * (high_max - df["Close"]) / denom


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average Directional Index (ADX) — measures trend strength regardless of direction.
    ADX > 20: trending; ADX > 25: strong trend; ADX < 20: ranging/sideways.
    """
    high = df["High"]
    low  = df["Low"]
    close = df["Close"]

    up_move   = high.diff()
    down_move = -low.diff()

    plus_dm  = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index
    )

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr14  = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di  = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr14.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr14.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add all technical indicator columns to a copy of df.
    Requires columns: Open, High, Low, Close, Volume.
    """
    df = df.copy()
    # Trend
    df["SMA20"]  = sma(df["Close"], 20)
    df["SMA50"]  = sma(df["Close"], 50)
    df["SMA200"] = sma(df["Close"], 200)
    df["EMA20"]  = ema(df["Close"], 20)

    # Momentum
    df["RSI14"]  = rsi(df["Close"], 14)
    df["MACD"], df["MACD_Signal"], df["MACD_Hist"] = macd(df["Close"])
    df["ROC20"]  = rate_of_change(df["Close"], 20)
    df["WilliamsR"] = williams_r(df)

    # Volatility / Bollinger
    df["BB_Upper"], df["BB_Mid"], df["BB_Lower"] = bollinger_bands(df["Close"], 20)
    df["ATR14"] = atr(df, 14)

    # Stochastic
    df["Stoch_K"], df["Stoch_D"] = stochastic(df)

    # Volume
    df["Vol_SMA50"] = sma(df["Volume"], 50)
    df["Vol_SMA20"] = sma(df["Volume"], 20)

    # Trend strength
    df["ADX14"] = adx(df, 14)

    return df
