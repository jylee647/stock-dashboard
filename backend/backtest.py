"""
백테스트(재설계 v3) — '지금 사면 좋은 종목' 추천 + 그 방식의 과거 적중률·리스크 검증.
- 상단: 이 선정 방식이 과거 N개월간 적중률 X%, 평균수익 +Y% (지수 대비), 최대낙폭·손익비 포함
- 본문: 오늘의 추천 종목 리스트 = 추천엔진 결과 + 목표가/손절가/보유기간

[정직성 원칙 — v3]
 - 단기 방향 예측은 out-of-sample로 동전던지기(≈50%)임이 자체 백테스트로 확인됨.
   따라서 '승률'은 '예측 실력'이 아니라 '상승장 국면 필터 + 추세·품질 선별 + 손절 규칙'의
   결합 결과(상승장 조건부 승률)임을 분명히 표기한다. 진짜 판단 기준은 '지수 대비 초과수익'과
   '최대낙폭(MDD)'이다.
 - winRate/avgReturn 은 부풀리지 않고 실측값 그대로 표기.
 - 리스크 관리: 변동성 기반 비중조절(인버스-볼) + 목표가/손절가 청산으로 낙폭을 통제.

[규칙]
 1) 시장 국면 필터: 지수(^GSPC/^KS11)가 60일선 위일 때만 신규 매수, 하락장은 현금 보유(스킵).
 2) 추세 필터: price>MA20>MA60 + 20일 모멘텀 양호만 선택, 과열(>MA20 +25%) 제외.
 3) 상대강도(RS): 지수 대비 60일 초과수익이 양(+)인 주도주만 매수.
 4) 목표가/손절가 청산: 변동성(일간표준편차×√기간) 밴드 기반, 보상:위험 = 2.0:1.6.
 5) 비중조절: 변동성이 큰 종목일수록 작게(인버스-볼) 담아 포트폴리오 낙폭을 낮춘다.

※ 뉴스는 과거 재현이 불가해 '과거 적중률'은 가격지표 기반 재현 검증값입니다.
※ 투자 자문이 아니며 미래 수익을 보장하지 않습니다.
"""
from __future__ import annotations

import math
import time
from typing import Dict, List

import numpy as np
import pandas as pd
import yfinance as yf

from backend import providers, recommend, signals

_CACHE: Dict[str, tuple] = {}
_BENCH = {"KR": "^KS11", "US": "^GSPC"}
_BENCH_NAME = {"KR": "코스피", "US": "S&P 500"}
_HOLD_LABEL = {5: "1주", 20: "1달", 42: "2달", 60: "3달"}

_TP_MULT = 2.0
_SL_MULT = 1.6
_BE_TRIGGER_MULT = 1.0  # +1.0xband 도달 시 SL을 진입가(BE)로 이동


def _universe(market: str) -> Dict[str, str]:
    if market == "US":
        return {s: s for s in providers.US_FALLBACK}
    return {f"{code}{suf}": name for code, (name, suf) in providers.KR_FALLBACK.items()}


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = d.clip(lower=0).rolling(n).mean()
    loss = (-d.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9) -> pd.Series:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal = macd.ewm(span=sig, adjust=False).mean()
    return macd - signal  # MACD 히스토그램


def _max_drawdown(curve: List[float]) -> float:
    """자본곡선(1.0 시작)에서 최대낙폭(%) — 양수로 반환(예: 12.3 = -12.3% 낙폭)."""
    peak = -1e9
    mdd = 0.0
    for v in curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v / peak) - 1.0
            if dd < mdd:
                mdd = dd
    return round(-mdd * 100, 2)


def run_backtest(market: str, months: int = 24, hold: int = 42, top: int = 10) -> Dict:
    market = market.upper()
    months = max(1, min(int(months), 36))
    hold = max(5, min(int(hold), 60))
    top = max(1, min(int(top), 15))
    ck = f"bt4:{market}:{months}:{hold}:{top}"
    c = _CACHE.get(ck)
    if c and time.time() - c[0] < 900:
        return c[1]

    validation = _validate(market, months, hold, top)
    picks = _today_picks(market, hold, top)

    out = {
        "market": market, "months": months, "hold": hold,
        "holdLabel": _HOLD_LABEL.get(hold, f"{hold}거래일"),
        "validation": validation,
        "picks": picks,
        "honestNote": (
            "‘승률’은 예측 실력이 아니라 ‘상승장 국면에서만 진입 + 추세·품질 선별 + 손절 규칙’이 "
            "결합된 결과입니다(상승장 조건부). 단기 방향 자체는 예측하기 어렵습니다. 실제 판단 기준은 "
            "‘지수 대비 초과수익(excessReturn)’과 ‘최대낙폭(maxDrawdown)’입니다 — 초과수익이 0 근처거나 "
            "음수면, 그냥 지수를 사는 편이 낫다는 뜻입니다."
        ),
        "disclaimer": (
            "‘과거 적중률’은 동일한 규칙(시장국면 필터 + 상승추세·모멘텀·상대강도 선별 + 목표가/손절가 청산 "
            "+ 변동성 비중조절)으로 과거를 재현해 검증한 값입니다. 하락장에서는 신규 매수를 건너뛰므로(현금 보유) "
            "지수와 단순 비교가 어려울 수 있고, 뉴스 등 일부 요소는 과거 재현이 불가해 제외했습니다. "
            "목표가·손절가는 최근 변동성 기반 참고치입니다. 투자 자문이 아니며 미래 수익을 보장하지 않습니다."
        ),
    }
    _CACHE[ck] = (time.time(), out)
    return out


