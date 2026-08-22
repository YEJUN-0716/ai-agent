"""인덱스 자동운용 규칙 — 부족한 것부터 정수주로 산다.

설계서 4장의 규칙 전부가 여기 있다. 파일도 네트워크도 안 건드리는 순수
함수라 러너 없이 그대로 테스트하고, 과거 시뮬(scripts/measure_index_autopilot.py)
에서도 같은 함수를 부른다. 규칙이 두 군데로 갈리면 잰 것과 도는 것이 달라진다.

규칙에 없는 것: 매도 · 밴드 · 레짐 필터 · 소수점 주식.
한 주를 살 돈이 모자라면 그 달은 건너뛰고 현금을 이월한다. 이월 자체가
정수주 적립이 실제로 가진 마찰이므로 감추지 않는다(설계서 4.1).
"""

from __future__ import annotations

TARGETS = {"ITOT": 0.70, "AGG": 0.20, "GLDM": 0.10}

# 계획은 전날 종가로 세우고 체결은 다음 거래일 시가다. 그 사이 갭상승이 나면
# 계획 수량의 실제 비용이 현금을 넘고, 브로커는 그 주문을 통째로 거절한다
# (부분 체결은 실제로 걸 수 없는 체결이라 일부러 금지했다 — virtual_broker).
# 그러면 그 달 그 자산만 조용히 빠지는데 적립은 이미 완료로 찍혀 재시도되지
# 않고, 벤치마크 ITOT 는 정상 매수된 걸로 계산돼 비교가 우리 쪽에 불리해진다.
# 계획 단계에서 이만큼 남겨 두면 그 여유로 갭을 흡수한다. 남은 현금은 다음 달
# 계획에 이월되므로 놀리는 게 아니라 미루는 것이다.
#
# 3% 의 근거 (2021-01~2026-08, 1,411 거래일 시가/전일종가 실측):
#   ITOT p99 +1.62% 최대 +3.64% · AGG p99 +0.74% 최대 +1.51%
#   GLDM p99 +2.16% 최대 +5.95%
# 정수주 잔돈이 이미 여유로 남으므로 실효 방어폭은 이보다 넓다. 다만 GLDM 급
# 6% 갭이 잔돈이 적은 달과 겹치면 여전히 거절될 수 있다 — 없앤 게 아니라 줄였다.
# 적립금이 클수록 잔돈 비율이 작아져 이 버퍼가 하는 일이 커진다.
#
# **여기 있는 이유**: 러너에만 두면 마찰을 재는 쪽(scripts/measure_index_autopilot)
# 이 안 따라온다 — 실제로 그랬다. 측정은 버퍼 없이 굴려 놓고 실전은 매달 3% 를
# 남겼으니, "정수주 마찰 연 −0.03%p" 는 도는 규칙이 아닌 것으로 잰 값이었다.
# 정상상태로 한 달치 적립금의 약 3.1% 가 늘 안 굴러간다(12개월이면 잔고의 0.26%).
GAP_BUFFER_BP = 300.0


