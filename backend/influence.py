"""
관련주 영향·방향 엔진 (테마 단위) — v2 (신뢰도 강화)
핵심 아이디어
- 같은 테마 안에서 '누가 테마를 끌고 가는가(리더)'를 먼저 찾고, 그 리더가 며칠 뒤
  팔로워를 어느 방향으로 움직였는지를 시차 상관(lead-lag)으로 본다.
개선점 (v1 → v2)
 1) 분할·이상치 필터: 한국 일일 등락 한계(±30%)를 넘는 수익률은 분할/조정 아티팩트로
    보고 제거 → 리더 선정·상관이 가짜 점프에 오염되지 않게 함.
 2) 유의성 검정: 각 시차 상관에 표본수 n과 양측 p값을 붙이고, 통계적으로 유의한(p<0.05)
    관계만 신호로 채택. 약한 우연 상관을 걸러낸다.
 3) 리더 = 영향력 중심성: '최근 모멘텀 1위'가 아니라 '테마 안에서 다른 종목을 가장 많이
    선행 설명하는 종목'(유의한 |corr| 합이 최대)을 리더로 선정.
※ 가격 데이터 기반의 통계적 관계이며, 인과나 미래 수익을 보장하지 않는다.
"""
from __future__ import annotations

import math
import time
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

_CACHE: Dict[str, tuple] = {}
_MAX_LAG = 5            # 며칠 뒤까지 따라가는지 (1~5거래일)
_MIN_MEMBERS = 3        # 테마 최소 구성 종목
_MIN_ABS_CORR = 0.15   # 의미 있는 상관 하한(2차 필터)
_ALPHA = 0.05          # 유의수준 (p값 임계)
_DAILY_CAP = 0.35      # 일일 수익률 이상치 컷(±35% 초과는 분할/조정으로 간주)
_MIN_OVERLAP = 40      # 시차상관 계산 최소 공통표본
_TRAIN_FRAC = 0.7      # 방향 적중률 백테스트 학습/검증 분할
_VAL_MIN_OVERLAP = 120 # 검증에 필요한 최소 공통표본(학습+검증)

# ---------------------------------------------------------------------------
# 명시적 테마 구성종목 맵 — (야후심볼, 표시이름)
# ---------------------------------------------------------------------------
_KR_THEMES: Dict[str, List[Tuple[str, str]]] = {
    "#반도체": [
        ("005930.KS", "삼성전자"), ("000660.KS", "SK하이닉스"),
        ("042700.KS", "한미반도체"), ("058470.KQ", "리노공업"),
        ("039030.KQ", "이오테크닉스"), ("000990.KS", "DB하이텍"),
        ("036930.KQ", "주성엔지니어링"), ("009150.KS", "삼성전기"),
    ],
    "#2차전지": [
        ("373220.KS", "LG에너지솔루션"), ("006400.KS", "삼성SDI"),
        ("247540.KQ", "에코프로비엠"), ("086520.KQ", "에코프로"),
        ("066970.KQ", "엘앤에프"), ("003670.KS", "포스코퓨처엠"),
        ("005070.KS", "코스모신소재"), ("278280.KQ", "천보"),
    ],
    "#바이오": [
        ("207940.KS", "삼성바이오로직스"), ("068270.KS", "셀트리온"),
        ("000100.KS", "유한양행"), ("128940.KS", "한미약품"),
        ("196170.KQ", "알테오젠"), ("141080.KQ", "리가켐바이오"),
        ("028300.KQ", "HLB"), ("298380.KQ", "에이비엘바이오"),
    ],
    "#방산": [
        ("012450.KS", "한화에어로스페이스"), ("047810.KS", "한국항공우주"),
        ("079550.KS", "LIG넥스원"), ("064350.KS", "현대로템"),
        ("272210.KS", "한화시스템"),
    ],
    "#원전": [
        ("034020.KS", "두산에너빌리티"), ("052690.KS", "한전기술"),
        ("083650.KQ", "비에이치아이"), ("457550.KQ", "우진엔텍"),
    ],
    "#전력인프라": [
        ("267260.KS", "HD현대일렉트릭"), ("010120.KS", "LS ELECTRIC"),
        ("298040.KS", "효성중공업"), ("033100.KQ", "제룡전기"),
        ("001440.KS", "대한전선"),
    ],
    "#조선": [
        ("009540.KS", "HD한국조선해양"), ("010140.KS", "삼성중공업"),
        ("042660.KS", "한화오션"), ("329180.KS", "HD현대중공업"),
        ("010620.KS", "HD현대미포"),
    ],
    "#게임": [
        ("259960.KS", "크래프톤"), ("036570.KS", "엔씨소프트"),
        ("251270.KS", "넷마블"), ("263750.KQ", "펄어비스"),
        ("293490.KQ", "카카오게임즈"), ("112040.KQ", "위메이드"),
    ],
    "#엔터": [
        ("352820.KS", "하이브"), ("035900.KQ", "JYP Ent."),
        ("041510.KQ", "에스엠"), ("122870.KQ", "와이지엔터테인먼트"),
    ],
    "#금융": [
        ("105560.KS", "KB금융"), ("055550.KS", "신한지주"),
        ("086790.KS", "하나금융지주"), ("316140.KS", "우리금융지주"),
        ("138040.KS", "메리츠금융지주"), ("032830.KS", "삼성생명"),
    ],
    "#자동차": [
        ("005380.KS", "현대차"), ("000270.KS", "기아"),
        ("012330.KS", "현대모비스"), ("204320.KS", "HL만도"),
        ("011210.KS", "현대위아"),
    ],
    "#인터넷": [
        ("035420.KS", "NAVER"), ("035720.KS", "카카오"),
        ("012510.KS", "더존비즈온"), ("181710.KS", "NHN"),
    ],
    "#로봇": [
        ("277810.KQ", "레인보우로보틱스"), ("454910.KS", "두산로보틱스"),
        ("090360.KQ", "로보스타"), ("058610.KQ", "에스피지"),
    ],
    "#철강": [
        ("005490.KS", "POSCO홀딩스"), ("004020.KS", "현대제철"),
        ("460860.KS", "동국제강"), ("103140.KS", "풍산"),
    ],
    "#화장품": [
        ("090430.KS", "아모레퍼시픽"), ("051900.KS", "LG생활건강"),
        ("192820.KS", "코스맥스"), ("161890.KS", "한국콜마"),
        ("214150.KQ", "클래시스"),
    ],
}

