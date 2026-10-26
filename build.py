"""
Bilingual personal website builder: English by default, मराठी with the header button (choice is remembered)

    python build.py                 build the site into docs/
    python build.py serve           build + local preview at http://localhost:8000
    python build.py new-alert       new alert file for today
    python build.py new-blog slug   new blog post (slug: lowercase letters, digits, -)
    python build.py forecast [--push]   rebuild with the AI rain forecast (content/forecast/latest.json)
    python build.py hawaman [YYYY-MM-DD] [--push]
                                    alert from the hawaman-vidnyan daily output (--push = build + git push)
    --drafts                        include draft: true posts (preview only)

Content lives in content/blog/*.md and content/alerts/*.md (YAML front matter + Markdown).
Profile data lives in site.yaml. Output (docs/) is plain HTML for GitHub Pages or any web host.

Two languages: any text value may be {en: "...", mr: "..."} (site.yaml, front matter); a plain string
is shown in both languages. A post body may hold both: English first, then a line <!-- mr -->, then Marathi.
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
    from markupsafe import Markup, escape
except ImportError as e:
    raise SystemExit(f"{e}\nUse myenv: run 'conda activate myenv' and try again, or double-click site.bat.")

# Marathi output must not crash a non-UTF-8 console / redirected stdout
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "docs"
CONTENT = ROOT / "content"

MR_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"]
WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS_MR = ["जानेवारी", "फेब्रुवारी", "मार्च", "एप्रिल", "मे", "जून", "जुलै",
             "ऑगस्ट", "सप्टेंबर", "ऑक्टोबर", "नोव्हेंबर", "डिसेंबर"]
WEEKDAYS_MR = ["सोमवार", "मंगळवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"]


def bi(en, mr):
    return {"en": en, "mr": mr}


# IMD colour-coded impact-based warnings
LEVELS = {
    "green":  dict(label=bi("Green", "हिरवा"), action=bi("No warning", "कोणताही इशारा नाही"),
                   desc=bi("Normal weather; no special precautions needed.", "सामान्य हवामान; विशेष काळजीची गरज नाही.")),
    "yellow": dict(label=bi("Yellow", "पिवळा"), action=bi("Be updated", "अद्ययावत राहा"),
                   desc=bi("Keep an eye on the weather; conditions may change.", "हवामानावर लक्ष ठेवा; परिस्थिती बदलू शकते.")),
    "orange": dict(label=bi("Orange", "नारंगी"), action=bi("Be prepared", "तयार राहा"),
                   desc=bi("Hazardous weather is likely; make the necessary preparations.", "धोकादायक हवामानाची शक्यता; आवश्यक तयारी करा.")),
    "red":    dict(label=bi("Red", "लाल"), action=bi("Take action", "कृती करा"),
                   desc=bi("Extreme weather; follow the instructions of local authorities.", "अतितीव्र हवामान; स्थानिक प्रशासनाच्या सूचनांचे पालन करा.")),
    # used when no IMD district warning was available: model guidance must never look like a warning
    "info":   dict(label=bi("Information", "माहितीपर"), action=bi("Model-based analysis", "मॉडेल-आधारित विश्लेषण"),
                   desc=bi("Model-based weather analysis published when no IMD district warning is available; not an official warning.",
                           "IMD चा जिल्हानिहाय इशारा उपलब्ध नसताना प्रसिद्ध केलेले मॉडेल-आधारित हवामान विश्लेषण; अधिकृत इशारा नाही.")),
}

md = mistune.create_markdown(plugins=["table", "strikethrough", "url"])


def mr_num(x):
    return str(x).translate(MR_DIGITS)


def mr_date(d, weekday=False):
    s = f"{mr_num(d.day)} {MONTHS_MR[d.month - 1]} {mr_num(d.year)}"
    return f"{WEEKDAYS_MR[d.weekday()]}, {s}" if weekday else s


def en_date(d, weekday=False):
    s = f"{d.day} {MONTHS_EN[d.month - 1]} {d.year}"
    return f"{WEEKDAYS_EN[d.weekday()]}, {s}" if weekday else s


def date_bi(d, weekday=False):
    return bi(en_date(d, weekday), mr_date(d, weekday))


def day_bi(d):                          # "Tuesday, 27 October" / "मंगळवार, २७ ऑक्टोबर"
    return bi(f"{WEEKDAYS_EN[d.weekday()]}, {d.day} {MONTHS_EN[d.month - 1]}",
              f"{WEEKDAYS_MR[d.weekday()]}, {mr_num(d.day)} {MONTHS_MR[d.month - 1]}")


# ---- template helpers: both languages go into the page, CSS shows the one in <html lang> ----
def t(en, mr=None):
    """Inline bilingual text. t("Home", "मुख्यपृष्ठ"), t({"en": .., "mr": ..}) or t("same in both")."""
    if isinstance(en, dict):
        en, mr = en.get("en"), en.get("mr")
    if en is None:
        en, mr = mr, None
    if en is None:
        return Markup("")
    if mr is None or mr == en:
        return escape(en)
    return Markup('<span class="l-en">{}</span><span class="l-mr" lang="mr">{}</span>').format(en, mr)


def num(n):
    """A number in both scripts: 27 / २७."""
    return t(str(n), mr_num(n))


def pick(x, lang):
    """Plain text in one language (for attributes, <title>, <option>)."""
    if isinstance(x, dict):
        return x.get(lang) or x.get("en") or x.get("mr") or ""
    return "" if x is None else str(x)


def read_post(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, flags=re.S)
    if not m:
        raise SystemExit(f"{path.name}: no front matter (--- block) at the top of the file")
    meta = yaml.safe_load(m.group(1)) or {}
    for key in ("title", "date"):
        if key not in meta:
            raise SystemExit(f"{path.name}: '{key}' is required")
    d = meta["date"]
    if isinstance(d, str):
        d = datetime.strptime(d, "%Y-%m-%d").date()
    elif isinstance(d, datetime):
        d = d.date()
    meta["date"] = d
    meta["slug"] = path.stem
    # English body, then a line "<!-- mr -->", then the Marathi body; one language alone is shown in both
    parts = re.split(r"^<!--\s*mr\s*-->[ \t]*$", m.group(2), maxsplit=1, flags=re.M)
    meta["body"] = bi(md(parts[0]), md(parts[1])) if len(parts) == 2 and parts[0].strip() else md(parts[-1])
    meta["date_str"] = date_bi(d)
    meta["date_long"] = date_bi(d, weekday=True)
    return meta


def load(kind, drafts):
    posts = [read_post(p) for p in sorted((CONTENT / kind).glob("*.md"))]
    posts = [p for p in posts if drafts or not p.get("draft")]
    return sorted(posts, key=lambda p: (p["date"], p["slug"]), reverse=True)


def check_alert(a):
    lvl = str(a.get("level", "")).lower()
    if lvl not in LEVELS:
        raise SystemExit(f"{a['slug']}.md: level must be green / yellow / orange / red (got {a.get('level')!r})")
    a["level"] = lvl
    a["lvl"] = LEVELS[lvl]
    for key in ("regions", "hazards"):
        v = a.get(key) or []
        a[key] = [v] if isinstance(v, (str, dict)) else list(v)
    return a


def load_forecast():
    """content/forecast/latest.json, written daily by extreme-rainfall-aiera (python -m operational.forecast --publish).
    That file is Marathi-first; the English names come from the region ids and the tables below."""
    import json
    path = CONTENT / "forecast" / "latest.json"
    if not path.exists():
        return None
    fc = json.loads(path.read_text(encoding="utf-8"))
    fc["base_str"] = date_bi(date.fromisoformat(fc["base_date"]), weekday=True)
    for b in fc.get("bands", []):
        b["label"] = bi(BAND_EN.get(b["key"], b["key"]), b["mr"])
    for r in fc["regions"]:
        r["name"] = bi(r.get("name_en") or r["id"], r["name_mr"])
        for d in r["days"]:
            day = date.fromisoformat(d.get("start", d["date"]))   # IMD rain day: 08:30 IST start -> 08:30 next day
            d["date_str"] = day_bi(day)
            d["band_label"] = bi(BAND_EN.get(d["band"], d["band"]), d["band_mr"])
            d["pct"] = round(d["score"] * 100)
            d["hist_pct"] = round(d["hist_hit_rate"] * 100) if d.get("hist_hit_rate") is not None else "–"
            if d.get("ifs_rain_mm") is not None:
                d["ifs_str"] = f"{d['ifs_rain_mm']:.1f}"
        r["threshold_str"] = f"{r['threshold_mm']:.1f}"
    cats = fc.get("categorical", [])
    # Marathi district name -> English, learned from the taluka ids "Taluka (District)"
    dist_en = {r["group"]: m.group(1) for c in cats for r in c["regions"]
               if r.get("group") and (m := re.search(r"\(([^()]+)\)$", r["id"]))}
    for c in cats:
        c["title"] = bi(CAT_TITLE_EN.get(c["id"], c.get("title_mr", c["id"])), c.get("title_mr", c["id"]))
        for d in c["days"]:
            day = date.fromisoformat(d.get("start", d["date"]))   # IMD rain day: 08:30 IST start -> 08:30 next day
            d["date_str"] = day_bi(day)
            d["short"] = bi(f"{day.day} {MONTHS_EN[day.month - 1][:3]}", f"{mr_num(day.day)} {MONTHS_MR[day.month - 1][:3]}")
        for k in c["classes"]:
            lo, hi = k["from"], k["to"]
            rng = f"< {hi}" if not lo else f"≥ {lo}" if hi is None else f"{lo}–{round(hi - 0.1, 1)}"
            k["label"] = bi(CLASS_EN.get(k["key"], k["key"]), k["mr"])
            k["range"] = bi(rng + " mm/day", mr_num(rng) + " मिमी/दिवस")
        c["hss"] = [f"{h:.2f}" if h is not None else "–" for h in c.get("test_median_hss", [])]
        c["hss_jjas"] = [f"{h:.2f}" if h is not None else "–" for h in c.get("test_median_hss_jjas") or []]
        for r in c["regions"]:
            r["pct"] = [round(max(p) * 100) for p in r["prob"]]
            g = r.get("group") or ""
            r["group_label"] = bi(SUBDIV_EN.get(g) or dist_en.get(g) or g, SUBDIV_MR.get(g) or g)
            r["name"] = bi(region_name_en(r, dist_en), r["name_mr"])
        groups = {}
        for r in c["regions"]:
            groups.setdefault(r["group_label"]["en"], (r["group_label"], []))[1].append(r)
        c["groups"] = [(lab, sorted(rs, key=lambda r: r["name"]["en"])) for _, (lab, rs) in sorted(groups.items())]
        c["maps"] = district_svg(CONTENT / "forecast" / f"shapes_{c['id']}.json")
        # Maharashtra-wide summary: how many regions fall in each category on each day
        c["summary"] = [[sum(r["cat"][i] == k for r in c["regions"]) for k in range(len(c["classes"]))]
                        for i in range(len(c["days"]))]
    return fc


def region_name_en(r, dist_en):
    rid = r["id"]
    if m := re.fullmatch(r"(.+?) \(([^()]+)\)", rid):              # taluka: "Shahuwadi (Kolhapur)"
        return f"{m.group(1)} — {m.group(2)}"
    if m := re.fullmatch(r"([\d.]+)N_([\d.]+)E", rid):              # grid cell: "15.75N_73.75E"
        g = r.get("group")
        return f"{m.group(1)}°N, {m.group(2)}°E" + (f" ({dist_en.get(g, g)})" if g else "")
    return rid                                                       # district: "Ahilyanagar"


SUBDIV_MR = {"Konkan": "कोकण", "Konkan & Goa": "कोकण", "Madhya Maharashtra": "मध्य महाराष्ट्र",
             "Marathwada": "मराठवाडा", "Vidarbha": "विदर्भ"}
SUBDIV_EN = {"Konkan & Goa": "Konkan", "Konkan": "Konkan", "Madhya Maharashtra": "Madhya Maharashtra",
             "Marathwada": "Marathwada", "Vidarbha": "Vidarbha"}
CLASS_EN = {"no_rain": "No rain / very light", "light": "Light", "moderate": "Moderate", "heavy": "Heavy"}
BAND_EN = {"low": "Low", "moderate": "Moderate", "high": "High", "very_high": "Very high"}
CAT_TITLE_EN = {"maharashtra_talukas": "Experimental AI rain forecast — pick your taluka",
                "maharashtra_districts": "Maharashtra — district-wise rain category forecast",
                "maharashtra_grid": "Maharashtra — IMD 0.25° grid rain category forecast"}


def district_svg(path):
    """{region id: SVG path 'd'} in a 0..W x 0..H box (equirectangular, cos-lat scaled), plus the box."""
    import json
    import math
    if not path.exists():
        return None
    shapes = json.loads(path.read_text(encoding="utf-8"))
    rings = {rid: (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]])
             for rid, g in shapes.items()}
    pts = [p for polys in rings.values() for poly in polys for ring in poly for p in ring]
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    k = math.cos(math.radians((y0 + y1) / 2))
    s = 400 / ((x1 - x0) * k)
    proj = lambda p: f"{(p[0] - x0) * k * s:.1f},{(y1 - p[1]) * s:.1f}"
    d = {rid: " ".join("M" + " L".join(proj(p) for p in ring) + "Z" for poly in polys for ring in poly)
         for rid, polys in rings.items()}
    return {"paths": d, "w": 400, "h": round((y1 - y0) * s)}


def build(drafts=False):
    site = yaml.safe_load((ROOT / "site.yaml").read_text(encoding="utf-8"))
    blog = load("blog", drafts)
    alerts = [check_alert(a) for a in load("alerts", drafts)]

    env = Environment(loader=FileSystemLoader(ROOT / "templates"),
                      autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
    env.globals["t"] = t
    env.filters["t"] = t
    env.filters["num"] = num
    env.filters["mr"] = mr_num
    env.filters["en"] = lambda x: pick(x, "en")
    env.filters["in_mr"] = lambda x: pick(x, "mr")
    photo = ROOT / site.get("photo", "static/images/profile.jpg")
    # ?v=<hash> on CSS/photo so browsers fetch the new file right after each deploy
    h = hashlib.md5((ROOT / "static/css/style.css").read_bytes() + (photo.read_bytes() if photo.exists() else b""))
    pubs = site.get("publications", [])
    stats = [(sum(p["type"] in ("journal", "preprint") for p in pubs), bi("Papers & preprints", "शोधनिबंध व प्रीप्रिंट")),
             (sum(p["type"] == "conference" for p in pubs), bi("Conference presentations", "परिषद सादरीकरणे")),
             (f"{date.today().year - 2020}+", bi("Years of research experience", "वर्षांचा संशोधन अनुभव"))]
    common = dict(site=site, levels=LEVELS, has_photo=photo.exists(), v=h.hexdigest()[:8], stats=stats,
                  built=date_bi(date.today()), year=date.today().year)

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

    fc = load_forecast()
    page("index.html", "index.html", "home", latest_alert=alerts[0] if alerts else None, posts=blog[:3], fc=fc)
    page("about.html", "about/index.html", "about")
    page("publications.html", "publications/index.html", "publications",
         groups=[(t_, lab, [p for p in site.get("publications", []) if p["type"] == t_])
                 for t_, lab in (("journal", bi("Journal articles", "शोधनिबंध")),
                                 ("preprint", bi("Preprints", "प्रीप्रिंट")),
                                 ("article", bi("Articles", "लेख")),
                                 ("report", bi("Reports", "अहवाल व प्रकाशने")),
                                 ("conference", bi("Conference presentations", "परिषदांमधील सादरीकरणे")))])
    page("blog_list.html", "blog/index.html", "blog", posts=blog)
    for p in blog:
        page("post.html", f"blog/{p['slug']}/index.html", "blog", post=p)
    page("alerts_list.html", "alerts/index.html", "alerts", alerts=alerts)
    for a in alerts:
        page("alert.html", f"alerts/{a['slug']}/index.html", "alerts", alert=a)
    page("forecast.html", "forecast/index.html", "forecast", fc=fc)
    page("contact.html", "contact/index.html", "contact")
    page("thanks.html", "contact/thanks/index.html", "contact")
    page("404.html", "404.html", "")

    note = " (with drafts)" if drafts else ""
    print(f"Built{note}: {len(blog)} posts, {len(alerts)} alerts -> {OUT}")


def new_alert():
    today = date.today()
    path = CONTENT / "alerts" / f"{today:%Y-%m-%d}.md"
    n = 2
    while path.exists():
        path = CONTENT / "alerts" / f"{today:%Y-%m-%d}-{n}.md"
        n += 1
    path.write_text(f"""---
