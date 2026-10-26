# Waxley

Roblox Studio (Vinegar's Wine build + a separate copy of the Studio install and prefix)
running with **no internet access at all**.

    waxley [place.rbxl]                    # also: "Waxley" in the app menu
    WAXLEY_HIDDEN=1 waxley ...     # headless gamescope, for testing

Wick (Sober, the Android client, offline on top of Waxley) lives in its own repo:
[rippelz/wick](https://github.com/rippelz/wick). It imports `mockblox.py`, `routes.py` and
`creds.py` from here, so keep those interfaces stable.

## How it stays offline
- Own user + network + mount namespace (`unshare`). Interfaces: `lo` and a dummy `os0`
  (10.77.0.2, no routes; Play Solo's local server needs a non-loopback address).
- `/etc/resolv.conf`, `nsswitch.conf` and `hosts` are bind-mounted over and the systemd-resolved
  socket is hidden, so name lookups can't leak through the host.
- `mockblox.py` (inside the namespace) answers DNS on 127.0.0.1:53 (every name -> 127.0.0.1) and
  HTTPS on :443 with certs from a local CA that is appended only to the Waxley copy's
  `ssl/cacert.pem`. `routes.py` has the responses (flags, OAuth, user info, assets); it is
  reloaded per request. Telemetry gets a 200 and is dropped; any other service answers 503
  (like that service being down).
- Login is fake: `creds.py seed` writes local-only tokens into the offline prefix on each
  launch. No real Roblox credentials are stored in it.

## Argon
Ports 8000-8010 (`WAXLEY_ARGON_PORTS`) are bridged from the namespace's loopback to the
host's loopback through unix sockets. Nothing else gets out. Connect the plugin as usual.

## Assets (rbxassetid://)
Served from this machine only, in this order (see `assets.py`):
1. `~/.local/share/waxley/assets/<id>[.ext]`: drop a file named after the asset id
2. the rbxasset upload ledger: your own uploads map back to their original local files

Anything else loads as "asset not found". Files in the Studio content folder still work
via `rbxasset://`.

## Data
`~/.local/share/waxley/`: `studio/` (install copy, version-7aaa6838d36c4448), `prefix/`,
`appdata/`, `mock/` (flag snapshots + user.json), `certs/`, `assets/`, `run/requests.log`.

## Known gaps
- Headless gamescope started on the host (as `WAXLEY_HIDDEN=1 waxley` does) takes the first display number
  without a `/tmp/.X*-lock`. On this desktop Hyprland's Xwayland :0 had no lock, so a test gamescope took over
  `/tmp/.X11-unix/X0` for new X clients (fixed by hand on 2026-10-25: `X0` re-linked to Hyprland's `X0_`,
  lock written).
- **Local MeshParts have no collision data.** A model Waxley serves (e.g. a bo3_local pack) loads
  MeshParts without PhysicsData, so Studio collides them as their bounding **Box**, whatever their
  CollisionFidelity. Real uploads get collision from the importer. Anything collision-sensitive
  (nav sampling, raycasts, hull brushes) has to be rebuilt with
  `AssetService:CreateMeshPartAsync(meshId, { CollisionFidelity = ... })` (tools/t7_localize.py does
  this for T7Zombies' hull brushes).
- HttpService is off in Play for an unpublished place: get big test output out through tagged
  `print` chunks in the Studio log.
- Studio's 3 GB online cache can't be read by asset id; assets come from `assets/` only.

## Install
    git clone https://github.com/rippelz/waxley && cd waxley
    ./fetch-deps
    ln -s "$PWD/waxley" ~/.local/bin/waxley
    cp icon/waxley.svg ~/.local/share/icons/hicolor/scalable/apps/
    cp desktop/waxley.desktop ~/.local/share/applications/

`fetch-deps` sets up everything at the builds pinned in `versions.env`, checking every download
against a hash, and skips what's already right (`--force` redoes it all):
- **Vinegar** from Flathub, pinned to `VINEGAR_COMMIT` and masked so it stays there.
- **Vinegar's Wine build** (`WINE_BUILD`). Vinegar only keeps its newest builds online, so it's
  mirrored on this repo's [`deps` release](https://github.com/rippelz/waxley/releases/tag/deps)
  (Wine is LGPL).
- **Roblox Studio** (`STUDIO_VERSION`) straight from Roblox's CDN (`deps/studio.py`), plus DXVK
  and Vinegar's renderer flags: the same files Vinegar installs. Roblox drops old builds
  eventually; this one was still served on 2026-10-25.
- **The offline prefix**, copied from Vinegar's Studio prefix (it has the WebView2 runtime). On a
  machine that never ran Vinegar, run it once (`flatpak run org.vinegarhq.Vinegar`), let Studio
  open, close it, and run `fetch-deps` again.
- **Mock data** from `defaults/mock/` (the fake user + Studio flag snapshots), only if missing.

`waxley` itself warns at startup when an installed build differs from `versions.env`.

Also needs `unshare`, socat, curl, zstd, python3 with `cryptography`; gamescope for
`WAXLEY_HIDDEN=1`. `tools/` also wants numpy.
