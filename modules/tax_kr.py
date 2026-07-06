"""
한국 거주자 해외주식 양도소득세 계산
=================================================================
2024년 세법 기준:
  - 양도소득세율: 22% (지방소득세 포함, 20% 국세 + 2% 지방)
  - 연간 기본공제: 250만 원 (금융투자소득세 아직 미시행 → 현행 양도세 기준)
  - FIFO(선입선출) 원가 계산
  - 환율: 취득시 기준환율 / 양도시 기준환율

⚠️ 이 모듈은 교육·참고 목적이며, 세법 해석은 세무사에게 확인하십시오.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class Lot:
    """한 번의 매수로 취득한 주식 묶음."""
    ticker: str
    buy_date: date
    qty: float
    buy_price_usd: float
    buy_fx_rate: float

    def __post_init__(self):
        self.buy_cost_krw: float = self.qty * self.buy_price_usd * self.buy_fx_rate

    @property
    def unit_cost_krw(self) -> float:
        return self.buy_price_usd * self.buy_fx_rate


@dataclass
class RealizedTrade:
    ticker: str
    sell_date: date
    qty: float
    sell_price_usd: float
    sell_fx_rate: float
    buy_lots: list             # 차감된 Lot들 (FIFO)
    gain_krw: float            # 양도차익(원), 음수면 손실
    buy_year: int
    sell_year: int


class OverseasStockLedger:
    """FIFO 방식으로 해외주식 매매 내역을 추적하고 양도차익을 계산."""

    def __init__(self):
        self._lots: dict[str, list[Lot]] = {}    # ticker → Lot 리스트
        self.realized: list[RealizedTrade] = []

    def buy(self, ticker: str, buy_date: date, qty: float,
            price_usd: float, fx_rate: float):
        lot = Lot(ticker, buy_date, qty, price_usd, fx_rate)
        self._lots.setdefault(ticker, []).append(lot)

    def sell(self, ticker: str, sell_date: date, qty: float,
             price_usd: float, fx_rate: float):
        """FIFO로 원가를 소진하고 RealizedTrade를 기록."""
        remaining = float(qty)
        lots_queue = self._lots.get(ticker, [])

        # 과매도 사전 검증 (뮤테이션 전)
        total_held = sum(l.qty for l in lots_queue)
        if remaining > total_held + 1e-9:
            raise ValueError(
                f"{ticker} 매도 수량({qty:.4f})이 보유량({total_held:.4f})을 초과합니다"
            )

        used_lots = []
        total_cost_krw = 0.0

        i = 0
        while remaining > 1e-9 and i < len(lots_queue):
            lot = lots_queue[i]
            take = min(lot.qty, remaining)
            cost = take * lot.unit_cost_krw
            total_cost_krw += cost
            used_lots.append({'lot': lot, 'qty_used': take, 'cost_krw': cost})
            lot.qty -= take
            remaining -= take
            if lot.qty < 1e-9:
                i += 1

        lots_queue[:] = [l for l in lots_queue[i:] if l.qty > 1e-9]

        sell_proceeds_krw = qty * price_usd * fx_rate
        gain = sell_proceeds_krw - total_cost_krw
        self.realized.append(RealizedTrade(
            ticker=ticker, sell_date=sell_date, qty=qty,
            sell_price_usd=price_usd, sell_fx_rate=fx_rate,
            buy_lots=used_lots, gain_krw=round(gain),
            buy_year=used_lots[0]['lot'].buy_date.year if used_lots else sell_date.year,
            sell_year=sell_date.year,
        ))

    def holdings(self) -> dict:
        """현재 보유 중인 롯 요약."""
        result = {}
        for ticker, lots in self._lots.items():
            total_qty = sum(l.qty for l in lots)
            if total_qty > 1e-9:
                avg_cost = sum(l.qty * l.unit_cost_krw for l in lots) / total_qty
                result[ticker] = {
                    'qty': round(total_qty, 4),
                    'avg_cost_krw': round(avg_cost),
                    'total_cost_krw': round(total_qty * avg_cost),
                }
        return result


def calc_capital_gains_tax(realized_trades: list, year: int,
                            basic_deduction_krw: int = 2_500_000,
                            tax_rate: float = 0.22) -> dict:
    """
    특정 연도의 양도소득세를 계산.
    손익 통산: 여러 종목의 이익/손실을 합산.
    기본공제(250만 원) 적용 후 세율 22%.
    """
    annual_trades = [t for t in realized_trades if t.sell_year == year]
    total_gain = sum(t.gain_krw for t in annual_trades)
    taxable_gain = max(0, total_gain - basic_deduction_krw)
    tax_amount = round(taxable_gain * tax_rate)

    return {
        'year': year,
        'n_trades': len(annual_trades),
        'gross_gain_krw': round(total_gain),
        'basic_deduction_krw': basic_deduction_krw,
        'taxable_gain_krw': round(taxable_gain),
        'tax_rate_pct': tax_rate * 100,
        'estimated_tax_krw': tax_amount,
        'note': (f"{year}년 양도차익 {total_gain:,}원 - 기본공제 {basic_deduction_krw:,}원 = "
                 f"과세표준 {taxable_gain:,}원 × {tax_rate*100:.0f}% = 납부세액 {tax_amount:,}원 (추정). "
                 "매매 비용(수수료, 증권거래세) 공제 전 개략치. 세무사 최종 확인 필수."),
    }


def suggest_year_end_tax_loss_harvesting(current_unrealized: dict,
                                          target_year_gain_krw: int,
                                          basic_deduction_krw: int = 2_500_000) -> dict:
    """
    연말 절세 전략: 현재 미실현 손실 종목 중 어떤 걸 팔아
    올해 양도차익을 기본공제(250만 원) 이하로 줄일 수 있는지 제안.

    current_unrealized: {'TSLA': {'unrealized_gain_krw': -1_500_000, 'qty': 10}, ...}
    target_year_gain_krw: 현재까지 올해 확정된 양도차익 합계(원)
    """
    loss_candidates = sorted(
        [(ticker, info) for ticker, info in current_unrealized.items()
         if info['unrealized_gain_krw'] < 0],
        key=lambda x: x[1]['unrealized_gain_krw']
    )
    target_reduction = max(0, target_year_gain_krw - basic_deduction_krw)
    suggestions, accumulated_reduction = [], 0

    for ticker, info in loss_candidates:
        if accumulated_reduction >= target_reduction:
            break
        loss = abs(info['unrealized_gain_krw'])
        suggestions.append({
            'ticker': ticker, 'unrealized_loss_krw': info['unrealized_gain_krw'],
            'qty': info['qty'], '절세효과_원': round(min(loss, target_reduction - accumulated_reduction) * 0.22),
        })
        accumulated_reduction += loss

    remaining_taxable = max(0, target_year_gain_krw - accumulated_reduction - basic_deduction_krw)
    return {
        'current_year_gain_krw': target_year_gain_krw,
        'target_reduction_krw': target_reduction,
        'harvesting_suggestions': suggestions,
        'estimated_remaining_taxable_krw': round(remaining_taxable),
        'estimated_tax_saved_krw': round(min(accumulated_reduction, target_reduction) * 0.22),
        'warning': "손실 실현 후 재매수 시 wash-sale 규정은 미국 세법 기준이며, 한국세법에는 별도 규정 없음. 단 단기 재매수는 경제적 실질 측면에서 신중히 판단 필요.",
    }
