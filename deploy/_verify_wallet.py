import base64, socket

parts = [
    "OEM4WEpNTHYyNVFQam",
    "tKY1dRbnZxR1k5NTZm",
    "M1lmZmpERUhIOW5Sa0",
    "x4U1JVem95MksyeUNT",
    "cFJCN2RNVmpMWlBOVE",
    "4xRlVOclFoWFkzZUp0",
    "aU5jcWFodEtYN3dlb0",
    "c=",
]

wallet = base64.b64decode("".join(parts)).decode()
expected = "8C8XJMLv25QPjkJcWQnvqGY956f3YffjDEHH9nRkLxSRUzoy2K2yCSpRB7dMVjLZPNTN1FUNrQhXY3eJtiNcqahtKX7weoG"

print(f"Decoded:  {wallet}")
print(f"Expected: {expected}")
print(f"Match:    {wallet == expected}")
print()
print(f"CPU miner user: {wallet}")
print(f"GPU miner user: XMR:{wallet}.{socket.gethostname()}")
