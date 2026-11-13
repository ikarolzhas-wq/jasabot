import hashlib
import hmac
import json
import time
from dataclasses import replace

from fastapi.testclient import TestClient

from app.brain import Brain
from app.businesses import Registry
from app.config import get_settings
from app.main import Deps, create_app
from app.menu import MenuBot
from app.rag import Hit
from app.store import Store
from app.whatsapp import parse_webhook
from app.ycloud import YCloudClient, parse_ycloud_webhook, verify_ycloud_signature
from tests.conftest import FakeLLM, FakeNotifier, FakeWhatsApp

SECRET = "whsec_test"
BIZ = "+77005550000"
CUSTOMER = "+77011112233"


def ysign(body: bytes, secret=SECRET, ts=None) -> str:
    ts = str(int(ts or time.time()))
    sig = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"t={ts},s={sig}"


def inbound(text="Бот қанша тұрады?", wamid="wamid.in1"):
    return {
        "id": "evt_1", "type": "whatsapp.inbound_message.received", "apiVersion": "v2",
        "createTime": "2026-11-07T00:00:00.000Z",
        "whatsappInboundMessage": {
            "id": "63f8", "wamid": wamid, "wabaId": "W", "from": CUSTOMER,
            "customerProfile": {"name": "Ерлан"}, "to": BIZ, "type": "text", "text": {"body": text},
        },
    }


def echo(text="Сәлеметсіз бе, Олжас жазып тұрмын", wamid="wamid.owner1"):
    return {
        "id": "evt_2", "type": "whatsapp.smb.message.echoes", "apiVersion": "v2",
        "whatsappMessage": {"id": "abc", "wamid": wamid, "from": BIZ, "to": CUSTOMER,
                            "type": "text", "text": {"body": text}},
    }


# ---------- қолтаңба ----------

def test_signature_valid():
    body = b'{"a":1}'
    assert verify_ycloud_signature(SECRET, body, ysign(body))


def test_signature_wrong_secret_or_body():
    body = b'{"a":1}'
    assert not verify_ycloud_signature(SECRET, body, ysign(body, secret="other"))
    assert not verify_ycloud_signature(SECRET, b'{"a":2}', ysign(body))
    assert not verify_ycloud_signature(SECRET, body, None)
    assert not verify_ycloud_signature("", body, ysign(body, secret=""))
    assert not verify_ycloud_signature(SECRET, body, "garbage")


def test_signature_too_old_is_rejected():
    body = b"{}"
    assert not verify_ycloud_signature(SECRET, body, ysign(body, ts=time.time() - 3600))


# ---------- талдау ----------

def test_parse_inbound():
    [m] = parse_ycloud_webhook(inbound())
    assert (m.phone_number_id, m.sender, m.message_id, m.text, m.name, m.kind) == (
        BIZ, CUSTOMER, "wamid.in1", "Бот қанша тұрады?", "Ерлан", "message")


def test_parse_echo():
    [m] = parse_ycloud_webhook(echo())
    assert m.kind == "echo" and m.sender == CUSTOMER and m.phone_number_id == BIZ
    assert "wamid.owner1" in m.ids


def test_other_events_ignored():
    assert parse_ycloud_webhook({"type": "whatsapp.message.updated", "whatsappMessage": {}}) == []


def test_meta_echo_parsed():
    payload = {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "111"},
        "message_echoes": [{"from": "77005550000", "to": "77011112233", "id": "wamid.e", "type": "text",
                            "text": {"body": "иесі"}}],
    }}]}]}
    [m] = parse_webhook(payload)
    assert m.kind == "echo" and m.sender == "77011112233"


# ---------- жіберу ----------

class FakeHTTP:
    def __init__(self):
        self.calls = []

    def post(self, url, headers=None, json=None):
        self.calls.append((url, headers, json))
        return type("R", (), {"status_code": 200, "text": "", "json": lambda self: {"id": "y1", "wamid": "wamid.y1"}})()


def test_send_payload():
    http = FakeHTTP()
    ids = YCloudClient("KEY", http=http).send_text(BIZ, CUSTOMER, "Сәлем")
    [(url, headers, body)] = http.calls
    assert url == "https://api.ycloud.com/v2/whatsapp/messages/sendDirectly"
    assert headers == {"X-API-Key": "KEY"}
    assert body == {"from": BIZ, "to": CUSTOMER, "type": "text", "text": {"body": "Сәлем"}}
    assert set(ids) == {"y1", "wamid.y1"}


