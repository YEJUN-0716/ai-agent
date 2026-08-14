"""
소형주 유니버스 빌더 — 생존자 편향 없는 월별 패널
=================================================================
설계서: docs/superpowers/specs/2026-08-14-smallcap-universe-design.md

    python scripts/build_smallcap_universe.py
    python scripts/build_smallcap_universe.py --force prices   # 그 단계만 다시

단계마다 디스크에 남기므로 중단해도 이어서 돈다. 로직은 전부
modules/smallcap_universe.py 에 있고 이 파일은 네트워크와 파일만 다룬다.

산출물:
    data/smallcap_universe.parquet        — (date, asset_id, ticker, cik, mktcap, rank)
    data/smallcap_universe_report.json    — 커버리지·자기검사

**자기검사에 걸리면 유니버스 파일을 쓰지 않는다.** 죽은 종목이 안 들어간
소형주 패널은 없는 것만 못하다.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import alpaca_data as ad          # noqa: E402
from modules import edgar_fundamentals as ef    # noqa: E402
from modules import smallcap_universe as su     # noqa: E402

OUT_DIR    = "data/smallcap"
SNAP_DIR   = os.path.join(OUT_DIR, "snapshots")
SHARD_DIR  = os.path.join(OUT_DIR, "prices")
UNIVERSE   = "data/smallcap_universe.parquet"
REPORT     = "data/smallcap_universe_report.json"

START_DATE = pd.Timestamp("2016-01-01")   # Alpaca 일봉의 시작
BATCH      = 100                           # 한 번에 조회할 티커 수

# 프레임 값을 쓸 수 있게 되는 시점 = 기간말 + 이만큼.
# SEC 프레임 API 는 공시일(filed)을 안 준다. 90일은 비가속 신고자의 10-K
# 기한이라 이보다 늦게 나오는 경우가 드물다. **늦게 쓰는 건 안전하고
# 일찍 쓰는 것만 룩어헤드다** — 그래서 넉넉한 쪽으로 잡았다.
SHARES_LAG_DAYS = 90

SHARES_FRAMES = [("dei", "EntityCommonStockSharesOutstanding"),
                 ("us-gaap", "CommonStockSharesOutstanding")]

CDX = "http://web.archive.org/cdx/search/cdx"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

if hasattr(sys.stdout, "reconfigure"):        # 윈도우 콘솔은 cp949 다
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _say(msg):
    print(f"[smallcap] {msg}", flush=True)


def _cached(path):
    """(DataFrame, 보고서) 캐시 쌍. 보고서는 parquet 옆 json 이다 —
    df.attrs 는 parquet 왕복에서 살아남는다는 보장이 없다."""
    rep_path = path.replace(".parquet", "_report.json")
    if not (os.path.exists(path) and os.path.exists(rep_path)):
        return None
    with open(rep_path, encoding="utf-8") as f:
        return pd.read_parquet(path), json.load(f)


def _save(df, rep, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_parquet(path, index=False)
    with open(path.replace(".parquet", "_report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)


# ── 1. 과거 상장 목록 (웨이백 스냅샷) ──────────────────────────────────
def _wayback(url, attempts=5):
    """웨이백은 자주 끊고 자주 막는다. 끊긴 스냅샷은 그 해가 통째로 비므로
    (상폐 종목이 그만큼 빠진다) 넉넉히 재시도한다."""
    for i in range(attempts):
        try:
            resp = requests.get(url, timeout=180)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 503, 502, 504):
                time.sleep(5 * 2 ** i)
                continue
            return None
        except Exception:                              # noqa: BLE001
            time.sleep(5 * 2 ** i)
    return None



def step_spans(force=False) -> tuple:
    """SEC company_tickers.json 의 과거 스냅샷 → 상장 구간표.

    **왜 이 출처인가.** 처음엔 Alpaca `/v2/assets?status=inactive` 로 상폐
    종목을 모았는데, 실측해 보니 SIVB·FRC·PXD·ATVI 가 그 목록에 아예 없었다
    (일봉은 멀쩡히 나오는데도). 상폐 등기부로 쓸 수 있는 물건이 아니다.
    회사명으로 CIK 를 붙이는 우회로도 실패했다 — 상폐 종목 매칭률 21~24%.

    스냅샷은 티커와 CIK 를 **같이** 주므로 매칭이 필요 없고, 그 시점에
    상장돼 있었다는 사실 자체가 기록이다.
    """
    path = os.path.join(OUT_DIR, "listing_spans.parquet")
    if not force and (hit := _cached(path)):
        return hit

    os.makedirs(SNAP_DIR, exist_ok=True)
    r = requests.get(CDX, params={"url": "sec.gov/files/company_tickers.json",
                                  "output": "json", "collapse": "timestamp:6",
                                  "fl": "timestamp,statuscode", "limit": 500},
                     timeout=120)
    r.raise_for_status()
    stamps = [row[0] for row in r.json()[1:] if row[1] == "200"]
    _say(f"웨이백 스냅샷 {len(stamps)}개 ({stamps[0][:6]} ~ {stamps[-1][:6]})")

    rows = []
    for i, ts in enumerate(stamps, 1):
        shard = os.path.join(SNAP_DIR, f"{ts}.json")
        if os.path.exists(shard):
            with open(shard, encoding="utf-8") as f:
                rows.extend(json.load(f))
            continue
        payload = _wayback(f"https://web.archive.org/web/{ts}id_/{TICKERS_URL}")
        if not payload:
            _say(f"  {ts}: 못 받았습니다 — 건너뜁니다")
            continue
        snap_date = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
        got = [[snap_date, v["ticker"], int(v["cik_str"])]
               for v in payload.values() if v.get("ticker")]
        with open(shard, "w", encoding="utf-8") as f:
            json.dump(got, f)
        rows.extend(got)
        _say(f"  {i}/{len(stamps)} {snap_date}: {len(got)}종목")
        time.sleep(1.0)                                # 웨이백에 예의는 지킨다

    # SEC 표기 → Alpaca 표기. 보통주가 아닌 것(우선주·워런트·유닛)은 여기서 빠진다.
    normed = [[d, sym, c] for d, t, c in rows if (sym := su.alpaca_symbol(t))]
    dropped = len({t for _, t, _ in rows}) - len({t for _, t, _ in normed})
    _say(f"보통주 아닌 티커 {dropped}개 제외")

    spans, rep = su.build_listing_spans(normed)
    rep["non_common_tickers_dropped"] = int(dropped)
    _say(f"상장 이력 {rep['listings']}건 · 티커 {rep['tickers']} · "
         f"재활용 {rep['recycled_tickers']} · 스냅샷 {rep['snapshots']}")
    _save(spans, rep, path)
    return spans, rep


# ── 2. 주식수 (SEC 프레임 API) ─────────────────────────────────────────
def _quarters(start: pd.Timestamp, end: pd.Timestamp):
    for ts in pd.date_range(start, end, freq="QE"):
        yield f"CY{ts.year}Q{ts.quarter}I", ts


def step_shares(force=False) -> tuple:
    """분기마다 전 종목 주식수를 **한 번의 요청**으로 받는다.

    종목별 companyfacts 를 1만 개 내려받는 대신 프레임 84개면 된다.
    프레임은 공시일을 안 주므로 SHARES_LAG_DAYS 만큼 늦춰 쓴다.
    """
    path = os.path.join(OUT_DIR, "shares_frames.parquet")
    if not force and (hit := _cached(path)):
        return hit

    now  = pd.Timestamp.now(tz=None).normalize()
    rows = []
    for frame, _ in _quarters(START_DATE - pd.DateOffset(years=1), now):
        got = 0
        for ns, tag in SHARES_FRAMES:
            url = f"https://data.sec.gov/api/xbrl/frames/{ns}/{tag}/shares/{frame}.json"
            time.sleep(ef.RATE_SLEEP)
            resp = requests.get(url, headers=ef.SEC_HEADERS, timeout=120)
            if resp.status_code != 200:
                continue
            for d in resp.json().get("data", []):
                rows.append({"cik": int(d["cik"]), "end": pd.Timestamp(d["end"]),
                             "val": float(d["val"]), "src": ns})
                got += 1
        _say(f"  {frame}: {got}행")

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("SEC 프레임에서 주식수를 하나도 못 받았습니다.")

    # 같은 (cik, end) 가 두 네임스페이스에 다 있으면 dei(표지 공시)를 쓴다.
    df["_pri"] = (df["src"] != "dei").astype(int)
    df = (df.sort_values(["cik", "end", "_pri"])
            .drop_duplicates(["cik", "end"], keep="first")
            .drop(columns="_pri"))
    df["usable_from"] = df["end"] + pd.Timedelta(days=SHARES_LAG_DAYS)

    rep = {"rows": int(len(df)), "ciks": int(df["cik"].nunique())}
    _save(df, rep, path)
    _say(f"주식수 {rep['rows']}행 · {rep['ciks']} CIK")
    return df, rep


# ── 3. 미조정 일봉 ─────────────────────────────────────────────────────
def _bars_tolerant(batch, end, start=None, adjustment="raw"):
    """배치를 받되, **400 일 때만** 반으로 갈라 나머지를 살린다.

    `start`·`adjustment` 는 조정 일봉 수급(`scripts/fetch_smallcap_panel.py`)이
    같은 관용 규칙을 쓰려고 뚫은 것이다 — 400 만 삼킨다는 이 판단이 두 벌로
    갈리면 한쪽만 고치는 날이 온다.

    심볼 하나가 배치 전체를 400 으로 죽이는 걸 실제로 겪었다(대시 심볼).
    표기는 `su.alpaca_symbol` 이 거르지만, 못 걸러낸 게 하나 나왔다고
    191개 배치가 통째로 멈추면 안 된다.

    **400 만 삼킨다.** 처음엔 HTTPError 를 통째로 삼켰는데, 그 바람에
    403(`subscription does not permit querying recent SIP data`)이 "심볼 거절"로
    둔갑해 빈 샤드를 조용히 쌓았다. 관용은 아는 실패에만 베푼다.
    """
    start = (start or START_DATE).to_pydatetime().replace(tzinfo=timezone.utc)
    try:
        return ad.get_bars(batch, timeframe="1Day", start=start,
                           end=end, feed_name="sip", adjustment=adjustment,
                           max_pages=400)
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 400:
            raise
        if len(batch) == 1:
            _say(f"  거절된 심볼: {batch[0]}")
            return {}
        mid = len(batch) // 2
        return {**_bars_tolerant(batch[:mid], end, pd.Timestamp(start), adjustment),
                **_bars_tolerant(batch[mid:], end, pd.Timestamp(start), adjustment)}



def step_prices(tickers, force=False) -> list:
    """일봉을 샤드로 받아 둔다 → 샤드 경로 목록.

    한 번에 메모리에 안 올린다. 티커 1만5천 × 10년이면 수천만 행이라,
    샤드별로 읽어 시총만 뽑고 버리는 게 이 규모에서 유일하게 도는 방법이다.
    """
    os.makedirs(SHARD_DIR, exist_ok=True)
    if force:
        for f in os.listdir(SHARD_DIR):
            os.remove(os.path.join(SHARD_DIR, f))

    # 무료 SIP 구독은 **최근 데이터를 안 준다**(403 "querying recent SIP data").
    # 어제까지로 끊는다 — 월말 패널이라 하루는 아쉽지 않다.
    tickers = sorted(tickers)
    end     = datetime.now(timezone.utc) - timedelta(days=1)
    batches = [tickers[i:i + BATCH] for i in range(0, len(tickers), BATCH)]
    paths   = []

    for n, batch in enumerate(batches, 1):
        # 샤드 이름은 **배치 내용의 해시**다. 번호로 두면 티커가 하나만
        # 늘거나 줄어도 배치 경계가 밀려서, 캐시된 샤드가 다른 티커 묶음인
        # 채로 조용히 재사용된다.
        key = hashlib.sha1(",".join(batch).encode()).hexdigest()[:16]
        shard = os.path.join(SHARD_DIR, f"{key}.parquet")
        paths.append(shard)
        if os.path.exists(shard):
            continue
        bars = _bars_tolerant(batch, end)
        rows = [pd.DataFrame({"ticker": sym,
                              "date": frame.index.tz_localize(None).normalize(),
                              "close": frame["Close"].astype("float32").to_numpy()})
                for sym, frame in bars.items() if not frame.empty]
        shard_df = (pd.concat(rows, ignore_index=True) if rows
                    else pd.DataFrame(columns=["ticker", "date", "close"]))
        shard_df.to_parquet(shard, index=False)
        _say(f"  일봉 {n}/{len(batches)} — {len(bars)}종목 {len(shard_df)}행")

    return paths


# ── 4. 조립 ────────────────────────────────────────────────────────────
def build(force=()):
    spans,     sp_rep = step_spans("spans" in force)
    shares_df, s_rep  = step_shares("shares" in force)

    by_cik = {cik: g.sort_values("usable_from").set_index("usable_from")["val"]
              for cik, g in shares_df.groupby("cik", sort=False)}
    spans = spans.loc[spans["cik"].isin(by_cik)].copy()
    _say(f"주식수가 있는 상장 이력 {len(spans)}건 · CIK {spans['cik'].nunique()}")

    shard_paths = step_prices(spans["ticker"].unique(), "prices" in force)

    # 패널은 **첫 스냅샷일부터** 시작한다. 그 전에 죽은 종목은 어느 스냅샷에도
    # 안 잡혀 아예 없으므로, 2016~2017 상반기 행을 내놓으면 생존자 편향이
    # 그대로 든 구간을 편향 없는 척 넘기는 셈이다. 일봉은 2016년부터 받되
    # (거래일 60일 하한을 채우려면 앞이 필요하다) 행은 여기서부터 낸다.
    panel_start = spans["first_seen"].min()
    month_ends = pd.date_range(panel_start, pd.Timestamp.now().normalize(), freq="ME")
    _say(f"패널 구간 {panel_start.date()} ~ (첫 스냅샷 기준)")
    spans_by_ticker = dict(tuple(spans.groupby("ticker", sort=False)))

    caps, last_bar, panel_end, n_rows = [], {}, pd.Timestamp.min, 0
    n_recycled = 0
    for path in shard_paths:
        if not os.path.exists(path):
            continue
        shard = pd.read_parquet(path)
        if shard.empty:
            continue
        n_rows += len(shard)
        panel_end = max(panel_end, shard["date"].max())

        closes_by_ticker = {t: g.set_index("date")["close"].sort_index()
                            for t, g in shard.groupby("ticker", sort=False)}
        part_spans = pd.concat([spans_by_ticker[t] for t in closes_by_ticker
                                if t in spans_by_ticker], ignore_index=True) \
            if closes_by_ticker else spans.head(0)
        if part_spans.empty:
            continue

        closes = su.slice_closes(closes_by_ticker, part_spans)
        # 재활용 티커의 이전 주인은 다른 회사의 봉이다 — 시가총액도 그만큼 틀린다.
        closes, dropped_recycled = su.drop_recycled_predecessors(closes, part_spans)
        n_recycled += len(dropped_recycled)
        shares = {row.listing_id: by_cik[row.cik]
                  for row in part_spans.itertuples(index=False)
                  if row.cik in by_cik}
        last_bar.update({lid: s.index[-1] for lid, s in closes.items()})
        caps.append(su.market_cap_panel(closes, shares, month_ends))

    cap_panel = pd.concat(caps, ignore_index=True) if caps else pd.DataFrame()
    if cap_panel.empty:
        raise RuntimeError("시가총액을 하나도 못 만들었습니다.")

    cap_panel.to_parquet(os.path.join(OUT_DIR, "market_caps.parquet"), index=False)
    universe = su.select_universe(cap_panel)
    universe = universe.merge(
        spans[["listing_id", "ticker", "cik"]].drop_duplicates("listing_id"),
        left_on="asset_id", right_on="listing_id", how="left").drop(columns="listing_id")

    ok, diag = su.survivorship_selftest(universe, last_bar, panel_end)
    report = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "spans":    sp_rep,
        "shares":   {**s_rep, "listings_with_shares": int(len(spans)),
                     "lag_days": SHARES_LAG_DAYS},
        "prices":   {"rows": int(n_rows), "listings": int(len(last_bar)),
                     "panel_end": str(panel_end.date()),
                     "dropped_recycled_predecessors": int(n_recycled)},
        # 모집단(pool)이 곧 이 유니버스의 천장이다. 순위 1,001~3,000 을 뽑는데
        # 시총을 잴 수 있는 종목이 3,500개뿐이면, 그건 '소형주'가 아니라
        # '잴 수 있었던 것 중 아래쪽'이다. 하한 시총을 같이 적어 그걸 드러낸다.
        "pool":     {"median_per_date": float(cap_panel.groupby("date").size().median()),
                     "min_per_date": int(cap_panel.groupby("date").size().min())},
        "universe": {"rows": int(len(universe)),
                     "dates": int(universe["date"].nunique()),
                     "listings": int(universe["asset_id"].nunique()),
                     "median_members": float(universe.groupby("date").size().median())
                                       if len(universe) else 0.0,
                     "mktcap_floor_median": float(
                         universe.groupby("date")["mktcap"].min().median()),
                     "mktcap_median": float(universe["mktcap"].median())},
        "selftest": {"passed": bool(ok), "checks": diag["checks"],
                     "delistings_by_year": {str(k): v for k, v
                                            in diag["delistings_by_year"].items()}},
    }
    os.makedirs("data", exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    if not ok:
        _say(f"자기검사 실패 — 유니버스를 쓰지 않습니다. {diag['checks']}")
        _say(f"진단은 {REPORT} 에 남겼습니다.")
        return report, False

    universe.to_parquet(UNIVERSE, index=False)
    _say(f"완료 — {UNIVERSE} · {len(universe)}행 · "
         f"월 중앙값 {report['universe']['median_members']:.0f}종목")
    return report, True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", nargs="*", default=[],
                    choices=["spans", "shares", "prices"],
                    help="해당 단계 캐시를 무시하고 다시 받는다")
    args = ap.parse_args()
    _, ok = build(force=set(args.force))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
