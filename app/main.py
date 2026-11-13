"""WhatsApp webhook сервері.

Іске қосу:  uvicorn app.main:app --port 8000

  GET  /webhook  — Meta webhook-ты тіркегенде бір рет тексереді (тек PROVIDER=meta)
  POST /webhook  — әр жаңа хабарлама осында келеді (Meta немесе YCloud)
  GET  /health   — сервер тірі ме, тексеру

Coexistence: бизнес иесі WhatsApp Business қосымшасынан клиентке өзі жазса,
бот сол чатта OWNER_PAUSE_HOURS сағат үндемейді — екеуі қатар жазбайды.
"""

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace

from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

from app.brain import Brain
from app.menu import MenuBot, render_text
from app.businesses import Registry
from app.config import Settings, get_settings
from app.notify import Notifier
from app.store import Store
from app.whatsapp import IncomingMessage, WhatsAppClient, parse_webhook, verify_signature
from app.ycloud import YCloudClient, YCloudRouter, parse_ycloud_webhook, verify_ycloud_any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

UNSUPPORTED_TEXT = (
    "Кешіріңіз, әзірге тек мәтіндік хабарламаларды түсінемін. Сұрағыңызды жазып жіберіңізші.\n"
    "Извините, пока я понимаю только текст. Напишите, пожалуйста, вопрос."
)


@dataclass
class Deps:
    settings: Settings
    registry: Registry
    brain: Brain
    wa: WhatsAppClient | YCloudClient | YCloudRouter
    store: Store
    notifier: Notifier
    menu: MenuBot | None = None   # базалық бот (mode: menu бизнестер үшін)


def send(deps: Deps, msg: IncomingMessage, text: str) -> None:
    """Клиентке жауап жіберіп, оның id-ін сақтайды (echo келгенде бот өз хабарын таниды)."""
    ids = deps.wa.send_text(msg.phone_number_id, msg.sender, text)
    deps.store.mark_sent(ids or [])


def send_options(deps: Deps, msg: IncomingMessage, text: str, options: list[str]) -> None:
    """Батырмалы жауап. Мәтін ұзын болса (>1000), алдымен мәтінді, сосын батырмаларды жібереді."""
    if not options:
        send(deps, msg, text)
        return
    if len(text) > 1000:
        send(deps, msg, text)
        text = "👇"
    try:
        ids = deps.wa.send_options(msg.phone_number_id, msg.sender, text, options)
    except Exception:
        # батырмалар жіберілмесе — нөмірленген мәтінмен жібереміз
        log.exception("Батырмалар жіберілмеді, мәтінмен жіберемін")
        lines = "\n".join(f"{i}. {o}" for i, o in enumerate(options, 1))
        ids = deps.wa.send_text(msg.phone_number_id, msg.sender, f"{text}\n\n{lines}")
    deps.store.mark_sent(ids or [])


def handle_menu(deps: Deps, business, msg: IncomingMessage) -> None:
    """Базалық бот: мәзір, батырмалар, формалар. Claude шақырылмайды."""
    try:
        reply = deps.menu.answer(business, msg.sender, msg.text)
    except Exception:
        log.exception("Мәзір қатесі")
        send(deps, msg, f"Кешіріңіз, қазір жауап бере алмай тұрмын. Байланыс: {business.contact_phone}")
        return
    send_options(deps, msg, reply.text, reply.options)
    log.info("[%s] %s → %s", business.id, (msg.text or "[медиа]")[:60], reply.reason)
    if reply.lead or reply.handoff:
        history = deps.store.history(business.id, msg.sender, limit=10)
        if reply.lead:
            deps.notifier.lead(business, msg.sender, msg.name, history)
        else:
            deps.notifier.handoff(business, msg.sender, msg.name, history, "human")


def _is_own_button_tap(deps: Deps, business, msg: IncomingMessage) -> bool:
    """Echo мәтіні клиентке соңғы көрсетілген батырмалардың біріне дәл сәйкес пе.
    WhatsApp Business-те боттың батырмалары иесінің телефонында да басылады — оны иесінің жазуы деп санамаймыз."""
    if not msg.text:
        return False
    state = deps.store.get_state(business.id, msg.sender) or {}
    text = " ".join(msg.text.split())
    return any(text == " ".join(str(o).split()) for o in state.get("shown") or [])