_US_THEMES: Dict[str, List[Tuple[str, str]]] = {
    "#Semiconductors": [
        ("NVDA", "NVIDIA"), ("AMD", "AMD"), ("AVGO", "Broadcom"),
        ("MU", "Micron"), ("QCOM", "Qualcomm"), ("INTC", "Intel"),
        ("TSM", "TSMC"), ("ASML", "ASML"),
    ],
    "#BigTechAI": [
        ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("GOOGL", "Alphabet"),
        ("AMZN", "Amazon"), ("META", "Meta"), ("PLTR", "Palantir"),
    ],
    "#EV": [
        ("TSLA", "Tesla"), ("RIVN", "Rivian"), ("LCID", "Lucid"),
        ("F", "Ford"), ("GM", "General Motors"),
    ],
    "#Banks": [
        ("JPM", "JPMorgan"), ("BAC", "Bank of America"), ("WFC", "Wells Fargo"),
        ("C", "Citigroup"), ("GS", "Goldman Sachs"),
    ],
    "#Software": [
        ("CRM", "Salesforce"), ("ADBE", "Adobe"), ("ORCL", "Oracle"),
        ("NOW", "ServiceNow"), ("SNOW", "Snowflake"),
    ],
}

# 일일 등락 한계: 미국은 한계 없음 → 더 느슨하게
_US_DAILY_CAP = 0.60


def _theme_map(market: str) -> Dict[str, List[Tuple[str, str]]]:
    return _US_THEMES if market == "US" else _KR_THEMES


def _pvalue_corr(r: float, n: int) -> float:
    """상관계수 r, 표본 n의 양측 p값(정규근사). n이 충분히 크면 t≈z."""
    if n < 5 or not np.isfinite(r):
        return 1.0
    r = max(min(r, 0.999999), -0.999999)
    t = r * math.sqrt((n - 2) / (1 - r * r))
    # 양측 p ≈ erfc(|t|/sqrt(2))  (n>=40 정규근사 충분)
    return math.erfc(abs(t) / math.sqrt(2.0))


def _lead_lag(leader: pd.Series, follower: pd.Series) -> Tuple[int, float, int, float]:
    """leader 수익률이 follower 수익률을 며칠 선행하는지.
    반환: (best_lag, corr, n, pvalue). corr 부호 +면 동행, -면 역행.
    유의성(p)이 가장 낮은(=가장 강한) 시차를 선택."""
    best = (0, 0.0, 0, 1.0)
    for lag in range(1, _MAX_LAG + 1):
        a = leader.iloc[:-lag]
        b = follower.iloc[lag:]
        n = min(len(a), len(b))
        if n < 20:
            continue
        a = a.iloc[-n:].values
        b = b.iloc[-n:].values
        if np.std(a) == 0 or np.std(b) == 0:
            continue
        c = float(np.corrcoef(a, b)[0, 1])
        if not np.isfinite(c):
            continue
        p = _pvalue_corr(c, n)
        if p < best[3]:
            best = (lag, c, n, p)
    return best


