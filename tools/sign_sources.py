"""Подпись и проверка sources.json (Ed25519).
  python tools/sign_sources.py sign sources.json      секрет: переменная VISION_SOURCES_KEY (64 hex-символа, seed), пишет sources.json.sig
  python tools/sign_sources.py verify sources.json    открытый ключ из public_key.hex
  python tools/sign_sources.py pub                    печатает открытый ключ по секрету (секрет не печатается)
Секрет в публичный репозиторий не кладётся: он только в секретах GitHub Actions и в хранилище на ПК владелицы."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ed25519_pure as ed  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _secret():
    h = (os.environ.get("VISION_SOURCES_KEY") or "").strip()
    if len(h) != 64:
        raise SystemExit("нет секрета VISION_SOURCES_KEY (64 hex)")
    return bytes.fromhex(h)


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    cmd = argv[1]
    if cmd == "pub":
        print(ed.public_key(_secret()).hex())
        return 0
    path = argv[2]
    data = open(path, "rb").read()
    if cmd == "sign":
        sig = ed.sign(_secret(), data)
        open(path + ".sig", "w", encoding="ascii", newline="\n").write(sig.hex() + "\n")
        print("подписано:", path + ".sig")
        return 0
    if cmd == "verify":
        pub = bytes.fromhex(open(os.path.join(ROOT, "public_key.hex"), encoding="ascii").read().strip())
        sig = bytes.fromhex(open(path + ".sig", encoding="ascii").read().strip())
        ok = ed.verify(pub, data, sig)
        print("подпись верна" if ok else "ПОДПИСЬ НЕВЕРНА")
        return 0 if ok else 1
    raise SystemExit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
