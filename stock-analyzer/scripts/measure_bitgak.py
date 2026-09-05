#!/usr/bin/env python
"""빗각 채널 — 사전 등록한 판정선으로 **처음 기대값을 본다.** (4단계~)

    python scripts/measure_bitgak.py [워커수]
    python scripts/measure_bitgak.py selftest
    python scripts/measure_bitgak.py largecap [--shapes both] [--fill gap]
                                              [--liq 5e6] [--entry nextopen]

사전 등록: `docs/superpowers/specs/2026-09-05-bitgak-design.md` (판정선 봉인)
1단계 명세: `docs/bitgak-spec.md` · 2단계 정찰: `docs/measurements/2026-09-04-bitgak-power.md`
7단계 사전 등록: `docs/superpowers/specs/2026-09-05-bitgak-stage7-design.md`

## 7단계 스위치 — **집계식·판정식은 한 줄도 새로 안 짰다**

    --shapes both     자유도 I — 돌파 후 안착 + 지지 확인 (기본은 돌파만 = 6단계)
    --fill   gap      자유도 J — 손절 min(Open,s) · 익절 max(Open,tg)
    --entry  nextopen 참고 F — 진입을 다음 거래일 시가로
    --liq    5e6      유동성 컷 — 진입일 직전 20일 중위 거래대금 ≥ · 종가 ≥ $5
    --tol    0.00149  **판정 아님** — 종가 문턱을 δ 만큼 내려 "15:50 엔 참이었을"
                      봉까지 센다. 사전 등록 §5.4 의 반대 방향 보고용

앞의 셋은 `pilot_bitgak_power.scan()` 인자로 그대로 내려가고, `--liq` 는 구성종목
마스크와 **같은 자리**에서 진입일만 거른다. 아무 스위치도 안 주면 6단계와 글자
그대로 같은 경로다 — 그게 참고 C(재현) 행이다.

**스위치는 반드시 캐시 키에 들어간다**(`_cache_key`). 6단계까지 키가 시장 이름
하나였을 때, 규칙을 바꾸고 돌려도 옛 스캔이 그대로 되돌아왔다 — 실패가 아니라
**성공처럼 보이는 정지**다(PR #218).

## 이 파일이 하는 일 — 집계뿐이다

규칙 코드는 **하나도 다시 안 짠다.** `pilot_bitgak_power.scan()` 을 그대로
import 해서 두 팔을 돌린다(사전 등록 2절). 재구현하면 정찰과 다른 물건을 재게
된다 — 정찰의 발동 12,962 / 1,729 가 여기서 그대로 나와야 한다.

    진짜   scan(df)            — 명세 3.3 의 3점(①역사적 고/저 ②추세변곡 ③매물대 돌파)
    위약   scan(df, seed=crc32(tk))  — 같은 스윙 풀에서 무작위 3점

다른 건 3점 선택 하나뿐이다. 같은 사다리·같은 진입/손절/익절·같은 보유창 40봉.

## 판정선 (봉인 — 여기서 안 고친다)

    0. 게이트  짝지은 종목 부트스트랩 MDE ≥ 0.14R → 그 시장은 「미측정」
    ① 크기    비용 차감 후 진짜 팔 평균 R 의 95% 하한 > 0
    ② 구성    평균R(진짜) − 평균R(위약) ≥ 실측 MDE  그리고  차이의 95% 하한 > 0
    ③ 안정    연도를 하나씩 빼도 ①② 가 전부 유지 (아니면 「미측정」)

①만 통과하고 ②가 미달이면 통과가 아니다 — 그건 사다리 돌파 추종의 성과다.

## 사전 등록이 안 정한 자리 — 여기서 못박는다 (결과 보기 전)

  G. **타임아웃을 뺀 평균이 아니라 넣은 평균으로 판정한다.** 이 저장소는
     2026-08-23 에 timeout 을 0R 로 세던 것을 고쳐 **실제 청산 R** 로 세기로
     했다(`docs/measurements/2026-08-23-trade-plan-oos.md`). 여기서도 timeout
     트레이드는 보유 상한 봉의 종가 R 을 가지고 있으므로 빼면 9% 를 무작위가
     아닌 방식으로 버린다. 결판만으로 낸 값도 **같이 보고**하되, 판정은 전체로
     한다. (사전 등록 §2 표가 두 숫자를 다 적어 둔 자리다.)
  H. **비용은 트레이드마다 고정 R 을 뺀다** — 사전 등록 §4 표의 0.020R /
     0.025R 그대로다. 채널 폭이 좁은 트레이드는 실제로는 비용이 더 크므로
     `비용%/채널폭%` 로 트레이드마다 다르게 빼는 값도 **참고로 같이 찍는다.**
     판정은 봉인된 고정값으로 한다.

## 못 재는 것 — 사전 등록 7절 그대로

날짜 군집 안 잡음(MDE 는 하한) · 위약이 진짜와 겹침(겹침률을 실제로 잰다) ·
크립토 군집 12개 · 두 시장 각각 양측 5%(주 판정은 미국주식) · 크립토 생존자
편향 · 크립토 30bp 는 가정 · 저장 패널 위 · 손절 r 이 −1.00 이 아님.

네트워크 無 — 저장 패널만 읽는다(크립토 패널이 없으면 정찰 스크립트가 받는다).
"""
from __future__ import annotations

