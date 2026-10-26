#!/usr/bin/env python3
"""wick_place.py: make a place playable in wick's Android engine (see wick_place.luau for what changes).

  wick_place.py <in.rbxl|rbxlx> <out.rbxl>

1. wick_place.luau (lune) adds the default player/character scripts, swaps client code for placeholders and
   gives shared modules server twins
2. wickc compiles the client code to Roblox-encoded bytecode (built here from ~/code/ref/luau when missing
   or older than wickc.cpp)
3. the placeholders in the binary .rbxl are replaced with the bytecode. Bytecode isn't UTF-8, which lune
   won't write, so this edits the PROP chunks directly; other chunks are copied as they are

The result is cached next to <out> by the input's content hash, so relaunching the same place is instant.
"""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("WAXLEY_DATA", os.path.expanduser("~/.local/share/waxley"))
WD = os.path.join(DATA, "wick")
STUDIO = os.path.join(WD, "studio")
# Luau at the forked engine's release (741): its bytecode format is the one the engine reads (v9)
LUAU = os.environ.get("WICK_LUAU_SRC", os.path.expanduser("~/code/ref/luau-0.741"))
WICKC = os.path.join(WD, "wickc")
VERSION = b"wick_place 2"  # bump when the rewrite changes, to drop cached places


def build_wickc():
    src = os.path.join(HERE, "wickc.cpp")
    stamp = WICKC + ".luau"
    if os.path.exists(WICKC) and os.path.getmtime(WICKC) >= os.path.getmtime(src) and \
            os.path.exists(stamp) and open(stamp).read() == LUAU:
        return
    if not os.path.isdir(LUAU):
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", "0.741", "https://github.com/luau-lang/luau.git", LUAU],
                       check=True)
    print("wick: building wickc", file=sys.stderr)
    files = [os.path.join(LUAU, d, "src", f) for d in ("Ast", "Compiler", "Common") for f in
             sorted(os.listdir(os.path.join(LUAU, d, "src"))) if f.endswith(".cpp")]
    files.append(os.path.join(LUAU, "Bytecode", "src", "BytecodeBuilder.cpp"))
    inc = [f"-I{os.path.join(LUAU, d, 'include')}" for d in ("Ast", "Compiler", "Bytecode", "Common")]
    subprocess.run(["g++", "-std=c++17", "-O2", "-o", WICKC + ".new", src, *files, *inc], check=True)
    os.replace(WICKC + ".new", WICKC)
    with open(stamp, "w") as f:
        f.write(LUAU)


# ---------- binary .rbxl ----------
def lz4_block(src, size):
    dst = bytearray()
    i = 0
    while i < len(src):
        tok = src[i]
        i += 1
        lit = tok >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        dst += src[i:i + lit]
        i += lit
        if i >= len(src):
            break
        off = src[i] | src[i + 1] << 8
        i += 2
        ml = tok & 15
        if ml == 15:
            while True:
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        s = len(dst) - off
        if off >= ml:
            dst += dst[s:s + ml]
        else:
            for k in range(ml):
                dst.append(dst[s + k])
    if len(dst) != size:
        raise ValueError(f"lz4: got {len(dst)} bytes, expected {size}")
    return bytes(dst)


def chunk_body(raw, clen, ulen):
    if not clen:
        return raw
    if raw[:4] == b"\x28\xb5\x2f\xfd":
        import compression.zstd
        return compression.zstd.decompress(raw)
    return lz4_block(raw, ulen)


def patch(data, repl):
    """Replace whole-string values in String-typed PROP chunks; other chunks are copied unchanged."""
    if data[:8] != b"<roblox!":
        raise ValueError("not a binary place")
    out = [data[:32]]
    pos = 32
    n = 0
    while pos < len(data):
        name = data[pos:pos + 4]
        clen, ulen = struct.unpack_from("<II", data, pos + 4)
        end = pos + 16 + (clen or ulen)
        whole = data[pos:end]
        if name == b"PROP":
            body = chunk_body(data[pos + 16:end], clen, ulen)
            nl, = struct.unpack_from("<I", body, 4)
            if body[8 + nl] == 1:  # String / ProtectedString
                vals, p, hit = [], 9 + nl, False
                while p < len(body):
                    l, = struct.unpack_from("<I", body, p)
                    s = body[p + 4:p + 4 + l]
                    p += 4 + l
                    if s in repl:
                        s = repl[s]
                        hit = True
                        n += 1
                    vals.append(s)
                if hit:
                    body = body[:9 + nl] + b"".join(struct.pack("<I", len(s)) + s for s in vals)
                    whole = name + struct.pack("<III", 0, len(body), 0) + body
        out.append(whole)
        pos = end
        if name == b"END\0":
            break
    return b"".join(out), n


def main():
    src, dst = sys.argv[1:3]
    with open(src, "rb") as f:
        h = hashlib.sha256(VERSION)
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    tag = h.hexdigest()[:16]
    stamp = dst + ".src"
    if os.path.exists(dst) and os.path.exists(stamp) and open(stamp).read() == tag:
        print("wick: place already prepared", file=sys.stderr)
        return
    build_wickc()
    work = tempfile.mkdtemp(prefix="wick-place-")
    try:
        ph = os.path.join(work, "placeholders.rbxl")
        print("wick: preparing the place for the Android engine", file=sys.stderr)
        r = subprocess.run(["lune", "run", os.path.join(HERE, "wick_place.luau"), src, ph, work, STUDIO],
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"wick: wick_place.luau failed:\n{r.stdout}{r.stderr}")
        print(f"wick: {r.stdout.strip()}", file=sys.stderr)
        manifest = json.load(open(os.path.join(work, "manifest.json")))
        lst = os.path.join(work, "list")
        with open(lst, "w") as f:
            for i, (key, srcfile) in enumerate(manifest.items()):
                f.write(f"{srcfile}\t{srcfile[:-5]}.bc\n")
        subprocess.run([WICKC, lst], check=True)
        repl = {k.encode(): open(v[:-5] + ".bc", "rb").read() for k, v in manifest.items()}
        data, n = patch(open(ph, "rb").read(), repl)
        if n != len(repl):
            sys.exit(f"wick: only {n} of {len(repl)} scripts were patched")
        with open(dst + ".new", "wb") as f:
            f.write(data)
        os.replace(dst + ".new", dst)
        with open(stamp, "w") as f:
            f.write(tag)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