# ---------- сервер YCloud режимінде ----------

class FakeKB:
    def search(self, business_id, question, k=3):
        return [Hit("Орнату 30 000 теңгеден.", 1, 0.1)]


def make_client(registry):
    zhas = replace(registry.get("zhas-bot"), whatsapp_number=BIZ, mode="ai")   # AI жолын тексереміз
    reg = Registry([zhas, registry.get("aru-salon")])
    settings = replace(get_settings(), provider="ycloud", ycloud_webhook_secret=SECRET, default_business_id="",
                       owner_pause_hours=6)
    store = Store()
    deps = Deps(settings=settings, registry=reg, brain=Brain(FakeKB(), FakeLLM("Орнату 30 000 ₸."), "m", 0.25, store),
                wa=FakeWhatsApp(), store=store, notifier=FakeNotifier())
    return TestClient(create_app(lambda: deps)), deps


def post(client, payload, secret=SECRET):
    body = json.dumps(payload).encode()
    return client.post("/webhook", content=body,
                       headers={"Content-Type": "application/json", "YCloud-Signature": ysign(body, secret)})


def test_routes_by_business_number_and_replies(registry):
    client, deps = make_client(registry)
    assert post(client, inbound()).status_code == 200
    [(sender_number, to, body)] = deps.wa.sent
    assert sender_number == BIZ and to == CUSTOMER and "30 000" in body


def test_bad_signature_rejected(registry):
    client, deps = make_client(registry)
    assert post(client, inbound(), secret="hacker").status_code == 401
    assert deps.wa.sent == []


def test_owner_reply_pauses_bot(registry):
    """Иесі телефоннан жазды → бот осы клиентке үндемейді, бірақ әңгімені тарихқа жазады."""
    client, deps = make_client(registry)
    post(client, inbound("Бот қанша?", wamid="w1"))
    post(client, echo("Сәлеметсіз бе, мен Олжаспын, көмектесейін", wamid="owner1"))
    post(client, inbound("Рахмет, ертең хабарласайын", wamid="w2"))
    assert len(deps.wa.sent) == 1          # тек бірінші хабарға бот жауап берді
    history = deps.store.history("zhas-bot", CUSTOMER, limit=10)
    assert history[-1]["content"] == "Рахмет, ертең хабарласайын"


def test_bot_own_echo_does_not_pause(registry):
    """Боттың өз хабары echo болып қайтса, бот тоқтамауы керек."""
    client, deps = make_client(registry)
    post(client, inbound("Бот қанша?", wamid="w1"))
    post(client, echo("Орнату 30 000 ₸.", wamid="wamid.out1"))   # FakeWhatsApp берген id
    post(client, inbound("Ал айына ше?", wamid="w2"))
    assert len(deps.wa.sent) == 2


def test_owner_tapping_bot_button_does_not_pause(registry):
    """Иесі бизнес телефонында боттың батырмасын басса — бот тоқтамайды. Өзі мәтін жазса — тоқтайды."""
    client, deps = make_client(registry)
    menu_zhas = replace(deps.registry.get("zhas-bot"), mode="menu")
    deps.registry = Registry([menu_zhas, registry.get("aru-salon")])
    deps.menu = MenuBot(deps.store)
    post(client, inbound("Сәлем", wamid="w1"))
    post(client, echo("💰 Бағасы", wamid="owner-tap"))          # батырманы басты
    post(client, inbound("2", wamid="w2"))
    assert len(deps.wa.sent) == 2
    post(client, echo("Сәлем, мен Олжаспын", wamid="owner-text"))   # өзі жазды
    post(client, inbound("Рахмет", wamid="w3"))
    assert len(deps.wa.sent) == 2


def test_pause_is_per_customer(registry):
    client, deps = make_client(registry)
    post(client, echo(wamid="owner1"))
    other = inbound("Бот қанша?", wamid="w9")
    other["whatsappInboundMessage"]["from"] = "+77019998877"
    post(client, other)
    assert len(deps.wa.sent) == 1


# ---------- әр клиенттің өз YCloud аккаунты ----------

SECRET2 = "whsec_aru"
BIZ2 = "+77007770000"


