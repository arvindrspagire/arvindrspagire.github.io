"""
मराठी वैयक्तिक संकेतस्थळ — बिल्ड स्क्रिप्ट (Marathi personal website builder)

    python build.py                 संकेतस्थळ docs/ मध्ये तयार करा (build site into docs/)
    python build.py serve           तयार करून http://localhost:8000 वर पाहा (build + local preview)
    python build.py new-alert       आजच्या तारखेचा नवीन इशारा तयार करा (new alert for today)
    python build.py new-blog नाव     नवीन ब्लॉग लेख तयार करा (new blog post; slug in English)
    python build.py hawaman [YYYY-MM-DD] [--push]
                                    हवामान विज्ञान pipeline च्या आजच्या tweet वरून इशारा तयार करा
                                    (alert from the hawaman-vidnyan daily output; --push = build + git push)
    --drafts                        draft: true असलेले लेखही दाखवा (include drafts, for preview only)

Content lives in content/blog/*.md and content/alerts/*.md (YAML front matter + Markdown).
Profile data lives in site.yaml. Output (docs/) is plain HTML for GitHub Pages or any web host.
Needs: jinja2, mistune, pyyaml.
"""

from datetime import date, datetime
from pathlib import Path
import functools
import hashlib
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
    # used when no IMD district warning was available: model guidance must never look like a warning
    "info":   dict(label="माहितीपर", action="मॉडेल-आधारित अंदाज", desc="IMD चा जिल्हानिहाय इशारा उपलब्ध नसताना प्रसिद्ध केलेला मॉडेल-आधारित अंदाज; अधिकृत इशारा नाही."),
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
    # ?v=<hash> on CSS/photo so browsers fetch the new file right after each deploy
    h = hashlib.md5((ROOT / "static/css/style.css").read_bytes() + (photo.read_bytes() if photo.exists() else b""))
    pubs = site.get("publications", [])
    stats = [(mr_num(sum(p["type"] in ("journal", "preprint") for p in pubs)), "शोधनिबंध व प्रीप्रिंट"),
             (mr_num(sum(p["type"] == "conference" for p in pubs)), "परिषद सादरीकरणे"),
             (mr_num(date.today().year - 2020) + "+", "वर्षांचा संशोधन अनुभव")]
    common = dict(site=site, levels=LEVELS, has_photo=photo.exists(), v=h.hexdigest()[:8], stats=stats,
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
                                ("preprint", "प्रीप्रिंट (Preprints)"),
                                ("article", "लेख (Articles)"),
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


HAWAMAN_COLOR = {1: "red", 2: "orange", 3: "yellow", 4: "green"}     # IMD district warning colour codes
REGIONS_MR = ["कोकण", "मध्य महाराष्ट्र", "मराठवाडा", "विदर्भ"]
HORIZON_MR = {"short": "अल्पकालीन अंदाज", "medium": "मध्यम कालावधी अंदाज",
              "extended": "विस्तारित अंदाज", "seasonal": "हंगामी अंदाज"}


def hawaman(day=None):
    """Turn the hawaman-vidnyan daily tweet package into content/alerts/<date>-hawaman.md (+ images)."""
    import json
    from PIL import Image
    site = yaml.safe_load((ROOT / "site.yaml").read_text(encoding="utf-8"))
    import os                                   # HAWAMAN_DIR is set by the GitHub Actions workflow
    src = Path(os.environ.get("HAWAMAN_DIR") or site.get("hawaman_dir", r"D:\01_Research_Projects\hawaman-vidnyan"))
    out_root = src / "twitter" / "output"
    day = day or (max(p.name for p in out_root.iterdir() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name))
                  if out_root.exists() else None)
    pkg = out_root / str(day)
    if not day or not (pkg / "thread.txt").exists():
        raise SystemExit(f"हवामान विज्ञान tweet सापडले नाही: {pkg}")
    d = date.fromisoformat(day)
    meta = json.loads((pkg / "meta.json").read_text(encoding="utf-8")) if (pkg / "meta.json").exists() else {}

    # IMD warnings for that day decide the colour; without them the alert is "info" (model guidance only)
    level, regions, hazards = "info", list(REGIONS_MR), []
    runs = sorted((src / "data" / "runs").glob(f"{d:%Y%m%d}_*/processed/facts.json"))
    warn = json.loads(runs[-1].read_text(encoding="utf-8")).get("warnings", {}) if runs else {}
    wday = next((w for w in warn.get("days", []) if w["date"] == day), None) if warn.get("available") else None
    if wday and wday["districts"]:
        sys.path.insert(0, str(src / "src"))
        from hawaman_maharashtra.content.phrases import WARNING_MR
        level = HAWAMAN_COLOR.get(wday["max_color"], "green")
        hit = [x for x in wday["districts"] if x["color"] < 4]
        regions = [x["name_mr"] for x in hit] or regions
        hazards = sorted({WARNING_MR.get(c) for x in hit for c in x["codes"]} - {None})

    thread = (pkg / "thread.txt").read_text(encoding="utf-8")
    parts = [s.strip() for s in re.split(r"\n-{3,}\n", thread) if s.strip()]
    first = parts[0].splitlines()
    title = re.sub(r"^\W+", "", first[0]).strip() or "महाराष्ट्र हवामान"
    horizon = HORIZON_MR.get(meta.get("horizon"), "हवामान अंदाज")

    def lines(s):                       # keep tweet line breaks, drop hashtag lines
        return "  \n".join(l for l in s.splitlines() if not l.startswith("#"))

    img_dir = ROOT / "static" / "images" / "alerts" / day
    imgs = []
    for name in meta.get("media") or sorted(p.name for p in pkg.glob("[0-9]_*.jpg")):
        if (pkg / name).exists():
            img_dir.mkdir(parents=True, exist_ok=True)
            im = Image.open(pkg / name).convert("RGB")
            im.thumbnail((1200, 1200))
            im.save(img_dir / name, quality=82, optimize=True)
            imgs.append(name)

    body = [f"**{horizon}** · स्रोत: हवामान विज्ञान स्वयंचलित विश्लेषण", "", lines("\n".join(first[1:])), ""]
    if imgs:
        body += [f"![{horizon}](../../static/images/alerts/{day}/{imgs[0]})", ""]
    if len(parts) > 1:
        body += ["### हवामानामागचे कारण", ""] + [lines(s) + "\n" for s in parts[1:]]
    body += [f"![हवामान प्रणाली](../../static/images/alerts/{day}/{n})\n" for n in imgs[1:]]
    tw = site.get("contact", {}).get("twitter")
    if tw:
        body += ["", f"दैनंदिन अपडेटसाठी X वर फॉलो करा: [@{tw.rstrip('/').rsplit('/', 1)[-1]}]({tw})"]

    q = lambda v: json.dumps(v, ensure_ascii=False)
    front = [f"title: {q(title)}", f"date: {day}", f"level: {level}",
             f"valid: {q(mr_date(d) + ' पासून — ' + horizon)}",
             f"regions: {q(regions)}", f"hazards: {q(hazards)}", "source: hawaman-vidnyan"]
    dest = CONTENT / "alerts" / f"{day}-hawaman.md"
    dest.write_text("---\n" + "\n".join(front) + "\n---\n\n" + "\n".join(body).strip() + "\n", encoding="utf-8")
    print(f"हवामान विज्ञान इशारा ({level}): {dest}")
    return day


def push(msg):
    import subprocess
    run = lambda *a: subprocess.run(["git", *a], cwd=ROOT, check=True)
    run("add", "-A")
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        print("बदल नाहीत; push केले नाही"); return
    run("commit", "-q", "-m", msg)
    run("pull", "-q", "--rebase")             # laptop and GitHub Actions may both publish
    run("push", "-q")
    print("GitHub वर push केले")


def serve(port=8000):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(OUT))
    print(f"पाहा: http://localhost:{port}   (थांबवण्यासाठी Ctrl+C)")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a not in ("--drafts", "--push")]
    drafts = "--drafts" in sys.argv
    cmd = args[0] if args else "build"
    if cmd == "build":
        build(drafts)
    elif cmd == "serve":
        build(drafts)
        serve()
    elif cmd == "new-alert":
        new_alert()
    elif cmd == "hawaman":
        day = hawaman(next((a for a in args[1:] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", a)), None))
        if "--push" in sys.argv:
            build()
            push(f"इशारा {day} (हवामान विज्ञान)")
    elif cmd == "new-blog":
        if len(args) < 2:
            raise SystemExit("उदा.: python build.py new-blog monsoon-2026")
        new_blog(args[1])
    else:
        raise SystemExit(__doc__)
