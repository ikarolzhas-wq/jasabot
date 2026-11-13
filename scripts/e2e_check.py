"""Толық жүйе тесті (end-to-end): нақты сервер + нақты anthropic SDK + нақты WhatsAppClient.
Meta Graph API мен Anthropic API жергілікті жалған сервермен ауыстырылған, сондықтан
интернет те, API кілттері де керек емес. Embedding орнына офлайн әріп n-грамдары.

  pip install scikit-learn
  python -m scripts.e2e_check                    # Meta режимі
  python -m scripts.e2e_check --provider ycloud  # YCloud режимі

Кодты өзгерткен сайын екеуін де іске қос: барлық тексеру өтпесе, бір нәрсе бұзылған.
"""
import hashlib, hmac, json, os, re, sys, threading, time
from pathlib import Path

import shutil, tempfile
ROOT = Path(__file__).resolve().parent.parent
DATA = Path(tempfile.mkdtemp(prefix="e2e_"))
YC = "--provider" in sys.argv and sys.argv[sys.argv.index("--provider") + 1] == "ycloud"
YC_NUMBERS = {"aru-salon": "+77005550001", "tulpar-auto": "+77005550002", "zhas-bot": "+77005550003"}
if YC:
    # Бизнестердің көшірмесіне YCloud нөмірлерін жазамыз (нағыз config файлдары өзгермейді)
    shutil.copytree(ROOT / "businesses", DATA / "businesses")
    for bid, num in YC_NUMBERS.items():
        cfg = DATA / "businesses" / bid / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8") + f'\nwhatsapp_number: "{num}"\n', encoding="utf-8")
    os.environ.update({
        "PROVIDER": "ycloud", "YCLOUD_API_KEY": "yc-test", "YCLOUD_WEBHOOK_SECRET": "whsec-test",
        "YCLOUD_API_BASE": "http://127.0.0.1:9001/v2", "BUSINESSES_DIR": str(DATA / "businesses"),
    })
else:
    os.environ["PROVIDER"] = "meta"
os.environ.update({
    "WHATSAPP_VERIFY_TOKEN": "verify-me", "WHATSAPP_APP_SECRET": "app-secret",
    "WHATSAPP_ACCESS_TOKEN": "EAAG-test-token", "GRAPH_API_BASE": "http://127.0.0.1:9001",
    "ANTHROPIC_API_KEY": "sk-test", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9001",
    "MAX_DISTANCE": "0.87", "DATA_DIR": str(DATA), "DEFAULT_BUSINESS_ID": "",
    "TELEGRAM_BOT_TOKEN": "tg-test", "TELEGRAM_CHAT_ID": "owner-chat", "TELEGRAM_API_BASE": "http://127.0.0.1:9001",
})
sys.path.insert(0, str(ROOT))

import httpx, uvicorn
httpx_timeout = 30.0
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import app.rag as rag
from sklearn.feature_extraction.text import HashingVectorizer
_vec = HashingVectorizer(analyzer="char_wb", ngram_range=(3, 5), n_features=2**16, norm="l2", alternate_sign=False)
def embed(texts):
    return _vec.transform([t.removeprefix("passage: ").removeprefix("query: ").lower() for t in texts]).toarray().tolist()
rag.default_embedder = lambda: embed

# ---------- Жалған Meta + Anthropic сервері ----------
mock = FastAPI(); graph_calls, llm_calls, tg_calls = [], [], []

@mock.post("/bottg-test/sendMessage")
async def telegram(request: Request):
    tg_calls.append(await request.json())
    return {"ok": True}

@mock.post("/v23.0/{pnid}/messages")
async def graph(pnid: str, request: Request):
    graph_calls.append({"pnid": pnid, "auth": request.headers.get("authorization"), "json": await request.json()})
    return {"messaging_product": "whatsapp", "messages": [{"id": "wamid.out"}]}

@mock.post("/v2/whatsapp/messages/sendDirectly")
async def ycloud_send(request: Request):
    j = await request.json()
    n = len(graph_calls) + 1
    # Meta форматына келтіріп сақтаймыз, сонда төмендегі тексерулер екі режимге де ортақ
    graph_calls.append({"pnid": j["from"], "auth": "Bearer " + request.headers.get("x-api-key", ""),
                        "json": {"messaging_product": "whatsapp", "to": j["to"], "type": j["type"], "text": j.get("text"),
                                 "interactive": j.get("interactive")}})
    return {"id": f"y{n}", "wamid": f"wamid.y{n}", "status": "accepted"}

