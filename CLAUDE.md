# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A Korean quant trading system for US/KRX equities. It has two faces that share one codebase:

1. **Interactive Streamlit app** (`app.py`) — manual analysis, backtesting, and a quant dashboard.
2. **Headless scripts** run on a schedule by GitHub Actions — factor scans, Telegram signal alerts, paper trading, weekly IC weight updates, and daily P&L reports.

There is **no server deployment**. GitHub Actions *is* the production runtime: cron-driven scripts run, send Telegram messages, and commit their state (JSON logs, the PIT DB) back into the repo.

## Commands

Python **3.12** (`.python-version`, `runtime.txt`). All CI workflows and the devcontainer pin 3.12.

```bash
pip install -r requirements.txt

# Run the interactive dashboard
streamlit run app.py

# Headless scripts (all read config from env vars — see below)
python signal_worker.py            # scan universe → Telegram alert → append signal_log.json
python ic_weight_updater.py        # weekly: recompute IC factor weights → ic_weights.json (~10-60 min)
python daily_report_toss.py        # Toss P&L report → Telegram
python paper_trade_runner_toss.py  # current broker path; set DRY_RUN=true to avoid live orders
```

**Two layers of testing exist.** (1) A **pytest suite** (`tests/`, ~132 tests across `test_bulls_signals`, `test_edgar_fundamentals`, `test_factor_formulas`, `test_factor_scores`, `test_ic_weights`, `test_price_panel`, `test_system_signals`, `test_tax_kr`, `test_universe`, `test_smoke`) runs fast and network-free; `ci.yml` gates every push to `main` and every PR with `ruff check .` + `pytest tests/`. (2) **Statistical validation** of the strategy lives in `modules/` (`factor_validator.py`, `stat_validation.py`, `strategy_backtest.py`, `survivorship_check.py`, `stress_test.py`) and is surfaced through the app's 퀀트 → 고급 분석 / 운영 안전성 sub-tabs. `ic_weight_updater.py` is the headless entry point that drives `factor_validator` end-to-end. Add a pytest test when you touch pure logic in `modules/`; keep them network-free so CI stays green.

## Architecture

### `app.py` is both the UI and the shared core library

`app.py` (~7,590 lines) is a monolith, now being incrementally unwound. **The extraction pattern:** move the logic to a Streamlit-free `modules/` file that takes its price-fetching and indicator functions as injected arguments (`app.py` imports `modules/`, so the reverse would be circular), then leave a thin delegating wrapper in `app.py` keeping the original signature so headless callers don't change. `generate_system_signals` → `modules/signal_engine.py` was the first one done this way; pin behavior with tests *before* extracting, then verify the tests pass unchanged.

 Headless scripts import it as the core library — e.g. `signal_worker.py` does `import app as core` and calls `core.generate_system_signals(...)`, `core.calc_factor_scores_sectoral(...)`, `core.UNIVERSE_PRESETS`, `core.send_telegram(...)`. **Changing a function signature or constant in `app.py` can break the headless scripts even though they never touch the Streamlit UI.** Streamlit UI code lives inside `main()` (top tabs: `종목 분석`, `퀀트 · 자동매매`, `매매 일지`; the quant tab has 10 sub-tabs from 팩터 랭킹 to 세금 계산기). Everything above `main()` is reusable scoring/analysis logic.

### Two factor engines exist — parallel, but the raw blends are now shared

- `app.py`'s `calc_factor_scores` / `calc_factor_scores_sectoral` power the UI and `signal_worker.py`. `calc_factor_scores` has **three** network seams, and the third is easy to miss: `download_stock(tk, …)`, `yf.Ticker(tk).info`, and `_load_ic_factor_weights_4f()` — which calls `get_market_regime()`, which downloads SPY. Any test or offline caller must stub all three; `tests/test_factor_scores.py`'s `patch_market` fixture is the reference for how.
- `modules/factor_engine.py` is a **standalone, Streamlit-free** reimplementation (pure pandas/numpy: 5 factors + ICT + regime detection) used by the paper-trade runners and `factor_validator.py`.

