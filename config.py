# config.py — Analizator Rynkowy configuration

APP_NAME = "Analizator Rynkowy"
APP_VERSION = "1.1.0"

# Cache TTL in seconds (1 day)
CACHE_TTL_SECONDS = 86400

# Cache TTL for intraday / 4h data (4 hours)
CACHE_TTL_4H_SECONDS = 14400

# Cache TTL for latest price (15 minutes — reduces cross-view price drift)
CACHE_TTL_PRICE_SECONDS = 900

# Cache TTL for macro regime snapshots (6 hours)
MACRO_CACHE_TTL_SECONDS = 21600

# Scanner concurrency: number of tickers processed in parallel
SCANNER_CONCURRENCY = 3

# Default chart period
DEFAULT_PERIOD = "1y"

# Chart periods available in UI
CHART_PERIODS = ["1mo", "3mo", "6mo", "1y", "2y", "5y"]
CHART_PERIOD_LABELS = ["1M", "3M", "6M", "1R", "2L", "5L"]

# Popular US indices
INDICES_US = [
    {"ticker": "^GSPC",  "name": "S&P 500"},
    {"ticker": "^DJI",   "name": "Dow Jones Industrial"},
    {"ticker": "^IXIC",  "name": "NASDAQ Composite"},
    {"ticker": "^NDX",   "name": "NASDAQ-100"},
    {"ticker": "^RUT",   "name": "Russell 2000"},
    {"ticker": "^VIX",   "name": "VIX (Volatility Index)"},
    {"ticker": "^SOX",   "name": "Philadelphia Semiconductor"},
    {"ticker": "^SP500-45", "name": "S&P 500 IT Sector"},
]

# Popular Polish indices
INDICES_PL = [
    {"ticker": "^WIG20",   "name": "WIG20"},
    {"ticker": "WIG.WA",   "name": "WIG"},
    {"ticker": "^MWIG40",  "name": "mWIG40"},
    {"ticker": "^SWIG80",  "name": "sWIG80"},
]

# International indices
INDICES_INTL = [
    {"ticker": "^FTSE",   "name": "FTSE 100 (UK)"},
    {"ticker": "^GDAXI",  "name": "DAX (Niemcy)"},
    {"ticker": "^FCHI",   "name": "CAC 40 (Francja)"},
    {"ticker": "^N225",   "name": "Nikkei 225 (Japonia)"},
    {"ticker": "^HSI",    "name": "Hang Seng (HK)"},
    {"ticker": "^STOXX50E","name": "Euro Stoxx 50"},
]

# Broad-market & asset-class ETFs
ETFS_BROAD = [
    {"ticker": "SPY",   "name": "SPDR S&P 500"},
    {"ticker": "VOO",   "name": "Vanguard S&P 500"},
    {"ticker": "IVV",   "name": "iShares Core S&P 500"},
    {"ticker": "VTI",   "name": "Vanguard Total Stock Market"},
    {"ticker": "QQQ",   "name": "Invesco QQQ (NASDAQ-100)"},
    {"ticker": "QQQM",  "name": "Invesco NASDAQ-100 (mini)"},
    {"ticker": "IWM",   "name": "iShares Russell 2000"},
    {"ticker": "DIA",   "name": "SPDR Dow Jones"},
    {"ticker": "VEA",   "name": "Vanguard Developed Markets"},
    {"ticker": "EFA",   "name": "iShares MSCI EAFE"},
    {"ticker": "EEM",   "name": "iShares MSCI Emerging Markets"},
    {"ticker": "VWO",   "name": "Vanguard Emerging Markets"},
    {"ticker": "ACWI",  "name": "iShares MSCI ACWI"},
]