import json
import os
import sys
import zlib
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from pilot_bitgak_power import (  # noqa: E402  — 규칙은 정찰 것을 그대로 쓴다
    LARGECAP_PANEL, LARGECAP_START, LARGECAP_UNIVERSE, MIN_LEN, US_PANEL,
    _ohlcv, crypto_panel, member_masks, scan,
)

REPS, SEED = 2000, 20260905     # 사전 등록 3.1
CACHE = Path("data/bitgak_arms.json")  # 스캔 결과만 캐시 — 집계는 매번 다시 한다.
                                       # 평문 JSON 이다(pickle 아님): 이 파일을 읽는 건
                                       # 코드 실행이 아니라 숫자 읽기여야 한다.
                                       # `data/` 는 gitignore — 지우면 그냥 다시 스캔한다
GATE = 0.14                     # 트레이드 기하학 OOS — 이 자보다 무디면 판정 안 한다
COST_R = {"us": 0.020, "crypto": 0.025, "largecap": 0.019}   # 사전 등록 §4 — 봉인
COST_PCT = {"us": 0.0012, "crypto": 0.0030, "largecap": 0.0012}  # 왕복 12bp / 30bp

# 5단계 참고 행 (사전 등록 §6) — 판정이 아니라 해석용. 진입일로만 자른다.
SPAN_A = ("2020-03-01", "2026-12-31")   # 유니버스 고정, 창 효과
SPAN_C = ("2017-09-01", "2020-02-29")   # 이번에 새로 생긴 구간만
DROP_3B = (2024, 2025, 2026)            # ③b — 최근 3년을 통째로


# ── 두 팔 ──────────────────────────────────────────────────────
def _liq_mask(df: pd.DataFrame, liq: float, min_px: float = 5.0) -> np.ndarray:
    """유동성 컷 (7단계 사전 등록 §6) — 진입일 **직전** 20거래일 중위 거래대금.

    `shift(1)` 이 그 "직전"이다: 진입일 자신의 거래량을 넣으면 그날 뭐가 터졌는지
    보고 들어가는 게 된다. 거래대금은 조정가 기준이라 당시 명목보다 **작게**
    나오는 쪽이고(사전 등록 §9), 그 방향이면 컷이 보수적으로 틀린다.

    구성종목 마스크와 같은 자리에서 **진입일만** 거른다 — 규칙 A~H 는 안 건드린다.
    """
    dv = (df["Close"] * df["Volume"]).rolling(20).median().shift(1).values
    return (dv >= liq) & (df["Close"].values >= min_px)


def _job(args):
    tk, df, mask, opts = args
    seed = zlib.crc32(tk.encode()) % 10 ** 6      # 정찰과 같은 씨앗
    liq = float(opts.pop("liq", 0.0) or 0.0)      # 컷은 스캔 인자가 아니다 — 진입일 필터
    if liq > 0:
        lm = _liq_mask(df, liq)
        mask = lm if mask is None else (mask & lm)
    out = {}
    for arm, s in (("real", None), ("placebo", seed)):
        out[arm] = [(int(df.index[t["idx"]].year), int(df.index[t["idx"]].toordinal()),
                     float(t["r"]), float(t["risk_pct"]), t["outcome"],
                     t["shape"], float(t["r_ideal"]), float(t["margin"]))
                    for t in scan(df, seed=s, **opts) if mask is None or mask[t["idx"]]]
    return tk, out