title:
  en: "Vidarbha: write the alert title here"
  mr: "विदर्भ: येथे इशाऱ्याचे शीर्षक लिहा"
date: {today:%Y-%m-%d}
level: yellow            # green / yellow / orange / red
valid:
  en: "{en_date(today)}, 08:30 IST for the next 24 hours"
  mr: "{mr_date(today)} सकाळी ८:३० ते पुढील २४ तास"
regions: [{{en: Nagpur, mr: नागपूर}}, {{en: Wardha, mr: वर्धा}}, {{en: Chandrapur, mr: चंद्रपूर}}]
hazards: [{{en: Heavy rain, mr: मुसळधार पाऊस}}, {{en: Thunderstorm with lightning, mr: विजांसह वादळ}}]
draft: true              # remove this line (or set false) to publish
---

**Summary:** write the main warning here in one or two sentences.

### Next 24 hours
- ...

### Advice for citizens
- Do not shelter under trees during lightning.
- ...

<!-- mr -->

**सारांश:** येथे एक-दोन वाक्यांत मुख्य इशारा लिहा.

### पुढील २४ तास
- ...

### नागरिकांसाठी सूचना
- विजा चमकत असताना झाडाखाली थांबू नका.
- ...
""", encoding="utf-8")
    print("New alert:", path)


def new_blog(slug):
    if not re.fullmatch(r"[a-z0-9-]+", slug):
        raise SystemExit("Give the post slug in lowercase letters, digits and -, e.g. monsoon-2026")
    today = date.today()
    path = CONTENT / "blog" / f"{today:%Y-%m-%d}-{slug}.md"
    if path.exists():
        raise SystemExit(f"{path} already exists")
    path.write_text(f"""---
