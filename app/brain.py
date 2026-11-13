"""Боттың "миы": клиент хабарламасына жауап құрастыру.

Реті:
  1. Сәлемдесу / алғыс         → дайын жауап (AI шақырылмайды, тегін)
  2. "Адаммен сөйлескім келеді" → адамға беру
  3. Іздеу (RAG)                → бизнес ақпаратынан ең жақын бөліктер
  4. Ұқсастық шегі              → ештеңе жақын емес: тақырыптан тыс, сыпайы бас тарту (иесі мазаланбайды)
  5. Claude                     → тек табылған ақпарат бойынша жауап
  6. [[OFFTOPIC]] белгісі       → бизнеске қатысы жоқ сұрақ: сыпайы бас тарту (иесі мазаланбайды)
  7. [[HANDOFF]] белгісі        → бизнеске қатысты, бірақ ақпаратта жоқ: нөмір беру + иесіне хабар
  8. [[LEAD]] белгісі           → клиент жазылғысы/сатып алғысы/қосылғысы келеді: жауап + иесіне "өтінім" хабары

Әңгіме жалғасып жатса ("ертең 15:00", "иә, келемін"), қысқа жауап тақырыптан тыс саналмайды.

Дайын мәтіндер клиенттің тілінде (қазақша/орысша) жіберіледі.
"""

import re
from dataclasses import dataclass, field

from app.businesses import Business
from app.rag import Hit, KnowledgeBase
from app.store import Store

HANDOFF_MARK = "[[HANDOFF]]"
OFFTOPIC_MARK = "[[OFFTOPIC]]"
LEAD_MARK = "[[LEAD]]"
MARKS = (HANDOFF_MARK, OFFTOPIC_MARK, LEAD_MARK)
KAZAKH_LETTERS = set("әғқңөұүһі")

HUMAN_WORDS = (
    "адаммен", "операто", "менеджер", "администратор", "әкімші",
    "человек", "живой", "позвоните", "қоңырау шалыңыз",
)
GREETINGS = (
    "сәлем", "салем", "сәлеметсіз", "салеметсиз", "ассалаумағалейкум", "ассалаумағалейкүм",
    "привет", "здравствуйте", "добрый день", "добрый вечер", "қайырлы күн", "қайырлы кеш", "hello",
)
THANKS = ("үлкен рахмет", "көп рахмет", "рахмет", "рақмет", "спасибо", "благодарю", "thanks")
# Сәлемнен/рахметтен кейін келсе де, хабарламаны "тек сәлем" деп санайтын сөздер.
FILLERS = {
    "бе", "ба", "қалайсыз", "қалай", "жағдай", "жағдайыңыз", "сізге", "сіздерге", "үлкен", "көп",
    "апа", "ағай", "как", "дела", "вам", "всем", "большое", "there", "you",
}


@dataclass
class Reply:
    text: str
    handoff: bool = False
    # "greeting", "thanks", "human", "off_topic", "not_found", "llm", "llm_handoff", "lead"
    reason: str = ""
    parts: list[int] = field(default_factory=list)   # жауап қай бөліктерден алынды (лог үшін)
    lead: bool = False        # клиент жазылғысы/сатып алғысы келеді — иесіне "өтінім" хабары


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", " ", text.lower()).strip()


def _is_short_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    """Хабарлама ТЕК сәлем/рахмет болса True.
    "Сәлем, маникюр қанша?" сияқты сұрағы бар хабарлама үшін False."""
    words = _normalize(text).split()
    stems = [w for p in phrases for w in p.split()]

    def is_phrase_word(w: str) -> bool:
        return any(w.startswith(s) for s in stems)   # "сәлеметсіздер" де "сәлем" деп танылады

    return (
        bool(words)
        and any(is_phrase_word(w) for w in words)
        and all(is_phrase_word(w) or w in FILLERS for w in words)
    )