def plan_orders(
    holdings: dict,
    prices: dict,
    cash_usd: float,
    targets: dict = TARGETS,
    whole_shares: bool = True,
    gap_buffer_bp: float = GAP_BUFFER_BP,
) -> list[dict]:
    """이번 달 낼 매수 주문. 부족분이 큰 순서, 항상 양의 정수주.

    holdings 는 {티커: 보유수량}, prices 는 {티커: 1주 가격(USD)}.
    반환 [{"ticker", "qty", "est_price"}, ...] — 총액은 갭 버퍼를 뺀 뒤의
    cash_usd 를 넘지 않는다.

    `gap_buffer_bp` 를 **여기서** 빼는 이유: 부르는 쪽이 각자 빼면 한쪽이
    빠뜨린다(러너는 뺐고 측정 스크립트는 안 뺐다). 규칙은 이 함수가 소유한다.

    `whole_shares=False` 는 **측정 전용**이다. 실운용은 소수점 매매를 안 쓴다
    (설계서 4.1). 마찰을 재려면 "같은 규칙에서 정수주 제약만 뺀 줄"이 필요한데,
    그걸 측정 스크립트에 따로 짜면 잰 규칙과 도는 규칙이 갈린다.
    """
    if abs(sum(targets.values()) - 1.0) > 1e-9:
        raise ValueError(f"목표비중 합이 1.0 이 아니다: {sum(targets.values())}")
    for t in targets:
        # 가격을 못 받아온 자산을 조용히 건너뛰면 그 자산만 영원히 안 사진다.
        if prices.get(t, 0) <= 0:
            raise ValueError(f"{t} 가격이 없다: {prices.get(t)!r}")

    cash_usd = cash_usd * (1 - gap_buffer_bp / 1e4)
    values = {t: holdings.get(t, 0) * prices[t] for t in targets}
    total = sum(values.values()) + cash_usd
    gaps = {t: total * w - values[t] for t, w in targets.items()}

    orders = []
    remaining = cash_usd
    for t in sorted(gaps, key=lambda k: -gaps[k]):
        avail = min(gaps[t], remaining)                  # 음수 부족분이면 0 이하
        qty = int(avail // prices[t]) if whole_shares else avail / prices[t]
        if qty <= 0:
            continue
        orders.append({"ticker": t, "qty": qty, "est_price": prices[t]})
        remaining -= qty * prices[t]
    return orders


def demo() -> None:
    """assert 기반 자체검사. 프레임워크 없음 (설계서 9장)."""
    prices = {"ITOT": 120.0, "AGG": 98.0, "GLDM": 87.0}
    monthly = 714.0  # 월 100만원 ≈ $714

    # 빈 장부: 목표 $500/$143/$71 → ITOT 4주, AGG 1주, GLDM 은 1주($87)를 못 채워 이월
    orders = plan_orders({}, prices, monthly)
    assert [(o["ticker"], o["qty"]) for o in orders] == [("ITOT", 4), ("AGG", 1)], orders
    spent = sum(o["qty"] * o["est_price"] for o in orders)
    assert spent <= monthly and monthly - spent > 0, spent

    # 두 달 이월하면 GLDM 1주가 나온다
    assert any(o["ticker"] == "GLDM" for o in plan_orders({}, prices, monthly * 2))

    # 매도 없음: 목표를 크게 넘긴 자산은 주문에서 빠질 뿐 음수가 안 된다
    for o in plan_orders({"AGG": 100}, prices, monthly):
        assert o["qty"] > 0 and isinstance(o["qty"], int), o
        assert o["ticker"] != "AGG", o

    # 측정용 소수점 경로: 같은 규칙인데 이월이 없다 — 그 차이가 곧 마찰이다.
    # 갭 버퍼는 양쪽에 똑같이 걸리므로 비교에서 약분된다.
    frac = plan_orders({}, prices, monthly, whole_shares=False)
    budget = monthly * (1 - GAP_BUFFER_BP / 1e4)
    assert abs(sum(o["qty"] * o["est_price"] for o in frac) - budget) < 1e-9, frac
    assert {o["ticker"] for o in frac} == set(prices), frac

    # 버퍼는 이 함수가 뺀다 — 부르는 쪽이 또 빼면 두 번 빠진다
    assert (plan_orders({}, prices, monthly, gap_buffer_bp=0.0)[0]["qty"]
            >= plan_orders({}, prices, monthly)[0]["qty"])

    # 현금 0 이면 아무것도 안 산다
    assert plan_orders({"ITOT": 10}, prices, 0.0) == []

    # 폭락: ITOT 반토막이면 ITOT 이 우선순위 1위
    holdings = {"ITOT": 42, "AGG": 15, "GLDM": 8}
    assert plan_orders(holdings, {**prices, "ITOT": 60.0}, monthly)[0]["ticker"] == "ITOT"

    # 목표비중 합 검사
    try:
        plan_orders({}, prices, monthly, {"ITOT": 0.7, "AGG": 0.2})
    except ValueError:
        pass
    else:
        raise AssertionError("비중 합 1.0 검사가 안 걸렸다")

    print("index_autopilot demo: OK")


if __name__ == "__main__":
    demo()