def _clean_returns(closeDF: pd.DataFrame, cap: float) -> pd.DataFrame:
    """일일 수익률에서 ±cap 초과(분할/조정 아티팩트)를 NaN 처리."""
    r = closeDF.pct_change()
    r = r.mask(r.abs() > cap)
    return r


def _clean_momentum(ret: pd.Series, win: int = 60) -> float:
    """이상치 제거된 일일수익률의 최근 win일 누적수익(분할 점프 영향 배제).
    최소표본은 win에 비례(5일 모멘텀이 NaN으로 죽지 않게)."""
    tail = ret.dropna().iloc[-win:]
    need = max(3, int(win * 0.5))
    if len(tail) < need:
        return float("nan")
    return float((1.0 + tail).prod() - 1.0)


def _theme_directional_hits(series: Dict[str, pd.Series], msyms: List[str]):
    """학습 70%로 리더·시차상관 부호를 적합하고, 검증 30%에서 '리더 5일 모멘텀×상관부호'가
    팔로워의 다음 lag일 누적수익 방향을 맞췄는지 out-of-sample로 센다.
    반환: (hits, total)."""
    fit = {}
    infl = {s: 0.0 for s in msyms}
    for i in msyms:
        for j in msyms:
            if i == j:
                continue
            idx = series[i].index.intersection(series[j].index)
            if len(idx) < _VAL_MIN_OVERLAP:
                continue
            cut = int(len(idx) * _TRAIN_FRAC)
            lag, corr, n, p = _lead_lag(series[i].reindex(idx).iloc[:cut],
                                        series[j].reindex(idx).iloc[:cut])
            if lag > 0 and p < _ALPHA and abs(corr) >= _MIN_ABS_CORR:
                fit[(i, j)] = (lag, corr, idx, cut)
                infl[i] += abs(corr)
    if not fit or max(infl.values()) <= 0:
        return 0, 0
    leader = max(infl, key=lambda s: infl[s])
    hits = total = 0
    for j in msyms:
        if (leader, j) not in fit:
            continue
        lag, corr, idx, cut = fit[(leader, j)]
        lead = series[leader].reindex(idx).values
        foll = series[j].reindex(idx).values
        sign_corr = 1.0 if corr > 0 else -1.0
        for t in range(cut, len(idx) - lag):
            window = lead[max(0, t - 4):t + 1]
            if len(window) < 3 or not np.all(np.isfinite(window)):
                continue
            move5 = float(np.prod(1.0 + window) - 1.0)
            fut = foll[t + 1:t + 1 + lag]
            if len(fut) < lag or not np.all(np.isfinite(fut)):
                continue
            fut_ret = float(np.prod(1.0 + fut) - 1.0)
            pred = sign_corr * move5
            if pred == 0 or fut_ret == 0:
                continue
            if (pred > 0) == (fut_ret > 0):
                hits += 1
            total += 1
    return hits, total


