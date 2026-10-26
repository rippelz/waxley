#!/usr/bin/env python3
"""Put a bo3map Roblox export into Waxley's local asset store (no uploads, nothing online).

  python3 -I tools/bo3_local.py <map> <roblox export dir>
  e.g. bo3_local.py zm_castle ~/code/ref/bo3-maps/zm_castle/roblox

Writes into ~/.local/share/waxley/assets:
  <id>.mesh          one Roblox mesh (v2.00) per piece, from pieces/pack_NNN.npz
  <id>.rbxmx         one Model per pack: MeshParts named after the pieces (what LoadAsset returns)
  <id>.decal.rbxmx   for each uploaded decal id (ids_textures.json): a Decal whose Texture is a local image id
  <id>.png           every texture in textures/
  local.json         registry: key -> local id (meshes and packs keyed by content hash, so a change
                     always gets a new id: Studio caches assets by id)
  maps/<map>.json    what a map needs: packs {"pending:fbx_pack_000": id}, images {name: id}, decals {decal id: image id}

Local ids start at 8000000000000000 (exact in a double, far above real Roblox ids).
Mesh axes: the export is Blender Z-up studs; CastleData world = (x, z, -y), and Bo3Map turns every
piece by MESH_ROT (180 deg about Y), so mesh space = (-dx, dz, dy) about the piece centre.
"""
import glob
import hashlib
import json
import os
import struct
import sys
from xml.sax.saxutils import escape

import numpy as np

BASE = 8_000_000_000_000_000
STORE = os.path.join(os.environ.get("WAXLEY_DATA", os.path.expanduser("~/.local/share/waxley")), "assets")
REG = os.path.join(STORE, "local.json")


class Registry:
    def __init__(self):
        self.map = json.load(open(REG)) if os.path.exists(REG) else {}
        self.next = max(self.map.values(), default=BASE - 1) + 1

    def id(self, key):
        if key not in self.map:
            self.map[key] = self.next
            self.next += 1
        return self.map[key]

    def save(self):
        tmp = REG + ".tmp"
        json.dump(self.map, open(tmp, "w"), indent=0, sort_keys=True)
        os.replace(tmp, REG)


def write_if_changed(path, data):
    if os.path.exists(path) and os.path.getsize(path) == len(data) and open(path, "rb").read() == data:
        return
    with open(path + ".tmp", "wb") as f:
        f.write(data)
    os.replace(path + ".tmp", path)


def mesh_v2(p, n, uv, t):
    """Roblox mesh 'version 2.00': 36-byte vertices (pos, normal, uvw), uint32 faces. Returns (bytes, size)."""
    lo, hi = p.min(0), p.max(0)
    d = p - (lo + hi) / 2
    pos = np.stack([-d[:, 0], d[:, 2], d[:, 1]], 1)
    if np.abs(n).sum() > 0:
        nrm = np.stack([-n[:, 0], n[:, 2], n[:, 1]], 1)
        verts, faces = (pos, nrm, uv), t
    else:  # flat shaded: one vertex per corner, face normals
        idx = t.reshape(-1)
        pos, uv = pos[idx], uv[idx]
        a, b, c = pos[0::3], pos[1::3], pos[2::3]
        fn = np.cross(b - a, c - a)
        fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
        verts, faces = (pos, np.repeat(fn, 3, 0), uv), np.arange(len(idx), dtype=np.int32).reshape(-1, 3)
    pos, nrm, uv = verts
    vbuf = np.zeros((len(pos), 9), np.float32)
    vbuf[:, 0:3], vbuf[:, 3:6] = pos, nrm
    vbuf[:, 6], vbuf[:, 7] = uv[:, 0], 1.0 - uv[:, 1]  # Roblox meshes store V flipped
    head = b"version 2.00\n" + struct.pack("<HBBII", 12, 36, 12, len(pos), len(faces))
    size = hi - lo
    return head + vbuf.tobytes() + faces.astype("<u4").tobytes(), (size[0], size[2], size[1])


