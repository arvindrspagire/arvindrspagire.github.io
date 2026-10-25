"""
मराठी वैयक्तिक संकेतस्थळ — बिल्ड स्क्रिप्ट (Marathi personal website builder)

    python build.py                 संकेतस्थळ docs/ मध्ये तयार करा (build site into docs/)
    python build.py serve           तयार करून http://localhost:8000 वर पाहा (build + local preview)
    python build.py new-alert       आजच्या तारखेचा नवीन इशारा तयार करा (new alert for today)
    python build.py new-blog नाव     नवीन ब्लॉग लेख तयार करा (new blog post; slug in English)
    --drafts                        draft: true असलेले लेखही दाखवा (include drafts, for preview only)

Content lives in content/blog/*.md and content/alerts/*.md (YAML front matter + Markdown).
Profile data lives in site.yaml. Output (docs/) is plain HTML for GitHub Pages or any web host.
Needs: jinja2, mistune, pyyaml.
"""

from datetime import date, datetime
from pathlib import Path
import functools
import http.server
import re
import shutil
import sys

try:
    import mistune
    import yaml
    from jinja2 import Environment, FileSystemLoader, select_autoescape
except ImportError as e:
    raise SystemExit(f"{e}\nmyenv वापरा: 'conda activate myenv' करून पुन्हा चालवा, किंवा site.bat वर double-click करा.")

# Marathi output must not crash a non-UTF-8 console / redirected stdout
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "docs"
CONTENT = ROOT / "content"

MR_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")
MONTHS = ["जानेवारी", "फेब्रुवारी", "मार्च", "एप्रिल", "मे", "जून", "जुलै",
          "ऑगस्ट", "सप्टेंबर", "ऑक्टोबर", "नोव्हेंबर", "डिसेंबर"]
WEEKDAYS = ["सोमवार", "मंगळवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"]

# IMD colour-coded impact-based warnings
LEVELS = {
    "green":  dict(label="हिरवा", action="कोणताही इशारा नाही", desc="सामान्य हवामान; विशेष काळजीची गरज नाही."),
    "yellow": dict(label="पिवळा", action="अद्ययावत राहा", desc="हवामानावर लक्ष ठेवा; परिस्थिती बदलू शकते."),
    "orange": dict(label="नारंगी", action="तयार राहा", desc="धोकादायक हवामानाची शक्यता; आवश्यक तयारी करा."),
    "red":    dict(label="लाल", action="कृती करा", desc="अतितीव्र हवामान; स्थानिक प्रशासनाच्या सूचनांचे पालन करा."),
}

md = mistune.create_markdown(plugins=["table", "strikethrough", "url"])


def mr_num(x):
    return str(x).translate(MR_DIGITS)


def mr_date(d, weekday=False):
    s = f"{mr_num(d.day)} {MONTHS[d.month - 1]} {mr_num(d.year)}"
    return f"{WEEKDAYS[d.weekday()]}, {s}" if weekday else s


def read_post(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, flags=re.S)
    if not m:
        raise SystemExit(f"{path.name}: फाइलच्या सुरुवातीला --- मधील माहिती (front matter) नाही")
    meta = yaml.safe_load(m.group(1)) or {}
    for key in ("title", "date"):
        if key not in meta:
            raise SystemExit(f"{path.name}: '{key}' आवश्यक आहे")
    d = meta["date"]
    if isinstance(d, str):
        d = datetime.strptime(d, "%Y-%m-%d").date()
    elif isinstance(d, datetime):
        d = d.date()
    meta["date"] = d
    meta["slug"] = path.stem
    meta["body"] = md(m.group(2))
    meta["date_mr"] = mr_date(d)
    meta["date_mr_long"] = mr_date(d, weekday=True)
    return meta


def load(kind, drafts):
    posts = [read_post(p) for p in sorted((CONTENT / kind).glob("*.md"))]
    posts = [p for p in posts if drafts or not p.get("draft")]
    return sorted(posts, key=lambda p: (p["date"], p["slug"]), reverse=True)


def check_alert(a):
    lvl = str(a.get("level", "")).lower()
    if lvl not in LEVELS:
        raise SystemExit(f"{a['slug']}.md: level हे green / yellow / orange / red यापैकी असावे (आहे: {a.get('level')!r})")
    a["level"] = lvl
    a["lvl"] = LEVELS[lvl]
    for key in ("regions", "hazards"):
        v = a.get(key) or []
        a[key] = [v] if isinstance(v, str) else list(v)
    return a


