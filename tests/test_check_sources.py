"""Тесты проверяльщика на локальном mock-сервере (сеть наружу не используется). Запуск: python -m unittest discover -s tests -v"""
import hashlib
import http.server
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import check_sources as cs  # noqa: E402
import ed25519_pure as ed  # noqa: E402

cs.ATTEMPTS = 1
cs.PAUSE = 0
cs.TIMEOUT = 5


def make_ts(lang):
    """Минимальный MPEG-TS: PAT -> PMT с одной аудиодорожкой и дескриптором языка + ещё пакеты-пустышки."""
    def pkt(pid, payload, pusi=True):
        h = bytes([0x47, (0x40 if pusi else 0) | (pid >> 8), pid & 0xFF, 0x10])
        return (h + payload).ljust(188, b"\xff")
    pat_body = bytes([0x00, 0xB0, 0x0D, 0x00, 0x01, 0xC1, 0x00, 0x00, 0x00, 0x01, 0xE1, 0x00, 0, 0, 0, 0])
    pat = pkt(0, b"\x00" + pat_body)
    desc = bytes([0x0A, 0x04]) + lang.encode() + b"\x00"
    es = bytes([0x0F, 0xE1, 0x01, 0xF0, len(desc)]) + desc
    sect = bytes([0x02, 0xB0, 0x00, 0x00, 0x01, 0xC1, 0x00, 0x00, 0xE1, 0x01, 0xF0, 0x00]) + es + b"\0\0\0\0"
    sect = sect[:2] + bytes([0xB0 | ((len(sect) - 3) >> 8), (len(sect) - 3) & 0xFF]) + sect[4:]
    pmt = pkt(0x100, b"\x00" + sect)
    return pat + pmt + b"".join(pkt(0x101, b"x" * 100, False) for _ in range(20))


def make_torrent():
    info = b"d6:lengthi5e4:name1:a12:piece lengthi16384e6:pieces20:" + b"\x01" * 20 + b"e"
    return b"d4:infod" + info[1:-1] + b"ee", hashlib.sha1(info).hexdigest()