# Sector ETFs (SPDR)
ETFS_SECTOR = [
    {"ticker": "XLK",  "name": "Technology (XLK)"},
    {"ticker": "XLE",  "name": "Energy (XLE)"},
    {"ticker": "XLF",  "name": "Financials (XLF)"},
    {"ticker": "XLV",  "name": "Health Care (XLV)"},
    {"ticker": "XLY",  "name": "Consumer Discret. (XLY)"},
    {"ticker": "XLP",  "name": "Consumer Staples (XLP)"},
    {"ticker": "XLI",  "name": "Industrials (XLI)"},
    {"ticker": "XLB",  "name": "Materials (XLB)"},
    {"ticker": "XLRE", "name": "Real Estate (XLRE)"},
    {"ticker": "XLU",  "name": "Utilities (XLU)"},
    {"ticker": "XLC",  "name": "Communication Services (XLC)"},
    {"ticker": "SMH",  "name": "VanEck Semiconductor"},
    {"ticker": "IBB",  "name": "iShares Biotech"},
    {"ticker": "XBI",  "name": "SPDR S&P Biotech"},
    {"ticker": "ITB",  "name": "iShares US Home Construction"},
    {"ticker": "KRE",  "name": "SPDR Regional Banking"},
]

# Bond / commodity / alternative ETFs
ETFS_OTHER = [
    {"ticker": "TLT",   "name": "iShares 20Y Treasury"},
    {"ticker": "IEF",   "name": "iShares 7-10Y Treasury"},
    {"ticker": "SHY",   "name": "iShares 1-3Y Treasury"},
    {"ticker": "HYG",   "name": "iShares High Yield Bond"},
    {"ticker": "LQD",   "name": "iShares Investment Grade Corp"},
    {"ticker": "GLD",   "name": "SPDR Gold"},
    {"ticker": "IAU",   "name": "iShares Gold"},
    {"ticker": "SLV",   "name": "iShares Silver"},
    {"ticker": "USO",   "name": "US Oil Fund"},
    {"ticker": "UNG",   "name": "US Natural Gas Fund"},
    {"ticker": "DBC",   "name": "Invesco DB Commodity"},
    {"ticker": "VNQ",   "name": "Vanguard Real Estate"},
]

# Thematic / leveraged ETFs
ETFS_THEMATIC = [
    {"ticker": "ARKK",  "name": "ARK Innovation"},
    {"ticker": "ARKG",  "name": "ARK Genomic Revolution"},
    {"ticker": "ARKW",  "name": "ARK Next Gen Internet"},
    {"ticker": "BOTZ",  "name": "Global X Robotics & AI"},
    {"ticker": "AIQ",   "name": "Global X AI & Big Data"},
    {"ticker": "ICLN",  "name": "iShares Global Clean Energy"},
    {"ticker": "JETS",  "name": "US Global Jets (Airlines)"},
    {"ticker": "KWEB",  "name": "KraneShares China Internet"},
    {"ticker": "SOXL",  "name": "Direxion Semis 3x Bull"},
    {"ticker": "TQQQ",  "name": "ProShares Ultra QQQ 3x"},
    {"ticker": "SPXL",  "name": "Direxion S&P 500 3x Bull"},
    {"ticker": "SQQQ",  "name": "ProShares UltraPro Short QQQ"},
]

# All ETFs combined
ETFS = ETFS_BROAD + ETFS_SECTOR + ETFS_OTHER + ETFS_THEMATIC

