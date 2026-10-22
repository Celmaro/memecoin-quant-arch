import sys, json, urllib.request, urllib.error, os, struct
sys.path.insert(0, '/home/agentuser/memecoin-quant')
os.chdir('/home/agentuser/memecoin-quant')

MINT = "HmqnR7WAWeZgGNF5QbcF74aTfzCAtxcgtsjTEbLHfart"  # SEXCOIN (known ground truth)

def raw(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, (e.read().decode()[:250] if hasattr(e, 'read') else '')
    except Exception as e:
        return 'ERR', str(e)[:150]

print('=== 1. DexPaprika ===')
for url in [f"https://api.dexpaprika.com/v1/tokens/solana/{MINT}",
            f"https://api.dexpaprika.com/v1/tokens/{MINT}"]:
    st, body = raw(url)
    print(f'  {url.split("dexpaprika.com")[1]}: {st} {body[:300]}')
    print()

print('=== 2. GeckoTerminal ===')
for url in [f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{MINT}",
            f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{MINT}/pools"]:
    st, body = raw(url)
    print(f'  {url.split("geckoterminal.com")[1]}: {st} {body[:300]}')
    print()

print('=== 3. Solana RPC (metadata PDA parse) ===')
# Metaplex metadata PDA: seeds = ["metadata", PROGRAM_ID, mint]
import hashlib
def find_pda(seeds, program_id):
    for bump in range(256):
        try:
            k = b"".join([bytes(s) for s in seeds]) + bytes([bump])
            if program_id == "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s":
                # PDA derivation via sha256(seeds + program_id)
                h = hashlib.sha256(k + bytes.fromhex(program_id)).digest()
                # check curve... simplified: standard seeds hash
            if bump == 0:
                h = hashlib.sha256(k + bytes.fromhex(program_id)).digest()
                if h[0] < 0xFF:  # not on curve (approx) — real check needs secp
                    return h, bump
        except Exception:
            pass
    return None, None
# Correct PDA: use the standard algorithm with solders-like pubkey ops — do it via
# simple known result: metadata PDA for a mint can be computed; we'll just try RPC
# getAccountInfo on a *known* metadata addr is not derivable without ed25519 ops.
# Instead use the classic trick: metadata account = PDA("metadata", metaqbxx..., mint)
# Implement proper PDA via sha256 and check on-curve by using secp256k1? Solana uses ed25519.
# Use pynacl? Not installed. Alternative: derive with a small embedded table: none.
# PRAGMATIC: RPC getAccountInfo on mint gives mint authority etc but not symbol.
# Try the common metadata address derivation via bytes (ed25519 on-curve check is
# hard without libs) — so instead TEST publicnode getTokenSupply + dexpair fallback
# viability: does RPC add anything? Mint metadata is the ONLY RPC path to symbol.
print('  (metadata PDA needs ed25519 — will test via Helius DAS getAsset instead)')

print('=== 3b. Helius DAS getAsset (metadata + symbol in one call) ===')
from desk_common import load_config
cfg = load_config()
hkey = cfg.get('HELIUS_KEY')
if hkey:
    body = json.dumps({"jsonrpc":"2.0","id":1,"method":"getAsset","params":{"id":MINT}}).encode()
    req = urllib.request.Request(f"https://mainnet.helius-rpc.com/?api-key={hkey}", data=body,
                                 headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read())
        res = d.get('result') or {}
        content = res.get('content') or {}
        md = content.get('metadata') or {}
        print(f"  symbol={md.get('symbol')} name={md.get('name')}")
        token_info = res.get('token_info') or {}
        print(f"  price={token_info.get('price_per_token')} supply={token_info.get('supply')} mcap_from_supply={token_info.get('supply') and token_info.get('price_per_token')}")
    except Exception as e:
        print(f'  getAsset err: {str(e)[:120]}')
