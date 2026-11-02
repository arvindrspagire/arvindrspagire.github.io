"""
Real-time upper-air (radiosonde) analysis over India, from the University of Wyoming sounding archive.

    python upperair.py                  latest ascent (00 or 12 UTC) -> content/upperair/latest.json + SVGs
    python upperair.py 2026-11-02T00    a given ascent (UTC)
    python upperair.py coast            (once) coastline for the maps, from Natural Earth via cartopy
    python upperair.py redraw           recompute maps, text and tiles from the saved latest.json (no download)

One request per station (TEXT:LIST = profile + Wyoming's indices), one station at a time with a pause:
the server answers "too busy" to parallel requests. Everything numeric happens here (numpy, scipy,
matplotlib); build.py only reads the JSON and the ready-made SVGs, so building the site needs no numpy.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import html
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "content" / "upperair"
SVG_DIR = ROOT / "static" / "upperair"
URL = "https://weather.uwyo.edu/wsgi/sounding"
UA = "arvindrspagire.github.io upper-air analysis (contact: arvindrspagire@gmail.com)"
PAUSE = 2.0                                  # seconds between requests

KT = 1.943844                                # m/s -> knots
G, RD, CP, LV, EPS, OMEGA = 9.80665, 287.04, 1004.0, 2.501e6, 0.622, 7.292e-5

# IMD radiosonde / rawinsonde network (WMO id, English, मराठी, lat, lon); coordinates are replaced by the
# ones in the sounding when it arrives. Stations that do not report on a day are simply skipped.
STATIONS = [
    (42027, "Srinagar", "श्रीनगर", 34.08, 74.83), (42101, "Patiala", "पतियाळा", 30.33, 76.47),
    (42165, "Bikaner", "बिकानेर", 28.00, 73.30), (42182, "New Delhi", "नवी दिल्ली", 28.58, 77.20),
    (42314, "Dibrugarh", "दिब्रुगड", 27.48, 95.02), (42339, "Jodhpur", "जोधपूर", 26.30, 73.02),
    (42361, "Gwalior", "ग्वाल्हेर", 26.23, 78.25), (42369, "Lucknow", "लखनौ", 26.75, 80.88),
    (42379, "Gorakhpur", "गोरखपूर", 26.75, 83.37), (42397, "Siliguri", "सिलिगुडी", 26.63, 88.33),
    (42410, "Guwahati", "गुवाहाटी", 26.10, 91.58), (42492, "Patna", "पाटणा", 25.60, 85.10),
    (42623, "Imphal", "इम्फाळ", 24.77, 93.90), (42647, "Ahmedabad", "अहमदाबाद", 23.07, 72.63),
    (42667, "Bhopal", "भोपाळ", 23.28, 77.35), (42701, "Ranchi", "रांची", 23.32, 85.32),
    (42724, "Agartala", "आगरतळा", 23.88, 91.25), (42809, "Kolkata", "कोलकाता", 22.65, 88.45),
    (42867, "Nagpur", "नागपूर", 21.10, 79.05), (42874, "Raipur", "रायपूर", 21.22, 81.65),
    (42895, "Balasore", "बालासोर", 21.52, 86.93), (42971, "Bhubaneswar", "भुवनेश्वर", 20.25, 85.83),
    (43003, "Mumbai", "मुंबई", 19.11, 72.85), (43014, "Chhatrapati Sambhajinagar", "छत्रपती संभाजीनगर", 19.85, 75.40),
    (43041, "Jagdalpur", "जगदलपूर", 19.08, 82.03), (43063, "Pune", "पुणे", 18.53, 73.85),
    (43128, "Hyderabad", "हैदराबाद", 17.45, 78.47), (43150, "Visakhapatnam", "विशाखापट्टणम", 17.72, 83.23),
    (43185, "Machilipatnam", "मछलीपट्टणम", 16.20, 81.15), (43192, "Goa", "गोवा", 15.48, 73.82),
    (43279, "Chennai", "चेन्नई", 13.00, 80.18), (43285, "Mangaluru", "मंगळुरू", 12.92, 74.83),
    (43295, "Bengaluru", "बंगळुरू", 12.97, 77.58), (43311, "Amini", "अमिनी", 11.12, 72.73),
    (43333, "Port Blair", "पोर्ट ब्लेअर", 11.67, 92.72), (43346, "Karaikal", "कराईकल", 10.92, 79.83),
    (43353, "Kochi", "कोची", 9.95, 76.27), (43369, "Minicoy", "मिनिकॉय", 8.30, 73.15),
    (43371, "Thiruvananthapuram", "तिरुवनंतपुरम", 8.48, 76.95),
]

STD = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100]       # standard isobaric levels (hPa)
MAP_LEVELS = [850, 700, 500, 200]
# map box (plate carrée, cos-lat scaled)
LON0, LON1, LAT0, LAT1 = 64.0, 100.0, 4.0, 38.0
MAP_W = 560
_K = math.cos(math.radians((LAT0 + LAT1) / 2))
_S = MAP_W / ((LON1 - LON0) * _K)
MAP_H = round((LAT1 - LAT0) * _S)
MR_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")


def bi(en, mr):
    return {"en": en, "mr": mr}


def mr(s):
    return str(s).translate(MR_DIGITS)


def xy(lon, lat):
    return (lon - LON0) * _K * _S, (LAT1 - lat) * _S


# ------------------------------------------------------------------ fetch & parse
def fetch(wmo, t):
    q = urllib.parse.urlencode({"datetime": t.strftime("%Y-%m-%d %H:%M:%S"), "id": wmo,
                                "src": "UNKNOWN", "type": "TEXT:LIST"})
    for wait in (PAUSE, 15, 40, 90):
        time.sleep(wait)
        try:
            req = urllib.request.Request(f"{URL}?{q}", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                text = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code != 503:                 # 400/404: no sounding for this station and time
                return None
            print(f"  {wmo}: server busy; retrying")
            continue
        except OSError as e:
            print(f"  {wmo}: {e}; retrying")
            continue
        if "too busy" in text.lower():
            print(f"  {wmo}: server busy; retrying")
            continue
        return text
    return None


def parse(page):
    """Profile columns + Wyoming's sounding indices from a TEXT:LIST page; None if no sounding."""
    m = re.search(r"<PRE>(.*?)</PRE>", page, re.S)
    if not m:
        return None
    rows = []
    for line in m.group(1).splitlines():
        if not re.match(r"^\s*\d", line):
            continue
        cells = [line[i:i + 7].strip() for i in range(0, 77, 7)]
        try:
            rows.append([float(c) if c else np.nan for c in cells])
        except ValueError:
            continue
    if len(rows) < 5:
        return None
    a = np.array(rows)
    _, keep = np.unique(-a[:, 0], return_index=True)             # one row per pressure, surface first
    a = a[np.sort(keep)]
    a = a[np.argsort(-a[:, 0], kind="stable")]
    idx = {k: float(v) for k, v in re.findall(
        r"<TD>(\w+)</TD>\s*<TD>[^<]*</TD>\s*<TD[^>]*>\s*(-?[\d.]+)\s*</TD>", page)}
    name = re.search(r"<H3>([^<]+)</H3>", page)
    return dict(p=a[:, 0], z=a[:, 1], t=a[:, 2], td=a[:, 3], rh=a[:, 4], drct=a[:, 6], sped=a[:, 7],
                idx=idx, name=html.unescape(name.group(1)).strip() if name else "")