# Polish WIG20 + mWIG40 blue chips
STOCKS_PL = [
    # WIG20
    {"ticker": "PKN.WA",  "name": "PKN Orlen"},
    {"ticker": "PKO.WA",  "name": "PKO BP"},
    {"ticker": "PZU.WA",  "name": "PZU"},
    {"ticker": "KGH.WA",  "name": "KGHM"},
    {"ticker": "LPP.WA",  "name": "LPP"},
    {"ticker": "DNP.WA",  "name": "Dino Polska"},
    {"ticker": "ALE.WA",  "name": "Allegro"},
    {"ticker": "CDR.WA",  "name": "CD Projekt"},
    {"ticker": "MBK.WA",  "name": "mBank"},
    {"ticker": "PEO.WA",  "name": "Bank Pekao"},
    {"ticker": "CPS.WA",  "name": "Cyfrowy Polsat"},
    {"ticker": "JSW.WA",  "name": "JSW"},
    {"ticker": "TPE.WA",  "name": "Tauron"},
    {"ticker": "PGE.WA",  "name": "PGE"},
    {"ticker": "OPL.WA",  "name": "Orange Polska"},
    {"ticker": "BDX.WA",  "name": "Budimex"},
    {"ticker": "PCO.WA",  "name": "Pepco"},
    # mWIG40
    {"ticker": "AMC.WA",  "name": "Ameco"},
    {"ticker": "ATT.WA",  "name": "Atende"},
    {"ticker": "EUR.WA",  "name": "Eurocash"},
    {"ticker": "INK.WA",  "name": "Inpost (IPC)"},
    {"ticker": "KER.WA",  "name": "Kernel"},
    {"ticker": "MRC.WA",  "name": "Mercator Medical"},
    {"ticker": "TEN.WA",  "name": "Tende"},
    {"ticker": "VRG.WA",  "name": "Vigo Photonics"},
    {"ticker": "SNT.WA",  "name": "Sanok RC"},
    {"ticker": "PCR.WA",  "name": "Polskie Centrum Recyklingu"},
]