# ── 집계 ───────────────────────────────────────────────────────
def _net(rows, mkt, per_trade=False, resolved_only=False, drop_year=None, span=None):
    """비용 차감 R. `per_trade` 면 채널 폭 대비로 빼는 참고값(자유도 H).

    `drop_year` 는 한 해(③a) 또는 여러 해(③b). `span` 은 (시작, 끝) 날짜 —
    **진입일**로만 자른다(참고 A/C). 규칙은 안 건드린다.
    """
    drop = {drop_year} if isinstance(drop_year, int) else set(drop_year or ())
    lo_o, hi_o = ((pd.Timestamp(span[0]).toordinal(), pd.Timestamp(span[1]).toordinal())
                  if span else (None, None))
    out = []
    for year, _day, r, risk_pct, outcome, *_rest in rows:
        if year in drop:
            continue
        if lo_o is not None and not (lo_o <= _day <= hi_o):
            continue
        if resolved_only and outcome not in ("win", "loss"):
            continue
        out.append(r - (COST_PCT[mkt] / risk_pct if per_trade else COST_R[mkt]))
    return np.asarray(out, float)


def _by_tk(arm, mkt, **kw):
    d = {tk: _net(rows, mkt, **kw) for tk, rows in arm.items()}
    return {k: v for k, v in d.items() if v.size}


def _mean_of(arrs):
    if not arrs:
        return float("nan")
    c = np.concatenate(arrs)
    return float(c.mean()) if c.size else float("nan")


def _boot(real, plac, tickers, reps=REPS, seed=SEED):
    """짝지은 종목 부트스트랩 — 두 팔이 **같은 재표본**을 받는다.

    사전 등록 3.1: 정찰의 MDE 는 두 팔을 독립으로 본 값이었다. 같은 종목이
    양쪽에 다 있으므로 짝지으면 공유 구조가 상쇄되고 값이 달라진다.
    반환: (진짜 평균 95% 하한, 차이 SE, 차이 95% 하한).
    """
    rng = np.random.default_rng(seed)
    keys = list(tickers)
    n = len(keys)
    ms, ds = [], []
    for _ in range(reps):
        pick = [keys[j] for j in rng.integers(n, size=n)]
        a = _mean_of([real[tk] for tk in pick if tk in real])
        b = _mean_of([plac[tk] for tk in pick if tk in plac])
        if a == a:
            ms.append(a)
            if b == b:
                ds.append(a - b)
    if len(ms) < 2 or len(ds) < 2:
        return float("nan"), float("nan"), float("nan")
    return (float(np.percentile(ms, 2.5)), float(np.std(ds, ddof=1)),
            float(np.percentile(ds, 2.5)))


def _verdict(real_arm, plac_arm, tickers, mkt, mde, **kw):
    """①② 를 한 번 매긴다. `mde` 가 None 이면 이 표본에서 새로 잰다(게이트용)."""
    R, P = _by_tk(real_arm, mkt, **kw), _by_tk(plac_arm, mkt, **kw)
    m_real, m_plac = _mean_of(list(R.values())), _mean_of(list(P.values()))
    lo_real, se_d, lo_d = _boot(R, P, tickers)
    use = 2.8 * se_d if mde is None else mde
    diff = m_real - m_plac
    return {"mean": m_real, "plac": m_plac, "diff": diff, "mde": use,
            "lo_real": lo_real, "lo_diff": lo_d,
            "n": sum(v.size for v in R.values()),
            "n_p": sum(v.size for v in P.values()),
            "c1": lo_real > 0, "c2": diff >= use and lo_d > 0}


# ── 출력 ───────────────────────────────────────────────────────
def _ox(b):
    return "O" if b else "X"


def _overlap(real_arm, plac_arm) -> float:
    """두 팔의 진입이 같은 종목·같은 날인 비율 (사전 등록 7절 — 재서 보고한다)."""
    hit = tot = 0
    for tk, rows in real_arm.items():
        p = {d for _y, d, *_ in plac_arm.get(tk, [])}
        for _y, day, *_ in rows:
            tot += 1
            hit += day in p
    return hit / tot if tot else float("nan")


def _cache_key(mkt: str, **opts) -> str:
    """캐시 키 — 스캔 결과를 바꾸는 인자를 **전부** 넣는다.

    키가 시장 이름 하나였을 때(6단계까지), 규칙이나 체결 규약을 바꾸고 돌려도
    옛 스캔이 그대로 되돌아왔다. 실패가 아니라 **성공처럼 보이는 정지**다 —
    새 규칙을 안 재고 "쟀다"는 숫자가 나온다.
    **`scan()` 에 인자를 새로 열면 반드시 여기에 더한다.**
    """
    return "|".join([mkt] + [f"{k}={opts[k]}" for k in sorted(opts)])