TORRENT, BTIH = make_torrent()
STATE = {"seg_lang": "deu", "hits": 0}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="text/plain", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        STATE["hits"] += 1
        p = self.path
        if p == "/live/master.m3u8":
            self._send(200, b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nv.m3u8\n", "application/vnd.apple.mpegurl")
        elif p == "/live/v.m3u8":
            self._send(200, b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.0,\nseg0.ts\n", "application/vnd.apple.mpegurl")
        elif p == "/live/seg0.ts":
            self._send(200, make_ts(STATE["seg_lang"]), "video/mp2t")
        elif p == "/redir":
            self._send(302, b"", extra={"Location": "/live/master.m3u8"})
        elif p == "/html.m3u8":
            self._send(200, b"<html>captcha</html>", "text/html")
        elif p == "/noseg/master.m3u8":
            self._send(200, b"#EXTM3U\n#EXTINF:6.0,\nmissing.ts\n", "application/vnd.apple.mpegurl")
        elif p == "/t.torrent":
            self._send(200, TORRENT, "application/x-bittorrent")
        elif p == "/file.mkv":
            self._send(200, b"x" * 100, "video/x-matroska")
        elif p == "/page.html":
            self._send(200, b"<html>hi</html>", "text/html")
        else:
            self._send(404, b"no")


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()


class TestHls(Base):
    def setUp(self):
        STATE["seg_lang"] = "deu"

    def test_live(self):
        self.assertEqual(cs.check_hls(self.base + "/live/master.m3u8"), "ok")

    def test_redirect_followed(self):
        self.assertEqual(cs.check_hls(self.base + "/redir"), "ok")

    def test_dead_404(self):
        with self.assertRaises(cs.Fail):
            cs.check_hls(self.base + "/dead.m3u8")

    def test_html_instead_of_playlist(self):
        with self.assertRaises(cs.Fail):
            cs.check_hls(self.base + "/html.m3u8")

    def test_segment_missing(self):
        with self.assertRaises(cs.Fail):
            cs.check_hls(self.base + "/noseg/master.m3u8")

    def test_language_match(self):
        self.assertEqual(cs.check_hls(self.base + "/live/master.m3u8", "deu"), "ok")

    def test_language_mismatch_rejected(self):
        STATE["seg_lang"] = "ara"
        with self.assertRaises(cs.Fail):
            cs.check_hls(self.base + "/live/master.m3u8", "deu")

    def test_ts_lang_parser(self):
        self.assertEqual(cs.ts_audio_langs(make_ts("spa")), ["spa"])
        self.assertEqual(cs.ts_audio_langs(b"nothing"), [])


class TestOther(Base):
    def test_torrent_hash(self):
        self.assertEqual(cs.check_torrent({"torrent_url": self.base + "/t.torrent", "btih": BTIH}), "ok")

    def test_torrent_wrong_hash(self):
        with self.assertRaises(cs.Fail):
            cs.check_torrent({"torrent_url": self.base + "/t.torrent", "btih": "0" * 40})

    def test_http_file_and_html(self):
        self.assertEqual(cs.check_http(self.base + "/file.mkv"), "ok")
        with self.assertRaises(cs.Fail):
            cs.check_http(self.base + "/page.html")

    def test_fallback_used_and_reordered(self):
        master = {"entries": [{"id": "c1", "kind": "channel", "type": "hls", "url": self.base + "/dead.m3u8",
                               "fallback": [self.base + "/live/master.m3u8"]}]}
        out, dead, rec = cs.run(master, None, "2026-11-19", hyst=1)
        e = out["entries"][0]
        self.assertEqual(e["status"], "ok")
        self.assertEqual(e["url"], self.base + "/live/master.m3u8")
        self.assertEqual(e["fallback"], [self.base + "/dead.m3u8"])
        self.assertEqual(dead, [])

    def test_hysteresis_and_recovery(self):
        entry = {"id": "c1", "kind": "channel", "type": "hls", "url": self.base + "/dead.m3u8", "fallback": []}
        prev = {"entries": [{"id": "c1", "status": "ok", "fail_count": 0, "last_ok": "2026-11-18"}]}
        out1, dead1, _ = cs.run({"entries": [entry]}, prev, "2026-11-19", hyst=2)
        self.assertEqual(out1["entries"][0]["status"], "ok")          # первая неудача: ещё не мёртв
        self.assertEqual(out1["entries"][0]["fail_count"], 1)
        self.assertEqual(dead1, [])
        out2, dead2, _ = cs.run({"entries": [entry]}, out1, "2026-11-20", hyst=2)
        self.assertEqual(out2["entries"][0]["status"], "dead")        # вторая подряд: мёртв
        self.assertEqual([d[0] for d in dead2], ["c1"])
        self.assertEqual(out2["entries"][0]["last_ok"], "2026-11-18")
        live = dict(entry, url=self.base + "/live/master.m3u8")
        out3, dead3, rec3 = cs.run({"entries": [live]}, out2, "2026-11-21", hyst=2)
        self.assertEqual(out3["entries"][0]["status"], "ok")
        self.assertEqual(rec3, ["c1"])

    def test_cli_writes_files_and_issue(self):
        import tempfile
        d = tempfile.mkdtemp()
        mp = os.path.join(d, "m.json")
        out = os.path.join(d, "s.json")
        iss = os.path.join(d, "issue.md")
        json.dump({"entries": [{"id": "d1", "kind": "channel", "type": "hls", "url": self.base + "/dead.m3u8", "fallback": []}]}, open(mp, "w"))
        cs.main(["--master", mp, "--out", out, "--issue", iss, "--hyst", "1"])
        self.assertTrue(os.path.isfile(iss))
        self.assertIn("d1", open(iss, encoding="utf-8").read())
        self.assertEqual(json.load(open(out, encoding="utf-8"))["entries"][0]["status"], "dead")


class TestSign(unittest.TestCase):
    def test_sign_verify_forged(self):
        seed = bytes(range(32))
        pub = ed.public_key(seed)
        data = b'{"schema":1}\n'
        sig = ed.sign(seed, data)
        self.assertTrue(ed.verify(pub, data, sig))
        self.assertFalse(ed.verify(pub, data + b" ", sig))
        bad = bytearray(sig)
        bad[5] ^= 0x01
        self.assertFalse(ed.verify(pub, data, bytes(bad)))
        self.assertFalse(ed.verify(pub, data, b"short"))
        self.assertFalse(ed.verify(b"\x00" * 31, data, sig))


if __name__ == "__main__":
    unittest.main()
