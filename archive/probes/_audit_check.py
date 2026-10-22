import os, subprocess, time

A = os.path.expanduser('~/.hermes/profiles/profile-b4632e6cdb854166b696555ae7ff4239/scripts/desk_tick.py')
B = '/home/agentuser/memecoin-quant/desk_tick.py'
for f in (A, B):
    st = os.stat(f)
    print(os.path.basename(os.path.dirname(f)) + '/', os.path.basename(f),
          st.st_size, "bytes, mtime", time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime)))

print("\n=== FULL DIFF (scripts vs memecoin-quant) ===")
r = subprocess.run(['diff', '-u', A, B], capture_output=True, text=True)
print(r.stdout[:5000])