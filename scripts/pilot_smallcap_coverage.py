"""소형주 F-Score 커버리지 파일럿 — 3,508개를 다 받기 전에 표본 150개로 잰다.

`docs/superpowers/specs/2026-08-14-fscore-smallcap-design.md` 5.3절의 숫자가
여기서 나온다. **수익률은 한 줄도 안 본다** — 재무 재료가 몇 종목에서 8항목을
채우는지, 그리고 전체 수급이 얼마나 걸리고 얼마를 쓰는지만 본다.

받은 companyfacts 는 `data/edgar_raw` 에 캐시되므로 본 수급이 그대로 재사용한다.

    python -m scripts.pilot_smallcap_coverage [표본수]
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules import edgar_fundamentals as ef  # noqa: E402
from scripts.measure_fscore import START, END, ticker_frame, fscore_rows  # noqa: E402

UNIVERSE = "data/smallcap_universe.parquet"
SEED = 20260814
NEED = ["net_income", "revenue", "operating_cash_flow", "assets",
        "current_assets", "current_liabilities", "long_term_debt", "equity", "shares"]


def sample_names(n: int) -> pd.DataFrame:
    """본구간에 한 번이라도 유니버스에 든 종목에서 n개. 종목 키는 asset_id 다 —
    티커는 재사용되므로(BBBY) 티커로 뽑으면 두 회사를 하나로 센다."""
    u = pd.read_parquet(UNIVERSE)
    w = u.loc[(u["date"] >= START) & (u["date"] <= END)]
    names = w[["asset_id", "ticker", "cik"]].drop_duplicates("asset_id")
    idx = random.Random(SEED).sample(range(len(names)), min(n, len(names)))
    return names.iloc[sorted(idx)], len(names), names["cik"].nunique()


def main(n: int = 150) -> int:
    sample, pool, n_cik = sample_names(n)
    print(f"본구간 {START.date()}~{END.date()} 종목 {pool} · CIK {n_cik} · 표본 {len(sample)}")

    stat = {"companyfacts 없음": 0, "분기 재료 없음": 0, "8항목 못 채움": 0, "성공": 0}
    miss = dict.fromkeys(NEED, 0)
    quarters, in_window, sizes = [], [], []

    for i, r in enumerate(sample.itertuples(), 1):
        cik = int(r.cik)
        ug = ef.load_raw(r.ticker, cik=cik)     # cik 필수 — 죽은 티커는 조회가 틀린다
        path = Path(ef.RAW_DIR) / f"CIK{cik:010d}.json"
        if path.exists():
            sizes.append(path.stat().st_size)
        if ug is None:
            stat["companyfacts 없음"] += 1
        else:
            m = ticker_frame(r.ticker, ug)
            if m.empty:
                stat["분기 재료 없음"] += 1
            else:
                rows = fscore_rows(m, r.ticker)
                if rows:
                    stat["성공"] += 1
                    quarters.append(len(rows))
                    filed = pd.Series([x["filed"] for x in rows])
                    in_window.append(int(((filed >= START) & (filed <= END)).sum()))
                else:
                    stat["8항목 못 채움"] += 1
                    tail = m.iloc[-9:]
                    for c in NEED:
                        if not np.isfinite(tail[c].to_numpy(float)).all():
                            miss[c] += 1
        if i % 25 == 0:
            print(f"  {i}/{len(sample)} · 성공 {stat['성공']}")

    q, iw, sz = (pd.Series(x, dtype=float) for x in (quarters, in_window, sizes))
    out = {
        "표본": len(sample), "본구간 종목": pool, "분해": stat,
        "성공률": round(stat["성공"] / len(sample), 4),
        "종목당 분기 수 중앙값": float(q.median()) if len(q) else 0.0,
        "본구간 분기 수 중앙값": float(iw.median()) if len(iw) else 0.0,
        "본구간 0분기": int((iw == 0).sum()) if len(iw) else 0,
        "탈락 시 결측 항목": miss,
        "파일 MB 평균": round(float(sz.mean()) / 1e6, 2) if len(sz) else 0.0,
        # 전체 수급 견적. 이 두 줄이 파일럿의 실제 용도다.
        # 파일은 CIK 당 하나고(한 회사가 두 번 상장해도 하나), 유효 종목은 상장 이력 단위다.
        "전체 견적 GB": round(float(sz.mean()) * n_cik / 1e9, 1) if len(sz) else 0.0,
        "전체 견적 유효종목": int(pool * stat["성공"] / len(sample)),
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 150))
