import hashlib
import hmac

from app.whatsapp import parse_webhook, verify_signature


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def text_payload(text="Маникюр қанша?", msg_id="wamid.1", phone_number_id="111"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "77001234567", "phone_number_id": phone_number_id},
                    "contacts": [{"profile": {"name": "Айгүл"}, "wa_id": "77011112233"}],
                    "messages": [{
                        "from": "77011112233", "id": msg_id, "timestamp": "1730000000",
                        "type": "text", "text": {"body": text},
                    }],
                },
            }],
        }],
    }


def test_signature_valid_and_invalid():
    body = b'{"a":1}'
    assert verify_signature("secret", body, sign("secret", body))
    assert not verify_signature("secret", body, sign("other", body))
    assert not verify_signature("secret", body, None)
    assert not verify_signature("", body, sign("", body))  # құпиясыз сервер ештеңе қабылдамайды


def test_parse_text_message():
    [m] = parse_webhook(text_payload())
    assert m.phone_number_id == "111"
    assert m.sender == "77011112233"
    assert m.text == "Маникюр қанша?"
    assert m.name == "Айгүл"
    assert m.type == "text"


def test_status_updates_are_ignored():
    payload = {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "111"},
        "statuses": [{"id": "wamid.1", "status": "delivered"}],
    }}]}]}
    assert parse_webhook(payload) == []


def test_non_text_message_has_empty_text():
    payload = text_payload()
    msg = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    msg["type"] = "audio"
    del msg["text"]
    [m] = parse_webhook(payload)
    assert m.type == "audio" and m.text == ""
