# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A Korean quant trading system for US/KRX equities. It has two faces that share one codebase:

1. **Interactive Streamlit app** (`app.py`) — manual analysis, backtesting, and a quant dashboard.
2. **Headless scripts** run on a schedule by GitHub Actions — factor scans, Telegram signal alerts, paper trading, weekly IC weight updates, and daily P&L reports.

There is **no server deployment**. GitHub Actions *is* the production runtime: cron-driven scripts run, send Telegram messages, and commit their state (JSON logs, the PIT DB) back into the repo.

## Commands

Python **3.12** (`.python-version`, `runtime.txt`). Note the CI workflows are inconsistent — `ic-update.yml` uses 3.12, the others still pin 3.11.

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

**No unit-test framework is configured.** "Testing" here means statistical validation of the strategy, not pytest — it lives in `modules/` (`factor_validator.py`, `stat_validation.py`, `strategy_backtest.py`, `survivorship_check.py`, `stress_test.py`) and is surfaced through the app's 퀀트 → 고급 분석 / 운영 안전성 sub-tabs. `ic_weight_updater.py` is the headless entry point that drives `factor_validator` end-to-end.

## Architecture

### `app.py` is both the UI and the shared core library

`app.py` (~3,800 lines) is a monolith. Headless scripts import it as the core library — e.g. `signal_worker.py` does `import app as core` and calls `core.generate_system_signals(...)`, `core.calc_factor_scores_sectoral(...)`, `core.UNIVERSE_PRESETS`, `core.send_telegram(...)`. **Changing a function signature or constant in `app.py` can break the headless scripts even though they never touch the Streamlit UI.** Streamlit UI code lives inside `main()` (top tabs: `종목 분석`, `퀀트 · 자동매매`, `매매 일지`; the quant tab has 10 sub-tabs from 팩터 랭킹 to 세금 계산기). Everything above `main()` is reusable scoring/analysis logic.

### Two factor engines exist — keep them in sync deliberately

- `app.py`'s `calc_factor_scores` / `calc_factor_scores_sectoral` power the UI and `signal_worker.py`.
- `modules/factor_engine.py` is a **standalone, Streamlit-free** reimplementation (pure pandas/numpy: 5 factors + ICT + regime detection) used by the paper-trade runners and `factor_validator.py`.

These are parallel implementations, not one calling the other. A scoring change often needs to be applied in both places.

### `modules/` — optional quant library loaded defensively

`app.py` imports every module inside `try/except`, setting `_*_AVAILABLE` flags (e.g. `_ML_AVAILABLE`, `_PIT_AVAILABLE`, `_TAX_KR_AVAILABLE`). The app degrades gracefully when a module or its dependency is missing, so features are guarded by these flags. Key modules: `factor_engine`, `factor_validator`, `ict_analysis`, `ml_signals`, `risk_management`, `portfolio_allocator`, `ops_safety` (kill switch / reconciliation), `pit_data_logger` (point-in-time fundamentals DB), `tax_kr`, `toss_trading` (current broker).

### The weekly IC feedback loop

`REGIME_WEIGHTS` in `factor_engine.py` are only a weak prior. Every Sunday `ic_weight_updater.py` computes 5-year walk-forward Information Coefficients and writes `ic_weights.json`; the factor engines read that file to rescale factor weights (low/negative-IC factors shrink automatically, floored at `IC_FLOOR`). Don't hand-tune the weights expecting them to stick — this job overwrites them.

### Config is entirely env-var driven; state is committed JSON

There is no config file — everything comes from environment variables, supplied as GitHub Actions **secrets**: `TOSS_CLIENT_ID/SECRET/ACCOUNT_SEQ`, `TELEGRAM_TOKEN/CHAT_ID`, `DART_API_KEY`, `ALPACA_API_KEY/SECRET_KEY` (legacy), plus an Anthropic key (news sentiment) and Google service-account creds (gspread trade journal). Behavior knobs (`UNIVERSE`, `TOP_N`, `DRY_RUN`, `BUY_SCORE_MIN`, regime position caps, `TRAIL_STOP_PCT`, `PORTFOLIO_DD_STOP_PCT`, …) are also env vars — see the `workflow_dispatch` inputs in `.github/workflows/` for the authoritative list and defaults.

Runtime **state lives in version-controlled files** that the workflows `git commit` back after each run: `signal_log.json` (buy signals; 21-day forward returns tracked automatically), `equity_log.json`, `peak_prices.json` (trailing-stop reference), `ic_weights.json`, and `pit_fundamentals.db`. Treat edits to these as data changes. DART caches (`dart_fund_cache.json`, `dart_corp_map.json`) and `.env` are gitignored.

### GitHub Actions workflows (`.github/workflows/`)

Daily cron sequence (UTC, weekdays): `signal-alerts.yml` (22:30) scans and alerts, `daily-report.yml` (23:30 → KST 08:30) sends the Toss P&L brief. `ic-update.yml` runs Sundays. **`paper-trade-us.yml`'s cron is intentionally disabled — the system currently runs in signal-only mode (alerts, no automated orders); `signal-alerts.yml` is the active scanner.** Workflows that produce state use `concurrency` groups and `git pull --rebase` before pushing to avoid clobbering each other's commits.
