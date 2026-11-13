from app.rag import KnowledgeBase, split_into_chunks
from tests.conftest import fake_embedder


def test_chunks_respect_size():
    text = "\n\n".join(f"Абзац {i}. " + "сөз " * 40 for i in range(10))
    chunks = split_into_chunks(text, size=500)
    assert len(chunks) > 1
    assert all(len(c) <= 700 for c in chunks)


def test_long_paragraph_is_split():
    chunks = split_into_chunks("Бір сөйлем. " * 200, size=500)
    assert len(chunks) >= 4


def test_businesses_are_isolated(salon, registry):
    kb = KnowledgeBase(embedder=fake_embedder)
    auto = registry.get("tulpar-auto")
    assert kb.index(salon.id, salon.knowledge_text()) > 0
    assert kb.index(auto.id, auto.knowledge_text()) > 0

    salon_hits = kb.search(salon.id, "колодка ауыстыру")
    auto_hits = kb.search(auto.id, "колодка ауыстыру")
    assert all("колодка" not in h.text.lower() for h in salon_hits)
    assert "колодка" in auto_hits[0].text.lower()


def test_search_finds_relevant_part(salon):
    kb = KnowledgeBase(embedder=fake_embedder)
    kb.index(salon.id, salon.knowledge_text())
    assert "маникюр" in kb.search(salon.id, "маникюр бағасы")[0].text.lower()


def test_unknown_business_returns_nothing():
    assert KnowledgeBase(embedder=fake_embedder).search("joq", "сұрақ") == []