def test_accounts_from_env():
    from app.config import ycloud_accounts_from_env
    env = {"YCLOUD_API_KEY_ARU": " k1 ", "YCLOUD_WEBHOOK_SECRET_ARU": "s1",
           "YCLOUD_API_KEY_TULPAR_AUTO": "k2", "YCLOUD_API_KEY_EMPTY": "", "OTHER": "x"}
    assert ycloud_accounts_from_env(env) == {"aru": ("k1", "s1"), "tulpar_auto": ("k2", "")}


def test_business_account_name_normalized(tmp_path):
    from app.businesses import load_business
    (tmp_path / "config.yaml").write_text(
        'id: x\nname: X\nphone_number_id: "1"\ncontact_phone: "+7"\nycloud_account: Aru-Salon\n', encoding="utf-8")
    (tmp_path / "knowledge.txt").write_text("", encoding="utf-8")
    assert load_business(tmp_path).ycloud_account == "aru_salon"


def test_verify_any_returns_matching_account():
    from app.ycloud import verify_ycloud_any
    accounts = {"default": ("K", SECRET), "aru": ("K2", SECRET2)}
    body = b'{"a":1}'
    assert verify_ycloud_any(accounts, body, ysign(body, SECRET2)) == "aru"
    assert verify_ycloud_any(accounts, body, ysign(body, SECRET)) == "default"
    assert verify_ycloud_any(accounts, body, ysign(body, "hacker")) is None
    assert verify_ycloud_any({"default": ("", "")}, body, ysign(body, "")) is None


def test_router_sends_with_business_own_key():
    from app.ycloud import YCloudRouter
    h1, h2 = FakeHTTP(), FakeHTTP()
    router = YCloudRouter({"default": YCloudClient("MY_KEY", http=h1), "aru": YCloudClient("ARU_KEY", http=h2)},
                          {BIZ: "default", "+7 700 777 00 00": "aru"})
    router.send_text(BIZ2, CUSTOMER, "салон")
    router.send_options(BIZ, CUSTOMER, "zhas", ["А", "Б"])
    assert [c[1]["X-API-Key"] for c in h2.calls] == ["ARU_KEY"]
    assert [c[1]["X-API-Key"] for c in h1.calls] == ["MY_KEY"]


def make_two_account_client(registry):
    zhas = replace(registry.get("zhas-bot"), whatsapp_number=BIZ, mode="ai")
    aru = replace(registry.get("aru-salon"), whatsapp_number=BIZ2, ycloud_account="aru")
    reg = Registry([zhas, aru])
    settings = replace(get_settings(), provider="ycloud", ycloud_api_key="K", ycloud_webhook_secret=SECRET,
                       ycloud_accounts={"aru": ("K2", SECRET2)}, default_business_id="", owner_pause_hours=6)
    store = Store()
    deps = Deps(settings=settings, registry=reg, brain=Brain(FakeKB(), FakeLLM("Маникюр 4000 ₸."), "m", 0.25, store),
                wa=FakeWhatsApp(), store=store, notifier=FakeNotifier())
    return TestClient(create_app(lambda: deps)), deps


def to_aru(payload):
    payload["whatsappInboundMessage"]["to"] = BIZ2
    return payload


def test_each_business_on_own_account(registry):
    client, deps = make_two_account_client(registry)
    assert post(client, to_aru(inbound("Маникюр қанша?", wamid="a1")), secret=SECRET2).status_code == 200
    assert post(client, inbound("Бот қанша?", wamid="z1"), secret=SECRET).status_code == 200
    assert sorted(s[0] for s in deps.wa.sent) == sorted([BIZ, BIZ2])


def test_other_account_cannot_speak_for_business(registry):
    """JasaBot аккаунтының secret-імен салонның атынан хабар келсе — бот жауап бермейді."""
    client, deps = make_two_account_client(registry)
    assert post(client, to_aru(inbound("Маникюр қанша?", wamid="a2")), secret=SECRET).status_code == 200
    assert deps.wa.sent == []


def test_build_ycloud_reports_missing_keys(registry):
    import pytest
    from app.main import build_ycloud
    aru = replace(registry.get("aru-salon"), whatsapp_number=BIZ2, ycloud_account="aru")
    settings = replace(get_settings(), ycloud_api_key="K", ycloud_webhook_secret=SECRET, ycloud_accounts={})
    with pytest.raises(SystemExit, match="YCLOUD_API_KEY_ARU"):
        build_ycloud(settings, Registry([aru]))
    ok = replace(settings, ycloud_accounts={"aru": ("K2", SECRET2)})
    assert build_ycloud(ok, Registry([aru])) is not None