# US stocks — S&P 500 top 80 by market cap / popularity
STOCKS_US_DEFAULT = [
    # Mega caps
    {"ticker": "AAPL",  "name": "Apple"},
    {"ticker": "MSFT",  "name": "Microsoft"},
    {"ticker": "NVDA",  "name": "NVIDIA"},
    {"ticker": "GOOGL", "name": "Alphabet (A)"},
    {"ticker": "GOOG",  "name": "Alphabet (C)"},
    {"ticker": "AMZN",  "name": "Amazon"},
    {"ticker": "META",  "name": "Meta Platforms"},
    {"ticker": "TSLA",  "name": "Tesla"},
    {"ticker": "AVGO",  "name": "Broadcom"},
    {"ticker": "BRK-B", "name": "Berkshire Hathaway"},
    # Financials
    {"ticker": "JPM",   "name": "JPMorgan Chase"},
    {"ticker": "V",     "name": "Visa"},
    {"ticker": "MA",    "name": "Mastercard"},
    {"ticker": "BAC",   "name": "Bank of America"},
    {"ticker": "WFC",   "name": "Wells Fargo"},
    {"ticker": "GS",    "name": "Goldman Sachs"},
    {"ticker": "MS",    "name": "Morgan Stanley"},
    {"ticker": "C",     "name": "Citigroup"},
    {"ticker": "AXP",   "name": "American Express"},
    {"ticker": "BLK",   "name": "BlackRock"},
    {"ticker": "SCHW",  "name": "Charles Schwab"},
    # Healthcare
    {"ticker": "UNH",   "name": "UnitedHealth"},
    {"ticker": "JNJ",   "name": "Johnson & Johnson"},
    {"ticker": "LLY",   "name": "Eli Lilly"},
    {"ticker": "ABBV",  "name": "AbbVie"},
    {"ticker": "MRK",   "name": "Merck"},
    {"ticker": "PFE",   "name": "Pfizer"},
    {"ticker": "TMO",   "name": "Thermo Fisher"},
    {"ticker": "ABT",   "name": "Abbott"},
    {"ticker": "AMGN",  "name": "Amgen"},
    {"ticker": "ISRG",  "name": "Intuitive Surgical"},
    # Technology
    {"ticker": "AMD",   "name": "AMD"},
    {"ticker": "INTC",  "name": "Intel"},
    {"ticker": "QCOM",  "name": "Qualcomm"},
    {"ticker": "TXN",   "name": "Texas Instruments"},
    {"ticker": "AMAT",  "name": "Applied Materials"},
    {"ticker": "LRCX",  "name": "Lam Research"},
    {"ticker": "KLAC",  "name": "KLA Corp"},
    {"ticker": "MU",    "name": "Micron Technology"},
    {"ticker": "ORCL",  "name": "Oracle"},
    {"ticker": "CRM",   "name": "Salesforce"},
    {"ticker": "ADBE",  "name": "Adobe"},
    {"ticker": "NOW",   "name": "ServiceNow"},
    {"ticker": "SNPS",  "name": "Synopsys"},
    {"ticker": "CDNS",  "name": "Cadence Design"},
    {"ticker": "PANW",  "name": "Palo Alto Networks"},
    {"ticker": "CRWD",  "name": "CrowdStrike"},
    {"ticker": "FTNT",  "name": "Fortinet"},
    {"ticker": "NET",   "name": "Cloudflare"},
    {"ticker": "DDOG",  "name": "Datadog"},
    {"ticker": "SNOW",  "name": "Snowflake"},
    {"ticker": "PLTR",  "name": "Palantir"},
    {"ticker": "UBER",  "name": "Uber"},
    {"ticker": "LYFT",  "name": "Lyft"},
    {"ticker": "ABNB",  "name": "Airbnb"},
    {"ticker": "SHOP",  "name": "Shopify"},
    # Consumer Discretionary
    {"ticker": "WMT",   "name": "Walmart"},
    {"ticker": "COST",  "name": "Costco"},
    {"ticker": "TGT",   "name": "Target"},
    {"ticker": "HD",    "name": "Home Depot"},
    {"ticker": "LOW",   "name": "Lowe's"},
    {"ticker": "MCD",   "name": "McDonald's"},
    {"ticker": "SBUX",  "name": "Starbucks"},
    {"ticker": "NKE",   "name": "Nike"},
    {"ticker": "LULU",  "name": "Lululemon"},
    # Consumer Staples
    {"ticker": "KO",    "name": "Coca-Cola"},
    {"ticker": "PEP",   "name": "PepsiCo"},
    {"ticker": "PG",    "name": "Procter & Gamble"},
    {"ticker": "MO",    "name": "Altria"},
    {"ticker": "PM",    "name": "Philip Morris International"},
    {"ticker": "CL",    "name": "Colgate-Palmolive"},
    {"ticker": "KMB",   "name": "Kimberly-Clark"},
    {"ticker": "GIS",   "name": "General Mills"},
    {"ticker": "STZ",   "name": "Constellation Brands"},
    {"ticker": "HSY",   "name": "Hershey"},
    {"ticker": "MDLZ",  "name": "Mondelez International"},
    {"ticker": "CAG",   "name": "ConAgra Brands"},
    # Energy
    {"ticker": "XOM",   "name": "ExxonMobil"},
    {"ticker": "CVX",   "name": "Chevron"},
    {"ticker": "COP",   "name": "ConocoPhillips"},
    {"ticker": "SLB",   "name": "Schlumberger"},
    {"ticker": "EOG",   "name": "EOG Resources"},
    # Industrials & misc
    {"ticker": "CAT",   "name": "Caterpillar"},
    {"ticker": "DE",    "name": "Deere & Co"},
    {"ticker": "BA",    "name": "Boeing"},
    {"ticker": "LMT",   "name": "Lockheed Martin"},
    {"ticker": "RTX",   "name": "Raytheon Technologies"},
    {"ticker": "GE",    "name": "GE Aerospace"},
    {"ticker": "HON",   "name": "Honeywell"},
    {"ticker": "MMM",   "name": "3M"},
    {"ticker": "UPS",   "name": "UPS"},
    {"ticker": "FDX",   "name": "FedEx"},
    # Communications
    {"ticker": "NFLX",  "name": "Netflix"},
    {"ticker": "DIS",   "name": "Walt Disney"},
    {"ticker": "CMCSA", "name": "Comcast"},
    {"ticker": "T",     "name": "AT&T"},
    {"ticker": "VZ",    "name": "Verizon"},
    {"ticker": "TMUS",  "name": "T-Mobile"},
    {"ticker": "SPOT",  "name": "Spotify"},
]

# Remove duplicates from STOCKS_US_DEFAULT
_seen = set()
_deduped = []
for s in STOCKS_US_DEFAULT:
    if s["ticker"] not in _seen:
        _seen.add(s["ticker"])
        _deduped.append(s)
