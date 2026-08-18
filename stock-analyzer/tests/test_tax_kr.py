"""국내(KRX) 세금 계산 테스트."""
from modules.tax_kr import (
    calc_domestic_transaction_tax,
    estimate_krx_annual_tax,
    DOMESTIC_TX_TAX_RATE,
)


def test_kospi_transaction_tax_default_rate():
    r = calc_domestic_transaction_tax(10_000_000, 'KOSPI')
    assert r['transaction_tax_krw'] == 18000  # 0.18%
    assert r['tax_rate_pct'] == DOMESTIC_TX_TAX_RATE['KOSPI']


def test_kosdaq_transaction_tax_and_case_insensitive_market():
    r = calc_domestic_transaction_tax(5_000_000, 'kosdaq')
    assert r['market'] == 'KOSDAQ'
    assert r['transaction_tax_krw'] == 9000


def test_rate_override_takes_precedence():
    r = calc_domestic_transaction_tax(10_000_000, 'KOSPI', rate_pct=0.15)
    assert r['transaction_tax_krw'] == 15000


def test_negative_proceeds_floored_to_zero():
    assert calc_domestic_transaction_tax(-100, 'KOSPI')['transaction_tax_krw'] == 0


def test_unknown_market_falls_back_to_default_rate():
    r = calc_domestic_transaction_tax(1_000_000, 'NASDAQ')
    assert r['tax_rate_pct'] == 0.18  # 기본값


def test_annual_aggregate_sums_transaction_tax():
    agg = estimate_krx_annual_tax([
        {'sell_proceeds_krw': 10_000_000, 'market': 'KOSPI'},
        {'sell_proceeds_krw': 5_000_000, 'market': 'KOSDAQ'},
    ])
    assert agg['n_sells'] == 2
    assert agg['total_transaction_tax_krw'] == 27000
    assert len(agg['breakdown']) == 2