These remain parallel implementations — different factor sets (app: skip-1M momentum + optional analyst/short/EPS-surprise extras; engine: mom_3m/mom_1m + regime weights) and different normalization (app: `_zscore_to_score`; engine: z-score → 10–90 min-max). One does not call the other, so momentum / low-vol / weighting changes still need applying in both places.

**What is no longer duplicated:** the value and quality *raw blends* (EP 40 / BP 30 / FCF 30; ROE 45 / margin 35 / accrual 20), the PER/PBR→yield conversion, and the accrual-quality rule now live only in `modules/factor_formulas.py` — a dependency-free pure-arithmetic module that works on scalars and Series alike. All three former copies (`calc_factor_scores`, `calc_factor_scores_sectoral`, `factor_engine.calc_factor_scores`) import it. Change a coefficient **there and only there**. `tests/test_factor_formulas.py` pins the coefficients and fails CI if either engine reintroduces an inline copy.

### `modules/` — optional quant library loaded defensively

`app.py` imports every module inside `try/except`, setting `_*_AVAILABLE` flags (e.g. `_ML_AVAILABLE`, `_DART_AVAILABLE`, `_TAX_KR_AVAILABLE`). The app degrades gracefully when a module or its dependency is missing, so features are guarded by these flags. Key modules: `factor_engine`, `factor_validator`, `ict_analysis`, `ml_signals`, `risk_management`, `portfolio_allocator`, `ops_safety` (kill switch / reconciliation), `tax_kr`, `toss_trading` (current broker). Point-in-time fundamentals discipline lives in `factor_engine.point_in_time_fundamentals` (EDGAR `filed`-indexed panel), not a separate DB.

### The weekly IC feedback loop

`REGIME_WEIGHTS` in `factor_engine.py` are only a weak prior. Every Sunday `ic_weight_updater.py` computes 5-year walk-forward Information Coefficients and writes `ic_weights.json`; the factor engines read that file to rescale factor weights (low/negative-IC factors shrink automatically, floored at `IC_FLOOR`). Don't hand-tune the weights expecting them to stick — this job overwrites them.

### Config is entirely env-var driven; state is committed JSON

There is no config file — everything comes from environment variables, supplied as GitHub Actions **secrets**: `TOSS_CLIENT_ID/SECRET/ACCOUNT_SEQ`, `TELEGRAM_TOKEN/CHAT_ID`, `DART_API_KEY`, `ALPACA_API_KEY/SECRET_KEY` (legacy), plus an Anthropic key (news sentiment) and Google service-account creds (gspread trade journal). Behavior knobs (`UNIVERSE`, `TOP_N`, `DRY_RUN`, `BUY_SCORE_MIN`, regime position caps, `TRAIL_STOP_PCT`, `PORTFOLIO_DD_STOP_PCT`, …) are also env vars — see the `workflow_dispatch` inputs in `.github/workflows/` for the authoritative list and defaults.

Runtime **state lives in version-controlled files** that the workflows `git commit` back after each run: `signal_log.json` (buy signals; 21-day forward returns tracked automatically), `equity_log.json`, `peak_prices.json` (trailing-stop reference), and `ic_weights.json`. Treat edits to these as data changes. DART caches (`dart_fund_cache.json`, `dart_corp_map.json`) and `.env` are gitignored.

### GitHub Actions workflows (`.github/workflows/`)

**Currently active crons:** `daily-report.yml` (23:30 UTC weekdays → KST 08:30, Toss P&L brief) and `ic-update.yml` (Sundays 14:00 UTC, weekly IC weights). `ci.yml` runs ruff+pytest on every push to `main` and PR. **Both `signal-alerts.yml` (scanner, was 22:30) and `paper-trade-us.yml` (orders, was 21:30) have their `schedule:` cron commented out — so the repo currently runs NO automated scan or trade on a timer; trigger them manually via `workflow_dispatch` when needed.** This is the intentional signal-only / no-live-order pause (see also `DRY_RUN=true` default). Note the asymmetry: `daily-report` still fires on cron while the scanner does not, so the daily brief can reflect stale/empty signals until `signal-alerts` is re-enabled. Workflows that produce state use `concurrency` groups and `git pull --rebase` before pushing to avoid clobbering each other's commits.