def market(panel: pd.DataFrame, label: str, mkt: str, workers: int,
           universe: Path = None, start=None, **opts) -> None:
    """`universe` 가 있으면 5단계 — 진입일이 그 달의 구성종목일 때만 센다.

    스캔은 전 이력 위에서 돈다(매물대 252봉 예열). 거르는 건 진입일뿐이고
    규칙 A~F 는 손도 안 댄다 (사전 등록 §1, `pilot_bitgak_power.member_masks`).
    """
    frames = {tk: d for tk in sorted({t for _, t in panel.columns})
              if len(d := _ohlcv(panel, tk)) >= MIN_LEN}
    masks = ({} if universe is None
             else member_masks(universe, {t: d.index for t, d in frames.items()}, start))
    tasks = [(tk, d, None if universe is None else masks[tk], dict(opts))
             for tk, d in frames.items() if universe is None or tk in masks]
    if universe is None:
        span = (min(d.index[0] for _, d, _, _ in tasks).date(),
                max(d.index[-1] for _, d, _, _ in tasks).date())
    else:   # 노출은 이력 전체가 아니라 **구성종목이었던 날**만
        span = (min(d.index[m][0] for _, d, m, _ in tasks).date(),
                max(d.index[m][-1] for _, d, m, _ in tasks).date())

    key = _cache_key(mkt, **opts)
    cached = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    if key in cached:
        real_arm, plac_arm = cached[key]
        print(f"  (캐시 {CACHE} 에서 읽음 — 지우면 다시 스캔한다)", flush=True)
    else:
        real_arm, plac_arm = {}, {}
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for i, (tk, got) in enumerate(pool.map(_job, tasks), 1):
                if got["real"]:
                    real_arm[tk] = got["real"]
                if got["placebo"]:
                    plac_arm[tk] = got["placebo"]
                if i % 50 == 0 or i == len(tasks):
                    print(f"  ..{i}/{len(tasks)}종목", flush=True)
        cached[key] = (real_arm, plac_arm)
        CACHE.write_text(json.dumps(cached), encoding="utf-8")
    tickers = sorted(set(real_arm) | set(plac_arm))

    print(f"\n### {label} — {len(tasks)}종목 · {span[0]}~{span[1]}")
    for name, arm in (("진짜", real_arm), ("위약", plac_arm)):
        flat = [t for rows in arm.values() for t in rows]
        res = [t for t in flat if t[4] in ("win", "loss")]
        win = sum(t[4] == "win" for t in flat)
        print(f"  {name}  발동 {len(flat):6d} (결판 {len(res)}, 타임아웃 "
              f"{len(flat) - len(res)}) · 승률 {win / max(len(res), 1) * 100:4.1f}% · "
              f"총액 {np.mean([t[2] for t in flat]):+.3f}R")

    # 0. 게이트 — 이 표본에서 MDE 를 실측한다 (사전 등록 3.1)
    v = _verdict(real_arm, plac_arm, tickers, mkt, None)
    mde = v["mde"]
    ok_gate = mde < GATE
    print(f"\n  0. 게이트  실측 MDE {mde:.3f}R  (< {GATE}R ?) → {_ox(ok_gate)}"
          f"   {'' if ok_gate else '→ 「미측정」 — 아래 숫자는 판정이 아니다'}")
    print(f"  ① 크기   비용차감 평균 {v['mean']:+.3f}R  95% 하한 "
          f"{v['lo_real']:+.3f}R → {_ox(v['c1'])}   n={v['n']}")
    print(f"  ② 구성   진짜 {v['mean']:+.3f}R − 위약 {v['plac']:+.3f}R = "
          f"{v['diff']:+.3f}R  (MDE {mde:.3f}R, 95% 하한 {v['lo_diff']:+.3f}R) "
          f"→ {_ox(v['c2'])}   n_위약={v['n_p']}")

    # ③ leave-one-year-out — 게이트에서 잰 MDE 를 그대로 쓴다
    years = sorted({y for rows in real_arm.values() for y, *_ in rows})
    loyo = {}
    for y in years:
        w = _verdict(real_arm, plac_arm, tickers, mkt, mde, drop_year=y)
        loyo[y] = w
    ok3a = all(w["c1"] and w["c2"] for w in loyo.values())
    ok3 = ok3a
    print(f"  ③{'a' if universe is not None else ''} 안정   그 해를 빼고 다시 → {_ox(ok3a)}")
    for y, w in loyo.items():
        # 소수 3자리로는 ② 의 경계(차이 vs MDE)가 같은 값으로 보인다 — 4자리로 찍는다.
        print(f"       −{y}  ①{_ox(w['c1'])}(하한 {w['lo_real']:+.4f}R) "
              f"②{_ox(w['c2'])}(차이 {w['diff']:+.4f}R vs MDE {mde:.4f}R, "
              f"하한 {w['lo_diff']:+.4f}R)")

    if universe is not None:      # ③b — 최근 3년을 통째로 (사전 등록 5.2)
        b = _verdict(real_arm, plac_arm, tickers, mkt, mde, drop_year=DROP_3B)
        ok3b = b["c1"] and b["c2"]
        ok3 = ok3a and ok3b
        print(f"  ③b 안정  {'·'.join(map(str, DROP_3B))} 를 통째로 빼고 → {_ox(ok3b)}"
              f"   ①{_ox(b['c1'])}(하한 {b['lo_real']:+.4f}R) "
              f"②{_ox(b['c2'])}(차이 {b['diff']:+.4f}R vs MDE {mde:.4f}R, "
              f"하한 {b['lo_diff']:+.4f}R)   n={b['n']}")

    # 분기 순서가 결론을 바꾼다 — 사전 등록 3.3 의 네 갈래를 그 순서로 읽는다.
    # ③ 은 "①② 가 통과했을 때 그게 한 해가 만든 값인가"를 묻는 조건이다. 전
    # 표본에서 ① 이 이미 미달이면 ③ 은 자동으로 X 가 되므로, ③ 을 먼저 보면
    # **① 미달이라는 결론이 「미측정」에 잡아먹힌다.** 그래서 전 표본 ①② 를 먼저.
    if not ok_gate:
        note = "게이트 걸림 → 「미측정」 — 실패가 아니다"
    elif not v["c1"]:
        note = "① 미달 — 비용 차감 후 양수가 아니다. 노선을 닫는다"
    elif not v["c2"]:
        note = "② 미달 — 신호는 3점 선택이 아니라 사다리 기하학에 있었다. 노선을 닫는다"
    elif not ok3:
        note = "③ 걸림 → 「미측정」 — 한 해를 빼면 무너진다. 실패가 아니다"
    else:
        note = "①②③ 통과 — 빗각 작도가 무작위 작도보다 낫다 (실전 투입은 별도 사전 등록)"
    print(f"  ▶ 판정: {note}")

    # 부수 보고 — 판정선은 이 숫자들을 보고 안 고친다
    r2 = _verdict(real_arm, plac_arm, tickers, mkt, mde, resolved_only=True)
    r3 = _verdict(real_arm, plac_arm, tickers, mkt, mde, per_trade=True)
    print(f"\n  참고 결판만    진짜 {r2['mean']:+.3f}R  차이 {r2['diff']:+.3f}R  "
          f"①{_ox(r2['c1'])}②{_ox(r2['c2'])}   (자유도 G — 판정은 위 전체 표본)")
    print(f"  참고 비용/폭   진짜 {r3['mean']:+.3f}R  차이 {r3['diff']:+.3f}R  "
          f"①{_ox(r3['c1'])}②{_ox(r3['c2'])}   (자유도 H — 판정은 고정 "
          f"{COST_R[mkt]}R)")
    if universe is not None:      # 사전 등록 §6 — 판정이 아니라 해석용
        for name, sp in (("A 창 2020-03~", SPAN_A), ("C 창 ~2020-02", SPAN_C)):
            w = _verdict(real_arm, plac_arm, tickers, mkt, mde, span=sp)
            print(f"  참고 {name}  진짜 {w['mean']:+.3f}R  위약 {w['plac']:+.3f}R  "
                  f"차이 {w['diff']:+.3f}R (하한 {w['lo_diff']:+.4f}R)  "
                  f"①{_ox(w['c1'])}②{_ox(w['c2'])}   n={w['n']}")
    print(f"  겹침          두 팔 진입이 같은 종목·같은 날 {_overlap(real_arm, plac_arm) * 100:.1f}%"
          f"   (위약이 닮은 만큼 MDE 는 하한이다)")

    # 채널 한 칸 중위 — 여유 bp 환산의 분모다(사전 등록 §4: 6.16% 를 재활용하지
    # 않고 **이 행에서 다시 잰 값**으로 나눈다).
    flat = [t for rows in real_arm.values() for t in rows]
    if flat:
        med = float(np.median([t[3] for t in flat]))
        print(f"  채널 한 칸    중위 {med * 100:.2f}% (주가 대비) → "
              f"1bp = {0.0001 / med:.6f}R   (1R = {med * 10000:.1f}bp)")
        # 형태별 분해 — **판정 아님**(사전 등록 §3.1). 발동 수만 여기서 센다.
        shp = defaultdict(int)
        for t in flat:
            shp[t[5]] += 1
        if len(shp) > 1 or "breakout" not in shp:
            print("  형태별 발동   " + " · ".join(
                f"{k} {v}({v / len(flat) * 100:.1f}%)" for k, v in sorted(shp.items())))
        # 갭이 R 을 얼마나 먹었나 — 같은 트레이드의 두 청산가를 짝지어 뺀다.
        for name, oc in (("손절", "loss"), ("익절", "win")):
            g = [t[2] - t[6] for t in flat if t[4] == oc]
            if g:
                print(f"  갭 {name}      평균 {np.mean(g):+.4f}R  (n={len(g)}, "
                      f"갭 문 트레이드 {sum(abs(x) > 1e-12 for x in g) / len(g) * 100:.1f}%)")
        bad = [t for t in flat if t[4] == "loss"]
        if bad:
            print(f"  손절 −1R 초과 {sum(t[2] < -1.0 for t in bad) / len(bad) * 100:.1f}% "
                  f"(6단계 식으로는 {sum(t[6] < -1.0 for t in bad) / len(bad) * 100:.1f}%)")

    print("  연도별 (그 해만)")
    per = defaultdict(lambda: [[], []])
    for k, arm in ((0, real_arm), (1, plac_arm)):
        for rows in arm.values():
            for year, _d, r, risk, *_rest in rows:
                per[year][k].append(r - COST_R[mkt])
    for y in years:
        a, b = per[y]
        print(f"    {y}  진짜 {np.mean(a):+.3f}R (n={len(a):5d}) · "
              f"위약 {np.mean(b):+.3f}R (n={len(b):5d}) · 차이 "
              f"{np.mean(a) - np.mean(b):+.3f}R")


