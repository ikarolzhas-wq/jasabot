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
