from app.brain import HANDOFF_MARK, Brain
from app.rag import Hit
from app.store import Store
from tests.conftest import FakeLLM


class FakeKB:
    def __init__(self, hits):
        self.hits = hits
        self.queries = []

    def search(self, business_id, question, k=3):
        self.queries.append(question)
        return self.hits


NEAR = [Hit(text="Классикалық маникюр 4000 теңге.", part=4, distance=0.10)]
FAR = [Hit(text="Жұмыс уақыты 09:00-20:00.", part=2, distance=0.60)]


def make(hits, answer="Маникюр 4000 теңге тұрады."):
    llm = FakeLLM(answer)
    return Brain(FakeKB(hits), llm, "test-model", 0.25, Store()), llm


def test_greeting_does_not_call_ai(salon):
    brain, llm = make(NEAR)
    reply = brain.answer(salon, "u1", "Сәлеметсіз бе!")
    assert reply.reason == "greeting"
    assert reply.text == salon.greeting
    assert llm.calls == []


def test_greeting_with_question_goes_to_ai(salon):
    brain, llm = make(NEAR)
    reply = brain.answer(salon, "u1", "Сәлем, маникюр қанша?")
    assert reply.reason == "llm"
    assert len(llm.calls) == 1


def test_thanks(salon):
    brain, llm = make(NEAR)
    assert brain.answer(salon, "u1", "Үлкен рахмет!").reason == "thanks"
    assert llm.calls == []


def test_asks_for_human(salon):
    brain, llm = make(NEAR)
    reply = brain.answer(salon, "u1", "Администратормен сөйлескім келеді")
    assert reply.handoff and reply.reason == "human"
    assert salon.contact_phone in reply.text
    assert llm.calls == []


def test_far_context_is_off_topic(salon):
    """Ештеңе жақын емес — AI шақырылмайды, сыпайы бас тарту, иесі мазаланбайды."""
    brain, llm = make(FAR)
    reply = brain.answer(salon, "u1", "Қазақстанның астанасы қай қала?")
    assert reply.reason == "off_topic" and not reply.handoff
    assert "Бұл чат тек «Ару» сұлулық салоны бойынша көмектеседі" in reply.text
    assert llm.calls == []


def test_off_topic_in_russian(salon):
    brain, _ = make(FAR)
    reply = brain.answer(salon, "u1", "Какой курс доллара сегодня?")
    assert reply.reason == "off_topic"
    assert "салона красоты «Ару»" in reply.text


def test_ai_off_topic_mark_uses_ai_wording(salon):
    """Claude бас тартуды өзі жазады (тақырыпты атап), белгі клиентке көрінбейді."""
    text = "Кешіріңіз, валюта бағамы туралы ақпарат бере алмаймын 🙏 Бұл чат тек «Ару» сұлулық салоны бойынша көмектеседі. [[OFFTOPIC]]"
    brain, _ = make(NEAR, answer=text)
    reply = brain.answer(salon, "u1", "Доллар бағамы қандай?")
    assert reply.reason == "off_topic" and not reply.handoff
    assert "валюта бағамы" in reply.text and "[[" not in reply.text


def test_not_found_text_follows_client_language(salon):
    brain, _ = make(NEAR, answer=HANDOFF_MARK)
    ru = brain.answer(salon, "u1", "Есть ли у вас массаж?")
    kk = brain.answer(salon, "u2", "Массаж жасайсыздар ма?")
    assert ru.text.startswith("Извините") and kk.text.startswith("Кешіріңіз")
    assert ru.handoff and kk.handoff


def test_answer_uses_only_found_context(salon):
    brain, llm = make(NEAR)
    reply = brain.answer(salon, "u1", "Маникюр қанша тұрады?")
    assert reply.reason == "llm"
    assert reply.parts == [4]
    call = llm.calls[0]
    assert "Классикалық маникюр 4000 теңге." in call["system"]
    assert salon.name in call["system"]


def test_no_removed_sampling_params(salon):
    """Anthropic API 2026-10-21: temperature/top_p/top_k бар сұраныс 400 қатесін береді."""
    brain, llm = make(NEAR)
    brain.answer(salon, "u1", "Маникюр қанша тұрады?")
    assert not {"temperature", "top_p", "top_k"} & set(llm.calls[0])


def test_ai_handoff_mark(salon):
    brain, _ = make(NEAR, answer=HANDOFF_MARK)
    reply = brain.answer(salon, "u1", "Ботокс жасайсыздар ма?")
    assert reply.handoff and reply.reason == "llm_handoff"
    assert HANDOFF_MARK not in reply.text


def test_history_is_sent_and_alternates(salon):
    brain, llm = make(NEAR)
    brain.answer(salon, "u1", "Маникюр қанша?")
    brain.answer(salon, "u1", "Ал педикюр ше?")
    messages = llm.calls[-1]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[-1]["content"] == "Ал педикюр ше?"


def test_history_is_per_user(salon):
    brain, llm = make(NEAR)
    brain.answer(salon, "u1", "Маникюр қанша?")
    brain.answer(salon, "u2", "Педикюр қанша?")
    assert len(llm.calls[-1]["messages"]) == 1


def test_lead_mark(salon):
    text = "Жақсы! Ертең 15:00-ге маникюрге өтініміңізді әкімшіге жібердім, ол растайды. [[LEAD]]"
    brain, _ = make(NEAR, answer=text)
    reply = brain.answer(salon, "u1", "Ертең 15:00-ге маникюрге жазылғым келеді")
    assert reply.lead and reply.reason == "lead" and not reply.handoff
    assert "[[" not in reply.text and "әкімшіге жібердім" in reply.text


def test_follow_up_is_not_off_topic(salon):
    """Әңгіме ішіндегі қысқа жауап ("ертең 15:00") бизнес ақпаратына ұқсамаса да, Claude-қа барады."""
    class StepKB:
        def __init__(self):
            self.n = 0
        def search(self, business_id, question, k=3):
            self.n += 1
            return NEAR if self.n == 1 else FAR
    llm = FakeLLM("Қай уақыт ыңғайлы? [[LEAD]]")
    brain = Brain(StepKB(), llm, "m", 0.25, Store())
    brain.answer(salon, "u1", "Маникюрге жазылғым келеді")
    reply = brain.answer(salon, "u1", "ертең 15:00")
    assert reply.reason == "lead" and len(llm.calls) == 2


def test_far_first_message_still_off_topic(salon):
    brain, llm = make(FAR)
    assert brain.answer(salon, "new-user", "ертең 15:00").reason == "off_topic"
    assert llm.calls == []


def test_lead_prompt_rule_present(salon):
    brain, llm = make(NEAR)
    brain.answer(salon, "u1", "Маникюр қанша?")
    assert "[[LEAD]]" in llm.calls[0]["system"]
