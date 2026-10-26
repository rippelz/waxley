#!/usr/bin/env python3
"""Give local files stable Waxley asset ids (nothing is uploaded).

  python3 -I tools/local_asset.py <key> <file> [<key> <file> ...]
  e.g. local_asset.py sound:t7/castle tools/sounds/out/t7_castle.wav

Copies each file into ~/.local/share/waxley/assets/<id>.<ext> (a re-run with the same key
replaces the file, the id stays) and prints "key id". Audio that isn't OGG/MP3 is transcoded
to OGG when Studio fetches it.
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bo3_local import STORE, Registry  # noqa: E402


def main(args):
    if not args or len(args) % 2:
        sys.exit(__doc__)
    reg = Registry()
    for key, path in zip(args[0::2], args[1::2]):
        aid = reg.id(key)
        ext = os.path.splitext(path)[1].lower()
        for old in os.listdir(STORE):
            if old.split(".")[0] == str(aid):
                os.remove(os.path.join(STORE, old))
        shutil.copyfile(path, os.path.join(STORE, f"{aid}{ext}"))
        print(key, aid)
    reg.save()


if __name__ == "__main__":
    main(sys.argv[1:])
