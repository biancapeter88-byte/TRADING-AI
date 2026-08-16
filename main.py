import os
import json
import time
import html
import requests

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Trading AI Market Dashboard",
    version="4.0"
)


# ============================================================
# CONFIG
# ============================================================

TWELVE_DATA_API_KEY = os.getenv(
    "TWELVE_DATA_API_KEY"
)

OPENAI_API_KEY = os.getenv(
    "OPENAI_API_KEY"
)

OPENAI_MODEL = os.getenv(
    "OPENAI_MODEL",
    "gpt-5.6"
)

TD_URL = "https://api.twelvedata.com"
OPENAI_URL = "https://api.openai.com/v1/responses"

TZ = ZoneInfo("Europe/Berlin")


# ============================================================
# WATCHLIST
# ============================================================

WATCHLIST = [
    ("EURUSD", "EUR/USD"),
    ("GBPUSD", "GBP/USD"),
    ("USDJPY", "USD/JPY"),
    ("USDCHF", "USD/CHF"),
    ("AUDUSD", "AUD/USD"),
    ("NZDUSD", "NZD/USD"),
    ("USDCAD", "USD/CAD"),
    ("EURGBP", "EUR/GBP"),
    ("GBPJPY", "GBP/JPY"),
    ("AUDCHF", "AUD/CHF"),
    ("XAUUSD", "XAU/USD"),
]


# ============================================================
# CACHE
# ============================================================

CACHE = {}

CACHE_TTL_SECONDS = int(
    os.getenv(
        "CACHE_TTL_SECONDS",
        "300"
    )
)


# ============================================================
# SYMBOL NORMALIZATION
# ============================================================

def normalize_symbol(symbol):

    s = symbol.upper().strip()

    aliases = {
        "EURUSD": "EUR/USD",
        "GBPUSD": "GBP/USD",
        "USDJPY": "USD/JPY",
        "USDCHF": "USD/CHF",
        "AUDUSD": "AUD/USD",
        "NZDUSD": "NZD/USD",
        "USDCAD": "USD/CAD",
        "EURGBP": "EUR/GBP",
        "GBPJPY": "GBP/JPY",
        "AUDCHF": "AUD/CHF",
        "XAUUSD": "XAU/USD",
    }

    return aliases.get(
        s,
        s
    )


# ============================================================
# GENERIC HELPERS
# ============================================================

def average(values):

    values = [
        x for x in values
        if x is not None
    ]

    if not values:
        return None

    return sum(values) / len(values)


def safe_float(value):

    try:
        return float(value)
    except Exception:
        return None


def parse_dt(value):

    return datetime.strptime(
        value,
        "%Y-%m-%d %H:%M:%S"
    ).replace(
        tzinfo=TZ
    )


# ============================================================
# TWELVE DATA
# ============================================================

def twelve_data(endpoint, params):

    if not TWELVE_DATA_API_KEY:

        raise HTTPException(
            500,
            "TWELVE_DATA_API_KEY is missing"
        )

    p = dict(params)

    p["apikey"] = TWELVE_DATA_API_KEY

    try:

        response = requests.get(
            f"{TD_URL}/{endpoint}",
            params=p,
            timeout=30
        )

    except requests.RequestException as e:

        raise HTTPException(
            502,
            f"Twelve Data connection error: {e}"
        )

    if response.status_code != 200:

        raise HTTPException(
            response.status_code,
            response.text
        )

    try:

        data = response.json()

    except Exception:

        raise HTTPException(
            502,
            "Invalid JSON from Twelve Data"
        )

    if data.get("status") == "error":

        raise HTTPException(
            400,
            str(data)
        )

    return data


def get_quote(symbol):

    symbol = normalize_symbol(
        symbol
    )

    return twelve_data(
        "quote",
        {
            "symbol": symbol
        }
    )