def interp(prof, key, p):
    """Value of prof[key] at pressure p (log-p interpolation, inside the observed range only)."""
    v, pp = prof[key], prof["p"]
    ok = np.isfinite(v) & np.isfinite(pp)
    if ok.sum() < 2:
        return np.nan
    pp, v = pp[ok], v[ok]
    if p > pp.max() + 1e-6 or p < pp.min() - 1e-6:
        return np.nan
    return float(np.interp(-np.log(p), -np.log(pp), v))


def wind_uv(prof):
    rad = np.radians(prof["drct"])
    prof["u"] = -prof["sped"] * np.sin(rad)
    prof["v"] = -prof["sped"] * np.cos(rad)


def to_dir_spd(u, v):
    spd = math.hypot(u, v)
    d = (math.degrees(math.atan2(-u, -v)) + 360) % 360
    return d, spd


# ------------------------------------------------------------------ thermodynamics (parcel for the Skew-T)
def es(tc):
    return 6.112 * np.exp(17.67 * tc / (tc + 243.5))


def moist_lapse(t_k, p):
    rs = EPS * es(t_k - 273.15) / (p - es(t_k - 273.15))
    return (RD * t_k + LV * rs) / (p * (CP + LV ** 2 * rs * EPS / (RD * t_k ** 2)))


def parcel_path(prof):
    """Most-unstable parcel (max θe in the lowest 300 hPa) lifted dry to the LCL, then pseudo-adiabatically.
    Returns (p, T °C) or None."""
    p, t, td = prof["p"], prof["t"], prof["td"]
    ok = np.isfinite(t) & np.isfinite(td) & (p >= p[0] - 300)
    if ok.sum() < 2:
        return None
    pk, tk, dk = p[ok], t[ok] + 273.15, td[ok] + 273.15
    r = EPS * es(dk - 273.15) / (pk - es(dk - 273.15))
    tl = 1 / (1 / (dk - 56) + np.log(tk / dk) / 800) + 56                       # Bolton (1980)
    theta_e = tk * (1000 / pk) ** (0.2854 * (1 - 0.28 * r)) * np.exp((3.376 / tl - 0.00254) * r * 1e3 * (1 + 0.81 * r))
    i = int(np.argmax(theta_e))
    p0, t0, tlcl = pk[i], tk[i], tl[i]
    plcl = p0 * (tlcl / t0) ** (CP / RD)
    out = [(p0, t0 - 273.15)]
    for pp in np.arange(p0 - 10, plcl, -10):
        out.append((pp, t0 * (pp / p0) ** (RD / CP) - 273.15))
    out.append((plcl, tlcl - 273.15))
    tt, pp = tlcl, plcl
    while pp > 100:
        dp = -min(10, pp - 100)
        k1 = moist_lapse(tt, pp)
        k2 = moist_lapse(tt + k1 * dp / 2, pp + dp / 2)
        tt, pp = tt + k2 * dp, pp + dp
        out.append((pp, tt - 273.15))
    return out


# ------------------------------------------------------------------ station diagnostics
def kt(x):
    return None if x is None or not np.isfinite(x) else round(x * KT)


def r1(x, n=1):
    return None if x is None or not np.isfinite(x) else round(float(x), n)


