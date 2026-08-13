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


def plan_orders(
    holdings: dict,
    prices: dict,
    cash_usd: float,
    targets: dict = TARGETS,
) -> list[dict]:
    """이번 달 낼 매수 주문. 부족분이 큰 순서, 항상 양의 정수주.

    holdings 는 {티커: 보유수량}, prices 는 {티커: 1주 가격(USD)}.
    반환 [{"ticker", "qty", "est_price"}, ...] — 총액은 cash_usd 를 넘지 않는다.
    """
    if abs(sum(targets.values()) - 1.0) > 1e-9:
        raise ValueError(f"목표비중 합이 1.0 이 아니다: {sum(targets.values())}")
    for t in targets:
        # 가격을 못 받아온 자산을 조용히 건너뛰면 그 자산만 영원히 안 사진다.
        if prices.get(t, 0) <= 0:
            raise ValueError(f"{t} 가격이 없다: {prices.get(t)!r}")

    values = {t: holdings.get(t, 0) * prices[t] for t in targets}
    total = sum(values.values()) + cash_usd
    gaps = {t: total * w - values[t] for t, w in targets.items()}

    orders = []
    remaining = cash_usd
    for t in sorted(gaps, key=lambda k: -gaps[k]):
        qty = int(min(gaps[t], remaining) // prices[t])  # 음수 부족분이면 0 이하
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
