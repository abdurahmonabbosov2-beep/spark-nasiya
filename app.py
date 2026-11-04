# Spark Plus nasiya — MoySklad "Взаиморасчеты" asosidagi dashboard
import os, re, time, base64, threading
from datetime import datetime, timedelta, timezone
import requests
from flask import Flask, jsonify, request, Response, send_file

TOKEN = os.environ.get("MOYSKLAD_TOKEN", "")
PASSWORD = os.environ.get("APP_PASSWORD", "")
API = "https://api.moysklad.ru/api/remap/1.2"
EXCLUDE = [w.strip().lower() for w in os.environ.get("EXCLUDE_WORDS", "oylik|xitoy|zavod|postav|ta'minot").split("|") if w.strip()]  # nomida shu so'z bo'lsa nasiyaga kirmaydi
DAYS_BACK = 180
REFRESH_SEC = 300
TZ = timezone(timedelta(hours=5))  # Toshkent
HERE = os.path.dirname(os.path.abspath(__file__))

def today(add=0):
    return (datetime.now(TZ) + timedelta(days=add)).strftime("%Y-%m-%d")

def add_days(s, n):
    return (datetime.strptime(s, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")

def id_of(href):
    return (href or "").split("/")[-1].split("?")[0]

def href_of(obj):
    return ((obj or {}).get("meta") or {}).get("href", "")

def excluded(name):
    n = (name or "").lower()
    return any(w in n for w in EXCLUDE)

S = requests.Session()
S.headers.update({"Authorization": "Bearer " + TOKEN, "Accept-Encoding": "gzip"})

def ms(path, params=None):
    for i in range(6):
        r = S.get(API + path, params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(1.5 * (i + 1)); continue
        if not r.ok:
            raise RuntimeError(f"MoySklad {r.status_code}: {r.text[:300]}")
        return r.json()
    raise RuntimeError("MoySklad: juda ko'p so'rov (429)")

def all_rows(path, extra=None):
    rows, offset = [], 0
    while True:
        p = dict(extra or {}); p.update(limit=1000, offset=offset)
        d = ms(path, p)
        batch = d.get("rows") or []
        rows += batch
        if len(batch) < 1000: return rows
        offset += 1000

DUE_RE = re.compile(r"(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?")
def parse_due(desc, ship):
    if not desc: return None
    for m in DUE_RE.finditer(desc):
        day, mon = int(m.group(1)), int(m.group(2))
        if not (1 <= day <= 31 and 1 <= mon <= 12): continue
        year = int(m.group(3)) if m.group(3) else int(ship[:4])
        if year < 100: year += 2000
        try: due = datetime(year, mon, day).strftime("%Y-%m-%d")
        except ValueError: continue
        if not m.group(3) and due < add_days(ship, -2):  # dekabr -> yanvar
            try: due = datetime(year + 1, mon, day).strftime("%Y-%m-%d")
            except ValueError: continue
        return due
    return None

cache = {"ready": False, "error": None}

def build():
    global cache
    since = {"filter": f"moment>={add_days(today(), -DAYS_BACK)} 00:00:00"}
    report = all_rows("/report/counterparty")
    employees = all_rows("/entity/employee")
    demands = all_rows("/entity/demand", since)
    pays = all_rows("/entity/paymentin", since) + all_rows("/entity/cashin", since)

    emp = {e["id"]: e.get("fullName") or e.get("name") for e in employees}
    agents = {}
    for row in report:
        c = row.get("counterparty") or {}
        aid = c.get("id") or id_of(href_of(c))
        agents[aid] = {"name": c.get("name") or "—", "balance": (row.get("balance") or 0) / 100,
                       "excluded": excluded(c.get("name"))}

    planned, last_dem, last_due = [], {}, {}
    for d in demands:
        aid = id_of(href_of(d.get("agent")))
        a = agents.get(aid)
        if a and a["excluded"]: continue
        seller = emp.get(id_of(href_of(d.get("owner"))), "Biriktirilmagan")
        mom = d.get("moment", ""); ship = mom[:10]
        if aid not in last_dem or mom > last_dem[aid]["moment"]:
            last_dem[aid] = {"moment": mom, "seller": seller}
        due = parse_due(d.get("description"), ship)
        if due:
            planned.append({"date": due, "agentId": aid, "amount": (d.get("sum") or 0) / 100, "doc": d.get("name"), "shipDate": ship})
            if aid not in last_due or mom > last_due[aid]["moment"]:
                last_due[aid] = {"moment": mom, "due": due, "doc": d.get("name"), "shipDate": ship}

    payments = []
    for p in pays:
        aid = id_of(href_of(p.get("agent")))
        a = agents.get(aid)
        if not a or a["excluded"]: continue
        payments.append({"date": p.get("moment", "")[:10], "agentId": aid, "amount": (p.get("sum") or 0) / 100})

    seller_of = lambda aid: last_dem[aid]["seller"] if aid in last_dem else "Biriktirilmagan"
    t = today(); tdt = datetime.strptime(t, "%Y-%m-%d")
    debtors = []
    for aid, a in agents.items():
        if a["excluded"] or a["balance"] >= -0.5: continue
        ld = last_due.get(aid)
        due = ld["due"] if ld else None
        days_left = (datetime.strptime(due, "%Y-%m-%d") - tdt).days if due else None
        term = (datetime.strptime(ld["due"], "%Y-%m-%d") - datetime.strptime(ld["shipDate"], "%Y-%m-%d")).days if ld else None
        debtors.append({"id": aid, "name": a["name"], "debt": -a["balance"], "seller": seller_of(aid),
                        "due": due, "daysLeft": days_left, "term": term, "doc": ld["doc"] if ld else None})
    debtors.sort(key=lambda x: -x["debt"])

    ids = {p["agentId"] for p in planned} | {p["agentId"] for p in payments}
    meta = {i: {"name": agents[i]["name"] if i in agents else "—", "seller": seller_of(i)} for i in ids}
    cache = {"ready": True, "error": None, "updatedAt": datetime.now(timezone.utc).isoformat(), "today": t,
             "debtors": debtors, "planned": planned, "payments": payments, "agents": meta, "excluded": EXCLUDE}
    print(f"[{datetime.now(TZ)}] yangilandi: {len(debtors)} qarzdor", flush=True)

lock = threading.Lock()
def safe_build():
    with lock:
        try: build()
        except Exception as e:
            print("XATO:", e, flush=True); cache["error"] = str(e)

def loop():
    while True:
        safe_build(); time.sleep(REFRESH_SEC)

if TOKEN:
    threading.Thread(target=loop, daemon=True).start()
else:
    cache["error"] = "MOYSKLAD_TOKEN o'rnatilmagan (Render -> Environment)"

app = Flask(__name__)

@app.before_request
def auth():
    if not PASSWORD: return
    h = request.headers.get("Authorization", "")
    try: pw = base64.b64decode(h.replace("Basic ", "")).decode().split(":", 1)[1]
    except Exception: pw = None
    if pw != PASSWORD:
        return Response("Parol kerak", 401, {"WWW-Authenticate": 'Basic realm="Spark Plus Nasiya"'})

@app.get("/")
def index(): return send_file(os.path.join(HERE, "index.html"))

@app.get("/api/data")
def data(): return jsonify(cache)

@app.post("/api/refresh")
def refresh():
    safe_build(); return jsonify({"ok": not cache.get("error"), "error": cache.get("error")})
