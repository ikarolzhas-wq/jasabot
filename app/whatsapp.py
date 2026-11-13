"""Meta WhatsApp Cloud API: хабарлама қабылдау және жіберу (ресми жол, тікелей Meta).
YCloud арқылы жұмыс — app/ycloud.py."""

import hashlib
import hmac
import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """Meta әр сұранысқа X-Hub-Signature-256 қолтаңбасын қояды.
    Бұл хабарлама шын мәнінде Meta-дан келгенін дәлелдейді — жалған сұраныстан қорғаныс."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


@dataclass(frozen=True)
class IncomingMessage:
    phone_number_id: str   # қай бизнестің нөміріне келді (YCloud-та — бизнестің өз нөмірі)
    sender: str            # клиенттің нөмірі
    message_id: str
    type: str              # text, image, audio, ...
    text: str
    name: str              # клиенттің WhatsApp-тағы аты
    kind: str = "message"  # "message" — клиенттен; "echo" — бизнес иесі телефоннан жазды (Coexistence)
    ids: tuple = ()        # echo хабарының барлық id-лері (бот өзі жіберген бе, тексеру үшін)
    account: str = ""      # YCloud: хабар қай аккаунттың қолтаңбасымен келді (бос — Meta)


def _message_text(m: dict) -> str:
    """Хабарламадан мәтін алу (мәтін, батырма, интерактивті жауап). Басқа түрлерде — бос."""
    mtype = m.get("type", "")
    if mtype == "text":
        return (m.get("text") or {}).get("body", "")
    if mtype == "button":
        return (m.get("button") or {}).get("text", "")
    if mtype == "interactive":
        inter = m.get("interactive") or {}
        return (inter.get("button_reply") or inter.get("list_reply") or {}).get("title", "")
    return ""


def parse_webhook(payload: dict) -> list[IncomingMessage]:
    """Webhook JSON-нан клиент хабарламаларын алады.
    Жеткізу статустары (sent/delivered/read) өткізіліп жіберіледі."""
    out: list[IncomingMessage] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_number_id = value.get("metadata", {}).get("phone_number_id", "")
            names = {c.get("wa_id"): c.get("profile", {}).get("name", "") for c in value.get("contacts", [])}
            for m in value.get("messages", []):
                out.append(
                    IncomingMessage(
                        phone_number_id=phone_number_id,
                        sender=m.get("from", ""),
                        message_id=m.get("id", ""),
                        type=m.get("type", ""),
                        text=_message_text(m),
                        name=names.get(m.get("from"), ""),
                    )
                )
            # Coexistence: иесі WhatsApp Business қосымшасынан жазған хабарлар (smb_message_echoes)
            for m in value.get("message_echoes", []):
                out.append(
                    IncomingMessage(
                        phone_number_id=phone_number_id,
                        sender=m.get("to", ""),
                        message_id=m.get("id", ""),
                        type=m.get("type", ""),
                        text=_message_text(m),
                        name="",
                        kind="echo",
                        ids=(m.get("id", ""),),
                    )
                )
    return out


LIST_LABELS = {"kk": ("Таңдау", "Мәзір"), "ru": ("Выбрать", "Меню")}

# True — ≤3 нұсқа жылдам жауап батырмасы болып шығады (клиентке ыңғайлы). Ескерту: батырмалар
# иесінің телефонында да басылады және клиентке хабар болып кетеді; тізімде бұл болмайды.
USE_REPLY_BUTTONS = True


def build_interactive(body: str, options: list[str], list_button: str | None = None,
                      reply_buttons: bool | None = None) -> dict:
    """Батырмалар (≤3 нұсқа) немесе тізім (4–10 нұсқа). Meta мен YCloud-та формат бірдей.
    Шектеулер: мәтін ≤1024, батырма атауы ≤20, тізім жолы ≤24, жол саны ≤10.
    Тізім батырмасының тілі хабар мәтінінің тіліне қарай таңдалады (Таңдау / Выбрать)."""
    from app.menu import detect_lang

    body = (body or "👇")[:1024]
    button, section = LIST_LABELS[detect_lang(body)]
    list_button = list_button or button
    ids = [f"opt{i}" for i in range(len(options))]
    if reply_buttons is None:
        reply_buttons = USE_REPLY_BUTTONS
    if reply_buttons and len(options) <= 3:
        return {
            "type": "button",
            "body": {"text": body},
            "action": {"buttons": [{"type": "reply", "reply": {"id": i, "title": o[:20]}} for i, o in zip(ids, options)]},
        }
    return {
        "type": "list",
        "body": {"text": body},
        "action": {"button": list_button, "sections": [
            {"title": section, "rows": [{"id": i, "title": o[:24]} for i, o in zip(ids[:10], options[:10])]}
        ]},
    }


class WhatsAppClient:
    def __init__(
        self,
        access_token: str,
        graph_version: str = "v23.0",
        http: httpx.Client | None = None,
        base_url: str = "https://graph.facebook.com",
    ):
        self._token = access_token
        self._base = f"{base_url.rstrip('/')}/{graph_version}"
        self._http = http or httpx.Client(timeout=15)

    def _post(self, phone_number_id: str, payload: dict) -> dict:
        r = self._http.post(
            f"{self._base}/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {self._token}"},
            json=payload,
        )
        if r.status_code >= 400:
            log.error("WhatsApp API қатесі %s: %s", r.status_code, r.text)
            r.raise_for_status()
        return r.json()

    def send_text(self, phone_number_id: str, to: str, body: str) -> list[str]:
        """Хабар жібереді. Жіберілген хабардың id-лерін қайтарады (echo-ны ажырату үшін)."""
        # WhatsApp мәтін шегі — 4096 таңба.
        data = self._post(
            phone_number_id,
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": False, "body": body[:4096]},
            },
        )
        return [m.get("id", "") for m in data.get("messages", []) if m.get("id")]

    def send_options(self, phone_number_id: str, to: str, body: str, options: list[str]) -> list[str]:
        """Мәтін + батырмалар/тізім (базалық бот үшін)."""
        data = self._post(
            phone_number_id,
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": build_interactive(body, options),
            },
        )
        return [m.get("id", "") for m in data.get("messages", []) if m.get("id")]

    def mark_read(self, phone_number_id: str, message_id: str) -> None:
        """Клиентке көк құсбелгі көрсетеді (хабарлама оқылды). Бұл ақылы емес."""
        try:
            self._post(
                phone_number_id,
                {"messaging_product": "whatsapp", "status": "read", "message_id": message_id},
            )
        except Exception as e:  # оқылды белгісі маңызды емес, қате болса өткізіп жібереміз
            log.warning("mark_read сәтсіз: %s", e)
