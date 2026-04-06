import json, urllib.request, urllib.error, time, sys

base = sys.argv[1] if len(sys.argv) > 1 else "http://192.154.102.26:8188"

code_file = None
if len(sys.argv) > 2:
    p = sys.argv[2]
    if p.endswith(".py") and __import__("os").path.isfile(p):
        code_file = p
        with open(p) as f:
            code = f.read()
    else:
        code = p
else:
    code = 'result = "HELLO_TEST_123"'

import random
cache_bust = f"# {random.randint(0, 999999999)}\n"
code = cache_bust + code

node_setup = {
    "1": {"class_type": "SRL Eval", "inputs": {"parameters": "", "code": code}},
    "2": {"class_type": "PreviewAny", "inputs": {"source": ["1", 0]}},
}
prompt = {"prompt": node_setup, "extra_data": {"extra_pnginfo": {
    "workflow": {"nodes": [{"id": 1, "type": "SRL Eval"}, {"id": 2, "type": "PreviewAny"}]}
}}}

body = json.dumps(prompt).encode()
req = urllib.request.Request(
    base + "/prompt", data=body,
    headers={"User-Agent": "M", "Content-Type": "application/json"},
)
r = urllib.request.urlopen(req, timeout=20)
resp = json.loads(r.read().decode())
print("PROMPT RESPONSE:", json.dumps(resp))

pid = resp.get("prompt_id")
if pid:
    for i in range(30):
        time.sleep(3)
        try:
            hr = urllib.request.urlopen(
                urllib.request.Request(base + "/history/" + pid, headers={"User-Agent": "M"}),
                timeout=15,
            )
            entry = json.loads(hr.read().decode()).get(pid, {})
            status = entry.get("status", {}).get("status_str", "pending")
            print(f"CHECK {i+1}: status={status}")
            if status != "pending":
                outputs = entry.get("outputs", {})
                for nid, nout in outputs.items():
                    for key, val in nout.items():
                        if isinstance(val, list):
                            for v in val:
                                print(v)
                        elif isinstance(val, str):
                            print(val)
                if not outputs:
                    print("NO OUTPUT. Status:", json.dumps(entry.get("status", {})))
                break
        except Exception as e:
            print(f"CHECK {i+1}: {e}")
