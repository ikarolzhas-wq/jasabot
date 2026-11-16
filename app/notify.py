"""Бизнес иесіне/әкімшісіне хабарлау: WhatsApp (шаблон) және/немесе Telegram.

WhatsApp: бизнестің өз нөмірі (бот) иесінің ЖЕКЕ нөміріне (config.yaml: notify_whatsapp)
"🔔 Жаңа өтінім" шаблонын жібереді. Шаблон YCloud-та бекітілген болуы керек.
Шаблон өтпесе — кәдімгі мәтін жіберіп көреміз (иесі соңғы 24 сағатта ботқа жазған болса өтеді).


Екі түрлі хабар:
  🟢 Өтінім  — клиент жазылғысы / сатып алғысы / қосылғысы келеді
  🔔 Адам керек — бот жауап бере алмады немесе клиент адаммен сөйлескісі келеді

Әр хабарда клиенттің нөмірі және бір басумен WhatsApp-та жазуға сілтеме (wa.me) болады.
Әр бизнестің хабары өз иесіне барады: config.yaml ішіндегі telegram_chat_id.
Ол бос болса, .env ішіндегі ортақ TELEGRAM_CHAT_ID-ге барады.

Неге WhatsApp емес: бизнестің өзі бірінші жазатын WhatsApp хабарламасы ақылы
шаблонды талап етеді. Telegram тегін әрі тез.
"""

import logging

import httpx

log = logging.getLogger(__name__)

REASONS = {
    "human": "клиент адаммен сөйлескісі келеді",
    "llm_handoff": "бот жауабын білмеді",
    "not_found": "бот жауабын білмеді",
}


def _conversation(history: list[dict], limit_chars: int = 1500) -> str:
    lines = [("👤 " if m["role"] == "user" else "🤖 ") + m["content"].strip() for m in history]
    text = "\n".join(lines)
    return text if len(text) <= limit_chars else "…" + text[-limit_chars:]


def _one_line(text: str, limit: int = 600) -> str:
    """WhatsApp шаблон айнымалысында жаңа жол, tab, 4+ бос орын болмауы керек."""
    flat = " · ".join(part.strip() for part in text.splitlines() if part.strip())
    flat = " ".join(flat.replace("\t", " ").split())
    return flat[:limit] or "—"


def _lead_details(history: list[dict]) -> str:
    """Өтінімнің қысқаша мазмұны: мәзір ботының "📝 Өтінім:" жазбасы, болмаса клиенттің соңғы хабары."""
    for m in reversed(history):
        if m["role"] == "assistant" and m["content"].startswith("📝 Өтінім:"):
            return m["content"].split("\n", 1)[1] if "\n" in m["content"] else ""
    for m in reversed(history):
        if m["role"] == "user":
            return "Соңғы хабары: " + m["content"]
    return ""


class Notifier:
    def __init__(
        self,
        bot_token: str = "",
        default_chat_id: str = "",
        http: httpx.Client | None = None,
        base_url: str = "https://api.telegram.org",
        whatsapp=None,
        template: str = "jasabot_new_lead",
        template_lang: str = "kk",
    ):
        self._token = bot_token
        self._default_chat = default_chat_id
        self._http = http or httpx.Client(timeout=10)
        self._base = base_url.rstrip("/")
        self._wa = whatsapp            # YCloudRouter/YCloudClient (send_template, send_text)
        self._template = template
        self._template_lang = template_lang

    def _send_whatsapp(self, business, title: str, customer: str, customer_name: str, details: str) -> None:
        owner = "".join(ch for ch in getattr(business, "notify_whatsapp", "") if ch.isdigit())
        number = getattr(business, "whatsapp_number", "")
        if not (self._wa and owner and number):
            return
        client = f"{customer_name}, +{customer}" if customer_name else f"+{customer}"
        params = [_one_line(title, 60), _one_line(business.name, 60), _one_line(client, 80),
                  _one_line(details), customer]
        try:
            self._wa.send_template(number, owner, self._template, self._template_lang, params)
            return
        except Exception as e:
            log.warning("WhatsApp шаблон жіберілмеді (%s), мәтінмен жіберіп көремін", e)
        text = (f"🔔 {title} — {business.name}\nКлиент: {client}\n{details}\n\n"
                f"Клиентке жазу: https://wa.me/{customer}")
        try:
            self._wa.send_text(number, owner, text)
        except Exception as e:
            log.warning("WhatsApp хабар иесіне жіберілмеді: %s", e)

    def _send(self, chat_id: str, text: str) -> None:
        chat_id = chat_id or self._default_chat
        if not (self._token and chat_id):
            log.info("Telegram бапталмаған, хабар тек логта:\n%s", text)
            return
        try:
            r = self._http.post(
                f"{self._base}/bot{self._token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            )
            if r.status_code >= 400:
                log.warning("Telegram қатесі %s: %s", r.status_code, r.text)
        except Exception as e:
            log.warning("Telegram хабар жіберілмеді: %s", e)

    def lead(self, business, customer: str, customer_name: str, history: list[dict]) -> None:
        self._send_whatsapp(business, "Жаңа өтінім", customer, customer_name, _lead_details(history))
        self._send(
            getattr(business, "telegram_chat_id", ""),
            f"🟢 ЖАҢА ӨТІНІМ — {business.name}\n"
            f"Клиент: {customer_name or '—'}, +{customer}\n"
            f"Жазу: https://wa.me/{customer}\n\n"
            f"Әңгіме:\n{_conversation(history)}",
        )

    def handoff(self, business, customer: str, customer_name: str, history: list[dict], reason: str) -> None:
        why = REASONS.get(reason, reason)
        self._send_whatsapp(business, "Адам керек", customer, customer_name,
                            f"Себебі: {why}. " + _lead_details(history))
        self._send(
            getattr(business, "telegram_chat_id", ""),
            f"🔔 АДАМ КЕРЕК — {business.name} ({REASONS.get(reason, reason)})\n"
            f"Клиент: {customer_name or '—'}, +{customer}\n"
            f"Жазу: https://wa.me/{customer}\n\n"
            f"Әңгіме:\n{_conversation(history)}",
        )
