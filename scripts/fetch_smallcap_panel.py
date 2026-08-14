"""소형주 측정 재료 수급 — 조정 일봉 패널 + companyfacts 캐시.

설계서: docs/superpowers/specs/2026-08-14-fscore-smallcap-design.md 6절.

    python scripts/fetch_smallcap_panel.py           # 둘 다 (기본)
    python scripts/fetch_smallcap_panel.py prices    # 조정 일봉만
    python scripts/fetch_smallcap_panel.py facts     # companyfacts 만

**조정(`adjustment="all"`) 일봉이다.** 유니버스는 시가총액을 재려고 미조정가로
지었지만(조정가 × 당시 주식수 = 틀린 시총), 수익률은 반대다 — 미조정가로 재면
분할일의 갭이 그대로 −50% 수익이 된다. 같은 종목을 두 번 받는 이유가 그것이다.

**패널의 열은 티커가 아니라 asset_id(`"CIK:티커"`)다.** 티커는 재활용되므로
(BBBY: 886158 파산 → 1130713 재상장) `su.slice_closes` 로 상장 구간을 갈라
붙인다. 그러지 않으면 죽은 회사의 자리에 다른 회사의 봉이 이어진다.

두 단계 다 **이어서 돈다.** 일봉은 배치 해시로 이름 붙인 샤드, companyfacts 는
`data/edgar_raw/CIK*.json` 이 캐시다. 중단하고 다시 부르면 받은 것은 안 받는다.

산출물 `data/smallcap_panel.parquet` 은 저장소에 안 남긴다 — Alpaca 에서 언제든
다시 받을 수 있다. 웨이백 스냅샷(`data/smallcap/snapshots`)과 다른 점이 그것이다.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import edgar_fundamentals as ef      # noqa: E402
from modules import smallcap_universe as su       # noqa: E402
from scripts.build_smallcap_universe import (     # noqa: E402
    OUT_DIR, UNIVERSE, _bars_tolerant, _say,
)

SHARD_DIR = os.path.join(OUT_DIR, "prices_adj")
SPANS     = os.path.join(OUT_DIR, "listing_spans.parquet")
PANEL     = "data/smallcap_panel.parquet"
REPORT    = "data/smallcap_panel_report.json"

# 재활용 티커의 **이전 주인**은 봉을 못 구한다 — 실측으로 알게 된 것.
#
# `886158:BBBY`(파산한 Bed Bath & Beyond)의 2023-04 종가가 $19 로 나왔다. 그때
# 진짜 BBBY 는 $0.25 였다. Alpaca 는 심볼로 **현재 주인의 연속 이력**을 주므로,
# 2025년에 그 티커를 물려받은 회사(1130713)의 과거 봉이 통째로 딸려 온 것이다.
# 상장 구간으로 자르면 꼬리는 잘리지만 **몸통이 남는다** — 죽은 회사의 자리에
# 다른 회사의 수익률이 앉는다.
#
# 가려내는 자: 이전 주인인데 마지막 봉이 **마지막 목격일보다 이만큼 뒤**면
# 그 심볼은 다음 주인의 이력이다(스냅샷 간격이 한 달 남짓이라 진짜 상폐면
# 목격일 근처에서 끝난다). 그런 열은 자르지 않고 **통째로 뺀다** — 어디까지가
# 누구 것인지 못 가르는 계열은 반만 쓸 수도 없다.
LATE_BAR_TOL_DAYS = 90

# 유니버스 첫 기준일이 2017-08-31 이고 2차 행이 2017-09-01 부터다. 그보다 앞은
# 이 측정이 쓰지 않는다 — 미조정가 패널(2016~)과 시작이 다른 건 그래서다.
START_DATE = pd.Timestamp("2017-01-01")
BATCH      = 100

# 이만큼도 안 남으면 멈춘다. 12GB 짜리 수급 도중에 디스크가 차면 반쯤 쓰인
# JSON 이 캐시로 남아 다음 실행이 그걸 재사용한다.
MIN_FREE_GB = 5.0


def _free_gb() -> float:
    return shutil.disk_usage(os.path.abspath(os.sep)).free / 1e9


# ── 1. 조정 일봉 ───────────────────────────────────────────────────────
def step_prices(force: bool = False) -> str:
    u = pd.read_parquet(UNIVERSE)
    spans = pd.read_parquet(SPANS)
    tickers = sorted(u["ticker"].unique())
    keep = set(u["asset_id"].unique())
    _say(f"조정 일봉 — 티커 {len(tickers)} · 상장 이력 {len(keep)}")

    os.makedirs(SHARD_DIR, exist_ok=True)
    end = datetime.now(timezone.utc) - timedelta(days=1)   # 무료 SIP 는 최근을 안 준다
    batches = [tickers[i:i + BATCH] for i in range(0, len(tickers), BATCH)]

    paths = []
    for n, batch in enumerate(batches, 1):
        # 샤드 이름은 배치 내용의 해시다. 번호로 두면 티커가 하나만 늘어도
        # 경계가 밀려 다른 묶음이 조용히 재사용된다 (미조정 수급에서 겪었다).
        key = hashlib.sha1(",".join(batch).encode()).hexdigest()[:16]
        shard = os.path.join(SHARD_DIR, f"{key}.parquet")
        paths.append(shard)
        if os.path.exists(shard) and not force:
            continue
        bars = _bars_tolerant(batch, end, start=START_DATE, adjustment="all")
        rows = [pd.DataFrame({"ticker": sym,
                              "date": frame.index.tz_localize(None).normalize(),
                              "close": frame["Close"].astype("float32").to_numpy()})
                for sym, frame in bars.items() if not frame.empty]
        df = (pd.concat(rows, ignore_index=True) if rows
              else pd.DataFrame(columns=["ticker", "date", "close"]))
        df.to_parquet(shard, index=False)
        _say(f"  일봉 {n}/{len(batches)} — {len(bars)}종목 {len(df)}행")

    # 조립. 샤드별로 상장 구간을 갈라 asset_id 를 붙이고 긴 표로 모은 뒤 한 번만 편다.
    spans_by_ticker = dict(tuple(spans.groupby("ticker", sort=False)))
    last_owner = set(spans.sort_values("first_seen").groupby("ticker").tail(1)["listing_id"])
    seen_last = spans.set_index("listing_id")["last_seen"]
    longs, dropped = [], []
    for path in paths:
        if not os.path.exists(path):
            continue
        shard = pd.read_parquet(path)
        if shard.empty:
            continue
        closes_by_ticker = {t: g.set_index("date")["close"].sort_index()
                            for t, g in shard.groupby("ticker", sort=False)}
        part = [spans_by_ticker[t] for t in closes_by_ticker if t in spans_by_ticker]
        if not part:
            continue
        sliced = su.slice_closes(closes_by_ticker, pd.concat(part, ignore_index=True))
        for aid, s in sliced.items():
            if aid not in keep or len(s) == 0:
                continue
            if (aid not in last_owner
                    and (s.index[-1] - seen_last[aid]).days > LATE_BAR_TOL_DAYS):
                dropped.append(aid)          # 다음 주인의 이력이다 — 위 주석
                continue
            longs.append(pd.DataFrame({"asset_id": aid, "date": s.index,
                                       "close": s.to_numpy()}))

    if not longs:
        raise RuntimeError("일봉을 하나도 못 받았습니다.")
    long = pd.concat(longs, ignore_index=True).drop_duplicates(["asset_id", "date"])
    wide = long.pivot(index="date", columns="asset_id", values="close").sort_index()
    wide.columns = pd.MultiIndex.from_product([["Close"], wide.columns],
                                              names=["Price", "Ticker"])
    wide.index.name = None
    wide.to_parquet(PANEL)
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump({"days": int(wide.shape[0]), "listings": int(wide.shape[1]),
                   "start": str(wide.index.min().date()),
                   "end": str(wide.index.max().date()),
                   "adjustment": "all",
                   "dropped_recycled": sorted(dropped)}, f, ensure_ascii=False, indent=2)
    _say(f"패널 {PANEL} — {wide.shape[0]}일 × {wide.shape[1]}종목 "
         f"({wide.index.min().date()} ~ {wide.index.max().date()}) · "
         f"재활용 티커 이전 주인 {len(dropped)}종목 제외")
    return PANEL


# ── 2. companyfacts ────────────────────────────────────────────────────
def step_facts(limit: int = 0) -> int:
    """CIK 로 받는다. `ef.get_cik` 은 SEC 의 *현재* 매핑이라 죽은 티커를 못 준다.

    본구간(2020-03-31~2024-12-31) CIK 를 먼저 받는다 — 중간에 끊겨도 판정 행은
    돌릴 수 있게. 나머지는 2차 행(전구간)과 봉인 구간이 쓴다.
    """
    from scripts.measure_fscore import START, END

    u = pd.read_parquet(UNIVERSE)
    names = (u.sort_values("date").drop_duplicates("cik", keep="last")[["cik", "ticker"]])
    main = set(u.loc[(u["date"] >= START) & (u["date"] <= END), "cik"].unique())
    names["_pri"] = (~names["cik"].isin(main)).astype(int)
    names = names.sort_values(["_pri", "cik"])
    if limit:
        names = names.head(limit)
    _say(f"companyfacts — CIK {len(names)} (본구간 {len(main)}) · 여유 {_free_gb():.0f}GB")

    got = missing = cached = 0
    t0 = time.time()
    for i, r in enumerate(names.itertuples(), 1):
        path = os.path.join(ef.RAW_DIR, f"CIK{int(r.cik):010d}.json")
        was = os.path.exists(path)
        if not was and _free_gb() < MIN_FREE_GB:
            _say(f"디스크 여유 {_free_gb():.1f}GB — 여기서 멈춥니다 ({i}/{len(names)})")
            break
        ug = ef.load_raw(r.ticker, cik=int(r.cik))
        cached += was
        got += ug is not None
        missing += ug is None
        if i % 100 == 0:
            rate = (time.time() - t0) / i
            _say(f"  {i}/{len(names)} · 성공 {got} · 없음 {missing} · 캐시 {cached} · "
                 f"남은 시간 {(len(names) - i) * rate / 60:.0f}분 · 여유 {_free_gb():.0f}GB")

    _say(f"companyfacts 완료 — 성공 {got} · 없음 {missing} · 이미 있던 것 {cached}")
    return got


def main(argv) -> int:
    steps = [a for a in argv[1:] if a in ("prices", "facts")] or ["prices", "facts"]
    if "prices" in steps:
        step_prices(force="--force" in argv)
    if "facts" in steps:
        step_facts(int(os.environ.get("LIMIT", "0")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