def build(drafts=False):
    site = yaml.safe_load((ROOT / "site.yaml").read_text(encoding="utf-8"))
    blog = load("blog", drafts)
    alerts = [check_alert(a) for a in load("alerts", drafts)]

    env = Environment(loader=FileSystemLoader(ROOT / "templates"),
                      autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
    env.filters["mr"] = mr_num
    photo = ROOT / site.get("photo", "static/images/profile.jpg")
    common = dict(site=site, levels=LEVELS, has_photo=photo.exists(),
                  built=mr_date(date.today()), year=mr_num(date.today().year))

    # empty docs/ but keep the folder itself (a running preview server keeps it open on Windows)
    OUT.mkdir(exist_ok=True)
    for child in OUT.iterdir():
        if child.name == "CNAME":            # custom-domain file for GitHub Pages, if any
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    shutil.copytree(ROOT / "static", OUT / "static")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")

    def page(template, rel_path, active, **ctx):
        depth = rel_path.count("/")
        dest = OUT / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        html = env.get_template(template).render(root="../" * depth or "./", active=active, **common, **ctx)
        # folder links -> explicit index.html, so pages also open from disk (file://), not only on a server
        html = re.sub(r'href="((?:\.\.?/)+(?:[^"#?:]*/)?)"', r'href="\1index.html"', html)
        dest.write_text(html, encoding="utf-8")

    page("index.html", "index.html", "home", latest_alert=alerts[0] if alerts else None, posts=blog[:3])
    page("about.html", "about/index.html", "about")
    page("publications.html", "publications/index.html", "publications",
         groups=[(t, lab, [p for p in site.get("publications", []) if p["type"] == t])
                 for t, lab in (("journal", "शोधनिबंध (Journal articles)"),
                                ("report", "अहवाल व प्रकाशने (Reports)"),
                                ("conference", "परिषदांमधील सादरीकरणे (Conference presentations)"))])
    page("blog_list.html", "blog/index.html", "blog", posts=blog)
    for p in blog:
        page("post.html", f"blog/{p['slug']}/index.html", "blog", post=p)
    page("alerts_list.html", "alerts/index.html", "alerts", alerts=alerts)
    for a in alerts:
        page("alert.html", f"alerts/{a['slug']}/index.html", "alerts", alert=a)
    page("contact.html", "contact/index.html", "contact")
    page("404.html", "404.html", "")

    note = " (drafts सह)" if drafts else ""
    print(f"तयार{note}: {len(blog)} लेख, {len(alerts)} इशारे -> {OUT}")


def new_alert():
    today = date.today()
    path = CONTENT / "alerts" / f"{today:%Y-%m-%d}.md"
    n = 2
    while path.exists():
        path = CONTENT / "alerts" / f"{today:%Y-%m-%d}-{n}.md"
        n += 1
    path.write_text(f"""---
title: "विदर्भ: येथे इशाऱ्याचे शीर्षक लिहा"
date: {today:%Y-%m-%d}
level: yellow            # green / yellow / orange / red
valid: "{mr_date(today)} सकाळी ८:३० ते पुढील २४ तास"
regions: [नागपूर, वर्धा, चंद्रपूर]
hazards: [मुसळधार पाऊस, विजांसह वादळ]
draft: true              # प्रसिद्ध करताना ही ओळ काढा किंवा false करा
---

**सारांश:** येथे एक-दोन वाक्यांत मुख्य इशारा लिहा.

### पुढील २४ तास
- ...

### नागरिकांसाठी सूचना
- विजा चमकत असताना झाडाखाली थांबू नका.
- ...
""", encoding="utf-8")
    print("नवीन इशारा:", path)


def new_blog(slug):
    if not re.fullmatch(r"[a-z0-9-]+", slug):
        raise SystemExit("लेखाचे नाव (slug) इंग्रजी लहान अक्षरे, अंक आणि - वापरून द्या, उदा. monsoon-2026")
    today = date.today()
    path = CONTENT / "blog" / f"{today:%Y-%m-%d}-{slug}.md"
    if path.exists():
        raise SystemExit(f"{path} आधीच अस्तित्वात आहे")
    path.write_text(f"""---
title: "लेखाचे शीर्षक"
date: {today:%Y-%m-%d}
summary: "लेखाचा एका वाक्यातील सारांश (यादीत दिसतो)."
tags: [हवामान]
draft: true              # प्रसिद्ध करताना ही ओळ काढा किंवा false करा
---

येथे तुमचा लेख लिहा. **ठळक**, *तिरके*, यादी, [दुवे](https://mausam.imd.gov.in) आणि चित्रे वापरता येतात:

![चित्राचे वर्णन](../../static/images/chitra.jpg)
""", encoding="utf-8")
    print("नवीन लेख:", path)


def serve(port=8000):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(OUT))
    print(f"पाहा: http://localhost:{port}   (थांबवण्यासाठी Ctrl+C)")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--drafts"]
    drafts = "--drafts" in sys.argv
    cmd = args[0] if args else "build"
    if cmd == "build":
        build(drafts)
    elif cmd == "serve":
        build(drafts)
        serve()
    elif cmd == "new-alert":
        new_alert()
    elif cmd == "new-blog":
        if len(args) < 2:
            raise SystemExit("उदा.: python build.py new-blog monsoon-2026")
        new_blog(args[1])
    else:
        raise SystemExit(__doc__)
