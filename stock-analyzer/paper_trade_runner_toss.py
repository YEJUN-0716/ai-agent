"""
페이퍼 트레이딩 자동 실행 스크립트 (가상 장부)
=================================================================
GitHub Actions cron으로 매일 장마감 후 자동 실행.
매수 후보는 **트레이드 플랜**(modules/trade_plan.py)이 고른다 — 진입 구간
지정가로 걸고, 손절/목표 라인으로 청산한다. 시장 레짐·낙폭 컷·킬스위치·섹터
한도는 그대로다. 체결은 modules/virtual_broker.py 가 장부에만 적는다.

⚠️ **실주문 브로커를 다시 붙이지 말 것 (2026-08-13, PR #96·#97).** 자리 경합·
현금 제약을 넣고 5.9년 굴리니 연 +5.6% 인데 같은 패널 동일가중 매수보유가
+19.5% 다. 비용 0bp 로 놓아도 +6.5% 로 진다(이긴 해 0/7).
근거: docs/measurements/2026-08-13-portfolio-vs-benchmark.md
이 전략은 접었고, 이 러너는 **공개 성적표용 가상 장부로만** 돈다. 되살리려면
트레이드 평균 R 이 아니라 **매수보유 대비 우위**를 먼저 통과시킬 것.

2026-08-10: 팩터 엔진(calc_factor_scores/generate_signals)에서 트레이드 플랜으로
교체. 팩터 경로는 OOS IC −0.0046 으로 예측력이 확인되지 않았고, 트레이드 플랜은
6.4년 OOS 에서 +0.66R(롱, 실행등급 A·B)이 나왔다. 측정된 규칙으로 장부를 돌린다.

2026-08-12 정정: **그 +0.66R 은 실행할 수 없는 진입 위에서 잰 값이다.** 실제로
걸 수 있는 지정가로 다시 재면 OOS +0.022R (p=0.26) 이다 — 마이너스는 아니지만
0 이다. 이 장부를 계속 돌리는 이유는 초과수익을 기대해서가 아니라, **측정으로는
못 얻는 실체결 기록**을 쌓기 위해서다. 상세: docs/measurements/2026-08-12-entry-rule-daily.md
설계: docs/superpowers/specs/2026-08-10-virtual-broker-trade-plan-design.md

환경변수:
  TELEGRAM_TOKEN        텔레그램 봇 토큰              (선택)
  TELEGRAM_CHAT_ID      텔레그램 채팅 ID              (선택)
  UNIVERSE              유니버스 이름                  (기본: 'S&P 500 대형 30')
  RISK_PCT_PER_TRADE    한 트레이드의 1R = 자본의 몇 % (기본: 0.155)
  MAX_POSITION_PCT      한 종목 최대 비중 %            (기본: 15)
  MAX_POSITIONS         최대 동시 포지션 수 (bull 기준) (기본: 18)
  NEUTRAL_MAX_POSITIONS neutral 레짐 최대 포지션 수    (기본: MAX_POSITIONS*0.7)
  BEAR_MAX_POSITIONS    bear 레짐 최대 포지션 수       (기본: MAX_POSITIONS*0.4)
  DRY_RUN               true면 주문 전송 안 함          (기본: false)
  TRAIL_STOP_PCT        트레일링 스톱 % (기존 팩터 보유분 마무리용, 기본: 10)
  PORTFOLIO_DD_STOP_PCT 포트폴리오 드로다운 한도 %    (기본: 15, 0이면 비활성)
  MAX_SECTOR_POSITIONS  동일 섹터 최대 보유 종목 수    (기본: 2)
"""
import json
import os
import sys
import warnings
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from modules import analyst_log
from modules.factor_engine import get_market_regime
from modules.fx import fetch_krw_per_usd
from modules.price_panel import PanelCoverageError, load_panel
from modules.trade_plan import ACTIONABLE_GRADES, build_trade_plan
from modules.virtual_broker import (
    place_market_sell  as _pm_market_sell,
    get_account        as _pt_get_account,
    get_positions      as _pt_get_positions,
)

try:
    from modules.ops_safety import KillSwitch as _KillSwitch
    _KILL_SWITCH_AVAILABLE = True
except Exception:
    _KILL_SWITCH_AVAILABLE = False

warnings.filterwarnings("ignore")

# ── 설정 ──────────────────────────────────────────────────────────────
TG_TOKEN      = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "")
UNIVERSE_NAME  = os.environ.get("UNIVERSE", "S&P 500 대형 30")
_KRW_PER_USD   = float(os.environ.get("KRW_PER_USD",  "1400"))     # 원/달러 환율 fallback
# 위험 기준 사이징. 이 시스템의 손절은 진입가에서 1.3~3% 밖에 안 떨어져 있어서
# 위험을 1% 로 잡으면 한 종목이 자본의 40% 가 된다. 15% 상한과 함께 동시
# 5~7종목이 들어간다.
#
# **0.5 → 0.31 (2026-08-16).** 이 값은 **플랜 위험** 1단위를 자본의 몇 % 로
# 잡을지다(분모가 entry_ref - stop). 그런데 실제 체결은 구간 상단이라 손절까지
# 실제 위험은 그 중앙 1.63배 · 최대 2.0배다 — 0.5 로 두면 손절 하나가 자본의
# 0.81% 였고, 10칸이면 명목 5% 가 아니라 실질 8% 였다. 0.31 × 1.63 ≈ 0.5% 다.
#
# 균등 축소라 **손익분기 bp 는 안 바뀐다**(비율에서 약분된다). 기대 손익도
# 같은 비율로 준다 — 이건 노출 다이얼이지 개선이 아니다.
RISK_PCT_PER_TRADE = float(os.environ.get("RISK_PCT_PER_TRADE", "0.155"))
MAX_POSITION_PCT   = float(os.environ.get("MAX_POSITION_PCT", "15"))
# 플랜 계산에 필요한 일봉 길이 — trade_plan.MIN_BARS(60)와 숏 레짐 게이트(70봉)를
# 모두 덮는다. 달력일 기준이라 휴장일을 감안해 넉넉히 잡는다.
PLAN_LOOKBACK_DAYS = 400
MAX_POSITIONS  = int(os.environ.get("MAX_POSITIONS", "18"))
# 레짐별 최대 포지션 수 → bear에서 노출을 줄여 하락 충격 완충
_REGIME_MAX_POS: dict[str, int] = {
    "bull":    MAX_POSITIONS,
    "neutral": int(os.environ.get("NEUTRAL_MAX_POSITIONS",
                                  str(max(1, round(MAX_POSITIONS * 0.7))))),
    "bear":    int(os.environ.get("BEAR_MAX_POSITIONS",
                                  str(max(1, round(MAX_POSITIONS * 0.4))))),
}
DRY_RUN        = os.environ.get("DRY_RUN", "false").strip().lower() == "true"
TRAIL_STOP_PCT = float(os.environ.get("TRAIL_STOP_PCT", "10"))
PORTFOLIO_DD_STOP_PCT = float(os.environ.get("PORTFOLIO_DD_STOP_PCT", "15"))
MAX_SECTOR_POSITIONS  = int(os.environ.get("MAX_SECTOR_POSITIONS", "2"))
# 킬스위치 임계치 — 전일 대비 일일손실 / 연속 브로커오류 / 단일주문 크기상한
KS_MAX_DAILY_LOSS_PCT   = float(os.environ.get("KS_MAX_DAILY_LOSS_PCT", "8"))
KS_MAX_ERRORS           = int(os.environ.get("KS_MAX_ERRORS", "3"))
KS_MAX_SINGLE_ORDER_PCT = float(os.environ.get("KS_MAX_SINGLE_ORDER_PCT", "20"))

PEAK_FILE        = "peak_prices.json"
SIGNAL_LOG_FILE  = "signal_log.json"
EQUITY_LOG_FILE  = "equity_log.json"
SIGNAL_HOLD_DAYS = 21

