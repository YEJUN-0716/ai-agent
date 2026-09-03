"""선물·암호 추세추종 — **재기 전에 검출력만 잰다.**

    python -m scripts.pilot_futures_power           # MDE 표
    python -m scripts.pilot_futures_power selftest  # 산수 점검

질문: "여러 선물시장에 추세추종(월별 롱/숏)을 걸면, 이 저장소의 자로 검출이 되는가?"

자산배분 노선은 **진폭이 손잡이가 아니다**로 닫혔다 — 비중을 크게 흔들면 효과와
MDE 가 같은 비율로 커진다(docs/measurements/2026-09-03-allocation-power.md).
여기서 묻는 건 다른 손잡이다: **넓이**. 시장이 여러 개고 서로 상관이 낮으면, 같은
위험에서 맞힌 베팅이 독립적으로 쌓이므로 천장은 커지는데 MDE 는(위험을 고정했으니)
안 커진다 — 이게 사실이면 넓이는 검출력을 살 수 있는 첫 손잡이고, 아니면 이 노선도
같은 이유로 닫힌다.

그래서 이 스크립트의 핵심 출력은 MDE 하나가 아니라 **N 을 늘렸을 때 천장/MDE 가
커지는가**다. 자산배분에서는 이 비율이 7.1·7.2·7.4x 로 평평했다.

## 눈가림 — 실제 규칙을 여기서 재지 않는다

`mde_pp` 에 넣는 건 **부호 판정만 무작위인 줄**이다. 시장 수·사이징·리밸런스 시점은
진짜 규칙과 같고 타이밍만 동전던지기다. 방해 모수(분산)는 사이징이 정하고 타이밍이
정하지 않으므로 MDE 는 진짜 규칙의 것과 같고, 효과는 구조적으로 0 이다.
**공개 규칙(12개월 TSMOM)의 실제 성적은 여기서 안 잰다** — 재면 사전 등록 전에
결과를 본 것이 된다. 나오는 건 "이만큼은 맞혀야 검출된다"는 필요 적중률뿐이다.

## 위험을 고정한다

N 이 다른 책들을 그냥 비교하면 MDE 가 N 과 함께 줄어드는 게 당연해서 아무것도
못 배운다. 그래서 **모든 줄을 실현 연변동성 10% 로 사후 재조정**한다.

**천장/MDE 의 절대값은 이 10% 에 딸려온다** — CAGR 이 복리라 상수배가 두 줄에 같은
비율로 안 걸린다(변동성 끌림이 제곱으로 큰다). 실측으로 10%→20% 에서 비율이
10.0→13.8 로 움직였다. 처음엔 안 움직인다고 적었다가 selftest 가 잡았다.
따라서 **행끼리는 같은 TARGET_VOL 에서만 비교하고, 절대값을 다른 문서로 옮기지
않는다.** 이 스크립트가 기대는 건 절대값이 아니라 N 사이의 순서이고, 그 순서가
TARGET_VOL 에 안 흔들린다는 것만 확인한다(selftest 3).

## 함정

- **선물 원계약(`=F`)으로는 못 쟀다 — 자를 바꾼 게 아니라 재료를 바꿨다.**
  yfinance 연결물은 롤 조정이 안 돼 있고, 첫 판에서 세 가지가 동시에 나왔다:
  `6J=F` 2001-12-17 이 0.0079→0.0008→0.0079 (하루 −90% 뒤 +904%, 자릿수 오타),
  `CL=F` 2020-04-20 종가 **−37.63** (마이너스 유가라 수익률 정의 자체가 안 됨),
  `SI=F` 2026-01-30 −31% (진짜 급락인지 롤인지 이 데이터로는 구분 불가).
  **큰 값을 잘라내는 청소는 여기서 금지다** — 추세추종의 수익은 꼬리에 있으므로
  꼬리를 자르면 재려는 대상을 자르는 것이다. 그래서 같은 시장을 **ETF 로 잡는다**:
  총수익 기준(auto_adjust)이고, 가격이 음수가 될 수 없고, 롤이 상품 안에서
  처리되고, 무엇보다 **실제로 살 수 있는 물건**이다. `run()` 이 원계약 진단을
  같이 찍어 이 판단의 근거를 남긴다.
- **ETF 는 공짜가 아니다.** 보수는 이미 NAV 안에 있지만 **공매도 차입비용은 안
  들어가 있다**(SLV·USO 는 비쌀 수 있다). 롱숏 책이므로 사전 등록 단계에서
  반드시 넣는다. MDE 는 수준이 아니라 분산이 정하므로 이 계산에는 영향이 작다.
- **암호는 주 7일이라 ×252 연율화가 틀린다.** 패널을 영업일로 재색인해 주말
  움직임이 월요일 수익에 들어가게 한다 — 버리지 않고 자를 맞춘다.
- **암호는 표본이 짧고 시장이 2개다.** 넓이도 창 길이도 불리하다. 변동성이 크다고
  유리해지지 않는다는 것이 자산배분에서 배운 것 그대로다.
- 거래비용은 안 넣었다. 월 1회 리밸런스라 회전이 낮고, 비용은 수준을 옮기지 분산을
  거의 안 바꾸므로 MDE 판정에는 영향이 작다. 사전 등록 단계에서 넣는다.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 윈도우 콘솔 기본이 cp949 라 한글·em dash 에서 죽는다. 출력만 UTF-8 로 돌린다.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts.measure_pead import mde_pp, excess_cagr_ci  # noqa: E402

CACHE = "data/futures_panel.parquet"
SEED = 20260903
N_SEED = 20            # 무작위 부호 줄의 씨앗 수 — MDE 의 씨앗 잡음을 중위로 걷어낸다
TARGET_VOL = 0.10      # 모든 줄을 이 연변동성으로 맞춘다
VOL_WIN = 60           # 사이징용 후행 실현변동성 창(거래일)

# 자산군이 겹치지 않게 고른 12개. N 스윕은 이 순서의 앞에서부터 자른다 —
# 앞쪽일수록 서로 다른 자산군이라, 넓이를 늘리는 순서가 상관을 낮추는 순서다.
# 공통 창은 제일 늦게 상장한 HYG(2007-04)가 정한다.
MARKETS = ["SPY", "IEF", "GLD", "USO", "FXE", "EEM",
           "TLT", "SLV", "DBA", "FXY", "EFA", "HYG"]
CRYPTO = ["BTC-USD", "ETH-USD"]
RAW_FUTURES = ["6J=F", "CL=F", "SI=F", "ES=F", "GC=F", "ZN=F"]   # 진단 전용, 판정에 안 씀


def load(tickers: list[str]) -> pd.DataFrame:
    """일별 수익률 패널(공통 창). 캐시가 없거나 티커가 모자라면 받아서 채운다.

    **영업일로 재색인한다.** 암호는 주 7일이라 그대로 두면 ×252 연율화가 틀린다.
    주말 움직임은 버려지지 않고 월요일 수익에 들어간다. 주식 ETF 에는 무해하다
    (휴장일이 NaN 으로 들어왔다가 아래 dropna 로 그대로 빠진다).
    """
    have = pd.read_parquet(CACHE) if os.path.exists(CACHE) else pd.DataFrame()
    need = [t for t in tickers if t not in have.columns]
    if need:
        import yfinance as yf
        got = yf.download(need, start="1990-01-01", auto_adjust=True, progress=False)["Close"]
        got = got.to_frame(need[0]) if isinstance(got, pd.Series) else got
        have = got if have.empty else have.join(got, how="outer")
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        have.to_parquet(CACHE)
    close = have[tickers].dropna(how="all")
    close = close.reindex(pd.bdate_range(close.index.min(), close.index.max()))
    return (close / close.shift(1) - 1.0).dropna()


def book(rets: pd.DataFrame, signs: dict) -> np.ndarray:
    """월초에 역변동성으로 사이징하고 `signs[월]` 방향으로 들어가는 책의 일별 수익.

    사이징은 **전월까지의** 후행 변동성만 쓴다. 달 안에서는 비중을 그대로 둔다 —
    매일 목표로 되돌리면 실제로 낼 수 없는 회전이다.
    """
    vol = rets.rolling(VOL_WIN).std().shift(1)
    out = []
    for period, blk in rets.groupby(rets.index.to_period("M")):
        v = vol.loc[blk.index[0]].values
        w = np.divide(1.0, v, out=np.zeros_like(v), where=(v > 0))
        w = np.nan_to_num(w) * signs[period] / len(rets.columns)
        out.extend(blk.values @ w)
    return np.array(out)


def scaled(line: np.ndarray) -> np.ndarray:
    """실현 연변동성을 TARGET_VOL 로 맞춘 상수배. N 이 다른 책을 비교 가능하게 한다."""
    sd = line.std() * np.sqrt(252)
    return line * (TARGET_VOL / sd) if sd > 0 else line


def months(rets: pd.DataFrame):
    return rets.index.to_period("M").unique()


def blind_line(rets: pd.DataFrame, seed: int) -> np.ndarray:
    """부호 판정만 무작위인 줄. 시장 수·사이징·리밸런스 시점은 진짜 규칙과 같다."""
    rng = np.random.default_rng(seed)
    n = len(rets.columns)
    return scaled(book(rets, {m: rng.choice([-1.0, 1.0], n) for m in months(rets)}))


def oracle_line(rets: pd.DataFrame) -> np.ndarray:
    """완전예지 천장 — 시장마다 다음 달 수익의 부호를 미리 안다. 전략이 아니라 상한이다."""
    signs = {}
    for period, blk in rets.groupby(rets.index.to_period("M")):
        cum = (1.0 + blk).prod() - 1.0
        signs[period] = np.where(cum.values >= 0, 1.0, -1.0)
    return scaled(book(rets, signs))


def skill_line(rets: pd.DataFrame, p: float, seed: int) -> np.ndarray:
    """시장·월마다 부호를 **정확히 확률 p 로** 맞히는 줄. p=0.5 가 우연이다."""
    rng = np.random.default_rng(seed)
    signs = {}
    for period, blk in rets.groupby(rets.index.to_period("M")):
        cum = (1.0 + blk).prod() - 1.0
        best = np.where(cum.values >= 0, 1.0, -1.0)
        signs[period] = np.where(rng.random(len(best)) < p, best, -best)
    return scaled(book(rets, signs))


def hit_rate_needed(mde: float, ceiling: float) -> float:
    """초과수익이 MDE 와 같아지는 월별·시장별 부호 적중률. 우연은 50%.

    적중률 p 로 완전예지를 따라가고 나머지는 반대면 초과수익은 p 에 선형이다
    (0.5 에서 0, p=1 에서 천장). selftest 5 가 선형성을 확인한다.
    """
    return 0.5 + 0.5 * mde / ceiling


def table(rets: pd.DataFrame, label: str, sweep: list[int]) -> None:
    print()
    print(f"### {label} — {rets.index[0].date()}~{rets.index[-1].date()} "
          f"({len(rets) / 252.0:.1f}년, {len(rets)}일)")
    print(f"{'시장수':>6} | {'MDE(연 %p)':>10} | {'완전예지 천장':>12} | "
          f"{'천장/MDE':>8} | {'필요 적중률':>10}")
    for n in sweep:
        sub = rets[rets.columns[:n]].dropna(how="all")
        zero = np.zeros(len(sub))   # 롱숏 책의 귀무가설은 "안 들어간다"(현금)다
        mde = float(np.median([mde_pp(blind_line(sub, SEED + i), zero)
                               for i in range(N_SEED)]))
        ceil_pt, _, _ = excess_cagr_ci(oracle_line(sub), zero)
        print(f"{n:>6} | {mde:>10.2f} | {ceil_pt:>12.2f} | {ceil_pt / mde:>7.1f}x | "
              f"{hit_rate_needed(mde, ceil_pt) * 100:>9.1f}%")


def run() -> None:
    table(load(MARKETS), "12시장 ETF 대용 (위험 고정 연변동성 10%)", [1, 3, 6, 12])
    print("  (동전던지기가 50%. 자산배분에서는 이 비율이 진폭을 3배 키워도 7.1→7.4x 로"
          " 평평했다 — 여기서 커지면 넓이가 손잡이라는 뜻이다.)")

    table(load(CRYPTO), "암호 2시장 (같은 자, 같은 위험)", [1, 2])

    print()
    print("### 왜 선물 원계약을 안 썼나 — 일별 |수익| 최대 (`=F` 는 롤 조정이 안 돼 있다)")
    raw = load(RAW_FUTURES)
    for t, v in raw.abs().max().sort_values(ascending=False).items():
        px = pd.read_parquet(CACHE)[t].dropna()
        print(f"  {t:7} 최대 {v * 100:7.1f}%   최저가 {px.min():>9.4f}")
    print("  (가격이 음수면 수익률이 정의되지 않고, 자릿수 오타는 꼬리와 구분되지 않는다.)")


def selftest() -> None:
    global TARGET_VOL
    idx = pd.bdate_range("2015-01-01", periods=1500)
    rng = np.random.default_rng(0)
    rets = pd.DataFrame(rng.normal(0, 0.01, (1500, 4)), index=idx,
                        columns=["A", "B", "C", "D"])
    zero = np.zeros(len(rets))

    # 1. 위험 고정이 실제로 걸린다 — 어떤 줄이든 실현 연변동성이 TARGET_VOL 이다.
    for line in (blind_line(rets, 1), oracle_line(rets), skill_line(rets, 0.7, 1)):
        assert abs(line.std() * np.sqrt(252) - TARGET_VOL) < 1e-9, line.std()

    # 2. 완전예지는 현금을 이긴다. 못 이기면 부호가 뒤집혀 있다.
    assert excess_cagr_ci(oracle_line(rets), zero)[0] > 0

    # 3. 결론은 N 사이의 **순서**이고, 그 순서가 TARGET_VOL 에 안 흔들린다.
    #    (절대값은 흔들린다 — 복리 끌림. 그래서 순서만 검사한다. 모듈 주석 참고.)
    def ratios() -> list[float]:
        out = []
        for n in (1, 4):
            sub = rets[rets.columns[:n]]
            out.append(excess_cagr_ci(oracle_line(sub), zero)[0]
                       / mde_pp(blind_line(sub, 1), zero))
        return out
    lo = ratios()
    TARGET_VOL = 0.20
    hi = ratios()
    TARGET_VOL = 0.10
    assert lo[1] > lo[0] and hi[1] > hi[0], (lo, hi)

    # 4. 시장이 늘면 같은 위험에서 천장이 커진다 — 이 스크립트가 묻는 것 자체.
    one = rets[["A"]]
    assert (excess_cagr_ci(oracle_line(rets), zero)[0]
            > excess_cagr_ci(oracle_line(one), zero)[0])

    # 5. 적중률 p 에 대해 초과수익이 선형이다 — hit_rate_needed 가 기대는 성질.
    assert np.allclose(skill_line(rets, 1.0, 3), oracle_line(rets))

    def eff(p: float) -> float:
        return float(np.mean([excess_cagr_ci(skill_line(rets, p, 100 + i), zero)[0]
                              for i in range(40)]))
    top, mid, chance = eff(1.0), eff(0.75), eff(0.5)
    assert abs(chance) < 0.1 * top, (chance, top)
    assert abs(mid - top / 2) < 0.2 * top, (mid, top)

    print("selftest OK")


if __name__ == "__main__":
    selftest() if len(sys.argv) > 1 and sys.argv[1] == "selftest" else run()
