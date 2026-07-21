"""
실물 경제지표 모듈 (FRED)
=================================================================
CPI(인플레이션)·실업률·산업생산·수익률곡선 등 실제 경제 데이터를 FRED에서
수집해 '실물경제 건전성' 점수(0~100, 높을수록 양호)를 산출.

기존 macro_score는 전부 시장가격 프록시(금리·VIX·환율 등)라 CPI/고용 같은
실물 지표를 못 봤다 — 이 모듈이 그 공백을 메운다.

환경변수:
  FRED_API_KEY — https://fredaccount.stlouisfed.org/apikeys 에서 무료 발급

키가 없거나 요청 실패 시 {'available': False}를 반환 → 호출부는 조용히
기존 동작(시장 프록시 macro_score만)으로 폴백한다.
"""
import os
from datetime import datetime, timedelta

import requests

_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# 사용 FRED 시리즈 (무료·라이선스 제약 없음; ISM PMI는 독점이라 산업생산으로 대체)
_SERIES = {
    "cpi":       "CPIAUCSL",   # 소비자물가지수 (전년比 인플레)
    "core_cpi":  "CPILFESL",   # 근원 CPI
    "unemp":     "UNRATE",     # 실업률
    "indpro":    "INDPRO",     # 산업생산지수 (PMI 대용)
    "payems":    "PAYEMS",     # 비농업 고용
    "yc":        "T10Y2Y",     # 10Y-2Y 수익률곡선 스프레드
}


def _fred_key() -> str:
    return os.environ.get("FRED_API_KEY", "")


def _fetch_series(series_id: str, api_key: str, start: str) -> list:
    """FRED 관측치 리스트 반환: [{'date','value'}...] (값은 float, 결측 '.' 제외)."""
    resp = requests.get(_FRED_BASE, params={
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start,
    }, timeout=20)
    resp.raise_for_status()
    obs = resp.json().get("observations", [])
    out = []
    for o in obs:
        v = o.get("value", ".")
        if v not in (".", "", None):
            try:
                out.append({"date": o["date"], "value": float(v)})
            except (ValueError, KeyError):
                continue
    return out


def _yoy_pct(obs: list) -> float:
    """월간 시리즈의 전년동월比 변화율(%). 데이터 부족 시 None."""
    if len(obs) < 13:
        return None
    return (obs[-1]["value"] - obs[-13]["value"]) / abs(obs[-13]["value"]) * 100


def real_macro_score() -> dict:
    """
    실물경제 건전성 점수(0~100, 높을수록 양호) + 세부 지표.

    반환: {
        'available': bool,
        'score': float,           # available=False면 50.0
        'detail': {지표명: 점수},
        'data': {지표명: 원값},
        'reason': str,
    }
    """
    api_key = _fred_key()
    if not api_key:
        return {'available': False, 'score': 50.0, 'detail': {}, 'data': {},
                'reason': 'FRED_API_KEY 미설정 — 실물지표 생략'}

    start = (datetime.now() - timedelta(days=800)).strftime("%Y-%m-%d")
    det, data = {}, {}
    try:
        series = {k: _fetch_series(sid, api_key, start) for k, sid in _SERIES.items()}

        # ── 인플레이션 (30%) — 근원 CPI 전년比, 2% 근처가 이상적 ──
        core_yoy = _yoy_pct(series["core_cpi"]) or _yoy_pct(series["cpi"])
        if core_yoy is not None:
            data["근원CPI(YoY%)"] = round(core_yoy, 2)
            det["인플레이션"] = (80 if core_yoy < 2.5 else (65 if core_yoy < 3.5
                                  else (45 if core_yoy < 5 else (28 if core_yoy < 7 else 15))))
        else:
            det["인플레이션"] = 50.0

        # ── 고용/실업률 (30%) — 낮고 안정적일수록 양호, 급등은 침체 신호 ──
        unemp = series["unemp"]
        if len(unemp) >= 4:
            u_now = unemp[-1]["value"]; u_3ago = unemp[-4]["value"]
            u_chg = u_now - u_3ago
            data["실업률(%)"] = round(u_now, 2); data["실업률변화(3M)"] = round(u_chg, 2)
            lvl = 80 if u_now < 4 else (65 if u_now < 5 else (48 if u_now < 6 else 30))
            # Sahm rule 근사: 3개월 내 +0.5%p 급등은 강한 침체 신호
            trend = 20 if u_chg >= 0.5 else (40 if u_chg >= 0.2 else (60 if u_chg >= 0 else 75))
            det["고용"] = lvl * 0.6 + trend * 0.4
        else:
            det["고용"] = 50.0

        # ── 산업생산 (25%, PMI 대용) — 전년比 성장률 ──
        ind_yoy = _yoy_pct(series["indpro"])
        if ind_yoy is not None:
            data["산업생산(YoY%)"] = round(ind_yoy, 2)
            det["산업생산"] = (78 if ind_yoy > 3 else (62 if ind_yoy > 1 else (48 if ind_yoy > -1
                                else (32 if ind_yoy > -4 else 18))))
        else:
            det["산업생산"] = 50.0

        # ── 수익률곡선 (15%) — 10Y-2Y 역전은 침체 선행 ──
        yc = series["yc"]
        if yc:
            yc_now = yc[-1]["value"]; data["수익률곡선(10Y-2Y)"] = round(yc_now, 2)
            det["수익률곡선"] = (75 if yc_now > 1 else (60 if yc_now > 0 else (38 if yc_now > -0.5 else 22)))
        else:
            det["수익률곡선"] = 50.0

        score = (det["인플레이션"] * 0.30 + det["고용"] * 0.30 +
                 det["산업생산"] * 0.25 + det["수익률곡선"] * 0.15)
        return {'available': True, 'score': float(score), 'detail': det, 'data': data,
                'reason': f'실물경제 {score:.0f}점 (근원CPI·실업률·산업생산·수익률곡선)'}
    except Exception as e:
        return {'available': False, 'score': 50.0, 'detail': det, 'data': data,
                'reason': f'FRED 조회 실패: {e}'}
