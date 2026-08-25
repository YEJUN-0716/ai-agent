"""9월 1일 첫 회차 리허설 — 실전 경로를 스크래치 장부에서 끝까지 밟는다.

드라이런은 주문을 안 내서 pending → settle_pending 흐름을 못 밟는다. 여기서는
DRY_RUN=false 로 진짜 경로를 돌리되, 장부는 .tmp 스크래치이고 텔레그램은
가로챈다(공개 채널로 나가면 안 된다).

    VIRTUAL_PORTFOLIO_FILE=.tmp/rehearsal_index_portfolio.json \
    VIRTUAL_CAPITAL_KRW=0 DRY_RUN=false python .tmp/index_rehearsal.py
"""
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import index_runner as ir
from modules import virtual_broker as vb

assert vb.STATE_FILE.startswith(".tmp"), f"스크래치 장부가 아니다: {vb.STATE_FILE}"
assert not ir.DRY_RUN, "DRY_RUN=false 로 불러야 실전 경로다"
if os.path.exists(vb.STATE_FILE):
    os.remove(vb.STATE_FILE)

sent = []
ir.send_tg = lambda msg: (sent.append(msg), True)[1]      # 발송 가로채기


def show(label, res):
    st = vb.load_state()
    print(f"\n### {label} → 적립 {'O' if res['deposited'] else 'X'} · "
          f"주문 {len(res['orders'])} · 보고 {'O' if res['reported'] else 'X'}")
    print(f"    현금 {st['cash_krw']:,.0f}원 · 보유 {len(st['positions'])}종목 · "
          f"대기 {len(st['pending'])}건 · 거래 {len(st.get('trades', []))}건")
    bad = vb.check_state(st)
    print(f"    자기점검: {'정상' if not bad else bad}")
    return st


print("=" * 68)
print("1일차 — 적립·주문 (보고는 아직)")
show("2026-09-01", ir.run(now=date(2026, 9, 1)))

# 다음 거래일이 와야 체결된다. 오늘 낸 주문은 다음 봉이 아직 없으므로, 예약일을
# 지난 거래일로 되감아 '다음 날이 왔다'를 흉내낸다. 이 한 줄이 리허설의 유일한 조작.
st = vb.load_state()
rewound = (date.today() - timedelta(days=7)).isoformat()
for o in st["pending"]:
    o["placed_date"] = rewound
vb.save_state(st)
print(f"\n[조작] 대기 {len(st['pending'])}건의 예약일을 {rewound} 로 되감았다")

print("\n" + "=" * 68)
print("2일차 — 체결·보고")
show("2026-09-02", ir.run(now=date(2026, 9, 2)))

print("\n" + "=" * 68)
print("3일차 — 같은 달에 또 깨워도 아무 일 없어야 한다")
show("2026-09-03", ir.run(now=date(2026, 9, 3)))

print("\n" + "=" * 68)
print("다음 달 — 다시 적립")
show("2026-10-01", ir.run(now=date(2026, 10, 1)))

print("\n" + "=" * 68)
print(f"보고 발송 {len(sent)}건 (가로챔 — 공개 채널로 안 나갔다)")
for m in sent:
    print("-" * 68)
    print(m)
print("-" * 68)
print("\n최종 장부:")
print(json.dumps(vb.load_state(), ensure_ascii=False, indent=1)[:1500])
