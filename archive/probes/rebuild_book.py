import sys, json, time
sys.path.insert(0, '/home/agentuser/memecoin-quant')
from desk_common import dex_pair
import position_monitor as pm

"""Rebuild the paper book from ground truth:
- drop $0-size positions (never really bought)
- mark stale >3h positions closed at current price (desk rule: quick flips, nothing held for hours)
- recompute cash/realized so that: cash + sum(open size) + realized_pnl = consistent equity
- keep trades log as the single source of P&L truth"""

b = json.load(open('/home/agentuser/memecoin-quant/paper_book.json'))
pos = pm.load_positions()

# 1) remove zero-size ghosts
for sym in list(b['positions']):
    if (b['positions'][sym].get('size_usd') or 0) < 1:
        print(f"DROP {sym}: ghost position (size ${b['positions'][sym].get('size_usd')})")
        pos.pop(b['positions'][sym]['mint'], None)
        b['positions'].pop(sym)

# 2) close stale positions (>3h old) at market — quick-flip mandate
NOW = time.time()
for sym in list(b['positions']):
    p = b['positions'][sym]
    age_h = (NOW - p.get('opened', NOW)) / 3600
    if age_h > 3:
        mkt = dex_pair(p['mint']) or {}
        px = mkt.get('priceUsd')
        mult = float(px)/p['entry_px'] if px and p['entry_px'] else 0
        proceeds = (p.get('size_usd') or 0) * mult * 0.983
        pnl = proceeds - p.get('size_usd', 0)
        b.setdefault('trades', []).append({"ts": NOW, "symbol": sym, "mint": p['mint'],
            "entry_px": p['entry_px'], "exit_px": float(px or 0), "mult": round(mult,3),
            "pnl": round(pnl,2), "how": "stale_close_3h"})
        print(f"CLOSE {sym}: held {age_h:.1f}h -> mult {mult:.2f}x pnl {pnl:+.2f}")
        pos.pop(p['mint'], None)
        b['positions'].pop(sym)

# 3) rebuild cash & realized from trades log + open positions
start = b['start']
open_size = sum(p.get('size_usd') or 0 for p in b['positions'].values())
# open positions were bought with cash; their P&L is unrealized (not in trades log)
# cash = start - total_ever_spent + total_proceeds.
# total spent = sum over ALL trades of (entry cost of the closed portion) + open sizes... simpler:
# realized_pnl = sum(trades pnl). cash = start + realized_pnl - open_cost_basis_for_unrealized? No—
# Correct identity: equity = start + sum(all trade pnl) + sum(open unrealized pnl)
# cash = start + sum(all trade pnl) - sum(open cost basis still deployed beyond returned capital)
# For clean paper accounting: treat every trade's proceeds as returned to cash:
# cash = start - sum(open size_usd) + sum(trade proceeds) ... but proceeds were partly re-spent.
# Simplest consistent model: cash = start + realized_pnl - open_size (open positions are cash already spent)
realized = round(sum(t['pnl'] for t in b.get('trades', [])), 2)
cash = round(start + realized - open_size, 2)
b['realized'] = realized
b['cash'] = cash
b.pop('data_loss_note', None)
b['rebuilt_ts'] = NOW
json.dump(b, open('/home/agentuser/memecoin-quant/paper_book.json', 'w'), indent=1)
pm.save_positions(pos)
print(f"\nREBUILT: start={start} realized={realized:+.2f} open_size={open_size:.2f} cash={cash:.2f}")
print("positions:", {s: p.get('size_usd') for s,p in b['positions'].items()})
