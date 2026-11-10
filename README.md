# Investment Research Engine

A research terminal for equities and prediction markets. Roughly 22,700 lines
of Python across 47 modules, with a command-line interface, a small web UI, and
258 tests that run offline.

[![tests](https://github.com/DWuuuuuu/investment-research-engine/actions/workflows/tests.yml/badge.svg)](https://github.com/DWuuuuuu/investment-research-engine/actions/workflows/tests.yml)

It covers US and A-share equities alongside public prediction markets in one
workflow, because the inputs overlap. A policy path inferred from Fed-funds
futures pricing is the same macro state a sector-rotation screen needs.

> **Status: working research code, published as-is.** I run it most days. It is
> not packaged for anyone else: interfaces change without notice, coverage is
> uneven across markets, and some modules are scaffolding I have not come back
> to. Read it as one analyst's tooling rather than as a product.

## What is in it

**Screening and valuation.** `screener.py`, `ashare_screener.py` and
`filter_screener.py` run cross-market screens. `valuation.py`, `rev_profit.py`,
`quarterly_growth.py` and `earnings.py` handle fundamentals and growth
decomposition. `peers.py` builds peer sets and scores their fit, which matters
more than it sounds: most screens fail because the comparison set is wrong.
`classify.py` holds the taxonomy.

**Macro and market state.** `macro_layer.py`, `cn_macro.py` and
`market_context.py` assemble the macro state. `fedwatch.py` and `fomc_path.py`
read policy-path pricing. `us_breadth.py`, `cn_limit.py`, `cn_vix.py` and
`cn_fear.py` cover breadth and sentiment. `cycle_assess.py` places the cycle;
`orderflow.py` and `divergence.py` look for disagreement between price and
internals.

**Prediction markets.** `clients.py` ingests Polymarket and Kalshi,
`db.py` normalises snapshots into SQLite, `radar.py` ranks markets by
probability change, and `cp3_distribution.py` handles distributional work. All
read-only: no wallet auth, no trading, no custody.

**Data and plumbing.** `cn_data.py`, `cn_reports.py`, `cn_forecasts.py`,
`price_history.py` and `fx_normalize.py` do ingestion and normalisation.
`signal_lab.py` is the experiment bench. `positions.py` runs a
candidate/watch/held/exited pipeline. `cli.py`, `web.py` and `static/` are the
interfaces.

## Quickstart

```bash
cp .env.example .env             # FRED_API_KEY and TUSHARE_TOKEN
pip install -e ".[dev]"          # pandas and requests, plus pytest
pytest                           # 253 pass, 5 skip, no credentials needed
python -m product.cli init
python -m product.cli ingest --source all --limit 200
python -m product.web            # local web UI
```

The test suite needs neither credentials nor vendor data, which is why it can
run in CI. The five skips cover paths that require a live API key.

## Design notes

**A thesis is a required field.** A name cannot enter the positions pipeline
without a written thesis, a target price with a stated basis, and a list of
disqualifiers fixed in advance. The disqualifiers do the real work: they are
what makes an exit reviewable afterwards instead of reconstructed.

**Snapshots are frozen at add time.** When a name enters the pipeline, the
factor values the screen saw are stored with it. Re-reading those months later
shows what the screen actually said at the time, not what it says now.

**Credentials come from the environment.** Every external call reads
`FRED_API_KEY` or `TUSHARE_TOKEN` from the environment. No tokens are in the
tree, and the `.gitignore` is a whitelist rather than a blacklist: it ignores
everything and opts specific paths in, because this working directory also
holds licensed vendor caches, a positions database and private notes.

**No vendor data ships.** The caches this runs against are licensed and not
mine to redistribute, so the repository carries code only.

## Absent by design

No trading, order management, broker or custody integration, wallet auth, or
personalised advice. This is a market-data and research workflow. Its output is
an input to a judgment, not a recommendation.

## Related

[quant-research-harness](https://github.com/DWuuuuuu/quant-research-harness):
the backtesting harness and indicator inventory the screens here are tested
against.

## License

MIT, see [LICENSE](LICENSE).