SHAPES = {"breakout": ("breakout",), "support": ("support",),
          "both": ("breakout", "support")}


def _opts(argv: list[str]) -> tuple[dict, list[str]]:
    """7단계 스위치를 판다 — `--shapes both --fill gap --liq 5e6 --entry nextopen`.

    **기본값은 6단계 경로다.** 아무 스위치도 안 주면 `scan()` 기본 인자가 그대로
    가고, 캐시 키도 옛 키와 같아야 한다(`_cache_key` 는 기본값을 안 넣는다).
    """
    opts, rest, i = {}, [], 0
    while i < len(argv):
        a = argv[i]
        if a in ("--shapes", "--fill", "--entry", "--liq", "--tol"):
            v = argv[i + 1]
            i += 2
            if a == "--shapes":
                opts["shapes"] = SHAPES[v]
            elif a in ("--liq", "--tol"):
                opts[a[2:]] = float(v)
            else:
                opts[a[2:]] = v
            continue
        rest.append(a)
        i += 1
    # 기본값과 같은 값은 키에서 뺀다 — `--shapes breakout` 이 6단계 캐시를 그대로 탄다.
    for k, default in (("shapes", ("breakout",)), ("fill", "ideal"),
                       ("entry", "close"), ("liq", 0.0), ("tol", 0.0)):
        if opts.get(k) == default:
            opts.pop(k)
    return opts, rest


