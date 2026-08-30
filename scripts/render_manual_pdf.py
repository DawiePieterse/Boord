#!/usr/bin/env python3
"""Render a Markdown file to PDF, matching MANUAL.md's existing style
(navy headings, styled code/tables, callout blockquotes).

Requires: weasyprint (e.g. `brew install weasyprint` on Mac) and the
`markdown` Python package installed into whatever interpreter runs this -
weasyprint's own bundled interpreter works well for this:
    /opt/homebrew/Cellar/weasyprint/*/libexec/bin/python3 -m pip install markdown
    /opt/homebrew/Cellar/weasyprint/*/libexec/bin/python3 scripts/render_manual_pdf.py . MANUAL
    /opt/homebrew/Cellar/weasyprint/*/libexec/bin/python3 scripts/render_manual_pdf.py . TRAINING_FIELD

Re-run this (and commit the result) any time the source .md file
changes - the PDF doesn't regenerate on its own.

Usage: python3 render_manual_pdf.py <repo_dir> [stem]
  <repo_dir> - path containing <stem>.md, output written next to it
  [stem]     - filename without extension, defaults to "MANUAL"
"""
import re
import sys
import subprocess
import markdown

LIST_ITEM_RE = re.compile(r'^\s*(\d+\.|-|\*)\s')
FENCE_RE = re.compile(r'^\s*```')


def ensure_blank_before_lists(text):
    """python-markdown requires a blank line before a list to treat it as a
    block (GitHub's renderer is more lenient) - without this, a list that
    immediately follows a paragraph line collapses into a run-on paragraph
    instead of rendering as list items.

    KNOWN TRAP, and it has bitten twice: this cannot tell a real list item
    from a *wrapped sentence* whose next line happens to begin with "- ",
    which is easy to write when a dash is being used as punctuation:

        ...says **"Scanning needs the secure address
        - check Tailscale is connected"** rather than opening a scanner.

    That second line looks exactly like a bullet, so a blank line is
    inserted, the paragraph is split in two, and any bold span crossing the
    break is left with literal ** in the PDF. GitHub renders the same source
    correctly, so it looks fine in the browser and wrong only on paper.

    Nothing here can fix it safely - the two cases are genuinely
    indistinguishable at this level. Write the markdown so a "- " never
    starts a line unless it really is a bullet. To check a rendered file:

        pdftotext MANUAL.pdf - | grep '\\*\\*'

    Any hit is this bug; a correctly rendered PDF contains no literal **.
    """
    lines = text.split('\n')
    out = []
    in_fence = False
    for line in lines:
        if FENCE_RE.match(line):
            in_fence = not in_fence
        if (not in_fence and LIST_ITEM_RE.match(line) and out
                and out[-1].strip() != ''
                and not LIST_ITEM_RE.match(out[-1])):
            out.append('')
        out.append(line)
    return '\n'.join(out)


def github_slugify(text, sep):
    """Matches GitHub's heading-anchor algorithm so MANUAL.md's internal
    links (#chapter-name) still resolve inside the rendered PDF."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9 \-]', '', text)
    return text.replace(' ', '-')


CSS = """
@page { size: A4; margin: 2.2cm 2cm; }
body { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; color: #1a1a1a; font-size: 10.5pt; line-height: 1.5; }
h1 { color: #0A2F6B; font-size: 22pt; font-weight: 600; border-bottom: 1.5pt solid #0A2F6B; padding-bottom: 10pt; margin-top: 0; }
h2 { color: #0A2F6B; font-size: 16pt; font-weight: 600; margin-top: 28pt; border-bottom: 0.75pt solid #cbd5e1; padding-bottom: 4pt; __H2_PAGE_BREAK__ }
h1 + h2, h2:first-of-type { page-break-before: avoid; }
h3 { color: #0A2F6B; font-size: 12.5pt; font-weight: 600; margin-top: 18pt; }
h4 { color: #0A2F6B; font-size: 11pt; font-weight: 600; margin-top: 14pt; }
p { margin: 8pt 0; }
a { color: #1a56db; text-decoration: none; }
ul, ol { margin: 6pt 0; padding-left: 22pt; }
li { margin: 3pt 0; }
code { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 9pt; background: #f1f5f9; padding: 1pt 4pt; border-radius: 3pt; }
pre { background: #f1f5f9; border: 0.5pt solid #e2e8f0; border-radius: 5pt; padding: 8pt 10pt; margin: 8pt 0; }
pre code { background: none; padding: 0; }
blockquote { border-left: 3pt solid #0A2F6B; margin: 10pt 0; padding: 4pt 12pt; background: #f8fafc; color: #334155; }
table { border-collapse: collapse; width: 100%; margin: 10pt 0; font-size: 9.5pt; }
th, td { border: 0.5pt solid #cbd5e1; padding: 5pt 8pt; text-align: left; }
th { background: #eef2f7; font-weight: 600; }
hr { border: none; border-top: 1pt solid #cbd5e1; margin: 14pt 0; }
strong { font-weight: 600; }
"""


def main():
    repo_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    stem = sys.argv[2] if len(sys.argv) > 2 else "MANUAL"
    # MANUAL.md is long with numbered chapters, so each ## chapter gets its
    # own page. Shorter docs (training guides) shouldn't force a page per
    # section - pass "compact" as a 3rd arg to let content flow naturally.
    compact = len(sys.argv) > 3 and sys.argv[3] == "compact"
    md_path = f"{repo_dir}/{stem}.md"
    pdf_path = f"{repo_dir}/{stem}.pdf"
    html_path = f"/tmp/{stem}_render.html"

    with open(md_path, "r", encoding="utf-8") as f:
        text = f.read()
    text = ensure_blank_before_lists(text)

    body = markdown.markdown(
        text,
        extensions=["extra", "toc", "sane_lists"],
        extension_configs={"toc": {"slugify": github_slugify, "permalink": False}},
    )

    css = CSS.replace("__H2_PAGE_BREAK__", "page-break-before: avoid;" if compact else "page-break-before: always;")
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{css}</style></head>
<body>{body}</body></html>"""

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    subprocess.run(["weasyprint", html_path, pdf_path], check=True)
    print(f"Wrote {pdf_path}")


if __name__ == "__main__":
    main()
