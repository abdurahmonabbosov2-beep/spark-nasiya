"""Kunlik nazorat: kechagi kun bo'yicha to'liq hisobot."""
import datetime as dt
from collections import defaultdict
import ms
from concurrent.futures import ThreadPoolExecutor

ACTS = [("demand", "Otgruzka"), ("salesreturn", "Qaytarish"), ("cashin", "Kassa kirim"),
        ("cashout", "Kassa chiqim"), ("paymentin", "Bank kirim"), ("paymentout", "Bank chiqim"),
        ("supply", "Priyomka"), ("loss", "Spisaniya"), ("enter", "Oprixodovaniya")]


def _emp_name(row):
    return (row.get("employee") or {}).get("name") or "—"


def _byemp(day):
    return ms.all_rows("report/profit/byemployee", ms.day_range(day))


def _variants(d1, d2):
    def slim(r):
        a = r.get("assortment") or {}
        return {"id": ms.id_of(a), "t": (a.get("meta") or {}).get("type", "product"), "name": a.get("name", ""), "code": a.get("code", ""),
                "q": r.get("sellQuantity", 0), "sum": r.get("sellSum", 0), "cost": r.get("sellCostSum", 0),
                "rq": r.get("returnQuantity", 0), "profit": r.get("profit", 0)}
    return {v["id"]: v for v in ms.all_rows("report/profit/byvariant", ms.day_range(d1, d2), slim)}


def _docs(kind, day):
    def slim(d):
        ag = d.get("agent") or {}
        return {"id": d.get("id"), "owner": ms.id_of(d.get("owner")), "sum": d.get("sum", 0), "payed": d.get("payedSum", 0),
                "name": d.get("name"), "agent": ag.get("name", ""), "aid": ms.id_of(ag) if ag.get("meta") else None,
                "desc": (d.get("description") or "")[:150], "moment": d.get("moment", "")}
    f = f"moment>={day} 00:00:00;moment<={day} 23:59:59"
    return ms.all_rows("entity/" + kind, {"filter": f, "expand": "agent"}, slim, page=100)


def _emp_row(r):
    sell = ms.som(r.get("sellSum")); pr = ms.som(r.get("profit"))
    e = r.get("employee") or {}
    return {"name": _emp_name(r), "id": ms.id_of(e) if e.get("meta") else None,
            "sales": round(sell), "profit": round(pr), "margin": round(pr / sell * 100, 1) if sell else 0,
            "count": int(r.get("salesCount", 0)), "returns": round(ms.som(r.get("returnSum"))),
            "rcount": int(r.get("returnCount", 0))}


