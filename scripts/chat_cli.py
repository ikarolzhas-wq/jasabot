"""WhatsApp-сыз, терминалда ботпен сөйлесу. Демо мен баптау үшін ең ыңғайлы жол.

  python -m scripts.chat_cli aru-salon
  python -m scripts.chat_cli tulpar-auto --debug   # табылған бөліктер мен қашықтықты көрсетеді

Алдымен: python -m scripts.build_index
"""

import argparse

import anthropic

from app.brain import Brain
from app.menu import MenuBot, render_text
from app.businesses import Registry
from app.config import get_settings
from app.rag import KnowledgeBase
from app.store import Store


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("business_id")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    business = Registry.from_dir(settings.businesses_dir).get(args.business_id)
    if business is None:
        raise SystemExit(f"'{args.business_id}' табылмады")

    if business.mode == "menu":
        bot = MenuBot(Store(":memory:"))
        print(f"{business.name} — базалық бот (нөмір немесе атау жаз; шығу: Ctrl+C)\n")
        try:
            text = input("Сен (мысалы: Сәлем): ")
            while True:
                r = bot.answer(business, "cli-user", text)
                tags = (" [ӨТІНІМ → иесіне]" if r.lead else "") + (" [АДАМҒА БЕРІЛДІ]" if r.handoff else "")
                print(f"Бот ({r.reason}){tags}:\n{render_text(r)}\n")
                if r.summary:
                    print(f"   📝 Иесіне кететін өтінім:\n   " + r.summary.replace("\n", "\n   ") + "\n")
                text = input("Сен: ")
        except (KeyboardInterrupt, EOFError):
            print("\nСау бол!")
        return

    kb = KnowledgeBase(settings.data_dir / "chroma")
    brain = Brain(
        kb=kb,
        llm=anthropic.Anthropic(api_key=settings.anthropic_api_key),
        model=settings.model,
        max_distance=settings.max_distance,
        store=Store(":memory:"),
    )

    print(f"{business.name} — сөйлесу басталды (шығу: Ctrl+C)\n")
    try:
        while True:
            text = input("Сен: ").strip()
            if not text:
                continue
            if args.debug:
                for h in kb.search(business.id, text, k=3):
                    mark = "✓" if h.distance <= settings.max_distance else "✗"
                    print(f"   {mark} {h.distance:.3f} [{h.part}] {h.text[:60]!r}")
            reply = brain.answer(business, "cli-user", text)
            tag = " [АДАМҒА БЕРІЛДІ]" if reply.handoff else ""
            print(f"Бот ({reply.reason}){tag}: {reply.text}\n")
    except (KeyboardInterrupt, EOFError):
        print("\nСау бол!")


if __name__ == "__main__":
    main()
