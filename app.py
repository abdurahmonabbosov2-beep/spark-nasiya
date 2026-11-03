import os, hmac
from flask import Flask, jsonify, send_from_directory, request, Response
import data

app = Flask(__name__)
PASSWORD = os.environ.get("APP_PASSWORD", "")


@app.before_request
def auth():
    if not PASSWORD or request.path == "/health":
        return
    a = request.authorization
    if not a or not hmac.compare_digest(a.password or "", PASSWORD):
        return Response("Parol kerak", 401, {"WWW-Authenticate": 'Basic realm="Spark Plus Nasiya"'})


@app.route("/")
def index():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "index.html")


@app.route("/api/data")
def api():
    s = data.STORE
    with s.lock:
        if not s.ready:
            return jsonify({"loading": True, "error": s.error})
        return jsonify({"loading": False, "updated": s.updated, "error": s.error, **s.view})


@app.route("/health")
def health():
    return "ok"


data.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