def _validate(market: str, months: int, hold: int, top: int) -> Dict:
    uni = _universe(market)
    syms = list(uni.keys())
    bench = _BENCH[market]
    period = f"{months + 12}mo"
    try:
        raw = yf.download(syms + [bench], period=period, interval="1d",
                          auto_adjust=True, progress=False, group_by="ticker")
    except Exception as e:
        return {"error": f"데이터 다운로드 실패: {str(e)[:100]}"}

    close, high, low, vol = {}, {}, {}, {}
    for s in syms:
        try:
            sub = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
            cl = sub["Close"].dropna()
            if len(cl) > 65:
                close[s] = cl
                high[s] = sub["High"].reindex(cl.index)
                low[s] = sub["Low"].reindex(cl.index)
                vol[s] = sub["Volume"].reindex(cl.index)
        except Exception:
            continue
    if len(close) < 5:
        return {"error": "유효 종목 데이터 부족(네트워크 확인)"}

    closeDF = pd.DataFrame(close).sort_index()
    highDF = pd.DataFrame(high).reindex(closeDF.index)
    lowDF = pd.DataFrame(low).reindex(closeDF.index)
    try:
        benchS = (raw[bench]["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw["Close"]).reindex(closeDF.index).ffill()
    except Exception:
        benchS = closeDF.mean(axis=1)
    benchMA60 = benchS.rolling(60).mean()
    benchMA200 = benchS.rolling(200).mean()

    ma20 = closeDF.rolling(20).mean()
    ma60 = closeDF.rolling(60).mean()
    ma200 = closeDF.rolling(200).mean()
    mom20 = closeDF.pct_change(20)
    ret60 = closeDF.pct_change(60)
    benchRet60 = benchS.pct_change(60)
    dstd = closeDF.pct_change().rolling(20).std()
    rsiDF = closeDF.apply(lambda c: _rsi(c, 14))

    dates = closeDF.index
    warm = 200
    if len(dates) <= warm + hold + 1:
        return {"error": "기간이 짧습니다. 개월 수를 늘려보세요."}

    rebal = list(range(warm, len(dates) - hold, hold))
    pick_rets: List[float] = []
    bench_rets: List[float] = []
    equity = 1.0
    equity_curve: List[float] = [1.0]
    bench_equity = 1.0
    bench_curve: List[float] = [1.0]
    skipped_regime = 0
    skipped_nopick = 0
    exit_tp = 0
    exit_sl = 0
    exit_time = 0

    for t in rebal:
        bpx, bma = benchS.iloc[t], benchMA60.iloc[t]
        bma200 = benchMA200.iloc[t]
        if not (np.isfinite(bpx) and np.isfinite(bma) and np.isfinite(bma200)
                and bpx > bma and bpx > bma200):
            skipped_regime += 1
            continue

        breq = benchRet60.iloc[t]
        cand = {}
        for s in closeDF.columns:
            price = closeDF[s].iloc[t]
            m20, m60 = ma20[s].iloc[t], ma60[s].iloc[t]
            m200 = ma200[s].iloc[t]
            mo = mom20[s].iloc[t]
            r60 = ret60[s].iloc[t]
            if not (np.isfinite(price) and np.isfinite(m20) and np.isfinite(m60)
                    and np.isfinite(m200) and m60 > 0 and m200 > 0):
                continue
            a20 = price / m20 - 1
            if not (price > m20 and m20 > m60 and m60 > m200):
                continue
            if not (np.isfinite(mo) and mo > 0):
                continue
            if a20 > 0.25:
                continue
            rsi_v = rsiDF[s].iloc[t]
            if np.isfinite(rsi_v) and rsi_v > 70:
                continue
            rs = (r60 - breq) if (np.isfinite(r60) and np.isfinite(breq)) else 0.0
            if rs <= 0:
                continue
            trend_str = m20 / m60 - 1
            cand[s] = (0.45 * min(rs, 0.6)
                       + 0.35 * min(mo, 0.5)
                       + 0.20 * min(max(trend_str, 0.0), 0.3)
                       - 0.3 * max(0.0, a20 - 0.15))
        if not cand:
            skipped_nopick += 1
            continue
        picks = sorted(cand, key=cand.get, reverse=True)[:top]

        fwd = []
        weights = []
        for s in picks:
            entry = closeDF[s].iloc[t]
            v = dstd[s].iloc[t]
            if not (np.isfinite(entry) and entry > 0):
                continue
            v = v if (np.isfinite(v) and v > 0) else 0.02
            band = max(0.03, min(v * math.sqrt(hold), 0.30))
            target = entry * (1 + _TP_MULT * band)
            stop = entry * (1 - _SL_MULT * band)
            be_trigger = entry * (1 + _BE_TRIGGER_MULT * band)
            be_armed = False
            ret = None
            for k in range(1, hold + 1):
                hi = highDF[s].iloc[t + k]
                lo = lowDF[s].iloc[t + k]
                if np.isfinite(lo) and lo <= stop:
                    ret = stop / entry - 1
                    exit_sl += 1
                    break
                if np.isfinite(hi) and hi >= target:
                    ret = target / entry - 1
                    exit_tp += 1
                    break
                if (not be_armed) and np.isfinite(hi) and hi >= be_trigger:
                    stop = entry
                    be_armed = True
            if ret is None:
                p1 = closeDF[s].iloc[t + hold]
                if not (np.isfinite(p1) and p1 > 0):
                    continue
                ret = p1 / entry - 1
                exit_time += 1
            fwd.append(ret)
            weights.append(1.0 / band)
        if not fwd:
            skipped_nopick += 1
            continue

        pick_rets.extend(fwd)
        w = np.array(weights, dtype=float)
        w = w / w.sum() if w.sum() > 0 else np.ones(len(fwd)) / len(fwd)
        port_ret = float(np.dot(w, np.array(fwd, dtype=float)))
        equity *= (1 + port_ret)
        equity_curve.append(equity)
        b0, b1 = benchS.iloc[t], benchS.iloc[t + hold]
        if np.isfinite(b0) and np.isfinite(b1) and b0 > 0:
            br = b1 / b0 - 1
            bench_rets.append(br)
            bench_equity *= (1 + br)
            bench_curve.append(bench_equity)

    n = len(pick_rets)
    if n == 0:
        return {"error": "조건을 만족하는 매수 시점이 없었습니다(추세 약함/하락장). 기간·종목을 조정해 보세요."}
    wins = [x for x in pick_rets if x > 0]
    losses = [x for x in pick_rets if x < 0]
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    payoff = round(avg_win / abs(avg_loss), 2) if losses and avg_loss != 0 else None
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else None

    strat_total = round((equity - 1) * 100, 2)
    bench_total = round((float(np.prod([1 + b for b in bench_rets])) - 1) * 100, 2) if bench_rets else 0.0
    return {
        "winRate": round(len(wins) / n * 100, 1),
        "avgReturn": round(float(np.mean(pick_rets)) * 100, 2),
        "trades": n,
        "rebalances": len(rebal),
        "stratTotal": strat_total,
        "benchReturn": bench_total,
        "excessReturn": round(strat_total - bench_total, 2),
        "maxDrawdownPct": _max_drawdown(equity_curve),
        "benchMaxDrawdownPct": _max_drawdown(bench_curve),
        "payoffRatio": payoff,
        "profitFactor": profit_factor,
        "avgWinPct": round(avg_win * 100, 2),
        "avgLossPct": round(avg_loss * 100, 2),
        "benchName": _BENCH_NAME[market],
        "bestTrade": round(max(pick_rets) * 100, 2),
        "worstTrade": round(min(pick_rets) * 100, 2),
        "tradedRebalances": len(bench_rets),
        "skippedDownMarket": skipped_regime,
        "skippedNoPick": skipped_nopick,
        "exitTakeProfit": exit_tp,
        "exitStopLoss": exit_sl,
        "exitTimeClose": exit_time,
    }


def _today_picks(market: str, hold: int, top: int) -> List[Dict]:
    try:
        reco = recommend.recommend(market, top).get("items", [])
    except Exception:
        reco = []
    out = []
    for r in reco:
        price = r.get("price")
        move = 0.0
        try:
            ysym = providers.yahoo_symbol(r["symbol"], market)
            daily = signals.get_daily(ysym)
            if daily and len(daily.get("close", [])) > 21:
                closes = daily["close"][-21:]
                rets = [(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes)) if closes[i - 1]]
                if rets:
                    dvol = float(np.std(rets))
                    move = dvol * math.sqrt(hold)
        except Exception:
            pass
        move = max(0.03, min(move, 0.30))
        target = round(price * (1 + _TP_MULT * move), 2) if price else None
        stop = round(price * (1 - _SL_MULT * move), 2) if price else None
        out.append({
            "symbol": r.get("symbol"), "market": market, "name": r.get("name"),
            "price": price, "changePct": r.get("changePct"), "score": r.get("score"),
            "tags": r.get("tags", []), "reason": r.get("reason", ""), "rsi": r.get("rsi"),
            "target": target, "stop": stop,
            "targetPct": round(_TP_MULT * move * 100, 1), "stopPct": round(_SL_MULT * move * 100, 1),
            "holdLabel": _HOLD_LABEL.get(hold, f"{hold}거래일"),
        })
   