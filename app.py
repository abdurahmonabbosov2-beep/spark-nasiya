"""Spark Plus · Boshqaruv: kunlik nazorat + nasiya (bitta sayt)."""
import os, sys, time, json, hashlib, threading, traceback, hmac
import requests
from flask import Flask, jsonify, request, session, redirect, send_file, Response
import ms, nasiya, daily

HERE = os.path.dirname(os.path.abspath(__file__))
USER = os.environ.get("DASHBOARD_USER", "")
PASSWORD = os.environ.get("APP_PASSWORD", "")
CRON_SECRET = os.environ.get("CRON_SECRET", "")
REPORT_HOUR = int(os.environ.get("REPORT_HOUR", "8"))
REFRESH_SEC = int(os.environ.get("REFRESH_SECONDS", "300"))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or hashlib.sha256(("spark|" + PASSWORD + "|" + ms.token()).encode()).hexdigest()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")), PERMANENT_SESSION_LIFETIME=30 * 86400)

DAILY = {"report": None, "error": None, "running": False, "last_day": None}
_dlock = threading.Lock()


def log(m): print(ms.now().strftime("%H:%M:%S"), m, flush=True)


# ---------------- kirish ----------------
OPEN = {"/login", "/health", "/cron/run"}


@app.before_request
def guard():
    start()  # fon ishi shu jarayonda ishlayotganiga ishonch (gunicorn --preload / fork holati)
    if request.path in OPEN or not PASSWORD: return
    if session.get("ok"): return
    if request.path.startswith("/api/"): return jsonify({"error": "login"}), 401
    return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def login():
    err = ""
    if request.method == "POST":
        u = request.form.get("u", "").strip(); p = request.form.get("p", "")
        if hmac.compare_digest(p, PASSWORD) and (not USER or u.lower() == USER.lower()):
            session.permanent = True; session["ok"] = True
            return redirect("/")
        time.sleep(1); err = "Login yoki parol noto'g'ri"
    html = open(os.path.join(HERE, "login.html"), encoding="utf-8").read()
    return html.replace("{{ERR}}", err)


@app.get("/logout")
def logout():
    session.clear(); return redirect("/login")


# ---------------- sahifalar ----------------
@app.get("/")
def index(): return send_file(os.path.join(HERE, "index.html"))


@app.get("/health")
def health(): return "ok"


@app.get("/api/nasiya")
def api_nasiya():
    S = nasiya.STORE
    with S.lock:
        return jsonify({"ready": S.ready, "error": S.error, "updated": S.updated, **(S.view or {})})


@app.post("/api/nasiya/refresh")
def api_nasiya_refresh():
    safe_nasiya(); return jsonify({"ok": not nasiya.STORE.error, "error": nasiya.STORE.error})


@app.get("/api/daily")
def api_daily():
    return jsonify({"running": DAILY["running"], "error": DAILY["error"], "report": DAILY["report"]})


@app.post("/api/daily/run")
def api_daily_run():
    day = (request.json or {}).get("day") if request.is_json else None
    threading.Thread(target=run_daily, kwargs={"day": day, "notify": False}, daemon=True).start()
    return jsonify({"ok": True})


@app.get("/cron/run")
def cron_run():
    if not CRON_SECRET or not hmac.compare_digest(request.args.get("key", ""), CRON_SECRET):
        return Response("ruxsat yo'q", 403)
    threading.Thread(target=run_daily, kwargs={"notify": True}, daemon=True).start()
    return "boshlandi"


# ---------------- fon ishlari ----------------
def safe_nasiya():
    try:
        nasiya.STORE.refresh()
    except Exception as e:
        nasiya.STORE.error = str(e)[:300]; log("NASIYA XATO: " + traceback.format_exc()[-800:])


def run_daily(day=None, notify=False):
    if not _dlock.acquire(blocking=False): return
    DAILY["running"] = True
    try:
        S = nasiya.STORE
        r = daily.build(day, S.view if S.ready else None)
        DAILY["report"] = r; DAILY["error"] = None
        log(f"kunlik hisobot tayyor: {r['day']}")
        if notify: send_telegram(r)
    except Exception as e:
        DAILY["error"] = str(e)[:300]; log("KUNLIK XATO: " + traceback.format_exc()[-800:])
    finally:
        DAILY["running"] = False; _dlock.release()


def send_telegram(r):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat: return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": daily.telegram_text(r, os.environ.get("PUBLIC_URL", ""))}, timeout=20)
    except Exception as e:
        log("Telegram xato: " + str(e))


def loop():
    while True:
        safe_nasiya()
        n = ms.now(); today = n.date().isoformat()
        due = n.hour >= REPORT_HOUR and DAILY["last_day"] != today
        if DAILY["report"] is None or due:
            # Telegram faqat soat 08:xx dagi rejali ishga tushishda (server qayta yonganda takrorlanmaydi)
            run_daily(notify=due and n.hour == REPORT_HOUR)
            if due: DAILY["last_day"] = today
        time.sleep(REFRESH_SEC if nasiya.STORE.ready else 30)


_started = None
_slock = threading.Lock()


def start():
    global _started
    if _started == os.getpid(): return
    with _slock:
        if _started == os.getpid(): return
        _started = os.getpid()
    if not ms.token():
        nasiya.STORE.error = DAILY["error"] = "MOYSKLAD_TOKEN o'rnatilmagan (Render → Environment)"; return
    threading.Thread(target=loop, daemon=True).start()
    log(f"fon ishi boshlandi (pid {os.getpid()})")


# gunicorn ostida fon ishi worker ichida gunicorn.conf.py (post_fork) orqali boshlanadi;
# oddiy "python app.py" da shu yerda boshlanadi.
if "gunicorn" not in sys.modules:
    start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
