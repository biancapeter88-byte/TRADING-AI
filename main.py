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
    version="5.0"
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

# Can be changed in Render Environment Variables.
# Default = GPT-5.6 Luna.
OPENAI_MODEL = os.getenv(
    "OPENAI_MODEL",
    "gpt-5.6-luna"
)

TD_URL = "https://api.twelvedata.com"

OPENAI_URL = (
    "https://api.openai.com/v1/responses"
)

TZ = ZoneInfo(
    "Europe/Berlin"
)


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

    s = (
        symbol
        .upper()
        .strip()
    )

    aliases = {

        "EURUSD":
            "EUR/USD",

        "GBPUSD":
            "GBP/USD",

        "USDJPY":
            "USD/JPY",

        "USDCHF":
            "USD/CHF",

        "AUDUSD":
            "AUD/USD",

        "NZDUSD":
            "NZD/USD",

        "USDCAD":
            "USD/CAD",

        "EURGBP":
            "EUR/GBP",

        "GBPJPY":
            "GBP/JPY",

        "AUDCHF":
            "AUD/CHF",

        "XAUUSD":
            "XAU/USD",
    }

    return aliases.get(
        s,
        s
    )


# ============================================================
# BASIC HELPERS
# ============================================================

def average(values):

    values = [
        x
        for x in values
        if x is not None
    ]

    if not values:
        return None

    return (
        sum(values)
        / len(values)
    )


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

def twelve_data(
    endpoint,
    params
):

    if not TWELVE_DATA_API_KEY:

        raise HTTPException(
            500,
            "TWELVE_DATA_API_KEY is missing"
        )

    p = dict(params)

    p["apikey"] = (
        TWELVE_DATA_API_KEY
    )

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

    return twelve_data(

        "quote",

        {
            "symbol":
                normalize_symbol(symbol)
        }
    )


