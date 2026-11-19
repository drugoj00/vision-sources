"""Проверка бесплатных источников Vision: sources_master.json -> sources.json (живые адреса, статусы).
Только стандартная библиотека. Запуск: python tools/check_sources.py --master sources_master.json --out sources.json --issue issue.md

Правила: проверяются ТОЛЬКО адреса из мастер-списка (ручной белый список официальных бесплатных потоков и открытых лицензий);
никаких агрегаторов. Для HLS: плейлист -> (вариант) -> первый сегмент заголовком Range. Для торрентов: .torrent и совпадение
хэша с magnet. Мёртвым запись становится после HYST подряд неудачных прогонов (защита от разовых сбоев)."""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) VisionSourcesCheck/1.0 (+https://github.com/drugoj00/vision-sources)"
TIMEOUT = 15
ATTEMPTS = 2
PAUSE = 1.0
HYST = 2


class Fail(Exception):
    pass


def fetch(url, rng=None, limit=2000000, method="GET", timeout=None):
    """-> (status, headers, body, final_url). Бросает Fail при ошибке сети/HTTP."""
    h = {"User-Agent": UA, "Accept": "*/*"}
    if rng:
        h["Range"] = rng
    req = urllib.request.Request(url, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
            body = r.read(limit) if method != "HEAD" else b""
            return r.status, dict(r.headers), body, r.geturl()
    except urllib.error.HTTPError as e:
        raise Fail("HTTP %s" % e.code)
    except Exception as e:
        raise Fail(str(getattr(e, "reason", e))[:80])


def _retry(fn, *a, **kw):
    last = None
    for i in range(ATTEMPTS):
        try:
            return fn(*a, **kw)
        except Fail as e:
            last = e
            if i + 1 < ATTEMPTS:
                time.sleep(PAUSE)
    raise last


def _text(b):
    return b.decode("utf-8", "replace")



def ts_audio_langs(data):
    """Языки звуковых дорожек из таблицы PMT фрагмента MPEG-TS (коды ISO 639-2, например deu/eng/fra/spa/jpn). Пусто, если не найдено."""
    pkts = [data[i:i + 188] for i in range(0, len(data) - 187, 188) if data[i] == 0x47]
    pmt_pids = set()
    for p in pkts:
        pid = ((p[1] & 0x1F) << 8) | p[2]
        if pid == 0 and (p[1] & 0x40):
            off = 4 + (1 + p[4] if (p[3] & 0x20) == 0 else 5 + p[4])
            off = 5 + p[4] if (p[3] & 0x30) == 0x10 else off
            payload = p[4:]
            if (p[3] & 0x20):
                payload = p[5 + p[4]:]
            ptr = payload[0]
            t = payload[1 + ptr:]
            if t and t[0] == 0:
                sect_len = ((t[1] & 0x0F) << 8) | t[2]
                body = t[8:3 + sect_len - 4]
                for i in range(0, len(body) - 3, 4):
                    prog = (body[i] << 8) | body[i + 1]
                    if prog:
                        pmt_pids.add(((body[i + 2] & 0x1F) << 8) | body[i + 3])
    langs = []
    for p in pkts:
        pid = ((p[1] & 0x1F) << 8) | p[2]
        if pid in pmt_pids and (p[1] & 0x40):
            payload = p[4:]
            if (p[3] & 0x20):
                payload = p[5 + p[4]:]
            ptr = payload[0]
            t = payload[1 + ptr:]
            if not t or t[0] != 2:
                continue
            sect_len = ((t[1] & 0x0F) << 8) | t[2]
            pinfo = ((t[10] & 0x0F) << 8) | t[11]
            i = 12 + pinfo
            end = 3 + sect_len - 4
            while i + 5 <= end and i + 5 <= len(t):
                es_info = ((t[i + 3] & 0x0F) << 8) | t[i + 4]
                d = t[i + 5:i + 5 + es_info]
                j = 0
                while j + 2 <= len(d):
                    tag, ln = d[j], d[j + 1]
                    if tag == 0x0A and ln >= 3:
                        langs.append(d[j + 2:j + 5].decode("ascii", "ignore").lower())
                    j += 2 + ln
                i += 5 + es_info
    return langs


def check_hls(url, audio_lang=None):
    st, _h, body, final = _retry(fetch, url, None, 2000000)
    txt = _text(body)
    if not txt.lstrip().startswith("#EXTM3U"):
        raise Fail("не плейлист HLS")
    base = final
    if "#EXT-X-STREAM-INF" in txt:
        lines = txt.splitlines()
        variant = None
        for i, ln in enumerate(lines):
            if ln.startswith("#EXT-X-STREAM-INF"):
                for nx in lines[i + 1:]:
                    nx = nx.strip()
                    if nx and not nx.startswith("#"):
                        variant = nx
                        break
                break
        if not variant:
            raise Fail("нет вариантов потока")
        base = urllib.parse.urljoin(final, variant)
        st, _h, body, final2 = _retry(fetch, base, None, 2000000)
        txt = _text(body)
        base = final2
        if not txt.lstrip().startswith("#EXTM3U"):
            raise Fail("вариант не плейлист")
    seg = next((ln.strip() for ln in txt.splitlines() if ln.strip() and not ln.startswith("#")), None)
    if not seg:
        raise Fail("нет сегментов")
    seg_url = urllib.parse.urljoin(base, seg)
    st, _h, data, _f = _retry(fetch, seg_url, "bytes=0-32767", 32768)
    if st not in (200, 206) or not data:
        raise Fail("сегмент пуст")
    if data[:1] == b"<":
        raise Fail("сегмент похож на HTML")
    if audio_lang:
        langs = ts_audio_langs(data)
        if langs and audio_lang.lower() not in langs:
            raise Fail("язык звука %s, ожидался %s" % ("/".join(langs), audio_lang))
    return "ok"


def check_http(url):
    try:
        st, h, _b, _f = _retry(fetch, url, None, 0, "HEAD")
    except Fail:
        st, h, _b, _f = _retry(fetch, url, "bytes=0-0", 16)
    if st not in (200, 206):
        raise Fail("HTTP %s" % st)
    ct = (h.get("Content-Type") or h.get("content-type") or "").lower()
    if ct.startswith("text/html"):
        raise Fail("вместо файла страница")
    return "ok"


def check_page(url):
    st, _h, _b, _f = _retry(fetch, url, None, 2000)
    if st != 200:
        raise Fail("HTTP %s" % st)
    return "ok"


def bdecode_info_hash(data):
    """SHA-1 от bencode-словаря info в .torrent (без внешних библиотек)."""
    pos = [0]

    def parse():
        c = data[pos[0]:pos[0] + 1]
        if c == b"i":
            e = data.index(b"e", pos[0])
            v = int(data[pos[0] + 1:e])
            pos[0] = e + 1
            return v
        if c == b"l":
            pos[0] += 1
            out = []
            while data[pos[0]:pos[0] + 1] != b"e":
                out.append(parse())
            pos[0] += 1
            return out
        if c == b"d":
            pos[0] += 1
            out = {}
            while data[pos[0]:pos[0] + 1] != b"e":
                k = parse()
                start = pos[0]
                v = parse()
                if k == b"info":
                    out["_info_raw"] = data[start:pos[0]]
                out[k] = v
            pos[0] += 1
            return out
        if c.isdigit():
            colon = data.index(b":", pos[0])
            n = int(data[pos[0]:colon])
            s = data[colon + 1:colon + 1 + n]
            pos[0] = colon + 1 + n
            return s
        raise ValueError("bencode")

    top = parse()
    raw = top.get("_info_raw") if isinstance(top, dict) else None
    if not raw:
        raise Fail("в .torrent нет info")
    return hashlib.sha1(raw).hexdigest()


def check_torrent(entry):
    url = entry.get("torrent_url")
    if not url:
        raise Fail("нет адреса .torrent")
    st, _h, data, _f = _retry(fetch, url, None, 6000000)
    if st != 200 or not data.startswith(b"d"):
        raise Fail("не .torrent")
    try:
        got = bdecode_info_hash(data)
    except Fail:
        raise
    except Exception:
        raise Fail("повреждённый .torrent")
    if got != (entry.get("btih") or "").lower():
        raise Fail("хэш .torrent не совпал с magnet")
    return "ok"


def candidates(entry):
    typ = entry.get("type")
    if typ in ("hls", "http", "page"):
        urls = [entry.get("url") or entry.get("link")] + list(entry.get("fallback") or [])
        return [u for u in urls if u]
    return [None]


def check_entry(entry):
    """-> (ok, active_url, reason)."""
    typ = entry.get("type")
    last = "нет адресов"
    if typ == "torrent":
        try:
            check_torrent(entry)
            return True, None, ""
        except Fail as e:
            return False, None, str(e)
    for u in candidates(entry):
        try:
            if typ == "hls":
                check_hls(u, entry.get("audio_lang"))
            elif typ == "http":
                check_http(u)
            else:
                check_page(u)
            return True, u, ""
        except Fail as e:
            last = "%s: %s" % (urllib.parse.urlsplit(u).netloc, e)
    return False, None, last


def run(master, previous, today, hyst=None, checker=check_entry):
    hyst = HYST if hyst is None else hyst
    prev = {e["id"]: e for e in (previous or {}).get("entries", []) if "id" in e}
    out, newly_dead, recovered = [], [], []
    for m in master["entries"]:
        e = dict(m)
        old = prev.get(e["id"], {})
        ok, active, reason = checker(m)
        if ok:
            if active and m.get("type") in ("hls", "http", "page"):
                key = "url" if "url" in m else "link"
                rest = [u for u in candidates(m) if u != active]
                e[key] = active
                if m.get("type") == "hls":
                    e["fallback"] = rest
            e.update(status="ok", fail_count=0, last_ok=today, checked=today)
            e.pop("reason", None)
            if old.get("status") == "dead":
                recovered.append(e["id"])
        else:
            fc = int(old.get("fail_count", 0)) + 1
            was = old.get("status")
            if fc >= hyst:
                st = "dead"
            else:
                st = was if was in ("ok", "dead") else "dead"
            e.update(status=st, fail_count=fc, checked=today, reason=reason)
            if old.get("last_ok"):
                e["last_ok"] = old["last_ok"]
            if st == "dead" and was != "dead":
                newly_dead.append((e["id"], reason))
        out.append(e)
    return {"schema": 1, "updated": today, "entries": out}, newly_dead, recovered


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", default="sources_master.json")
    ap.add_argument("--out", default="sources.json")
    ap.add_argument("--issue", default="")
    ap.add_argument("--hyst", type=int, default=HYST)
    a = ap.parse_args(argv)
    with open(a.master, "r", encoding="utf-8-sig") as f:
        master = json.load(f)
    previous = None
    if os.path.isfile(a.out):
        try:
            with open(a.out, "r", encoding="utf-8-sig") as f:
                previous = json.load(f)
        except ValueError:
            previous = None
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    result, dead, rec = run(master, previous, today, a.hyst)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
        f.write("\n")
    total = len(result["entries"])
    bad = [e for e in result["entries"] if e["status"] == "dead"]
    print("записей: %d, живых: %d, недоступных: %d" % (total, total - len(bad), len(bad)))
    for e in bad:
        print("  не отвечает:", e["id"], e.get("reason", ""))
    if a.issue:
        if dead:
            lines = ["Автопроверка: недоступны источники (после %d неудачных прогонов подряд):" % a.hyst, ""]
            for i, r in dead:
                lines.append("- `%s`: %s" % (i, r))
            lines += ["", "Что делать: найти новый официальный адрес на странице вещателя и поправить `sources_master.json`."]
            with open(a.issue, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines) + "\n")
        elif os.path.isfile(a.issue):
            os.remove(a.issue)
    return 0


if __name__ == "__main__":
    sys.exit(main())
