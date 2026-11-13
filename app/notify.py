"""Бизнес иесіне/әкімшісіне Telegram арқылы хабарлау.

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


class Notifier:
    def __init__(
        self,
        bot_token: str = "",
        default_chat_id: str = "",
        http: httpx.Client | None = None,
        base_url: str = "https://api.telegram.org",
    ):
        self._token = bot_token
        self._default_chat = default_chat_id
        self._http = http or httpx.Client(timeout=10)
        self._base = base_url.rstrip("/")

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
        self._send(
            getattr(business, "telegram_chat_id", ""),
            f"🟢 ЖАҢА ӨТІНІМ — {business.name}\n"
            f"Клиент: {customer_name or '—'}, +{customer}\n"
            f"Жазу: https://wa.me/{customer}\n\n"
            f"Әңгіме:\n{_conversation(history)}",
        )

    def handoff(self, business, customer: str, customer_name: str, history: list[dict], reason: str) -> None:
        self._send(
            getattr(business, "telegram_chat_id", ""),
            f"🔔 АДАМ КЕРЕК — {business.name} ({REASONS.get(reason, reason)})\n"
            f"Клиент: {customer_name or '—'}, +{customer}\n"
            f"Жазу: https://wa.me/{customer}\n\n"
            f"Әңгіме:\n{_conversation(history)}",
        )