def station_analysis(st, prof):
    wmo, name_en, name_mr, lat, lon = st
    wind_uv(prof)
    lat, lon = prof["idx"].get("SLAT", lat), prof["idx"].get("SLON", lon)
    psfc, zsfc = float(prof["p"][0]), float(prof["z"][0])
    levels = {}
    for p in STD:
        if p > psfc:
            continue
        u, v = interp(prof, "u", p), interp(prof, "v", p)
        rec = dict(z=r1(interp(prof, "z", p), 0), t=r1(interp(prof, "t", p)), td=r1(interp(prof, "td", p)),
                   rh=r1(interp(prof, "rh", p), 0))
        if np.isfinite(u) and np.isfinite(v):
            d, s = to_dir_spd(u, v)
            rec.update(u=round(u, 1), v=round(v, 1), dir=round(d / 5) * 5 % 360, kt=kt(s))
        levels[str(p)] = rec

    def uv(p):
        x = levels.get(str(p), {})
        return (x["u"], x["v"]) if "u" in x else None

    # winds in layers
    def layer_max(pbot, ptop):
        sel = (prof["p"] <= pbot) & (prof["p"] >= ptop) & np.isfinite(prof["sped"])
        if not sel.any():
            return None
        i = np.flatnonzero(sel)[np.argmax(prof["sped"][sel])]
        return dict(p=float(prof["p"][i]), kt=kt(prof["sped"][i]), dir=int(prof["drct"][i]),
                    u=round(float(prof["u"][i]), 1))

    jet = layer_max(400, 100)
    llj = layer_max(min(psfc, 1000), 700)
    shear = None
    if uv(850) and uv(200):
        shear = kt(math.hypot(uv(200)[0] - uv(850)[0], uv(200)[1] - uv(850)[1]))
    # 0-6 km bulk shear
    z_agl = prof["z"] - zsfc
    shear06 = None
    if np.isfinite(prof["u"][0]) and np.nanmax(z_agl) > 6000:
        okw = np.isfinite(prof["u"])
        u6 = np.interp(6000, z_agl[okw], prof["u"][okw]); v6 = np.interp(6000, z_agl[okw], prof["v"][okw])
        shear06 = kt(math.hypot(u6 - prof["u"][0], v6 - prof["v"][0]))
    # freezing level
    frz = None
    okt = np.isfinite(prof["t"])
    tt_, zz_ = prof["t"][okt], prof["z"][okt]
    for i in range(1, len(tt_)):
        if tt_[i - 1] > 0 >= tt_[i]:
            frz = round(float(zz_[i - 1] + (zz_[i] - zz_[i - 1]) * tt_[i - 1] / (tt_[i - 1] - tt_[i])))
            break
    # mid-level humidity 850-500
    rhs = [interp(prof, "rh", p) for p in range(850, 499, -50) if p <= psfc]
    rh_mid = r1(np.nanmean(rhs), 0) if np.isfinite(rhs).any() else None
    # geostrophic temperature advection in 850-500 hPa from the turning of the wind (thermal wind), north of 18°N
    adv = None
    if lat >= 18 and uv(850) and uv(500):
        (u1, v1), (u2, v2) = uv(850), uv(500)
        f = 2 * OMEGA * math.sin(math.radians(lat))
        adv = round(-(f / (RD * math.log(850 / 500))) * (u1 * v2 - v1 * u2) * 86400, 1)    # K/day
    ix = prof["idx"]
    k = ix.get("KINX")
    cape = ix.get("MUCAPE")
    potential = None
    if k is not None:
        potential = 0 if k < 20 else 1 if k < 26 else 2 if k < 31 else 3 if k < 36 else 4
        if cape is not None and cape >= 1500:
            potential = max(potential, 3)

    # profile for the Skew-T: surface + every 10 hPa
    grid = [psfc] + [p for p in range(int(psfc // 10 * 10), 99, -10) if p < psfc]
    sk = [[p, r1(interp(prof, "t", p)), r1(interp(prof, "td", p))] for p in grid]
    return dict(
        wmo=wmo, name=bi(name_en, name_mr), wy_name=prof["name"], lat=round(lat, 2), lon=round(lon, 2),
        elev=round(zsfc), psfc=psfc, top=float(prof["p"][-1]), nlev=len(prof["p"]),
        levels=levels, jet=jet, llj=llj, shear_850_200=shear, shear_0_6=shear06, frz=frz, rh_mid=rh_mid, adv=adv,
        k=k, tt=ix.get("TOTL"), li=ix.get("LFVT"), show=ix.get("SHOW"), cape=cape, cin=ix.get("MUCIN"),
        dcape=ix.get("DCAPE"), pw=ix.get("PWAT"), lcl=ix.get("LCLZ"), sweat=ix.get("SWET"), potential=potential,
        _skew=sk, _parcel=parcel_path(prof),
    )


# ------------------------------------------------------------------ regional objective analysis
def barnes(slon, slat, val, glon, glat, spacing):
    """Two-pass Barnes analysis (Koch et al. 1983) on a lon-lat grid; distances in cos-lat scaled degrees."""
    kappa = 5.052 * (2 * spacing / math.pi) ** 2
    cosl = math.cos(math.radians(22))
    d2 = ((glon[..., None] - slon) * cosl) ** 2 + (glat[..., None] - slat) ** 2
    w = np.exp(-d2 / kappa)
    g0 = (w * val).sum(-1) / w.sum(-1)
    ds2 = ((slon[:, None] - slon) * cosl) ** 2 + (slat[:, None] - slat) ** 2
    ws = np.exp(-ds2 / kappa)
    at_st = (ws * val).sum(-1) / ws.sum(-1)
    w1 = np.exp(-d2 / (0.3 * kappa))
    return g0 + (w1 * (val - at_st)).sum(-1) / w1.sum(-1)


def analyse_level(stations, p):
    pts = [(s["lon"], s["lat"], s["levels"][str(p)]["u"], s["levels"][str(p)]["v"])
           for s in stations if "u" in s["levels"].get(str(p), {})]
    if len(pts) < 6:
        return None
    slon, slat, su, sv = (np.array(c) for c in zip(*pts))
    cosl = math.cos(math.radians(22))
    dd = np.sqrt(((slon[:, None] - slon) * cosl) ** 2 + (slat[:, None] - slat) ** 2) + np.eye(len(slon)) * 99
    spacing = float(np.median(dd.min(1)))
    lon = np.arange(LON0, LON1 + 0.01, 0.5)
    lat = np.arange(LAT0, LAT1 + 0.01, 0.5)
    glon, glat = np.meshgrid(lon, lat)
    gu, gv = barnes(slon, slat, su, glon, glat, spacing), barnes(slon, slat, sv, glon, glat, spacing)
    near = np.sqrt(((glon[..., None] - slon) * cosl) ** 2 + (glat[..., None] - slat) ** 2).min(-1)
    mask = near > max(3.5, 1.6 * spacing)                     # too far from any ascent: no analysis
    # relative vorticity (s^-1)
    a = 6.371e6
    dx = np.radians(0.5) * a * np.cos(np.radians(glat))
    dy = np.radians(0.5) * a
    zeta = np.gradient(gv, axis=1) / dx - np.gradient(gu, axis=0) / dy
    zeta[mask] = np.nan
    return dict(p=p, lon=lon, lat=lat, u=np.where(mask, np.nan, gu), v=np.where(mask, np.nan, gv), zeta=zeta,
                spacing=spacing, n=len(pts), stations=pts)


def nearest_names(stations, lon, lat, n=2):
    d = sorted(stations, key=lambda s: (s["lon"] - lon) ** 2 + (s["lat"] - lat) ** 2)
    return [s["name"] for s in d[:n]]


def circulation_centres(an, stations):
    """Local vorticity extrema; 'closed' if the analysed wind turns all the way round the centre."""
    z = an["zeta"]
    out = []
    from scipy.ndimage import maximum_filter, minimum_filter
    zf = np.nan_to_num(z, nan=0.0)
    for sign in (1, -1):
        zz = sign * zf
        peaks = (zz == maximum_filter(zz, size=9)) & (zz > 1.0e-5)
        for j, i in zip(*np.nonzero(peaks)):
            lo, la = float(an["lon"][i]), float(an["lat"][j])
            tang = []
            for ang in np.radians(np.arange(0, 360, 45)):
                ii, jj = i + round(4 * math.cos(ang)), j + round(4 * math.sin(ang))
                if not (0 <= jj < z.shape[0] and 0 <= ii < z.shape[1]) or not np.isfinite(an["u"][jj, ii]):
                    tang = None
                    break
                tang.append(-math.sin(ang) * an["u"][jj, ii] + math.cos(ang) * an["v"][jj, ii])   # counter-clockwise +
            closed = bool(tang) and all(sign * x > 0 for x in tang)
            out.append(dict(kind="cyclonic" if sign > 0 else "anticyclonic", lon=lo, lat=la,
                            zeta=round(float(z[j, i]) * 1e5, 1), closed=closed,
                            near=nearest_names(stations, lo, la)))
    out.sort(key=lambda c: -abs(c["zeta"]))
    return out[:6]


def ridge_line(an, lon_band=(70, 90)):
    """Latitude where the analysed zonal wind turns from easterly (south) to westerly (north): the subtropical
    ridge axis. Per grid column, and the median over the band."""
    pts = []
    for i, lo in enumerate(an["lon"]):
        col = an["u"][:, i]
        for j in range(len(col) - 1):
            if np.isfinite(col[j]) and np.isfinite(col[j + 1]) and col[j] < 0 <= col[j + 1]:
                la = an["lat"][j] + 0.5 * (-col[j]) / (col[j + 1] - col[j])
                pts.append((float(lo), float(la)))
                break
    band = [la for lo, la in pts if lon_band[0] <= lo <= lon_band[1]]
    return dict(points=smooth_line(pts), lat=round(float(np.median(band)), 1) if len(band) >= 5 else None)


def smooth_line(pts, gap=0.6):
    """A per-longitude line (lon, lat) without grid steps and single-column jumps: running median, then mean,
    within each run of adjacent columns; runs shorter than 3° are dropped."""
    from scipy.ndimage import median_filter, uniform_filter1d
    runs, cur = [], []
    for p in pts:
        if cur and p[0] - cur[-1][0] > gap:
            runs.append(cur)
            cur = []
        cur.append(p)
    runs.append(cur)
    out = []
    for r in runs:
        if len(r) < 7:
            continue
        lat = uniform_filter1d(median_filter(np.array([b for _, b in r]), size=9, mode="nearest"), size=7, mode="nearest")
        out += [(a, float(b)) for (a, _), b in zip(r, lat)]
    return out


# ------------------------------------------------------------------ SVG drawing
def barb(x, y, u, v, scale=1.0, cls="barb"):
    """Wind barb (knots) at screen point x, y; staff points to where the wind comes from, feathers on the
    clockwise side (northern hemisphere)."""
    spd = math.hypot(u, v) * KT
    if spd < 2.5:
        return f'<circle class="{cls}" cx="{x:.1f}" cy="{y:.1f}" r="{3 * scale:.1f}" fill="none"/>'
    dx, dy = -u / math.hypot(u, v), v / math.hypot(u, v)     # screen: toward the source (y down)
    L = 22 * scale
    px, py = -dy, dx                                         # clockwise side of the staff, on screen
    tipx, tipy = x + dx * L, y + dy * L
    parts = [f"M{x:.1f},{y:.1f}L{tipx:.1f},{tipy:.1f}"]
    n = int(round(spd / 5))
    flags, n = divmod(n, 10)
    full, half = divmod(n, 2)
    pos, step, F = 0.0, 4 * scale, 9 * scale
    for _ in range(flags):
        bx, by = tipx - dx * pos, tipy - dy * pos
        parts.append(f'M{bx:.1f},{by:.1f}L{bx + px * F:.1f},{by + py * F:.1f}L{bx - dx * step:.1f},{by - dy * step:.1f}Z')
        pos += step * 1.4
    for _ in range(full):
        bx, by = tipx - dx * pos, tipy - dy * pos
        parts.append(f"M{bx:.1f},{by:.1f}L{bx + px * F + dx * 3 * scale:.1f},{by + py * F + dy * 3 * scale:.1f}")
        pos += step
    if half:
        if pos == 0:
            pos = step
        bx, by = tipx - dx * pos, tipy - dy * pos
        parts.append(f"M{bx:.1f},{by:.1f}L{bx + px * F / 2 + dx * 1.5 * scale:.1f},{by + py * F / 2 + dy * 1.5 * scale:.1f}")
    return f'<path class="{cls}" d="{"".join(parts)}"/>'


def path_d(path, tf):
    """matplotlib Path -> SVG d, through tf(lon, lat) -> (x, y)."""
    out = []
    for (vx, vy), code in zip(path.vertices, path.codes if path.codes is not None else [1] + [2] * (len(path.vertices) - 1)):
        if code == 79:
            out.append("Z")
            continue
        x, y = tf(vx, vy)
        out.append(("M" if code == 1 else "L") + f"{x:.1f},{y:.1f}")
    return "".join(out)


ISOTACH = [(20, "#cde2fb"), (40, "#9ec5f4"), (60, "#6da7ec"), (80, "#3987e5"), (999, "#1c5cab")]   # kt, sequential blue


def grid_svg():
    g = []
    for lo in range(65, 100, 5):
        x, _ = xy(lo, 0)
        g.append(f'<line class="ua-grid" x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{MAP_H}"/>'
                 f'<text class="ua-tick" x="{x + 2:.1f}" y="{MAP_H - 4}">{lo}°E</text>')
    for la in range(5, 38, 5):
        _, y = xy(0, la)
        g.append(f'<line class="ua-grid" x1="0" y1="{y:.1f}" x2="{MAP_W}" y2="{y:.1f}"/>'
                 f'<text class="ua-tick" x="3" y="{y - 3:.1f}">{la}°N</text>')
    return "".join(g)


def map_svg(an, stations, centres, ridge, coast):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    p = an["p"]
    spd = np.hypot(an["u"], an["v"]) * KT
    fig, ax = plt.subplots()
    levels = [0, 10] + [b for b, _ in ISOTACH]
    cs = ax.contourf(an["lon"], an["lat"], np.where(np.isfinite(spd), spd, np.nan), levels=levels)
    shade = []
    for k, path in enumerate(cs.get_paths()):
        if k == 0 or not len(path.vertices):
            continue
        shade.append(f'<path d="{path_d(path, xy)}" fill="{ISOTACH[k - 1][1]}" fill-rule="evenodd"/>')
    u = np.ma.masked_invalid(an["u"] / np.cos(np.radians(an["lat"]))[:, None])
    v = np.ma.masked_invalid(an["v"])
    sp = ax.streamplot(an["lon"], an["lat"], u, v, density=1.5, arrowstyle="-", broken_streamlines=True)
    plt.close(fig)
    lines, cur = [], []
    for seg in sp.lines.get_segments():      # newer matplotlib: one polyline per streamline; older: 2-point pieces
        if len(seg) > 2:
            lines.append(list(seg))
            continue
        if cur and np.allclose(cur[-1], seg[0]):
            cur.append(seg[1])
        else:
            if len(cur) > 3:
                lines.append(cur)
            cur = [seg[0], seg[1]]
    if len(cur) > 3:
        lines.append(cur)
    stream, arrows = [], []
    for ln in lines:
        pts = [xy(a, b) for a, b in ln]
        stream.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts))
        if len(pts) > 8:
            m = len(pts) // 2
            (x0, y0), (x1, y1) = pts[m - 1], pts[m + 1]
            ang = math.degrees(math.atan2(y1 - y0, x1 - x0))
            arrows.append(f'<path class="ua-arrow" d="M-4,-3.5L3,0L-4,3.5" transform="translate({pts[m][0]:.1f},{pts[m][1]:.1f}) rotate({ang:.0f})"/>')
    grid = [grid_svg()]
    marks = []
    for s in stations:
        lv = s["levels"].get(str(p), {})
        x, y = xy(s["lon"], s["lat"])
        tip = (f'{s["name"]["en"]} · {p} hPa: {lv["dir"]:03d}°/{lv["kt"]} kt' if "u" in lv
               else f'{s["name"]["en"]} · {p} hPa: no wind')
        if "u" in lv:
            marks.append(f'<g class="ua-st"><title>{html.escape(tip)}</title><circle cx="{x:.1f}" cy="{y:.1f}" r="2.6"/>'
                         f'{barb(x, y, lv["u"], lv["v"])}</g>')
    rid = ""
    if ridge["points"]:
        segs, prev = [], None
        for lo, la in ridge["points"]:
            x, y = xy(lo, la)
            segs.append(("L" if prev is not None and abs(lo - prev) < 0.6 else "M") + f"{x:.1f},{y:.1f}")
            prev = lo
        rid = f'<path class="ua-ridge" d="{"".join(segs)}"/>'
    cen = []
    for c in centres:
        if not c["closed"]:
            continue
        x, y = xy(c["lon"], c["lat"])
        letter, cls = ("C", "ua-cyc") if c["kind"] == "cyclonic" else ("A", "ua-anti")
        cen.append(f'<text class="{cls}" x="{x:.1f}" y="{y + 7:.1f}" text-anchor="middle">{letter}</text>')
    return (f'<svg class="ua-map" viewBox="0 0 {MAP_W} {MAP_H}" role="img" aria-label="{p} hPa streamlines and winds">'
            f'<rect class="ua-sea" width="{MAP_W}" height="{MAP_H}"/>{"".join(shade)}'
            f'<path class="ua-coast" d="{coast}"/>{"".join(grid)}'
            f'<path class="ua-stream" d="{"".join(stream)}"/>{"".join(arrows)}{rid}{"".join(cen)}{"".join(marks)}</svg>')


def skewt_svg(s):
    """Small Skew-T log-p: temperature, dew point, most-unstable parcel, wind barbs (language-neutral)."""
    W, H, PAD, BW = 300, 330, 28, 46
    pb, pt = 1050.0, 100.0
    lb, lt = math.log(pb), math.log(pt)
    PW = W - BW

    def Y(p):
        return PAD / 2 + (H - PAD) * (math.log(p) - lt) / (lb - lt)

    def X(tc, p):
        return (tc + 40) / 90 * PW + (H - PAD / 2 - Y(p)) * 0.9

    def line(pts, cls):
        pts = [(p, t) for p, t in pts if t is not None]
        if len(pts) < 2:
            return ""
        return f'<path class="{cls}" d="M' + "L".join(f"{X(t, p):.1f},{Y(p):.1f}" for p, t in pts) + '"/>'

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="system-ui,sans-serif" font-size="9">',
           "<style>.g{stroke:#dde3ea;stroke-width:.7;fill:none}.ad{stroke:#e9c9a8;stroke-width:.7;fill:none}"
           ".t{stroke:#c62828;stroke-width:2;fill:none}.d{stroke:#2e7d32;stroke-width:2;fill:none}"
           ".pc{stroke:#1b2430;stroke-width:1.2;stroke-dasharray:4 3;fill:none}.barb{stroke:#1b2430;stroke-width:1.1;fill:#1b2430}"
           "text{fill:#5d6b7a}</style>",
           f'<clipPath id="c"><rect x="0" y="0" width="{PW}" height="{H - PAD / 2}"/></clipPath><g clip-path="url(#c)">']
    for tc in range(-100, 51, 10):
        out.append(f'<line class="g" x1="{X(tc, pb):.1f}" y1="{Y(pb):.1f}" x2="{X(tc, pt):.1f}" y2="{Y(pt):.1f}"/>')
    for th in range(270, 451, 20):
        out.append(line([(p, th * (p / 1000) ** 0.2854 - 273.15) for p in range(1050, 99, -25)], "ad"))
    for p in (1000, 850, 700, 500, 400, 300, 200, 150, 100):
        out.append(f'<line class="g" x1="0" y1="{Y(p):.1f}" x2="{PW}" y2="{Y(p):.1f}"/>')
    if s["_parcel"]:
        out.append(line(s["_parcel"], "pc"))
    out.append(line([(p, t) for p, t, _ in s["_skew"]], "t"))
    out.append(line([(p, d) for p, _, d in s["_skew"]], "d"))
    out.append("</g>")
    for p in (1000, 850, 700, 500, 300, 200, 100):
        out.append(f'<text x="2" y="{Y(p) - 2:.1f}">{p}</text>')
    for tc in (-30, -10, 10, 30):
        x = X(tc, pb)
        if 0 < x < PW:
            out.append(f'<text x="{x:.1f}" y="{H - 2}" text-anchor="middle">{tc}°</text>')
    out.append(f'<line class="g" x1="{PW + BW / 2}" y1="{Y(1000):.1f}" x2="{PW + BW / 2}" y2="{Y(100):.1f}"/>')
    for p in STD:
        lv = s["levels"].get(str(p), {})
        if "u" in lv:
            out.append(barb(PW + BW / 2, Y(p), lv["u"], lv["v"], scale=0.85 * BW / 2 / 22))
    out.append("</svg>")
    return "".join(out)