def compute(market: str) -> Dict:
    market = market.upper()
    ck = f"infl3:{market}"
    c = _CACHE.get(ck)
    if c and time.time() - c[0] < 900:
        return c[1]

    tmap = _theme_map(market)
    cap = _US_DAILY_CAP if market == "US" else _DAILY_CAP
    name_of: Dict[str, str] = {}
    for members in tmap.values():
        for sym, nm in members:
            name_of[sym] = nm
    syms = list(name_of.keys())

    try:
        raw = yf.download(syms, period="1y", interval="1d",
                          auto_adjust=True, progress=False, group_by="ticker")
    except Exception as e:
        return {"error": f"데이터 다운로드 실패: {str(e)[:100]}"}

    close = {}
    for s in syms:
        try:
            sub = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
            cl = sub["Close"].dropna()
            if len(cl) > 80:
                close[s] = cl
        except Exception:
            continue
    if len(close) < _MIN_MEMBERS:
        return {"error": "유효 종목 데이터 부족(네트워크 확인)"}

    closeDF = pd.DataFrame(close).sort_index()
    retDF = _clean_returns(closeDF, cap)           # 이상치 제거된 일일수익률
    ret5 = {s: _clean_momentum(retDF[s], 5) for s in retDF.columns}

    skipped_outliers = int(retDF.isna().sum().sum() - closeDF.isna().sum().sum())

    themes_out = []
    val_hits = val_total = 0
    theme_val: Dict[str, float] = {}
    for theme, members in tmap.items():
        msyms = [s for s, _ in members if s in retDF.columns]
        if len(msyms) < _MIN_MEMBERS:
            continue
        series = {s: retDF[s].dropna() for s in msyms}
        msyms = [s for s in msyms if len(series[s]) >= _MIN_OVERLAP]
        if len(msyms) < _MIN_MEMBERS:
            continue

        # 방향 적중률 백테스트(학습70/검증30, out-of-sample) 누적
        _h, _t = _theme_directional_hits(series, msyms)
        val_hits += _h
        val_total += _t
        if _t >= 30:
            theme_val[theme] = round(_h / _t * 100, 1)

        # 모든 순서쌍 (i 리더 → j 팔로워) 시차상관 사전계산
        pair: Dict[Tuple[str, str], Tuple[int, float, int, float]] = {}
        for i in msyms:
            for j in msyms:
                if i == j:
                    continue
                idx = series[i].index.intersection(series[j].index)
                if len(idx) < _MIN_OVERLAP:
                    continue
                pair[(i, j)] = _lead_lag(series[i].reindex(idx), series[j].reindex(idx))

        # 영향력 중심성: i가 리더일 때 '유의하고 의미있는' |corr| 합
        infl: Dict[str, float] = {s: 0.0 for s in msyms}
        for (i, j), (lag, corr, n, p) in pair.items():
            if lag > 0 and p < _ALPHA and abs(corr) >= _MIN_ABS_CORR:
                infl[i] += abs(corr)
        leader_sym = max(infl, key=lambda s: infl[s])
        if infl[leader_sym] <= 0:
            continue  # 테마 내 유의한 선행관계 없음 → 신호 없음

        leader_mom = _clean_momentum(retDF[leader_sym], 60)
        leader_move5 = ret5.get(leader_sym, float("nan"))
        leader_move5 = 0.0 if not np.isfinite(leader_move5) else leader_move5

        followers = []
        for j in msyms:
            if j == leader_sym:
                continue
            v = pair.get((leader_sym, j))
            if not v:
                continue
            lag, corr, n, p = v
            if lag == 0 or p >= _ALPHA or abs(corr) < _MIN_ABS_CORR:
                continue
            direction = "동행" if corr > 0 else "역행"
            bias = leader_move5 * corr
            signal = "상승 기대" if bias > 0 else ("하락 주의" if bias < 0 else "중립")
            followers.append({
                "symbol": j, "name": name_of.get(j, j),
                "lag": lag, "corr": round(corr, 2),
                "n": n, "pValue": round(p, 3),
                "direction": direction, "signal": signal,
            })
        if not followers:
            continue
        followers.sort(key=lambda x: x["pValue"])
        themes_out.append({
            "theme": theme,
            "members": len(msyms),
            "leader": {
                "symbol": leader_sym, "name": name_of.get(leader_sym, leader_sym),
                "influence": round(infl[leader_sym], 2),
                "mom60Pct": (round(leader_mom * 100, 1) if np.isfinite(leader_mom) else None),
                "move5Pct": round(leader_move5 * 100, 2),
            },
            "followers": followers[:8],
        })

    # 유의한 팔로워가 많은 테마 우선
    themes_out.sort(key=lambda t: len(t["followers"]), reverse=True)
    out = {
        "market": market,
        "themes": themes_out,
        "asOf": str(closeDF.index[-1].date()) if len(closeDF) else None,
        "method": {
            "leader": "테마 내 영향력 중심성(유의 |corr| 합 최대)",
            "significance": f"양측 p<{_ALPHA}, |corr|>={_MIN_ABS_CORR}",
            "outlierFilter": f"일일 ±{int(cap*100)}% 초과 제거",
            "skippedOutlierDays": skipped_outliers,
            "window": "8개월 일봉, 시차 1~5거래일",
        },
        "validation": {
            "directionalAccuracyPct": (round(val_hits / val_total * 100, 1) if val_total else None),
            "predictions": val_total,
            "baselinePct": 50.0,
            "scheme": "학습70%/검증30% 분할, 검증구간 out-of-sample 방향 적중",
            "perThemePct": theme_val,
        },
        "disclaimer": (
            "같은 테마 안에서 '다른 종목을 가장 많이 선행 설명하는 종목'을 리더로 두고, 리더가 "
            "며칠 뒤 팔로워를 어느 방향으로 움직였는지를 과거 8개월 시차 상관으로 계산해 통계적으로 "
            "유의한(p<0.05) 관계만 표시한 값입니다. 통계적 관계이며 인과·미래수익을 보장하지 않습니다. "
            "투자 자문이 아닙니다."
        ),
    }
    _CACHE[ck] = (time.time(), out)
    return out