@mock.post("/v1/messages")
async def messages(request: Request):
    body = await request.json(); llm_calls.append(body)
    q = body["messages"][-1]["content"]
    if "ҚАТЕ" in q:
        return JSONResponse({"type": "error", "error": {"type": "api_error", "message": "boom"}}, status_code=500)
    if "астана" in q.lower() or "доллар" in q.lower():
        text = "Кешіріңіз, бұл туралы ақпарат бере алмаймын 🙏 Бұл чат тек салон бойынша көмектеседі. [[OFFTOPIC]]"
        return {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
                "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 100, "output_tokens": 20}}
    if "жазыл" in q.lower() or "15:00" in q:
        text = "Жақсы! Өтініміңізді әкімшіге жібердім, ол уақытты растайды. [[LEAD]]"
        return {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
                "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 100, "output_tokens": 20}}
    ctx = body["system"].split("<ақпарат>")[-1]
    # Қарапайым имитация: сұрақтағы сөз бар сөйлемді ақпараттан алып қайтарады, болмаса [[HANDOFF]]
    words = [w for w in re.findall(r"\w{4,}", q.lower())]
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", ctx) if s.strip()]
    hit = next((s for w in words for s in sents if w[:5] in s.lower()), None)
    text = hit if hit else "[[HANDOFF]]"
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 20}}

def serve(app, port):
    s = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=s.run, daemon=True).start()
    for _ in range(200):
        try: httpx.get(f"http://127.0.0.1:{port}/docs", timeout=1); return
        except Exception: time.sleep(0.3)

# ---------- Ботты іске қосу ----------
from scripts import build_index
build_index.main([])
from app.main import app as bot_app
from app.businesses import Registry
reg = Registry.from_dir(Path(os.environ.get("BUSINESSES_DIR", ROOT / "businesses")))
salon, auto = reg.get("aru-salon"), reg.get("tulpar-auto")
def biz(b):   # бизнесті анықтайтын белгі: Meta — phone_number_id, YCloud — нөмір
    return b.whatsapp_number if YC else b.phone_number_id
TOKEN = "yc-test" if YC else "EAAG-test-token"
print("РЕЖИМ:", "YCloud" if YC else "Meta")
serve(mock, 9001); serve(bot_app, 9000)
BOT = "http://127.0.0.1:9000"

def payload(text, mid, pnid, mtype="text", sender="77011112233"):
    click = {"type": "list_reply", "list_reply": {"id": "opt0", "title": text}}   # тізімнен таңдау
    if YC:
        m = {"id": "y" + mid, "wamid": mid, "wabaId": "W", "from": sender, "customerProfile": {"name": "Айгүл"},
             "to": pnid, "type": mtype}
        if mtype == "text": m["text"] = {"body": text}
        if mtype == "interactive": m["interactive"] = click
        return {"id": "evt_" + mid, "type": "whatsapp.inbound_message.received", "apiVersion": "v2",
                "whatsappInboundMessage": m}
    m = {"from": sender, "id": mid, "timestamp": "1", "type": mtype}
    if mtype == "text": m["text"] = {"body": text}
    if mtype == "interactive": m["interactive"] = click
    return {"object": "whatsapp_business_account", "entry": [{"id": "W", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "1", "phone_number_id": pnid},
        "contacts": [{"profile": {"name": "Айгүл"}, "wa_id": sender}], "messages": [m]}}]}]}

def echo_payload(text, mid, pnid, customer):
    """Бизнес иесі телефоннан клиентке жазған хабар (Coexistence echo)."""
    m = {"from": pnid, "to": customer, "type": "text", "text": {"body": text}}
    if YC:
        return {"id": "evt_" + mid, "type": "whatsapp.smb.message.echoes", "whatsappMessage": {**m, "wamid": mid}}
    return {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": pnid},
                                              "message_echoes": [{**m, "id": mid}]}}]}]}

