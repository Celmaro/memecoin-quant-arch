# BLIX Radar Engine Thresholds (engine.js) — REA deep-dive

Source of truth: `server/engine.js` cross-checked against a live settings dump and live-state fixture. REA Evidence: `ev_f34217f8fe8a858272fabb362cfb1071b616051067d6ec34686b0500b0eea2c4`.

## Admission / nursery
- `engine.js:262` — a coin is tracked when `mcap >= nurseryMinMcap (12000) OR vol >= nurseryMinVol5m (3000)`. OR-condition, so either gate alone admits.
- `engine.js:441-447` — `maxTracked (900)` cap; eviction candidates ranked by `vol_h1 + 12*vol_m5` ascending, only coins older than 10 min (30 min window cap on the query), lowest score first.

## Alert rules (engine.js:619-626)
- **launch**: age < 2h, `score >= launchScore (62)`, mcap >= 35000, vol_h1 >= 40000, buyShare > 0.52, 24h cooldown per mint.
- **volume**: age >= 15min, mcap >= 30000, `vol_m5 >= spikeMinVol (8000)`, vol_m5*12 > 3*max(vol_h1,1), chg_m5 > 0, 1h cooldown.
- **momentum**: `score >= momentumScore (72)`, chg_h1 >= 10, 3h cooldown.
- Hard-coded 35000/40000/30000 mcap & vol floors are NOT in settings — they only appear in source, so operators tuning settings cannot change them.

## Safety / exit
- `engine.js:562` — `verdict()` uses `minSafety (60)`, `minExitLiq (1000)`, and dev launch count.
- `engine.js:334-337` — rug/liq-pulled detection: flag when liquidity < minExitLiq and tracked prior liquidity >= 5 * minExitLiq (non-pump-curve dexes). `exitState` (678) and `floor` (696) also key off minExitLiq.
- `engine.js:757` — buy rejection when safety_score < minSafety.

## Issues
- The hard-coded mcap/vol floors inside the launch/volume rules duplicate nursery intent but bypass settings; recommend hoisting into settings.js if tunability is intended.
- No `radar`-named file exists in the repo; the radar engine logic lives in `server/engine.js` + `bin/supervise.mjs` (verified via ripgrep).

(Note: this child pivoted to engine.js thresholds; the holder-forensics tags — dev/sniper/bundle in snipe.js: bundlers = non-dev buyers in the launch slot, snipers = first-buy in next 2 slots; dev-launch 6h window ≥4 = serial launcher gate in quality.js:86 — are covered there and in 05.)
