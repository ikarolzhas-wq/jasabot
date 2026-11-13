"""Тесттерге ортақ жалған (fake) объектілер: интернет пен API кілтсіз жұмыс істейді."""

import hashlib
import math
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.businesses import Registry  # noqa: E402


def fake_embedder(texts: list[str]) -> list[list[float]]:
    """Сөздер қапшығы (bag of words) векторы. Нақты модельдің орнына тек тесттер үшін."""
    out = []
    for t in texts:
        vec = [0.0] * 256
        for w in re.findall(r"\w+", t.lower().replace("passage", "").replace("query", "")):
            vec[int(hashlib.md5(w[:5].encode()).hexdigest(), 16) % 256] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        out.append([v / norm for v in vec])
    return out


class FakeLLM:
    """anthropic.Anthropic() орнына: шақыруларды жазып алады, дайын жауап қайтарады."""

    def __init__(self, answer: str = "Маникюр 4000 теңге тұрады."):
        self.answer = answer
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.answer)])


class FakeWhatsApp:
    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []
        self.options: list[list[str]] = []
        self.read: list[str] = []

    def send_text(self, phone_number_id, to, body):
        self.sent.append((phone_number_id, to, body))
        return [f"wamid.out{len(self.sent)}"]

    def send_options(self, phone_number_id, to, body, options):
        self.sent.append((phone_number_id, to, body))
        self.options.append(list(options))
        return [f"wamid.out{len(self.sent)}"]

    def mark_read(self, phone_number_id, message_id):
        self.read.append(message_id)


class FakeNotifier:
    def __init__(self):
        self.calls: list[tuple] = []   # "адам керек" хабарлары
        self.leads: list[tuple] = []   # өтінімдер

    def handoff(self, *args):
        self.calls.append(args)

    def lead(self, *args):
        self.leads.append(args)


@pytest.fixture
def registry() -> Registry:
    return Registry.from_dir(ROOT / "businesses")


@pytest.fixture
def salon(registry):
    return registry.get("aru-salon")