def post_raw(data, secret=None):
    body = json.dumps(data, ensure_ascii=False).encode()
    if YC:
        ts = str(int(time.time()))
        sig = hmac.new((secret or "whsec-test").encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json", "YCloud-Signature": f"t={ts},s={sig}"}
    else:
        sig = "sha256=" + hmac.new((secret or "app-secret").encode(), body, hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json", "X-Hub-Signature-256": sig}
    r = httpx.post(f"{BOT}/webhook", timeout=httpx_timeout, content=body, headers=headers)
    time.sleep(1.2)  # фондық өңдеуді күту
    return r.status_code

def send(text, mid, pnid=None, secret=None, mtype="text", sender="77011112233"):
    return post_raw(payload(text, mid, pnid or biz(salon), mtype, sender), secret)

def n_replies():   # клиентке кеткен жауаптар саны ("оқылды" белгісін санамаймыз)
    return len([c for c in graph_calls if c["json"].get("type") in ("text", "interactive")])

def last_reply():
    texts = [c for c in graph_calls if c["json"].get("type") == "text"]
    return texts[-1] if texts else None

results = []
def check(name, cond, detail=""):
    results.append(cond); print(("✅" if cond else "❌"), name, ("— " + detail if detail else ""))

# 1. Webhook тексеру
r = httpx.get(timeout=httpx_timeout, url=f"{BOT}/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "1158201444"})
check("Meta webhook тексеруі (GET)", r.status_code == 200 and r.text == "1158201444")
r = httpx.get(timeout=httpx_timeout, url=f"{BOT}/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "x"})
check("Қате verify token қабылданбайды", r.status_code == 403)
check("/health", {"aru-salon", "tulpar-auto"} <= set(httpx.get(timeout=httpx_timeout, url=f"{BOT}/health").json()["businesses"]))

# 2. Жалған қолтаңба
n = len(graph_calls)
check("Жалған қолтаңба → 401, жауап жіберілмейді", send("Маникюр?", "m0", secret="hacker") == 401 and len(graph_calls) == n)

# 3. Сәлемдесу
n_llm = len(llm_calls)
send("Сәлеметсіз бе!", "m1")
rep = last_reply()
check("Сәлемдесу: AI шақырылмайды", len(llm_calls) == n_llm and rep["json"]["text"]["body"] == salon.greeting)
check("Жіберу сұранысы дұрыс (кілт, бизнес нөмірі, клиент)",
      rep["auth"] == f"Bearer {TOKEN}" and rep["pnid"] == biz(salon) and rep["json"]["to"] == "77011112233"
      and rep["json"]["messaging_product"] == "whatsapp")
if not YC:
    check("Оқылды белгісі жіберілді", any(c["json"].get("status") == "read" and c["json"]["message_id"] == "m1" for c in graph_calls))

# 4. Нақты сұрақ
send("Маникюр қанша тұрады?", "m2")
call = llm_calls[-1]; rep = last_reply()["json"]["text"]["body"]
check("Claude-қа сұраныс кетті (SDK арқылы, temperature жоқ)", call["model"] == "claude-haiku-5-5" and "temperature" not in call)
check("Промптта тек табылған ақпарат бар", "маникюр" in call["system"].lower() and "<ақпарат>" in call["system"])
check("Клиентке жауап барды", "4000" in rep or "7000" in rep, rep[:80])

# 5. Әңгіме жалғасы
send("Ал педикюр ше?", "m3")
roles = [m["role"] for m in llm_calls[-1]["messages"]]
check("Әңгіме тарихы жіберіледі (user-дан басталып, кезектеседі)", len(roles) >= 3 and roles[0] == "user" and roles[-1] == "user" and all(a != b for a, b in zip(roles, roles[1:])), str(roles))

# 6. Қайталанған хабарлама
n = len(graph_calls)
send("Маникюр қанша тұрады?", "m2")
check("Meta қайта жіберсе, екінші жауап жоқ", len(graph_calls) == n)

# 7. Тақырыптан тыс
n_llm = len(llm_calls)
send("Қазақстанның астанасы қай қала?", "m4", sender="77019998877")   # жаңа клиент
rep = last_reply()["json"]["text"]["body"]
check("Жаңа клиенттің тақырыптан тыс сұрағы: шек тоқтатады, AI шақырылмайды",
      "Бұл чат тек" in rep and len(llm_calls) == n_llm, rep[:60])
send("Ал доллар бағамы қандай?", "m4b")                               # әңгіме ішінде
rep = last_reply()["json"]["text"]["body"]
check("Әңгіме ішіндегі тақырыптан тыс сұрақ: Claude бас тартады, белгі көрінбейді",
      "Бұл чат тек" in rep and "[[" not in rep and len(llm_calls) == n_llm + 1, rep[:60])
check("Тақырыптан тыс сұрақта иесіне хабар жоқ", tg_calls == [])

# 8. Адам сұрау
send("Администратормен сөйлескім келеді", "m5")
check("Адамға беру", "әкімшіге" in last_reply()["json"]["text"]["body"])
check("Иесінің Telegram-ына «АДАМ КЕРЕК» хабары барды",
      len(tg_calls) == 1 and "АДАМ КЕРЕК" in tg_calls[-1]["text"] and tg_calls[-1]["chat_id"] == "owner-chat")

# 8б. Өтінім: клиент жазылғысы келеді, сосын уақытты жазады
send("Маникюрге жазылғым келеді", "m5a")
rep = last_reply()["json"]["text"]["body"]
check("Өтінім: клиентке жауап, белгі көрінбейді", "әкімшіге жібердім" in rep and "[[" not in rep, rep[:60])
t = tg_calls[-1]["text"] if tg_calls else ""
check("Иесіне «ЖАҢА ӨТІНІМ»: нөмір, wa.me сілтеме, әңгіме",
      "ЖАҢА ӨТІНІМ" in t and "https://wa.me/77011112233" in t and "Маникюрге жазылғым келеді" in t and "Айгүл" in t)
n_llm = len(llm_calls)
send("ертең 15:00", "m5b")
check("Әңгіме жалғасы («ертең 15:00») жоғалмайды, Claude-қа барады",
      len(llm_calls) == n_llm + 1 and "ертең 15:00" in (tg_calls[-1]["text"] if tg_calls else ""))

# 9. Дауыстық хабарлама
send("", "m6", mtype="audio")
check("Дауыстық хабарламаға мәтін сұралады", "мәтін" in last_reply()["json"]["text"]["body"])

# 10. Екінші бизнес
send("Колодка ауыстыру қанша?", "m7", pnid=biz(auto))
rep = last_reply()
check("Екінші бизнеске өз нөмірі арқылы жауап", rep["pnid"] == biz(auto) and "колодка" in llm_calls[-1]["system"].lower(), rep["json"]["text"]["body"][:70])
check("Бизнестер араласпайды", "маникюр" not in llm_calls[-1]["system"].lower())

# 11. Claude API қатесі
send("Маникюр ҚАТЕ", "m8"); time.sleep(4)  # SDK 2 рет қайталап көреді
check("API қатесінде клиентке кешірім + нөмір", salon.contact_phone in last_reply()["json"]["text"]["body"])

# 12. Белгісіз нөмір
n = len(graph_calls)
send("Сәлем", "m9", pnid="999")
check("Белгісіз phone_number_id елемейді", len(graph_calls) == n)

# 13. Coexistence: иесі телефоннан жазса, бот үндемейді
cust = "77012223344"
send("Маникюр қанша тұрады?", "c1", sender=cust)
bot_id = "wamid.y%d" % len(graph_calls) if YC else "wamid.out"
post_raw(echo_payload("Маникюр 4000 теңге.", bot_id, biz(salon), cust))          # боттың өз хабары қайтты
n = n_replies()
send("Ал педикюр ше?", "c2", sender=cust)
check("Боттың өз хабарының echo-сы ботты тоқтатпайды", n_replies() == n + 1)
post_raw(echo_payload("Сәлеметсіз бе, әкімшімін, көмектесейін", "owner-1", biz(salon), cust))  # иесі жазды
n = n_replies()
send("Рахмет, ертең 15:00-ге келемін", "c3", sender=cust)
check("Иесі жазғаннан кейін бот сол клиентке үндемейді", n_replies() == n)
send("Маникюр қанша тұрады?", "c4", sender="77015556677")
check("Басқа клиенттерге бот жауап бере береді", n_replies() == n + 1)

# 14. Базалық бот (JasaBot, mode: menu): батырмалар, форма, өтінім — Claude-сыз
zhas = reg.get("zhas-bot")
zc = "77017778899"
n_llm, n_tg = len(llm_calls), len(tg_calls)
send("Сәлем", "z1", pnid=biz(zhas), sender=zc)
last = graph_calls[-1]["json"]
check("Базалық бот: сәлемге батырмалы мәзір (тізім) жіберілді",
      last.get("type") == "interactive" and last["interactive"]["type"] == "list"
      and last["interactive"]["action"]["sections"][0]["rows"][0]["title"] == "🤖 Бот не істейді")
send("📝 Қосылу", "z2", pnid=biz(zhas), sender=zc, mtype="interactive")   # тізімнен басты
last = graph_calls[-1]["json"]
check("Тізімнен таңдау түсінілді: форманың бірінші сұрағы", "Бизнесіңіздің түрі" in last["interactive"]["body"]["text"])
send("Клиника", "z3", pnid=biz(zhas), sender=zc, mtype="interactive")
send("ертең 14:00", "z5", pnid=biz(zhas), sender=zc)
t = tg_calls[-1]["text"] if len(tg_calls) > n_tg else ""
check("Форма аяқталды: иесіне «ЖАҢА ӨТІНІМ» (бизнес, уақыт)",
      "ЖАҢА ӨТІНІМ" in t and "Бизнес: Клиника" in t and "ертең 14:00" in t)
check("Базалық бот Claude-ты шақырмайды", len(llm_calls) == n_llm)

# 15. Деректер сақталды ма
check("SQLite дерекқоры құрылды", (DATA / "bot.sqlite3").exists())

print(f"\nНӘТИЖЕ: {sum(results)}/{len(results)} тексеру өтті")
sys.exit(0 if all(results) else 1)
