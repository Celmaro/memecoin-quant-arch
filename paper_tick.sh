#!/bin/bash
cd /home/agentuser/memecoin-quant
# Paper-tick: mark to market + execute plan. Only print actionable lines + equity.
OUT=$(timeout 90 python3 paper_tick.py 2>&1)
echo "$OUT" | grep -E "STOP|EXIT|CLIP|Paper account|BUY|no market" || true
# auto-buy new calls: hook into crawler output via calls.jsonl delta is complex in bash;
# new-call buys are handled by the agent on alert. This tick only manages exits.
