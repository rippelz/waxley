#!/usr/bin/env python3
"""poke.py: wait for the wick engine ("Main", child of sober, in wickblox.py's netns), find libroblox in its
memory, then
  - patch the script loader so a script whose bytecode field is empty runs its Source field as bytecode
    (Studio's server replicates scripts as Source only; the Android engine has no compiler and only runs
    the bytecode field, so wick's place prep puts compiled bytecode in Source, see sober/wick_place.py)
  - make SigGuard (luau_load's check that bytecode of 280+ bytes carries Roblox's signature, which only
    Roblox's servers can make) always pass
  - keep the RbxTransport selection flags set for a few seconds (without them the engine picks RakNet)
Offsets are for the forked libroblox (Roblox 741); the patch checks the original bytes first."""
import os, struct, sys, time

FLAGS = {0x736ccc8: 1, 0x769fee0: 1, 0x736cca0: 1}
LOADER = 0x27542d2  # in the script loader: mov rcx, [rsi+8] (bytecode holder); lea rsi, [rcx+0x18]
LOADER_ORIG = bytes.fromhex("488b4e08488d7118")
CAVE = 0x265c2cc  # int3 padding (0x265c2c9..0x265c300)
SIGGUARD = 0x275492e  # its only caller is luau_load, which reports "bytecode corrupted" unless it returns 0
SIGGUARD_ORIG = bytes.fromhex("554889e5")  # push rbp; mov rbp, rsp
SIGGUARD_PASS = bytes.fromhex("31c0c390")  # xor eax, eax; ret


def rel32(src_next, dst):
    return struct.pack("<i", dst - src_next)


# cave: rcx = bytecode holder; if its string is empty, and the Source holder's string starts with wick's
# bytecode magic (sober/wick_place.py: 0xe009325b4a107a52, the same header Roblox's own scripts carry),
# use the Source holder instead (libc++ string at holder+0x18: short = inline at +0x19, long = ptr at +0x28)
CAVE_CODE = bytes.fromhex(
    "488b4e08"      # mov rcx, [rsi+8]
    "80791800"      # cmp byte [rcx+0x18], 0
    "7521"          # jne L
    "488b06"        # mov rax, [rsi]
    "4885c0"        # test rax, rax
    "7419"          # je L
    "f6401801"      # test byte [rax+0x18], 1
    "488d7819"      # lea rdi, [rax+0x19]
    "7404"          # je S
    "488b7828"      # mov rdi, [rax+0x28]
    "813f527a104a"  # S: cmp dword [rdi], 0x4a107a52
    "7503"          # jne L
    "4889c1"        # mov rcx, rax
    "488d7118")     # L: lea rsi, [rcx+0x18]
CAVE_CODE += b"\xe9" + rel32(CAVE + len(CAVE_CODE) + 5, LOADER + 8)
JUMP = b"\xe9" + rel32(LOADER + 5, CAVE) + b"\x90" * 3


def wick_netns():
    # wick's network namespace, found through its mock backend; your real Sober is never in it
    for p in os.listdir('/proc'):
        if not p.isdigit(): continue
        try:
            if b'sober/wickblox.py' in open(f'/proc/{p}/cmdline', 'rb').read():
                return os.readlink(f'/proc/{p}/ns/net')
        except OSError: pass


def engine():
    ns = wick_netns()
    if ns is None: return None
    for p in os.listdir('/proc'):
        if not p.isdigit(): continue
        try:
            if open(f'/proc/{p}/comm').read().strip() != 'Main': continue
            ppid = open(f'/proc/{p}/stat').read().split(')')[1].split()[1]
            if open(f'/proc/{ppid}/comm').read().strip() != 'sober': continue
            if os.readlink(f'/proc/{p}/ns/net') == ns: return int(p)
        except OSError: pass


t0 = time.time()
p = None
while time.time() - t0 < 120:
    p = engine()
    if p: break
    time.sleep(0.005)
