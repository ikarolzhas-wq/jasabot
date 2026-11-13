"""site/zhas-bot.html → site/deploy/index.html (Cloudflare Pages-ке жүктейтін толық бет).

  python -m scripts.build_site
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEAD = """<!doctype html>
<html lang="kk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<link rel="icon" type="image/png" href="jasabot-logo.png">
<link rel="apple-touch-icon" href="jasabot-logo.png">
<meta property="og:title" content="JasaBot">
<meta property="og:description" content="Қазақстандағы бизнеске WhatsApp бот жасаймыз. Орнату тегін, 7 күн тегін сынақ.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://jasabot.pages.dev/">
<meta property="og:image" content="https://jasabot.pages.dev/jasabot-logo.png">
<style>
:root { color-scheme: light; padding-top: env(safe-area-inset-top, 0px); padding-bottom: env(safe-area-inset-bottom, 0px); }
body { margin: 0; font: 14px system-ui, sans-serif; }
img { max-width: 100%; }
[hidden] { display: none !important; }
</style>
"""


def main() -> None:
    src = (ROOT / "site" / "zhas-bot.html").read_text(encoding="utf-8")
    i = src.index('<header class="top">')
    head = src[:i].replace('<meta name="claude:viewport-fit-sides" content="cover">\n', "")
    out = HEAD + head + "</head>\n<body>\n" + src[i:] + "\n</body>\n</html>\n"
    dest = ROOT / "site" / "deploy" / "index.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(out, encoding="utf-8")
    print(f"Дайын: {dest}")


if __name__ == "__main__":
    main()