def wants_human(text: str) -> bool:
    norm = text.lower()
    return any(w in norm for w in HUMAN_WORDS)


RUSSIAN_WORDS = {
    "и", "в", "на", "я", "что", "как", "у", "вас", "есть", "ли", "сколько", "можно", "это", "для",
    "по", "с", "а", "мне", "где", "когда", "до", "от", "какой", "какая", "какие", "здравствуйте",
    "привет", "спасибо", "стоит", "нужно", "хочу", "вы", "работаете", "сегодня", "завтра", "или",
}


def detect_lang(text: str) -> str:
    """Қазақ әріптері болса — 'kk'; орысшаға тән сөздер болса — 'ru'; әйтпесе — 'kk'.
    ("Массаж жасайсыздар ма?" сияқты сөйлемде қазақ әрпі жоқ, бірақ ол қазақша.)"""
    low = text.lower()
    if any(ch in KAZAKH_LETTERS for ch in low):
        return "kk"
    if set(re.findall(r"[а-яё]+", low)) & RUSSIAN_WORDS:
        return "ru"
    return "kk"


def off_topic_text(business: Business, lang: str) -> str:
    if lang == "ru":
        return f"Извините, с этим вопросом я помочь не могу 🙏 Этот чат помогает только по вопросам {business.short_name_ru}."
    return f"Кешіріңіз, бұл сұрақ бойынша көмектесе алмаймын 🙏 Бұл чат тек {business.short_name} бойынша көмектеседі."


def not_found_text(business: Business, lang: str) -> str:
    if lang == "ru":
        return f"Извините, точной информации по этому вопросу у меня нет. Свяжитесь с администратором: {business.contact_phone}"
    return f"Кешіріңіз, бұл сұраққа нақты ақпаратым жоқ. Әкімшімен байланысыңыз: {business.contact_phone}"


def handoff_text(business: Business, lang: str) -> str:
    if lang == "ru":
        return f"Хорошо, я передал ваш вопрос администратору, он скоро ответит. Если срочно: {business.contact_phone}"
    return f"Жақсы, сұрағыңызды әкімшіге жібердім, жақын арада жауап береді. Шұғыл болса: {business.contact_phone}"


def thanks_text(lang: str) -> str:
    if lang == "ru":
        return "Спасибо! Если будут ещё вопросы, пишите 🙂"
    return "Рахмет! Тағы сұрағыңыз болса, жаза беріңіз 🙂"


def build_system_prompt(business: Business, hits: list[Hit]) -> str:
    context = "\n\n".join(f"[{h.part}-бөлік]\n{h.text}" for h in hits)
    return f"""Сен {business.name} бизнесінің WhatsApp-тағы клиенттерге көмекшісісің.

Қатаң ережелер:
1. Тек <ақпарат> ішіндегі мәліметке сүйеніп жауап бер. Бағаны, жеңілдікті, уақытты, мекенжайды ойдан шығарма.
2. Сұрақ осы бизнеске қатысты, бірақ <ақпарат> ішінде жауабы жоқ болса немесе сенімсіз болсаң, жауабыңды тек мына белгімен жаз: {HANDOFF_MARK}
3. Сұрақтың осы бизнеске мүлде қатысы жоқ болса (валюта бағамы, жаңалықтар, жалпы білім, үй тапсырмасы, саясат), бір сөйлеммен сыпайы бас тарт, мысалы: "Кешіріңіз, валюта бағамы туралы ақпарат бере алмаймын 🙏 Бұл чат тек {business.short_name} бойынша көмектеседі." Соңына {OFFTOPIC_MARK} белгісін қос.
4. Клиент қай тілде жазса (қазақша немесе орысша), сол тілде жауап бер.
5. Қысқа жаз: 1–4 сөйлем. WhatsApp-қа ыңғайлы: керек болса *қалың* мәтін, тізім.
6. Өзіңді адам етіп көрсетпе. Сұраса, автоматты көмекші екеніңді айт.
7. Клиент жазылғысы, сатып алғысы, қосылғысы немесе тапсырыс бергісі келетінін білдірсе: керекті мәліметті (қандай қызмет, ыңғайлы күн мен уақыт) қысқа сұра. Мәлімет жеткілікті болса немесе клиент әкімшімен байланысқысы келсе, өтінімін әкімшіге жібергеніңді және әкімші растайтынын айт. Жазылуды өзің растама. Осындай әр жауаптың соңына {LEAD_MARK} белгісін қос.
{business.extra_rules}

<ақпарат>
{context}
</ақпарат>"""


