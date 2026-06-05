"""
기술적 지표 계산 (yfinance 일봉 기반)
이동평균(MA), RSI, 최근 고저점, 진입여력, 과열/과매도 등.
네트워크 실패 시 None을 반환하므로 호출부에서 방어적으로 처리.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

import yfinance as yf

_CACHE: Dict[str, tuple] = {}


def _cache_get(k, ttl):
    it = _CACHE.get(k)
    if it and time.time() - it[0] < ttl:
        return it[1]
    return None


def _cache_set(k, v):
    _CACHE[k] = (time.time(), v)


def get_daily(yahoo_sym: str, days: int = 80) -> Optional[Dict[str, list]]:
    """최근 일봉 종가/거래량/고가/저가 리스트."""
    ck = f"daily:{yahoo_sym}:{days}"
    cached = _cache_get(ck, ttl=600)
    if cached is not None:
        return cached
    try:
        period = "6mo" if days <= 130 else "1y"
        hist = yf.Ticker(yahoo_sym).history(period=period, interval="1d")
        if hist is None or len(hist) < 10:
            _cache_set(ck, None)
            return None
        out = {
            "close": [float(x) for x in hist["Close"].tolist()],
            "volume": [float(x) for x in hist["Volume"].tolist()],
            "high": [float(x) for x in hist["High"].tolist()],
            "low": [float(x) for x in hist["Low"].tolist()],
        }
        _cache_set(ck, out)
        return out
    except Exception:
        _cache_set(ck, None)
        return None


def _ma(vals: List[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def _rsi(closes: List[float], n: int = 14) -> Optional[float]:
    if len(closes) < n + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(-n, 0):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100 - (100 / (1 + rs))


def indicators(daily: Dict[str, list], price: float, change_pct: float,
               market: str) -> Dict:
    """일봉 + 현재가/등락률로 지표 묶음 산출 (모두 0~1 또는 의미값)."""
    closes = daily["close"]
    vols = daily["volume"]
    highs = daily["high"]
    if price is None and closes:
        price = closes[-1]
    ma5, ma20, ma60 = _ma(closes, 5), _ma(closes, 20), _ma(closes, 60)
    rsi = _rsi(closes, 14)
    hi_recent = max(highs[-40:]) if len(highs) >= 1 else price
    lo_recent = min(closes[-40:]) if len(closes) >= 1 else price
    avg_vol20 = _ma(vols, 20) or (vols[-1] if vols else 0)

    # 진입여력: 최근 고점 대비 남은 공간(0~1). 고점에 가까울수록 작음.
    room = 0.0
    if hi_recent and price:
        room = max(0.0, min(1.0, (hi_recent - price) / hi_recent))

    # 상한가/고점잠김 여부: 한국 +29% 이상이면 사실상 상한가
    limit_locked = False
    if market == "KR" and change_pct is not None and change_pct >= 29.0:
        limit_locked = True
    # 당일 급등(미국 포함): +15% 이상이면 과열 진입 부담
    overheated_today = change_pct is not None and change_pct >= 15.0

    # 이평 대비 위치
    above_ma20 = (price / ma20 - 1) if (ma20 and price) else 0.0
    above_ma60 = (price / ma60 - 1) if (ma60 and price) else 0.0

    # 거래량 서지
    vol_surge = (vols[-1] / avg_vol20) if (avg_vol20 and vols) else 1.0

    return {
        "price": price, "ma5": ma5, "ma20": ma20, "ma60": ma60, "rsi": rsi,
        "hiRecent": hi_recent, "loRecent": lo_recent,
        "room": room, "limitLocked": limit_locked, "overheatedToday": overheated_today,
        "aboveMa20": above_ma20, "aboveMa60": above_ma60, "volSurge": vol_surge,
    }
