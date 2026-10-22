import sys, json, urllib.request, urllib.error, os
sys.path.insert(0, '/home/agentuser/memecoin-quant')
os.chdir('/home/agentuser/memecoin-quant')
from desk_common import load_config
cfg = load_config()
MINT = "HmqnR7WAWeZgGNF5QbcF74aTfzCAtxcgtsjTEbLHfart"

def raw(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, (e.read().decode()[:200] if hasattr(e, 'read') else '')
    except Exception as e:
        return 'ERR', str(e)[:120]

key = cfg.get('DEXPAPRIKA_KEY')
print('DexPaprika key present:', bool(key))
for hdr_name, hdr in [('x-api-key', {'x-api-key': key}), ('Authorization', {'Authorization': f'Bearer {key}'}), ('X-API-KEY', {'X-API-KEY': key})]:
    if not key:
        break
    st, body = raw(f"https://api.dexpaprika.com/v1/tokens/solana/{MINT}", hdr)
    print(f'  [{hdr_name}]: {st} {body[:220]}')

# keyless MCP endpoint maybe? test the mcp health/token route
st, body = raw(f"https://api.dexpaprika.com/v1/tokens/solana/{MINT}", {'User-Agent': 'Mozilla/5.0'})
print('  keyless retry:', st, body[:150])