def handle_message(deps: Deps, msg: IncomingMessage) -> None:
    """Бір хабарламаны өңдеу. Webhook жауабынан кейін фонда іске қосылады."""
    if not msg.message_id or not deps.store.first_time(msg.message_id):
        return  # Meta қайта жіберген хабарлама — екінші рет жауап бермейміз

    business = deps.registry.by_phone_number_id(msg.phone_number_id)
    if business is None and deps.settings.default_business_id:
        business = deps.registry.get(deps.settings.default_business_id)
    if business is None:
        log.warning("Белгісіз phone_number_id=%s — config.yaml тексер", msg.phone_number_id)
        return
    if msg.account and msg.account != business.ycloud_account:
        # Бір клиенттің аккаунтынан басқа бизнестің атынан хабар келді — қабылдамаймыз.
        log.warning("[%s] хабар '%s' аккаунтынан келді, ал бизнес '%s' аккаунтында — өткізілді",
                    business.id, msg.account, business.ycloud_account)
        return

    if msg.kind == "echo":
        # Бизнестің нөмірінен кеткен хабар. Бот өзі жіберген болса — елемейміз.
        if not deps.store.was_sent_by_bot(msg.ids or (msg.message_id,)):
            if _is_own_button_tap(deps, business, msg):
                # Иесі бизнес телефонында боттың батырмасын басып қалды — бұл әдейі жазу емес, тоқтамаймыз.
                log.info("[%s] иесі боттың батырмасын басты ('%s') — елемеймін", business.id, msg.text)
                return
            deps.store.pause(business.id, msg.sender, deps.settings.owner_pause_hours)
            if msg.text:
                deps.store.add(business.id, msg.sender, "assistant", msg.text)
            log.info("[%s] иесі +%s клиентке өзі жазды — бот %s сағат үндемейді",
                     business.id, msg.sender, deps.settings.owner_pause_hours)
        return

    if deps.store.is_paused(business.id, msg.sender):
        # Иесі осы клиентпен өзі сөйлесіп жатыр — бот араласпайды, тек тарихқа жазады.
        if msg.text:
            deps.store.add(business.id, msg.sender, "user", msg.text)
        log.info("[%s] +%s: иесі сөйлесіп жатыр, бот үндемейді", business.id, msg.sender)
        return

    deps.wa.mark_read(msg.phone_number_id, msg.message_id)

    if business.mode == "menu":
        handle_menu(deps, business, msg)
        return

    if not msg.text:
        send(deps, msg, UNSUPPORTED_TEXT)
        return

    try:
        reply = deps.brain.answer(business, msg.sender, msg.text)
    except Exception:
        log.exception("Жауап құрастыру қатесі")
        send(deps, msg, f"Кешіріңіз, қазір жауап бере алмай тұрмын. Байланыс: {business.contact_phone}")
        return

    send(deps, msg, reply.text)
    log.info("[%s] %s → %s (бөліктер %s)", business.id, msg.text[:60], reply.reason, reply.parts)

    if reply.lead or reply.handoff:
        history = deps.store.history(business.id, msg.sender, limit=10)
        if reply.lead:
            deps.notifier.lead(business, msg.sender, msg.name, history)
        else:
            deps.notifier.handoff(business, msg.sender, msg.name, history, reply.reason)