def _strip_marks(text: str) -> str:
    for m in MARKS:
        text = text.replace(m, "")
    return text.strip()


def _clean_history(history: list[dict]) -> list[dict]:
    """Claude үшін: user-дан басталып, рөлдер кезектесуі керек."""
    out: list[dict] = []
    for m in history:
        if not out and m["role"] != "user":
            continue
        if out and out[-1]["role"] == m["role"]:
            out[-1] = {"role": m["role"], "content": out[-1]["content"] + "\n" + m["content"]}
        else:
            out.append(dict(m))
    # Соңғысы user болса, оны жаңа хабарламамен біріктірмес үшін алып тастаймыз.
    if out and out[-1]["role"] == "user":
        out.pop()
    return out


class Brain:
    def __init__(self, kb: KnowledgeBase, llm, model: str, max_distance: float, store: Store):
        self.kb = kb
        self.llm = llm               # anthropic.Anthropic() немесе тесттегі жалған клиент
        self.model = model
        self.max_distance = max_distance
        self.store = store

    def answer(self, business: Business, user: str, text: str) -> Reply:
        reply = self._decide(business, user, text)
        self.store.add(business.id, user, "user", text)
        self.store.add(business.id, user, "assistant", reply.text)
        return reply

    def _decide(self, business: Business, user: str, text: str) -> Reply:
        text = text.strip()
        if not text:
            return Reply(business.greeting, reason="greeting")
        lang = detect_lang(text)

        if _is_short_phrase(text, GREETINGS):
            return Reply(business.greeting, reason="greeting")
        if _is_short_phrase(text, THANKS):
            return Reply(thanks_text(lang), reason="thanks")
        if wants_human(text):
            return Reply(handoff_text(business, lang), handoff=True, reason="human")

        history = _clean_history(self.store.history(business.id, user, limit=6))
        found = self.kb.search(business.id, text, k=3)
        hits = [h for h in found if h.distance <= self.max_distance]
        if not hits:
            if not history:
                # Жаңа әңгіме және бизнес ақпаратына мүлде жақын емес — тақырыптан тыс. Иесін мазаламаймыз.
                return Reply(off_topic_text(business, lang), reason="off_topic")
            # Әңгіме жалғасып жатыр ("ертең 15:00"): шешімді Claude-қа қалдырамыз
            hits = found
        response = self.llm.messages.create(
            model=self.model,
            max_tokens=400,
            # temperature/top_p/top_k ЖІБЕРМЕ: API 2026-10-21 нұсқасынан бастап олар 400 қатесін береді.
            system=build_system_prompt(business, hits),
            messages=history + [{"role": "user", "content": text}],
        )
        answer = "".join(b.text for b in response.content if getattr(b, "type", "text") == "text").strip()
        parts = [h.part for h in hits]

        clean = _strip_marks(answer)
        if OFFTOPIC_MARK in answer:
            return Reply(clean or off_topic_text(business, lang), reason="off_topic", parts=parts)
        if not answer or (HANDOFF_MARK in answer and LEAD_MARK not in answer):
            return Reply(not_found_text(business, lang), handoff=True, reason="llm_handoff", parts=parts)
        if LEAD_MARK in answer:
            return Reply(clean or handoff_text(business, lang), reason="lead", parts=parts, lead=True)
        return Reply(clean, reason="llm", parts=parts)
