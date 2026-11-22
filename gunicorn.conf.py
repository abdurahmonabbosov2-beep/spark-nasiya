# Gunicorn bu faylni o'zi o'qiydi. Fon ishi (MoySklad yangilash) har bir worker ichida boshlansin.
def post_fork(server, worker):
    import app
    app.start()