def build(day=None, nasiya_view=None):
    today = ms.now().date()
    y = dt.date.fromisoformat(day) if day else today - dt.timedelta(days=1)
    Y = y.isoformat()
    emps = {e["id"]: e["name"] for e in ms.all_rows("entity/employee", {})}

    # 1) 30 kunlik grafik va 7 kun bilan solishtirish (har kun alohida foyda hisoboti)
    series = []
    per_emp_y = []
    ndays = max(30, y.day)
    days = [(y - dt.timedelta(days=i)).isoformat() for i in range(ndays - 1, -1, -1)]
    M0 = y.replace(day=1).isoformat()
    emp_days = defaultdict(dict)  # xodim -> {kun: savdo} (shu oy)
    with ThreadPoolExecutor(3) as ex:
        all_rows = list(ex.map(_byemp, days))
    for d, rows in zip(days, all_rows):
        s = {"day": d, "sales": 0, "cost": 0, "returns": 0, "rcost": 0, "profit": 0, "count": 0}
        for r in rows:
            s["sales"] += ms.som(r.get("sellSum")); s["cost"] += ms.som(r.get("sellCostSum"))
            s["returns"] += ms.som(r.get("returnSum")); s["rcost"] += ms.som(r.get("returnCostSum"))
            s["profit"] += ms.som(r.get("profit")); s["count"] += r.get("salesCount", 0)
        s["emps"] = sorted(({"name": _emp_name(r), "sales": round(ms.som(r.get("sellSum"))),
                             "profit": round(ms.som(r.get("profit")))} for r in rows), key=lambda x: -x["sales"])
        series.append(s)
        if d >= M0:
            for r in rows: emp_days[_emp_name(r)][d] = round(ms.som(r.get("sellSum")))
        if d == Y:
            per_emp_y = [_emp_row(r) for r in rows]
    per_emp_y.sort(key=lambda x: -x["sales"])
    cur = series[-1]; prev7 = series[-8:-1]

    # 1b) Oy boshidan kechagacha xodimlar reytingi
    month = sorted((_emp_row(r) for r in ms.all_rows("report/profit/byemployee", ms.day_range(M0, Y))),
                   key=lambda x: -x["sales"])
    for m in month: m["days"] = emp_days.get(m["name"], {})
    m_sales = sum(m["sales"] for m in month); m_profit = sum(m["profit"] for m in month)
    month_info = {"from": M0, "to": Y, "sales": m_sales, "profit": m_profit,
                  "margin": round(m_profit / m_sales * 100, 1) if m_sales else 0,
                  "returns": sum(m["returns"] for m in month), "count": sum(m["count"] for m in month),
                  "employees": month}

    def avg(k): return sum(s[k] for s in prev7) / 7 if prev7 else 0

    def margin(s, p): return round(p / s * 100, 1) if s else 0
    kpi = {
        "sales": round(cur["sales"]), "sales7": round(avg("sales")),
        "profit": round(cur["profit"]), "profit7": round(avg("profit")),
        "margin": margin(cur["sales"], cur["profit"]),
        "margin7": margin(sum(s["sales"] for s in prev7), sum(s["profit"] for s in prev7)),
        "returns": round(cur["returns"]), "returns7": round(avg("returns")),
        "count": int(cur["count"]),
    }

    # 2) Kim nima qildi (hujjatlar soni)
    acts = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    docs_y = {}
    for kind, _ in ACTS:
        rows = _docs(kind, Y); docs_y[kind] = rows
        for d in rows:
            n = emps.get(d["owner"], "—")
            acts[n][kind][0] += 1; acts[n][kind][1] += ms.som(d["sum"])
    activity = [{"name": n, **{k: v[0] for k, v in kinds.items()},
                 **{k + "_sum": round(v[1]) for k, v in kinds.items()}} for n, kinds in acts.items()]
    activity.sort(key=lambda x: -sum(x.get(k, 0) for k, _ in ACTS))

    # Qaytarishlar: Admin seshanba/shanba = tovar topilmagan
    weekday = y.weekday()  # 1=seshanba, 5=shanba
    ret_admin = [d for d in docs_y["salesreturn"] if emps.get(d["owner"], "").lower() == "admin"]
    returns_info = {"count": len(docs_y["salesreturn"]), "sum": round(sum(ms.som(d["sum"]) for d in docs_y["salesreturn"])),
                    "admin_count": len(ret_admin), "admin_sum": round(sum(ms.som(d["sum"]) for d in ret_admin)),
                    "admin_notfound": weekday in (1, 5)}

    # 3) Ziyonga sotilgan tovarlar (kecha, sotish narxi < tannarx)
    vy = _variants(Y, Y)
    loss_sales = []
    for v in vy.values():
        if v["q"] > 0 and v["sum"] < v["cost"] - 1:
            loss_sales.append({"id": v["id"], "t": v["t"], "name": v["name"], "code": v["code"], "q": v["q"],
                               "price": round(ms.som(v["sum"]) / v["q"]), "costp": round(ms.som(v["cost"]) / v["q"]),
                               "loss": round(ms.som(v["cost"] - v["sum"]))})
    loss_sales.sort(key=lambda x: -x["loss"])

    # 4) Ombor tahlili
    stock = ms.all_rows("report/stock/all", {}, lambda r: {
        "id": ms.id_of(r), "t": (r.get("meta") or {}).get("type", "product"), "name": r.get("name", ""), "code": r.get("code", ""),
        "stock": r.get("stock", 0) or 0, "price": r.get("price", 0) or 0, "sale": r.get("salePrice", 0) or 0})
    st = {s["id"]: s for s in stock}
    rngs = [((y - dt.timedelta(days=13)).isoformat(), Y), ((y - dt.timedelta(days=29)).isoformat(), Y),
            ((y - dt.timedelta(days=59)).isoformat(), Y),
            ((y - dt.timedelta(days=89)).isoformat(), (y - dt.timedelta(days=14)).isoformat())]
    with ThreadPoolExecutor(2) as ex:
        v14, v30, v60, vold = ex.map(lambda r: _variants(*r), rngs)

    stopped = []  # oldin sotilardi, 14 kunda bitta ham ketmadi
    for pid, v in vold.items():
        if v["q"] >= 3 and (v14.get(pid) or {}).get("q", 0) <= 0:
            s = st.get(pid) or {}
            stopped.append({"id": pid, "t": v["t"], "name": v["name"], "code": v["code"], "q76": v["q"],
                            "per_month": round(v["q"] / 76 * 30, 1), "stock": s.get("stock", 0)})
    stopped.sort(key=lambda x: -x["q76"])

    reorder = []  # tugagan, lekin talab bor
    for pid, v in v30.items():
        s = st.get(pid)
        if s is not None and s["stock"] <= 0 and v["q"] > 0:
            reorder.append({"id": pid, "t": v["t"], "name": v["name"], "code": v["code"], "q30": v["q"], "stock": s["stock"],
                            "lost_profit": round(ms.som(v["profit"]))})
    reorder.sort(key=lambda x: -x["q30"])

    dead, dead_value = [], 0  # 60 kunda umuman sotilmagan
    for s in stock:
        if s["stock"] > 0 and (v60.get(s["id"]) or {}).get("q", 0) <= 0:
            val = s["stock"] * ms.som(s["price"]); dead_value += val
            dead.append({"id": s["id"], "t": s["t"], "name": s["name"], "code": s["code"], "stock": s["stock"], "value": round(val)})
    dead.sort(key=lambda x: -x["value"])

    # 5) Qarzga sotuvlar (kecha), qarzdorlar, spisaniya
    credit = []
    for d in docs_y["demand"]:
        unpaid = ms.som(d["sum"] - d["payed"])
        if unpaid >= 1000:
            credit.append({"id": d["id"], "doc": d["name"], "client": d["agent"], "aid": d["aid"], "seller": emps.get(d["owner"], "—"), "sum": round(ms.som(d["sum"])),
                           "unpaid": round(unpaid), "note": d["desc"]})
    credit.sort(key=lambda x: -x["unpaid"])
    losses = [{"id": d["id"], "doc": d["name"], "who": emps.get(d["owner"], "—"), "sum": round(ms.som(d["sum"])), "note": d["desc"]}
              for d in docs_y["loss"]]
    debtors, debt_total, overdue_total = [], 0, 0
    if nasiya_view:
        for c in nasiya_view["customers"]:
            debt_total += c["debt"]
            od = sum(x["amount"] for x in c["docs"] if x["due"] and x["due"] < today.isoformat())
            overdue_total += od
        debtors = [{"id": c["id"], "name": c["name"], "seller": c["seller"], "debt": c["debt"], "due": c["due"]}
                   for c in nasiya_view["customers"][:15]]

    docs_out = {k: sorted(({"id": d["id"], "doc": d["name"], "who": emps.get(d["owner"], "—"), "client": d["agent"],
                            "aid": d["aid"], "sum": round(ms.som(d["sum"])), "payed": round(ms.som(d["payed"])),
                            "time": d["moment"][11:16], "note": d["desc"]} for d in rows), key=lambda x: -x["sum"])
                for k, rows in docs_y.items()}
    return {
        "month": month_info, "docs": docs_out,
        "day": Y, "built": ms.now().strftime("%d.%m.%Y %H:%M"), "kpi": kpi,
        "series": [{"day": s["day"], "sales": round(s["sales"]), "profit": round(s["profit"]),
                    "returns": round(s["returns"]), "emps": s["emps"]} for s in series[-30:]],
        "employees": per_emp_y, "activity": activity, "acts": ACTS, "returns": returns_info,
        "loss_sales": loss_sales[:100], "loss_total": round(sum(x["loss"] for x in loss_sales)),
        "stopped": stopped[:150], "stopped_n": len(stopped),
        "reorder": reorder[:150], "reorder_n": len(reorder),
        "dead": dead[:150], "dead_n": len(dead), "dead_value": round(dead_value),
        "credit": credit, "credit_total": round(sum(x["unpaid"] for x in credit)),
        "losses": losses, "losses_total": round(sum(x["sum"] for x in losses)),
        "debtors": debtors, "debt_total": round(debt_total), "overdue_total": round(overdue_total),
    }


def telegram_text(r, url=""):
    k = r["kpi"]
    def f(x): return f"{x:,.0f}".replace(",", " ")
    def ch(a, b): return f" ({'+' if a >= b else ''}{(a - b) / b * 100:.0f}% 7 kunga nisbatan)" if b else ""
    lines = [f"📊 Spark Plus · {r['day']} kunlik nazorat",
             f"Savdo: {f(k['sales'])} so'm{ch(k['sales'], k['sales7'])}",
             f"Foyda: {f(k['profit'])} so'm · marja {k['margin']}%",
             f"Qaytarish: {f(k['returns'])} so'm",
             f"Ziyonga sotuv: {len(r['loss_sales'])} ta tovar, {f(r['loss_total'])} so'm",
             f"Tugagan (talab bor): {r['reorder_n']} ta · To'xtagan: {r['stopped_n']} ta",
             f"O'lik zaxira: {r['dead_n']} ta, {f(r['dead_value'])} so'm",
             f"Kechagi qarzga sotuv: {f(r['credit_total'])} so'm · Umumiy qarz: {f(r['debt_total'])} so'm"]
    if url: lines.append(url)
    return "\n".join(lines)
