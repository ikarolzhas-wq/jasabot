from dataclasses import replace

from app.notify import Notifier


class FakeHTTP:
    def __init__(self):
        self.posts = []

    def post(self, url, json):
        self.posts.append((url, json))
        return type("R", (), {"status_code": 200, "text": "ok"})()


HISTORY = [
    {"role": "user", "content": "Маникюрге жазылғым келеді"},
    {"role": "assistant", "content": "Қай күні, қай уақытқа?"},
    {"role": "user", "content": "Ертең 15:00"},
]


def test_lead_message_has_link_and_conversation(salon):
    http = FakeHTTP()
    Notifier("TOKEN", "DEFAULT", http=http).lead(salon, "77011112233", "Айгүл", HISTORY)
    [(url, body)] = http.posts
    assert url.endswith("/botTOKEN/sendMessage")
    assert body["chat_id"] == "DEFAULT"
    assert "ЖАҢА ӨТІНІМ" in body["text"] and "https://wa.me/77011112233" in body["text"]
    assert "👤 Ертең 15:00" in body["text"] and "Айгүл" in body["text"]


def test_each_business_owner_gets_own_chat(salon):
    http = FakeHTTP()
    owner = replace(salon, telegram_chat_id="SALON_OWNER")
    Notifier("TOKEN", "DEFAULT", http=http).handoff(owner, "7701", "", HISTORY, "llm_handoff")
    assert http.posts[0][1]["chat_id"] == "SALON_OWNER"
    assert "АДАМ КЕРЕК" in http.posts[0][1]["text"]


def test_without_telegram_nothing_is_sent(salon):
    http = FakeHTTP()
    Notifier("", "", http=http).lead(salon, "7701", "", HISTORY)
    assert http.posts == []


# ---------- WhatsApp арқылы иесіне хабар ----------

class FakeWA:
    def __init__(self, template_fails=False):
        self.templates, self.texts, self.fail = [], [], template_fails

    def send_template(self, number, to, name, lang, params):
        if self.fail:
            raise RuntimeError("template not approved")
        self.templates.append((number, to, name, lang, params))
        return ["t1"]

    def send_text(self, number, to, body):
        self.texts.append((number, to, body))
        return ["x1"]


LEAD_HISTORY = [
    {"role": "user", "content": "📝 Қосылу"},
    {"role": "assistant", "content": "Бизнесіңіз қай салада?"},
    {"role": "user", "content": "Клиника"},
    {"role": "assistant", "content": "📝 Өтінім:\nСала: Клиника\nАтауы, қаласы: «Шипа», Астана"},
]


def test_lead_goes_to_owner_personal_whatsapp_as_template(salon):
    wa = FakeWA()
    biz = replace(salon, whatsapp_number="+7 702 114 38 55", notify_whatsapp="+7 777 000 11 22")
    Notifier(http=FakeHTTP(), whatsapp=wa).lead(biz, "77781234567", "Айгерім", LEAD_HISTORY)
    [(number, to, name, lang, params)] = wa.templates
    assert number == "+7 702 114 38 55" and to == "77770001122"
    assert name == "jasabot_new_lead" and lang == "kk"
    assert params[0] == "Жаңа өтінім" and params[2] == "Айгерім, +77781234567"
    assert params[3] == "Сала: Клиника · Атауы, қаласы: «Шипа», Астана" and params[4] == "77781234567"
    assert all("\n" not in p for p in params)            # шаблон айнымалысында жаңа жол болмауы керек


def test_template_failure_falls_back_to_text(salon):
    wa = FakeWA(template_fails=True)
    biz = replace(salon, whatsapp_number="+77021143855", notify_whatsapp="77770001122")
    Notifier(http=FakeHTTP(), whatsapp=wa).handoff(biz, "7701", "", HISTORY, "human")
    [(_, to, body)] = wa.texts
    assert to == "77770001122" and "Адам керек" in body and "https://wa.me/7701" in body


def test_no_owner_number_no_whatsapp(salon):
    wa = FakeWA()
    Notifier(http=FakeHTTP(), whatsapp=wa).lead(replace(salon, whatsapp_number="+7702"), "7701", "", HISTORY)
    assert wa.templates == [] and wa.texts == []
