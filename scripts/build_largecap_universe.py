"""
대형주 유니버스 빌더 — 러셀1000 규칙 (상위 1,000)
=================================================================
설계서: docs/superpowers/specs/2026-08-16-largecap-repanel-design.md

    python scripts/build_largecap_universe.py

**네트워크 0회.** 소형주 유니버스를 지을 때 쌓아둔
`data/smallcap/market_caps.parquet` 은 *잴 수 있는 전 종목*의 시점 시가총액이고,
소형주는 그중 1,001~3,000 위만 잘라 썼다. 1~1,000 위는 그 파일 안에 그대로 있다.
그래서 이 스크립트는 새로 받는 게 없다 — 같은 캐시의 다른 칸을 자를 뿐이다.

산출물:
    data/largecap_universe.parquet      — (date, asset_id, ticker, cik, mktcap, rank)
    data/largecap_universe_report.json  — 커버리지·자기검사·음성 대조군

**자기검사에 걸리면 유니버스 파일을 쓰지 않는다** (소형주와 같은 규율).
"""
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import smallcap_universe as su          # noqa: E402
from modules.universe import SP500                   # noqa: E402
from scripts.build_smallcap_universe import (        # noqa: E402
    OUT_DIR, SHARD_DIR, step_spans,
)

CAPS     = os.path.join(OUT_DIR, "market_caps.parquet")
UNIVERSE = "data/largecap_universe.parquet"
REPORT   = "data/largecap_universe_report.json"

# 러셀1000 규칙. 소형주가 쓴 러셀2000(상위 1,000 제외, 다음 2,000)의 짝이라
# 고를 게 없고, 고를 게 없으면 튜닝할 여지도 없다.
TOP_EXCLUDE = 0
SIZE        = 1000

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _say(msg):
    print(f"[largecap] {msg}", flush=True)


def last_bars(spans: pd.DataFrame) -> tuple:
    """asset_id → 마지막 봉 날짜, 그리고 패널 끝. 캐시된 미조정 샤드만 읽는다.

    상장폐지의 정의가 "그 뒤로 봉이 없다"이므로 자기검사에 이게 필요하다.
    `build_smallcap_universe.build()` 가 하는 것과 같은 조립이고, 시가총액을
    안 만드는 것만 다르다.
    """
    spans_by_ticker = dict(tuple(spans.groupby("ticker", sort=False)))
    last, panel_end = {}, pd.Timestamp.min
    shards = sorted(f for f in os.listdir(SHARD_DIR) if f.endswith(".parquet"))
    for i, name in enumerate(shards, 1):
        shard = pd.read_parquet(os.path.join(SHARD_DIR, name))
        if shard.empty:
            continue
        panel_end = max(panel_end, shard["date"].max())
        closes_by_ticker = {t: g.set_index("date")["close"].sort_index()
                            for t, g in shard.groupby("ticker", sort=False)}
        part = [spans_by_ticker[t] for t in closes_by_ticker if t in spans_by_ticker]
        if not part:
            continue
        closes = su.slice_closes(closes_by_ticker, pd.concat(part, ignore_index=True))
        # 재활용 티커의 이전 주인은 다음 주인의 봉을 달고 있다 — 살려두면
        # 죽은 종목이 살아 있는 걸로 보여 상폐 수가 줄어든다.
        closes, _ = su.drop_recycled_predecessors(closes, pd.concat(part, ignore_index=True))
        last.update({aid: s.index[-1] for aid, s in closes.items() if len(s)})
        if i % 50 == 0:
            _say(f"  샤드 {i}/{len(shards)} — 상장 이력 {len(last)}")
    return last, panel_end


def negative_control(dates, spans, last_bar, panel_end) -> dict:
    """음성 대조군 — `modules/universe.py` 의 현재상장 목록으로 같은 상폐 수를 센다.

    이건 **0 이어야 한다.** 0 이 아니면 자기검사 1번의 통과가 아무 뜻도 없다
    (검사가 아무거나 통과시킨다는 뜻이므로). 설계서 5.3 절.
    """
    latest = (spans.sort_values("first_seen").groupby("ticker").tail(1)
                   .set_index("ticker")["listing_id"])
    aids = sorted({latest[t] for t in SP500 if t in latest.index})
    fake = pd.DataFrame([(d, a) for d in dates for a in aids],
                        columns=["date", "asset_id"])
    by_year = su.delistings_by_year(fake, last_bar, panel_end)
    return {"members": len(aids), "of": len(SP500),
            "delistings_by_year": {str(k): v for k, v in by_year.items()},
            "passed": not any(by_year.values())}


def build():
    spans, _ = step_spans()                    # 캐시. 없으면 여기서 웨이백을 받는다.
    caps = pd.read_parquet(CAPS)
    _say(f"시총 패널 {len(caps)}행 · {caps['date'].min().date()} ~ {caps['date'].max().date()}")

    universe = su.select_universe(caps, top_exclude=TOP_EXCLUDE, size=SIZE)
    universe = universe.merge(
        spans[["listing_id", "ticker", "cik"]].drop_duplicates("listing_id"),
        left_on="asset_id", right_on="listing_id", how="left").drop(columns="listing_id")

    last_bar, panel_end = last_bars(spans)
    ok, diag = su.survivorship_selftest(universe, last_bar, panel_end)
    ctrl = negative_control(sorted(universe["date"].unique()), spans, last_bar, panel_end)

    report = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "rule": {"top_exclude": TOP_EXCLUDE, "size": SIZE},
        "source": {"market_caps": CAPS, "network_requests": 0},
        "prices": {"listings": len(last_bar), "panel_end": str(panel_end.date())},
        "universe": {
            "rows": int(len(universe)),
            "dates": int(universe["date"].nunique()),
            "listings": int(universe["asset_id"].nunique()),
            "ciks": int(universe["cik"].nunique()),
            "median_members": float(universe.groupby("date").size().median()),
            "mktcap_floor_median": float(universe.groupby("date")["mktcap"].min().median()),
            "mktcap_median": float(universe["mktcap"].median()),
        },
        "selftest": {"passed": bool(ok), "checks": diag["checks"],
                     "delistings_by_year": {str(k): v for k, v
                                            in diag["delistings_by_year"].items()}},
        "negative_control": ctrl,
    }
    os.makedirs("data", exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    passed = ok and ctrl["passed"]
    if not passed:
        _say(f"자기검사 실패 — 유니버스를 쓰지 않습니다. {diag['checks']} · "
             f"음성대조 {ctrl['passed']}")
        _say(f"진단은 {REPORT} 에 남겼습니다.")
        return report, False

    universe.to_parquet(UNIVERSE, index=False)
    u = report["universe"]
    _say(f"완료 — {UNIVERSE} · {u['rows']}행 · 월 중앙값 {u['median_members']:.0f}종목 · "
         f"CIK {u['ciks']} · 시총 하한 중앙값 ${u['mktcap_floor_median'] / 1e9:.2f}B")
    return report, True


if __name__ == "__main__":
    _, _ok = build()
    sys.exit(0 if _ok else 1)