def v3(name, v):
    return f'<Vector3 name="{name}"><X>{v[0]:.6g}</X><Y>{v[1]:.6g}</Y><Z>{v[2]:.6g}</Z></Vector3>'


def meshpart(ref, name, mesh_id, size):
    return (f'<Item class="MeshPart" referent="{ref}"><Properties>'
            f'<string name="Name">{escape(name)}</string><bool name="Anchored">true</bool>'
            f'<Content name="MeshId"><url>rbxassetid://{mesh_id}</url></Content>'
            f'{v3("InitialSize", size)}{v3("size", size)}'
            f'<token name="CollisionFidelity">0</token></Properties></Item>')


def main(map_name, root):
    reg = Registry()
    os.makedirs(os.path.join(STORE, "maps"), exist_ok=True)
    out = {"packs": {}, "images": {}, "decals": {}}

    for path in sorted(glob.glob(os.path.join(root, "pieces", "pack_*.npz"))):
        pack = "fbx_" + os.path.basename(path)[:-4]  # fbx_pack_000, as named in CastleData
        z = np.load(path)
        names = sorted({k.split("|")[0] for k in z.files})
        items = []
        for i, nm in enumerate(names):
            data, size = mesh_v2(z[nm + "|p"], z[nm + "|n"], z[nm + "|uv"], z[nm + "|t"])
            # (content-hashed keys: a changed mesh or pack gets a new id, so Studio's id-keyed cache,
            # rbx-storage + rbx-storage.db, can never hand back an old version)
            mid = reg.id(f"mesh:{map_name}/{nm}#{hashlib.sha1(data).hexdigest()[:12]}")
            write_if_changed(os.path.join(STORE, f"{mid}.mesh"), data)
            items.append(meshpart(f"RBX{i}", nm, mid, size))
        body = "".join(items)
        pid = reg.id(f"pack:{map_name}/{pack}#{hashlib.sha1(body.encode()).hexdigest()[:12]}")
        xml = (f'<roblox version="4"><Item class="Model" referent="RBXM"><Properties>'
               f'<string name="Name">{pack}</string></Properties>{body}</Item></roblox>')
        write_if_changed(os.path.join(STORE, f"{pid}.rbxmx"), xml.encode())
        out["packs"][f"pending:{pack}"] = pid
        print(f"{pack}: {len(names)} pieces -> {pid}")

    for png in sorted(glob.glob(os.path.join(root, "textures", "*.png"))):
        name = os.path.basename(png)[:-4]
        iid = reg.id(f"image:{map_name}/{name}")
        dst = os.path.join(STORE, f"{iid}.png")
        if not os.path.exists(dst):
            os.symlink(os.path.abspath(png), dst)
        out["images"][name] = iid

    ids = os.path.join(root, "ids_textures.json")
    for name, decal in (json.load(open(ids)).items() if os.path.exists(ids) else []):
        if name not in out["images"]:
            continue
        xml = (f'<roblox version="4"><Item class="Decal" referent="RBXD"><Properties>'
               f'<string name="Name">{escape(name)}</string>'
               f'<Content name="Texture"><url>rbxassetid://{out["images"][name]}</url></Content>'
               f'</Properties></Item></roblox>')
        write_if_changed(os.path.join(STORE, f"{decal}.decal.rbxmx"), xml.encode())
        out["decals"][str(decal)] = out["images"][name]

    reg.save()
    json.dump(out, open(os.path.join(STORE, "maps", f"{map_name}.json"), "w"), indent=1, sort_keys=True)
    print(f"{len(out['packs'])} packs, {len(out['images'])} images, {len(out['decals'])} decals -> maps/{map_name}.json")


if __name__ == "__main__":
    main(sys.argv[1], os.path.expanduser(sys.argv[2]))