title:
  en: "Post title"
  mr: "लेखाचे शीर्षक"
date: {today:%Y-%m-%d}
summary:
  en: "One-sentence summary of the post (shown in the list)."
  mr: "लेखाचा एका वाक्यातील सारांश (यादीत दिसतो)."
tags: [{{en: Weather, mr: हवामान}}]
draft: true              # remove this line (or set false) to publish
---

Write the English post here. You can use **bold**, *italics*, lists, [links](https://mausam.imd.gov.in) and images:

![Image description](../../static/images/image.jpg)

<!-- mr -->

येथे मराठी लेख लिहा (हा भाग काढून टाकल्यास इंग्रजी लेख दोन्ही भाषांत दिसतो).
""", encoding="utf-8")
    print("New post:", path)


HAWAMAN_COLOR = {1: "red", 2: "orange", 3: "yellow", 4: "green"}     # IMD district warning colour codes
# IMD district warning codes (same codes as WARNING_MR in hawaman_maharashtra/content/phrases.py)
WARNING_EN = {2: "Heavy rain", 3: "Snowfall", 4: "Thunderstorm & lightning", 5: "Hailstorm",
              6: "Dust storm", 7: "Dust raising winds", 8: "Strong surface winds", 9: "Heat wave",
              10: "Hot & humid", 11: "Warm night", 12: "Cold wave", 13: "Cold day",
              14: "Ground frost", 15: "Dense fog", 16: "Very heavy rain", 17: "Extremely heavy rain"}
REGIONS = [bi("Konkan", "कोकण"), bi("Madhya Maharashtra", "मध्य महाराष्ट्र"),
           bi("Marathwada", "मराठवाडा"), bi("Vidarbha", "विदर्भ")]
HORIZON = {"short": bi("Short-range analysis (1–3 days)", "अल्पकालीन विश्लेषण (१–३ दिवस)"),
           "medium": bi("Medium-range analysis (4–10 days)", "मध्यम कालावधी विश्लेषण (४–१० दिवस)"),
           "extended": bi("Extended-range analysis (2–4 weeks)", "विस्तारित कालावधी विश्लेषण (२–४ आठवडे)"),
           "seasonal": bi("Seasonal analysis (1–3 months)", "हंगामी विश्लेषण (१–३ महिने)")}
SLIDES = {"cover": bi("Cover", "मुखपृष्ठ"), "short_range": bi("Short-range analysis", "अल्पकालीन विश्लेषण"),
          "satellite": bi("Satellite view", "उपग्रह निरीक्षण"),
          "circulation_today": bi("Today's wind circulation", "आजची वाऱ्यांची स्थिती"),
          "medium_range": bi("Medium-range analysis", "मध्यम कालावधी विश्लेषण"),
          "circulation_week": bi("This week's wind circulation", "आठवड्याची वाऱ्यांची स्थिती"),
          "extended_range": bi("Extended-range analysis", "विस्तारित कालावधी विश्लेषण"),
          "seasonal": bi("Seasonal analysis", "हंगामी विश्लेषण"), "outro": bi("Closing", "समारोप")}

# the site presents the pipeline output as analysis, never as a forecast ("अंदाज")
ANALYSIS_WORDS = [
    ("हा मॉडेल-आधारित अंदाज आहे", "हे मॉडेल-आधारित विश्लेषण आहे"),
    ("महाराष्ट्राचा हवामान अंदाज", "महाराष्ट्राचे हवामान विश्लेषण"),
    ("या काळाचा अंदाज", "या काळाचे विश्लेषण"),
    ("संभाव्य अंदाजांवर", "संभाव्य मॉडेल-परिणामांवर"),
    ("विस्तारित अंदाज नकाशे", "विस्तारित कालावधी नकाशे"),
]


def analysis_words(text):
    for a, b in ANALYSIS_WORDS:
        text = text.replace(a, b)
    text = re.sub(r"पैकी (\S+) अंदाज\)", r"पैकी \1 सदस्य)", text)
    return re.sub(r"अंदाज(?![\u093e-\u094d\u0902])", "विश्लेषण", text)    # keep अंदाजे (approximately)


def hawaman(day=None):
    """Turn the hawaman-vidnyan daily output (tweet, reel slides, discussion) into content/alerts/<date>-hawaman.md.
    The pipeline writes Marathi only: labels are bilingual, the body stays Marathi (lang: mr)."""
    import json
    import os                                   # HAWAMAN_DIR is set by the GitHub Actions workflow
    from PIL import Image
    site = yaml.safe_load((ROOT / "site.yaml").read_text(encoding="utf-8"))
    src = Path(os.environ.get("HAWAMAN_DIR") or site.get("hawaman_dir", r"D:\01_Research_Projects\hawaman-vidnyan"))
    out_root = src / "twitter" / "output"
    day = day or (max(p.name for p in out_root.iterdir() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name))
                  if out_root.exists() else None)
    pkg = out_root / str(day)
    if not day or not (pkg / "thread.txt").exists():
        raise SystemExit(f"hawaman-vidnyan tweet not found: {pkg}")
    d = date.fromisoformat(day)
    meta = json.loads((pkg / "meta.json").read_text(encoding="utf-8")) if (pkg / "meta.json").exists() else {}
    runs = sorted((src / "data" / "runs").glob(f"{d:%Y%m%d}_*"))
    run = runs[-1] if runs else None

    # IMD warnings for that day decide the colour; without them the alert is "info" (model-based analysis only)
    level, regions, hazards = "info", list(REGIONS), []
    facts = run / "processed" / "facts.json" if run else None
    warn = json.loads(facts.read_text(encoding="utf-8")).get("warnings", {}) if facts and facts.exists() else {}
    wday = next((w for w in warn.get("days", []) if w["date"] == day), None) if warn.get("available") else None
    if wday and wday["districts"]:
        sys.path.insert(0, str(src / "src"))
        from hawaman_maharashtra.content.phrases import WARNING_MR
        level = HAWAMAN_COLOR.get(wday["max_color"], "green")
        hit = [x for x in wday["districts"] if x["color"] < 4]
        regions = [bi(x.get("district") or x["name_mr"], x["name_mr"]) for x in hit] or regions
        codes = sorted({c for x in hit for c in x["codes"] if WARNING_MR.get(c)})
        hazards = [bi(WARNING_EN.get(c, WARNING_MR[c]), WARNING_MR[c]) for c in codes]

    thread = analysis_words((pkg / "thread.txt").read_text(encoding="utf-8"))
    parts = [s.strip() for s in re.split(r"\n-{3,}\n", thread) if s.strip()]
    first = parts[0].splitlines()
    title = re.sub(r"^\W+", "", first[0]).strip() or "महाराष्ट्र हवामान विश्लेषण"
    horizon = HORIZON.get(meta.get("horizon"), bi("Weather analysis", "हवामान विश्लेषण"))

    def lines(s):                       # keep tweet line breaks, drop hashtag lines
        return "  \n".join(l for l in s.splitlines() if not l.startswith("#"))

    img_dir = ROOT / "static" / "images" / "alerts" / day
    rel = f"static/images/alerts/{day}"

    def save(src_img, dest, box):
        dest.parent.mkdir(parents=True, exist_ok=True)
        im = Image.open(src_img).convert("RGB")
        im.thumbnail(box)
        im.save(dest, quality=80, optimize=True)

    imgs = []
    for name in meta.get("media") or sorted(p.name for p in pkg.glob("[0-9]_*.jpg")):
        if (pkg / name).exists():
            save(pkg / name, img_dir / name, (1200, 1200))
            imgs.append(name)

    # reel slides (1080×1920) → 540×960 gallery images
    slides = []
    for f in sorted((run / "graphics" / "instagram").glob("*.jpg")) if run else []:
        save(f, img_dir / "reel" / f.name, (540, 960))
        key = re.sub(r"^\d+_|(_slide)?$", "", f.stem)
        slides.append({"src": f"{rel}/reel/{f.name}", "label": SLIDES.get(key, key.replace("_", " "))})

    # the same slides as one slow, looping GIF (360×640, 4 s per slide; ~0.6 MB)
    gif = None
    if slides:
        frames = [Image.open(img_dir / "reel" / Path(s["src"]).name).convert("RGB").resize((360, 640), Image.LANCZOS)
                  .quantize(colors=160, method=Image.Quantize.MEDIANCUT) for s in slides]
        frames[0].save(img_dir / "reel.gif", save_all=True, append_images=frames[1:], duration=4000, loop=0, optimize=True)
        gif = f"{rel}/reel.gif"

    body = [f"**{horizon['mr']}** · वैयक्तिक विश्लेषणावर आधारित", "", lines("\n".join(first[1:])), ""]
    if imgs:
        body += [f"![{horizon['mr']}](../../{rel}/{imgs[0]})", ""]
    if len(parts) > 1:
        body += ["### हवामानामागचे कारण", ""] + [lines(s) + "\n" for s in parts[1:]]
    body += [f"![वाऱ्यांची स्थिती व दाब प्रणाली](../../{rel}/{n})\n" for n in imgs[1:]]
    disc = run / "content" / "marathi_discussion.txt" if run else None
    if disc and disc.exists():
        text = analysis_words(disc.read_text(encoding="utf-8")).strip().splitlines()
        if text and not text[0].startswith("#"):
            text = text[1:]                                   # first line repeats the page title/date
        text = [("### " + l.lstrip("# ").strip()) if l.startswith("#") else l for l in text]
        body += ["", "## सविस्तर हवामान विश्लेषण", ""] + text
    tw = site.get("contact", {}).get("twitter")
    if tw:
        body += ["", f"दैनंदिन अपडेटसाठी X वर फॉलो करा: [@{tw.rstrip('/').rsplit('/', 1)[-1]}]({tw})"]

    q = lambda v: json.dumps(v, ensure_ascii=False)
    valid = bi(en_date(d) + " — " + horizon["en"], mr_date(d) + " — " + horizon["mr"])
    front = [f"title: {q(title)}", f"date: {day}", f"level: {level}", "lang: mr", f"valid: {q(valid)}",
             f"regions: {q(regions)}", f"hazards: {q(hazards)}", f"slides: {q(slides)}", f"gif: {q(gif)}", "source: hawaman-vidnyan"]
    dest = CONTENT / "alerts" / f"{day}-hawaman.md"
    dest.write_text("---\n" + "\n".join(front) + "\n---\n\n" + "\n".join(body).strip() + "\n", encoding="utf-8")
    print(f"hawaman-vidnyan analysis ({level}, {len(slides)} slides): {dest}")
    return day


def push(msg):
    import subprocess
    run = lambda *a: subprocess.run(["git", *a], cwd=ROOT, check=True)
    run("add", "-A")
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        print("No changes; nothing pushed"); return
    run("commit", "-q", "-m", msg)
    run("pull", "-q", "--rebase")             # laptop and GitHub Actions may both publish
    run("push", "-q")
    print("Pushed to GitHub")


def serve(port=8000):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(OUT))
    print(f"Preview: http://localhost:{port}   (Ctrl+C to stop)")
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
            push(f"Alert {day} (hawaman-vidnyan)")
    elif cmd == "forecast":                   # called by extreme-rainfall-aiera after it writes latest.json
        build()
        if "--push" in sys.argv:
            fc = load_forecast()
            push(f"Experimental AI rain forecast {fc['base_date'] if fc else ''}".strip())
    elif cmd == "new-blog":
        if len(args) < 2:
            raise SystemExit("e.g. python build.py new-blog monsoon-2026")
        new_blog(args[1])
    else:
        raise SystemExit(__doc__)
