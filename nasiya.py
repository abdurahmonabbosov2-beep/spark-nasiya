"""Nasiya (mijoz qarzlari) — MoySklad "Взаиморасчеты" asosida.

Qoidalar (Boburmirzo bilan kelishilgan):
- Qarz = Взаиморасчеты (report/counterparty) dagi manfiy saldo.
- Faqat mijozlar: nomida oylik/xitoy/zavod/postav/ta'minot bo'lsa, shuningdek
  "Dilshodjon 1008" (oylik) va "Xakimjon 0998" kirmaydi.
- Hech qachon otgruzka qilinmagan kontragent (demandsCount=0) — postavshik, kirmaydi.
- Muddat — otgruzka izohidagi sana (15.11 yoki 15.11.2026).
- Sotuvchi — mijozga oxirgi otgruzkani qilgan xodim (Admin hisobga olinmaydi), bo'lmasa teg.
"""
import os, re, threading, datetime as dt
from collections import defaultdict
import ms

EXCLUDE = re.compile(os.environ.get(
    "EXCLUDE_WORDS",
    r"oylik|xitoy|zavod|postav|ta.?minot|dilshodjon\s*1008|xakimjon\s*0998"), re.I)
TAG_SELLER = {"ulugbek": "Ulug'bek", "saidmalik": "Saidmalik", "islomxon": "Islomxon",
              "akramjon": "Akram", "ma'rufxon": "Marufxon", "abdulbosit": "Abbosxon"}
DEM_DAYS = int(os.environ.get("DEM_DAYS", "100"))
PAY_DAYS = int(os.environ.get("PAY_DAYS", "75"))

DATE_RE = re.compile(r"(?<![\d.])(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?![\d])")


def parse_due(text, ship_day):
    """Izohdan muddat sanasini topadi (eng oxirgi mos sana)."""
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
        if cand < ship - dt.timedelta(days=1) or cand > ship + dt.timedelta(days=200): continue
        best = cand if best is None or cand > best else best
    return best.isoformat() if best else None


GA_RE = re.compile(r"(?<![\d.,:])(\d{1,2})\s*-?\s*(?:ga|га|gacha|гача)\b", re.I)


def due_info(text, ship_day):
    """(sana, aniqmi). Aniq: "15.11". Taxminiy: "7 ga" — oyning 7-kuni (o'tib ketgan bo'lsa keyingi oy)."""
    d = parse_due(text, ship_day)
    if d: return d, True
    m = GA_RE.search(text or "")
    if not m: return None, False
    day = int(m.group(1))
    if not 1 <= day <= 31: return None, False
    ship = dt.date.fromisoformat(ship_day)
    y, mo = ship.year, ship.month
    for _ in range(2):
        try:
            cand = dt.date(y, mo, day)
            if cand >= ship: return cand.isoformat(), False
        except ValueError:
            pass
        mo += 1
        if mo == 13: y, mo = y + 1, 1
    return None, False


def slim_doc(d):
    return {"id": d["id"], "name": d.get("name"), "moment": d["moment"], "sum": d.get("sum", 0),
            "agent": ms.id_of(d.get("agent")), "owner": ms.id_of(d.get("owner")),
            "description": (d.get("description") or "")[:200], "deleted": d.get("deleted")}


def slim_cp(c):
    return {"id": c["id"], "name": c.get("name", ""), "phone": c.get("phone", ""), "tags": c.get("tags", [])}


def slim_bal(r):
    return {"id": ms.id_of(r["counterparty"]), "name": r["counterparty"].get("name", ""),
            "balance": r.get("balance", 0), "demands": r.get("demandsCount", 0)}


