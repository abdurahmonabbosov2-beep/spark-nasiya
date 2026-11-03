"""MoySklad'dan nasiya ma'lumotlarini yig'ib, hisoblab beradi."""
import os, re, time, threading, datetime as dt
from collections import defaultdict
import requests
try:
    import orjson
    _loads = orjson.loads
except ImportError:  # pragma: no cover
    import json
    _loads = json.loads
from concurrent.futures import ThreadPoolExecutor

TOKEN = os.environ.get("MOYSKLAD_TOKEN", "")
B = "https://api.moysklad.ru/api/remap/1.2/"
TZ = dt.timezone(dt.timedelta(hours=5))  # Toshkent

# Qarzdorlar ro'yxatiga kirmaydiganlar (nomida shu so'z bo'lsa)
EXCLUDE_WORDS = re.compile(os.environ.get(
    "EXCLUDE_WORDS", r"oylik|xitoy|zavod|postav|ta.?minot"), re.I)
# Teg -> sotuvchi ismi (teg bo'lmasa oxirgi otgruzkani kim qilgan bo'lsa o'sha)
TAG_SELLER = {"ulugbek": "Ulug'bek", "saidmalik": "Saidmalik", "islomxon": "Islomxon",
              "akramjon": "Akram", "ma'rufxon": "Marufxon", "abdulbosit": "Abbosxon"}

DEM_DAYS = int(os.environ.get("DEM_DAYS", "100"))     # muddat sanalarini qidirish uchun necha kunlik otgruzka
PAY_DAYS = int(os.environ.get("PAY_DAYS", "75"))     # kalendarda ko'rinadigan tushumlar (necha kun orqaga)

S = requests.Session()
S.headers.update({"Authorization": "Bearer " + TOKEN, "Accept-Encoding": "gzip"})


def _get(path, params=None):
    for i in range(5):
        try:
            r = S.get(B + path, params=params, timeout=90)
            if r.status_code == 429:
                time.sleep(2 + i * 2); continue
            r.raise_for_status(); return _loads(r.content)
        except requests.RequestException:
            if i == 4: raise
            time.sleep(3)


def _all(path, params, slim=None):
    """Hamma sahifani parallel (4 tadan) yuklaydi — tezroq."""
    conv = (lambda rows: [slim(r) for r in rows]) if slim else (lambda rows: rows)
    first = _get(path, dict(params, limit=1000, offset=0))
    out = conv(first["rows"])
    size = first.get("meta", {}).get("size", len(first["rows"]))
    offs = list(range(1000, size, 1000))
    if offs:
        with ThreadPoolExecutor(4) as ex:
            for rows in ex.map(lambda o: conv(_get(path, dict(params, limit=1000, offset=o))["rows"]), offs):
                out += rows
    return out


def _id(meta_obj): return meta_obj["meta"]["href"].rsplit("/", 1)[-1].split("?")[0]


# Xotirani tejash: har hujjatdan faqat kerakli maydonlar saqlanadi
def slim_doc(d):
    return {"id": d["id"], "name": d.get("name"), "moment": d["moment"], "sum": d.get("sum", 0),
            "agent": _id(d["agent"]) if d.get("agent") else None,
            "owner": _id(d["owner"]) if d.get("owner") else None,
            "description": (d.get("description") or "")[:200], "deleted": d.get("deleted")}


def slim_cp(c):
    return {"id": _id(c), "name": c.get("name", ""), "phone": c.get("phone", ""), "tags": c.get("tags", [])}