def create_app(deps_factory) -> FastAPI:
    """deps_factory — тәуелділіктерді құратын функция (тесттерде жалғандары беріледі)."""
    state: dict = {}

    def deps() -> Deps:
        if "deps" not in state:
            state["deps"] = deps_factory()
        return state["deps"]

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Дерекқор, Claude клиенті, embedding моделі — сервер қосылғанда бір рет дайындалады.
        # Әйтпесе бірінші хабарлама баяу өңделіп, Meta оны қайта жіберуі мүмкін.
        d = deps()
        for b in d.registry.all():
            if b.mode == "menu":
                d.menu.menu_for(b)                     # menu.yaml қатесі болса — бірден көрінеді
            else:
                d.brain.kb.search(b.id, "сәлем", k=1)
        log.info("Сервер дайын: %s", [b.id for b in d.registry.all()])
        yield

    app = FastAPI(title="WhatsApp AI бот", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "ok", "businesses": [b.id for b in deps().registry.all()]}

    @app.get("/webhook")
    def verify(request: Request):
        q = request.query_params
        if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == deps().settings.verify_token:
            return PlainTextResponse(q.get("hub.challenge", ""))
        return Response(status_code=403)

    @app.post("/webhook")
    async def receive(request: Request, background: BackgroundTasks):
        body = await request.body()
        d = deps()
        account = ""
        if d.settings.provider == "ycloud":
            # Әр клиенттің өз YCloud аккаунты болуы мүмкін: қолтаңбаны барлық secret-пен тексереміз
            account = verify_ycloud_any(d.settings.ycloud_all(), body, request.headers.get("YCloud-Signature"))
            ok = account is not None
        else:
            ok = verify_signature(d.settings.app_secret, body, request.headers.get("X-Hub-Signature-256"))
        if not ok:
            return Response(status_code=401)
        try:
            payload = await request.json()
        except Exception:
            return Response(status_code=400)
        parse = parse_ycloud_webhook if d.settings.provider == "ycloud" else parse_webhook
        for msg in parse(payload):
            if account:
                msg = replace(msg, account=account)
            background.add_task(handle_message, d, msg)
        # Тез 200 қайтару маңызды, әйтпесе Meta/YCloud хабарламаны қайта жібереді.
        return {"status": "ok"}

    return app


def build_ycloud(settings: Settings, registry: Registry) -> YCloudRouter:
    """Әр YCloud аккаунтына жеке клиент. Бизнес жоқ аккаунтқа сілтесе — сервер бірден қате айтады."""
    accounts = settings.ycloud_all()
    problems = []
    for b in registry.all():
        key, secret = accounts.get(b.ycloud_account, ("", ""))
        if not key or not secret:
            env = "" if b.ycloud_account == "default" else "_" + b.ycloud_account.upper()
            problems.append(f"{b.id}: .env ішінде YCLOUD_API_KEY{env} және YCLOUD_WEBHOOK_SECRET{env} толтыр")
    if problems:
        raise SystemExit("YCloud кілттері жетіспейді:\n  " + "\n  ".join(problems))
    clients = {name: YCloudClient(key, base_url=settings.ycloud_base_url) for name, (key, _s) in accounts.items() if key}
    return YCloudRouter(clients, {b.whatsapp_number: b.ycloud_account for b in registry.all()})


def build_deps() -> Deps:
    import anthropic

    from app.rag import KnowledgeBase

    settings = get_settings()
    if settings.provider not in ("meta", "ycloud"):
        raise SystemExit(f"PROVIDER '{settings.provider}' белгісіз: meta немесе ycloud жаз")
    registry = Registry.from_dir(settings.businesses_dir)
    store = Store(settings.data_dir / "bot.sqlite3")
    store.prune(days=30)
    kb = KnowledgeBase(settings.data_dir / "chroma")
    return Deps(
        settings=settings,
        registry=registry,
        brain=Brain(
            kb=kb,
            # timeout: API баяуласа, клиент 10 минут (әдепкі) күтіп қалмауы үшін
            llm=anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=30.0, max_retries=2),
            model=settings.model,
            max_distance=settings.max_distance,
            store=store,
        ),
        wa=(
            build_ycloud(settings, registry)
            if settings.provider == "ycloud"
            else WhatsAppClient(settings.access_token, settings.graph_version, base_url=settings.graph_base_url)
        ),
        store=store,
        notifier=Notifier(settings.telegram_bot_token, settings.telegram_chat_id, base_url=settings.telegram_api_base),
        menu=MenuBot(store),
    )


app = create_app(build_deps)
