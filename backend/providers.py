"""
주식 데이터 프로바이더
- 한국(KR): 전체 시장 등락률 상위는 pykrx, 시세/차트는 yfinance(.KS/.KQ)
- 미국(US): yfinance (시세/차트/등락률 상위 screener)
- pykrx 로그인(KRX_ID/KRX_PW)이 없으면 주요 종목 묶음으로 자동 폴백
모든 함수는 네트워크 실패에 대해 방어적으로 작성됨.
"""
from __future__ import annotations

import datetime as _dt
import time
from functools import lru_cache
from typing import Dict, List, Optional

import yfinance as yf

# ---------------------------------------------------------------------------
# 캐시 (간단한 TTL 메모리 캐시) — 같은 호출을 짧은 시간 반복할 때 외부망 부하/지연 감소
# ---------------------------------------------------------------------------
_CACHE: Dict[str, tuple] = {}


def _cache_get(key: str, ttl: int):
    item = _CACHE.get(key)
    if item and (time.time() - item[0]) < ttl:
        return item[1]
    return None


def _cache_set(key: str, value):
    _CACHE[key] = (time.time(), value)


# ---------------------------------------------------------------------------
# 한국 주요 종목 폴백 목록 (pykrx 로그인 없이도 동작) — 코드: (이름, 야후접미사)
# KOSPI -> .KS, KOSDAQ -> .KQ
# ---------------------------------------------------------------------------
KR_FALLBACK: Dict[str, tuple] = {
    "005930": ("삼성전자", ".KS"),
    "000660": ("SK하이닉스", ".KS"),
    "373220": ("LG에너지솔루션", ".KS"),
    "207940": ("삼성바이오로직스", ".KS"),
    "005380": ("현대차", ".KS"),
    "000270": ("기아", ".KS"),
    "068270": ("셀트리온", ".KS"),
    "005490": ("POSCO홀딩스", ".KS"),
    "035420": ("NAVER", ".KS"),
    "035720": ("카카오", ".KS"),
    "051910": ("LG화학", ".KS"),
    "006400": ("삼성SDI", ".KS"),
    "105560": ("KB금융", ".KS"),
    "055550": ("신한지주", ".KS"),
    "012330": ("현대모비스", ".KS"),
    "028260": ("삼성물산", ".KS"),
    "066570": ("LG전자", ".KS"),
    "003670": ("포스코퓨처엠", ".KS"),
    "096770": ("SK이노베이션", ".KS"),
    "017670": ("SK텔레콤", ".KS"),
    "015760": ("한국전력", ".KS"),
    "034730": ("SK", ".KS"),
    "032830": ("삼성생명", ".KS"),
    "018260": ("삼성에스디에스", ".KS"),
    "010130": ("고려아연", ".KS"),
    "011200": ("HMM", ".KS"),
    "086790": ("하나금융지주", ".KS"),
    "009150": ("삼성전기", ".KS"),
    "247540": ("에코프로비엠", ".KQ"),
    "086520": ("에코프로", ".KQ"),
    "091990": ("셀트리온헬스케어", ".KQ"),
    "066970": ("엘앤에프", ".KQ"),
    "022100": ("포스코DX", ".KQ"),
    "028300": ("HLB", ".KQ"),
    "196170": ("알테오젠", ".KQ"),
    "263750": ("펄어비스", ".KQ"),
    "293490": ("카카오게임즈", ".KQ"),
    "041510": ("에스엠", ".KQ"),
    "035900": ("JYP Ent.", ".KQ"),
    "112040": ("위메이드", ".KQ"),
}

# 미국 주요 종목 폴백 (screener 실패 시 등락률 계산 대상)
US_FALLBACK: List[str] = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "AMD",
    "NFLX", "INTC", "CRM", "ADBE", "ORCL", "QCOM", "CSCO", "PEP", "COST",
    "JPM", "BAC", "WMT", "DIS", "BA", "XOM", "PLTR", "UBER", "SHOP", "COIN",
    "MU", "MRNA",
]

# 지수 / 환율
INDEX_SYMBOLS = {
    "KR": [("^KS11", "코스피"), ("^KQ11", "코스닥")],
    "US": [("^GSPC", "S&P 500"), ("^IXIC", "나스닥"), ("^DJI", "다우존스")],
}
FX_SYMBOLS = [("KRW=X", "원/달러"), ("EURKRW=X", "원/유로"), ("JPYKRW=X", "원/엔(100)")]


# ---------------------------------------------------------------------------
# pykrx (선택적) — 있으면 사용, 로그인 실패/미설치 시 None 반환
# ---------------------------------------------------------------------------
def _pykrx_available() -> bool:
    try:
        import os
        return bool(os.getenv("KRX_ID") and os.getenv("KRX_PW"))
    except Exception:
        return False


def _safe_round(v, n=2):
    try:
        if v is None:
            return None
        return round(float(v), n)
    except Exception:
        return None


