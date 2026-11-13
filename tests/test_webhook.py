import json
from dataclasses import replace

from fastapi.testclient import TestClient

from app.brain import Brain
from app.config import get_settings
from app.main import UNSUPPORTED_TEXT, Deps, create_app
from app.rag import Hit
from app.store import Store
from tests.conftest import FakeLLM, FakeNotifier, FakeWhatsApp
from tests.test_whatsapp import sign, text_payload

SECRET = "test-secret"


class FakeKB:
    def search(self, business_id, question, k=3):
        if "маникюр" in question.lower():
            return [Hit("Классикалық маникюр 4000 теңге.", 4, 0.1)]
        return [Hit("Жұмыс уақыты.", 2, 0.9)]


def make_client(registry, default_business="", answer="Маникюр 4000 теңге тұрады."):
    settings = replace(
        get_settings(), verify_token="verify-me", app_secret=SECRET, default_business_id=default_business
    )
    store = Store()
    deps = Deps(
        settings=settings,
        registry=registry,
        brain=Brain(FakeKB(), FakeLLM(answer), "m", 0.25, store),
        wa=FakeWhatsApp(),
        store=store,
        notifier=FakeNotifier(),
    )
    return TestClient(create_app(lambda: deps)), deps


def post(client, payload, secret=SECRET):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhook", content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sign(secret, body)},
    )


def test_verify_webhook(registry):
    client, _ = make_client(registry)
    ok = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    bad = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "42"})
    assert bad.status_code == 403


def test_rejects_bad_signature(registry):
    client, deps = make_client(registry, default_business="aru-salon")
    assert post(client, text_payload(), secret="hacker").status_code == 401
    assert deps.wa.sent == []


def test_answers_message(registry):
    client, deps = make_client(registry, default_business="aru-salon")
    assert post(client, text_payload("Маникюр қанша?")).status_code == 200
    [(pnid, to, body)] = deps.wa.sent
    assert to == "77011112233" and "4000" in body
    assert deps.wa.read == ["wamid.1"]


def test_duplicate_delivery_answered_once(registry):
    client, deps = make_client(registry, default_business="aru-salon")
    post(client, text_payload(msg_id="wamid.X"))
    post(client, text_payload(msg_id="wamid.X"))
    assert len(deps.wa.sent) == 1


def test_routes_by_phone_number_id(registry):
    client, deps = make_client(registry)  # default жоқ
    auto = registry.get("tulpar-auto")
    post(client, text_payload("Сәлем", phone_number_id=auto.phone_number_id))
    assert deps.wa.sent[0][2] == auto.greeting


def test_unknown_number_is_ignored(registry):
    client, deps = make_client(registry)
    post(client, text_payload(phone_number_id="unknown"))
    assert deps.wa.sent == []


def test_off_topic_does_not_bother_owner(registry):
    client, deps = make_client(registry, default_business="aru-salon")
    post(client, text_payload("Доллар бағамы қандай?"))
    assert "Бұл чат тек" in deps.wa.sent[0][2]
    assert deps.notifier.calls == []


def test_missing_info_notifies_owner(registry):
    """Сұрақ бизнеске қатысты (маникюр бөлігі табылды), бірақ Claude жауабын таппады."""
    client, deps = make_client(registry, default_business="aru-salon", answer="[[HANDOFF]]")
    post(client, text_payload("Маникюрге гарантия бар ма?"))
    assert "+7 700 123 45 67" in deps.wa.sent[0][2]
    assert len(deps.notifier.calls) == 1


def test_voice_message_gets_text_only_notice(registry):
    client, deps = make_client(registry, default_business="aru-salon")
    payload = text_payload()
    msg = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    msg["type"] = "audio"
    del msg["text"]
    post(client, payload)
    assert deps.wa.sent[0][2] == UNSUPPORTED_TEXT


def test_lead_notifies_owner_with_conversation(registry):
    client, deps = make_client(registry, default_business="aru-salon",
                               answer="Өтініміңізді әкімшіге жібердім. [[LEAD]]")
    post(client, text_payload("Маникюрге жазылғым келеді, ертең 15:00", msg_id="L1"))
    assert "[[" not in deps.wa.sent[0][2]
    [(business, customer, name, history)] = deps.notifier.leads
    assert business.id == "aru-salon" and customer == "77011112233" and name == "Айгүл"
    assert history[0]["content"] == "Маникюрге жазылғым келеді, ертең 15:00"
    assert deps.notifier.calls == []