def run() -> None:
    opts, argv = _opts(sys.argv[1:])
    args = [a for a in argv if a != "largecap"]
    workers = int(args[0]) if args else max((os.cpu_count() or 2) - 1, 1)
    print(f"워커 {workers} · 부트스트랩 {REPS}회 시드 {SEED} · 게이트 {GATE}R", flush=True)
    if opts:
        print(f"7단계 스위치 {opts}  (캐시 키 {_cache_key('largecap', **opts)})", flush=True)
    if "largecap" in argv:              # 5단계 — 편향 없는 PIT 대형주 패널
        market(pd.read_parquet(LARGECAP_PANEL),
               "미국주식 (PIT 대형주) — 판정", "largecap", workers,
               universe=LARGECAP_UNIVERSE, start=LARGECAP_START, **opts)
        return
    market(pd.read_parquet(US_PANEL), "미국주식 (대형주 패널) — 주 판정", "us", workers)
    market(crypto_panel(), "크립토 (yfinance 일봉) — 부 판정", "crypto", workers)
    print("\n다중검정: 두 시장에 각각 양측 5% 를 썼다. 주 판정은 미국주식이다.")
    print("표는 손으로 docs/measurements/ 에 옮긴다 — 이 스크립트는 문서를 쓰지 않는다.")