# ── 유니버스 ──────────────────────────────────────────────────────────
UNIVERSE_PRESETS = {
    # ── 미국 ──────────────────────────────────────────────────────────────
    "S&P 500 대형 30": [
        "AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","BRK-B","JPM","V",
        "JNJ","UNH","XOM","PG","HD","MA","ABBV","MRK","KO","PEP",
        "COST","AVGO","LLY","WMT","MCD","CRM","ADBE","CSCO","ACN","TMO",
    ],
    "NASDAQ 기술주 20": [
        "AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","AVGO","ADBE","CRM",
        "AMD","INTC","QCOM","NFLX","PYPL","INTU","AMAT","MU","LRCX","SNPS",
    ],
    "반도체 15": [
        "NVDA","AMD","INTC","TSM","ASML","QCOM","AVGO","MU","LRCX","AMAT",
        "MRVL","ON","NXPI","TXN","KLAC",
    ],
    "배당 귀족 15": [
        "JNJ","PG","KO","PEP","MMM","EMR","ABT","ADP","AFL","SHW",
        "GD","ITW","ED","WMT","MCD",
    ],
    # $100 이하 종목 다수 — 100달러 투입금으로 매수 가능성 높음
    # DFS 제거(2026-08-18): 2025년 캐피털원 합병으로 상장폐지 — 시세가 영영 안 온다.
    # 프리셋 키는 CI 워크플로가 문자열로 참조하므로 이름의 숫자는 그대로 둔다.
    "미국 금융주 15": [
        "BAC","WFC","C","USB","KEY","RF","FITB","HBAN","CFG","AXP",
        "COF","ALLY","SYF","MS",
    ],
    "미국 가치배당주 20": [
        "KO","T","VZ","PFE","BMY","GILD","MRNA","F","GM","INTC",
        "CSCO","HPQ","HPE","CVS","MO","KHC","DAL","LUV","OKE","WMB",
    ],
    "미국 AI·빅테크 15": [
        "NVDA","MSFT","AAPL","META","GOOGL","AMZN","TSLA","AVGO",
        "CRM","ADBE","AMD","NFLX","ORCL","PLTR","IBM",
    ],
    "미국 헬스케어 15": [
        "UNH","LLY","ABBV","MRK","JNJ","ABT","TMO","ISRG","MDT",
        "SYK","PFE","BMY","GILD","MRNA","CVS",
    ],
    # $100 이하 S&P500 중소형 위주 — 100달러 투입금 최적화
    "미국 중소형 30": [
        "BAC","WFC","C","USB","KEY","RF","FITB","HBAN","CFG","AXP",
        "COF","ALLY","SYF","T","VZ","F","GM","INTC","CSCO",
        "HPQ","HPE","CVS","MO","KHC","DAL","LUV","AAL","OKE","WMB",
    ],

    # ── 국내(KRX) ─────────────────────────────────────────────────────────
    # '.KS'(코스피)/'.KQ'(코스닥) 접미사 형식 그대로 사용
    "코스피 대형 30": [
        "005930.KS","000660.KS","373220.KS","207940.KS","005380.KS",
        "005490.KS","035420.KS","000270.KS","068270.KS","105560.KS",
        "055550.KS","012330.KS","028260.KS","006400.KS","051910.KS",
        "096770.KS","032830.KS","003670.KS","015760.KS","009150.KS",
        "018260.KS","010130.KS","034730.KS","033780.KS","024110.KS",
        "010950.KS","011200.KS","047050.KS","086790.KS","035720.KS",
    ],
    "코스피 대형 50": [
        # 기존 30
        "005930.KS","000660.KS","373220.KS","207940.KS","005380.KS",
        "005490.KS","035420.KS","000270.KS","068270.KS","105560.KS",
        "055550.KS","012330.KS","028260.KS","006400.KS","051910.KS",
        "096770.KS","032830.KS","003670.KS","015760.KS","009150.KS",
        "018260.KS","010130.KS","034730.KS","033780.KS","024110.KS",
        "010950.KS","011200.KS","047050.KS","086790.KS","035720.KS",
        # 추가 20
        "066570.KS","030200.KS","017670.KS","003490.KS","078930.KS",
        "011170.KS","329180.KS","004020.KS","139480.KS","021240.KS",
        "002790.KS","000720.KS","001040.KS","097950.KS","316140.KS",
        "010140.KS","051900.KS","004170.KS","011780.KS","161390.KS",
    ],
    "코스닥 기술주 15": [
        "247540.KQ","091990.KQ","328130.KQ","196170.KQ","240810.KQ",
        "086520.KQ","293490.KQ","039030.KQ","145020.KQ","263750.KQ",
        "066970.KQ","214150.KQ","141080.KQ","058470.KQ","036830.KQ",
    ],
    "코스닥 기술주 25": [
        # 기존 15
        "247540.KQ","091990.KQ","328130.KQ","196170.KQ","240810.KQ",
        "086520.KQ","293490.KQ","039030.KQ","145020.KQ","263750.KQ",
        "066970.KQ","214150.KQ","141080.KQ","058470.KQ","036830.KQ",
        # 추가 10
        "035900.KQ","041510.KQ","122870.KQ","257720.KQ","081660.KQ",
        "078340.KQ","032500.KQ","094360.KQ","054620.KQ","025900.KQ",
    ],
    # 섹터별 프리셋
    "국내 2차전지 10": [
        "373220.KS","006400.KS","003670.KS","096770.KS","051910.KS",
        "086520.KQ","247540.KQ","066970.KQ","011780.KS","281820.KQ",
    ],
    "국내 바이오 12": [
        "207940.KS","068270.KS","128940.KS","000100.KS","302440.KS",
        "196170.KQ","091990.KQ","141080.KQ","145020.KQ","214150.KQ",
        "328130.KQ","085660.KQ",
    ],
    "국내 반도체 10": [
        "005930.KS","000660.KS","009150.KS","018260.KS","042700.KS",
        "240810.KQ","039030.KQ","058470.KQ","094360.KQ","054620.KQ",
    ],
    "국내 금융 10": [
        "105560.KS","055550.KS","086790.KS","024110.KS","032830.KS",
        "000810.KS","316140.KS","006800.KS","005940.KS","016360.KS",
    ],
}

# 종목코드 → 시장 구분 (토스 API 주문 시 market='KRX'|'US' 넘길 때 사용)
def ticker_market(ticker: str) -> str:
    """'.KS'/'.KQ'로 끝나면 국내(KRX), 그 외는 해외(US)로 판단."""
    return "KRX" if ticker.endswith((".KS", ".KQ")) else "US"


def toss_symbol(ticker: str) -> str:
    """'.KS'/'.KQ' 접미사를 뗀 토스 실제 주문용 종목코드 반환 (국내 주문 시 필요)."""
    return ticker.split(".")[0] if ticker_market(ticker) == "KRX" else ticker


def market_of_symbol(sym: str) -> str:
    """이미 .KS/.KQ가 떨어진 broker용 심볼(held 딕셔너리의 키 등)만 있을 때
    시장을 추정. 국내 종목코드는 숫자 6자리라 이 방식으로 충분히 구분됨."""
    return "KRX" if sym.isdigit() else "US"


# 브로커가 주문을 확실히 거부했음을 뜻하는 상태들.
# 'expired' 는 Alpaca 에만 있는 상태다(토스에 없음). 여기 없으면 만료된 주문이
# 영영 '살아 있는 주문'으로 세어져 자리와 현금이 안 풀린다. Alpaca 의
# 'done_for_day' 는 GTC 주문이 오늘만 끝난 것이라 반대로 자리를 계속 잡는다.
_DEFINITIVE_FAIL = {"canceled", "rejected", "replaced", "expired",
                    "cancel_rejected", "replace_rejected"}


