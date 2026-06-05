"""
백테스트 — 과거 N개월 동안 가격기반 점수 상위 종목을 주기적으로 골라 보유했을 때 수익률.
※ 뉴스감성·뉴스량은 과거 데이터를 가져올 수 없어 제외하고, 재현 가능한
   가격 지표(추세건전성·저평가·진입여력·거래량)만으로 점수를 재계산합니다.
※ 알고리즘 검증용이며 미래 수익을 보장하지 않습니다.
"""
from __future__ import annotations

import time
from typing import Dict, List

import numpy as np
import pandas as pd
import yfinance as yf

from backend import providers

_CACHE: Dict[str, tuple] = {}
_BENCH = {"KR": "^KS11", "US": "^GSPC"}
_BENCH_NAME = {"KR": "코스피", "US": "S&P 500"}


def _universe(market: str) -> Dict[str, str]:
    """{야후심볼: 표시이름}"""
    if market == "US":
        return {s: s for s in providers.US_FALLBACK}
    return {f"{code}{suf}": name for code, (name, suf) in providers.KR_FALLBACK.items()}


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = d.clip(lower=0).rolling(n).mean()
    loss = (-d.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def run_backtest(market: str, months: int = 6, hold: int = 20, top: int = 10) -> Dict:
    market = market.upper()
    months = max(1, min(int(months), 18))
    hold = max(2, min(int(hold), 60))
    top = max(1, min(int(top), 15))
    ck = f"bt:{market}:{months}:{hold}:{top}"
    cached = _CACHE.get(ck)
    if cached and time.time() - cached[0] < 1800:
        return cached[1]

    uni = _universe(market)
    syms = list(uni.keys())
    bench = _BENCH[market]
    period = f"{months + 4}mo"  # 지표 워밍업 버퍼

    try:
        raw = yf.download(syms + [bench], period=period, interval="1d",
                          auto_adjust=True, progress=False, group_by="ticker")
    except Exception as e:
        return _err(market, f"데이터 다운로드 실패: {str(e)[:120]}")

    # 종가/거래량 프레임 구성
    close = {}
    vol = {}
    for s in syms:
        try:
            sub = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
            c = sub["Close"].dropna()
            if len(c) > 65:
                close[s] = c
                vol[s] = sub["Volume"].reindex(c.index)
        except Exception:
            continue
    if len(close) < 5:
        return _err(market, "유효한 종목 데이터가 부족합니다 (네트워크/티커 확인).")

    closeDF = pd.DataFrame(close).sort_index()
    volDF = pd.DataFrame(vol).reindex(closeDF.index)
    try:
        benchS = (raw[bench]["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw["Close"]).reindex(closeDF.index).ffill()
    except Exception:
        benchS = closeDF.mean(axis=1)  # 대체: 유니버스 평균

    # 지표 (벡터화)
    ma20 = closeDF.rolling(20).mean()
    ma60 = closeDF.rolling(60).mean()
    rmax40 = closeDF.rolling(40).max()
    vavg20 = volDF.rolling(20).mean()
    rsi = closeDF.apply(_rsi)
    ret1 = closeDF.pct_change()

    dates = closeDF.index
    warm = 60
    if len(dates) <= warm + hold + 1:
        return _err(market, "기간이 짧아 백테스트가 어렵습니다. 개월 수를 늘려보세요.")

    # 리밸런싱 시점
    rebal_idx = list(range(warm, len(dates) - hold, hold))
    equity = 1.0
    bench_eq = 1.0
    curve = []
    trades = []
    wins = 0
    rets = []

    for t in rebal_idx:
        scores = {}
        for s in closeDF.columns:
            price = closeDF[s].iloc[t]
            if not np.isfinite(price):
                continue
            m20, m60 = ma20[s].iloc[t], ma60[s].iloc[t]
            rh = rmax40[s].iloc[t]
            r = rsi[s].iloc[t]
            va = vavg20[s].iloc[t]
            dr = ret1[s].iloc[t]
            if not (np.isfinite(m20) and np.isfinite(rh) and rh > 0):
                continue
            a20 = price / m20 - 1
            a60 = (price / m60 - 1) if np.isfinite(m60) else 0.0
            room = max(0.0, min(1.0, (rh - price) / rh))
            if market == "KR" and np.isfinite(dr) and dr >= 0.29:
                room *= 0.15
            elif np.isfinite(dr) and dr >= 0.15:
                room *= 0.5
            trend = max(0.0, 1 - abs(a20 - 0.05) / 0.25)
            if a20 < -0.15:
                trend *= 0.6
            rr = r if np.isfinite(r) else 50.0
            value = 0.6 * max(0.0, (55 - rr) / 55) + 0.4 * min(max(0.0, -a60) / 0.20, 1.0)
            vs = (closeDF[s].iloc[t] and (volDF[s].iloc[t] / va)) if (np.isfinite(va) and va) else 1.0
            interest = min((vs if np.isfinite(vs) else 1.0) / 3.0, 1.0)
            # 가격기반 가중 (뉴스 제외, 합 1.0)
            scores[s] = 0.28 * trend + 0.28 * value + 0.28 * room + 0.16 * interest

        if not scores:
            continue
        picks = sorted(scores, key=scores.get, reverse=True)[:top]
        # 보유 수익률 (동일 비중)
        fwd = []
        for s in picks:
            p0, p1 = closeDF[s].iloc[t], closeDF[s].iloc[t + hold]
            if np.isfinite(p0) and np.isfinite(p1) and p0 > 0:
                fwd.append(p1 / p0 - 1)
        if not fwd:
            continue
        port_ret = float(np.mean(fwd))
        b0, b1 = benchS.iloc[t], benchS.iloc[t + hold]
        bench_ret = float(b1 / b0 - 1) if (np.isfinite(b0) and np.isfinite(b1) and b0 > 0) else 0.0
        equity *= (1 + port_ret)
        bench_eq *= (1 + bench_ret)
        rets.append(port_ret)
        if port_ret > bench_ret:
            wins += 1
        curve.append({"date": str(dates[t + hold].date()),
                      "strategy": round((equity - 1) * 100, 2),
                      "benchmark": round((bench_eq - 1) * 100, 2)})
        trades.append({"date": str(dates[t].date()),
                       "picks": [_label(s, uni) for s in picks[:top]],
                       "ret": round(port_ret * 100, 2),
                       "benchRet": round(bench_ret * 100, 2)})

    n = len(rets)
    if n == 0:
        return _err(market, "거래가 생성되지 않았습니다. 기간/보유일을 조정해보세요.")
    out = {
        "market": market, "months": months, "hold": hold, "top": top,
        "benchName": _BENCH_NAME[market],
        "stats": {
            "totalReturn": round((equity - 1) * 100, 2),
            "benchReturn": round((bench_eq - 1) * 100, 2),
            "trades": n,
            "winRateVsBench": round(wins / n * 100, 1),
            "avgPerTrade": round(float(np.mean(rets)) * 100, 2),
            "bestTrade": round(max(rets) * 100, 2),
            "worstTrade": round(min(rets) * 100, 2),
        },
        "curve": curve,
        "recentTrades": trades[-6:][::-1],
        "universeSize": len(closeDF.columns),
        "disclaimer": (
            "뉴스 항목은 과거 재현이 불가능해 제외했습니다(가격 지표만 사용). "
            "거래비용·세금·슬리피지·미체결은 반영되지 않았으며, 과거 성과는 미래를 보장하지 않습니다."
        ),
    }
    _CACHE[ck] = (time.time(), out)
    return out


def _label(yahoo_sym: str, uni: Dict[str, str]) -> str:
    return uni.get(yahoo_sym, yahoo_sym)


def _err(market, msg):
    return {"market": market, "error": msg, "curve": [], "stats": None, "recentTrades": []}