t_eng = time.time()
print('engine', p, flush=True)
if p is None: sys.exit('no wick engine showed up')
base = None
while base is None and time.time() - t0 < 120:
    for l in open(f'/proc/{p}/maps'):
        parts = l.split(); a, b = [int(x, 16) for x in parts[0].split('-')]
        if 'memfd' in l and 'r-xp' in parts[1] and parts[2] == '00000000' and b - a > 50_000_000:
            base = a; mapname = parts[0]; break
    time.sleep(0.002)
print('base', hex(base), flush=True)
mem = open(f'/proc/{p}/mem', 'r+b', buffering=0)
# Code patches go into the memfd libroblox is mapped from, not through /proc/pid/mem: a write to the mapping
# makes private copies of those pages, and the engine's integrity check kills the process about a minute
# after its code stops matching that file
try:  # the memfd's descriptor is closed by now; map_files still reaches it (root only)
    code = open(f'/proc/{p}/map_files/{mapname}', 'r+b', buffering=0)
except OSError as e:
    code = None
    print('map_files:', e, flush=True)
if os.environ.get("WICK_CODEMODE") == "mem": code = None
if code is None: print('memfd not found, patching through memory', flush=True)


def write_code(va, data):
    if code is not None:
        code.seek(va); code.write(data)  # the text segment maps file offset 0 at va 0
    else:
        mem.seek(base + va); mem.write(data)

# the loader patch, once the code is mapped (the cave first, then the jump into it)
mem.seek(base + LOADER); cur = mem.read(len(LOADER_ORIG))
mem.seek(base + CAVE); cave = mem.read(len(CAVE_CODE))
if os.environ.get("WICK_NOLOADER") is None and cur == LOADER_ORIG and cave == b"\xcc" * len(CAVE_CODE):
    write_code(CAVE, CAVE_CODE)
    write_code(LOADER, JUMP)
    print('loader patched', flush=True)
elif cur == JUMP:
    print('loader already patched', flush=True)
else:
    print(f'loader NOT patched: unexpected bytes {cur.hex()} / {cave.hex()} (different libroblox?)', flush=True)

mem.seek(base + SIGGUARD); cur = mem.read(4)
if os.environ.get("WICK_NOSIG") is None and cur == SIGGUARD_ORIG:
    write_code(SIGGUARD, SIGGUARD_PASS)
    print('sigguard patched', flush=True)
elif cur != SIGGUARD_PASS:
    print(f'sigguard NOT patched: unexpected bytes {cur.hex()}', flush=True)

def poke(flags):
    for va, v in flags.items():
        mem.seek(base + va); mem.write(bytes([v]))


end = time.time() + 8
while time.time() < end:
    try: poke(FLAGS)
    except OSError: break
    time.sleep(0.001)
print('done', flush=True)

# put the original code back WICK_UNDO seconds in (default 15): the engine's integrity check runs
# ~20-35 s in (patched-at-35 dies at 61 s, patched-at-20 survives), and by 15 s the place has joined,
# so luau_load's SigGuard and the Source-as-bytecode loader fallback have done their job already.
# The restore goes page-wise in one write per page (LOADER and SIGGUARD share the 0x2754000 page):
# the checker hashes pages, and catching a half-restored page makes the engine exit on the spot
undo = os.environ.get("WICK_UNDO", "15")
time.sleep(max(0, t_eng + float(undo) - time.time()))
page = 0x1000
for first, last in ((LOADER, SIGGUARD), (CAVE, CAVE)):
    p0 = first & ~(page - 1)
    end = ((last & ~(page - 1)) + page)
    try:
        code.seek(p0); buf = bytearray(code.read(end - p0))
    except OSError:
        break
    for site, orig in ((LOADER, LOADER_ORIG), (SIGGUARD, SIGGUARD_ORIG), (CAVE, b"\xcc" * len(CAVE_CODE))):
        if first <= site <= last:
            off = site - p0
            buf[off:off + len(orig)] = orig
    code.seek(p0); code.write(buf)
print('undone at', round(time.time() - t_eng, 1), flush=True)
