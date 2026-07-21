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


def load_analyst_weights(regime: str):
    """
    ic_weights.json의 regime_weights[regime]을 방향성 3인 가중치로 집계.

    반환: {"차트+파동+모멘텀": w, "퀀트+재무": w, "ICT+CRT": w} (합계 1.0)
    파일 없음 · 스키마 불일치 · 전 팩터 0(측정 불가)이면 None —
    호출부(app.py)는 None이면 기존 TEAM_WEIGHTS 비율로 폴백해야 한다.
    """
    try:
        with open(_IC_WEIGHT_FILE, encoding="utf-8") as f:
            data = json.load(f)
        rw = data.get("regime_weights", {}).get(regime, {})
        if not rw:
            return None
        raw = {name: sum(max(float(rw.get(f, 0.0)), 0.0) for f in factors)
               for name, factors in _FACTOR_MAP.items()}
        total = sum(raw.values())
        if total < 1e-9:
            return None
        return {k: v / total for k, v in raw.items()}
    except Exception:
        return None
