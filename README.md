# वैयक्तिक संकेतस्थळ — अरविंद पागिरे

मराठी प्रोफाइल, ब्लॉग आणि दैनंदिन हवामान इशाऱ्यांसाठी साधे (static) संकेतस्थळ.
मजकूर साध्या Markdown फाइल्समध्ये लिहायचा; `python build.py` पूर्ण संकेतस्थळ `docs/` मध्ये तयार करते.

सर्व आदेश या फोल्डरमधून चालवा (Anaconda Prompt / VS Code terminal):

```
conda activate myenv
cd D:\06_Personal\03_Portfolio
```

## दैनंदिन हवामान इशारा (रोजचे काम)

1. `python build.py new-alert` — `content/alerts/` मध्ये आजच्या तारखेची फाइल तयार होते.
2. ती फाइल उघडून शीर्षक, `level`, जिल्हे, धोके आणि मजकूर लिहा.
   - `level`: `green` (हिरवा), `yellow` (पिवळा), `orange` (नारंगी), `red` (लाल)
   - प्रसिद्ध करण्यासाठी `draft: true` ही ओळ काढा.
3. `python build.py serve` — http://localhost:8000 वर तपासा (Ctrl+C ने थांबवा).
4. प्रसिद्ध करा (खाली "ऑनलाइन प्रसिद्ध करणे" पाहा).

## नवीन ब्लॉग लेख

```
python build.py new-blog monsoon-2026      (नाव इंग्रजी लहान अक्षरांत; ते लेखाच्या URL मध्ये येते)
```

`content/blog/` मधील नवीन फाइलमध्ये लेख लिहा. Markdown मध्ये `**ठळक**`, `### उपशीर्षक`,
`- यादी`, `[दुवा](https://...)`, `> अवतरण` आणि तक्ते वापरता येतात. चित्रे `static/images/` मध्ये
ठेवा आणि लेखात `![वर्णन](../../static/images/nav.jpg)` असे लिहा.

मसुदे (`draft: true`) पाहण्यासाठी: `python build.py serve --drafts`. सामान्य `build` मध्ये मसुदे प्रसिद्ध होत नाहीत.

## प्रोफाइल बदलणे

नाव, पद, अनुभव, शिक्षण, प्रकाशने, संपर्क — सर्व `site.yaml` मध्ये आहे. तुमचा फोटो
`static/images/profile.jpg` या नावाने ठेवा (चौरस, किमान 400×400). फोटो नसल्यास "अ" आद्याक्षर दिसते.

## ऑनलाइन प्रसिद्ध करणे (GitHub Pages — मोफत)

पहिल्यांदा एकदाच:
1. github.com वर नवीन repository तयार करा, उदा. `arvindpagire.github.io` (हे नाव ठेवल्यास पत्ता
   `https://arvindpagire.github.io` होतो; `arvindpagire` ऐवजी तुमचे GitHub username).
2. या फोल्डरमध्ये:
   ```
   git init -b main
   git add .
   git commit -m "Marathi personal website"
   git remote add origin https://github.com/<username>/<username>.github.io.git
   git push -u origin main
   ```
3. GitHub वर Settings → Pages → Source: "Deploy from a branch", Branch: `main`, Folder: `/docs` → Save.

त्यानंतर दररोज:
```
python build.py
git add .
git commit -m "इशारा 2026-10-25"
git push
```
एक-दोन मिनिटांत संकेतस्थळ अद्ययावत होते. (`docs/` कोणत्याही इतर web host वर — Netlify, स्वतःचा सर्व्हर — थेट अपलोड करता येतो.)

## फोल्डर रचना

```
site.yaml            प्रोफाइल माहिती
content/blog/        ब्लॉग लेख (.md)
content/alerts/      हवामान इशारे (.md)
templates/           HTML साचे (Jinja2)
static/css/          डिझाइन (style.css)
static/images/       फोटो व चित्रे
build.py             संकेतस्थळ तयार करणारी स्क्रिप्ट
docs/                तयार झालेले संकेतस्थळ (हाताने बदलू नका — प्रत्येक build ला नव्याने तयार होते)
_archive_flask_v1/   जुने Flask संकेतस्थळ (संदर्भासाठी)
```

आवश्यक Python packages: `jinja2`, `mistune`, `pyyaml` (myenv मध्ये आधीच आहेत).

---

**English summary.** Static Marathi site: profile in `site.yaml`, posts and alerts as Markdown in
`content/`. `python build.py` renders to `docs/` (serve with GitHub Pages from `/docs`).
`new-alert` / `new-blog <slug>` scaffold files; `serve [--drafts]` previews on localhost:8000.
Alert `level` follows IMD colour codes (green/yellow/orange/red).