# ── selftest ───────────────────────────────────────────────────
def selftest() -> None:
    rng = np.random.default_rng(0)
    mk = lambda vals, y=2020: [(y, 100 + i, float(v), 0.06, "win") for i, v in enumerate(vals)]

    # ① 비용은 트레이드마다 고정 R 을 뺀다 — 평균이 정확히 그만큼 내려간다.
    rows = mk(rng.normal(0, 1, 50))
    assert abs(_net(rows, "us").mean() - (np.mean([r[2] for r in rows]) - 0.020)) < 1e-12
    # 참고값(자유도 H): 폭 6% 에 왕복 12bp → 0.12/6 = 0.02R 로 같아야 한다.
    assert abs(_net(rows, "us", per_trade=True).mean()
               - _net(rows, "us").mean()) < 1e-12

    # ② 두 팔이 완전히 같으면 차이는 0, 짝지은 SE 도 0 — 짝짓기가 실제로 물렸나.
    arm = {f"T{i}": mk(rng.normal(0.1, 1, 30)) for i in range(20)}
    tks = sorted(arm)
    v = _verdict(arm, arm, tks, "us", None)
    assert abs(v["diff"]) < 1e-12 and v["mde"] < 1e-9, v
    assert not v["c2"], "같은 팔인데 ②가 통과했다"

    # ③ 짝을 깨면(위약을 독립 표본으로) SE 가 살아난다 — ②의 자가 죽지 않았나.
    other = {f"T{i}": mk(rng.normal(0.1, 1, 30)) for i in range(20)}
    w = _verdict(arm, other, tks, "us", None)
    assert w["mde"] > 0.01, w

    # ④ LOYO 가 정말 그 해를 뺀다.
    two = {"T0": mk([1.0, 1.0], 2020) + mk([-1.0, -1.0, -1.0], 2021)}
    assert _net(two["T0"], "us", drop_year=2021).size == 2
    assert abs(_verdict(two, two, ["T0"], "us", 0.0, drop_year=2021)["mean"]
               - (1.0 - 0.020)) < 1e-12

    # ⑤ timeout 을 빼는 자유도 G 가 실제로 표본을 줄인다.
    mixed = [(2020, 1, 1.0, 0.06, "win"), (2020, 2, 0.3, 0.06, "timeout")]
    assert _net(mixed, "us").size == 2 and _net(mixed, "us", resolved_only=True).size == 1

    # ⑥ 겹침률 — 같은 종목·같은 날만 센다.
    a = {"T0": [(2020, 5, 0.0, 0.06, "win"), (2020, 6, 0.0, 0.06, "win")]}
    b = {"T0": [(2020, 6, 0.0, 0.06, "win")]}
    assert abs(_overlap(a, b) - 0.5) < 1e-12

    # ⑦ ③b — 여러 해를 한꺼번에 뺀다 (③a 의 한 해 빼기와 같은 자리)
    many = {"T0": mk([1.0], 2023) + mk([-1.0], 2024) + mk([-1.0], 2025)}
    assert _net(many["T0"], "us", drop_year=(2024, 2025)).size == 1
    assert _net(many["T0"], "us", drop_year=2024).size == 2

    # ⑧ 참고 행 A/C — 진입일로만 자른다. 경계는 양끝 포함.
    o = lambda d: pd.Timestamp(d).toordinal()
    days = [(2019, o("2019-06-01"), 1.0, 0.06, "win"),
            (2020, o("2020-03-01"), 1.0, 0.06, "win"),
            (2021, o("2021-06-01"), 1.0, 0.06, "win")]
    assert _net(days, "us", span=SPAN_C).size == 1        # 2019 만
    assert _net(days, "us", span=SPAN_A).size == 2        # 2020-03-01 포함

    # ⑨ 캐시 키 — 스캔을 바꾸는 인자가 키를 바꿔야 한다.
    assert _cache_key("largecap") == "largecap"
    assert _cache_key("largecap", shapes="both") != _cache_key("largecap")
    assert _cache_key("largecap", fill="gap") != _cache_key("largecap", fill="ideal")
    assert _cache_key("largecap", a=1, b=2) == _cache_key("largecap", b=2, a=1)

    # ── 7단계 (사전 등록 §11) — 새 자유도 I·J 와 유동성 컷 ────────────
    from pilot_bitgak_power import _synth, fill_px, shape_at

    df = _synth()
    cl, op = df["Close"].values, df["Open"].values
    BOTH = ("breakout", "support")

    # ⑩ 기본값 조합이 6단계 경로와 **글자 그대로** 같다.
    assert scan(df) == scan(df, shapes=("breakout",), fill="ideal", entry="close")

    # ⑪ I-a·I-b 는 상호 배타다 — 한 봉·한 선에서 둘 다 참일 수 없다.
    #    `shape_at` 이 답을 하나만 내므로 격자로 두 조건을 **따로** 세서 확인한다.
    grid = (-1.0, 0.0, 1.0)
    seen = set()
    for pc in grid:
        for c in grid:
            for lw in grid:
                for lp in grid:
                    a = c > 0.0 and pc <= lp                    # I-a
                    b = c > 0.0 and pc > lp and lw <= 0.0       # I-b
                    assert not (a and b), (pc, c, lw, lp)
                    got = shape_at(pc, c, lw, lp, 0.0)
                    assert got == ("breakout" if a else "support" if b else None)
                    seen.add(got)
    assert seen == {"breakout", "support", None}, seen

    # ⑫ 선 위에서 저가만 선을 찍고 종가가 위 → **I-b 만**.
    assert shape_at(105.0, 104.0, 99.0, 100.0, 100.0) == "support"
    assert shape_at(105.0, 104.0, 101.0, 100.0, 100.0) is None   # 안 눌렸다
    assert shape_at(99.0, 104.0, 99.0, 100.0, 100.0) == "breakout"

    # ⑬ 체결 규약은 **진입 결정을 안 바꾼다** — 트레이드 집합이 그대로여야 한다.
    ideal, gap = scan(df, shapes=BOTH), scan(df, shapes=BOTH, fill="gap")
    key = lambda ts: [(t["idx"], t["exit"], t["outcome"], t["r_ideal"]) for t in ts]
    assert key(ideal) == key(gap) and gap

    nogap = worse = better = deep = 0
    for t in gap:
        px = float(cl[t["idx"]])
        risk = t["risk_pct"] * px
        lvl = px + t["r_ideal"] * risk          # 청산선을 R 에서 되짚는다
        if t["outcome"] == "timeout":
            assert t["r"] == t["r_ideal"]       # MOC — 갭이 없다
            continue
        want = fill_px(t["outcome"], float(op[t["exit"]]), lvl, "gap")
        assert abs((want - px) / risk - t["r"]) < 1e-9, t
        if abs(t["r"] - t["r_ideal"]) < 1e-12:
            nogap += 1                          # ④ 갭이 없으면 기본과 일치
        elif t["outcome"] == "loss":
            # ⑤ 손절선 아래로 갭다운 → 이상적 손절보다 **나쁘다**.
            #    선이 기울어져 있어 이상적 손절도 정확히 −1 이 아니다 — 그래서
            #    문턱은 −1 이 아니라 짝지은 `r_ideal` 이다(6단계와 같은 이유).
            worse += 1
            assert t["r"] < t["r_ideal"], t
            deep += t["r"] < -1.0
        else:
            # ⑥ 익절선 위로 갭업 → 이상적 익절보다 **좋다** (부호 실수 방지)
            better += 1
            assert t["r"] > t["r_ideal"], t
    assert nogap and worse and better and deep, (nogap, worse, better, deep)

    # ⑭ 유동성 컷 — 0 이면 컷 없음과 같고, 무한대면 0건.
    mk = lambda liq: _job(("T", df, None, {"shapes": BOTH, "liq": liq}))[1]["real"]
    assert mk(0.0) == mk(None) and len(mk(0.0)) == len(gap)
    assert mk(float("inf")) == []
    lm = _liq_mask(df, 0.0, min_px=0.0)
    assert not lm[:20].any() and lm[20:].all()   # 20봉 예열 전은 전부 False

    print("selftest OK — 비용·짝짓기·LOYO·타임아웃·겹침·③b·창·캐시키 8종"
          f" + 7단계 5종 (갭 없음 {nogap} · 손절갭 {worse}/{deep}건 −1R 초과 · "
          f"익절갭 {better})")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        selftest()
    else:
        run()
