"""
AI 애널리스트 팀 방향성 가중치 — ic_weights.json 팩터 ICIR 매핑
=================================================================
analyst-team-feedback-loop 설계(2026-07-22 브레인스토밍 승인) 구현.

app.py의 "AI 애널리스트 팀"은 방향성 점수를 차트+파동+모멘텀 / 퀀트+재무 /
ICT+CRT 3명의 가중 블렌드로만 계산한다(역할 분리). 매크로/백테스트/리스크는
역할이 다르므로(레짐 맥락 · 신뢰도 플래그 · Kelly 사이징) 방향성 가중치
대상이 아니다 — factor_engine의 ic_weight_updater가 매주 갱신하는
ic_weights.json의 팩터 ICIR을 재사용해 이 3명의 가중치를 정한다(새 리플레이
엔진을 만들지 않는다).

매핑:
  차트+파동+모멘텀 ← mom_3m, mom_1m, low_vol
  퀀트+재무        ← value, quality
  ICT+CRT          ← ict
"""
import json
import os

_IC_WEIGHT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ic_weights.json")

DIRECTIONAL_ANALYSTS = ("차트+파동+모멘텀", "퀀트+재무", "ICT+CRT")

_FACTOR_MAP = {
    "차트+파동+모멘텀": ("mom_3m", "mom_1m", "low_vol"),
    "퀀트+재무":        ("value", "quality"),
    "ICT+CRT":          ("ict",),
}

# 프로덕션 블록(production_weights)에서의 매핑. 실전 스캔은 12-1 모멘텀과
# 252봉 변동성으로 랭킹하므로 차트 애널리스트의 몫은 그쪽에서 와야 한다.
# ict 는 프로덕션 4팩터에 없으므로 regime_weights 에서 따로 가져온다.
_PRODUCTION_MAP = {
    "차트+파동+모멘텀": ("momentum", "low_vol"),
    "퀀트+재무":        ("value", "quality"),
}


def _sum_positive(block, factors):
    return sum(max(float(block.get(f, 0.0)), 0.0) for f in factors)


def _normalize(raw):
    total = sum(raw.values())
    if total < 1e-9:
        return None
    return {k: v / total for k, v in raw.items()}


def load_analyst_weights(regime: str):
    """
    ic_weights.json에서 방향성 3인 가중치를 집계.

    `production_weights` 를 먼저 본다. 실전 스캔이 실제로 쓰는 팩터 정의의
    IC 로 배분된 블록이다. 예전에는 `regime_weights` 만 읽었는데, 그 블록의
    mom_3m/mom_1m/low_vol 은 **실전 스캔이 계산조차 하지 않는 정의**라
    차트 애널리스트의 발언권이 엉뚱한 근거로 정해지고 있었다 (PR #22).

    ict 는 프로덕션 4팩터에 없으므로 `regime_weights` 의 ict 몫을 그대로
    가져와 합산한 뒤 정규화한다 — 안 그러면 ICT 애널리스트 몫이 0 이 된다.

    반환: {"차트+파동+모멘텀": w, "퀀트+재무": w, "ICT+CRT": w} (합계 1.0)
    파일 없음 · 스키마 불일치 · 전 팩터 0(측정 불가)이면 None —
    호출부(app.py)는 None이면 기존 TEAM_WEIGHTS 비율로 폴백해야 한다.
    """
    try:
        with open(_IC_WEIGHT_FILE, encoding="utf-8") as f:
            data = json.load(f)

        rw = data.get("regime_weights", {}).get(regime, {})
        pw = (data.get("production_weights") or {}).get(regime, {})

        if pw:
            raw = {name: _sum_positive(pw, factors)
                   for name, factors in _PRODUCTION_MAP.items()}
            raw["ICT+CRT"] = _sum_positive(rw, _FACTOR_MAP["ICT+CRT"])
            weights = _normalize(raw)
            if weights:
                return weights

        if not rw:
            return None
        return _normalize({name: _sum_positive(rw, factors)
                           for name, factors in _FACTOR_MAP.items()})
    except Exception:
        return None