def yahoo_symbol(code: str, market: str) -> str:
    """KR 6자리코드 -> 야후 심볼(.KS/.KQ). US는 그대로."""
    if market == "US":
        return code
    if code.endswith((".KS", ".KQ")):
        return code
    suffix = KR_FALLBACK.get(code, (None, ".KS"))[1]
    return f"{code}{suffix}"


# ---------------------------------------------------------------------------
# 시세 (Quote)
# ---------------------------------------------------------------------------
def get_quote(code: str, market: str) -> Dict:
    sym = yahoo_symbol(code, market)
    ck = f"quote:{sym}"
    cached = _cache_get(ck, ttl=20)
    if cached:
        return cached
    out = {"symbol": code, "market": market, "name": _name_of(code, market),
           "price": None, "change": None, "changePct": None, "currency": None,
           "prevClose": None, "volume": None}
    try:
        t = yf.Ticker(sym)
        fi = t.fast_info
        price = _fi(fi, "last_price", "lastPrice")
        prev = _fi(fi, "previous_close", "previousClose")
        out["price"] = _safe_round(price)
        out["prevClose"] = _safe_round(prev)
        out["currency"] = _fi(fi, "currency", "currency") or ("KRW" if market == "KR" else "USD")
        out["volume"] = _fi(fi, "last_volume", "lastVolume")
        if price is not None and prev:
            out["change"] = _safe_round(price - prev)
            out["changePct"] = _safe_round((price - prev) / prev * 100)
    except Exception as e:
        out["error"] = str(e)[:120]
    _cache_set(ck, out)
    return out


def _fi(fast_info, snake, camel):
    """fast_info 는 버전에 따라 dict-유사/속성 접근이 달라 양쪽 시도."""
    for key in (snake, camel):
        try:
            if hasattr(fast_info, "get"):
                v = fast_info.get(key)
                if v is not None:
                    return v
        except Exception:
            pass
        try:
            v = getattr(fast_info, key)
            if v is not None:
                return v
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# 차트 (기간: 1d / 1w / 1m / 1y)
# ---------------------------------------------------------------------------
_PERIOD_MAP = {
    "1d": ("1d", "5m"),
    "1w": ("5d", "30m"),
    "1m": ("1mo", "1d"),
    "1y": ("1y", "1d"),
}


def get_chart(code: str, market: str, period: str = "1d") -> Dict:
    sym = yahoo_symbol(code, market)
    yperiod, interval = _PERIOD_MAP.get(period, ("1d", "5m"))
    ck = f"chart:{sym}:{period}"
    ttl = 30 if period == "1d" else 600
    cached = _cache_get(ck, ttl=ttl)
    if cached:
        return cached
    out = {"symbol": code, "market": market, "period": period,
           "name": _name_of(code, market), "points": []}
    try:
        t = yf.Ticker(sym)
        hist = t.history(period=yperiod, interval=interval)
        for idx, row in hist.iterrows():
            ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
            out["points"].append({
                "t": ts.isoformat(),
                "close": _safe_round(row.get("Close")),
                "open": _safe_round(row.get("Open")),
                "high": _safe_round(row.get("High")),
                "low": _safe_round(row.get("Low")),
                "volume": int(row.get("Volume")) if row.get("Volume") == row.get("Volume") else 0,
            })
    except Exception as e:
        out["error"] = str(e)[:120]
    _cache_set(ck, out)
    return out


# ---------------------------------------------------------------------------
# 이름 조회
# ---------------------------------------------------------------------------
def _name_of(code: str, market: str) -> str:
    if market == "KR":
        if code in KR_FALLBACK:
            return KR_FALLBACK[code][0]
        return code
    return code  # US는 심볼 자체가 이름 역할 (상세에서 longName 보강 가능)


# ---------------------------------------------------------------------------
# 등락률 상위 (Top gainers)
# ---------------------------------------------------------------------------
def get_top_gainers(market: str, limit: int = 10) -> List[Dict]:
    ck = f"gainers:{market}:{limit}"
    cached = _cache_get(ck, ttl=60)
    if cached:
        return cached
    if market == "US":
        res = _us_gainers(limit)
    else:
        res = _kr_gainers(limit)
    _cache_set(ck, res)
    return res


def _us_gainers(limit: int) -> List[Dict]:
    # 1) yfinance 사전정의 screener (day_gainers)
    try:
        data = yf.screen("day_gainers", count=max(limit, 25))
        quotes = data.get("quotes", []) if isinstance(data, dict) else []
        out = []
        for q in quotes:
            sym = q.get("symbol")
            if not sym:
                continue
            out.append({
                "symbol": sym, "market": "US",
                "name": q.get("shortName") or q.get("longName") or sym,
                "price": _safe_round(q.get("regularMarketPrice")),
                "change": _safe_round(q.get("regularMarketChange")),
                "changePct": _safe_round(q.get("regularMarketChangePercent")),
                "volume": q.get("regularMarketVolume"),
            })
        if out:
            return out[:limit]
    except Exception:
        pass
    # 2) 폴백: 주요 종목 등락률 계산
    return _gainers_from_list([(s, "US") for s in US_FALLBACK], limit)