def since(days):
    return (ms.now() - dt.timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.ready = False; self.error = None; self.updated = None
        self.emps = {}; self.cps = {}; self.bal = {}
        self.demands = {}; self.pays = {}
        self.view = None; self._mark = None; self._last_full = 0

    def _load_balance(self):
        self.bal = {r["id"]: r for r in ms.all_rows("report/counterparty", {}, slim_bal)}

    def full_load(self):
        import time
        self.emps = {e["id"]: e["name"] for e in ms.all_rows("entity/employee", {})}
        self.cps = {c["id"]: c for c in ms.all_rows("entity/counterparty", {}, slim_cp)}
        self.demands = {d["id"]: d for d in ms.all_rows("entity/demand", {"filter": "moment>=" + since(DEM_DAYS)}, slim_doc)}
        pays = {}
        for kind in ("paymentin", "cashin"):
            for p in ms.all_rows("entity/" + kind, {"filter": "moment>=" + since(PAY_DAYS)}, slim_doc):
                pays[p["id"]] = p
        self.pays = pays
        self._last_full = time.time()

    def delta_load(self):
        import time
        f = "updated>=" + self._mark
        for d in ms.all_rows("entity/demand", {"filter": f}, slim_doc): self.demands[d["id"]] = d
        for kind in ("paymentin", "cashin"):
            for p in ms.all_rows("entity/" + kind, {"filter": f}, slim_doc): self.pays[p["id"]] = p
        for c in ms.all_rows("entity/counterparty", {"filter": f}, slim_cp): self.cps[c["id"]] = c
        if time.time() - self._last_full > 6 * 3600:  # o'chirilganlarni tozalash
            self.full_load()

    def refresh(self):
        mark = (ms.now() - dt.timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        if not self.ready or not self._mark: self.full_load()
        else: self.delta_load()
        self._load_balance()
        view = self.compute()
        with self.lock:
            self.view = view; self._mark = mark
            self.updated = ms.now().strftime("%d.%m %H:%M")
            self.ready = True; self.error = None

    def is_customer(self, cid):
        b = self.bal.get(cid)
        name = (b or {}).get("name") or (self.cps.get(cid) or {}).get("name", "")
        if EXCLUDE.search(name or ""): return False
        if b is not None and not b.get("demands"): return False  # hech qachon otgruzka yo'q = postavshik
        return True

    def compute(self):
        today = ms.now().date()
        by_cp = defaultdict(list)
        for d in self.demands.values():
            if d.get("deleted") or not d.get("agent"): continue
            by_cp[d["agent"]].append(d)
        for v in by_cp.values(): v.sort(key=lambda d: d["moment"], reverse=True)

        def seller_of(cid):
            for d in by_cp.get(cid, []):
                n = self.emps.get(d["owner"], "")
                if n and n.lower() != "admin": return n
            for t in (self.cps.get(cid) or {}).get("tags", []):
                if t in TAG_SELLER: return TAG_SELLER[t]
            return "Biriktirilmagan"

        customers, open_keys = [], set()
        for cid, b in self.bal.items():
            debt = -b["balance"] / 100
            if debt < 1000 or not self.is_customer(cid): continue
            c = self.cps.get(cid, {})
            left = debt; docs = []
            for d in by_cp.get(cid, []):  # qarz eng yangi otgruzkalarda turibdi (FIFO)
                if left <= 0: break
                amt = min(left, d["sum"] / 100); left -= amt
                due, exact = due_info(d.get("description"), d["moment"][:10])
                docs.append({"id": d["id"], "doc": d["name"], "ship": d["moment"][:10], "amount": round(amt),
                             "due": due, "exact": exact,
                             "note": (d.get("description") or "")[:120]})
                open_keys.add((cid, d["name"]))
            if left > 0.5:
                docs.append({"doc": "eski qarz", "ship": None, "amount": round(left), "due": None, "note": ""})
            dues = [x["due"] for x in docs if x["due"]]
            customers.append({"id": cid, "name": b["name"] or c.get("name", "?"), "phone": c.get("phone", ""),
                              "seller": seller_of(cid), "debt": round(debt), "docs": docs,
                              "due": min(dues) if dues else None})
        customers.sort(key=lambda x: -x["debt"])
        seller_by = {c["id"]: c["seller"] for c in customers}

        pay_day = defaultdict(float); pay_cp_day = defaultdict(float)
        for p in self.pays.values():
            cid = p["agent"]
            if p.get("deleted") or not cid or not self.is_customer(cid): continue
            k = p["moment"][:10]; pay_day[k] += p["sum"] / 100; pay_cp_day[(cid, k)] += p["sum"] / 100

        due_all = defaultdict(list)
        for d in self.demands.values():
            cid = d["agent"]
            if d.get("deleted") or not cid or not self.is_customer(cid): continue
            due, exact = due_info(d.get("description"), d["moment"][:10])
            if not due: continue
            name = (self.bal.get(cid) or {}).get("name") or (self.cps.get(cid) or {}).get("name", "?")
            due_all[due].append({"cid": cid, "client": name, "seller": seller_by.get(cid) or seller_of(cid),
                                 "id": d["id"], "doc": d["name"], "ship": d["moment"][:10], "amount": round(d["sum"] / 100),
                                 "open": (cid, d["name"]) in open_keys, "exact": exact,
                                 "note": (d.get("description") or "")[:120]})
        days = {}
        for k in set(pay_day) | set(due_all):
            items = due_all.get(k, [])
            for it in items: it["paid_that_day"] = round(pay_cp_day.get((it["cid"], k), 0))
            days[k] = {"income": round(pay_day.get(k, 0)), "due": sum(i["amount"] for i in items), "items": items}
        cut = (today - dt.timedelta(days=PAY_DAYS)).isoformat()
        payers = defaultdict(list)
        for (cid, k), v in pay_cp_day.items():
            if k >= cut:
                payers[k].append({"cid": cid, "client": (self.bal.get(cid) or {}).get("name") or (self.cps.get(cid) or {}).get("name", "?"),
                                  "seller": seller_by.get(cid) or seller_of(cid), "amount": round(v)})
        return {"today": today.isoformat(), "customers": customers, "days": days, "payers": payers}


STORE = Store()