def get_candles(
    symbol,
    interval,
    outputsize
):

    symbol = normalize_symbol(
        symbol
    )

    data = twelve_data(
        "time_series",
        {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "timezone": "Europe/Berlin"
        }
    )

    values = data.get(
        "values",
        []
    )

    if not values:

        raise HTTPException(
            404,
            f"No data for {symbol} {interval}"
        )

    candles = []

    for x in values:

        try:

            volume = None

            if x.get("volume") not in (
                None,
                "",
                "0",
                0
            ):

                volume = float(
                    x["volume"]
                )

            candles.append({

                "datetime":
                    x["datetime"],

                "open":
                    float(x["open"]),

                "high":
                    float(x["high"]),

                "low":
                    float(x["low"]),

                "close":
                    float(x["close"]),

                "volume":
                    volume
            })

        except Exception:
            continue

    candles.reverse()

    return candles


# ============================================================
# EMA
# ============================================================

def ema(
    values,
    period
):

    if len(values) < period:
        return None

    result = sum(
        values[:period]
    ) / period

    multiplier = (
        2 / (period + 1)
    )

    for value in values[period:]:

        result = (
            value * multiplier
            + result
            * (1 - multiplier)
        )

    return result


# ============================================================
# RSI
# ============================================================

def rsi(
    values,
    period=14
):

    if len(values) <= period:
        return None

    gains = []
    losses = []

    for i in range(
        1,
        len(values)
    ):

        change = (
            values[i]
            - values[i - 1]
        )

        gains.append(
            max(change, 0)
        )

        losses.append(
            max(-change, 0)
        )

    avg_gain = (
        sum(gains[:period])
        / period
    )

    avg_loss = (
        sum(losses[:period])
        / period
    )

    for i in range(
        period,
        len(gains)
    ):

        avg_gain = (
            avg_gain * (period - 1)
            + gains[i]
        ) / period

        avg_loss = (
            avg_loss * (period - 1)
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100

    rs = (
        avg_gain
        / avg_loss
    )

    return (
        100
        - 100 / (1 + rs)
    )


# ============================================================
# ATR
# ============================================================

def atr(
    candles,
    period=14
):

    if len(candles) <= period:
        return None

    ranges = []

    for i in range(
        1,
        len(candles)
    ):

        c = candles[i]
        p = candles[i - 1]

        true_range = max(

            c["high"]
            - c["low"],

            abs(
                c["high"]
                - p["close"]
            ),

            abs(
                c["low"]
                - p["close"]
            )
        )

        ranges.append(
            true_range
        )

    return average(
        ranges[-period:]
    )


# ============================================================
# VWAP
# ============================================================

def vwap(candles):

    usable = [
        c for c in candles
        if c["volume"] is not None
    ]

    if not usable:
        return None

    total_pv = 0
    total_volume = 0

    for c in usable:

        typical = (
            c["high"]
            + c["low"]
            + c["close"]
        ) / 3

        total_pv += (
            typical
            * c["volume"]
        )

        total_volume += (
            c["volume"]
        )

    if total_volume == 0:
        return None

    return (
        total_pv
        / total_volume
    )


# ============================================================
# SWINGS
# ============================================================

def find_swings(
    candles,
    strength=2
):

    highs = []
    lows = []

    for i in range(
        strength,
        len(candles) - strength
    ):

        current = candles[i]

        left = candles[
            i - strength:i
        ]

        right = candles[
            i + 1:
            i + strength + 1
        ]

        if (
            current["high"]
            >= max(
                x["high"]
                for x in left
            )
            and
            current["high"]
            >= max(
                x["high"]
                for x in right
            )
        ):

            highs.append({
                "price":
                    current["high"],
                "datetime":
                    current["datetime"]
            })

        if (
            current["low"]
            <= min(
                x["low"]
                for x in left
            )
            and
            current["low"]
            <= min(
                x["low"]
                for x in right
            )
        ):

            lows.append({
                "price":
                    current["low"],
                "datetime":
                    current["datetime"]
            })

    return highs, lows


# ============================================================
# STRUCTURE
# ============================================================

def market_structure(
    candles
):

    highs, lows = find_swings(
        candles
    )

    highs = highs[-8:]
    lows = lows[-8:]

    if (
        len(highs) < 2
        or len(lows) < 2
    ):

        return {
            "bias": "unknown",
            "HH": False,
            "HL": False,
            "LH": False,
            "LL": False,
            "BOS": None,
            "CHoCH": None
        }

    previous_high = highs[-2][
        "price"
    ]

    latest_high = highs[-1][
        "price"
    ]

    previous_low = lows[-2][
        "price"
    ]

    latest_low = lows[-1][
        "price"
    ]

    HH = latest_high > previous_high
    LH = latest_high < previous_high

    HL = latest_low > previous_low
    LL = latest_low < previous_low

    if HH and HL:

        bias = "bullish"

    elif LH and LL:

        bias = "bearish"

    else:

        bias = "range"

    price = candles[-1][
        "close"
    ]

    BOS = None
    CHoCH = None

    if price > latest_high:
        BOS = "bullish"

    elif price < latest_low:
        BOS = "bearish"

    if (
        bias == "bearish"
        and price > previous_high
    ):

        CHoCH = "bullish"

    elif (
        bias == "bullish"
        and price < previous_low
    ):

        CHoCH = "bearish"

    return {

        "bias": bias,

        "HH": HH,
        "HL": HL,
        "LH": LH,
        "LL": LL,

        "BOS": BOS,
        "CHoCH": CHoCH,

        "recent_high":
            latest_high,

        "recent_low":
            latest_low
    }


# ============================================================
# EQUAL LEVELS
# ============================================================

def equal_levels(
    points,
    tolerance=0.0005
):

    result = []

    for i in range(
        len(points)
    ):

        for j in range(
            i + 1,
            len(points)
        ):

            a = points[i][
                "price"
            ]

            b = points[j][
                "price"
            ]

            denominator = max(
                abs(a),
                abs(b),
                1e-12
            )

            if (
                abs(a - b)
                / denominator
                <= tolerance
            ):

                result.append({
                    "level":
                        (a + b) / 2,
                    "first":
                        points[i]["datetime"],
                    "second":
                        points[j]["datetime"]
                })

    return result[-10:]


# ============================================================
# LIQUIDITY
# ============================================================

def liquidity(
    candles
):

    highs, lows = find_swings(
        candles
    )

    return {

        "equal_highs":
            equal_levels(highs),

        "equal_lows":
            equal_levels(lows),

        "buy_side_liquidity":
            (
                max(
                    x["price"]
                    for x in highs[-10:]
                )
                if highs
                else None
            ),

        "sell_side_liquidity":
            (
                min(
                    x["price"]
                    for x in lows[-10:]
                )
                if lows
                else None
            )
    }


# ============================================================
# FVG
# ============================================================

def fvg(candles):

    bullish = []
    bearish = []

    for i in range(
        2,
        len(candles)
    ):

        a = candles[i - 2]
        b = candles[i - 1]
        c = candles[i]

        if (
            a["high"]
            < c["low"]
        ):

            bullish.append({

                "low":
                    a["high"],

                "high":
                    c["low"],

                "size":
                    c["low"]
                    - a["high"],

                "datetime":
                    b["datetime"]
            })

        if (
            a["low"]
            > c["high"]
        ):

            bearish.append({

                "low":
                    c["high"],

                "high":
                    a["low"],

                "size":
                    a["low"]
                    - c["high"],

                "datetime":
                    b["datetime"]
            })

    return {

        "bullish":
            bullish[-10:],

        "bearish":
            bearish[-10:]
    }


# ============================================================
# DISPLACEMENT
# ============================================================

def displacement(
    candles
):

    current = candles[-1]

    current_atr = atr(
        candles,
        14
    )

    if not current_atr:

        return {
            "detected": False,
            "direction": None,
            "atr_multiple": None
        }

    body = abs(
        current["close"]
        - current["open"]
    )

    multiple = (
        body
        / current_atr
    )

    return {

        "detected":
            multiple >= 1.5,

        "direction":
            (
                "bullish"
                if current["close"]
                > current["open"]
                else "bearish"
            ),

        "atr_multiple":
            multiple
    }


# ============================================================
# TIMEFRAME ANALYSIS
# ============================================================

def timeframe_analysis(
    candles
):

    closes = [
        c["close"]
        for c in candles
    ]

    return {

        "price":
            closes[-1],

        "EMA21":
            ema(
                closes,
                21
            ),

        "EMA50":
            ema(
                closes,
                50
            ),

        "EMA200":
            ema(
                closes,
                200
            ),

        "RSI14":
            rsi(
                closes,
                14
            ),

        "ATR14":
            atr(
                candles,
                14
            ),

        "VWAP":
            vwap(
                candles
            ),

        "structure":
            market_structure(
                candles
            ),

        "liquidity":
            liquidity(
                candles
            ),

        "FVG":
            fvg(
                candles
            ),

        "displacement":
            displacement(
                candles
            )
    }


# ============================================================
# DAILY
# ============================================================

def daily_stats(
    candles
):

    groups = {}

    for candle in candles:

        date = parse_dt(
            candle["datetime"]
        ).date()

        groups.setdefault(
            date,
            []
        ).append(candle)

    result = []

    for date in sorted(
        groups
    ):

        day = groups[date]

        result.append({

            "date":
                str(date),

            "open":
                day[0]["open"],

            "high":
                max(
                    x["high"]
                    for x in day
                ),

            "low":
                min(
                    x["low"]
                    for x in day
                ),

            "close":
                day[-1]["close"]
        })

    return result


def calculate_adr(
    days,
    period
):

    if len(days) < period:
        return None

    ranges = [

        x["high"]
        - x["low"]

        for x in days[-period:]
    ]

    return average(
        ranges
    )


# ============================================================
# SESSION
# ============================================================

def session_range(
    candles,
    date,
    start_hour,
    end_hour
):

    selected = []

    for candle in candles:

        candle_dt = parse_dt(
            candle["datetime"]
        )

        if candle_dt.date() != date:
            continue

        if (
            start_hour
            <= candle_dt.hour
            < end_hour
        ):

            selected.append(
                candle
            )

    if not selected:
        return None

    high = max(
        x["high"]
        for x in selected
    )

    low = min(
        x["low"]
        for x in selected
    )

    return {

        "high": high,

        "low": low,

        "range":
            high - low
    }


# ============================================================
# PIVOTS
# ============================================================

def calculate_pivots(
    previous_day
):

    if not previous_day:
        return None

    h = previous_day["high"]
    l = previous_day["low"]
    c = previous_day["close"]

    p = (
        h + l + c
    ) / 3

    return {

        "pivot": p,

        "R1":
            2 * p - l,

        "R2":
            p + h - l,

        "R3":
            h + 2 * (
                p - l
            ),

        "S1":
            2 * p - h,

        "S2":
            p - h + l,

        "S3":
            l - 2 * (
                h - p
            )
    }


# ============================================================
# MARKET DATA FOR ONE SYMBOL
# ============================================================

def build_market_data(
    display_symbol,
    api_symbol
):

    quote = get_quote(
        api_symbol
    )

    price = float(
        quote["close"]
    )

    candles = {

        "1m":
            get_candles(
                api_symbol,
                "1min",
                1500
            ),

        "5m":
            get_candles(
                api_symbol,
                "5min",
                5000
            ),

        "15m":
            get_candles(
                api_symbol,
                "15min",
                2500
            ),

        "1H":
            get_candles(
                api_symbol,
                "1h",
                1200
            ),

        "4H":
            get_candles(
                api_symbol,
                "4h",
                600
            ),

        "1D":
            get_candles(
                api_symbol,
                "1day",
                100
            )
    }

    daily = daily_stats(
        candles["5m"]
    )

    current_day = daily[-1]

    previous_day = (
        daily[-2]
        if len(daily) >= 2
        else None
    )

    current_range = (
        current_day["high"]
        - current_day["low"]
    )

    ADR5 = calculate_adr(
        daily[:-1],
        5
    )

    ADR10 = calculate_adr(
        daily[:-1],
        10
    )

    ADR14 = calculate_adr(
        daily[:-1],
        14
    )

    ADR20 = calculate_adr(
        daily[:-1],
        20
    )

    berlin_date = datetime.now(
        TZ
    ).date()

    asia = session_range(
        candles["5m"],
        berlin_date,
        0,
        9
    )

    london = session_range(
        candles["5m"],
        berlin_date,
        9,
        12
    )

    pivots = calculate_pivots(
        previous_day
    )

    timeframes = {}

    for tf in candles:

        timeframes[tf] = (
            timeframe_analysis(
                candles[tf]
            )
        )

    biases = {

        tf:
            timeframes[tf]
            ["structure"]
            ["bias"]

        for tf in timeframes
    }

    bullish = list(
        biases.values()
    ).count("bullish")

    bearish = list(
        biases.values()
    ).count("bearish")

    if bullish > bearish:
        overall_bias = "bullish"

    elif bearish > bullish:
        overall_bias = "bearish"

    else:
        overall_bias = "neutral"

    range_position = None

    if (
        current_day["high"]
        != current_day["low"]
    ):

        range_position = (

            (
                price
                - current_day["low"]
            )

            /

            (
                current_day["high"]
                - current_day["low"]
            )

        ) * 100

    if range_position is None:

        range_zone = "unknown"

    elif range_position <= 30:

        range_zone = "discount"

    elif range_position >= 70:

        range_zone = "premium"

    else:

        range_zone = "equilibrium"

    return {

        "symbol":
            display_symbol,

        "api_symbol":
            api_symbol,

        "timestamp":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "price":
            price,

        "bias": {

            "overall":
                overall_bias,

            "bullish_count":
                bullish,

            "bearish_count":
                bearish,

            "timeframes":
                biases
        },

        "daily": {

            "current":
                current_day,

            "previous":
                previous_day,

            "range":
                current_range,

            "ADR5":
                ADR5,

            "ADR10":
                ADR10,

            "ADR14":
                ADR14,

            "ADR20":
                ADR20,

            "ADR_consumed":
                (
                    current_range
                    / ADR14
                    * 100
                    if ADR14
                    else None
                ),

            "range_position":
                range_position,

            "range_zone":
                range_zone
        },

        "sessions": {

            "Asia":
                asia,

            "London":
                london
        },

        "pivots":
            pivots,

        "timeframes":
            timeframes
    }


# ============================================================
# AI ANALYSIS
# ============================================================

AI_SYSTEM_PROMPT = """
You are a professional discretionary intraday trading analyst.

You receive objective market data calculated from live market prices.

Your task is NOT to blindly follow the mechanical setup engine.

You must independently interpret the data.

Analyze:

1D
4H
1H
15M
5M
1M

Evaluate:

- HTF trend
- intraday trend
- market structure
- HH
- HL
- LH
- LL
- BOS
- CHoCH
- daily range
- ADR
- Asia range
- London range
- previous day high/low
- daily open
- pivots
- liquidity
- equal highs/lows
- FVG
- displacement
- EMA21/50/200
- RSI
- ATR
- VWAP if available
- premium/discount
- price location

Separate:

BIAS

from

ENTRY.

A bullish bias does NOT automatically mean BUY.

A bearish bias does NOT automatically mean SELL.

Look for these setup types:

LONG_PULLBACK
SHORT_PULLBACK
BREAKOUT_RETEST
LIQUIDITY_SWEEP_REVERSAL

Do NOT force a trade.

WAIT or NO_TRADE is preferred when there is insufficient confirmation.

A valid trade should have:

- clear directional context
- meaningful level
- logical entry
- logical invalidation
- realistic TP
- acceptable RR
- confirmation

Do not invent prices.

All entry, SL and TP values must be derived from the supplied market data.

Confidence is a qualitative score, NOT win probability.

Return ONLY the requested JSON.
"""


AI_SCHEMA = {

    "type": "object",

    "properties": {

        "bias": {
            "type": "string",
            "enum": [
                "BUY",
                "SELL",
                "WAIT",
                "NO_TRADE"
            ]
        },

        "confidence": {
            "type": "number"
        },

        "regime": {
            "type": "string"
        },

        "primary_setup": {
            "type": "string"
        },

        "entry": {
            "type": "number"
        },

        "sl": {
            "type": "number"
        },

        "tp1": {
            "type": "number"
        },

        "tp2": {
            "type": "number"
        },

        "tp3": {
            "type": "number"
        },

        "rr": {
            "type": "number"
        },

        "trigger": {
            "type": "string"
        },

        "invalidation": {
            "type": "string"
        },

        "htf_bias": {
            "type": "string"
        },

        "intraday_bias": {
            "type": "string"
        },

        "execution_bias": {
            "type": "string"
        },

        "key_level": {
            "type": "number"
        },

        "reason": {
            "type": "string"
        },

        "no_trade_reason": {
            "type": "string"
        }
    },

    "required": [

        "bias",
        "confidence",
        "regime",
        "primary_setup",
        "entry",
        "sl",
        "tp1",
        "tp2",
        "tp3",
        "rr",
        "trigger",
        "invalidation",
        "htf_bias",
        "intraday_bias",
        "execution_bias",
        "key_level",
        "reason",
        "no_trade_reason"
    ],

    "additionalProperties": False
}


def ai_analyze(
    market_data
):

    if not OPENAI_API_KEY:

        raise HTTPException(
            500,
            "OPENAI_API_KEY is missing"
        )

    payload = {

        "model":
            OPENAI_MODEL,

        "instructions":
            AI_SYSTEM_PROMPT,

        "input": (
            "Analyze this market data.\n\n"
            + json.dumps(
                market_data,
                separators=(
                    ",",
                    ":"
                )
            )
        ),

        "text": {

            "format": {

                "type":
                    "json_schema",

                "name":
                    "trading_analysis",

                "strict":
                    True,

                "schema":
                    AI_SCHEMA
            }
        }
    }

    headers = {

        "Authorization":
            f"Bearer {OPENAI_API_KEY}",

        "Content-Type":
            "application/json"
    }

    try:

        response = requests.post(
            OPENAI_URL,
            headers=headers,
            json=payload,
            timeout=120
        )

    except requests.RequestException as e:

        return {
            "error":
                f"OpenAI connection error: {e}"
        }

    if response.status_code != 200:

        return {
            "error":
                (
                    "OpenAI API error "
                    f"{response.status_code}: "
                    f"{response.text[:500]}"
                )
        }

    data = response.json()

    # Responses API convenience field
    output_text = data.get(
        "output_text"
    )

    if output_text:

        try:

            return json.loads(
                output_text
            )

        except json.JSONDecodeError:

            return {
                "error":
                    "OpenAI returned invalid JSON",
                "raw":
                    output_text
            }

    # Fallback parser
    for item in data.get(
        "output",
        []
    ):

        if item.get("type") != "message":
            continue

        for content in item.get(
            "content",
            []
        ):

            if (
                content.get("type")
                == "output_text"
            ):

                text = content.get(
                    "text",
                    ""
                )

                try:

                    return json.loads(
                        text
                    )

                except Exception:

                    return {
                        "error":
                            "Unable to parse AI JSON",
                        "raw":
                            text
                    }

    return {
        "error":
            "No AI output returned"
    }


# ============================================================
# ONE SYMBOL COMPLETE RESULT
# ============================================================

def analyze_symbol(
    display_symbol,
    api_symbol
):

    cache_key = display_symbol

    cached = CACHE.get(
        cache_key
    )

    now = time.time()

    if cached:

        age = (
            now
            - cached["timestamp"]
        )

        if age < CACHE_TTL_SECONDS:

            return cached["data"]

    try:

        market_data = build_market_data(
            display_symbol,
            api_symbol
        )

        ai = ai_analyze(
            market_data
        )

        result = {

            "symbol":
                display_symbol,

            "price":
                market_data["price"],

            "market":
                market_data["bias"],

            "daily":
                market_data["daily"],

            "sessions":
                market_data["sessions"],

            "pivots":
                market_data["pivots"],

            "ai":
                ai,

            "timestamp":
                market_data["timestamp"]
        }

        CACHE[cache_key] = {

            "timestamp":
                now,

            "data":
                result
        }

        return result

    except Exception as e:

        return {

            "symbol":
                display_symbol,

            "error":
                str(e),

            "ai": {
                "bias":
                    "NO_TRADE",

                "confidence":
                    0,

                "primary_setup":
                    "NONE",

                "reason":
                    "Data unavailable"
            }
        }


# ============================================================
# ALL MARKETS
# ============================================================

def analyze_all():

    results = []

    for display_symbol, api_symbol in WATCHLIST:

        results.append(
            analyze_symbol(
                display_symbol,
                api_symbol
            )
        )

    return {

        "timestamp":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "symbols":
            results
    }


# ============================================================
# API
# ============================================================

@app.get("/")
def root():

    return {

        "status":
            "online",

        "service":
            "Trading AI Dashboard",

        "dashboard":
            "/dashboard",

        "api":
            "/api?symbol=EURUSD",

        "watchlist":
            [
                x[0]
                for x in WATCHLIST
            ]
    }


@app.get("/api")
def api_analysis(
    symbol: str
):

    display = symbol.upper()

    normalized = normalize_symbol(
        symbol
    )

    market_data = build_market_data(
        display,
        normalized
    )

    return market_data


@app.get("/dashboard-data")
def dashboard_data():

    return analyze_all()


# ============================================================
# HTML HELPERS
# ============================================================

def fmt_price(
    value
):

    if value is None:
        return "—"

    try:

        value = float(value)

    except Exception:

        return "—"

    if abs(value) >= 100:
        return f"{value:.2f}"

    if abs(value) >= 10:
        return f"{value:.3f}"

    return f"{value:.5f}"


def fmt_number(
    value
):

    if value is None:
        return "—"

    try:
        return f"{float(value):.2f}"
    except Exception:
        return "—"


def bias_class(
    bias
):

    if bias == "BUY":
        return "buy"

    if bias == "SELL":
        return "sell"

    return "wait"


# ============================================================
# DASHBOARD
# ============================================================

@app.get(
    "/dashboard",
    response_class=HTMLResponse
)
def dashboard():

    data = analyze_all()

    rows = []

    for item in data["symbols"]:

        symbol = item["symbol"]

        price = item.get(
            "price"
        )

        ai = item.get(
            "ai",
            {}
        )

        bias = ai.get(
            "bias",
            "NO_TRADE"
        )

        confidence = ai.get(
            "confidence",
            0
        )

        setup = ai.get(
            "primary_setup",
            "NONE"
        )

        entry = ai.get(
            "entry"
        )

        sl = ai.get(
            "sl"
        )

        tp1 = ai.get(
            "tp1"
        )

        tp2 = ai.get(
            "tp2"
        )

        tp3 = ai.get(
            "tp3"
        )

        rr = ai.get(
            "rr"
        )

        reason = ai.get(
            "reason",
            ""
        )

        trigger = ai.get(
            "trigger",
            ""
        )

        invalidation = ai.get(
            "invalidation",
            ""
        )

        cls = bias_class(
            bias
        )

        row = f"""

        <tr>

            <td class="symbol">
                {html.escape(symbol)}
            </td>

            <td>
                {fmt_price(price)}
            </td>

            <td>
                <span class="badge {cls}">
                    {html.escape(str(bias))}
                </span>
            </td>

            <td>
                <strong>
                    {fmt_number(confidence)}
                </strong>
            </td>

            <td>
                {html.escape(str(setup))}
            </td>

            <td>
                {fmt_price(entry)}
            </td>

            <td>
                {fmt_price(sl)}
            </td>

            <td>
                {fmt_price(tp1)}
            </td>

            <td>
                {fmt_price(tp2)}
            </td>

            <td>
                {fmt_number(rr)}
            </td>

            <td class="reason">
                <strong>
                    {html.escape(str(reason))}
                </strong>

                <div class="small">
                    Trigger:
                    {html.escape(str(trigger))}
                </div>

                <div class="small">
                    Invalidation:
                    {html.escape(str(invalidation))}
                </div>
            </td>

        </tr>
        """

        rows.append(row)

    generated = datetime.now(
        TZ
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    page = f"""

<!DOCTYPE html>

<html>

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<meta
    http-equiv="refresh"
    content="300"
>

<title>
Trading AI Dashboard
</title>

<style>

* {{
    box-sizing: border-box;
}}

body {{

    margin: 0;

    background:
        #0b0f14;

    color:
        #e8edf3;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}}

.container {{

    padding:
        24px;

    width:
        100%;

    overflow-x:
        auto;
}}

.header {{

    display:
        flex;

    justify-content:
        space-between;

    align-items:
        center;

    margin-bottom:
        20px;
}}

h1 {{

    margin:
        0;

    font-size:
        28px;
}}

.subtitle {{

    color:
        #8d99a8;

    margin-top:
        6px;
}}

table {{

    width:
        100%;

    min-width:
        1500px;

    border-collapse:
        collapse;

    background:
        #111720;

    border:
        1px solid #27313d;
}}

th {{

    background:
        #18212c;

    color:
        #9eabb9;

    text-align:
        left;

    padding:
        13px 12px;

    font-size:
        12px;

    text-transform:
        uppercase;

    letter-spacing:
        .05em;

    position:
        sticky;

    top:
        0;
}}

td {{

    padding:
        14px 12px;

    border-top:
        1px solid #202a35;

    vertical-align:
        top;

    font-size:
        13px;
}}

tr:hover {{

    background:
        #151e28;
}}

.symbol {{

    font-weight:
        700;

    font-size:
        15px;
}}

.badge {{

    display:
        inline-block;

    padding:
        5px 10px;

    border-radius:
        5px;

    font-weight:
        700;

    font-size:
        11px;
}}

.buy {{

    background:
        #173b2b;

    color:
        #61e6a4;
}}

.sell {{

    background:
        #452023;

    color:
        #ff7d87;
}}

.wait {{

    background:
        #3a3420;

    color:
        #f0ce69;
}}

.reason {{

    min-width:
        350px;

    max-width:
        500px;

    line-height:
        1.45;
}}

.small {{

    color:
        #8996a5;

    margin-top:
        5px;

    font-size:
        11px;
}}

.footer {{

    margin-top:
        15px;

    color:
        #687585;

    font-size:
        12px;
}}

</style>

</head>

<body>

<div class="container">

<div class="header">

<div>

<h1>
Trading AI — Market Dashboard
</h1>

<div class="subtitle">
11 markets · live market data · AI interpretation
</div>

</div>

<div class="subtitle">
Updated:
{html.escape(generated)}
</div>

</div>

<table>

<thead>

<tr>

<th>
Symbol
</th>

<th>
Price
</th>

<th>
Bias
</th>

<th>
Confidence
</th>

<th>
Setup
</th>

<th>
Entry
</th>

<th>
SL
</th>

<th>
TP1
</th>

<th>
TP2
</th>

<th>
RR
</th>

<th>
AI Interpretation
</th>

</tr>

</thead>

<tbody>

{"".join(rows)}

</tbody>

</table>

<div class="footer">

Dashboard refreshes every
{CACHE_TTL_SECONDS}
seconds.

Data source:
Twelve Data.

AI:
{html.escape(OPENAI_MODEL)}.

</div>

</div>

</body>

</html>

"""

    return HTMLResponse(
        content=page
    )
