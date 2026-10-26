#!/usr/bin/env python3
"""peek.py <pid> addr...: read bytes at libroblox.so-relative virtual addresses in a running Sober (root)."""
import sys, re
pid = int(sys.argv[1])
base = None
for l in open(f"/proc/{pid}/maps"):
    if "libroblox" in l:
        a, off = l.split()[0].split("-")[0], l.split()[2]
        if int(off, 16) == 0: base = int(a, 16); break
if base is None:
    # Sober maps it anonymously? look for the ELF header of a 120MB mapping
    for l in open(f"/proc/{pid}/maps"): pass
    print("no libroblox mapping"); sys.exit(1)
f = open(f"/proc/{pid}/mem", "rb")
for a in sys.argv[2:]:
    va = int(a, 16); f.seek(base + va); print(a, f.read(8).hex())
