"""Бизнестерді businesses/<id>/ папкаларынан жүктеу.

Әр папкада екі файл:
  config.yaml   — атауы, phone_number_id, байланыс нөмірі, қосымша ережелер
  knowledge.txt — бот жауап беретін барлық ақпарат (бағалар, уақыт, ережелер)
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

from app.config import account_name


@dataclass(frozen=True)
class Business:
    id: str
    name: str
    short_name: str          # "Бұл чат тек ___ бойынша көмектеседі"
    short_name_ru: str       # "Этот чат помогает только по вопросам ___"
    sector: str
    phone_number_id: str
    contact_phone: str
    greeting: str
    extra_rules: str
    knowledge_path: Path
    telegram_chat_id: str = ""   # өтінімдер мен "адам керек" хабарлары осы иесіне барады
    whatsapp_number: str = ""    # бизнестің WhatsApp нөмірі, мысалы "+77001234567" (YCloud режимі үшін)
    mode: str = "ai"             # "ai" — Claude (еркін сөйлесу), "menu" — базалық бот (menu.yaml)
    ycloud_account: str = "default"   # клиенттің өз YCloud аккаунты (.env: YCLOUD_API_KEY_<АТЫ>)

    def knowledge_text(self) -> str:
        return self.knowledge_path.read_text(encoding="utf-8")


def load_business(folder: Path) -> Business:
    cfg = yaml.safe_load((folder / "config.yaml").read_text(encoding="utf-8")) or {}
    missing = [k for k in ("id", "name", "phone_number_id", "contact_phone") if not cfg.get(k)]
    if missing:
        raise ValueError(f"{folder.name}/config.yaml ішінде жоқ: {', '.join(missing)}")
    return Business(
        id=str(cfg["id"]),
        name=str(cfg["name"]),
        short_name=str(cfg.get("short_name") or cfg["name"]),
        short_name_ru=str(cfg.get("short_name_ru") or cfg.get("short_name") or cfg["name"]),
        sector=str(cfg.get("sector", "")),
        phone_number_id=str(cfg["phone_number_id"]),
        contact_phone=str(cfg["contact_phone"]),
        greeting=str(cfg.get("greeting", "")).strip(),
        extra_rules=str(cfg.get("extra_rules", "")).strip(),
        knowledge_path=folder / "knowledge.txt",
        telegram_chat_id=str(cfg.get("telegram_chat_id") or ""),
        whatsapp_number=str(cfg.get("whatsapp_number") or ""),
        mode=str(cfg.get("mode") or "ai").strip().lower(),
        ycloud_account=account_name(str(cfg.get("ycloud_account") or "default")),
    )


def _digits(number: str) -> str:
    return "".join(ch for ch in number if ch.isdigit())


class Registry:
    """Барлық бизнестер: id бойынша және phone_number_id бойынша іздеу."""

    def __init__(self, businesses: list[Business]):
        self._by_id = {b.id: b for b in businesses}
        self._by_phone = {b.phone_number_id: b for b in businesses}
        self._by_number = {_digits(b.whatsapp_number): b for b in businesses if _digits(b.whatsapp_number)}

    @classmethod
    def from_dir(cls, root: Path) -> "Registry":
        folders = sorted(p for p in root.iterdir() if (p / "config.yaml").exists())
        return cls([load_business(f) for f in folders])

    def get(self, business_id: str) -> Business | None:
        return self._by_id.get(business_id)

    def by_phone_number_id(self, phone_number_id: str) -> Business | None:
        """Meta режимінде — phone_number_id, YCloud режимінде — бизнестің нөмірі (+7700...)."""
        return self._by_phone.get(phone_number_id) or self._by_number.get(_digits(phone_number_id))

    def all(self) -> list[Business]:
        return list(self._by_id.values())
