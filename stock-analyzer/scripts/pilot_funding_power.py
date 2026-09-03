"""무기한선물 펀딩 캐리 — **재기 전에 검출력만 잰다.**

    python -m scripts.pilot_funding_power           # 표
    python -m scripts.pilot_funding_power selftest  # 산수 점검

질문: "1천만원으로 델타중립 펀딩 캐리를 돌리면, 이 저장소의 자로 검출이 되는가?"

추세추종 심사(docs/measurements/2026-09-03-futures-trend-power.md)에서 **암호는
방향성으로 닫혔다** — 넓이 2·창 9.1년이라 천장/MDE 5.6x, 필요 적중률 58.9%. 그래서
여기서 재는 건 방향이 아니라 **캐리**다. 롱 현물 + 숏 무기한선물로 델타를 0 으로
묶고 8시간마다 들어오는 펀딩만 받는다. 예측이 아니라 수수료 흐름이다.

## 왜 오라클·눈가림 기계를 안 쓰나 — 이건 8-K 모양이다

추세추종은 규칙의 성적을 사전 등록 전에 보면 안 되므로 부호를 무작위로 가렸다.
캐리는 다르다. **펀딩률은 바이낸스가 공표하는 공개 수치**고, 규칙에 판단이 없다
(롱 현물·숏 선물, 끝). 그래서 여기서 재는 모양은 8-K 정찰과 같다: **노리는 효과 vs
MDE 를 맞대고, 노리는 효과가 MDE 아래면 사전 등록 전에 닫는다.**

노리는 효과는 문헌값이 아니라 계산값이다 — 실측 펀딩 × 자본효율 − 수수료. 셋 다
관측되거나 고시된 수이고 추정이 아니다. **사전 등록이 물을 것은 과거 평균이
양수인가가 아니라(그건 이미 아는 것) 자본이 더 들어와도 앞으로 남는가**이므로,
과거 순캐리를 여기서 쓰는 건 결과를 미리 보는 것이 아니라 입력을 채우는 것이다.

MDE 는 노이즈에서 온다. 캐리의 노이즈는 펀딩이 아니라 **베이시스**다 — 선물과
현물이 따로 움직이는 만큼 손익이 흔들린다. 그래서 MDE 를 낼 때는 줄의 평균만 블록
단위로 뒤집어 효과를 0 으로 만들고 편차는 그대로 둔다.

## 자본이 반토막 난다 — 1천만원에서 이게 판정을 가른다

현물을 사려면 현금이 필요하고 선물 숏에는 증거금이 필요한데, 바이낸스는 (포트폴리오
마진 없이는) 둘을 교차로 못 쓴다. 자본 C 를 반씩 나누면 다리당 노셔널이 C/2 다.
**따라서 자본 대비 수익은 노셔널 대비 수익의 절반이다.** 연 10% 펀딩이 연 5% 가
된다. 레버리지로 되살릴 수 있지만 그건 청산 위험을 사는 것이고, 델타중립의 유일한
장점을 버리는 것이다.

## 판정선은 3층이다 — 단독으로 ITOT 와 맞대지 않는다

델타중립 슬리브는 주식 베타가 0 이라 100% 주식과 수익률만 맞대면 위험 수준이 다른
둘을 비교하는 것이다. PR #207 이 추세추종에 대해 못 박은 것과 같다.

  ① 생존   순캐리 > 0                    (비용 빼고 남는가)
  ② 값어치 순캐리 > 파킹통장 3%           (거래소 위험을 감수할 값인가) ← 진짜 관문
  ③ 채택   ITOT + 슬리브 > ITOT 100%      (포트폴리오에 넣을 것인가)

②를 못 넘으면 ③은 안 본다.

## 함정

- **펀딩 이력만으로 재면 유령 체결이다.** 펀딩은 깨끗한 공표값이지만 진입·청산에서
  실제로 건너야 하는 현물↔선물 스프레드는 아니다. 그래서 펀딩을 따로 더하지 않고
  **두 다리의 실제 종가 수익률을 그대로 빼서**(r_현물 − r_선물) 베이시스 손익이
  저절로 들어오게 한다. 일봉 유령 체결이 터진 자리와 같은 모양이다.
- **수수료를 연율로 뭉개면 보유기간이 사라진다.** 왕복 0.30% 는 30일 보유면 연 3.65%p
  이고 1년 보유면 연 0.30%p 다 — 순캐리의 부호가 여기서 뒤집힌다. 그래서 보유기간을
  스윕한다. 15분봉에서 비용이 R 을 먹던 것과 같은 축이다.
- **암호는 주 7일이라 ×252 연율화가 틀린다.** `mde_pp`·`excess_cagr_ci` 가 252 를
  박아두고 있으므로 책을 영업일로 접는다(주말 움직임은 버리지 않고 월요일에 들어간다).
  추세추종 스크립트의 `load()` 와 같은 처리다.
- **펀딩 주기가 심볼·시기마다 다르다**(8시간이 기본이지만 4시간으로 바뀐 구간이 있다).
  건별로 세지 않고 **날짜별로 합쳐서** 자를 맞춘다.
- 차입 이자·현물 상장폐지·거래소 자체 위험은 안 들어간다. ②를 넘겼을 때 사전 등록에
  반드시 넣는다 — 거래소가 통째로 날아가는 꼬리가 이 전략의 진짜 위험이다.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 윈도우 콘솔 기본이 cp949 라 한글·em dash 에서 죽는다. 출력만 UTF-8 로 돌린다.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts.measure_pead import mde_pp, excess_cagr_ci  # noqa: E402

CACHE = "data/funding_panel.parquet"
SEED = 20260903
N_SEED = 20             # 눈가림 줄의 씨앗 수 — MDE 의 씨앗 잡음을 중위로 걷어낸다
START_MS = 1546300800000                      # 2019-01-01, 바이낸스 무기한선물 이전

SPOT_TAKER = 0.0010     # VIP0, BNB 할인 없음 — 1천만원이면 여기다
PERP_TAKER = 0.0005
ROUND_TRIP = 2 * (SPOT_TAKER + PERP_TAKER)    # 진입+청산, 양다리 = 0.30%
LEG_SPLIT = 0.5         # 자본을 현물/증거금으로 반 나눈다 → 다리당 노셔널 = C/2
# ponytail: 교차마진(포트폴리오 마진)을 쓰면 이 값이 커진다. 청산 위험을 사는 것이므로
#           ②를 넘긴 뒤에 별도로 잰다.
CASH_RATE = 0.03        # ② 파킹통장 — 이 슬리브의 진짜 대안
HOLD_SWEEP = [7, 30, 90, 365]
BASE_HOLD = 90          # 종목별 표에 쓰는 보유기간
RECENT_FROM = "2024-01-01"   # 판정을 내는 구간 — 과거가 아니라 지금을 묻는다

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT",
           "ADAUSDT", "DOGEUSDT", "LINKUSDT", "LTCUSDT", "AVAXUSDT"]


# ------------------------------------------------------------------ 데이터

def _get(url: str, params: dict) -> list:
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _paged(url: str, params: dict, stamp) -> list:
    """`limit` 이 꽉 차는 동안 startTime 을 밀며 전부 받는다. 공개 엔드포인트라 키 불필요."""
    out, start, limit = [], START_MS, params.get("limit", 1000)
    while True:
        batch = _get(url, {**params, "startTime": start, "limit": limit})
        out += batch
        if len(batch) < limit:
            return out
        start = stamp(batch[-1]) + 1


def _fetch(symbol: str) -> pd.DataFrame:
    """한 심볼의 일별 [funding, spot, perp]. 펀딩은 **날짜별 합**(주기 변경에 무관)."""
    def closes(url: str) -> pd.Series:
        rows = _paged(url, {"symbol": symbol, "interval": "1d"}, lambda r: r[0])
        s = pd.Series({pd.to_datetime(r[0], unit="ms").normalize(): float(r[4]) for r in rows})
        return s[~s.index.duplicated()]

    fr = _paged("https://fapi.binance.com/fapi/v1/fundingRate", {"symbol": symbol},
                lambda r: r["fundingTime"])
    funding = pd.Series(
        [float(r["fundingRate"]) for r in fr],
        index=pd.to_datetime([r["fundingTime"] for r in fr], unit="ms").normalize(),
    ).groupby(level=0).sum()

    df = pd.DataFrame({"funding": funding,
                       "spot": closes("https://api.binance.com/api/v3/klines"),
                       "perp": closes("https://fapi.binance.com/fapi/v1/klines")}).dropna()
    df["symbol"] = symbol
    return df


def panel(symbols: list[str]) -> pd.DataFrame:
    """긴 형태 패널(date, symbol, funding, spot, perp). 없는 심볼만 받아 캐시에 붙인다."""
    have = pd.read_parquet(CACHE) if os.path.exists(CACHE) else pd.DataFrame(columns=["symbol"])
    need = [s for s in symbols if s not in set(have["symbol"])]
    if need:
        got = pd.concat([_fetch(s) for s in need])
        have = got if have.empty else pd.concat([have, got])
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        have.to_parquet(CACHE)
    return have[have["symbol"].isin(symbols)]


# ------------------------------------------------------------------ 책

def notional_line(df: pd.DataFrame) -> pd.Series:
    """롱 현물 + 숏 선물, 노셔널 대비 일별 수익(수수료 전).

    **펀딩을 따로 더하지 않고 두 다리의 실제 수익률을 뺀다** — 그래야 베이시스 손익이
    저절로 들어온다. 펀딩만 더하면 진입·청산에서 건너는 스프레드가 사라져 유령 체결이 된다.
    """
    d = df.sort_index()
    return (d["spot"].pct_change() - d["perp"].pct_change() + d["funding"]).dropna()


def to_bdays(daily: pd.Series) -> np.ndarray:
    """주 7일 줄을 영업일로 접는다. 주말은 버려지지 않고 월요일 수익에 들어간다.

    `mde_pp`·`excess_cagr_ci` 가 ×252 를 박아두고 있어 365일 줄을 그냥 넣으면 연율화가 틀린다.
    """
    eq = (1.0 + daily).cumprod()
    eq = eq.reindex(pd.bdate_range(eq.index.min(), eq.index.max())).ffill()
    return eq.pct_change().dropna().values


def fee_drag(n_days: int, hold: int) -> float:
    """보유기간 `hold` 로 굴릴 때의 **일별** 수수료 부담. 왕복 횟수 = 일수/보유기간."""
    return (n_days / hold) * ROUND_TRIP / n_days


def book(df: pd.DataFrame, hold: int) -> np.ndarray:
    """자본 대비 영업일 수익. 자본효율 반토막과 수수료를 여기서 건다."""
    line = notional_line(df)
    return to_bdays(line - fee_drag(len(line), hold)) * LEG_SPLIT


def basket(pan: pd.DataFrame, hold: int) -> np.ndarray:
    """심볼 동일가중. 상장 시점이 달라 겹치는 날만 쓴다."""
    wide = pd.concat({s: notional_line(g) for s, g in pan.groupby("symbol")},
                     axis=1, sort=True).dropna()
    line = wide.mean(axis=1)
    return to_bdays(line - fee_drag(len(line), hold)) * LEG_SPLIT


def cagr(line: np.ndarray) -> float:
    return float(np.expm1(np.log1p(line).mean() * 252) * 100)


def blinded(line: np.ndarray, seed: int, block: int = 63) -> np.ndarray:
    """줄의 평균만 블록 단위로 뒤집은 줄 — 분산은 그대로, 기대효과는 0.

    `mde_pp` 에 넣을 때 결과를 미리 보지 않기 위한 장치다. 편차(베이시스 노이즈)는
    건드리지 않으므로 MDE 는 진짜 책의 것과 같다.
    """
    rng = np.random.default_rng(seed)
    mu, dev = line.mean(), line - line.mean()
    signs = rng.choice([-1.0, 1.0], int(np.ceil(len(line) / block)))
    return dev + mu * np.repeat(signs, block)[:len(line)]


def mde_of(line: np.ndarray) -> float:
    zero = np.zeros(len(line))
    return float(np.median([mde_pp(blinded(line, SEED + i), zero) for i in range(N_SEED)]))


# ------------------------------------------------------------------ 출력

def run() -> None:
    pan = panel(SYMBOLS)

    print(f"### 종목별 (보유 {BASE_HOLD}일, 자본효율 {LEG_SPLIT:.0%}, 왕복 {ROUND_TRIP:.2%})")
    print(f"{'심볼':<9}|{'표본(년)':>8}|{'총펀딩(연%)':>11}|{'베이시스σ(연%)':>13}|"
          f"{'순캐리(연%)':>11}|{'MDE(연%p)':>10}|{'순/MDE':>7}")
    for sym, g in pan.groupby("symbol"):
        line, cap = notional_line(g), book(g, BASE_HOLD)
        yrs = len(line) / 365.0
        gross = float(np.expm1(np.log1p(g["funding"]).sum() / yrs) * 100)
        basis = float((line - g["funding"].reindex(line.index)).std() * np.sqrt(365) * 100)
        net, mde = cagr(cap), mde_of(cap)
        print(f"{sym:<9}|{yrs:>8.1f}|{gross:>11.2f}|{basis:>13.2f}|"
              f"{net:>11.2f}|{mde:>10.2f}|{net / mde:>6.2f}x")

    print()
    print("### 동일가중 바구니 — 보유기간 스윕 (수수료가 부호를 뒤집는 자리)")
    print(f"{'보유(일)':>8}|{'연 수수료(%p)':>13}|{'순캐리(연%)':>11}|{'MDE(연%p)':>10}|{'순/MDE':>7}")
    rows = {}
    for hold in HOLD_SWEEP:
        line = basket(pan, hold)
        net, mde = cagr(line), mde_of(line)
        rows[hold] = (line, net, mde)
        print(f"{hold:>8}|{365.0 / hold * ROUND_TRIP * 100:>13.2f}|{net:>11.2f}|"
              f"{mde:>10.2f}|{net / mde:>6.2f}x")

    best = max(rows, key=lambda h: rows[h][1])
    line, net, mde = rows[best]

    # **판정은 최근 구간으로 낸다.** 전표본 평균은 2020~21 이 만든 값이고, 이 노선이
    # 답해야 하는 질문은 "과거에 있었나"가 아니라 "지금도 있나"다.
    recent = basket(pan[pan.index >= RECENT_FROM], best)
    r_net, r_mde = cagr(recent), mde_of(recent)

    print()
    print(f"### 3층 판정 (가장 좋은 보유기간 {best}일 기준)")
    print(f"{'':<11}{'전표본':>10}{'  최근(' + RECENT_FROM[:4] + '~)':>12}")
    print(f"  {'순캐리':<9}{net:>9.2f}%{r_net:>11.2f}%")
    print(f"  {'MDE':<9}{mde:>9.2f} {r_mde:>11.2f}")
    print(f"  ① 생존   순캐리 > 0            → "
          f"{'통과' if r_net > 0 else '실패'}")
    print(f"  ② 값어치 순캐리 > 파킹통장 {CASH_RATE:.0%}   → "
          f"{'통과' if r_net > CASH_RATE * 100 else '실패'}   ← 진짜 관문")
    if r_net > CASH_RATE * 100 and r_net > r_mde:
        _gate3(line)
    else:
        print(f"     최근 순캐리 {r_net:+.2f}% 가 파킹통장 {CASH_RATE:.0%} 에 진다. "
              f"전표본 {net:+.2f}% 는 2020~21 이 만든 값이다.")
        print("  ③ 채택   ②를 못 넘었으므로 안 본다.")

    _tail(pan)
    _decay(pan)


def _tail(pan: pd.DataFrame, n: int = 6) -> None:
    """최악의 날들 — **MDE 가 못 잡는 위험이다.**

    MDE 는 구간 반폭이라 대칭 잡음만 본다. 캐리의 진짜 위험은 왼쪽 꼬리다:
    베이시스가 벌어졌다 되메워지는 날 손실이 나는데, **하필 그날 펀딩도 뒤집혀
    같은 방향으로 때린다.** 둘이 독립이 아니라는 것이 이 표의 요점이다.

    추세추종에서 배운 대로 **큰 값을 잘라내지 않는다** — 자르면 재려는 대상을 자른다.
    """
    print()
    print(f"### 최악의 {n}일 — 노셔널 대비 (MDE 가 못 보는 왼쪽 꼬리)")
    print(f"{'심볼':<9}|{'날짜':>11}|{'일손익':>8}|{'전일 베이시스':>12}|{'당일 베이시스':>12}|{'당일 펀딩':>9}")
    rows = []
    for sym, g in pan.groupby("symbol"):
        g = g.sort_index()
        line = notional_line(g)
        basis = (g["perp"] / g["spot"] - 1.0) * 100
        for d in line.nsmallest(n).index:
            prev = basis.index[basis.index.get_loc(d) - 1]
            rows.append((line[d], sym, d, basis[prev], basis[d], g["funding"][d] * 100))
    for pnl, sym, d, b0, b1, f in sorted(rows)[:n]:
        print(f"{sym:<9}|{str(d.date()):>11}|{pnl * 100:>7.2f}%|{b0:>11.2f}%|{b1:>11.2f}%|{f:>8.2f}%")
    print("  (베이시스가 되메워지는 날 손실이고, 같은 날 펀딩이 음수면 숏이 돈을 낸다 — 겹쳐서 맞는다.)")


def _gate3(line: np.ndarray, weight: float = 0.10) -> None:
    """ITOT 100% vs ITOT (1-w) + 캐리 w.

    **원수익률로 맞대면 안 된다.** 슬리브가 변동성을 낮추므로 CAGR 만 비교하면 위험이
    다른 둘을 비교하는 것이고, 그게 이 문서가 처음부터 피하려던 실수다. 그래서 셋을
    같이 찍는다: 원 CAGR · 샤프 · **ITOT 와 같은 변동성으로 레버한 CAGR**.
    """
    import yfinance as yf
    px = yf.download("ITOT", start="2019-01-01", auto_adjust=True, progress=False)["Close"]
    itot = px.squeeze().pct_change().dropna().values
    m = min(len(itot), len(line))
    itot, sleeve = itot[-m:], line[-m:]
    mix = (1 - weight) * itot + weight * sleeve

    def stat(x):
        v = x.std() * np.sqrt(252)
        return cagr(x), v, cagr(x) / (v * 100)

    (c0, v0, s0), (c1, v1, s1) = stat(itot), stat(mix)
    print(f"  ③ 채택   상관 {np.corrcoef(itot, sleeve)[0, 1]:+.2f}, "
          f"슬리브 최대낙폭 {_mdd(sleeve):.1%} (자본 대비)")
    print(f"     {'':<18}{'CAGR':>8}{'변동성':>8}{'샤프':>7}")
    print(f"     {'ITOT 100%':<18}{c0:>7.2f}%{v0:>7.1%}{s0:>7.2f}")
    print(f"     {f'ITOT+캐리 {weight:.0%}':<18}{c1:>7.2f}%{v1:>7.1%}{s1:>7.2f}")
    pt, lo, hi = excess_cagr_ci(mix, itot)
    print(f"     원수익 초과 {pt:+.2f}%p [{lo:+.2f}, {hi:+.2f}]"
          f" → {'통과' if lo > 0 else '실패'}")
    lev = mix * (v0 / v1)   # ITOT 와 같은 변동성으로 맞춘 뒤 비교 (차입비용 미포함)
    pt2, lo2, hi2 = excess_cagr_ci(lev, itot)
    print(f"     위험조정 초과 {pt2:+.2f}%p [{lo2:+.2f}, {hi2:+.2f}]"
          f" → {'통과' if lo2 > 0 else '실패'}   (레버 {v0 / v1:.2f}x, 차입비용 미포함)")
    print(f"     (가중 {weight:.0%} 는 예시다. 사전 등록에서 실제 인덱스 잔고로 못 박을 것.)")


def _mdd(line: np.ndarray) -> float:
    eq = np.cumprod(1.0 + line)
    return float((eq / np.maximum.accumulate(eq) - 1.0).min())


def _decay(pan: pd.DataFrame, hold: int = 365) -> None:
    """**캐리가 닳고 있는가.** 사전 등록이 물을 것이 과거 평균이 아니라 앞으로이므로,
    구간을 잘라 순캐리가 유지되는지 본다. 자본이 들어오면 펀딩은 압축된다.
    """
    print()
    print(f"### 기간 분할 — 캐리가 닳는가 (보유 {hold}일)")
    print(f"{'구간':<13}|{'순캐리(연%)':>11}|{'MDE(연%p)':>10}|{'순/MDE':>7}")
    for lo, hi in [("2020", "2021"), ("2022", "2023"), ("2024", "2026")]:
        sub = pan[(pan.index >= f"{lo}-01-01") & (pan.index <= f"{hi}-12-31")]
        keep = [s for s, g in sub.groupby("symbol") if len(g) > 200]
        line = basket(sub[sub["symbol"].isin(keep)], hold)
        net, mde = cagr(line), mde_of(line)
        print(f"{lo}~{hi:<8}|{net:>11.2f}|{mde:>10.2f}|{net / mde:>6.2f}x")


# ------------------------------------------------------------------ 점검

def selftest() -> None:
    idx = pd.date_range("2020-01-01", periods=730)
    rng = np.random.default_rng(0)

    def frame(fund: float, basis: np.ndarray) -> pd.DataFrame:
        spot = pd.Series(100.0 * np.cumprod(1 + rng.normal(0, 0.03, len(idx))), index=idx)
        return pd.DataFrame({"funding": fund, "spot": spot,
                             "perp": spot * (1.0 + basis), "symbol": "T"}, index=idx)

    # 1. 델타중립이 실제로 중립이다 — 베이시스가 안 움직이고 펀딩이 0 이면 손익 0.
    #    현물이 아무리 흔들려도 두 다리가 상쇄돼야 한다.
    flat = notional_line(frame(0.0, np.zeros(len(idx))))
    assert abs(flat).max() < 1e-12, abs(flat).max()

    # 2. 펀딩은 그대로 들어온다 — 베이시스 고정이면 일별 수익 = 일별 펀딩.
    paid = notional_line(frame(0.0001, np.zeros(len(idx))))
    assert np.allclose(paid, 0.0001), paid[:3]

    # 3. 자본효율 반토막이 실제로 걸린다. 노셔널 수익의 LEG_SPLIT 배여야 한다.
    df = frame(0.0001, np.zeros(len(idx)))
    assert np.allclose(book(df, 10 ** 9), to_bdays(notional_line(df)) * LEG_SPLIT, atol=1e-9)

    # 4. 수수료는 보유기간에 반비례한다 — 짧게 굴릴수록 순캐리가 작다. 부호가 뒤집히는 자리.
    nets = [cagr(book(df, h)) for h in (7, 30, 365)]
    assert nets[0] < nets[1] < nets[2], nets
    assert nets[0] < 0 < nets[2], nets   # 0.01%/8h 캐리는 7일 회전이면 수수료에 진다

    # 5. 눈가림이 효과만 지우고 분산은 안 건드린다 — MDE 가 결과를 미리 보지 못하게 하는 장치.
    line = book(frame(0.0001, rng.normal(0, 0.002, len(idx))), 90)
    blind = [blinded(line, i) for i in range(200)]
    assert abs(np.mean([b.mean() for b in blind])) < 0.05 * abs(line.mean())
    assert all(abs(b.std() / line.std() - 1) < 0.05 for b in blind)

    # 6. 베이시스는 노이즈지 효과가 아니다 — 베이시스 변동이 커지면 MDE 도 커져야 한다.
    calm = book(frame(0.0001, rng.normal(0, 0.001, len(idx))), 90)
    wild = book(frame(0.0001, rng.normal(0, 0.010, len(idx))), 90)
    assert mde_of(wild) > mde_of(calm) * 2, (mde_of(calm), mde_of(wild))

    # 7. 주말 접기가 원금을 안 흘린다 — 접기 전후 누적수익이 같아야 한다.
    daily = notional_line(frame(0.0001, np.zeros(len(idx))))
    folded = to_bdays(daily)
    assert abs((1 + folded).prod() / (1 + daily).cumprod().iloc[-1] - 1) < 0.01

    print("selftest OK")


if __name__ == "__main__":
    selftest() if len(sys.argv) > 1 and sys.argv[1] == "selftest" else run()
