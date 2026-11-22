"""MoySklad API bilan ishlash (umumiy)."""
import os, time, datetime as dt
import requests
from concurrent.futures import ThreadPoolExecutor

B = "https://api.moysklad.ru/api/remap/1.2/"
TZ = dt.timezone(dt.timedelta(hours=5))  # Toshkent
_S = requests.Session()
_S.headers.update({"Accept-Encoding": "gzip"})


def token():
    return os.environ.get("MOYSKLAD_TOKEN", "")


def get(path, params=None):
    for i in range(6):
        try:
            r = _S.get(B + path, params=params, timeout=120,
                       headers={"Authorization": "Bearer " + token()})
            if r.status_code == 429:
                time.sleep(2 + i * 2); continue
            if r.status_code >= 500:
                time.sleep(3); continue
            if not r.ok:
                raise RuntimeError(f"MoySklad {r.status_code}: {r.text[:200]}")
            return r.json()
        except requests.RequestException:
            if i == 5: raise
            time.sleep(3)
    raise RuntimeError("MoySklad javob bermadi")


def all_rows(path, params=None, slim=None, page=1000):
    """Hamma sahifalarni yuklaydi; slim berilsa har qatordan faqat keraklisi qoladi (xotira tejaladi)."""
    params = dict(params or {})
    conv = (lambda rows: [slim(r) for r in rows]) if slim else (lambda rows: rows)
    first = get(path, dict(params, limit=page, offset=0))
    out = conv(first.get("rows") or [])
    size = (first.get("meta") or {}).get("size", len(out))
    offs = list(range(page, size, page))
    if offs:
        with ThreadPoolExecutor(2) as ex:
            for rows in ex.map(lambda o: conv(get(path, dict(params, limit=page, offset=o)).get("rows") or []), offs):
                out += rows
    return out


def id_of(obj):
    if not obj: return None
    return obj["meta"]["href"].rsplit("/", 1)[-1].split("?")[0]


def now():
    return dt.datetime.now(TZ)


def day_range(d1, d2=None):
    d2 = d2 or d1
    return {"momentFrom": f"{d1} 00:00:00", "momentTo": f"{d2} 23:59:59"}


def som(kop):
    return (kop or 0) / 100
