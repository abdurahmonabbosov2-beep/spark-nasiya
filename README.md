# Spark Plus · Boshqaruv (kunlik nazorat + nasiya)

Bitta sayt, bitta login:
- **Kunlik nazorat** — har kuni 08:00 (Toshkent) kechagi kunni tekshiradi: savdo, foyda, marja, qaytarishlar (7 kun bilan solishtirib),
  xodimlar, kim nima qildi, ziyonga sotuv, to'xtagan / tugagan / o'lik zaxira, qarzga sotuvlar, qarzdorlar, spisaniya.
- **Nasiya** — har 5 daqiqada yangilanadi: sotuvchilar bo'yicha qarzlar, kalendar, bugungi (18:25) hisobot.

## Render → Environment
- `MOYSKLAD_TOKEN` — MoySklad tokeni (majburiy)
- `APP_PASSWORD` — kirish paroli (majburiy)
- `DASHBOARD_USER` — login (ixtiyoriy; bo'sh bo'lsa faqat parol tekshiriladi)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `PUBLIC_URL` — 08:00 hisobotini Telegram'ga yuborish (ixtiyoriy)
- `EXCLUDE_WORDS` — nasiyadan chiqariladigan nomlar (standart: oylik|xitoy|zavod|postav|ta'minot|Dilshodjon 1008|Xakimjon 0998)

Token va parollar kodda yo'q — faqat Render sozlamalarida.

## Muddat yozish
Otgruzka izohiga: `25.11` (aniq). `7 ga` ham tushuniladi, lekin "taxminiy" deb belgilanadi.
