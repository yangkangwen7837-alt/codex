# A-Share Strategy Hunter — Codex Agent

## Objective
Build an auditable GitHub strategy discovery, A-share suitability, backtesting, ranking and PushPlus reporting system. Do not place orders. Never invent GitHub stars, returns or backtests.

## Schedule (Asia/Shanghai)
- Every day 08:00: Discover GitHub strategy repositories, update stars and changes.
- Sunday 20:00: Review strategy code and A-share compatibility.
- Monday 08:30: Publish two independent TOP5 rankings and PushPlus report.
- First Sunday of each month 20:30: Recheck out-of-sample performance and strategy decay.
- Support manual workflow_dispatch and DRY_RUN. GitHub Actions scheduled execution may be delayed. Convert timezone to UTC carefully.

## Repository discovery
Search GitHub REST repository API with paginated queries: A-share strategy, China stock quant, 股票选股回测, qlib strategy, rqalpha strategy, backtrader stock, alpha factors, momentum, mean reversion, multi-factor. Track query coverage, errors, paging, search result caps and API throttling. Persist daily stars, 7/30-day star delta, last commit, commit SHA, license, repository URL, fork/archived status and maintenance. Do not claim global exhaustive coverage.

## Classification and safety
- A: executable buy/sell strategy; B: multi-strategy repository (extract each identifiable strategy as separate strategy_id); C: infrastructure/data/backtest engine (exclude from trade strategy ranks); D: unverifiable claim (watchlist only).
- Detect forks, copied and near-identical rules. Keep provenance and license.
- Treat third-party code as untrusted. Static review first, sandbox with no credentials/network/order routing, pinned commit/dependencies and CPU/time/storage limits. Do not run unreviewed scripts.

## Chinese stock-market correctness
- Model cash equity T+1, suspend/resume, price limits, limit-up/down unfilled orders, lot sizes, ST, IPO/board-specific historical rules, delistings, corporate actions, commissions, stamp duty, slippage and market impact using historically applicable dates.
- Avoid future leakage, look-ahead corporate reporting and survivorship bias. Never fill using future prices or unavailable close values.
- Record data provenance, cutoff dates, field validity, adjusted-price method. Integrate legitimate Tushare/AKShare sources only when configured; avoid committing credentials.

## Validation
- Common strategy signal interface, unified execution and cost models.
- Rolling walk-forward, isolated validation/OOS, ≥3 regime slices, benchmark CSI300/500/1000, parameter perturbation and transaction-cost sensitivity; preserve strictly held-out evaluation period.
- Report CAGR, Sharpe, Sortino, max drawdown, Calmar, win-rate, payoff ratio, sample trades, turnover, excess returns, estimated capacity and confidence intervals.
- Mark insufficient-data or unreplicable cases UNVERIFIED; no fabricated metric or guaranteed win rate.

## Two distinct rankings
1. HOT_REPO_TOP5: eligible strategy repositories by GitHub star count descending (repo stars, not an alleged strategy star count).
2. VERIFIED_ASHARE_TOP5: independently verified strategy IDs ranked by score: A-share suitability 25, OOS risk-adjusted return 25, drawdown/robustness 20, GitHub activity 15, regime fit 15.
3. WATCHLIST_TOP5: promising but unverified with clear warnings.
Do not fill a ranking to five when fewer qualify. Include explicit rank changes, version and reasons.

## Output and delivery
Produce historical records and HTML/Markdown report including date, sources, universe coverage, each repo URL, stars/delta, strategy description, execution adaptations, validation status, out-of-sample period, metrics, risks, rank and trend. PushPlus integration reads PUSHPLUS_TOKEN from GitHub Actions Secrets exclusively, never prints secrets; sending is disabled in DRY_RUN. GitHub token via Actions environment. Keep reports/artifacts. Add failure alerts and retry guards; do not duplicate push sends.

## Implementation sequence
1. Repository discovery + persistence + mocked tests.
2. Classification/security audit.
3. A-share adapter + backtesting + leakage/cost tests.
4. Scoring + reports + PushPlus DRY_RUN.
5. GitHub Actions schedules, CI, deployment instructions, end-to-end smoke tests.
Implement actual code and tests, rather than a README-only design. Keep STATUS.md with PASS/PARTIAL/BLOCKED and evidence; do not claim a test ran unless executed. Where data or tokens are missing, preserve an executable simulation path and document exact secret setup steps.
