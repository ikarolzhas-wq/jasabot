import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import Deps, create_app
from app.menu import BACK, HOME, Menu, MenuBot, MenuError, render_text
from app.store import Store
from app.whatsapp import build_interactive
from tests.conftest import FakeNotifier, FakeWhatsApp
from tests.test_whatsapp import sign, text_payload


@pytest.fixture
def zhas(registry):
    return registry.get("zhas-bot")


@pytest.fixture
def salon_menu(registry):
    return replace(registry.get("aru-salon"), mode="menu")


def chat(bot, business, user, *messages):
    replies = [bot.answer(business, user, m) for m in messages]
    return replies[-1]


# ---------- мәзір файлдары ----------

def test_real_menus_are_valid(zhas, salon_menu):
    bot = MenuBot(Store())
    assert bot.menu_for(zhas).all_titles()
    assert bot.menu_for(salon_menu).all_titles()


def test_long_title_rejected():
    with pytest.raises(MenuError, match="20"):
        Menu({"menu": {"items": [{"title": "Бұл атау өте өте ұзын болып кетті", "answer": "x"}]}})


def test_missing_form_rejected():
    with pytest.raises(MenuError, match="формасы"):
        Menu({"menu": {"items": [{"title": "Жазылу", "action": "form", "form": "joq"}]}})


def test_duplicate_title_rejected():
    with pytest.raises(MenuError, match="екі рет"):
        Menu({"menu": {"items": [{"title": "А", "answer": "1"}, {"title": "А", "answer": "2"}]}})


# ---------- сөйлесу ----------

def test_first_message_shows_welcome_and_menu(zhas):
    r = MenuBot(Store()).answer(zhas, "u1", "Сәлем, бот қанша тұрады?")
    assert r.reason == "welcome" and "JasaBot" in r.text
    assert r.options[0] == "🤖 Бот не істейді" and len(r.options) == 6


def test_choose_by_number_and_by_title(zhas):
    bot = MenuBot(Store())
    r = chat(bot, zhas, "u1", "сәлем", "2")
    assert r.reason == "answer" and "10 000" in r.text and "25 000" not in r.text   # тек базалық тариф
    assert r.options == ["🎁 Тегін сынақ", "✅ Қосылу", HOME]
    r2 = chat(bot, zhas, "u2", "сәлем", "бағасы")
    assert r2.text == r.text


def test_numbers_refer_to_buttons_on_screen(zhas):
    """Жауаптан кейін "1" — экрандағы бірінші батырма (Тегін сынақ), басты мәзірдің біріншісі емес."""
    bot = MenuBot(Store())
    r = chat(bot, zhas, "u1", "сәлем", "2", "1")
    assert "7 күн" in r.text


def test_connect_form_creates_lead(zhas):
    bot = MenuBot(Store())
    chat(bot, zhas, "u1", "сәлем", "💰 Бағасы")
    r = bot.answer(zhas, "u1", "✅ Қосылу")
    assert r.reason == "form" and "Клиника" in r.options and HOME in r.options
    bot.answer(zhas, "u1", "2")                 # Клиника
    r = bot.answer(zhas, "u1", "ертең 14:00")
    assert r.lead and r.reason == "lead"
    assert r.summary == "Бизнес: Клиника\nЫңғайлы уақыт: ертең 14:00"
    assert "JasaBot" in r.text and "Олжас" not in r.text


def test_form_can_be_cancelled_with_home(zhas):
    bot = MenuBot(Store())
    chat(bot, zhas, "u1", "сәлем", "✅ Қосылу")
    r = bot.answer(zhas, "u1", HOME)
    assert r.reason == "menu" and not r.lead
    r = bot.answer(zhas, "u1", "Клиника")       # енді форма емес — мәзірде жоқ
    assert r.reason == "fallback"


def test_unknown_text_shows_fallback_with_same_buttons(zhas):
    bot = MenuBot(Store())
    first = bot.answer(zhas, "u1", "сәлем")
    r = bot.answer(zhas, "u1", "Python-да тізімді қалай сұрыптаймын?")
    assert r.reason == "fallback" and r.options == first.options


def test_human_action_hands_off(zhas):
    r = chat(MenuBot(Store()), zhas, "u1", "сәлем", "📞 Менеджер")
    assert r.handoff and r.reason == "human"


