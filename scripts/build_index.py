"""Бизнестердің knowledge.txt файлдарынан білім базасын құру.

  python -m scripts.build_index              # барлық бизнес
  python -m scripts.build_index aru-salon    # тек біреуі

knowledge.txt өзгерген сайын қайта іске қос.
"""

import sys

from app.businesses import Registry
from app.config import get_settings
from app.rag import KnowledgeBase


def main(only: list[str]) -> None:
    settings = get_settings()
    registry = Registry.from_dir(settings.businesses_dir)
    kb = KnowledgeBase(settings.data_dir / "chroma")
    targets = [b for b in registry.all() if not only or b.id in only]
    if not targets:
        raise SystemExit("Бизнес табылмады. businesses/ папкасын тексер.")
    for b in targets:
        if b.mode == "menu":
            print(f"⏭️  {b.id}: базалық бот (mode: menu), білім базасы керек емес")
            continue
        n = kb.index(b.id, b.knowledge_text())
        print(f"✅ {b.id}: {n} бөлік")


if __name__ == "__main__":
    main(sys.argv[1:])
