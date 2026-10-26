#!/usr/bin/env python3
"""Install one Roblox Studio build from Roblox's own CDN, laid out the way Vinegar does it.

    studio.py <version-hash> <dest-dir>

STUDIO_MIRRORS (space-separated base URLs, file:// works) are tried before Roblox's own, e.g. an
unpacked backup of the original files (fetch-deps does that when Roblox no longer has the build).
The package -> folder map comes out of that build's RobloxStudioInstaller.exe (like Vinegar's
rbxbin does); every zip is checked against the MD5 in rbxPkgManifest.txt.
"""
import hashlib, io, json, os, re, shutil, sys, urllib.request, zipfile

MIRRORS = ["https://setup.rbxcdn.com", "https://roblox-setup.cachefly.net",
           "https://s3.amazonaws.com/setup.roblox.com"]
MIRRORS[:0] = os.environ.get("STUDIO_MIRRORS", "").split()


def fetch(name):
    err = None
    for m in MIRRORS:
        try:
            with urllib.request.urlopen(f"{m}/{name}", timeout=60) as r:
                return r.read()
        except Exception as e:  # try the next mirror
            err = e
    sys.exit(f"studio.py: can't download {name}: {err} (Roblox may have removed this build)")


def package_dirs(exe):
    for m in re.finditer(rb'\0(\{"[^\0]*?"\})\0', exe):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if any(k.endswith(".zip") for k in d):
            return {k: v.replace("\\", "/").strip("/") for k, v in d.items()}
    sys.exit("studio.py: no package map in the installer")


def main(version, dest):
    lines = fetch(f"{version}-rbxPkgManifest.txt").decode().split()
    assert lines[0] == "v0", "unknown manifest format"
    pkgs = [(lines[i], lines[i + 1]) for i in range(1, len(lines) - 3, 4)]
    dirs = package_dirs(fetch(f"{version}-RobloxStudioInstaller.exe"))
    dest = os.path.abspath(dest)
    tmp = dest + ".part"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    for name, md5 in pkgs:
        if not name.endswith(".zip"):
            continue
        print(f"studio.py: {name}", file=sys.stderr)
        data = fetch(f"{version}-{name}")
        if hashlib.md5(data).hexdigest() != md5:
            sys.exit(f"studio.py: {name} doesn't match its manifest MD5")
        sub = dirs.get(name)
        if sub is None:
            sys.exit(f"studio.py: {name} isn't in the installer's package map")
        out = os.path.join(tmp, sub)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for i in z.infolist():
                p = i.filename.replace("\\", "/")
                if p.endswith("/"):
                    continue
                t = os.path.normpath(os.path.join(out, p))
                if os.path.commonpath([t, tmp]) != tmp:
                    sys.exit(f"studio.py: {name} has a path outside the install: {p}")
                os.makedirs(os.path.dirname(t), exist_ok=True)
                with z.open(i) as src, open(t, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    with open(os.path.join(tmp, "AppSettings.xml"), "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\r\n<Settings>\r\n'
                '        <ContentFolder>content</ContentFolder>\r\n'
                '        <BaseUrl>http://www.roblox.com</BaseUrl>\r\n</Settings>\r\n')
    shutil.rmtree(dest, ignore_errors=True)
    os.rename(tmp, dest)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
