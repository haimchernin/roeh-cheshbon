#!/usr/bin/env python3
"""הדוח לרואה החשבון: PDF מסכם + PDF אחד עם כל הקבלות.

שימוש:  python3 make_report.py <תיקיית התקופה>
למשל:   python3 make_report.py "2026-09-10"

קורא את data.json ואת החלטות.json מתיקיית התקופה, ויוצר בתוכה את התיקייה לרואה-החשבון/ עם:
  דוח-<תקופה>.pdf          כמה נכנס, כמה יצא, מע"מ, קבלות שיש ושחסרות, לפי קטגוריות
  כל-הקבלות-<תקופה>.pdf    עמוד תוכן ואחריו כל הקבלות בקובץ אחד (הקבצים הנפרדים נשארים בקבלות/)
  דוח-<תקופה>.csv          אותם נתונים לאקסל

צריך: pip install pypdf pillow playwright && python3 -m playwright install chromium
"""
import csv
import html
import io
import json
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

NOT_EXPENSE = {"draw", "loan", "tax", "test"}
NO_VAT_CATS = {"ביטוחים ובריאות", "מימון", "מסים", "העברות ומשיכות"}
SRC = {"biz_card": "כרטיס עסקי", "biz_bank": "חשבון העסק", "private_card": "כרטיס פרטי",
       "wife_card": "כרטיס של בן/בת הזוג", "private_bank": "חשבון פרטי"}