STOCKS_US_DEFAULT = _deduped

# European stocks (Yahoo Finance tickers)
STOCKS_EU = [
    # France (.PA)
    {"ticker": "MC.PA",   "name": "LVMH"},
    {"ticker": "TTE.PA",  "name": "TotalEnergies"},
    {"ticker": "SAN.PA",  "name": "Sanofi"},
    {"ticker": "AIR.PA",  "name": "Airbus"},
    {"ticker": "OR.PA",   "name": "L'Oréal"},
    {"ticker": "BNP.PA",  "name": "BNP Paribas"},
    {"ticker": "AI.PA",   "name": "Air Liquide"},
    {"ticker": "SU.PA",   "name": "Schneider Electric"},
    {"ticker": "DG.PA",   "name": "Vinci"},
    {"ticker": "RI.PA",   "name": "Pernod Ricard"},
    {"ticker": "CS.PA",   "name": "AXA"},
    {"ticker": "CAP.PA",  "name": "Capgemini"},
    {"ticker": "EL.PA",   "name": "EssilorLuxottica"},
    {"ticker": "VIE.PA",  "name": "Veolia Environment"},
    {"ticker": "SGO.PA",  "name": "Saint-Gobain"},
    # Germany (.DE)
    {"ticker": "SAP.DE",  "name": "SAP"},
    {"ticker": "SIE.DE",  "name": "Siemens"},
    {"ticker": "ALV.DE",  "name": "Allianz"},
    {"ticker": "BAYN.DE", "name": "Bayer"},
    {"ticker": "BMW.DE",  "name": "BMW"},
    {"ticker": "MBG.DE",  "name": "Mercedes-Benz"},
    {"ticker": "VOW3.DE", "name": "Volkswagen"},
    {"ticker": "DTE.DE",  "name": "Deutsche Telekom"},
    {"ticker": "DBK.DE",  "name": "Deutsche Bank"},
    {"ticker": "MRK.DE",  "name": "Merck KGaA"},
    {"ticker": "MUV2.DE", "name": "Munich Re"},
    {"ticker": "BAS.DE",  "name": "BASF"},
    {"ticker": "EOAN.DE", "name": "E.ON"},
    {"ticker": "HEN3.DE", "name": "Henkel"},
    {"ticker": "ADS.DE",  "name": "Adidas"},
    # Netherlands (.AS)
    {"ticker": "ASML.AS", "name": "ASML Holding"},
    {"ticker": "HEIA.AS", "name": "Heineken"},
    {"ticker": "PHIA.AS", "name": "Philips"},
    {"ticker": "WKL.AS",  "name": "Wolters Kluwer"},
    {"ticker": "NN.AS",   "name": "NN Group"},
    # UK (.L)
    {"ticker": "SHEL.L",  "name": "Shell"},
    {"ticker": "BP.L",    "name": "BP"},
    {"ticker": "AZN.L",   "name": "AstraZeneca"},
    {"ticker": "GSK.L",   "name": "GSK"},
    {"ticker": "ULVR.L",  "name": "Unilever"},
    {"ticker": "HSBA.L",  "name": "HSBC"},
    {"ticker": "RIO.L",   "name": "Rio Tinto"},
    {"ticker": "LLOY.L",  "name": "Lloyds Banking Group"},
    {"ticker": "VOD.L",   "name": "Vodafone"},
    {"ticker": "DGE.L",   "name": "Diageo"},
    {"ticker": "REL.L",   "name": "RELX"},
    {"ticker": "BA.L",    "name": "BAE Systems"},
    # Switzerland (.SW)
    {"ticker": "NESN.SW", "name": "Nestlé"},
    {"ticker": "ROG.SW",  "name": "Roche"},
    {"ticker": "NOVN.SW", "name": "Novartis"},
    {"ticker": "ABBN.SW", "name": "ABB"},
    {"ticker": "UBSG.SW", "name": "UBS"},
    {"ticker": "ZURN.SW", "name": "Zurich Insurance"},
    # Spain (.MC)
    {"ticker": "ITX.MC",  "name": "Inditex (Zara)"},
    {"ticker": "SAN.MC",  "name": "Santander"},
    {"ticker": "BBVA.MC", "name": "BBVA"},
    {"ticker": "IBE.MC",  "name": "Iberdrola"},
    {"ticker": "TEF.MC",  "name": "Telefonica"},
    # Italy (.MI)
    {"ticker": "ENI.MI",  "name": "Eni"},
    {"ticker": "ENEL.MI", "name": "Enel"},
    {"ticker": "ISP.MI",  "name": "Intesa Sanpaolo"},
    {"ticker": "UCG.MI",  "name": "UniCredit"},
    {"ticker": "LDO.MI",  "name": "Leonardo"},
    # Sweden (.ST)
    {"ticker": "ERIC-B.ST", "name": "Ericsson"},
    {"ticker": "VOLV-B.ST", "name": "Volvo"},
    {"ticker": "ATCO-A.ST", "name": "Atlas Copco"},
]

