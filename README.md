# vision-sources

Signed list of free, legal sources for the Vision player: official free broadcast streams and open-licence media
(Blender open movies etc.). The list is checked daily by GitHub Actions; the player downloads it once a week.

- `sources.json` and `sources.json.sig` (Ed25519, public key in `public_key.hex`) are published at
  `https://drugoj00.github.io/vision-sources/sources.json`
- `sources_master.json` is the hand-maintained whitelist (official pages and open licences only; no aggregators).
- `tools/check_sources.py` checks every entry: HLS playlist, variant, first segment (Range request), audio language of the
  stream (to catch a wrong channel), `.torrent` hash against the magnet link; dead addresses are replaced by fallbacks.
  After two failed runs in a row an entry is marked `dead` and an issue is opened.
- The list contains no content: only addresses and licence notes. The user is responsible for compliance with the laws of their country.

Local checks: `python -m unittest discover -s tests -v`.

---

Подписанный список бесплатных и законных источников для плеера Vision: официальные бесплатные эфиры и открытые лицензии.
Проверяется ежедневно (GitHub Actions); плеер загружает его раз в неделю. Закрытый ключ подписи в репозитории не хранится
(только в секретах GitHub Actions и в защищённом хранилище владельца).
