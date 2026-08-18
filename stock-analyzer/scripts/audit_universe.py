"""
app.py의 'S&P 500 전체' 프리셋에서 데이터가 안 나오는 티커를 찾아낸다.
modules/universe.py의 SP500 목록을 확정할 때 1회 실행하는 도구.

  python scripts/audit_universe.py
"""
import sys
from datetime import datetime, timedelta

sys.path.insert(0, ".")
import yfinance as yf


def main():
    import app  # noqa: E402 — Streamlit 앱이지만 import 시 UI는 뜨지 않는다
    tickers = app.UNIVERSE_PRESETS["S&P 500 전체 (500종목)"]
    print(f"프리셋 티커 {len(tickers)}개 (중복 제거 후 {len(set(tickers))}개) 조회 중...")

    dupes = sorted({t for t in tickers if tickers.count(t) > 1})
    if dupes:
        print(f"중복 티커 {len(dupes)}개: {', '.join(dupes)}")

    uniq  = list(dict.fromkeys(tickers))
    end   = datetime.now()
    start = end - timedelta(days=400)
    raw   = yf.download(uniq, start=start, end=end,
                        progress=False, auto_adjust=True, threads=True)

    close = raw["Close"] if "Close" in raw.columns.get_level_values(0) else raw
    dead  = [t for t in uniq if t not in close.columns or close[t].dropna().empty]

    print(f"\n응답 없는 티커 {len(dead)}개:")
    for t in dead:
        print(f"  {t}")
    print("\n이 목록을 근거로 modules/universe.py의 SP500을 확정할 것.")


if __name__ == "__main__":
    main()