# Opportunity scanner: universe to scan
SCANNER_UNIVERSE = (
    [x["ticker"] for x in STOCKS_US_DEFAULT]
    + [x["ticker"] for x in STOCKS_EU]
    + [x["ticker"] for x in ETFS_BROAD]
    + [x["ticker"] for x in ETFS_SECTOR]
    + [x["ticker"] for x in ETFS_OTHER]
    + [x["ticker"] for x in ETFS_THEMATIC]
    + [x["ticker"] for x in STOCKS_PL]
)

# Opportunity scanner thresholds
OPP_RSI_MAX = 45              # RSI must be below this
OPP_PRICE_VS_SMA200 = 1.0     # Price must be >= SMA200 (uptrend)
OPP_PRICE_VS_SMA50 = 1.0      # Price must be <= SMA50 (dip)
OPP_VOLUME_RATIO_MIN = 1.2    # Recent avg volume vs 50d avg
OPP_DRAWDOWN_MIN = 0.10       # Min drawdown from 52W high

# Macro regime thresholds and region proxies
MACRO_SUPPORT_THRESHOLD = 65
MACRO_HEADWIND_THRESHOLD = 40

# Company-specific fundamentals and events thresholds
COMPANY_SUPPORT_THRESHOLD = 65
COMPANY_HEADWIND_THRESHOLD = 40
COMPANY_NEWS_LOOKBACK_DAYS = 21
COMPANY_NEWS_LIMIT = 6

MACRO_REGION_PROXIES = {
    "US": {
        "label": "USA",
        "benchmark": "^GSPC",
        "benchmark_name": "S&P 500",
    },
    "EU": {
        "label": "Europa",
        "benchmark": "^STOXX50E",
        "benchmark_name": "Euro Stoxx 50",
        "ecb_series": "YC/B.U2.EUR.4F.G_N_A.SV_C_YM.IF_3M",
        "conditions_name": "EUR 3M",
    },
    "PL": {
        "label": "Polska",
        "benchmark": "EPOL",
        "benchmark_name": "MSCI Poland ETF (EPOL proxy)",
        "fx_pair": "USDPLN=X",
        "conditions_name": "USD/PLN",
    },
}

# Colors — light theme
COLOR_UP      = "#16a34a"   # green
COLOR_DOWN    = "#dc2626"   # red
COLOR_BG      = "#ffffff"   # chart plot area
COLOR_BG2     = "#f8f9fa"   # paper / panel background
COLOR_TEXT    = "#212529"   # primary text
COLOR_ACCENT  = "#2563eb"   # blue accent
COLOR_SUBTEXT = "#6c757d"   # secondary text
COLOR_BORDER  = "#dee2e6"   # borders
COLOR_GRID    = "#e9ecef"   # chart grid lines
COLOR_SMA20   = "#16a34a"   # green
COLOR_SMA50   = "#f97316"   # orange
COLOR_SMA200  = "#dc2626"   # red
