"""YCloud арқылы WhatsApp (ресми серіктес, Coexistence қолдайды).

Meta-мен айырмашылығы:
  - Хабар жіберу: POST https://api.ycloud.com/v2/whatsapp/messages/sendDirectly, X-API-Key тақырыбы
  - Бизнес нөмірі phone_number_id емес, E.164 форматындағы нөмір: "+77001234567"
  - Webhook қолтаңбасы: YCloud-Signature: t=TIMESTAMP,s=HMAC_SHA256(secret, "TIMESTAMP.BODY")
  - Оқиға түрлері: whatsapp.inbound_message.received (клиенттен),
    echo оқиғалары (бизнес иесі телефоннан жазған хабарлар — Coexistence)

Құжаттама: docs.ycloud.com (Webhooks, WhatsApp Message Sending Guide).
"""

import hashlib
import hmac
import logging
import time

import httpx

from app.whatsapp import IncomingMessage, _message_text, build_interactive

log = logging.getLogger(__name__)

INBOUND = "whatsapp.inbound_message.received"
# Иесі WhatsApp Business қосымшасынан жазған хабарлардың көшірмесі (echo).
# Нақты оқиға атауын алғашқы нақты webhook-пен тексер: логта "YCloud оқиғасы" жазылады.
ECHO_TYPES = {"whatsapp.smb.message.echoes", "whatsapp.echo_message.created"}
ECHO_KEYS = ("whatsappMessage", "whatsappSmbMessageEchoes", "smbMessageEchoes", "whatsappEchoMessage")


def verify_ycloud_signature(
    secret: str, body: bytes, header: str | None, now: float | None = None, tolerance: int = 300
) -> bool:
    if not secret or not header:
        return False
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts, sig = parts["t"].strip(), parts["s"].strip()
        if abs((now or time.time()) - int(ts)) > tolerance:   # ескі сұраныс — қайта жіберу шабуылынан қорғаныс
            return False
    except (KeyError, ValueError):
        return False
    expected = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def verify_ycloud_any(accounts: dict[str, tuple[str, str]], body: bytes, header: str | None) -> str | None:
    """Бірнеше YCloud аккаунты болса: қолтаңба қайсысының secret-іне сәйкес келеді — соның атын қайтарады.
    Ешқайсысына сәйкес келмесе — None (жалған сұраныс)."""
    for name, (_key, secret) in accounts.items():
        if verify_ycloud_signature(secret, body, header):
            return name
    return None


def parse_ycloud_webhook(payload: dict) -> list[IncomingMessage]:
    etype = payload.get("type", "")
    if etype == INBOUND:
        m = payload.get("whatsappInboundMessage") or {}
        return [IncomingMessage(
            phone_number_id=m.get("to", ""),          # бизнес нөмірі
            sender=m.get("from", ""),                 # клиент нөмірі
            message_id=m.get("wamid") or m.get("id") or payload.get("id", ""),
            type=m.get("type", ""),
            text=_message_text(m),
            name=(m.get("customerProfile") or {}).get("name", ""),
        )]
    if etype in ECHO_TYPES:
        m = next((payload[k] for k in ECHO_KEYS if isinstance(payload.get(k), dict)), {})
        return [IncomingMessage(
            phone_number_id=m.get("from", ""),        # бизнес нөмірі (иесі жазды)
            sender=m.get("to", ""),                   # клиент нөмірі
            message_id=m.get("wamid") or m.get("id") or payload.get("id", ""),
            type=m.get("type", ""),
            text=_message_text(m),
            name="",
            kind="echo",
            ids=tuple(x for x in (m.get("wamid"), m.get("id")) if x),
        )]
    if etype:
        log.info("YCloud оқиғасы өткізілді: %s", etype)
    return []


class YCloudClient:
    def __init__(self, api_key: str, http: httpx.Client | None = None, base_url: str = "https://api.ycloud.com/v2"):
        self._key = api_key
        self._http = http or httpx.Client(timeout=15)
        self._base = base_url.rstrip("/")

    def _send(self, payload: dict) -> list[str]:
        r = self._http.post(
            f"{self._base}/whatsapp/messages/sendDirectly",
            headers={"X-API-Key": self._key},
            json=payload,
        )
        if r.status_code >= 400:
            log.error("YCloud API қатесі %s: %s", r.status_code, r.text)
            r.raise_for_status()
        data = r.json()
        return [x for x in (data.get("wamid"), data.get("id")) if x]

    def send_text(self, business_number: str, to: str, body: str) -> list[str]:
        """Хабар жібереді. Жіберілген хабардың id-лерін қайтарады (echo-ны ажырату үшін)."""
        return self._send({"from": business_number, "to": to, "type": "text", "text": {"body": body[:4096]}})

    def send_options(self, business_number: str, to: str, body: str, options: list[str]) -> list[str]:
        """Мәтін + батырмалар/тізім (базалық бот үшін). Формат Meta-дағыдай."""
        return self._send({"from": business_number, "to": to, "type": "interactive",
                           "interactive": build_interactive(body, options)})

    def mark_read(self, business_number: str, message_id: str) -> None:
        """YCloud құжаттамасында "оқылды" белгісі көрсетілмеген — әзірге ештеңе істемейміз."""
        return None


def _digits(number: str) -> str:
    return "".join(ch for ch in number if ch.isdigit())


class YCloudRouter:
    """Әр бизнес өз YCloud аккаунтында болса: хабарды сол бизнестің кілтімен жібереді.

    clients            — {"default": YCloudClient(...), "aru": YCloudClient(...)}
    account_by_number  — {"77001234567": "aru"} (бизнестің WhatsApp нөмірі → аккаунт)
    """

    def __init__(self, clients: dict[str, "YCloudClient"], account_by_number: dict[str, str]):
        self._clients = clients
        self._by_number = {_digits(n): a for n, a in account_by_number.items() if _digits(n)}

    def _client(self, business_number: str) -> "YCloudClient":
        name = self._by_number.get(_digits(business_number), "default")
        return self._clients[name]

    def send_text(self, business_number: str, to: str, body: str) -> list[str]:
        return self._client(business_number).send_text(business_number, to, body)

    def send_options(self, business_number: str, to: str, body: str, options: list[str]) -> list[str]:
        return self._client(business_number).send_options(business_number, to, body, options)

    def mark_read(self, business_number: str, message_id: str) -> None:
        return self._client(business_number).mark_read(business_number, message_id)
