"""Барлық баптаулар .env файлынан оқылады."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    # "meta" — тікелей Meta Cloud API (тест нөмірі), "ycloud" — YCloud серіктесі арқылы
    provider: str
    ycloud_api_key: str
    ycloud_webhook_secret: str
    ycloud_base_url: str
    owner_pause_hours: float   # иесі клиентке өзі жазса, бот сол чатта қанша сағат үндемейді

    # Meta / WhatsApp
    verify_token: str          # webhook тексеру үшін өзің ойлап табатын құпия сөз
    app_secret: str            # Meta App → Settings → Basic → App secret
    access_token: str          # WhatsApp API токені
    graph_version: str         # Graph API нұсқасы, мысалы v23.0
    graph_base_url: str        # әдетте https://graph.facebook.com (тестте жергілікті сервер)

    # AI
    anthropic_api_key: str
    model: str
    max_distance: float        # ұқсастық шегі: одан алыс болса — "ақпарат жоқ"

    # Қалталар
    businesses_dir: Path
    data_dir: Path

    # Meta тест нөмірі біреу ғана болса: белгісіз phone_number_id-ге осы бизнес жауап береді
    default_business_id: str

    # Міндетті емес: адамға беру кезінде иесіне Telegram хабарлама
    telegram_bot_token: str
    telegram_chat_id: str      # ортақ чат (бизнесте өз telegram_chat_id болмаса)
    telegram_api_base: str

    # Клиенттердің өз YCloud аккаунттары: {"aru": ("API_KEY", "WEBHOOK_SECRET")}.
    # .env ішінде: YCLOUD_API_KEY_ARU=... және YCLOUD_WEBHOOK_SECRET_ARU=...
    ycloud_accounts: dict = field(default_factory=dict)

    def ycloud_all(self) -> dict[str, tuple[str, str]]:
        """Барлық YCloud аккаунттары. "default" — YCLOUD_API_KEY (сенің өз аккаунтың)."""
        accounts = {"default": (self.ycloud_api_key, self.ycloud_webhook_secret)}
        accounts.update(self.ycloud_accounts)
        return accounts


def account_name(name: str) -> str:
    """Аккаунт атауын бір түрге келтіру: "Aru-Salon" → "aru_salon" (.env-те YCLOUD_API_KEY_ARU_SALON)."""
    return "".join(ch if ch.isalnum() else "_" for ch in name.strip().lower())


def ycloud_accounts_from_env(env=None) -> dict[str, tuple[str, str]]:
    env = os.environ if env is None else env
    prefix = "YCLOUD_API_KEY_"
    out = {}
    for key, value in env.items():
        if key.upper().startswith(prefix) and value.strip():
            name = account_name(key[len(prefix):])
            secret = env.get(f"YCLOUD_WEBHOOK_SECRET_{name.upper()}", "")
            out[name] = (value.strip(), secret.strip())
    return out


def get_settings() -> Settings:
    return Settings(
        provider=os.getenv("PROVIDER", "meta").strip().lower(),
        ycloud_api_key=os.getenv("YCLOUD_API_KEY", ""),
        ycloud_webhook_secret=os.getenv("YCLOUD_WEBHOOK_SECRET", ""),
        ycloud_base_url=os.getenv("YCLOUD_API_BASE", "https://api.ycloud.com/v2"),
        owner_pause_hours=float(os.getenv("OWNER_PAUSE_HOURS", "6")),
        verify_token=os.getenv("WHATSAPP_VERIFY_TOKEN", ""),
        app_secret=os.getenv("WHATSAPP_APP_SECRET", ""),
        access_token=os.getenv("WHATSAPP_ACCESS_TOKEN", ""),
        graph_version=os.getenv("GRAPH_API_VERSION", "v23.0"),
        graph_base_url=os.getenv("GRAPH_API_BASE", "https://graph.facebook.com"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        model=os.getenv("CLAUDE_MODEL", "claude-haiku-5-5"),
        max_distance=float(os.getenv("MAX_DISTANCE", "0.24")),
        businesses_dir=Path(os.getenv("BUSINESSES_DIR", ROOT / "businesses")),
        data_dir=Path(os.getenv("DATA_DIR", ROOT / "data")),
        default_business_id=os.getenv("DEFAULT_BUSINESS_ID", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        telegram_api_base=os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org"),
        ycloud_accounts=ycloud_accounts_from_env(),
    )
