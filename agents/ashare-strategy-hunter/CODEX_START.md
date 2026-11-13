# Codex execution task — A-Share Strategy Hunter

Work in this repository on branch `feature/ashare-strategy-hunter-agent`. Read `agents/ashare-strategy-hunter/AGENTS.md` as the binding requirements. Build the Python service under `agents/ashare-strategy-hunter/` without changing existing trading systems or leaking secrets.

Immediately inspect repo conventions, write a scoped implementation plan, then implement and test all five phases: GitHub discovery, repository and strategy classification/security, A-share-compatible backtesting, independent Star TOP5 and verified A-share TOP5, reporting + PushPlus in DRY_RUN, GitHub Actions schedules. Include workflows and fixtures so first full scan is runnable with GitHub Actions GITHUB_TOKEN even without Tushare and PushPlus. Mark real-market-data validation and live notifications BLOCKED until secrets/data and test confirmation exist.

Use Asia/Shanghai schedule: daily 08:00, Sunday 20:00, Monday 08:30, first Sunday monthly 20:30. Ensure proper UTC conversion and avoid scheduling every monthly Sunday by accident. Never automatically submit real trade orders. Untrusted repo code must not run outside an isolated, restricted environment. Keep clear status, unit tests, and example reports. Start coding now; do not stop after planning.