def order_accepted(fill_status: str | None) -> bool:
    """이 주문이 매수여력과 보유 자리를 차지하는가.

    "체결이 확인됐는가"와는 다른 질문이다. 체결 확인은 '얼마에 샀나'를 묻고,
    이건 '이번 실행에서 주문을 몇 건 더 낼 수 있나'를 묻는다. 명백히 거부되지
    않은 주문은 아직 살아 있으므로 자리와 돈을 잡아둬야 한다 — timeout 은
    실제로 체결됐을 수 있고, 가상 브로커의 pending_next_open 은 이미 장부에
    예약된 상태다.

    두 질문을 buy_rec["ok"] 하나로 묶었더니 가상 모드에서 한도가 통째로
    꺼졌다. 가상 주문은 영원히 "체결 확인 안 됨"이라 카운터가 늘지 않았고,
    5종목 한도에 12종목이 예약됐다(2026-07-31). 그렇다고 ok 를 켜면 이번엔
    체결도 안 된 주문이 어제 종가로 성적표에 올라간다. 그래서 나눈다.
    """
    return fill_status not in _DEFINITIVE_FAIL


def place_sell(symbol: str, qty, market: str = "KRX") -> dict:
    return _pm_market_sell(symbol, qty, market=market, dry_run=DRY_RUN)


