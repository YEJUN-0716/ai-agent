#!/usr/bin/env python
"""signal_log.json 의 조기 확정분을 21거래일 규칙으로 다시 찍는다.

    python scripts/restamp_signal_log.py [--apply]

러너가 달력 21일 + '도는 날 종가' 로 채점하던 시절의 기록을 고친다.
21봉이 찬 건은 다시 계산하고, **아직 안 찬 건은 미결로 되돌린다** —
채점할 수 없는 것을 채점해 둔 상태가 원래 결함이다.

한 번 쓰고 버리는 도구가 아니다. 다시 돌려도 결과가 같아야 한다(이미 옳은
값은 그대로 나오고, 미결은 봉이 차면 그때 채워진다).
"""
from __future__ import annotations

import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")

# 로컬 콘솔이 cp949 면 em dash 하나가 스크립트를 죽인다 (ic_weight_updater 와
# 같은 처리 — 한글 본문은 그대로 나오고 기호만 '?' 가 된다).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402
import yfinance as yf  # noqa: E402

from modules.signal_scorecard import score_signal  # noqa: E402

LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "signal_log.json")


def _closes(symbol: str, start: str):
    raw = yf.download(symbol, start=start, progress=False, auto_adjust=True)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.droplevel(1)
    return None if raw.empty else raw["Close"].dropna()


def main(apply: bool) -> int:
    data = json.load(open(LOG, encoding="utf-8"))
    signals = data["signals"]
    cache: dict = {}
    changed = cleared = 0

    for s in signals:
        old = s.get("return_pct")
        sym, entry = s.get("symbol"), s.get("entry_date")
        if not sym or not entry:
            continue
        if sym not in cache:
            try:
                cache[sym] = _closes(sym, entry)
            except Exception as e:
                print(f"  [건너뜀] {sym} 시세 실패 — {type(e).__name__}")
                cache[sym] = None
        scored = score_signal(cache[sym], entry, s.get("entry_price"))
        if scored is None:
            if old is not None:
                # 21봉이 안 찼는데 값이 박혀 있던 것 — 미결로 되돌린다.
                s["return_pct"] = s["outcome_price"] = s["outcome_date"] = None
                cleared += 1
                print(f"  [미결로] {sym} {entry}  {old:+.2f}% → 대기 "
                      f"(21봉 미도달)")
            continue
        if old is None or abs(old - scored["return_pct"]) >= 0.01:
            print(f"  [재계산] {sym} {entry}  "
                  f"{'대기' if old is None else f'{old:+.2f}%'} → "
                  f"{scored['return_pct']:+.2f}%  ({scored['outcome_date']})")
            changed += 1
        s.update(scored)

    print(f"\n재계산 {changed}건 · 미결 복원 {cleared}건 / 전체 {len(signals)}건")
    if apply:
        with open(LOG, "w", encoding="utf-8") as f:
            json.dump({"signals": signals}, f, ensure_ascii=False, indent=2)
        print(f"{LOG} 저장 완료.")
    else:
        print("--apply 없이 돌렸다 — 파일은 안 건드렸다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--apply" in sys.argv))
