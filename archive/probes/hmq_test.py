import sys, json
sys.path.insert(0, '/home/agentuser/memecoin-quant')
from desk_common import load_config, http_json
tok = load_config()['HMQ_BOT_TOKEN']
mint = None
for line in open('/home/agentuser/memecoin-quant/calls.jsonl').read().strip().split('\n')[-5:]:
    d = json.loads(line)
    if d.get('mint'):
        mint = d['mint']
print("test CA:", mint)
st, d = http_json(f"https://api.telegram.org/bot{tok}/sendMessage",
    method="POST",
    body={"chat_id": -1004430296219,
          "text": f"Desk test - scan this: {mint}"},
    headers={"Content-Type": "application/json"})
print("posted:", d.get('ok'))
