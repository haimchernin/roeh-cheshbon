#!/usr/bin/env python3
"""מסך לרואה החשבון: שרת מקומי קטן.

מבנה התיקייה של העסק:
  <העסק>/
    עסק.json            שם העסק והתקופה הנוכחית: {"name": "...", "current": "2026-09-10"}
    זיכרון.md            מה Claude זוכר על העסק מתקופה לתקופה
    מסך.html, שנה.html, serve.py, make_report.py, CLAUDE.md
    2026-09-10/          תיקייה לכל תקופה: data.json, החלטות.json, קבצים/, סרוקות/, קבלות/, לרואה-החשבון/

הרצה: python3 serve.py  ←  http://localhost:8765 (המסך), http://localhost:8765/שנה.html (השנה)
מאזין רק למחשב הזה (127.0.0.1), בלי ספריות חיצוניות.
"""
import json
import os
import sys
import threading
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

ROOT = Path(__file__).resolve().parent
BUSINESS = ROOT / "עסק.json"
EMPTY = {"biz": {}, "rec": {}, "todo": {}, "skip": {}, "notes": {}}
NOT_EXPENSE = {"draw", "loan", "tax", "test"}
LOCK = threading.Lock()
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
ALLOWED_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path, obj):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def periods():
    """All period folders (a folder with data.json). A business that keeps data.json in the root is one period."""
    found = sorted(p for p in ROOT.iterdir() if p.is_dir() and (p / "data.json").exists())
    if not found and (ROOT / "data.json").exists():
        found = [ROOT]
    return found


def period_dir(name=None):
    all_p = periods()
    if name is not None:
        for p in all_p:
            if (p.name if p != ROOT else "") == name:
                return p
    current = read_json(BUSINESS, {}).get("current")
    for p in all_p:
        if p.name == current:
            return p
    return all_p[-1] if all_p else None


def rel(p):
    return "" if p == ROOT else p.name


def read_decisions(p):
    dec = read_json(p / "החלטות.json", {})
    for k in EMPTY:
        dec.setdefault(k, {})
    return dec


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, *args):
        pass

    def _host_ok(self):
        if self.headers.get("Host") in ALLOWED_HOSTS:
            return True
        self.send_error(403)
        return False

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._host_ok():
            return
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path == "/state":
            p = period_dir(q.get("p", [None])[0])
            biz = read_json(BUSINESS, {})
            base = {"business": biz.get("name", ""), "periods": [{"dir": rel(x), "period": read_json(x / "data.json", {}).get("period", x.name)} for x in periods()]}
            if p is None:
                return self._json({**base, "data": None, "dir": "", "decisions": EMPTY})
            try:
                data = read_json(p / "data.json", None)
            except json.JSONDecodeError as e:
                return self._json({"error": f"data.json לא תקין: {e}"}, 500)
            return self._json({**base, "data": data, "dir": rel(p), "decisions": read_decisions(p)})
        if url.path == "/year":
            return self._json(self._year())
        if url.path in ("/", "/index.html"):
            self.path = "/" + quote("מסך.html")
        return super().do_GET()

    def _year(self):
        """Everything the yearly screen needs: per period, the income and the business expenses (after the user's marks)."""
        out = []
        for p in periods():
            try:
                d = read_json(p / "data.json", {})
            except json.JSONDecodeError:
                continue
            dec = read_decisions(p)
            is_biz = lambda e: dec["biz"][e["id"]] if e["id"] in dec["biz"] else bool(e.get("biz"))
            out.append({
                "dir": rel(p), "period": d.get("period", p.name), "range": d.get("range"),
                "income": [{"date": i["date"], "net": i["net"] + i["exempt"], "vat": i["vat"], "cat": i.get("cat", "")}
                           for i in d.get("income", []) if not i.get("noise") and i.get("count", True) is not False],
                "expenses": [{"date": e["date"], "amount": e["amount"], "cat": e.get("cat", "")}
                             for e in d.get("expenses", []) if is_biz(e) and e.get("kind") not in NOT_EXPENSE],
            })
        return {"business": read_json(BUSINESS, {}).get("name", ""), "periods": out}

    def do_POST(self):
        if not self._host_ok():
            return
        if self.path != "/decide":
            return self._json({"error": "not found"}, 404)
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._json({"error": "json only"}, 415)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)
        kind, item_id, value = body.get("kind"), body.get("id"), body.get("value")
        if kind not in EMPTY or not isinstance(item_id, str):
            return self._json({"error": "bad request"}, 400)
        p = period_dir(body.get("dir"))
        if p is None:
            return self._json({"error": "אין תקופה"}, 404)
        with LOCK:
            dec = read_decisions(p)
            if kind == "notes":
                text = str(value or "").strip()[:2000]
                if text:
                    dec[kind][item_id] = text
                else:
                    dec[kind].pop(item_id, None)
            elif value is None:
                dec[kind].pop(item_id, None)
            else:
                dec[kind][item_id] = bool(value)
            dec["updated"] = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
            write_json(p / "החלטות.json", dec)
        return self._json({"ok": True, "decisions": dec})


if __name__ == "__main__":
    print(f"המסך פתוח: http://localhost:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