# ------------------------------------------------------------------ text: what the soundings say
DIR16_EN = ["northerly", "north-north-easterly", "north-easterly", "east-north-easterly", "easterly",
            "east-south-easterly", "south-easterly", "south-south-easterly", "southerly", "south-south-westerly",
            "south-westerly", "west-south-westerly", "westerly", "west-north-westerly", "north-westerly", "north-north-westerly"]
DIR8_MR = ["उत्तरेकडील", "ईशान्येकडील", "पूर्वेकडील", "आग्नेयेकडील", "दक्षिणेकडील", "नैऋत्येकडील", "पश्चिमेकडील", "वायव्येकडील"]


def dir_words(d):
    return bi(DIR16_EN[round(d / 22.5) % 16], DIR8_MR[round(d / 45) % 8])


def key(en, mr):
    """A headline finding (circulation, trough, jet, instability): shown first on the summary card."""
    return {"en": en, "mr": mr, "key": True}


def names(sts, n=4):
    sts = sts[:n]
    return bi(", ".join(s["name"]["en"] for s in sts), ", ".join(s["name"]["mr"] for s in sts))


def findings(stations, regional):
    """Plain-language summary of the circulation and stability (bilingual), ordered from low to upper levels."""
    out = []
    # 850 hPa flow by region: prevailing zonal sense + the strongest ascent (a vector mean hides opposing winds)
    for (lo, hi), reg in (((0, 16), bi("south peninsula (south of 16°N)", "दक्षिण द्वीपकल्प (१६°N च्या दक्षिणेस)")),
                          ((16, 24), bi("central India (16–24°N)", "मध्य भारत (१६–२४°N)")),
                          ((24, 90), bi("north India (north of 24°N)", "उत्तर भारत (२४°N च्या उत्तरेस)"))):
        sts = [s for s in stations if lo <= s["lat"] < hi and "u" in s["levels"].get("850", {})]
        if len(sts) < 3:
            continue
        n, ne, nw = len(sts), sum(s["levels"]["850"]["u"] < -1 for s in sts), sum(s["levels"]["850"]["u"] > 1 for s in sts)
        med = sorted(s["levels"]["850"]["kt"] for s in sts)[n // 2]
        top = max(sts, key=lambda s: s["levels"]["850"]["kt"])
        tw = top["levels"]["850"]
        if med < 8:
            sense = bi(f"mostly light winds (median {med} kt)", f"बहुतांश हलके वारे (मध्यक {mr(med)} नॉट)")
        elif ne >= 0.6 * n:
            sense = bi(f"easterly component at {ne} of {n} ascents", f"{mr(n)} पैकी {mr(ne)} स्थानकांवर पूर्वेकडून वारे")
        elif nw >= 0.6 * n:
            sense = bi(f"westerly component at {nw} of {n} ascents", f"{mr(n)} पैकी {mr(nw)} स्थानकांवर पश्चिमेकडून वारे")
        else:
            sense = bi("mixed directions", "मिश्र दिशांचे वारे")
        out.append(bi(f"850 hPa, {reg['en']}: {sense['en']}; strongest {dir_words(tw['dir'])['en']} {tw['kt']} kt at {top['name']['en']}.",
                      f"८५० hPa, {reg['mr']}: {sense['mr']}; सर्वाधिक {top['name']['mr']} येथे {dir_words(tw['dir'])['mr']} वारे, {mr(tw['kt'])} नॉट."))
    llj = sorted([s for s in stations if s["llj"] and (s["llj"]["kt"] or 0) >= 25], key=lambda s: -s["llj"]["kt"])
    if llj:
        s = llj[0]
        out.append(bi(f"Strong low-level winds (≥ 25 kt below 700 hPa) at {names(llj)['en']}; strongest {s['llj']['kt']} kt "
                      f"from {s['llj']['dir']}° near {s['llj']['p']:.0f} hPa at {s['name']['en']}.",
                      f"खालच्या स्तरात जोरदार वारे (७०० hPa खाली ≥ २५ नॉट): {names(llj)['mr']}; सर्वाधिक {mr(s['llj']['kt'])} नॉट "
                      f"({mr(s['llj']['dir'])}° कडून, सुमारे {mr(round(s['llj']['p']))} hPa) — {s['name']['mr']}."))
    for c in headline_centres(regional):
        p, nr = c["p"], c["near"]
        where_en = f"near {c['lat']:.0f}°N, {c['lon']:.0f}°E (between {nr[0]['en']} and {nr[1]['en']})"
        where_mr = f"{mr(round(c['lat']))}°N, {mr(round(c['lon']))}°E जवळ ({nr[0]['mr']} व {nr[1]['mr']} दरम्यान)"
        if c["closed"]:
            kind = bi("cyclonic circulation", "चक्रीय वाऱ्यांची स्थिती") if c["kind"] == "cyclonic" else bi("anticyclone", "प्रतिचक्रीय स्थिती")
            out.append((key if c["headline"] else bi)(f"{p} hPa: {kind['en']} in the objective analysis {where_en}.",
                                                      f"{mr(p)} hPa: वस्तुनिष्ठ विश्लेषणात {where_mr} {kind['mr']}."))
        else:
            z = f"{c['zeta']:.0f}"
            out.append(key(f"{p} hPa: marked cyclonic wind shear (trough; vorticity ≈ {z}×10⁻⁵ s⁻¹) {where_en} — "
                              f"not confirmed as a closed circulation by the surrounding ascents.",
                              f"{mr(p)} hPa: {where_mr} ठळक चक्रीय वाऱ्यांचा फरक (द्रोणीय स्थिती; भोवरा ≈ {mr(z)}×10⁻⁵ s⁻¹) — "
                              f"आजूबाजूच्या निरीक्षणांवरून बंद चक्रीय स्थिती असल्याची खात्री होत नाही."))
    for p in (500, 200):
        r = regional.get(str(p), {}).get("ridge_lat")
        if r is not None:
            out.append(bi(f"Subtropical ridge at {p} hPa (easterlies to the south, westerlies to the north) near {r:.1f}°N "
                          f"along 70–90°E.",
                          f"{mr(p)} hPa वर उपोष्णकटिबंधीय उच्च दाब पट्टा (दक्षिणेस पूर्वेकडील, उत्तरेस पश्चिमेकडील वारे) "
                          f"७०–९०°E दरम्यान सुमारे {mr(f'{r:.1f}')}°N वर."))
    jets = sorted([s for s in stations if s["jet"] and s["jet"]["u"] > 0 and (s["jet"]["kt"] or 0) >= 60],
                  key=lambda s: -s["jet"]["kt"])
    if jets:
        s = jets[0]
        out.append(key(f"Subtropical westerly jet: winds ≥ 60 kt aloft at {names(jets)['en']}; core {s['jet']['kt']} kt "
                      f"near {s['jet']['p']:.0f} hPa over {s['name']['en']}.",
                      f"उपोष्णकटिबंधीय पश्चिमी झोत वारे (jet stream): वरच्या स्तरात ≥ ६० नॉट — {names(jets)['mr']}; सर्वाधिक "
                      f"{mr(s['jet']['kt'])} नॉट (सुमारे {mr(round(s['jet']['p']))} hPa) — {s['name']['mr']}."))
    tej = sorted([s for s in stations if s["lat"] < 20 and "u" in s["levels"].get("150", {}) and s["levels"]["150"]["u"] <= -15],
                 key=lambda s: s["levels"]["150"]["u"])
    if len(tej) >= 2:
        s = tej[0]
        out.append(key(f"Tropical easterly jet: easterlies ≥ 30 kt at 150 hPa over {names(tej)['en']} "
                      f"(strongest {s['levels']['150']['kt']} kt at {s['name']['en']}).",
                      f"उष्णकटिबंधीय पूर्वी झोत वारे: १५० hPa वर ≥ ३० नॉट पूर्वेकडील वारे — {names(tej)['mr']} "
                      f"(सर्वाधिक {mr(s['levels']['150']['kt'])} नॉट, {s['name']['mr']})."))
    wy = regional.get("shear_index")
    if wy is not None:
        sense = (bi("monsoon-like (westerly below, easterly above)", "मान्सूनसारखी (खाली पश्चिमी, वर पूर्वी वारे)") if wy > 0 else
                 bi("reversed from the monsoon pattern (easterly below, westerly above)", "मान्सूनच्या उलट (खाली पूर्वी, वर पश्चिमी वारे)"))
        out.append(bi(f"Vertical shear of the zonal wind (u850 − u200, mean of ascents south of 20°N): {wy:+.0f} m/s — {sense['en']}.",
                      f"पूर्व-पश्चिम वाऱ्याचा उंचीनुसार फरक (u850 − u200, २०°N दक्षिणेकडील स्थानकांची सरासरी): {mr(f'{wy:+.0f}')} मी/से — {sense['mr']}."))
    unstable = sorted([s for s in stations if (s["potential"] or 0) >= 3], key=lambda s: -(s["k"] or 0))
    if unstable:
        out.append(key(f"Most unstable / moist columns (K-index ≥ 31 or MUCAPE ≥ 1500 J/kg): {names(unstable, 6)['en']} — "
                      f"conditions that favour thunderstorms if there is a trigger.",
                      f"सर्वाधिक अस्थिर / दमट वातावरण (K-निर्देशांक ≥ ३१ किंवा MUCAPE ≥ १५०० J/kg): {names(unstable, 6)['mr']} — "
                      f"उचल मिळाल्यास गडगडाटी वादळांना अनुकूल स्थिती."))
    else:
        out.append(bi("No ascent shows strong instability (all K-index < 31 and MUCAPE < 1500 J/kg).",
                      "कोणत्याही स्थानकावर तीव्र अस्थिरता नाही (सर्व ठिकाणी K-निर्देशांक < ३१ व MUCAPE < १५०० J/kg)."))
    pw = sorted([s for s in stations if s["pw"] is not None], key=lambda s: -s["pw"])
    if pw:
        out.append(bi(f"Precipitable water is highest at {pw[0]['name']['en']} ({pw[0]['pw']:.0f} mm) and lowest at "
                      f"{pw[-1]['name']['en']} ({pw[-1]['pw']:.0f} mm).",
                      f"अवक्षेपणक्षम पाणी (precipitable water) सर्वाधिक {pw[0]['name']['mr']} ({mr(round(pw[0]['pw']))} मिमी) व "
                      f"सर्वात कमी {pw[-1]['name']['mr']} ({mr(round(pw[-1]['pw']))} मिमी)."))
    adv = [s for s in stations if s["adv"] is not None and abs(s["adv"]) >= 1]
    warm = [s for s in adv if s["adv"] > 0]
    cold = [s for s in adv if s["adv"] < 0]
    if warm or cold:
        en, mrs = [], []
        if warm:
            en.append(f"warm advection (veering wind) at {names(warm)['en']}")
            mrs.append(f"उष्ण हवेचे वहन (उंचीनुसार वारे घड्याळाच्या दिशेने वळतात): {names(warm)['mr']}")
        if cold:
            en.append(f"cold advection (backing wind) at {names(cold)['en']}")
            mrs.append(f"थंड हवेचे वहन (वारे उलट दिशेने वळतात): {names(cold)['mr']}")
        out.append(bi("850–500 hPa thermal wind (north of 18°N): " + "; ".join(en) + ".",
                      "८५०–५०० hPa थर्मल वारे (१८°N च्या उत्तरेस): " + "; ".join(mrs) + "."))
    return out


def headline_centres(regional):
    """Circulations worth showing: closed ones, and strong low-level cyclonic shear (troughs), each trough once."""
    out = []
    for p in (850, 700, 500, 200):
        for c in regional.get(str(p), {}).get("centres", []):
            if c["closed"]:
                # an upper-level anticyclone is routine, not a headline
                out.append(dict(c, p=p, headline=c["kind"] == "cyclonic" or p >= 500))
            elif c["kind"] == "cyclonic" and p >= 700 and c["zeta"] >= 4:
                if any(not o["closed"] and abs(c["lat"] - o["lat"]) <= 2 and abs(c["lon"] - o["lon"]) <= 2 for o in out):
                    continue
                out.append(dict(c, p=p, headline=True))
    return out


# ------------------------------------------------------------------ at a glance: number tiles + key-features map
def tiles(stations, regional):
    """Headline numbers for the page and the summary card: value, label, short note (all bilingual)."""
    out = []
    jets = [s for s in stations if s["jet"] and s["jet"]["u"] > 0 and (s["jet"]["kt"] or 0) >= 60]
    if jets:
        s = max(jets, key=lambda s: s["jet"]["kt"])
        out.append(dict(value=bi(f"{s['jet']['kt']} kt", f"{mr(s['jet']['kt'])} नॉट"),
                        label=bi("Westerly jet core", "पश्चिमी झोत वारे"),
                        note=bi(f"{s['name']['en']} · {s['jet']['p']:.0f} hPa", f"{s['name']['mr']} · {mr(round(s['jet']['p']))} hPa")))
    r = regional.get("500", {}).get("ridge_lat")
    if r is not None:
        out.append(dict(value=bi(f"{r:.1f}°N", f"{mr(f'{r:.1f}')}°N"),
                        label=bi("Subtropical ridge, 500 hPa", "उच्च दाब पट्टा, ५०० hPa"),
                        note=bi("easterlies south, westerlies north", "दक्षिणेस पूर्वी, उत्तरेस पश्चिमी वारे")))
    lows = [c for c in headline_centres(regional) if c["headline"]]
    out.append(dict(value=bi(str(len(lows)), mr(len(lows))),
                    label=bi("Troughs / circulations", "द्रोणीय / चक्रीय स्थिती"),
                    note=bi(" · ".join(f"{c['p']}: {c['near'][0]['en']}" for c in lows[:2]) or "none",
                            " · ".join(f"{mr(c['p'])}: {c['near'][0]['mr']}" for c in lows[:2]) or "नाही")))
    hot = sorted([s for s in stations if (s["potential"] or 0) >= 3], key=lambda s: -(s["k"] or 0))
    out.append(dict(value=bi(f"{len(hot)} / {len(stations)}", f"{mr(len(hot))} / {mr(len(stations))}"),
                    label=bi("Stations favouring thunderstorms", "वादळास अनुकूल स्थानके"),
                    note=names(hot, 3) if hot else bi("none", "नाही")))
    pw = [s for s in stations if s["pw"] is not None]
    if pw:
        s = max(pw, key=lambda s: s["pw"])
        out.append(dict(value=bi(f"{s['pw']:.0f} mm", f"{mr(round(s['pw']))} मिमी"),
                        label=bi("Most moisture (precipitable water)", "सर्वाधिक बाष्प (PW)"), note=s["name"]))
    wy = regional.get("shear_index")
    if wy is not None:
        out.append(dict(value=bi(f"{wy:+.0f} m/s", f"{mr(f'{wy:+.0f}')} मी/से"),
                        label=bi("Monsoon shear (u850 − u200)", "मान्सून वारे फरक (u850 − u200)"),
                        note=bi("monsoon-like" if wy > 0 else "not monsoon-like",
                                "मान्सूनसारखा" if wy > 0 else "मान्सूनसारखा नाही")))
    return out


POT_COLORS = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"]     # ordinal blue: weak -> high potential


def jet_axis(an):
    """Latitude of the strongest westerly per longitude (north of 18°N), where it is ≥ 60 kt."""
    pts = []
    sel = an["lat"] >= 18
    for i, lo in enumerate(an["lon"]):
        col = np.where(sel, an["u"][:, i], np.nan)
        if np.isfinite(col).any() and np.nanmax(col) * KT >= 60:
            pts.append((float(lo), float(an["lat"][np.nanargmax(col)])))
    return pts


def summary_svg(stations, regional, analyses, coast):
    """One map with today's key features: jet axis, 500 hPa ridge, troughs / circulations, stations coloured by
    thunderstorm potential. Labels in both languages (CSS shows one)."""
    def lab(x, y, en, mr_, cls, anchor="start"):
        return (f'<text class="{cls} l-en" x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}">{html.escape(en)}</text>'
                f'<text class="{cls} l-mr" x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}">{html.escape(mr_)}</text>')

    def poly(pts):
        segs, prev = [], None
        for lo, la in pts:
            x, y = xy(lo, la)
            segs.append(("L" if prev is not None and abs(lo - prev) < 0.6 else "M") + f"{x:.1f},{y:.1f}")
            prev = lo
        return "".join(segs)

    g = [f'<rect class="ua-sea" width="{MAP_W}" height="{MAP_H}"/><path class="ua-coast" d="{coast}"/>{grid_svg()}']
    if "200" in analyses:
        pts = smooth_line(jet_axis(analyses["200"]))
        if len(pts) >= 6:
            g.append(f'<path class="ua-jet" d="{poly(pts)}" marker-end="url(#ua-jet-head)"/>')
            core = max((s for s in stations if s["jet"] and s["jet"]["u"] > 0), key=lambda s: s["jet"]["kt"])
            x, y = xy(*pts[len(pts) // 3])
            g.append(lab(x, y - 12, f"Jet {core['jet']['kt']} kt", f"झोत वारे {mr(core['jet']['kt'])} नॉट", "ua-jet-lab"))
    if "500" in analyses:
        rp = ridge_line(analyses["500"])["points"]
        if rp:
            g.append(f'<path class="ua-ridge" d="{poly(rp)}"/>')
            x, y = xy(*rp[-1])
            g.append(lab(x - 2, y - 6, "Ridge 500 hPa", "उच्च दाब पट्टा ५००", "ua-ridge-lab", "end"))
    for c in headline_centres(regional):
        if not c["headline"]:
            continue
        x, y = xy(c["lon"], c["lat"])
        if c["closed"]:
            letter, cls = ("C", "ua-cyc") if c["kind"] == "cyclonic" else ("A", "ua-anti")
            g.append(f'<text class="{cls}" x="{x:.1f}" y="{y + 7:.1f}" text-anchor="middle">{letter}</text>')
        else:
            g.append(f'<path class="ua-trough" d="M{x - 14:.1f},{y - 10:.1f}Q{x:.1f},{y + 14:.1f} {x + 14:.1f},{y - 10:.1f}"/>')
        g.append(lab(x, y + 24, f"{c['p']} hPa", f"{mr(c['p'])} hPa", "ua-c-lab", "middle"))
    for s in stations:
        x, y = xy(s["lon"], s["lat"])
        pot = s["potential"] if s["potential"] is not None else 0
        tip = f"{s['name']['en']}: K {s['k']}, MUCAPE {s['cape']:.0f} J/kg, PW {s['pw']:.0f} mm" if s["cape"] is not None and s["pw"] is not None else s["name"]["en"]
        g.append(f'<circle class="ua-pdot" cx="{x:.1f}" cy="{y:.1f}" r="{4 + pot}" fill="{POT_COLORS[pot]}"><title>{html.escape(tip)}</title></circle>')
    return (f'<svg class="ua-map ua-summary" viewBox="0 0 {MAP_W} {MAP_H}" role="img" aria-label="Key upper-air features">'
            '<defs><marker id="ua-jet-head" viewBox="0 0 10 10" refX="2" refY="5" markerWidth="16" markerHeight="16" markerUnits="userSpaceOnUse" orient="auto">'
            '<path d="M0,0L10,5L0,10z" fill="#1c5cab"/></marker></defs>' + "".join(g) + "</svg>")


def analyse(stations, coast):
    """Everything derived from the station analyses: regional diagnostics, maps, findings, tiles, summary map."""
    regional, maps, analyses = {}, {}, {}
    for p in MAP_LEVELS:
        an = analyse_level(stations, p)
        if not an:
            continue
        analyses[str(p)] = an
        cen = circulation_centres(an, stations)
        rid = ridge_line(an) if p in (500, 200) else {"points": [], "lat": None}
        regional[str(p)] = dict(centres=cen, ridge_lat=rid["lat"], n=an["n"], spacing=round(an["spacing"], 1),
                                mean_kt=round(float(np.nanmean(np.hypot(an["u"], an["v"]))) * KT))
        maps[str(p)] = map_svg(an, stations, cen, rid, coast)
    sh = [s["levels"]["850"]["u"] - s["levels"]["200"]["u"] for s in stations
          if s["lat"] < 20 and "u" in s["levels"].get("850", {}) and "u" in s["levels"].get("200", {})]
    regional["shear_index"] = round(float(np.mean(sh)), 1) if len(sh) >= 3 else None
    return dict(regional=regional, maps=maps, findings=findings(stations, regional), tiles=tiles(stations, regional),
                summary_map=summary_svg(stations, regional, analyses, coast), isotach=[[b, c] for b, c in ISOTACH])


def save(out, t):
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "latest.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    # compact archive (no maps) for trends
    arch = {k: v for k, v in out.items() if k not in ("maps", "summary_map")}
    (DATA / "archive").mkdir(exist_ok=True)
    (DATA / "archive" / f"{t:%Y%m%d%H}.json").write_text(json.dumps(arch, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def redraw():
    """Recompute maps, findings and tiles from the saved soundings in latest.json (no download)."""
    out = json.loads((DATA / "latest.json").read_text(encoding="utf-8"))
    coast = json.loads((DATA / "coast.json").read_text(encoding="utf-8"))["d"]
    out.update(analyse(out["stations"], coast))
    save(out, datetime.strptime(out["time"], "%Y-%m-%dT%H:00Z"))
    print("Redrawn:", DATA / "latest.json")
    return out


# ------------------------------------------------------------------ main
def latest_time(now=None):
    now = now or datetime.now(timezone.utc)
    t = now - timedelta(hours=2, minutes=30)                  # ascent ~23:30/11:30 UTC, reaches Wyoming in 1-2 h
    return t.replace(hour=0 if t.hour < 12 else 12, minute=0, second=0, microsecond=0, tzinfo=None)


def run(t=None):
    t = t or latest_time()
    coast = json.loads((DATA / "coast.json").read_text(encoding="utf-8"))["d"]
    stations, missing = [], []
    for st in STATIONS:
        page = fetch(st[0], t)
        prof = parse(page) if page else None
        if prof is None or np.isfinite(prof["sped"]).sum() < 3:
            missing.append(bi(st[1], st[2]))
            print(f"  {st[0]} {st[1]}: no sounding")
            continue
        s = station_analysis(st, prof)
        stations.append(s)
        print(f"  {st[0]} {st[1]}: {s['nlev']} levels to {s['top']:.0f} hPa, K={s['k']}, PW={s['pw']}")
    if len(stations) < 6:                     # e.g. a thin 12 UTC network: keep the previous analysis
        print(f"Only {len(stations)} soundings for {t:%Y-%m-%d %H} UTC; previous analysis kept")
        return None

    SVG_DIR.mkdir(parents=True, exist_ok=True)
    for f in SVG_DIR.glob("skewt_*.svg"):
        f.unlink()
    for s in stations:
        (SVG_DIR / f"skewt_{s['wmo']}.svg").write_text(skewt_svg(s), encoding="utf-8")
    for s in stations:
        s.pop("_skew"); s.pop("_parcel")

    out = dict(time=t.strftime("%Y-%m-%dT%H:00Z"), fetched=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
               n_stations=len(stations), n_network=len(STATIONS), missing=missing,
               stations=sorted(stations, key=lambda s: -s["lat"]))
    out.update(analyse(stations, coast))
    save(out, t)
    print(f"Upper-air analysis {t:%Y-%m-%d %H} UTC: {len(stations)} soundings -> {DATA / 'latest.json'}")
    return out


def make_coast():
    """Natural Earth 50 m coastline clipped to the map box, projected to the map's SVG coordinates."""
    import cartopy.io.shapereader as shpreader
    from shapely.geometry import box
    clip = box(LON0, LAT0, LON1, LAT1)
    d = []
    for g in shpreader.Reader(shpreader.natural_earth("50m", "physical", "coastline")).geometries():
        g = g.intersection(clip).simplify(0.04)
        for ln in getattr(g, "geoms", [g]):
            if ln.is_empty or ln.length < 0.3:
                continue
            d.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in (xy(a, b) for a, b in ln.coords)))
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "coast.json").write_text(json.dumps({"d": "".join(d), "w": MAP_W, "h": MAP_H}), encoding="utf-8")
    print("Coastline:", DATA / "coast.json")


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg == "coast":
        make_coast()
    elif arg == "redraw":
        redraw()
    elif arg:
        run(datetime.strptime(arg, "%Y-%m-%dT%H"))
    else:
        run()