# ── 고점·시그널 로그 ────────────────────────────────────────────────────
def load_peak_prices() -> dict:
    if os.path.exists(PEAK_FILE):
        try:
            with open(PEAK_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_peak_prices(peaks: dict) -> None:
    with open(PEAK_FILE, "w") as f:
        json.dump(peaks, f, indent=2, sort_keys=True)


def load_signal_log() -> list:
    if os.path.exists(SIGNAL_LOG_FILE):
        try:
            with open(SIGNAL_LOG_FILE) as f:
                return json.load(f).get("signals", [])
        except Exception:
            return []
    return []


def save_signal_log(signals: list) -> None:
    with open(SIGNAL_LOG_FILE, "w") as f:
        json.dump({"signals": signals[-300:]}, f, indent=2)


# ── 자산 로그 (P&L 추적) ─────────────────────────────────────────────
def load_equity_log() -> list:
    if os.path.exists(EQUITY_LOG_FILE):
        try:
            with open(EQUITY_LOG_FILE) as f:
                return json.load(f).get("records", [])
        except Exception:
            return []
    return []


def save_equity_log(records: list) -> None:
    with open(EQUITY_LOG_FILE, "w") as f:
        json.dump({"records": records[-504:]}, f, indent=2)  # 최대 2년(504 거래일)


def append_equity_log(records: list, equity: float, spy_price: float | None,
                       positions: list) -> list:
    """오늘 날짜 항목이 없으면 추가, 이미 있으면 업데이트."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    unrealized = sum(float(p.get("unrealized_pl", 0) or 0) for p in positions)
    entry = {
        "date":           today,
        "equity":         round(equity, 2),
        "spy_price":      round(spy_price, 2) if spy_price else None,
        "unrealized_pnl": round(unrealized, 2),
        "n_positions":    len(positions),
    }
    if records and records[-1]["date"] == today:
        records[-1] = entry
    else:
        records.append(entry)
    return records


def calc_performance_metrics(records: list) -> dict:
    """샤프 비율, 최대 드로다운, 총 수익률, SPY 대비 알파 계산."""
    if len(records) < 5:
        return {}
    equities   = [r["equity"]    for r in records]
    spy_prices = [r.get("spy_price") for r in records]

    # 일별 수익률
    port_rets = [(equities[i] / equities[i-1] - 1) for i in range(1, len(equities))]
    mean_r, std_r = float(np.mean(port_rets)), float(np.std(port_rets, ddof=1))
    sharpe = (mean_r / (std_r + 1e-9)) * np.sqrt(252) if std_r > 0 else 0.0

    # 최대 드로다운
    peak_e, max_dd = equities[0], 0.0
    for e in equities:
        peak_e = max(peak_e, e)
        max_dd = min(max_dd, (e / peak_e - 1) * 100)

    # 총 수익률
    total_ret = (equities[-1] / equities[0] - 1) * 100

    # SPY 수익률 (유효 데이터만)
    spy_valid = [p for p in spy_prices if p]
    spy_ret   = (spy_valid[-1] / spy_valid[0] - 1) * 100 if len(spy_valid) >= 2 else 0.0

    return {
        "total_return": round(total_ret, 2),
        "spy_return":   round(spy_ret, 2),
        "alpha":        round(total_ret - spy_ret, 2),
        "sharpe":       round(sharpe, 2),
        "max_dd":       round(max_dd, 2),
        "n_days":       len(records),
    }


def check_portfolio_drawdown(equity_now: float, records: list) -> tuple[bool, float]:
    """계좌 고점 대비 드로다운이 PORTFOLIO_DD_STOP_PCT 초과하면 (True, dd%) 반환."""
    if PORTFOLIO_DD_STOP_PCT <= 0 or not records:
        return False, 0.0
    peak = max(r["equity"] for r in records)
    dd   = (equity_now / peak - 1) * 100
    return dd < -PORTFOLIO_DD_STOP_PCT, round(dd, 2)


# ── 섹터 조회 ──────────────────────────────────────────────────────────
_sector_cache: dict[str, str] = {}


def _get_sector(ticker: str) -> str:
    """yfinance로 섹터 조회 (결과 캐싱). 실패하면 'Unknown'."""
    if ticker in _sector_cache:
        return _sector_cache[ticker]
    try:
        info   = yf.Ticker(ticker).info
        sector = info.get("sector") or "Unknown"
    except Exception:
        sector = "Unknown"
    _sector_cache[ticker] = sector
    return sector


# ── 포지션 reconciliation ───────────────────────────────────────────────
def reconcile_positions(peaks: dict, actual_positions: list,
                         legacy_positions: list, alert_fn) -> dict:
    """
    peak_prices.json(시스템 기록) vs 실제 포지션 불일치 감지 및 자동 수정.
    - 시스템O/계좌X: 외부 매도 → peak 항목 삭제 + 알림
    - 시스템X/계좌O: 외부 매수 → 현재가로 peak 초기화
    - 플랜 포지션이 peaks 에 있으면 조용히 뺀다 (아래 참조)

    **peaks 는 트레일링 스톱 전용이고, 트레일링은 플랜 없는 기존 보유분에만
    붙는다**(2-2 단계와 같은 기준). 그래서 대사 대상도 legacy 뿐이다.
    플랜 포지션까지 넣으면 이렇게 돈다: 오늘 orphan 으로 판정돼 peak 가
    생기고 → 손절/목표로 청산되면(settle_pending 경로라 sell_done 에 안 들어와
    peak 가 안 지워진다) → 다음 날 ghost 로 잡혀 **"외부 매도 의심"** 알림이
    뜬다. 가상 장부에는 외부 매도라는 게 존재할 수 없으므로 전부 거짓 경보였다
    (2026-08-14 ALLY·KHC·VZ 3건).
    """
    actual_syms   = {p["symbol"] for p in actual_positions}
    legacy_syms   = {p["symbol"] for p in legacy_positions}
    peak_syms     = set(peaks.keys())
    ghost         = peak_syms - actual_syms   # 시스템엔 있는데 계좌에 없음
    stale         = (peak_syms & actual_syms) - legacy_syms  # 플랜 포지션
    orphan        = legacy_syms - peak_syms   # 계좌엔 있는데 시스템에 없음

    for sym in stale:
        peaks.pop(sym, None)

    if not ghost and not orphan:
        return peaks

    pos_map = {p["symbol"]: p for p in legacy_positions}

    if ghost:
        msgs = ["⚠️ *포지션 불일치* (외부 매도 의심)"]
        for sym in ghost:
            msgs.append(f"  `{sym}` 시스템O → 계좌 X (peak 삭제)")
            peaks.pop(sym, None)
        msg = "\n".join(msgs)
        print(msg)
        alert_fn(msg)

    for sym in orphan:
        cur_price = float(pos_map[sym].get("current_price") or
                          pos_map[sym].get("avg_entry_price") or 0)
        if cur_price > 0:
            peaks[sym] = cur_price
            print(f"  [peak 초기화] {sym} @ {cur_price:.2f}")

    return peaks


# ── 시그널 로그 ─────────────────────────────────────────────────────────
def append_signals_to_log(new_signals: list, existing_log: list) -> list:
    """매수 시그널을 로그에 추가 (같은 날 중복 제외).

    항목에 entry_date 가 있으면 그 날짜로 남긴다. 가상 체결은 예약 다음
    거래일 시가에 일어나므로 기록하는 날(오늘)과 체결일이 다르다. 오늘로
    찍으면 보유 기간이 하루씩 짧게 계산돼 성적이 어긋난다.
    """
    default_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # signal-alerts 워크플로가 남기는 항목에는 id가 없다. 없는 항목은
    # symbol-entry_date 조합으로 같은 키를 만들어 중복 판정에 참여시킨다.
    existing_ids = {
        s.get("id") or f"{s.get('symbol', '')}-{s.get('entry_date', '')}"
        for s in existing_log
    }
    for sig in new_signals:
        if sig.get("action") != "매수":
            continue
        entry_date = sig.get("entry_date") or default_date
        sig_id = f"{sig['symbol']}-{entry_date}"
        if sig_id in existing_ids:
            continue
        existing_log.append({
            "id":           sig_id,
            "symbol":       sig["symbol"],
            "action":       sig["action"],
            "entry_date":   entry_date,
            "entry_price":  sig.get("price", 0.0),
            "score":        sig.get("score", 0.0),
            "rsi":          sig.get("rsi", 50.0),
            "outcome_date":  None,
            "outcome_price": None,
            "return_pct":    None,
        })
    return existing_log


def record_virtual_fills(new_trades: list) -> int:
    """방금 체결된 가상 매수를 성적표(signal_log)에 올린다. 기록한 건수 반환.

    가상 체결은 주문한 날이 아니라 다음 거래일 시가에, 그것도 러너의 매수
    경로가 아니라 settle_pending 안에서 일어난다. 그래서 주문 시점에 기록하는
    기존 경로(buy_results)로는 영원히 잡히지 않았고, 가상 매매는 성적표에 한
    건도 오르지 않았다 — 지금 유일하게 돌고 있는 매매인데도.

    체결가와 체결일은 이 시점에 처음 확정되므로 여기서 기록한다.

    점수·RSI 는 주문 시점의 값이라 체결 시점에 다시 계산할 수 없다. 오늘 점수로
    채우면 주문 근거가 아닌 값이 성적표에 박혀 점수-수익률 관계가 조용히 틀어진다.
    그래서 주문에 실어 둔 것(trade["meta"])을 그대로 옮긴다. 근거가 없는 주문
    (meta 를 안 실은 예전 예약분, 비서를 통한 수동 매수)은 비워 둔다 —
    signal_scorecard.by_score_bucket 이 점수 없는 항목을 빼고 계산하므로,
    승률·기대값에는 잡히고 점수 구간별 분석에서만 빠진다.
    """
    buys = [t for t in new_trades if t.get("side") == "buy"]
    if not buys:
        return 0
    save_signal_log(append_signals_to_log(
        [{"symbol":     t["symbol"],
          "action":     "매수",
          "price":      t.get("price_usd", 0.0),
          "score":      (t.get("meta") or {}).get("score"),
          "rsi":        (t.get("meta") or {}).get("rsi"),
          "entry_date": t.get("date")} for t in buys],
        load_signal_log(),
    ))
    return len(buys)


def resolve_signal_outcomes(signal_log: list, prices_cache: dict) -> list:
    """entry_date + SIGNAL_HOLD_DAYS 이상 경과한 미결 시그널의 결과를 기록."""
    today   = datetime.now(timezone.utc).date()
    updated = []
    for sig in signal_log:
        if sig.get("return_pct") is not None:
            updated.append(sig)
            continue
        try:
            entry_date = datetime.strptime(sig["entry_date"], "%Y-%m-%d").date()
        except Exception:
            updated.append(sig)
            continue
        if (today - entry_date).days < SIGNAL_HOLD_DAYS:
            updated.append(sig)
            continue
        sym = sig["symbol"]
        cur = prices_cache.get(sym)
        if cur is None:
            try:
                yahoo_sym = sym.replace(".", "-")
                raw = yf.download(yahoo_sym, period="5d", progress=False, auto_adjust=True)
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = raw.columns.droplevel(1)
                if not raw.empty:
                    cur = float(raw["Close"].dropna().iloc[-1])
                    prices_cache[sym] = cur
            except Exception:
                pass
        if cur and float(sig.get("entry_price", 0)) > 0:
            ret = (cur / float(sig["entry_price"]) - 1) * 100
            sig["outcome_price"] = round(cur, 2)
            sig["outcome_date"]  = str(today)
            sig["return_pct"]    = round(ret, 2)
        updated.append(sig)
    return updated


def signal_log_summary(signal_log: list) -> dict:
    """완료된 시그널의 승률·평균 수익률 요약."""
    done = [s for s in signal_log if s.get("return_pct") is not None]
    if not done:
        return {"n": 0, "win_rate": 0.0, "avg_return": 0.0, "avg_win": 0.0, "avg_loss": 0.0}
    returns = [s["return_pct"] for s in done]
    wins    = [r for r in returns if r > 0]
    losses  = [r for r in returns if r <= 0]
    return {
        "n":          len(done),
        "win_rate":   round(len(wins) / len(done) * 100, 1),
        "avg_return": round(float(np.mean(returns)), 2),
        "avg_win":    round(float(np.mean(wins)),    2) if wins   else 0.0,
        "avg_loss":   round(float(np.mean(losses)),  2) if losses else 0.0,
    }


# 체결 없이 끝난 주문. 청산이 아니라 **셋업의 분모**로 세야 체결률이 안 부푼다.
# nofill=진입구간 미도달(백테스트와 같은 사유), cash_short=우리 현금이 모자람.
DISCARD_OUTCOMES = ("nofill", "cash_short")


def plan_trade_summary(trades: list) -> dict:
    """플랜 트레이드 성적 — **R 단위**로. 백테스트와 같은 자로 재기 위한 것.

    %수익률로는 백테스트의 +0.66R 과 비교할 수 없다. R 은 위험 1단위 기준이라
    손절이 촘촘한 종목과 넓은 종목을 같은 무게로 놓기 때문이다.

    체결률을 함께 낸다 — 미체결 폐기를 빼고 세면 승률이 부풀어 보인다
    (백테스트에서 셋업의 36% 가 진입구간 미도달로 사라졌다).
    """
    plans    = [t for t in trades if t.get("plan")]
    nofill   = [t for t in plans if t.get("outcome") in DISCARD_OUTCOMES]
    filled   = [t for t in plans if t.get("side") == "buy"]
    resolved = [t for t in plans if t.get("outcome") in ("win", "loss", "timeout")]
    wins     = [t for t in resolved if t.get("outcome") == "win"]
    n_setups = len(filled) + len(nofill)
    return {
        "n_setups":   n_setups,
        "n_filled":   len(filled),
        "n_resolved": len(resolved),
        "avg_r":      round(float(np.mean([t["r_realized"] for t in resolved])), 3)
                      if resolved else 0.0,
        "win_rate":   round(len(wins) / len(resolved) * 100, 1) if resolved else 0.0,
        "fill_rate":  round(len(filled) / n_setups * 100, 1) if n_setups else 0.0,
    }


def check_trailing_stops(positions: list, trail_pct: float = TRAIL_STOP_PCT,
                          peaks: dict = None) -> tuple:
    """고점 대비 trail_pct% 이하로 떨어지면 시장가 매도 (트레일링 스톱).
    반환: (results, 업데이트된 peaks)
    """
    if trail_pct <= 0 or not positions:
        return [], peaks or {}

    if peaks is None:
        peaks = {}

    results = []
    for pos in positions:
        sym       = pos.get("symbol", "")
        qty       = pos.get("qty", "0")
        avg_price = float(pos.get("avg_entry_price", 0) or 0)
        cur_price = float(pos.get("current_price",   0) or 0)
        if avg_price <= 0 or cur_price <= 0 or float(qty) <= 0:
            continue

        prev_peak  = float(peaks.get(sym, avg_price))
        new_peak   = max(prev_peak, avg_price, cur_price)
        peaks[sym] = new_peak

        trail_line = new_peak * (1 - trail_pct / 100)
        gain_pct   = (cur_price / avg_price - 1) * 100

        if cur_price < trail_line:
            print(f"  [트레일링 스톱] {sym}  고점 {new_peak:.2f} → 현재 {cur_price:.2f}"
                  f"  ({trail_pct:.0f}% 이상 하락)  수익 {gain_pct:+.1f}%")
            if DRY_RUN:
                results.append({"symbol": sym, "dry_run": True, "gain_pct": gain_pct})
            else:
                try:
                    place_sell(sym, qty, market=market_of_symbol(sym))
                    results.append({"symbol": sym, "ok": True, "gain_pct": gain_pct})
                    peaks.pop(sym, None)
                except Exception as e:
                    print(f"  [트레일링 스톱 오류] {sym}: {e}")
                    results.append({"symbol": sym, "error": str(e)})
        else:
            room = (cur_price / trail_line - 1) * 100
            print(f"  {sym}  현재 {cur_price:.2f}  고점 {new_peak:.2f}"
                  f"  수익 {gain_pct:+.1f}%  스톱까지 {room:.1f}% 여유")

    return results, peaks


def _to_broker_sym(ticker: str):
    """실제 주문에 넘길 종목코드로 변환.
    국내(.KS/.KQ)는 접미사를 뗀 6자리 코드, 해외는 그대로(대문자) 반환."""
    if ticker_market(ticker) == "KRX":
        return toss_symbol(ticker)
    return ticker.replace("-", ".").upper()


# ── 텔레그램 ──────────────────────────────────────────────────────────
def send_tg(msg: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[TG] TELEGRAM_TOKEN 또는 TELEGRAM_CHAT_ID 없음 → 발송 생략")
        return
    try:
        # Markdown 먼저 시도, 특수문자 파싱 오류 시 plain text로 재시도
        resp = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
        if resp.status_code == 400 and "parse entities" in resp.text:
            resp = requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT_ID, "text": msg},
                timeout=10,
            )
        if resp.status_code == 200:
            print("[TG] 발송 성공 ✓")
        else:
            print(f"[TG 오류] HTTP {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[TG 오류] {e}", file=sys.stderr)


# ── 트레이드 플랜 후보 ─────────────────────────────────────────────────
def scan_trade_plans(ohlcv: dict) -> list[dict]:
    """유니버스 전 종목의 트레이드 플랜 중 **실행 대상(actionable)만** 골라 온다.

    actionable 은 valid 와 다른 질문이다 — valid 는 기하가 성립하나, actionable
    은 비용을 견디고 방향이 되나. 문턱은 trade_plan 안에 있다(롱 + 등급 A·B).
    걸러진 셋업도 라인은 계산되고 화면·성적표에는 남는다. 여기서는 주문만 안 낸다.
    """
    out = []
    for ticker, df in ohlcv.items():
        plan = build_trade_plan(df)
        if not plan.get("actionable"):
            continue
        out.append({
            "ticker":    ticker,
            "current":   plan["current"],
            "entry_ref": plan["entry"]["ref"],
            "limit":     plan["entry"]["high"],   # 롱 진입은 구간 상단 지정가
            "stop":      plan["stop"],
            "target":    plan["targets"][0],      # T1 선착 청산 (T2는 안 쓴다)
            "rr":        (plan["rr"] or [None])[0],
            "grade":     plan["cost_grade"],
            "risk_pct":  plan["risk_pct"],
            "signals":   plan.get("signals", []),
        })
    return out


def latest_analyst_scores() -> tuple[dict, str | None]:
    """직전 기록일의 종목별 애널리스트 점수 → ({ticker: {...}}, 그 날짜).

    analyst-log 워크플로는 이 러너보다 뒤(23:00 UTC vs 21:30 UTC)에 돌므로
    가장 최근 기록은 **어제치**다. 주문을 내는 순간 실제로 알 수 있었던 값이라
    look-ahead 가 없다 — 오늘 다시 계산해서 채우면 그게 look-ahead 다.
    """
    try:
        days = analyst_log.load_days()
    except Exception as e:                    # 기록이 없거나 깨져도 주문은 나가야 한다
        print(f"  [경고] 애널리스트 기록 로드 실패 — 점수 없이 주문: {e}")
        return {}, None
    if not days:
        return {}, None
    last = days[-1]
    return last.get("scores") or {}, last.get("date")


def order_meta(scores: dict, asof: str | None, ticker: str) -> dict:
    """주문에 실어 둘 관측 기록. **주문 근거가 아니다.**

    러너는 트레이드 플랜만 보고 산다 — 총괄 판정은 방향에도 자리에도 안 들어간다.
    그런데 총괄 판정을 언젠가 매매에 붙이려면 **"그 점수가 우리가 실제로 낸
    주문의 결과를 예측했나"** 를 먼저 물어야 하고, 그때 쓸 표본은 지금부터
    쌓아야 한다. 붙이기 전에 재는 순서를 지키려고 남기는 줄이다.

    `score` 키에 총괄 판정을 넣는 이유는 signal_scorecard.by_score_bucket 이
    그 키를 읽기 때문이다. `score_role="observed"` 로 **주문이 쓴 값이 아님**을
    같이 박아 둔다 — 나중에 이 줄을 근거로 읽으면 안 된다.

    기록에 없는 종목(유니버스가 다르다)은 빈 dict 를 준다. 채울 수 없는 것을
    중립값으로 채우면 '계산 불가'가 '중립 판단'으로 성적에 섞인다.
    """
    row = scores.get(ticker) or {}
    verdict = row.get("verdict")
    if verdict is None:
        return {}
    return {"score": verdict, "score_role": "observed",
            "analyst": dict(row), "analyst_asof": asof}


def rank_plan_candidates(cands: list[dict]) -> list[dict]:
    """A등급 먼저, 같은 등급 안에서는 현재가가 진입가에 가까운 순.

    등급 안에서는 기대값이 구별되지 않는다고 이미 측정됐다(부트스트랩 구간이
    0 을 포함). 그래서 안 측정된 순위 기준을 새로 만들지 않고 **체결 확률이
    높은 쪽**을 앞에 둔다 — 20거래일 안에 안 닿으면 주문 자체가 사라지므로.
    """
    def key(c: dict) -> tuple:
        grade_rank = (ACTIONABLE_GRADES.index(c["grade"])
                      if c["grade"] in ACTIONABLE_GRADES else len(ACTIONABLE_GRADES))
        gap = abs(c["current"] - c["entry_ref"]) / c["entry_ref"] if c["entry_ref"] else 9.9
        return (grade_rank, gap)
    return sorted(cands, key=key)


def plan_position_size(equity_krw: float, entry_ref: float, stop: float, fx: float,
                       risk_pct: float = RISK_PCT_PER_TRADE,
                       max_pos_pct: float = MAX_POSITION_PCT) -> int:
    """위험 기준 수량. **플랜 위험** 1단위가 자본의 risk_pct% 가 되게 잡는다.

    금액이 아니라 위험을 고정해야 손절이 촘촘한 종목과 넓은 종목이 장부에
    같은 무게로 들어간다. 그게 R 단위로 성적을 재는 전제다.
    1주도 못 사면 0 (소수점 거래 불가).

    분모는 `entry_ref - stop` 이다. **실제 체결가가 아니다** — 체결가로
    나누면 손절 바로 위에 갭체결된 건에 수량이 폭발한다(위험 $0.10 → 수량
    10배). 플랜 위험은 셋업 기하라 갭에 안 흔들린다. 대신 손절까지 실제
    손실은 이 값의 1.0~2.0배(중앙 1.63)이고, RISK_PCT_PER_TRADE 가 그만큼을
    감안한 값이다.
    """
    risk_per_share_krw = (entry_ref - stop) * fx
    if risk_per_share_krw <= 0 or entry_ref <= 0:
        return 0
    qty = int(equity_krw * risk_pct / 100 / risk_per_share_krw)
    cap_qty = int(equity_krw * max_pos_pct / 100 / (entry_ref * fx))
    return max(0, min(qty, cap_qty))


# ── 메인 ────────────────────────────────────────────────────────────────
def main():
    run_ts   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    mode_tag = "[가상장부]"
    print(f"\n{'='*60}")
    print(f"  자동매매 실행  {run_ts}  {mode_tag}")
    print(f"  DRY_RUN={DRY_RUN}  UNIVERSE={UNIVERSE_NAME}  MAX_POS={MAX_POSITIONS}")
    print(f"  위험/트레이드 {RISK_PCT_PER_TRADE}%  종목상한 {MAX_POSITION_PCT}%")
    print(f"  DD_STOP={PORTFOLIO_DD_STOP_PCT}%  MAX_SECTOR={MAX_SECTOR_POSITIONS}")
    print(f"{'='*60}\n")

    # 트레이드 플랜은 **진입 구간 지정가 대기**로 들어간다. 실주문 브로커를
    # 붙일 때 그 주문 종류가 없으면(토스가 그랬다) 시가 시장가로 대신 사게
    # 되는데, 그러면 손절폭과 R:R 이 계획과 달라져 백테스트가 잰 값이 이
    # 장부에 적용되지 않는다. 조용히 다른 규칙으로 사느니 안 붙인다.

    # 0-0. 실시간 환율 (미국 종목 매수여력 원화 환산용)
    _KRW_PER_USD = fetch_krw_per_usd(fallback=float(os.environ.get("KRW_PER_USD", "1400")))

    # 0-1. 지난 실행분 정산 — 보유 포지션 청산 판정 → 지정가 진입 체결 → 만료 폐기.
    #      판정은 러너가 도는 순간의 종가가 아니라 일봉 되짚기로 한다.
    from modules.virtual_broker import (
        check_state, load_state, place_limit_entry, save_state, set_fx, settle_pending,
    )
    set_fx(_KRW_PER_USD)
    _vstate_before = load_state()
    # 체결은 settle_pending 안에서 trades 뒤에 덧붙는다. 부르기 전 길이를
    # 재두면 이번에 새로 체결된 건만 성적표에 올릴 수 있다.
    _n_trades_before = len(_vstate_before["trades"])
    _vstate = settle_pending(_vstate_before, _KRW_PER_USD)
    save_state(_vstate)
    new_trades = _vstate["trades"][_n_trades_before:]
    _n_logged = record_virtual_fills(new_trades)
    if _n_logged:
        print(f"  [가상] 체결 {_n_logged}건을 성적표에 기록했습니다.")

    # 정산 직후가 장부가 가장 많이 움직인 순간이다 — 여기서 한 번 되짚는다.
    # 실행을 막지는 않는다: 새 주문은 place_limit_entry 가 가용 현금으로 이미
    # 막고 있고, 여기서 러너를 세우면 거짓 경보 한 번에 하루를 통째로 날린다.
    # 대신 로그와 일별 리포트(daily_report_toss.py) 양쪽에 남긴다.
    _ledger_problems = check_state(_vstate)
    if _ledger_problems:
        print(f"  ⚠️ [장부 점검] 이상 {len(_ledger_problems)}건 — 확인 필요")
        for _p in _ledger_problems:
            print(f"     - {_p}")
    else:
        print("  [장부 점검] 이상 없음")

    # 0. 시장 레짐 감지
    print("시장 레짐 감지 중...")
    regime, regime_info = get_market_regime()
    regime_emoji = {"bull": "🐂", "neutral": "😐", "bear": "🐻"}.get(regime, "😐")
    print(f"  레짐: {regime_emoji} {regime.upper()}  "
          f"SPY/200MA={regime_info['spy_ratio']:.3f}  VIX={regime_info['vix']:.1f}")
    effective_max_pos = _REGIME_MAX_POS.get(regime, MAX_POSITIONS)
    if effective_max_pos < MAX_POSITIONS:
        print(f"  ⚠️  {regime.upper()} 레짐 → MAX_POSITIONS {MAX_POSITIONS} → {effective_max_pos} (노출 축소)")

    # 1. 토스증권 계좌 확인
    try:
        acct = _pt_get_account()
    except Exception as e:
        print(f"[오류] 토스증권 계좌 조회 실패: {e}")
        sys.exit(1)

    equity_now   = float(acct.get("equity", 0))
    buying_power = float(acct.get("buying_power", 0))
    print(f"계좌 자산: {equity_now:,.0f}  매수여력: {buying_power:,.0f}")

    if acct.get("trading_blocked") or acct.get("account_blocked"):
        msg = "계좌이 잠겨 있습니다 → 실행 중단."
        print(msg); send_tg(msg); sys.exit(0)

    # 1-1. 자산 로그 로드 & 포트폴리오 드로다운 체크
    equity_log   = load_equity_log()
    dd_blocked, dd_pct = check_portfolio_drawdown(equity_now, equity_log)
    if dd_blocked:
        warn = (f"⚠️ 포트폴리오 드로다운 *{dd_pct:.1f}%* "
                f"(한도: -{PORTFOLIO_DD_STOP_PCT:.0f}%) → 신규 매수 중단")
        print(warn); send_tg(warn)

    # 1-1b. 킬스위치 — DD stop과 별개로 (a)전일 대비 일일손실 (b)연속 브로커오류
    #        (c)단일주문 크기상한을 감시. DD stop이 고점 대비라면 이건 급락 하루를 잡는다.
    kill = _KillSwitch(max_daily_loss_pct=KS_MAX_DAILY_LOSS_PCT,
                       max_errors=KS_MAX_ERRORS,
                       max_single_order_pct=KS_MAX_SINGLE_ORDER_PCT) if _KILL_SWITCH_AVAILABLE else None
    if kill is not None and equity_log:
        # 전일(직전 레코드) 자산을 당일 기준가로 삼아 하루 급락을 감지
        kill.set_day_start_equity(float(equity_log[-1].get("equity", equity_now)))
        if kill.check_daily_loss(equity_now):
            ks_warn = (f"🛑 킬스위치 발동: {kill.status()['reason']} → 신규 매수 전면 중단")
            print(ks_warn); send_tg(ks_warn)
            dd_blocked = True  # 기존 매수 차단 경로 재사용

    # 1-2. SPY 현재가 수집 (성과 추적용)
    spy_price = None
    try:
        _spy = yf.download("SPY", period="5d", progress=False, auto_adjust=True)
        if isinstance(_spy.columns, pd.MultiIndex):
            _spy.columns = _spy.columns.droplevel(1)
        if not _spy.empty:
            spy_price = float(_spy["Close"].dropna().iloc[-1])
    except Exception:
        pass

    # 2. 현재 보유 포지션
    try:
        positions = _pt_get_positions()
    except Exception as e:
        print(f"[경고] 포지션 조회 실패: {e}")
        positions = []

    held = {p["symbol"]: p for p in positions}
    print(f"현재 보유 {len(held)}개: {list(held.keys())}")

    # 2-1. peak_prices 로드 & 포지션 reconciliation
    #
    # legacy 판정을 대사보다 **먼저** 한다 — peaks 는 트레일링 전용이라
    # 대사 대상도 legacy 뿐이다 (reconcile_positions 주석 참조).
    legacy_positions = [p for p in positions if not (p.get("_raw") or {}).get("plan")]
    peaks = load_peak_prices()
    if positions:
        peaks = reconcile_positions(peaks, positions, legacy_positions, send_tg)

    # 2-2. 트레일링 스톱 체크 — **플랜 없이 산 기존 보유분만**.
    #
    # 플랜 포지션은 손절/목표 라인으로 청산된다(settle_pending). 거기에 트레일링을
    # 겹치면 백테스트에 없던 규칙이 섞여 R 이 거짓이 된다. 반대로 KHC·GM 등 예전
    # 팩터 매수분은 라인 없이 산 것이라 사후에 라인을 붙일 수 없다. 그래서 각자
    # 자기 규칙으로 마무리한다.
    sell_results, sell_done = [], set()
    if legacy_positions and TRAIL_STOP_PCT > 0:
        print(f"\n트레일링 스톱 점검 ({TRAIL_STOP_PCT:.0f}%, 기존 팩터 보유 "
              f"{len(legacy_positions)}종목)...")
        _stop_fired, peaks = check_trailing_stops(legacy_positions, peaks=peaks)
        for r in _stop_fired:
            if r.get("ok") or r.get("dry_run"):
                sell_results.append({"symbol": r["symbol"], "ok": True,
                                     "reason": f"트레일링 스톱 {r.get('gain_pct', 0):+.1f}%"})
                sell_done.add(r["symbol"])

    # 3. 유니버스 (';'로 여러 프리셋 합치기 가능, 중복 제거)
    raw_tickers: list[str] = []
    for _name in UNIVERSE_NAME.split(";"):
        _name = _name.strip()
        _preset = UNIVERSE_PRESETS.get(_name)
        if _preset:
            raw_tickers.extend(_preset)
        else:
            raw_tickers.extend(t.strip().upper() for t in _name.split(",") if t.strip())
    seen: set[str] = set()
    tickers = [t for t in raw_tickers if t not in seen and not seen.add(t)]  # type: ignore[func-returns-value]
    print(f"\n유니버스: {UNIVERSE_NAME} → {len(tickers)}개 (중복 제거 후)")

    # 4. 일봉 패널 — 유니버스 전 종목. 팩터 스코어보다 오히려 가볍다.
    _panel_end   = datetime.now(timezone.utc).date()
    _panel_start = _panel_end - timedelta(days=PLAN_LOOKBACK_DAYS)
    print(f"일봉 로드 중... {len(tickers)}종목 × 최근 {PLAN_LOOKBACK_DAYS}일")
    try:
        _, ohlcv = load_panel(tickers, _panel_start, _panel_end)
    except PanelCoverageError as e:
        print(f"[경고] 일봉 확보 실패 → 오늘 실행 건너뜀: {e}")
        send_tg(f"페이퍼 트레이딩: 일봉 확보 실패, 오늘 실행 건너뜀 ({e})")
        sys.exit(0)

    # 5. 트레이드 플랜 → 실행 대상만 골라 순위
    print("트레이드 플랜 계산 중...")
    candidates = rank_plan_candidates(scan_trade_plans(ohlcv))
    print(f"  실행 대상 {len(candidates)}종목 / 유니버스 {len(ohlcv)}종목 "
          f"(롱 + 실행등급 {'·'.join(ACTIONABLE_GRADES)})")
    for c in candidates[:5]:
        print(f"    {c['ticker']:6s} {c['grade']}  현재 {c['current']:.2f} → "
              f"진입 {c['limit']:.2f} / 손절 {c['stop']:.2f} / 목표 {c['target']:.2f}"
              f"  R:R {c['rr']}  위험폭 {c['risk_pct']:.2f}%")

    # 5-1. 주문에 실어 둘 총괄 판정 — **관측 기록이지 주문 근거가 아니다**(order_meta).
    analyst_scores, analyst_asof = latest_analyst_scores()
    if analyst_scores:
        covered = sum(1 for c in candidates if c["ticker"] in analyst_scores)
        print(f"  애널리스트 기록 {analyst_asof} · {len(analyst_scores)}종목 "
              f"(실행 대상 {len(candidates)}중 {covered}종목 매칭)")
    else:
        print("  애널리스트 기록 없음 — 주문에 점수를 안 싣는다")

    # 6. 지정가 진입 주문
    #
    # 자리는 **보유 + 대기 중인 지정가 주문**을 함께 센다. 지정가는 최대
    # LIMIT_FILL_WINDOW 거래일을 기다리므로, 대기분이 자리를 안 차지하면
    # 상한이 통째로 꺼지고 20일치 주문이 겹쳐 쌓인다.
    pending_syms = {o["symbol"] for o in load_state()["pending"]
                    if o.get("side") == "buy"}
    occupied = (len(held) - len(sell_done)) + len(pending_syms - set(held))
    slots    = max(0, effective_max_pos - occupied)
    print(f"\n자리: 보유 {len(held)} + 대기주문 {len(pending_syms)} "
          f"→ 신규 {slots}건 가능 (상한 {effective_max_pos})")

    order_results:  list[dict] = []
    skipped_sector: list[str]  = []
    skipped_size:   list[str]  = []
    skipped_cash:   list[str]  = []

    # cold cache 시 첫 반복에서 yfinance 호출이 몰리는 문제 방지
    for _s in set(held) | pending_syms:
        _get_sector(_s)

    ordered_sectors: dict[str, int] = {}

    for cand in candidates:
        if len(order_results) >= slots:
            break
        if dd_blocked:
            print(f"  [드로다운 스톱] 신규 매수 전면 중단 (dd={dd_pct:.1f}%)")
            break
        if kill is not None and kill.is_active():
            print(f"  [킬스위치] {kill.status()['reason']} → 신규 매수 중단")
            break

        sym = _to_broker_sym(cand["ticker"])
        if not sym or sym in held or sym in pending_syms:
            continue

        # 섹터 집중도 제한
        sym_sector = _get_sector(sym)
        held_in_sector = (
            sum(1 for s in (set(held) | pending_syms)
                if s not in sell_done and _get_sector(s) == sym_sector)
            + ordered_sectors.get(sym_sector, 0)
        )
        if held_in_sector >= MAX_SECTOR_POSITIONS:
            print(f"  [섹터 제한] {sym} ({sym_sector}) 이미 {held_in_sector}개 → 스킵")
            skipped_sector.append(f"{sym}({sym_sector})")
            continue

        # 위험 기준 수량 — 손절까지 맞으면 자본의 RISK_PCT_PER_TRADE% 를 잃는다.
        qty = plan_position_size(equity_now, cand["entry_ref"], cand["stop"],
                                 _KRW_PER_USD)
        if qty < 1:
            print(f"  [매수 스킵] {sym} 1주도 못 삼 (소수점 거래 불가, "
                  f"주가 ${cand['limit']:,.2f})")
            skipped_size.append(f"{sym}(${cand['limit']:,.0f})")
            continue

        notional_krw = qty * cand["limit"] * _KRW_PER_USD
        if notional_krw > buying_power:
            print(f"  [매수 스킵] {sym} 매수여력 부족 "
                  f"(필요 {notional_krw:,.0f}원, 가용 {buying_power:,.0f}원)")
            skipped_cash.append(sym)
            continue

        # 킬스위치: 단일 주문이 계좌 대비 과도하면 스킵 (사이징 버그·급변 방어)
        if kill is not None and equity_now > 0 and kill.check_order_size(notional_krw, equity_now):
            print(f"  [킬스위치] {sym} 주문 {notional_krw:,.0f}원이 계좌 "
                  f"{equity_now:,.0f}원의 {KS_MAX_SINGLE_ORDER_PCT:.0f}% 초과 → 스킵")
            continue

        print(f"  [지정가 매수] {sym} {qty}주 @ ${cand['limit']:,.2f} "
              f"({notional_krw:,.0f}원, 등급 {cand['grade']}, R:R {cand['rr']}, "
              f"섹터 {sym_sector})")
        if cand["signals"]:
            print(f"    ▶ {cand['signals'][0]}")

        rec = {"symbol": sym, "ticker": cand["ticker"], "qty": qty,
               "limit": cand["limit"], "stop": cand["stop"],
               "target": cand["target"], "rr": cand["rr"],
               "grade": cand["grade"], "risk_pct": cand["risk_pct"],
               "notional_krw": notional_krw, "sector": sym_sector, "ok": True}
        if DRY_RUN:
            rec["dry_run"] = True
        else:
            try:
                place_limit_entry(
                    sym, qty, cand["limit"],
                    plan={"stop": cand["stop"], "target": cand["target"],
                          "rr": cand["rr"], "grade": cand["grade"],
                          "risk_pct": cand["risk_pct"],
                          # 사이징 분모 = 장부 R 분모. 이게 없으면 장부가
                          # 체결가로 나눠 손절을 늘 -1.00R 로 과소 기록한다.
                          "entry_ref": cand["entry_ref"]},
                    market=market_of_symbol(sym),
                    meta=order_meta(analyst_scores, analyst_asof, cand["ticker"]),
                )
                if kill is not None:
                    kill.record_success()
            except Exception as e:
                print(f"  [매수 오류] {sym}: {e}")
                order_results.append({"symbol": sym, "error": str(e)})
                if kill is not None:
                    kill.record_error()
                continue

        order_results.append(rec)
        buying_power -= notional_krw
        ordered_sectors[sym_sector] = ordered_sectors.get(sym_sector, 0) + 1

    # 8. 사후 처리
    if not DRY_RUN:
        for sym in sell_done:
            peaks.pop(sym, None)
    save_peak_prices(peaks)

    # 성적표에는 settle_pending 이 확정한 체결만 오른다(record_virtual_fills).
    # 여기서 주문 시점에 또 올리면 체결가 자리에 어제 종가가 박힌다.
    sig_log = load_signal_log()
    prices_cache = {
        _to_broker_sym(tk): float(df["Close"].dropna().iloc[-1])
        for tk, df in ohlcv.items() if not df["Close"].dropna().empty
    }
    sig_log = resolve_signal_outcomes(sig_log, prices_cache)
    save_signal_log(sig_log)
    sl_summary = signal_log_summary(sig_log)

    # 자산 로그 업데이트
    equity_log = append_equity_log(equity_log, equity_now, spy_price, positions)
    save_equity_log(equity_log)
    perf = calc_performance_metrics(equity_log)

    # 9. 요약 & 텔레그램
    n_sells = sum(1 for r in sell_results if r.get("ok"))
    n_errs  = sum(1 for r in sell_results + order_results if "error" in r)
    _trail_sells = [r["symbol"] for r in sell_results
                    if r.get("ok") and "트레일링" in r.get("reason", "")]
    _filled  = [t for t in new_trades if t.get("plan") and t.get("side") == "buy"]
    _exited  = [t for t in new_trades if t.get("r_realized") is not None
                and t.get("outcome") not in DISCARD_OUTCOMES]
    _nofill  = [t for t in new_trades if t.get("outcome") in DISCARD_OUTCOMES]

    lines = [
        f"*페이퍼 트레이딩* `{run_ts}` {mode_tag}",
        f"{'[DRY-RUN]' if DRY_RUN else '[실제 주문]'}  자산 `{equity_now:,.0f}원`",
        f"{regime_emoji} 레짐: *{regime.upper()}*  SPY/MA={regime_info['spy_ratio']:.3f}  VIX={regime_info['vix']:.1f}"
        + (f"  (MAX_POS→{effective_max_pos})" if effective_max_pos < MAX_POSITIONS else ""),
        "",
    ]
    if _exited:
        lines.append("*청산* " + ", ".join(
            f"{t['symbol']} {t['outcome']} `{t['r_realized']:+.2f}R`" for t in _exited))
    if _filled:
        lines.append("*체결* " + ", ".join(
            f"{t['symbol']} {t['qty']}주 @${t['price_usd']:,.2f}" for t in _filled))
    if _nofill:
        lines.append(f"미체결 폐기 {len(_nofill)}건: "
                     + ", ".join(t["symbol"] for t in _nofill))
    if n_sells:
        lines.append(f"*팩터 보유 매도* {n_sells}건: "
                     + ", ".join(r["symbol"] for r in sell_results if r.get("ok")))

    lines.append(f"*신규 지정가* {len(order_results)}건 / 실행대상 {len(candidates)}종목")
    for r in order_results:
        if "error" in r:
            continue
        lines.append(f"  `{r['symbol']}` {r['grade']}등급 {r['qty']}주  "
                     f"진입 {r['limit']:.2f} → 손절 {r['stop']:.2f} / 목표 {r['target']:.2f}  "
                     f"R:R {r['rr']}")

    if _trail_sells:
        lines.append(f"트레일링 스톱 발동: {', '.join(_trail_sells)}")
    if skipped_sector:
        lines.append(f"섹터 제한 스킵: {', '.join(skipped_sector)}")
    if skipped_size:
        lines.append(f"1주 미달 스킵: {', '.join(skipped_size)}")
    if skipped_cash:
        lines.append(f"현금 부족 스킵: {', '.join(skipped_cash)}")
    # 주문이 0건인 날은 이유를 적어 준다. 기존 팩터 보유분이 자리와 현금을
    # 붙잡고 있는 동안은 실행 대상이 있어도 못 산다 — 고장이 아니라 설계다.
    if candidates and not order_results and legacy_positions:
        lines.append(f"ℹ️ 기존 팩터 보유 {len(legacy_positions)}종목이 자리·현금을 "
                     f"쥐고 있습니다. 소진되는 대로 플랜 주문이 나갑니다.")
    if dd_blocked:
        lines.append(f"⛔ 드로다운 *{dd_pct:.1f}%* → 신규 매수 중단")
    if n_errs:
        lines.append(f"오류 {n_errs}건 → Actions 로그 확인")

    # 플랜 트레이드 성적 — 백테스트의 +0.66R 과 **같은 단위**다. 아래 '시그널
    # 정확도'(%수익률)는 팩터 시절 기록이라 단위가 다르니 섞어 읽으면 안 된다.
    plan_perf = plan_trade_summary(_vstate["trades"])
    if plan_perf["n_resolved"] >= 3:
        lines.append(
            f"\n*플랜 성적* (결판 {plan_perf['n_resolved']}건 / 체결 {plan_perf['n_filled']}"
            f" / 셋업 {plan_perf['n_setups']})\n"
            f"  기대값 `{plan_perf['avg_r']:+.2f}R`  승률 {plan_perf['win_rate']:.0f}%  "
            f"체결률 {plan_perf['fill_rate']:.0f}%"
        )

    # 성과 지표
    if perf:
        lines.append(
            f"\n*성과 ({perf['n_days']}일)*\n"
            f"  수익률 {perf['total_return']:+.1f}%  SPY {perf['spy_return']:+.1f}%  "
            f"알파 {perf['alpha']:+.1f}%\n"
            f"  샤프 {perf['sharpe']:.2f}  최대낙폭 {perf['max_dd']:.1f}%"
        )

    if sl_summary["n"] >= 5:
        lines.append(
            f"\n*시그널 정확도* ({sl_summary['n']}건)\n"
            f"  승률 {sl_summary['win_rate']:.0f}%  "
            f"평균수익 {sl_summary['avg_return']:+.1f}%  "
            f"평균수익거래 {sl_summary['avg_win']:+.1f}%  "
            f"평균손실거래 {sl_summary['avg_loss']:+.1f}%"
        )

    msg = "\n".join(lines)
    print("\n" + "─"*50)
    print(msg)
    print("─"*50)
    send_tg(msg)
    print(f"\n완료. 청산 {len(_exited)}건 / 체결 {len(_filled)}건 / "
          f"신규 주문 {len(order_results)}건 / 오류 {n_errs}건")


if __name__ == "__main__":
    main()