def _kr_gainers(limit: int) -> List[Dict]:
    # 1) pykrx: 전체 시장 등락률 (로그인 필요)
    if _pykrx_available():
        try:
            res = _kr_gainers_pykrx(limit)
            if res:
                return res
        except Exception:
            pass
    # 2) 폴백: 주요 종목 묶음을 yfinance로 등락률 계산
    pairs = [(code, "KR") for code in KR_FALLBACK.keys()]
    return _gainers_from_list(pairs, limit)


def _kr_gainers_pykrx(limit: int) -> List[Dict]:
    from pykrx import stock
    today, prev = _recent_two_bdays()
    rows: List[Dict] = []
    for mkt in ("KOSPI", "KOSDAQ"):
        try:
            df = stock.get_market_price_change(prev, today, market=mkt)
        except Exception:
            continue
        suffix = ".KS" if mkt == "KOSPI" else ".KQ"
        for code, r in df.iterrows():
            rows.append({
                "symbol": code, "market": "KR",
                "name": r.get("종목명") or _name_of(code, "KR"),
                "price": _safe_round(r.get("종가")),
                "change": _safe_round(r.get("변동폭")),
                "changePct": _safe_round(r.get("등락률")),
                "volume": int(r.get("거래량")) if r.get("거래량") == r.get("거래량") else None,
                "_suffix": suffix,
            })
    rows = [x for x in rows if x.get("changePct") is not None]
    rows.sort(key=lambda x: x["changePct"], reverse=True)
    # 야후 접미사 보강 (차트/시세 연동용으로 KR_FALLBACK 갱신)
    for x in rows[:limit]:
        KR_FALLBACK.setdefault(x["symbol"], (x["name"], x.pop("_suffix", ".KS")))
        x.pop("_suffix", None)
    return rows[:limit]


def _gainers_from_list(pairs: List[tuple], limit: int) -> List[Dict]:
    out = []
    for code, market in pairs:
        q = get_quote(code, market)
        if q.get("changePct") is not None:
            out.append({
                "symbol": code, "market": market, "name": q["name"],
                "price": q["price"], "change": q["change"],
                "changePct": q["changePct"], "volume": q.get("volume"),
            })
    out.sort(key=lambda x: x["changePct"], reverse=True)
    return out[:limit]


def _recent_two_bdays():
    """오늘 포함 최근 거래일과 그 전 거래일(영업일 근사: 주말 제외)."""
    d = _dt.date.today()
    bdays = []
    while len(bdays) < 2:
        if d.weekday() < 5:  # 0~4 = 월~금
            bdays.append(d.strftime("%Y%m%d"))
        d -= _dt.timedelta(days=1)
    today, prev = bdays[0], bdays[1]
    return today, prev


# ---------------------------------------------------------------------------
# 지수 / 환율
# ---------------------------------------------------------------------------
def get_indices() -> Dict:
    ck = "indices"
    cached = _cache_get(ck, ttl=60)
    if cached:
        return cached
    out = {"KR": [], "US": [], "FX": []}
    for grp in ("KR", "US"):
        for sym, label in INDEX_SYMBOLS[grp]:
            out[grp].append(_index_quote(sym, label))
    for sym, label in FX_SYMBOLS:
        out["FX"].append(_index_quote(sym, label))
    _cache_set(ck, out)
    return out


def _index_quote(sym: str, label: str) -> Dict:
    o = {"symbol": sym, "name": label, "price": None, "change": None, "changePct": None}
    try:
        t = yf.Ticker(sym)
        fi = t.fast_info
        price = _fi(fi, "last_price", "lastPrice")
        prev = _fi(fi, "previous_close", "previousClose")
        o["price"] = _safe_round(price)
        if price is not None and prev:
            o["change"] = _safe_round(price - prev)
            o["changePct"] = _safe_round((price - prev) / prev * 100)
    except Exception as e:
        o["error"] = str(e)[:80]
    return o


# ---------------------------------------------------------------------------
# 검색
# ---------------------------------------------------------------------------
def search_symbols(query: str, market: Optional[str] = None) -> List[Dict]:
    q = query.strip()
    results: List[Dict] = []
    # 1) KR 로컬 사전 검색 (코드/이름)
    if market in (None, "KR"):
        for code, (name, suffix) in KR_FALLBACK.items():
            if q in code or q in name:
                results.append({"symbol": code, "market": "KR", "name": name})
    # 2) yfinance Search (미국/해외)
    if market in (None, "US"):
        try:
            sr = yf.Search(q, max_results=10, news_count=0)
            for item in getattr(sr, "quotes", []) or []:
                sym = item.get("symbol")
                if not sym:
                    continue
                results.append({
                    "symbol": sym,
                    "market": "US",
                    "name": item.get("shortname") or item.get("longname") or sym,
                    "exchange": item.get("exchDisp"),
                })
        except Exception:
            pass
    # 중복 제거
    seen, uniq = set(), []
    for r in results:
        k = (r["market"], r["symbol"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq[:20]
