"""
관련주 영향·방향 엔진 (테마 단위)
- 같은 테마(#반도체, #2차전지 ...) 안에서 '리더'가 움직이면 '팔로워'가 며칠 뒤 어느 방향으로
  따라가는지를 시차 상관(lead-lag cross-correlation)으로 계산한다.
- 출력: 테마별 리더 + 팔로워(최적 시차·상관·방향) + 현재 방향 신호.
※ 테마 구성종목을 '명시적 큐레이션 맵'으로 관리한다(종목명 키워드 매칭이 아니라).
  → 폴백 유니버스가 작아도 의미 있는 테마 그룹이 항상 만들어진다.
※ 가격 데이터 기반의 통계적 관계이며, 인과나 미래 수익을 보장하지 않는다.
"""
from __future__ import annotations

import time
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

_CACHE: Dict[str, tuple] = {}
_MAX_LAG = 5          # 며칠 뒤까지 따라가는지 (1~5거래일)
_MIN_MEMBERS = 3      # 테마 최소 구성 종목
_MIN_ABS_CORR = 0.15  # 의미 있는 상관 하한

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


def _theme_map(market: str) -> Dict[str, List[Tuple[str, str]]]:
    return _US_THEMES if market == "US" else _KR_THEMES


def _lead_lag(leader: pd.Series, follower: pd.Series) -> tuple:
    """leader 수익률이 follower 수익률을 며칠 선행하는지.
    반환: (best_lag, corr) — corr 부호가 +면 동행, -면 역행."""
    best_lag, best_corr = 0, 0.0
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
        if np.isfinite(c) and abs(c) > abs(best_corr):
            best_lag, best_corr = lag, c
    return best_lag, best_corr


def compute(market: str) -> Dict:
    market = market.upper()
    ck = f"infl2:{market}"
    c = _CACHE.get(ck)
    if c and time.time() - c[0] < 900:
        return c[1]

    tmap = _theme_map(market)
    name_of: Dict[str, str] = {}
    for members in tmap.values():
        for sym, nm in members:
            name_of[sym] = nm
    syms = list(name_of.keys())

    try:
        raw = yf.download(syms, period="8mo", interval="1d",
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
    retDF = closeDF.pct_change()
    ret60 = closeDF.pct_change(60)
    ret5 = closeDF.pct_change(5)

    themes_out = []
    for theme, members in tmap.items():
        msyms = [s for s, _ in members if s in retDF.columns]
        if len(msyms) < _MIN_MEMBERS:
            continue
        scored = [(m, ret60[m].iloc[-1]) for m in msyms if np.isfinite(ret60[m].iloc[-1])]
        if len(scored) < _MIN_MEMBERS:
            continue
        scored.sort(key=lambda x: x[1], reverse=True)
        leader_sym = scored[0][0]
        leader_ret = retDF[leader_sym].dropna()
        leader_move5 = float(ret5[leader_sym].iloc[-1]) if np.isfinite(ret5[leader_sym].iloc[-1]) else 0.0

        followers = []
        for f, _ in scored[1:]:
            fr = retDF[f].dropna()
            idx = leader_ret.index.intersection(fr.index)
            if len(idx) < 40:
                continue
            lag, corr = _lead_lag(leader_ret.reindex(idx), fr.reindex(idx))
            if lag == 0 or abs(corr) < _MIN_ABS_CORR:
                continue
            direction = "동행" if corr > 0 else "역행"
            bias = leader_move5 * corr
            signal = "상승 기대" if bias > 0 else ("하락 주의" if bias < 0 else "중립")
            followers.append({
                "symbol": f, "name": name_of.get(f, f),
                "lag": lag, "corr": round(corr, 2),
                "direction": direction, "signal": signal,
            })
        if not followers:
            continue
        followers.sort(key=lambda x: abs(x["corr"]), reverse=True)
        themes_out.append({
            "theme": theme,
            "members": len(msyms),
            "leader": {"symbol": leader_sym, "name": name_of.get(leader_sym, leader_sym),
                       "ret60Pct": round(scored[0][1] * 100, 1),
                       "move5Pct": round(leader_move5 * 100, 2)},
            "followers": followers[:8],
        })

    themes_out.sort(key=lambda t: t["members"], reverse=True)
    out = {
        "market": market,
        "themes": themes_out,
        "asOf": str(closeDF.index[-1].date()) if len(closeDF) else None,
        "disclaimer": (
            "같은 테마 안에서 리더(최근 60일 모멘텀 1위)가 움직이면 팔로워가 며칠 뒤 어느 방향으로 "
            "따라갔는지를 과거 8개월 시차 상관으로 계산한 값입니다. 통계적 관계이며 인과·미래수익을 "
            "보장하지 않습니다. 투자 자문이 아닙니다."
        ),
    }
    _CACHE[ck] = (time.time(), out)
    return out
