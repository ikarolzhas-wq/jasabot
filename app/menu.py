"""Базалық бот: мәзір мен батырмалар, AI-сыз (Claude шығыны жоқ).

Бизнестің config.yaml ішінде `mode: menu` болса, бот осы режимде жұмыс істейді.
Мәзір businesses/<id>/menu.yaml файлында сипатталады:

  welcome: "Сәлеметсіз бе! ..."          # жаңа клиентке бірінші хабар
  menu:
    text: "Таңдаңыз 👇"
    items:
      - title: "📋 Бағалар"              # батырма атауы (≤ 20 таңба)
        text: "Қай қызмет?"             # ішкі мәзір мәтіні
        items:
          - title: "Тырнақ"
            answer: "Маникюр — 4 000 ₸"   # дайын жауап
            also: ["✍️ Жазылу"]          # жауаптан кейін қосымша батырма
      - title: "✍️ Жазылу"
        action: form                    # сұрақ-жауап формасы → иесіне өтінім
        form: booking
      - title: "📞 Әкімші"
        action: human                   # адамға беру
  forms:
    booking:
      steps:
        - ask: "Қай қызмет?"
          label: "Қызмет"
          options: ["Шаш", "Тырнақ"]
        - ask: "Қай уақытта? Мысалы: ертең 15:00"
          label: "Уақыт"
      done: "Рахмет! Өтініміңіз қабылданды ✅"

Клиент батырманы басады, нөмірін жазады немесе атауын жазады — бәрі түсініледі.
"мәзір", "меню" немесе "0" деп жазса — басты мәзірге оралады.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

TITLE_MAX = 20          # WhatsApp батырма атауының шегі
MAX_OPTIONS = 10        # WhatsApp тізіміндегі жолдар шегі
HOME = "🏠 Басты мәзір"
BACK = "⬅️ Артқа"
HOME_WORDS = {"мәзір", "мазир", "меню", "menu", "0", "басты мәзір", "start", "старт", "главное меню"}

# Екі тіл: menu.yaml ішінде `ru:` бөлімі болса, бот орысша да жауап береді.
LABELS = {
    "kk": {"home": HOME, "back": BACK, "switch": "🇷🇺 Русский"},
    "ru": {"home": "🏠 Главное меню", "back": "⬅️ Назад", "switch": "🇰🇿 Қазақша"},
}
LANG_WORDS = {
    "ru": {"русский", "рус", "орысша", "на русском", "по русски", "ru", "russian"},
    "kk": {"қазақша", "казакша", "казахский", "на казахском", "kz", "kk", "kazakh"},
}
KK_LETTERS = set("әғқңөұүһі")
KK_WORDS = {"салем", "салеметсиз", "сәлеметсіз", "керек", "канша", "қанша", "бар", "жок", "жоқ", "иа", "ия",
            "рахмет", "калай", "қалай", "кайырлы", "қайырлы", "ассалаумағалейкум", "ассалаумагалейкум"}


def detect_lang(text: str) -> str:
    """Клиенттің бірінші хабарынан тілді болжау. Күмәнді болса — қазақша."""
    t = (text or "").lower()
    if any(ch in KK_LETTERS for ch in t):
        return "kk"
    words = _norm(t).split()
    if any(w in KK_WORDS or w.startswith(("салем", "ассалау")) for w in words):
        return "kk"
    if any("а" <= ch <= "я" or ch == "ё" for ch in t):
        return "ru"
    return "kk"
GREETING_STEMS = ("сәлем", "салем", "ассалау", "привет", "здравств", "добрый", "қайырлы", "hello", "hi")


def _is_greeting(text: str) -> bool:
    words = _norm(text).split()
    return 0 < len(words) <= 3 and any(words[0].startswith(g) for g in GREETING_STEMS)


class MenuError(ValueError):
    pass


@dataclass
class MenuReply:
    text: str
    options: list[str] = field(default_factory=list)
    lead: bool = False
    handoff: bool = False
    reason: str = "menu"      # welcome, menu, answer, fallback, form, lead, human
    summary: str = ""         # форма жауаптары (иесіне жіберіледі)


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (s or "").lower()).split())


def render_text(reply: MenuReply) -> str:
    """Батырмасыз арналар үшін (терминал, лог): опцияларды нөмірлеп жазады."""
    if not reply.options:
        return reply.text
    lines = [f"{i}. {o}" for i, o in enumerate(reply.options, 1)]
    return reply.text + "\n\n" + "\n".join(lines)


class Menu:
    def __init__(self, data: dict, source: str = "menu.yaml", lang: str = "kk"):
        if not isinstance(data, dict) or "menu" not in data:
            raise MenuError(f"{source}: 'menu' бөлімі жоқ")
        self.source = source
        self.lang = lang
        self.home = LABELS[lang]["home"]
        self.back = LABELS[lang]["back"]
        self.variants: dict[str, "Menu"] = {lang: self}
        self.welcome = str(data.get("welcome", "")).strip()
        self.root = data["menu"]
        self.forms = data.get("forms") or {}
        self.human_text = str(data.get("human_text") or
                              "Жақсы, сұрағыңызды әкімшіге жібердім, жақын арада жауап береді 🙏").strip()
        self.fallback = str(data.get("fallback") or
                            "Кешіріңіз, түсінбедім 🙏 Төмендегі мәзірден таңдаңыз 👇").strip()
        self._by_title: dict[str, list[int]] = {}
        self._index(self.root, [])
        self._validate()

    @classmethod
    def load(cls, path: Path) -> "Menu":
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        main = cls(data, source=str(path))
        if isinstance(data, dict) and data.get("ru"):
            ru = cls(data["ru"], source=f"{path} (ru)", lang="ru")
            main.variants["ru"] = ru
            ru.variants = main.variants
        return main

    def for_lang(self, lang: str) -> "Menu":
        return self.variants.get(lang) or self

    @property
    def switch(self) -> str | None:
        """Тілді ауыстыру батырмасы (екі тіл болса ғана)."""
        return LABELS[self.lang]["switch"] if len(self.variants) > 1 else None

    # ---------- құрылым ----------

    def _index(self, node: dict, path: list[int]) -> None:
        for i, item in enumerate(node.get("items") or []):
            key = _norm(item.get("title", ""))
            if key in self._by_title:
                raise MenuError(f"{self.source}: '{item.get('title')}' атауы екі рет кездеседі")
            self._by_title[key] = path + [i]
            if item.get("items"):
                self._index(item, path + [i])

    def _validate(self) -> None:
        problems = []

        def check_titles(titles, where):
            for t in titles:
                if not t or len(t) > TITLE_MAX:
                    problems.append(f"{where}: '{t}' атауы бос немесе {TITLE_MAX} таңбадан ұзын")
            if len(titles) + 1 > MAX_OPTIONS:
                problems.append(f"{where}: {MAX_OPTIONS - 1}-дан көп нұсқа болмауы керек")

        def walk(node, where):
            items = node.get("items") or []
            if not items:
                problems.append(f"{where}: 'items' бос")
            check_titles([it.get("title", "") for it in items], where)
            for it in items:
                w = f"{where} → {it.get('title')}"
                if it.get("items"):
                    walk(it, w)
                elif it.get("action") == "form":
                    if it.get("form") not in self.forms:
                        problems.append(f"{w}: '{it.get('form')}' формасы forms бөлімінде жоқ")
                elif it.get("action") == "human":
                    pass
                elif not it.get("answer"):
                    problems.append(f"{w}: 'answer', 'items' немесе 'action' керек")
                for t in it.get("also") or []:
                    if _norm(t) not in self._by_title:
                        problems.append(f"{w}: also ішіндегі '{t}' мәзірде жоқ")

        walk(self.root, "menu")
        for name, form in self.forms.items():
            steps = form.get("steps") or []
            if not steps:
                problems.append(f"forms.{name}: 'steps' бос")
            for s in steps:
                if not s.get("ask"):
                    problems.append(f"forms.{name}: әр қадамда 'ask' болуы керек")
                check_titles(s.get("options") or [], f"forms.{name}")
        if problems:
            raise MenuError(f"{self.source} қателері:\n- " + "\n- ".join(problems))

    def node(self, path: list[int]) -> dict:
        node = self.root
        for i in path:
            node = (node.get("items") or [])[i]
        return node

    def options(self, path: list[int]) -> list[str]:
        titles = [it["title"] for it in self.node(path).get("items") or []]
        if path:
            return titles + [self.back]
        return titles + ([self.switch] if self.switch else [])

    def all_titles(self) -> list[str]:
        out = []

        def walk(node):
            for it in node.get("items") or []:
                out.append(it["title"])
                if it.get("items"):
                    walk(it)
        walk(self.root)
        return out

    def find(self, text: str, options: list[str], allow_digits: bool = True) -> str | None:
        """Клиенттің жазғанын опциямен сәйкестендіру: нөмір, толық атау немесе атаудың басы."""
        t = _norm(text)
        if not t:
            return None
        if allow_digits and t.isdigit() and 1 <= int(t) <= len(options):
            return options[int(t) - 1]
        for o in options:
            if _norm(o) == t:
                return o
        for o in options:
            n = _norm(o)
            if n and (n.startswith(t) or t.startswith(n)) and len(t) >= 3:
                return o
        return None

    def path_of(self, title: str) -> list[int] | None:
        return self._by_title.get(_norm(title))


class MenuBot:
    """Мәзір бойынша жауап беру. Әр клиенттің күйі Store-да сақталады:
    shown — клиент соңғы көрген батырмалар (нөмірмен жазса, осыған сәйкестендіріледі),
    back  — "Артқа" басылса қай мәзір көрсетіледі,
    form  — толтырылып жатқан форма (қадамы мен жауаптары)."""

    def __init__(self, store):
        self.store = store
        self._cache: dict[str, tuple[float, Menu]] = {}

    def menu_for(self, business) -> Menu:
        path = business.knowledge_path.parent / "menu.yaml"
        if not path.exists():
            raise MenuError(f"{business.id}: menu.yaml табылмады ({path})")
        mtime = path.stat().st_mtime
        cached = self._cache.get(business.id)
        if not cached or cached[0] != mtime:          # файл өзгерсе — қайта оқимыз
            self._cache[business.id] = (mtime, Menu.load(path))
        return self._cache[business.id][1]

    def answer(self, business, user: str, text: str) -> MenuReply:
        reply = self._decide(business, user, text or "")
        self.store.add(business.id, user, "user", text or "[медиа хабарлама]")
        self.store.add(business.id, user, "assistant", render_text(reply))
        if reply.summary:
            self.store.add(business.id, user, "assistant", "📝 Өтінім:\n" + reply.summary)
        return reply

    # ---------- шешім ----------

    def _reply(self, m: Menu, business, user, reply: MenuReply, back, form=None) -> MenuReply:
        state = {"shown": reply.options, "back": back, "lang": m.lang}
        if form:
            state["form"] = form
        self.store.set_state(business.id, user, state)
        return reply

    def _show(self, m: Menu, business, user, path: list[int], intro: str = "", reason: str = "menu") -> MenuReply:
        default = "Выберите 👇" if m.lang == "ru" else "Таңдаңыз 👇"
        node_text = (m.node(path).get("text") or default).strip()
        text = f"{intro}\n\n{node_text}" if intro else node_text
        return self._reply(m, business, user, MenuReply(text.strip(), m.options(path), reason=reason),
                           back=path[:-1] if path else None)

    @staticmethod
    def _lang_command(text: str) -> str | None:
        t = _norm(text)
        for lang, words in LANG_WORDS.items():
            if t in words:
                return lang
        return None

    def _decide(self, business, user: str, text: str) -> MenuReply:
        base = self.menu_for(business)
        state = self.store.get_state(business.id, user)

        if state is None:                                   # жаңа әңгіме: тілді бірінші хабардан анықтаймыз
            m = base.for_lang(detect_lang(text))
            return self._show(m, business, user, [], intro=m.welcome, reason="welcome")

        m = base.for_lang(state.get("lang") or "kk")
        shown = state.get("shown") or []

        # тілді ауыстыру: батырма немесе "русский" / "қазақша" деп жазса
        wanted = self._lang_command(text)
        if m.switch and (m.find(text, shown) == m.switch or (wanted and wanted != m.lang)):
            other = next(v for k, v in base.variants.items() if k != m.lang)
            return self._show(other, business, user, [], intro=other.welcome, reason="welcome")

        if _is_greeting(text) and not state.get("form"):
            return self._show(m, business, user, [], intro=m.welcome, reason="welcome")
        if _norm(text) in HOME_WORDS or m.find(text, shown) == m.home:
            return self._show(m, business, user, [])

        if state.get("form"):
            return self._form_step(m, business, user, state, text)

        choice = m.find(text, shown)
        if choice == m.back and state.get("back") is not None:
            return self._show(m, business, user, state["back"])
        target = m.path_of(choice) if choice else None
        if target is None:
            # экрандағы батырмаларда жоқ — бүкіл мәзірден атауы бойынша іздейміз (нөмірсіз)
            alt = m.find(text, m.all_titles(), allow_digits=False)
            target = m.path_of(alt) if alt else None
        if target is None:
            reply = MenuReply(f"{m.fallback}", shown or m.options([]), reason="fallback")
            return self._reply(m, business, user, reply, back=state.get("back"))
        return self._open(m, business, user, target)

    def _open(self, m: Menu, business, user: str, path: list[int]) -> MenuReply:
        item = m.node(path)
        parent = path[:-1]
        if item.get("items"):
            return self._show(m, business, user, path)
        if item.get("action") == "form":
            form = {"name": item["form"], "step": 0, "answers": []}
            return self._reply(m, business, user, self._ask(m, item["form"], 0), back=None, form=form)
        if item.get("action") == "human":
            return self._reply(m, business, user, MenuReply(m.human_text, [m.home], handoff=True, reason="human"),
                               back=None)
        opts = list(dict.fromkeys(list(item.get("also") or []) + ([m.back] if parent else []) + [m.home]))
        return self._reply(m, business, user, MenuReply(str(item["answer"]).strip(), opts, reason="answer"),
                           back=parent if parent else None)

    @staticmethod
    def _ask(m: Menu, form: str, step: int) -> MenuReply:
        s = m.forms[form]["steps"][step]
        return MenuReply(str(s["ask"]).strip(), list(s.get("options") or []) + [m.home], reason="form")

    def _form_step(self, m: Menu, business, user: str, state: dict, text: str) -> MenuReply:
        f = state["form"]
        steps = m.forms[f["name"]]["steps"]
        i = f.get("step", 0)
        value = m.find(text, steps[i].get("options") or []) or text.strip()
        if not value:                                     # бос хабар (сурет т.б.) — сұрақты қайталаймыз
            return self._reply(m, business, user, self._ask(m, f["name"], i), back=None, form=f)
        answers = f.get("answers", []) + [value]
        if i + 1 < len(steps):
            nf = {**f, "step": i + 1, "answers": answers}
            return self._reply(m, business, user, self._ask(m, f["name"], i + 1), back=None, form=nf)
        summary = "\n".join(f"{s.get('label') or s['ask']}: {a}" for s, a in zip(steps, answers))
        if m.lang != "kk":
            summary = f"Тілі: {m.lang}\n" + summary
        default_done = "Спасибо! Заявка принята ✅" if m.lang == "ru" else "Рахмет! Өтініміңіз қабылданды ✅"
        done = str(m.forms[f["name"]].get("done") or default_done).strip()
        return self._reply(m, business, user, MenuReply(done, [m.home], lead=True, reason="lead", summary=summary),
                           back=None)
