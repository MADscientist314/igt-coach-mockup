"""
Build docs/ -- the static site GitHub Pages serves.

web/app.html is authored as an Artifact fragment (no document skeleton) so the
same source can be published as a Claude Artifact. Pages needs a real HTML
document, so this wraps it in the same skeleton the Artifact platform uses and
writes the result to docs/index.html. One source, three destinations: the local
server, the Artifact, and Pages.

Also renders README.md to docs/methods.html, so the methods are readable
without the viewer needing access to the repository.

A GitHub Pages site is public to the internet whether or not its source repo
is private. Access-controlled Pages is Enterprise Cloud only. Since this is an
unpublished study, every page here carries robots noindex/nofollow and the
site ships a robots.txt that disallows everything. That keeps the URL usable
by IRB reviewers while keeping the study out of search results. It is
obscurity, not access control -- anyone with the link can read it.

Run:  python server/build_site.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WEB = ROOT / "web"
DOCS = ROOT / "docs"

NOINDEX = (
    '<meta name="robots" content="noindex, nofollow, noarchive, nosnippet">\n'
    '<meta name="googlebot" content="noindex, nofollow">\n'
)

SKELETON_OPEN = (
    '<!doctype html>\n<html lang="en">\n<head>\n'
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width,initial-scale=1,'
    'viewport-fit=cover">\n'
    + NOINDEX +
    '<style>:root{color-scheme:light;'
    'padding-top:env(safe-area-inset-top,0px);'
    'padding-bottom:env(safe-area-inset-bottom,0px)}'
    'body{margin:0;font:14px system-ui}'
    'img{max-width:100%}[hidden]{display:none!important}</style>\n'
    '</head>\n<body>\n')
SKELETON_CLOSE = "\n</body>\n</html>\n"

# A link back to the methods page, injected into the app's masthead.
METHODS_LINK = (
    '<p style="margin:10px 0 0"><a href="./methods.html" '
    'style="color:var(--series-1);font-size:13px;font-weight:600">'
    'Methods, guardrails and sources &rarr;</a></p>')


def build_app():
    frag = (WEB / "app.html").read_text(encoding="utf-8")
    anchor = "College of Medicine.</p>"
    if anchor not in frag:
        raise SystemExit("masthead anchor not found -- app.html changed shape; "
                         "the methods link would be dropped silently.")
    frag = frag.replace(anchor, anchor + METHODS_LINK, 1)
    (DOCS / "index.html").write_text(
        SKELETON_OPEN + frag + SKELETON_CLOSE, encoding="utf-8")


def build_methods():
    import markdown
    md = (ROOT / "README.md").read_text(encoding="utf-8")
    html = markdown.markdown(
        md, extensions=["tables", "fenced_code", "toc", "sane_lists"])
    page = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
""" + NOINDEX + """<title>IGT Coach &mdash; methods</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600&family=Public+Sans:wght@400;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{--paper:#F7F3F1;--surface:#fff;--sunken:#EFE9E6;--ink:#241B23;--ink-2:#584B55;
  --ink-3:#8A7C85;--rule:#E2D8D4;--accent:#8E2F55;--accent-2:#1F6F6B;
  --display:"Fraunces",Georgia,serif;--body:"Public Sans",system-ui,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,monospace;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --paper:#17131A;--surface:#211B23;--sunken:#2B232E;--ink:#F3EBF0;--ink-2:#C3B5BE;
  --ink-3:#8E8089;--rule:#352C38;--accent:#E58AAC;--accent-2:#5FC3BB;
  color-scheme:dark}}
:root[data-theme="dark"]{--paper:#17131A;--surface:#211B23;--sunken:#2B232E;
  --ink:#F3EBF0;--ink-2:#C3B5BE;--ink-3:#8E8089;--rule:#352C38;--accent:#E58AAC;
  --accent-2:#5FC3BB;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--body);
  line-height:1.65;-webkit-font-smoothing:antialiased}
.wrap{max-width:820px;margin:0 auto;
  padding-left:max(16px,env(safe-area-inset-left,0px));
  padding-right:max(16px,env(safe-area-inset-right,0px));
  padding-block:36px 72px}
h1,h2,h3{font-family:var(--display);font-weight:600;text-wrap:balance;
  line-height:1.2;margin:2em 0 .5em}
h1{font-size:clamp(28px,5vw,40px);margin-top:0;letter-spacing:-.015em}
h2{font-size:24px;padding-top:18px;border-top:1px solid var(--rule)}
h3{font-size:18px}
p,li{color:var(--ink-2);max-width:68ch}
strong{color:var(--ink)}
a{color:var(--accent)}
code{font-family:var(--mono);font-size:.88em;background:var(--sunken);
  padding:1px 5px;border-radius:5px}
pre{background:var(--sunken);padding:14px 16px;border-radius:11px;
  overflow-x:auto;border:1px solid var(--rule)}
pre code{background:none;padding:0}
table{width:100%;border-collapse:collapse;margin:1em 0;font-size:14.5px;
  display:block;overflow-x:auto}
th,td{text-align:left;padding:8px 11px;border-bottom:1px solid var(--rule)}
th{font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;
  color:var(--ink-3)}
blockquote{border-left:3px solid var(--accent-2);margin:1.2em 0;
  padding:2px 0 2px 16px;color:var(--ink-2)}
hr{border:0;border-top:1px solid var(--rule);margin:2.4em 0}
.back{display:inline-block;margin-bottom:26px;font-size:13.5px;font-weight:600;
  color:var(--accent);text-decoration:none}
.banner{background:var(--sunken);border:1px solid var(--rule);border-radius:12px;
  padding:14px 16px;margin-bottom:30px;font-size:13.5px;color:var(--ink-2)}
.banner strong{display:block;margin-bottom:3px;color:var(--ink);
  font-family:var(--display);font-size:15px}
</style>
</head>
<body><div class="wrap">
<a class="back" href="./">&larr; Back to the mock-up</a>
<div class="banner"><strong>Unpublished research</strong>
This describes a study that has not been submitted or published. The page is
unlisted and excluded from search engines, but anyone holding the link can read
it. Please do not circulate it.</div>
""" + html + """
</div></body></html>
"""
    (DOCS / "methods.html").write_text(page, encoding="utf-8")


def main():
    if not (WEB / "data.json").exists():
        print("data.json missing -- run python server/build_data.py first.")
        return 1
    if DOCS.exists():
        shutil.rmtree(DOCS)
    DOCS.mkdir(parents=True)

    build_app()
    build_methods()
    shutil.copy2(WEB / "data.json", DOCS / "data.json")

    # Keep crawlers out. Belt and braces alongside the per-page meta tags.
    (DOCS / "robots.txt").write_text(
        "# Unpublished research. Please do not index.\n"
        "User-agent: *\nDisallow: /\n", encoding="utf-8")
    # Stop GitHub running Jekyll over the directory.
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")

    print("built %s" % DOCS)
    for f in sorted(DOCS.iterdir()):
        print("  %-16s %8.1f KB" % (f.name, f.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
