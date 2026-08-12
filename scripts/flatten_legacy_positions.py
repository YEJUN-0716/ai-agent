#!/usr/bin/env python
"""가상 장부의 **팩터 시절 보유분**을 전량 매도 예약한다 (일회성 정리).

    python scripts/flatten_legacy_positions.py --dry-run   # 뭘 팔지만 본다
    python scripts/flatten_legacy_positions.py             # 매도 예약

## 왜

2026-08-10 에 장부를 팩터 엔진에서 트레이드 플랜으로 갈아끼웠는데(PR #81),
**갈아끼운 뒤 플랜 트레이드가 한 건도 안 생겼다.** 자리가 10칸인데 팩터 시절
보유 10종목이 전부 쥐고 있고 남은 현금은 한 건도 못 산다. 2026-08-12 확인:
`r_realized` 기록 0건, 9일째 그 상태다.

그 사이 진입 규칙 측정(PR #90·#91)에서 백테스트 기대값이 +0.49R 이 아니라
+0.02R 로 내려앉았다. 그러면 **실체결 기록이 유일하게 남은 증거**인데, 지금
구조로는 그게 영영 안 쌓인다. 트레일링 10% 가 알아서 비켜 줄 때까지 기다리는
설계였지만 그건 기약이 없다.

## 무엇을

`plan` 키가 없는 포지션 = 팩터 시절 보유분이다. 플랜 포지션은 손절·목표
라인이 붙어 있어 `plan` 키가 있다. 그 구분만 보고 판다 — **규칙을 섞지 않는다.**

체결은 가상 브로커의 정상 경로를 탄다: 예약 → 다음 거래일 **시가** 체결.
지금 가격에 즉시 채워 주지 않는다. 없는 체결을 만들어 내는 순간 이 장부가
증거로서의 값을 잃는다.

이미 매도 예약이 걸린 종목은 건너뛴다(두 번 돌려도 안전).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import virtual_broker as vb  # noqa: E402


def legacy_symbols(state: dict) -> list[str]:
    """팩터 시절 보유분 = `plan` 키가 없는 포지션. 이미 예약된 것은 뺀다."""
    queued = {o["symbol"] for o in state.get("pending", [])
              if o.get("side") == "sell"}
    return sorted(sym for sym, pos in state.get("positions", {}).items()
                  if not pos.get("plan") and sym not in queued)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    dry = "--dry-run" in sys.argv
    state = vb.load_state()
    targets = legacy_symbols(state)
    if not targets:
        print("정리할 팩터 보유분이 없습니다.")
        return 0

    fx = vb.krw_per_usd()
    print(f"팩터 시절 보유 {len(targets)}종목 — 다음 거래일 시가 매도 예약"
          f"{' (드라이런)' if dry else ''}\n")
    total = 0.0
    for sym in targets:
        pos = state["positions"][sym]
        qty, avg = pos["qty"], pos["avg_price_usd"]
        total += qty * avg * fx
        print(f"  {sym:6s} {qty:4d}주  평단 ${avg:,.2f}  ≈ {qty * avg * fx:,.0f}원")
        if not dry:
            vb.place_market_sell(sym, qty)

    print(f"\n합계 원가 약 {total:,.0f}원 · 현재 현금 "
          f"{state['cash_krw']:,.0f}원")
    if dry:
        print("\n드라이런입니다. 실제로 예약하려면 --dry-run 을 빼고 다시.")
    else:
        print("\n예약 완료. 다음 러너 실행이 시가로 체결하고 자리를 비웁니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
