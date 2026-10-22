import sys, json, time
sys.path.insert(0, '/home/agentuser/memecoin-quant')
from desk_common import dex_pair
import position_monitor as pm

b = json.load(open('/home/agentuser/memecoin-quant/paper_book.json'))
pos = pm.load_positions()
cash = b.get('cash', 0.0)
realized = b.get('realized', 0.0)
total = 0.0
for sym, p in list(b['positions'].items()):
    mkt = dex_pair(p['mint']) or {}
    px = mkt.get('priceUsd')
    if px:
        proceeds = p['size_usd'] * float(px) / p['entry_px'] * 0.983  # fee adj
        mult = float(px) / p['entry_px']
    else:
        proceeds, mult = 0.0, 0.0
    pnl = proceeds - p['size_usd']
    cash += proceeds
    total += pnl
    print(f"CLOSED {sym} @ {mult:.2f}x -> ${proceeds:.2f} (pnl {pnl:+.2f})")
    pos.pop(p['mint'], None)
    b['positions'].pop(sym, None)
b['cash'] = round(cash, 2)
b['realized'] = round(realized + total, 2)
b['equity'] = b['cash']
json.dump(b, open('/home/agentuser/memecoin-quant/paper_book.json', 'w'), indent=1)
pm.save_positions(pos)
print(f"BOOK RESET: equity ${b['cash']:.2f} (was $100 start, pnl {total:+.2f})")
print("Armed: only NEW calls (posted <5m ago) get bought.")