def test_submenu_back_and_home(salon_menu):
    bot = MenuBot(Store())
    r = chat(bot, salon_menu, "u1", "сәлем", "📋 Бағалар")
    assert r.options == ["Шаш", "Тырнақ", "Қас пен кірпік", BACK]
    r = bot.answer(salon_menu, "u1", "Тырнақ")
    assert "маникюр" in r.text and r.options == ["✍️ Жазылу", BACK, HOME]
    r = bot.answer(salon_menu, "u1", BACK)
    assert r.options[0] == "Шаш"               # Бағалар ішкі мәзіріне оралдық
    r = bot.answer(salon_menu, "u1", BACK)
    assert r.options[0] == "📋 Бағалар"        # басты мәзір


def test_booking_from_price_answer(salon_menu):
    bot = MenuBot(Store())
    chat(bot, salon_menu, "u1", "сәлем", "📋 Бағалар", "Тырнақ")
    bot.answer(salon_menu, "u1", "✍️ Жазылу")
    bot.answer(salon_menu, "u1", "Тырнақ")
    bot.answer(salon_menu, "u1", "Ертең")
    r = bot.answer(salon_menu, "u1", "15:00")
    assert r.lead and r.summary == "Қызмет: Тырнақ\nКүні: Ертең\nУақыты: 15:00"


def test_menu_word_resets(zhas):
    bot = MenuBot(Store())
    r = chat(bot, zhas, "u1", "сәлем", "2", "мәзір")
    assert r.options[0] == "🤖 Бот не істейді"


def test_users_are_independent(zhas):
    bot = MenuBot(Store())
    chat(bot, zhas, "u1", "сәлем", "✅ Қосылу")
    assert bot.answer(zhas, "u2", "Клиника").reason == "welcome"


def test_history_contains_lead_summary(zhas):
    store = Store()
    bot = MenuBot(store)
    chat(bot, zhas, "u1", "сәлем", "✅ Қосылу", "Клиника", "ертең")
    assert "📝 Өтінім:" in store.history("zhas-bot", "u1", limit=20)[-1]["content"]


def test_render_text_numbers_options():
    from app.menu import MenuReply
    assert render_text(MenuReply("Таңда", ["А", "Б"])) == "Таңда\n\n1. А\n2. Б"


# ---------- WhatsApp пішімі ----------

def test_build_interactive_buttons_and_list():
    b = build_interactive("Таңдаңыз", ["А", "Б", "В"])
    assert b["type"] == "button" and len(b["action"]["buttons"]) == 3
    l = build_interactive("Таңдаңыз", ["1", "2", "3", "4"])
    assert l["type"] == "list" and len(l["action"]["sections"][0]["rows"]) == 4
    assert len(build_interactive("x" * 2000, ["А"])["body"]["text"]) == 1024


# ---------- сервер ----------

def make_client(registry):
    settings = replace(get_settings(), verify_token="v", app_secret="s", default_business_id="", provider="meta")
    store = Store()
    deps = Deps(settings=settings, registry=registry, brain=None, wa=FakeWhatsApp(), store=store,
                notifier=FakeNotifier(), menu=MenuBot(store))
    return TestClient(create_app(lambda: deps)), deps


def post(client, text, mid, pnid):
    body = json.dumps(text_payload(text, msg_id=mid, phone_number_id=pnid)).encode()
    return client.post("/webhook", content=body,
                       headers={"Content-Type": "application/json", "X-Hub-Signature-256": sign("s", body)})


def test_server_menu_flow_and_lead(registry, zhas):
    client, deps = make_client(registry)
    pnid = zhas.phone_number_id
    for i, t in enumerate(["сәлем", "✅ Қосылу", "Автосервис", "сенбі 11:00"]):
        assert post(client, t, f"m{i}", pnid).status_code == 200
    assert deps.wa.options[0][0] == "🤖 Бот не істейді"        # бірінші жауап — мәзір батырмалары
    assert len(deps.wa.sent) == 4
    [(business, customer, name, history)] = deps.notifier.leads
    assert business.id == "zhas-bot" and customer == "77011112233"
    assert "Бизнес: Автосервис" in history[-1]["content"]


def test_greeting_again_shows_welcome_not_fallback(zhas):
    bot = MenuBot(Store())
    r = chat(bot, zhas, "u1", "сәлем", "2", "Сәлеметсіз бе!")
    assert r.reason == "welcome" and r.options[0] == "🤖 Бот не істейді"


def test_greeting_does_not_break_form(zhas):
    """Форма ішінде "сәлем" жазса — жауап ретінде қабылданады, форма үзілмейді."""
    bot = MenuBot(Store())
    r = chat(bot, zhas, "u1", "сәлем", "✅ Қосылу", "Сәлем")
    assert r.reason == "form"
