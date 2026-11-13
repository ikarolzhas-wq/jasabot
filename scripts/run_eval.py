"""Боттың сапасын өлшеу: eval/<id>.yaml сұрақтары бойынша дәлдік есебі.

  python -m scripts.run_eval aru-salon                   # толық тест (Claude API керек)
  python -m scripts.run_eval aru-salon --retrieval-only  # тек іздеу, API-сыз: MAX_DISTANCE таңдау үшін

Нәтиже экранда шығады және data/eval_<id>.csv файлына жазылады —
оны клиентке «дәлдік есебі» ретінде көрсетуге болады.
"""

import argparse
import csv

import yaml

from app.brain import Brain
from app.businesses import Registry
from app.config import ROOT, get_settings
from app.rag import KnowledgeBase
from app.store import Store

# Бот жауап бермей тоқтаған жағдайлар (ойдан шығарған жоқ)
REFUSALS = {"llm_handoff", "not_found", "off_topic", "human"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("business_id")
    parser.add_argument("--retrieval-only", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    business = Registry.from_dir(settings.businesses_dir).get(args.business_id)
    if business is None:
        raise SystemExit(f"'{args.business_id}' табылмады")
    if business.mode == "menu":
        raise SystemExit(f"{business.id} базалық режимде (mode: menu). Сапа тесті AI бот үшін; "
                         f"мәзірді тексеру үшін: python -m scripts.chat_cli {business.id}")
    cases = yaml.safe_load((ROOT / "eval" / f"{business.id}.yaml").read_text(encoding="utf-8"))["cases"]

    kb = KnowledgeBase(settings.data_dir / "chroma")
    kb.index(business.id, business.knowledge_text())   # тест әрқашан соңғы knowledge.txt бойынша

    # 1) Іздеу: әр сұраққа ең жақын бөліктің қашықтығы
    best = {c["q"]: (kb.search(business.id, c["q"], k=1) or [None])[0] for c in cases}
    ans_d = [best[c["q"]].distance for c in cases if c["expect"] in ("answer", "lead") and best[c["q"]]]
    off_d = [best[c["q"]].distance for c in cases if c["expect"] in ("handoff", "off_topic") and best[c["q"]]]
    print(f"\nІздеу қашықтығы (аз = жақын). Қазіргі шек MAX_DISTANCE={settings.max_distance}")
    print(f"  Жауабы бар сұрақтар:  ең үлкені {max(ans_d):.3f}")
    print(f"  Жауабы жоқ сұрақтар:  ең кішісі {min(off_d):.3f}")
    if max(ans_d) < min(off_d):
        print(f"  ✅ Ұсынылатын шек: {(max(ans_d) + min(off_d)) / 2:.3f}")
    else:
        print("  ⚠️ Топтар араласып кетті: шек барлығын бөле алмайды, қалғанын Claude-тың [[HANDOFF]] ережесі шешеді.")

    if args.retrieval_only:
        for c in cases:
            h = best[c["q"]]
            print(f"  {h.distance:.3f}  {c['expect']:<9}  {c['q']}")
        return

    # 2) Толық тест: Claude жауабы
    import anthropic

    brain = Brain(
        kb=kb,
        llm=anthropic.Anthropic(api_key=settings.anthropic_api_key),
        model=settings.model,
        max_distance=settings.max_distance,
        store=Store(":memory:"),
    )
    rows, passed = [], 0
    for i, c in enumerate(cases):
        reply = brain.answer(business, f"eval-{i}", c["q"])   # әр сұрақ — жаңа клиент
        if c["expect"] == "lead":
            ok = reply.lead                           # иесіне "ЖАҢА ӨТІНІМ" кетуі керек
        elif c["expect"] == "off_topic":
            ok = reply.reason == "off_topic"          # сыпайы бас тарту, иесі мазаланбайды
        elif c["expect"] == "handoff":
            ok = reply.reason in REFUSALS             # ең бастысы — ойдан шығармау
        else:
            ok = (not reply.handoff) and any(s.lower() in reply.text.lower() for s in c.get("must_contain", []))
        passed += ok
        rows.append({
            "сұрақ": c["q"], "күтілген": c["expect"], "нәтиже": "✅" if ok else "❌",
            "себеп": reply.reason, "қашықтық": f"{best[c['q']].distance:.3f}", "жауап": reply.text,
        })
        print(f"{'✅' if ok else '❌'} [{reply.reason}] {c['q']}\n    → {reply.text[:150]}")

    out = settings.data_dir / f"eval_{business.id}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as f:   # utf-8-sig: Excel қазақша әріптерді дұрыс ашады
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nДӘЛДІК: {passed}/{len(cases)} = {passed / len(cases):.0%}   (есеп: {out})")


if __name__ == "__main__":
    main()
