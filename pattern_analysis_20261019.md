# Backtest pattern analysis — Oct 19, 2026 (hours 06:00-08:00 UTC)

Sample: 26 call rows (22 unique mints) replayed against PumpAPI hourly tape.
Engine: replay_backtest.py — MC-adaptive ladder, 50% clip at TP1, TP2, hard stop,
1.5s latency, 3ms Jito bundles, 3% sell fee, migration-artifact guard.

## Outcome mix
- TP2 winners: 8 (31%)  -> +14% avg (clip+remainder)
- STOP:        13 (50%) -> -18% avg
- OPEN (2h):   5 (19%)  -> -5% avg (flat/dead)
- Baseline avg per call: -5.7%

## Winner vs loser vs dead — tape shape (first 60s after entry)
metric        TP2(8)   STOP(13)  OPEN(5)
push60       1.338    1.099     1.038
b60:s60 ratio ~1.6:1  ~1.2:1    ~1:2
net60 (SOL)  +11.6    +64.5*    dead/neg  (*skewed by COIN whale buy; med negative)
act60        174      248       6  <-- DEAD TOKENS HAVE <10 TRADES IN 60s
exit median  58s      47s       (held 2h)

## Channel separation (the dominant edge)
soltrenchtrending     n=13  wins 7  stops 2  winrate 54%  avg +3.2%
signalsolanaby4am     n= 3  wins 1  stops 2  winrate 33%  avg -7.3%
pirbviewtrending      n= 3  wins 0  stops 2  winrate  0%  avg -13.7%
solearlytrending      n= 6  wins 0  stops 6  winrate  0%  avg -18.0%  <- stop factory
solwhaletrending      n= 1  wins 0  stops 1  winrate  0%  avg -18.0%

## Strategy variants (2h, 26 calls)
baseline (buy all)                    avg -5.7%
drop solearlytrending                 avg -1.9%
drop solearly+pirb+solwhale           avg +1.2%
only soltrenchtrending                avg +3.2%
drop dead tape (act60<10)             avg -5.2%
drop dead + drop 3 losers             avg +2.7%
momentum push60>=1.15                 avg -0.2%

## Key findings
1. Channel quality is the single biggest edge. solearlytrending (0/6 stops)
   and pirbviewtrending/solwhaletrending (0/4 combined) are negative EV.
   soltrenchtrending alone flips the book positive.
2. Dead-tape entries (act60 < 10 trades) go nowhere — 5 of 26 calls, all OPEN/flat.
   A 60s post-entry activity check would skip them.
3. Winners push >=1.3x within 60s with buy:sell ~1.6:1. Losers push ~1.1x.
   (Momentum filter alone is weak; channel filter is stronger.)
4. Exits are FAST: TP2 median 58s, STOP median 47s. The 2h horizon is almost
   never used — everything resolves in the first minutes. Quick-flip thesis holds.
5. "Confluence" trap: the 3 SPSI calls in the window were 3 DIFFERENT mints —
   channels all said "SPSI" but signaled different contracts (ticker squatting).
   All-time, 121/889 symbols map to >1 mint. Only MINT-level confluence is real;
   symbol-level "confluence" is noise/dangerous.

## Caveats (n=26 is small)
- Channel ranking is directional, not proof. solearlytrending 0/6 could regress.
- net60 for STOP group is skewed by a whale buy on COIN (872 SOL in, still stopped).
- Recommend expanding to 12-24h before hard-cutting any channel.