def get_candles(
    symbol,
    interval,
    outputsize
):

    normalized = normalize_symbol(
        symbol
    )

    data = twelve_data(

        "time_series",

        {

            "symbol":
                normalized,

            "interval":
                interval,

            "outputsize":
                outputsize,

            "timezone":
                "Europe/Berlin"
        }
    )

    values = data.get(
        "values",
        []
    )

    if not values:

        raise HTTPException(

            404,

            (
                "No historical candle data "
                f"for {normalized} {interval}"
            )
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
                    float(
                        x["open"]
                    ),

                "high":
                    float(
                        x["high"]
                    ),

                "low":
                    float(
                        x["low"]
                    ),

                "close":
                    float(
                        x["close"]
                    ),

                "volume":
                    volume
            })

        except Exception:

            continue

    candles.reverse()

    if not candles:

        raise HTTPException(

            404,

            (
                "Historical candles could "
                f"not be parsed for {normalized}"
            )
        )

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

    result = (
        sum(values[:period])
        / period
    )

    multiplier = (
        2
        / (period + 1)
    )

    for value in values[period:]:

        result = (

            value
            * multiplier

            +

            result
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

            avg_gain
            * (period - 1)
            + gains[i]

        ) / period

        avg_loss = (

            avg_loss
            * (period - 1)
            + losses[i]

        ) / period

    if avg_loss == 0:

        return 100.0

    rs = (
        avg_gain
        / avg_loss
    )

    return (
        100
        - 100
        / (1 + rs)
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

        current = candles[i]

        previous = candles[i - 1]

        true_range = max(

            current["high"]
            - current["low"],

            abs(
                current["high"]
                - previous["close"]
            ),

            abs(
                current["low"]
                - previous["close"]
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

        c
        for c in candles

        if c["volume"] is not None
    ]

    if not usable:

        return None

    total_pv = 0
    total_volume = 0

    for candle in usable:

        typical_price = (

            candle["high"]
            + candle["low"]
            + candle["close"]

        ) / 3

        total_pv += (

            typical_price
            * candle["volume"]
        )

        total_volume += (
            candle["volume"]
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

    if len(candles) < (
        strength * 2 + 1
    ):

        return highs, lows

    for i in range(

        strength,

        len(candles)
        - strength
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
# MARKET STRUCTURE
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

            "bias":
                "unknown",

            "HH":
                False,

            "HL":
                False,

            "LH":
                False,

            "LL":
                False,

            "BOS":
                None,

            "CHoCH":
                None,

            "recent_high":
                None,

            "recent_low":
                None
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

    HH = (
        latest_high
        > previous_high
    )

    LH = (
        latest_high
        < previous_high
    )

    HL = (
        latest_low
        > previous_low
    )

    LL = (
        latest_low
        < previous_low
    )

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

    if price > latest_high:

        BOS = "bullish"

    elif price < latest_low:

        BOS = "bearish"

    CHoCH = None

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

        "bias":
            bias,

        "HH":
            HH,

        "HL":
            HL,

        "LH":
            LH,

        "LL":
            LL,

        "BOS":
            BOS,

        "CHoCH":
            CHoCH,

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

def fvg(
    candles
):

    bullish = []
    bearish = []

    for i in range(
        2,
        len(candles)
    ):

        first = candles[i - 2]

        middle = candles[i - 1]

        third = candles[i]

        if (
            first["high"]
            < third["low"]
        ):

            bullish.append({

                "low":
                    first["high"],

                "high":
                    third["low"],

                "size":
                    third["low"]
                    - first["high"],

                "datetime":
                    middle["datetime"]
            })

        if (
            first["low"]
            > third["high"]
        ):

            bearish.append({

                "low":
                    third["high"],

                "high":
                    first["low"],

                "size":
                    first["low"]
                    - third["high"],

                "datetime":
                    middle["datetime"]
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

            "detected":
                False,

            "direction":
                None,

            "atr_multiple":
                None
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
# DAILY STATS
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
# SESSION RANGE
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

            candle_dt.hour
            >= start_hour

            and

            candle_dt.hour
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

        "high":
            high,

        "low":
            low,

        "range":
            high - low,

        "start":
            selected[0]["datetime"],

        "end":
            selected[-1]["datetime"]
    }


# ============================================================
# PIVOTS
# ============================================================

def calculate_pivots(
    previous_day
):

    if not previous_day:

        return None

    high = previous_day[
        "high"
    ]

    low = previous_day[
        "low"
    ]

    close = previous_day[
        "close"
    ]

    pivot = (

        high
        + low
        + close

    ) / 3

    return {

        "pivot":
            pivot,

        "R1":
            2 * pivot - low,

        "R2":
            pivot + high - low,

        "R3":
            high
            + 2 * (
                pivot - low
            ),

        "S1":
            2 * pivot - high,

        "S2":
            pivot - high + low,

        "S3":
            low
            - 2 * (
                high - pivot
            )
    }


# ============================================================
# BUILD MARKET DATA
#
# IMPORTANT:
# HISTORICAL CANDLES ARE REQUESTED FIRST.
# A missing live quote DOES NOT make the market unavailable.
# ============================================================

def build_market_data(
    display_symbol,
    api_symbol
):

    # --------------------------------------------------------
    # 1. HISTORICAL DATA FIRST
    # --------------------------------------------------------

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


    # --------------------------------------------------------
    # 2. LAST HISTORICAL PRICE
    # --------------------------------------------------------

    last_candle = candles[
        "1m"
    ][-1]

    historical_price = (
        last_candle["close"]
    )

    last_candle_time = (
        last_candle["datetime"]
    )


    # --------------------------------------------------------
    # 3. TRY LIVE QUOTE
    # --------------------------------------------------------

    live_price = None

    quote_error = None

    try:

        quote = get_quote(
            api_symbol
        )

        if quote.get(
            "close"
        ) is not None:

            live_price = float(
                quote["close"]
            )

    except Exception as e:

        quote_error = str(e)


    # --------------------------------------------------------
    # 4. PRICE SOURCE / MARKET STATUS
    # --------------------------------------------------------

    if live_price is not None:

        price = live_price

        market_status = "OPEN"

        price_source = (
            "LIVE_QUOTE"
        )

    else:

        price = historical_price

        market_status = (
            "CLOSED_OR_"
            "LIVE_QUOTE_UNAVAILABLE"
        )

        price_source = (
            "LAST_COMPLETED_CANDLE"
        )


    # --------------------------------------------------------
    # 5. DAILY
    # --------------------------------------------------------

    daily = daily_stats(
        candles["5m"]
    )

    if not daily:

        raise HTTPException(

            404,

            (
                "Historical daily "
                "data unavailable"
            )
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


    # --------------------------------------------------------
    # 6. SESSIONS
    # --------------------------------------------------------

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


    # --------------------------------------------------------
    # 7. PIVOTS
    # --------------------------------------------------------

    pivots = calculate_pivots(
        previous_day
    )


    # --------------------------------------------------------
    # 8. TIMEFRAMES
    # --------------------------------------------------------

    timeframes = {}

    for tf in candles:

        timeframes[tf] = (
            timeframe_analysis(
                candles[tf]
            )
        )


    # --------------------------------------------------------
    # 9. MULTI-TIMEFRAME BIAS
    # --------------------------------------------------------

    biases = {

        tf:
            timeframes[tf]
            ["structure"]
            ["bias"]

        for tf in timeframes
    }


    bullish = list(
        biases.values()
    ).count(
        "bullish"
    )

    bearish = list(
        biases.values()
    ).count(
        "bearish"
    )


    if bullish > bearish:

        overall_bias = "bullish"

    elif bearish > bullish:

        overall_bias = "bearish"

    else:

        overall_bias = "neutral"


    # --------------------------------------------------------
    # 10. DAILY RANGE LOCATION
    # --------------------------------------------------------

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


    # --------------------------------------------------------
    # 11. RETURN
    # --------------------------------------------------------

    return {

        "symbol":
            display_symbol,

        "api_symbol":
            api_symbol,

        "timestamp":
            datetime.now(
                timezone.utc
            ).isoformat(),

        # MARKET STATUS
        "market_status":
            market_status,

        "price_source":
            price_source,

        "price":
            price,

        "last_completed_candle":
            last_candle_time,

        "live_quote_available":
            live_price is not None,

        "quote_error":
            quote_error,

        # BIAS
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

        # DAILY
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

        # SESSIONS
        "sessions": {

            "Asia":
                asia,

            "London":
                london
        },

        # PIVOTS
        "pivots":
            pivots,

        # TIMEFRAMES
        "timeframes":
            timeframes
    }


# ============================================================
# AI SYSTEM PROMPT
# ============================================================

AI_SYSTEM_PROMPT = """

You are a professional discretionary intraday trading analyst.

You receive objective market data calculated from live or
historical market prices.

Your job is to interpret the market, not blindly follow
mechanical indicators.

IMPORTANT MARKET STATUS RULE:

If market_status is OPEN:
    Use the current live quote as the current price.

If market_status is
CLOSED_OR_LIVE_QUOTE_UNAVAILABLE:

    Historical candles are still valid.

    Use the last completed historical candle as the
    reference price.

    Do NOT say that data is unavailable if historical
    candles are available.

    Clearly understand that the market is not currently
    confirmed live.

    The setup should be interpreted as preparation for
    the next trading session, not necessarily an
    immediate market entry.

Never invent a price.

Analyze:

1D
4H
1H
15M
5M
1M

Evaluate:

- higher timeframe trend
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
- previous day high
- previous day low
- daily open
- pivots
- liquidity
- equal highs
- equal lows
- FVG
- displacement
- EMA21
- EMA50
- EMA200
- RSI14
- ATR14
- VWAP when available
- premium / discount / equilibrium
- price location

Separate:

BIAS

from

ENTRY.

A bullish bias does NOT automatically mean BUY.

A bearish bias does NOT automatically mean SELL.

Possible setups:

LONG_PULLBACK
SHORT_PULLBACK
BREAKOUT_RETEST
LIQUIDITY_SWEEP_REVERSAL
NO_SETUP

A valid trade should have:

- clear directional context
- meaningful level
- logical entry
- logical invalidation
- realistic target
- acceptable risk/reward
- confirmation

Do NOT force a trade.

WAIT or NO_TRADE is preferred when confirmation
is insufficient.

If the market is closed:

    The analysis can still be useful.

    Return the best directional bias and setup
    for the next session.

    Do not describe the historical price as live.

ENTRY:

The entry should be a logical level or price derived
from the supplied data.

SL:

Must represent a logical invalidation level.

TP:

Use realistic nearby liquidity, structure or range
targets.

RR:

Calculate risk/reward from the proposed entry, SL
and TP1.

CONFIDENCE:

This is an analysis confidence score from 0 to 100.

It is NOT a statistical probability of winning.

If the evidence is mixed:

    use WAIT.

If there is no valid setup:

    use NO_TRADE.

Return ONLY valid JSON matching the requested schema.
"""


# ============================================================
# AI JSON SCHEMA
# ============================================================

AI_SCHEMA = {

    "type":
        "object",

    "properties": {

        "bias": {

            "type":
                "string",

            "enum": [

                "BUY",

                "SELL",

                "WAIT",

                "NO_TRADE"
            ]
        },

        "confidence": {

            "type":
                "number"
        },

        "regime": {

            "type":
                "string"
        },

        "primary_setup": {

            "type":
                "string"
        },

        "entry": {

            "type":
                "number"
        },

        "sl": {

            "type":
                "number"
        },

        "tp1": {

            "type":
                "number"
        },

        "tp2": {

            "type":
                "number"
        },

        "tp3": {

            "type":
                "number"
        },

        "rr": {

            "type":
                "number"
        },

        "trigger": {

            "type":
                "string"
        },

        "invalidation": {

            "type":
                "string"
        },

        "htf_bias": {

            "type":
                "string"
        },

        "intraday_bias": {

            "type":
                "string"
        },

        "execution_bias": {

            "type":
                "string"
        },

        "key_level": {

            "type":
                "number"
        },

        "reason": {

            "type":
                "string"
        },

        "no_trade_reason": {

            "type":
                "string"
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

    "additionalProperties":
        False
}


# ============================================================
# OPENAI
# ============================================================

def ai_analyze(
    market_data
):

    if not OPENAI_API_KEY:

        return {

            "error":
                "OPENAI_API_KEY is missing",

            "bias":
                "NO_TRADE",

            "confidence":
                0,

            "regime":
                "UNKNOWN",

            "primary_setup":
                "NONE",

            "entry":
                0,

            "sl":
                0,

            "tp1":
                0,

            "tp2":
                0,

            "tp3":
                0,

            "rr":
                0,

            "trigger":
                "",

            "invalidation":
                "",

            "htf_bias":
                "",

            "intraday_bias":
                "",

            "execution_bias":
                "",

            "key_level":
                0,

            "reason":
                "OpenAI API key missing",

            "no_trade_reason":
                "OpenAI API key missing"
        }


    payload = {

        "model":
            OPENAI_MODEL,

        "instructions":
            AI_SYSTEM_PROMPT,

        "input": (

            "Analyze this market data:\n\n"

            +

            json.dumps(

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
            (
                "Bearer "
                + OPENAI_API_KEY
            ),

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
                f"OpenAI connection error: {e}",

            "bias":
                "NO_TRADE",

            "confidence":
                0,

            "regime":
                "UNKNOWN",

            "primary_setup":
                "NONE",

            "entry":
                0,

            "sl":
                0,

            "tp1":
                0,

            "tp2":
                0,

            "tp3":
                0,

            "rr":
                0,

            "trigger":
                "",

            "invalidation":
                "",

            "htf_bias":
                "",

            "intraday_bias":
                "",

            "execution_bias":
                "",

            "key_level":
                0,

            "reason":
                "OpenAI connection error",

            "no_trade_reason":
                str(e)
        }


    if response.status_code != 200:

        return {

            "error":
                (
                    "OpenAI API error "
                    f"{response.status_code}: "
                    f"{response.text[:500]}"
                ),

            "bias":
                "NO_TRADE",

            "confidence":
                0,

            "regime":
                "UNKNOWN",

            "primary_setup":
                "NONE",

            "entry":
                0,

            "sl":
                0,

            "tp1":
                0,

            "tp2":
                0,

            "tp3":
                0,

            "rr":
                0,

            "trigger":
                "",

            "invalidation":
                "",

            "htf_bias":
                "",

            "intraday_bias":
                "",

            "execution_bias":
                "",

            "key_level":
                0,

            "reason":
                "OpenAI API error",

            "no_trade_reason":
                response.text[:500]
        }


    try:

        data = response.json()

    except Exception:

        return {

            "error":
                "Invalid JSON from OpenAI",

            "bias":
                "NO_TRADE",

            "confidence":
                0
        }


    # Responses API convenience field
    output_text = data.get(
        "output_text"
    )


    if output_text:

        try:

            return json.loads(
                output_text
            )

        except Exception:

            pass


    # Fallback parser
    for item in data.get(
        "output",
        []
    ):

        if item.get(
            "type"
        ) != "message":

            continue

        for content in item.get(
            "content",
            []
        ):

            if (
                content.get(
                    "type"
                )
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
                            "AI JSON parsing failed",

                        "bias":
                            "NO_TRADE",

                        "confidence":
                            0,

                        "reason":
                            text
                    }


    return {

        "error":
            "No AI output returned",

        "bias":
            "NO_TRADE",

        "confidence":
            0
    }


# ============================================================
# COMPLETE SYMBOL ANALYSIS
# ============================================================

def analyze_symbol(
    display_symbol,
    api_symbol
):

    cache_key = display_symbol

    now = time.time()

    cached = CACHE.get(
        cache_key
    )

    if cached:

        age = (

            now
            - cached["timestamp"]
        )

        if age < CACHE_TTL_SECONDS:

            return cached["data"]


    try:

        market_data = (
            build_market_data(
                display_symbol,
                api_symbol
            )
        )

        ai = ai_analyze(
            market_data
        )


        result = {

            "symbol":
                display_symbol,

            "price":
                market_data[
                    "price"
                ],

            "market_status":
                market_data[
                    "market_status"
                ],

            "price_source":
                market_data[
                    "price_source"
                ],

            "last_completed_candle":
                market_data[
                    "last_completed_candle"
                ],

            "live_quote_available":
                market_data[
                    "live_quote_available"
                ],

            "market":
                market_data[
                    "bias"
                ],

            "daily":
                market_data[
                    "daily"
                ],

            "sessions":
                market_data[
                    "sessions"
                ],

            "pivots":
                market_data[
                    "pivots"
                ],

            "ai":
                ai,

            "timestamp":
                market_data[
                    "timestamp"
                ]
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

            "price":
                None,

            "market_status":
                "ERROR",

            "price_source":
                "NONE",

            "live_quote_available":
                False,

            "error":
                str(e),

            "ai": {

                "bias":
                    "NO_TRADE",

                "confidence":
                    0,

                "regime":
                    "ERROR",

                "primary_setup":
                    "NONE",

                "entry":
                    0,

                "sl":
                    0,

                "tp1":
                    0,

                "tp2":
                    0,

                "tp3":
                    0,

                "rr":
                    0,

                "trigger":
                    "",

                "invalidation":
                    "",

                "htf_bias":
                    "",

                "intraday_bias":
                    "",

                "execution_bias":
                    "",

                "key_level":
                    0,

                "reason":
                    "Historical market data unavailable",

                "no_trade_reason":
                    str(e)
            }
        }


# ============================================================
# ALL MARKETS
# ============================================================

def analyze_all():

    results = []

    for (
        display_symbol,
        api_symbol
    ) in WATCHLIST:

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
# ROOT
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

        "dashboard_data":
            "/dashboard-data",

        "api":
            "/api?symbol=EURUSD",

        "watchlist":
            [
                x[0]
                for x in WATCHLIST
            ]
    }


# ============================================================
# API
# ============================================================

@app.get("/api")
def api_analysis(
    symbol: str
):

    normalized = normalize_symbol(
        symbol
    )

    return build_market_data(

        symbol.upper(),

        normalized
    )


# ============================================================
# DASHBOARD DATA
# ============================================================

@app.get(
    "/dashboard-data"
)
def dashboard_data():

    return analyze_all()


# ============================================================
# FORMAT HELPERS
# ============================================================

def fmt_price(value):

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


def fmt_number(value):

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


def market_class(
    status
):

    if status == "OPEN":

        return "open"

    if (
        status
        == "CLOSED_OR_LIVE_QUOTE_UNAVAILABLE"
    ):

        return "closed"

    return "error"


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


    for item in data[
        "symbols"
    ]:

        symbol = item[
            "symbol"
        ]

        price = item.get(
            "price"
        )

        market_status = item.get(
            "market_status",
            "UNKNOWN"
        )

        price_source = item.get(
            "price_source",
            "UNKNOWN"
        )

        last_candle = item.get(
            "last_completed_candle",
            "—"
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

        htf_bias = ai.get(
            "htf_bias",
            ""
        )

        intraday_bias = ai.get(
            "intraday_bias",
            ""
        )


        bias_cls = bias_class(
            bias
        )

        market_cls = market_class(
            market_status
        )


        row = f"""

<tr>

<td class="symbol">
    {html.escape(str(symbol))}
</td>


<td class="price">
    {fmt_price(price)}
</td>


<td>

    <span class="badge {market_cls}">

        {html.escape(
            str(market_status)
        )}

    </span>

    <div class="small">

        {html.escape(
            str(price_source)
        )}

    </div>

</td>


<td>

    <span class="badge {bias_cls}">

        {html.escape(
            str(bias)
        )}

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
    {fmt_price(tp3)}
</td>


<td>
    {fmt_number(rr)}
</td>


<td class="reason">

    <strong>
        {html.escape(str(reason))}
    </strong>

    <div class="small">

        HTF:
        {html.escape(
            str(htf_bias)
        )}

        <br>

        Intraday:
        {html.escape(
            str(intraday_bias)
        )}

    </div>

    <div class="small">

        <strong>
            Trigger:
        </strong>

        {html.escape(
            str(trigger)
        )}

    </div>

    <div class="small">

        <strong>
            Invalidation:
        </strong>

        {html.escape(
            str(invalidation)
        )}

    </div>

</td>


<td class="small">

    Last candle:

    <br>

    {html.escape(
        str(last_candle)
    )}

</td>

</tr>
"""

        rows.append(
            row
        )


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

<title>
Trading AI Market Dashboard
</title>


<style>

* {{
    box-sizing:
        border-box;
}}


body {{

    margin:
        0;

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
        flex-start;

    gap:
        20px;

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

    font-size:
        13px;
}}


.refresh {{

    display:
        inline-block;

    padding:
        10px 14px;

    border:
        1px solid #344150;

    border-radius:
        6px;

    color:
        #e8edf3;

    text-decoration:
        none;

    background:
        #151d27;
}}


.refresh:hover {{

    background:
        #1c2733;
}}


table {{

    width:
        100%;

    min-width:
        1750px;

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
        11px;

    text-transform:
        uppercase;

    letter-spacing:
        .05em;

    position:
        sticky;

    top:
        0;

    z-index:
        2;
}}


td {{

    padding:
        13px 12px;

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


.price {{

    font-weight:
        600;

    white-space:
        nowrap;
}}


.badge {{

    display:
        inline-block;

    padding:
        5px 9px;

    border-radius:
        5px;

    font-weight:
        700;

    font-size:
        10px;

    white-space:
        nowrap;
}}


/* BUY */

.buy {{

    background:
        #173b2b;

    color:
        #61e6a4;
}}


/* SELL */

.sell {{

    background:
        #452023;

    color:
        #ff7d87;
}}


/* WAIT / NO TRADE */

.wait {{

    background:
        #3a3420;

    color:
        #f0ce69;
}}


/* OPEN */

.open {{

    background:
        #18372b;

    color:
        #69e3a1;
}}


/* CLOSED */

.closed {{

    background:
        #3a3420;

    color:
        #f0ce69;
}}


/* ERROR */

.error {{

    background:
        #452023;

    color:
        #ff7d87;
}}


.reason {{

    min-width:
        380px;

    max-width:
        550px;

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

    line-height:
        1.45;
}}


.footer {{

    margin-top:
        15px;

    color:
        #687585;

    font-size:
        12px;
}}


.legend {{

    display:
        flex;

    gap:
        15px;

    margin-top:
        12px;

    flex-wrap:
        wrap;
}}


.legend span {{

    font-size:
        11px;

    color:
        #8d99a8;
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

11 instruments ·
multi-timeframe analysis ·
AI interpretation

</div>


<div class="legend">

<span>
BUY = bullish setup
</span>

<span>
SELL = bearish setup
</span>

<span>
WAIT = confirmation required
</span>

<span>
CLOSED = last available data
</span>

</div>

</div>


<div>

<div class="subtitle">

Updated:
{html.escape(generated)}

</div>


<br>


<a
    class="refresh"
    href="/dashboard"
>
Refresh analysis
</a>

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
Market
</th>

<th>
AI Bias
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
TP3
</th>

<th>
RR
</th>

<th>
AI Interpretation
</th>

<th>
Last Candle
</th>

</tr>

</thead>


<tbody>

{"".join(rows)}

</tbody>


</table>


<div class="footer">

Historical market data:
Twelve Data.

AI interpretation:
OpenAI Responses API.

Cache:
{CACHE_TTL_SECONDS} seconds.

Market closed does NOT mean data unavailable.
The dashboard uses the last completed historical
candle when a live quote is unavailable.

</div>


</div>


</body>

</html>

"""


    return HTMLResponse(
        content=page
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get(
    "/health"
)
def health():

    return {

        "status":
            "ok",

        "twelve_data_key":
            bool(
                TWELVE_DATA_API_KEY
            ),

        "openai_key":
            bool(
                OPENAI_API_KEY
            ),

        "openai_model":
            OPENAI_MODEL,

        "cache_ttl_seconds":
            CACHE_TTL_SECONDS
    }