def _day(moment): return moment[:10]
def _since(days): return (dt.datetime.now(TZ) - dt.timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")


DATE_RE = re.compile(r"(?<![\d.])(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?![\d])")


def parse_due(text, ship_day):
    """Izohdan eng oxirgi sanani topadi: 15.11, 15.11.26, 15.11.2026"""
    if not text: return None
    ship = dt.date.fromisoformat(ship_day)
    best = None
    for d, m, y in DATE_RE.findall(text):
        d, m = int(d), int(m)
        if not (1 <= d <= 31 and 1 <= m <= 12): continue
        try:
            if y:
                y = int(y); y = y + 2000 if y < 100 else y
                cand = dt.date(y, m, d)
            else:
                cand = dt.date(ship.year, m, d)
                if cand < ship - dt.timedelta(days=20):
                    cand = dt.date(ship.year + 1, m, d)
        except ValueError:
            continue
        # otgruzkadan oldingi sana — muddat emas (masalan "yuk ketdi" sanasi)
        if cand < ship - dt.timedelta(days=1) or cand > ship + dt.timedelta(days=200): continue
        best = cand if best is None or cand > best else best
    return best.isoformat() if best else None


def log(msg):
    print(dt.datetime.now(TZ).strftime("%H:%M:%S"), "[nasiya]", msg, flush=True)


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.ready = False
        self.error = None
        self.updated = None
        self.emps = {}; self.cps = {}; self.suppliers = set()
        self.balance = {}; self.demands = {}; self.pays = {}
        self.view = None
        self._last_full = 0; self._mark = None

    # ---------- yuklash ----------
    def full_load(self):
        log("yuklash boshlandi")
        self.emps = {_id(e): e["name"] for e in _all("entity/employee", {})}
        self.cps = {c["id"]: c for c in _all("entity/counterparty", {}, slim_cp)}
        self.suppliers = set(_all("entity/supply", {"filter": "moment>=" + _since(int(os.environ.get("SUP_DAYS", "500")))},
                                  lambda s: _id(s["agent"])))
        log(f"kontragentlar {len(self.cps)}, postavshiklar {len(self.suppliers)}")
        self.demands = {d["id"]: d for d in _all("entity/demand", {"filter": "moment>=" + _since(DEM_DAYS)}, slim_doc)}
        log(f"otgruzkalar {len(self.demands)}")
        # qarzlar tayyor — tushumlarni kutmasdan sahifani ko'rsatamiz
        self._publish_balance()
        pays = {}
        for kind in ("paymentin", "cashin"):
            for p in _all("entity/" + kind, {"filter": "moment>=" + _since(PAY_DAYS)}, slim_doc):
                pays[p["id"]] = p
        self.pays = pays
        log(f"to'lovlar {len(self.pays)}")
        self._last_full = time.time()

    def delta_load(self, mark):
        f = "updated>=" + mark
        for d in _all("entity/demand", {"filter": f}, slim_doc): self.demands[d["id"]] = d
        for kind in ("paymentin", "cashin"):
            for p in _all("entity/" + kind, {"filter": f}, slim_doc): self.pays[p["id"]] = p
        for c in _all("entity/counterparty", {"filter": f}, slim_cp): self.cps[c["id"]] = c
        # o'chirilganlarni tozalash uchun kunda bir marta to'liq yuklash
        if time.time() - self._last_full > 6 * 3600: self.full_load()

    def _publish_balance(self):
        self.balance = {_id(r["counterparty"]): -r["balance"] / 100
                        for r in _all("report/counterparty", {}) if r.get("balance", 0) < 0}
        view = self.compute()
        with self.lock:
            self.view = view; self.ready = True
            self.updated = dt.datetime.now(TZ).strftime("%H:%M") + " (tushumlar yuklanmoqda)"

    def refresh(self):
        mark = (dt.datetime.now(TZ) - dt.timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        if not self.ready: self.full_load()
        else: self.delta_load(self._mark)
        self.balance = {_id(r["counterparty"]): -r["balance"] / 100
                        for r in _all("report/counterparty", {}) if r.get("balance", 0) < 0}
        view = self.compute()
        with self.lock:
            self.view = view; self._mark = mark
            self.updated = dt.datetime.now(TZ).strftime("%H:%M")
            self.ready = True; self.error = None

    # ---------- hisob ----------
    def is_customer(self, cid):
        c = self.cps.get(cid)
        name = c["name"] if c else ""
        return cid not in self.suppliers and not EXCLUDE_WORDS.search(name)

    def compute(self):
        today = dt.datetime.now(TZ).date()
        # mijoz -> otgruzkalar (yangidan eskiga)
        by_cp = defaultdict(list)
        for d in self.demands.values():
            if d.get("deleted"): continue
            by_cp[d["agent"]].append(d)
        for v in by_cp.values(): v.sort(key=lambda d: d["moment"], reverse=True)

        def seller_of(cid):
            for d in by_cp.get(cid, []):
                n = self.emps.get(d["owner"], "")
                if n and n.lower() != "admin": return n
            for t in (self.cps.get(cid) or {}).get("tags", []):
                if t in TAG_SELLER: return TAG_SELLER[t]
            return "Biriktirilmagan"

        customers = []
        open_docs = []          # qarzga qolgan otgruzkalar (FIFO: qarz eng yangi otgruzkalarda)
        for cid, debt in self.balance.items():
            if debt < 1000 or not self.is_customer(cid): continue
            c = self.cps.get(cid, {})
            left = debt; docs = []
            for d in by_cp.get(cid, []):
                if left <= 0: break
                amt = min(left, d["sum"] / 100); left -= amt
                docs.append({"doc": d["name"], "ship": _day(d["moment"]), "amount": round(amt),
                             "due": parse_due(d.get("description"), _day(d["moment"])),
                             "note": (d.get("description") or "")[:120]})
            if left > 0:
                docs.append({"doc": "eski qarz", "ship": None, "amount": round(left), "due": None, "note": ""})
            dues = [x["due"] for x in docs if x["due"]]
            nearest = min(dues) if dues else None
            seller = seller_of(cid)
            customers.append({"id": cid, "name": c.get("name", "?"), "phone": c.get("phone", ""),
                              "seller": seller, "debt": round(debt), "docs": docs, "due": nearest,
                              "last_ship": docs[0]["ship"] if docs else None})
            for x in docs: open_docs.append(dict(x, cid=cid, client=c.get("name", "?"), seller=seller))

        # kunlar bo'yicha: tushum va kelishi kerak
        pay_day = defaultdict(float); pay_cp_day = defaultdict(float)
        for p in self.pays.values():
            cid = p["agent"]
            if not cid or not self.is_customer(cid): continue
            dday = _day(p["moment"]); pay_day[dday] += p["sum"] / 100
            pay_cp_day[(cid, dday)] += p["sum"] / 100

        seller_by_cp = {c["id"]: c["seller"] for c in customers}
        due_all = defaultdict(list)   # sana -> barcha otgruzkalar shu kunga muddat qo'yilgan (to'langan/to'lanmagan)
        for d in self.demands.values():
            if d.get("deleted"): continue
            cid = d["agent"]
            if not cid or not self.is_customer(cid): continue
            due = parse_due(d.get("description"), _day(d["moment"]))
            if not due: continue
            due_all[due].append({"cid": cid, "client": (self.cps.get(cid) or {}).get("name", "?"),
                                 "seller": seller_by_cp.get(cid) or seller_of(cid),
                                 "doc": d["name"], "ship": _day(d["moment"]), "amount": round(d["sum"] / 100),
                                 "open": any(o["doc"] == d["name"] and o["cid"] == cid for o in open_docs)})
        days = {}
        for k in set(pay_day) | set(due_all):
            items = due_all.get(k, [])
            for it in items:
                it["paid_that_day"] = round(pay_cp_day.get((it["cid"], k), 0))
            days[k] = {"income": round(pay_day.get(k, 0)),
                       "due": sum(i["amount"] for i in items), "items": items}

        cut = (today - dt.timedelta(days=PAY_DAYS)).isoformat()
        payer_names = {a: (self.cps.get(a) or {}).get("name", "?") for (a, b) in pay_cp_day if b >= cut}
        return {"today": today.isoformat(), "customers": customers, "days": days, "names": payer_names,
                "income_by_cp": {f"{a}|{b}": round(v) for (a, b), v in pay_cp_day.items()
                                 if b >= cut}}


STORE = Store()


def loop():
    while True:
        try: STORE.refresh()
        except Exception as e:
            STORE.error = str(e)[:300]
            log("XATO: " + STORE.error)
        time.sleep(int(os.environ.get("REFRESH_SECONDS", "300")))


_started_pid = None
_start_lock = threading.Lock()


def start():
    """Har bir jarayonda faqat bir marta fon yangilanishini ishga tushiradi."""
    global _started_pid
    if _started_pid == os.getpid():
        return
    with _start_lock:
        if _started_pid == os.getpid():
            return
        _started_pid = os.getpid()
        threading.Thread(target=loop, daemon=True).start()