MONTHS = ["", "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


def load(period_dir: Path):
    data = json.loads((period_dir / "data.json").read_text(encoding="utf-8"))
    dec_path = period_dir / "החלטות.json"
    dec = json.loads(dec_path.read_text(encoding="utf-8")) if dec_path.exists() else {}
    for k in ("biz", "rec", "todo", "skip", "notes"):
        dec.setdefault(k, {})
    return data, dec


def money(n):
    return f"₪{n:,.2f}"


def dm(iso):
    return f"{iso[8:10]}.{iso[5:7]}"


def build(data, dec):
    """Same rules as the screen (מסך.html), so the PDF and the screen always agree."""
    src_names = {**SRC, **(data.get("srcNames") or {})}
    is_biz = lambda e: dec["biz"][e["id"]] if e["id"] in dec["biz"] else bool(e.get("biz"))
    has_rec = lambda e: bool(e.get("receipt")) or bool(dec["rec"].get(e["id"]))

    def vat_of(e):
        if isinstance(e.get("vat"), (int, float)):
            return float(e["vat"])
        if (e.get("foreign") or e.get("orig") or e.get("cat") in NO_VAT_CATS or e.get("kind") in NOT_EXPENSE
                or e.get("kind") == "doc" or re.search(r"עיריית|עירית", e["vendor"])):
            return 0.0
        return round(e["amount"] * 18 / 118, 2)

    exp = sorted([e for e in data["expenses"] if is_biz(e) and e.get("kind") not in NOT_EXPENSE], key=lambda e: e["date"])
    inc = sorted([i for i in data["income"] if not i.get("noise") and i.get("count", True) is not False], key=lambda i: i["date"])
    issues = [i for i in data["income"] if i.get("status") in ("issue", "action", "missing")]
    todo_open = [t for t in data.get("todo", []) if not dec["todo"].get(t["id"]) and not dec["skip"].get(t["id"])]
    for e in exp:
        e["_vat"], e["_rec"], e["_src"] = vat_of(e), has_rec(e), src_names.get(e["src"], e["src"])
    return dict(exp=exp, inc=inc, issues=issues, todo=todo_open, notes=dec["notes"])


# ---------------------------------------------------------------- report html
CSS = """
@page{size:A4;margin:16mm 14mm}
*{box-sizing:border-box}
body{font-family:'Heebo',Arial,sans-serif;color:#0f1419;direction:rtl;font-size:11pt;line-height:1.5;margin:0}
h1{font-size:24pt;margin:0 0 2px;font-weight:800}
h2{font-size:14pt;margin:22px 0 8px;font-weight:800;border-bottom:2px solid #0f1419;padding-bottom:4px}
.sub{color:#536471;font-size:10pt}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px}
.box{background:#f2f4f5;border-radius:12px;padding:12px 14px}
.box .k{font-size:10pt;font-weight:700;color:#536471}
.box .v{font-size:20pt;font-weight:800}
.box .s{font-size:9.5pt;color:#536471}
.miss{background:#fdedf2;padding:0 5px;border-radius:4px;font-weight:700;color:#e0245e}
table{width:100%;border-collapse:collapse;font-size:9.5pt}
th{text-align:right;color:#8b98a5;font-weight:500;border-bottom:1px solid #cfd9de;padding:4px}
td{border-bottom:1px solid #eff3f4;padding:4px;vertical-align:top}
td.n,th.n{text-align:left;white-space:nowrap;font-variant-numeric:tabular-nums}
tr.tot td{font-weight:800;border-top:2px solid #0f1419;border-bottom:0}
.note{color:#536471;font-size:9pt}
.foot{margin-top:24px;font-size:8.5pt;color:#8b98a5;border-top:1px solid #eff3f4;padding-top:6px}
.idx td{font-size:9.5pt}
"""


def page(title, body):
    return f"""<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<link href="https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;700;800&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>{body}</body></html>"""


def report_html(data, b, business):
    """Classic accountant report: income list, expense list, totals. Nothing else."""
    e = lambda s: html.escape(str(s or ""))
    inc, exp = b["inc"], b["exp"]
    out = [f"""<style>
body{{font-family:Arial,sans-serif;font-size:10.5pt}}
h1{{font-size:16pt;margin:0}}
h2{{font-size:12pt;border-bottom:1.5px solid #000;padding-bottom:3px;margin:22px 0 6px}}
th{{color:#000;font-weight:700;border-bottom:1px solid #000}}
td{{border-bottom:1px solid #ddd}}
</style>
<h1>{e(business) or 'דוח'} · {e(data['period'])}</h1>
<div class="sub">הכנסות והוצאות · הופק {date.today():%d.%m.%Y}</div>

<h2>הכנסות</h2>
<table><tr><th>תאריך</th><th>מסמך</th><th>לקוח</th><th class='n'>לפני מע"מ</th><th class='n'>מע"מ</th><th class='n'>סה"כ</th></tr>"""]
    for i in inc:
        out.append(f"<tr><td>{dm(i['date'])}.{i['date'][2:4]}</td><td>{e(i['type'])} {e(i.get('doc'))}</td><td>{e(i['client'])}</td>"
                   f"<td class='n'>{money(i['net'] + i['exempt'])}</td><td class='n'>{money(i['vat'])}</td><td class='n'>{money(i['total'])}</td></tr>")
    out.append(f"<tr class='tot'><td colspan='3'>סה\"כ הכנסות</td><td class='n'>{money(sum(i['net'] + i['exempt'] for i in inc))}</td>"
               f"<td class='n'>{money(sum(i['vat'] for i in inc))}</td><td class='n'>{money(sum(i['total'] for i in inc))}</td></tr></table>")

    out.append("<h2>הוצאות</h2><table><tr><th>תאריך</th><th>ספק</th><th class='n'>סכום</th><th>קבלה</th></tr>")
    for x in exp:
        out.append(f"<tr><td>{dm(x['date'])}.{x['date'][2:4]}</td><td>{e(x['vendor'])}</td><td class='n'>{money(x['amount'])}</td><td>{'✓' if x['_rec'] else 'אין'}</td></tr>")
    out.append(f"<tr class='tot'><td colspan='2'>סה\"כ הוצאות</td><td class='n'>{money(sum(x['amount'] for x in exp))}</td><td></td></tr></table>")
    return page(f"דוח {data['period']}", "".join(out))


def index_html(data, rows, business):
    e = lambda s: html.escape(str(s or ""))
    body = [f"<h1>כל הקבלות · {e(data['period'])}</h1><div class='sub'>{e(business) + ' · ' if business else ''}{len(rows)} קבלות בקובץ</div>",
            "<h2>תוכן</h2><table class='idx'><tr><th>#</th><th>תאריך</th><th>ספק</th><th>קטגוריה</th><th class='n'>סכום</th><th class='n'>עמוד</th></tr>"]
    for n, r in enumerate(rows, 1):
        body.append(f"<tr><td>{n}</td><td>{dm(r['date'])}</td><td>{e(r['vendor'])}</td><td>{e(r['cat'])}</td>"
                    f"<td class='n'>{e(r['amount'])}</td><td class='n'>{r.get('page', '')}</td></tr>")
    body.append("</table>")
    return page("כל הקבלות", "".join(body))


# ---------------------------------------------------------------- pdf helpers
def html_to_pdf(html_text, out_path: Path):
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory() as tmp:          # Chrome can choke on Hebrew paths, so render from /tmp
        src = Path(tmp) / "page.html"
        src.write_text(html_text, encoding="utf-8")
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            pg = browser.new_page()
            pg.goto(src.as_uri())
            pg.wait_for_timeout(600)                     # let the web font load if online
            pdf = pg.pdf(format="A4", print_background=True, prefer_css_page_size=True)
            browser.close()
    out_path.write_bytes(pdf)
    return out_path


def file_pages(path: Path):
    """Return a list of pypdf pages for one receipt file (PDF or image)."""
    from pypdf import PdfReader
    if path.suffix.lower() == ".pdf":
        return list(PdfReader(str(path)).pages)
    if path.suffix.lower() in IMAGE_EXT:
        from PIL import Image
        img = Image.open(path)
        img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PDF", resolution=150)
        buf.seek(0)
        return list(PdfReader(buf).pages)
    return []


def merged_receipts(period_dir: Path, data, b, business, out_path: Path):
    from pypdf import PdfReader, PdfWriter
    rec_dir = period_dir / "קבלות"
    rows, used = [], set()
    for x in b["exp"]:
        if x.get("receipt"):
            p = period_dir / x["receipt"]
            if p.exists() and p not in used:
                used.add(p)
                rows.append(dict(date=x["date"], vendor=x["vendor"], cat=x.get("cat") or "", amount=money(x["amount"]), path=p))
    extra = sorted(p for p in rec_dir.glob("*") if p.is_file() and p not in used and (p.suffix.lower() == ".pdf" or p.suffix.lower() in IMAGE_EXT)) if rec_dir.exists() else []
    for p in extra:                                       # receipts found without a matching charge
        m = re.match(r"(\d{4}-\d{2}-\d{2})_(.+?)_([^_]+)$", p.stem)
        rows.append(dict(date=m.group(1) if m else "0000-00-00", vendor=(m.group(2) if m else p.stem).replace("-", " ") + " (בלי חיוב בתדפיס)",
                         cat="", amount=m.group(3) if m else "", path=p))
    pages = {id(r): file_pages(r["path"]) for r in rows}
    rows = [r for r in rows if pages[id(r)]]

    with tempfile.TemporaryDirectory() as tmp:
        idx = Path(tmp) / "index.pdf"
        html_to_pdf(index_html(data, rows, business), idx)       # first pass: how many pages is the index?
        start = len(PdfReader(str(idx)).pages) + 1
        for r in rows:
            r["page"] = start
            start += len(pages[id(r)])
        html_to_pdf(index_html(data, rows, business), idx)       # second pass with real page numbers
        w = PdfWriter()
        for pg in PdfReader(str(idx)).pages:
            w.add_page(pg)
        for r in rows:
            for pg in pages[id(r)]:
                w.add_page(pg)
        with open(out_path, "wb") as f:
            w.write(f)
    return len(rows)


def write_csv(b, out_path: Path):
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["סוג", "קטגוריה", "תאריך", "שם", "מסמך / מקור", "לפני מע\"מ", "מע\"מ (בהוצאות: משוער)", "סה\"כ ₪", "קבלה"])
        for i in b["inc"]:
            w.writerow(["הכנסה", i.get("cat", ""), i["date"], i["client"], f"{i['type']} {i.get('doc') or ''}", i["net"] + i["exempt"], i["vat"], i["total"], ""])
        for x in b["exp"]:
            w.writerow(["הוצאה", x.get("cat", ""), x["date"], x["vendor"], x["_src"], round(x["amount"] - x["_vat"], 2), x["_vat"], x["amount"],
                        x.get("receipt") or ("יש" if x["_rec"] else "חסר")])


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    period_dir = Path(sys.argv[1]).expanduser().resolve()
    data, dec = load(period_dir)
    biz_file = next((p / "עסק.json" for p in (period_dir, period_dir.parent) if (p / "עסק.json").exists()), None)
    business = json.loads(biz_file.read_text(encoding="utf-8")).get("name", "") if biz_file else data.get("business", "")
    b = build(data, dec)
    key = data.get("periodKey", period_dir.name)
    out = period_dir / "לרואה-החשבון"
    out.mkdir(exist_ok=True)
    html_to_pdf(report_html(data, b, business), out / f"דוח-{key}.pdf")
    n = merged_receipts(period_dir, data, b, business, out / f"כל-הקבלות-{key}.pdf")
    write_csv(b, out / f"דוח-{key}.csv")
    print(f"נוצר ב-{out}:\n  דוח-{key}.pdf\n  כל-הקבלות-{key}.pdf ({n} קבלות)\n  דוח-{key}.csv")


if __name__ == "__main__":
    main()
