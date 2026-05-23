#!/usr/bin/env python3
"""
Splits — TFRRS + Runcruit scraper
Collects D3 outdoor track performance data and recruiting standards.
Double-click refresh.command to run.
"""

import json, os, re, sys, time, random, traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Installing required packages...")
    os.system(f"{sys.executable} -m pip install requests beautifulsoup4 lxml --break-system-packages -q")
    import requests
    from bs4 import BeautifulSoup

# ─── CONFIG ───────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
DATA_FILE  = SCRIPT_DIR / "data.json"
LOG_FILE   = SCRIPT_DIR / "scrape_log.txt"

# Global TFRRS season handles (same across all schools)
# season_hnd=730 = 2026 Outdoor, season_hnd=719 = 2026 Indoor
# list_hnd=5771 = current all_performances list
OUTDOOR_SEASON_HND = 730
OUTDOOR_LIST_HND   = 5771

# Past outdoor seasons for all-time data (last 10 years)
# season_hnd values for outdoor seasons going back
PAST_OUTDOOR_SEASON_HNDS = [
    (719, 2025), (697, 2024), (672, 2023), (648, 2022),
    (624, 2021), (601, 2020), (584, 2019), (563, 2018),
    (540, 2017), (519, 2016),
]

# Events we care about — keys match TFRRS h3 headings on all_performances page
def heading_to_event(text):
    t   = text.strip()
    low = t.lower()
    if re.match(r"^100 meters?$",         t, re.I): return "100m"
    if re.match(r"^200 meters?$",         t, re.I): return "200m"
    if re.match(r"^400 meters?$",         t, re.I): return "400m"
    if re.match(r"^800 meters?$",         t, re.I): return "800m"
    if re.match(r"^1[,.]?500 meters?$",   t, re.I): return "1500m"
    if re.match(r"^(mile( run)?|1 mile)$",t, re.I): return "Mile"
    if re.match(r"^5[,.]?000 meters?$",   t, re.I): return "5000m"
    if re.match(r"^10[,.]?000 meters?$",  t, re.I): return "10000m"
    if "4 x 100" in low or "4x100" in low:          return "4x100m Relay"
    if "4 x 400" in low or "4x400" in low:          return "4x400m Relay"
    return None

# TOP MARKS table on team page uses these abbreviations
TOPMARKS_EVENT_MAP = {
    "100": "100m", "200": "200m", "400": "400m", "800": "800m",
    "1500": "1500m", "Mile": "Mile", "5000": "5000m",
    "10,000": "10000m", "4x100": "4x100m Relay", "4x400": "4x400m Relay",
}

DELAY_MIN, DELAY_MAX = 1.8, 3.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ─── LOGGING ──────────────────────────────────
def log(msg, also_print=True):
    ts   = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    if also_print: print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

# ─── HTTP ─────────────────────────────────────
session = requests.Session()
session.headers.update(HEADERS)

def fetch(url, retries=3):
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 200:
                time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
                return r.text
            elif r.status_code == 404:
                return None
            elif r.status_code == 429:
                wait = 45 + attempt * 30
                log(f"  Rate limited — waiting {wait}s...")
                time.sleep(wait)
            else:
                log(f"  HTTP {r.status_code}: {url}")
                time.sleep(6)
        except Exception as e:
            log(f"  Request error: {e}")
            time.sleep(8)
    return None

# ─── DATA FILE ────────────────────────────────
def load_data():
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"meta": {"last_updated": None, "division": "D3", "season": "outdoor"}, "schools": []}

def save_data(data):
    data["meta"]["last_updated"] = datetime.now().isoformat()
    data["meta"]["school_count"] = len(data["schools"])
    # Write data.json
    tmp = DATA_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(DATA_FILE)
    # Also write data.js for file:// protocol (Firefox/Safari)
    js_file = SCRIPT_DIR / "data.js"
    with open(js_file, "w") as f:
        f.write("window.__splitsData = ")
        json.dump(data, f)
        f.write(";")

def already_scraped(data, school_id):
    return any(s["id"] == school_id for s in data["schools"])

def upsert_school(data, school):
    for i, s in enumerate(data["schools"]):
        if s["id"] == school["id"]:
            data["schools"][i] = school
            return
    data["schools"].append(school)
    data["schools"].sort(key=lambda s: s["name"])

# ─── PARSE GRADE ──────────────────────────────
def parse_grade(raw):
    m = re.match(r"(FR|SO|JR|SR|GRAD|5TH)", raw.strip().upper())
    return m.group(1) if m else raw.strip()

# ─── SCRAPE ALL_PERFORMANCES PAGE ─────────────
def scrape_all_performances(slug, season_hnd, year, team_url):
    """
    Fetch all_performances page for a specific outdoor season.
    Returns list of { event, rank, athlete, grade, year, time, tfrrs_url }
    """
    url = (f"https://www.tfrrs.org/all_performances/{slug}.html"
           f"?list_hnd={OUTDOOR_LIST_HND}&season_hnd={season_hnd}")
    html = fetch(url)
    if not html:
        return []

    soup    = BeautifulSoup(html, "lxml")
    results = []

    for h3 in soup.find_all("h3"):
        event = heading_to_event(h3.get_text(strip=True))
        if not event:
            continue

        # Find next <table> sibling
        table = None
        for sib in h3.next_siblings:
            if not hasattr(sib, "name"):
                continue
            if sib.name == "table":
                table = sib
                break
            if sib.name == "h3":
                break

        if not table:
            continue

        rank = 0
        seen = set()

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            # col 0: athlete link
            ath_link = cells[0].find("a", href=True)
            if not ath_link:
                continue
            athlete = ath_link.get_text(strip=True)
            if not athlete:
                continue
            # Deduplicate per athlete (keep only their best)
            if athlete in seen:
                continue
            seen.add(athlete)

            # col 1: grade
            grade = parse_grade(cells[1].get_text(strip=True))

            # col 2: time
            time_val = cells[2].get_text(strip=True)
            # Must be a valid time: 1:49.67 / 10.68 / 28:01.22
            if not re.match(r"^\d{1,3}(:\d{2})*[\.:]\d+$", time_val):
                continue

            time_link = cells[2].find("a", href=True)
            time_url  = urljoin("https://www.tfrrs.org", time_link["href"]) if time_link else team_url

            rank += 1
            results.append({
                "event": event, "rank": rank,
                "athlete": athlete, "grade": grade,
                "year": year, "time": time_val,
                "tfrrs_url": time_url,
            })
            if rank >= 3:
                break

    return results


# ─── SCRAPE TEAM PAGE (for conference + top marks fallback) ───
def scrape_team_page(slug):
    """
    Returns (conference, top_marks_for_outdoor)
    top_marks: list from the TOP MARKS table — used only if all_performances fails.
    Note: team page may show indoor season by default, so we only use it
    for conference detection and as a last resort fallback.
    """
    url  = f"https://www.tfrrs.org/teams/{slug}.html"
    html = fetch(url)
    if not html:
        return "", []

    soup = BeautifulSoup(html, "lxml")

    # Conference
    conference = ""
    for a in soup.find_all("a", href=re.compile(r"/leagues/\d+\.html")):
        text = a.get_text(strip=True)
        if text and len(text) < 40 and not any(x in text for x in ["ECAC","Region","DIII","AARTFC","NEICAAA"]):
            conference = text
            break

    # TOP MARKS table — only useful for outdoor events
    top_marks = []
    current_year = datetime.now().year
    for table in soup.find_all("table"):
        ths = [th.get_text(strip=True).upper() for th in table.find_all("th")]
        if "EVENT" not in ths:
            continue
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            event = TOPMARKS_EVENT_MAP.get(cells[0].get_text(strip=True))
            if not event:
                continue
            ath_links = cells[1].find_all("a", href=True)
            athlete   = " / ".join(a.get_text(strip=True) for a in ath_links[:4]) if ath_links \
                        else cells[1].get_text(strip=True)
            grade     = parse_grade(cells[2].get_text(strip=True))
            time_val  = cells[3].get_text(strip=True)
            if not time_val:
                continue
            t_link    = cells[3].find("a", href=True)
            time_url  = urljoin("https://www.tfrrs.org", t_link["href"]) if t_link else url
            top_marks.append({
                "event": event, "rank": 1, "athlete": athlete,
                "grade": grade, "year": current_year,
                "time": time_val, "tfrrs_url": time_url,
            })
        break

    return conference, top_marks


# ─── RUNCRUIT ─────────────────────────────────
def scrape_runcruit(school_id, school_name, runcruit_url):
    html = fetch(runcruit_url)
    if not html:
        slug2 = re.sub(r"[^a-z0-9\s-]", "", school_name.lower()).strip()
        slug2 = re.sub(r"\s+", "-", slug2)
        alt   = f"https://runcruit.com/standards/{slug2}"
        html  = fetch(alt)
        if html:
            runcruit_url = alt
    if not html:
        return {"men": [], "women": []}

    soup   = BeautifulSoup(html, "lxml")
    result = {"men": [], "women": []}

    for table in soup.find_all("table"):
        ths = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not any(k in " ".join(ths) for k in ["recruit", "walk", "tryout"]):
            continue

        gender = "men"
        for prev in table.find_all_previous(["h2","h3","h4","div","span"], limit=6):
            t = prev.get_text(strip=True).lower()
            if "women" in t or "female" in t:
                gender = "women"; break
            if "men" in t or "male" in t:
                gender = "men";   break

        ei = ri = wi = ti = None
        for i, h in enumerate(ths):
            if "event" in h:   ei = i
            elif "recruit" in h: ri = i
            elif "walk" in h:    wi = i
            elif "tryout" in h:  ti = i
        if ei is None: ei = 0

        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 2: continue
            raw = cells[ei].get_text(strip=True).lower() if ei < len(cells) else ""
            event = None
            if   "100m" in raw or raw == "100":          event = "100m"
            elif "200m" in raw or raw == "200":          event = "200m"
            elif "400m" in raw or raw == "400":          event = "400m"
            elif "800m" in raw or raw == "800":          event = "800m"
            elif "1500" in raw:                          event = "1500m"
            elif "mile" in raw:                          event = "Mile"
            elif "3000" in raw and "5" not in raw:       event = "3000m"
            elif "5000" in raw or "5k" in raw:           event = "5000m"
            elif "10000" in raw or "10k" in raw or "10,000" in raw: event = "10000m"
            elif "4x100" in raw or "4 x 100" in raw:    event = "4x100m Relay"
            elif "4x400" in raw or "4 x 400" in raw:    event = "4x400m Relay"
            if not event: continue

            def gc(idx):
                return cells[idx].get_text(strip=True) if idx is not None and idx < len(cells) else ""

            recruit, walkon, tryout = gc(ri), gc(wi), gc(ti)
            if recruit or walkon or tryout:
                result[gender].append({
                    "event": event, "recruit": recruit,
                    "walkon": walkon, "tryout": tryout,
                    "runcruit_url": runcruit_url,
                })
    return result


# ─── SCHOOL LIST ──────────────────────────────
def get_d3_school_list():
    log("Fetching D3 school list from TFRRS...")
    schools = {}
    for gender_char in ("m", "f"):
        for url in [
            f"https://tf.tfrrs.org/lists/current_season_outdoor.html?level=d3&gender={gender_char}",
            f"https://www.tfrrs.org/college_team_list.html?level=d3&gender={gender_char}",
        ]:
            html = fetch(url)
            if not html: continue
            soup = BeautifulSoup(html, "lxml")
            for a in soup.find_all("a", href=re.compile(r"/teams/[A-Z]{2}_college_[mf]_")):
                href = a["href"]
                m = re.search(r"/teams/([A-Z]{2})_college_([mf])_(.+?)(?:\.html)?$", href)
                if not m: continue
                state, g, slug_part = m.group(1), m.group(2), m.group(3)
                sid  = slug_part.lower().replace("_", "-")
                name = a.get_text(strip=True)
                if not name or len(name) < 2: continue
                if sid not in schools:
                    schools[sid] = {
                        "id": sid, "name": name, "division": "D3",
                        "conference": "", "state": state,
                        "tfrrs_slug_m": "", "tfrrs_slug_f": "",
                        "tfrrs_url": f"https://www.tfrrs.org/teams/{state}_college_m_{slug_part}.html",
                        "runcruit_url": f"https://runcruit.com/standards/{sid}",
                        "performances": {
                            "men":   {"outdoor": {"current": [], "alltime": []}},
                            "women": {"outdoor": {"current": [], "alltime": []}}
                        },
                        "recruiting": {"men": [], "women": []}
                    }
                schools[sid]["tfrrs_slug_m" if g=="m" else "tfrrs_slug_f"] = f"{state}_college_{g}_{slug_part}"
            if schools: break

    if schools:
        log(f"  Found {len(schools)} D3 schools")
        return list(schools.values())

    log("  Live list unavailable — using built-in school list")
    return get_hardcoded_d3_schools()


def get_hardcoded_d3_schools():
    # (id, name, conference, state, tfrrs_suffix_for_men)
    raw = [
        ("williams",         "Williams College",               "NESCAC",               "MA", "Williams"),
        ("amherst",          "Amherst College",                "NESCAC",               "MA", "Amherst"),
        ("middlebury",       "Middlebury College",             "NESCAC",               "VT", "Middlebury"),
        ("bowdoin",          "Bowdoin College",                "NESCAC",               "ME", "Bowdoin"),
        ("colby",            "Colby College",                  "NESCAC",               "ME", "Colby"),
        ("bates",            "Bates College",                  "NESCAC",               "ME", "Bates"),
        ("trinity-ct",       "Trinity College",                "NESCAC",               "CT", "Trinity"),
        ("wesleyan",         "Wesleyan University",            "NESCAC",               "CT", "Wesleyan"),
        ("hamilton",         "Hamilton College",               "NESCAC",               "NY", "Hamilton"),
        ("tufts",            "Tufts University",               "NESCAC",               "MA", "Tufts"),
        ("emory",            "Emory University",               "UAA",                  "GA", "Emory"),
        ("chicago",          "University of Chicago",          "UAA",                  "IL", "U_of_Chicago"),
        ("brandeis",         "Brandeis University",            "UAA",                  "MA", "Brandeis"),
        ("case-western",     "Case Western Reserve",           "UAA",                  "OH", "Case_Western"),
        ("carnegie-mellon",  "Carnegie Mellon University",     "UAA",                  "PA", "Carnegie_Mellon"),
        ("nyu",              "New York University",            "UAA",                  "NY", "NYU"),
        ("rochester",        "University of Rochester",        "UAA",                  "NY", "Rochester"),
        ("washu",            "Washington University St. Louis","UAA",                  "MO", "Washington_U"),
        ("mit",              "MIT",                            "NEWMAC",               "MA", "MIT"),
        ("springfield",      "Springfield College",            "NEWMAC",               "MA", "Springfield"),
        ("wpi",              "WPI",                            "NEWMAC",               "MA", "WPI"),
        ("pomona-pitzer",    "Pomona-Pitzer",                  "SCIAC",                "CA", "Pomona-Pitzer"),
        ("claremont-ms",     "Claremont-Mudd-Scripps",         "SCIAC",                "CA", "Claremont-Mudd-Scripps"),
        ("redlands",         "University of Redlands",         "SCIAC",                "CA", "Redlands"),
        ("occidental",       "Occidental College",             "SCIAC",                "CA", "Occidental"),
        ("cal-lutheran",     "Cal Lutheran",                   "SCIAC",                "CA", "Cal_Lutheran"),
        ("uw-la-crosse",     "UW-La Crosse",                   "WIAC",                 "WI", "Wis_La_Crosse"),
        ("uw-oshkosh",       "UW-Oshkosh",                     "WIAC",                 "WI", "Wis_Oshkosh"),
        ("uw-whitewater",    "UW-Whitewater",                  "WIAC",                 "WI", "Wis_Whitewater"),
        ("uw-eau-claire",    "UW-Eau Claire",                  "WIAC",                 "WI", "Wis_Eau_Claire"),
        ("johns-hopkins",    "Johns Hopkins University",        "Centennial",           "MD", "Johns_Hopkins"),
        ("dickinson",        "Dickinson College",               "Centennial",           "PA", "Dickinson"),
        ("gettysburg",       "Gettysburg College",              "Centennial",           "PA", "Gettysburg"),
        ("muhlenberg",       "Muhlenberg College",              "Centennial",           "PA", "Muhlenberg"),
        ("franklin-marshall","Franklin & Marshall College",     "Centennial",           "PA", "Franklin_And_Marshall"),
        ("haverford",        "Haverford College",               "Centennial",           "PA", "Haverford"),
        ("swarthmore",       "Swarthmore College",              "Centennial",           "PA", "Swarthmore"),
        ("rpi",              "RPI",                             "Liberty League",       "NY", "RPI"),
        ("union-ny",         "Union College",                   "Liberty League",       "NY", "Union"),
        ("vassar",           "Vassar College",                  "Liberty League",       "NY", "Vassar"),
        ("skidmore",         "Skidmore College",                "Liberty League",       "NY", "Skidmore"),
        ("denison",          "Denison University",              "North Coast Athletic", "OH", "Denison"),
        ("kenyon",           "Kenyon College",                  "North Coast Athletic", "OH", "Kenyon"),
        ("oberlin",          "Oberlin College",                 "North Coast Athletic", "OH", "Oberlin"),
        ("ohio-wesleyan",    "Ohio Wesleyan University",        "North Coast Athletic", "OH", "Ohio_Wesleyan"),
        ("wooster",          "College of Wooster",              "North Coast Athletic", "OH", "Wooster"),
        ("grinnell",         "Grinnell College",                "Midwest Conference",   "IA", "Grinnell"),
        ("st-olaf",          "St. Olaf College",                "Midwest Conference",   "MN", "St_Olaf"),
        ("carleton",         "Carleton College",                "Midwest Conference",   "MN", "Carleton"),
        ("macalester",       "Macalester College",              "Midwest Conference",   "MN", "Macalester"),
        ("rhodes",           "Rhodes College",                  "Southern Athletic",    "TN", "Rhodes"),
        ("sewanee",          "University of the South",         "Southern Athletic",    "TN", "Sewanee"),
        ("berry",            "Berry College",                   "Southern Athletic",    "GA", "Berry"),
        ("allegheny",        "Allegheny College",               "Presidents' Athletic", "PA", "Allegheny"),
        ("grove-city",       "Grove City College",              "Presidents' Athletic", "PA", "Grove_City"),
        ("roanoke",          "Roanoke College",                 "ODAC",                 "VA", "Roanoke"),
        ("lynchburg",        "University of Lynchburg",         "ODAC",                 "VA", "Lynchburg"),
        ("st-thomas",        "University of St. Thomas",        "MIAC",                 "MN", "St_Thomas"),
        ("gustavus",         "Gustavus Adolphus College",       "MIAC",                 "MN", "Gustavus_Adolphus"),
        ("christopher-newport","Christopher Newport University","USA South",            "VA", "Christopher_Newport"),
        ("mary-washington",  "University of Mary Washington",   "USA South",            "VA", "Mary_Washington"),
    ]
    result = []
    for sid, name, conf, state, suffix in raw:
        result.append({
            "id": sid, "name": name, "division": "D3",
            "conference": conf, "state": state,
            "tfrrs_slug_m": f"{state}_college_m_{suffix}",
            "tfrrs_slug_f": f"{state}_college_f_{suffix}",
            "tfrrs_url":    f"https://www.tfrrs.org/teams/{state}_college_m_{suffix}.html",
            "runcruit_url": f"https://runcruit.com/standards/{sid}",
            "performances": {
                "men":   {"outdoor": {"current": [], "alltime": []}},
                "women": {"outdoor": {"current": [], "alltime": []}}
            },
            "recruiting": {"men": [], "women": []}
        })
    return result


# ─── SCRAPE ONE SCHOOL ────────────────────────
def make_slug_variants(slug):
    """
    Generate alternate slug formats to try if the primary one returns no data.
    TFRRS slug naming is inconsistent — this covers the most common patterns.
    """
    variants = [slug]
    state = slug[:2]
    gender = slug[3:4] if "_college_" in slug else "m"
    # Extract the school name part
    parts = slug.split("_college_" + gender + "_", 1)
    if len(parts) == 2:
        name_part = parts[1]
        prefix = f"{state}_college_{gender}_"
        # Try common variations
        variants.append(prefix + name_part.replace("_And_", "_"))
        variants.append(prefix + name_part.replace("_And_", "_and_"))
        variants.append(prefix + name_part.replace("_U_", "_U."))
        variants.append(prefix + "U_of_" + name_part)
        variants.append(prefix + name_part.replace("U_of_", ""))
        # Remove duplicates while preserving order
        seen = set()
        unique = []
        for v in variants:
            if v not in seen:
                seen.add(v)
                unique.append(v)
        return unique
    return variants


def scrape_school(school):
    out = dict(school)
    out["performances"] = {
        "men":   {"outdoor": {"current": [], "alltime": []}},
        "women": {"outdoor": {"current": [], "alltime": []}}
    }
    out["recruiting"] = {"men": [], "women": []}

    for gender_char, gender_label in [("m", "men"), ("f", "women")]:
        slug_key = "tfrrs_slug_m" if gender_char == "m" else "tfrrs_slug_f"
        slug = school.get(slug_key, "")
        if not slug:
            continue

        team_url = f"https://www.tfrrs.org/teams/{slug}.html"

        # Get conference (and fallback top marks) from team page
        conference, top_marks = scrape_team_page(slug)
        if conference and not out["conference"]:
            out["conference"] = conference
        if gender_char == "m":
            out["tfrrs_url"] = team_url

        # ── Current season: use season_hnd=730 (2026 outdoor) ──
        current = scrape_all_performances(slug, OUTDOOR_SEASON_HND, 2026, team_url)

        # If nothing returned, try alternate slug variants
        if not current:
            for alt_slug in make_slug_variants(slug)[1:]:  # skip first (already tried)
                current = scrape_all_performances(alt_slug, OUTDOOR_SEASON_HND, 2026, team_url)
                if current:
                    log(f"  Used alternate slug: {alt_slug}")
                    slug = alt_slug  # use this for alltime too
                    break

        # If still nothing, try team-page top marks as last resort
        if not current:
            current = [m for m in top_marks if m["event"] in {
                "100m","200m","400m","800m","1500m","Mile",
                "5000m","10000m","4x100m Relay","4x400m Relay"
            }]

        # ── All-time: scrape past seasons ──
        alltime = []
        seen_alltime = set()
        for past_hnd, past_year in PAST_OUTDOOR_SEASON_HNDS[:4]:  # last 4 seasons = ~4 years
            past_perfs = scrape_all_performances(slug, past_hnd, past_year, team_url)
            for p in past_perfs:
                key = (p["event"], p["athlete"])
                if key not in seen_alltime:
                    seen_alltime.add(key)
                    alltime.append(p)
            if len(alltime) >= 30:  # enough data, stop
                break

        out["performances"][gender_label]["outdoor"]["current"] = current
        out["performances"][gender_label]["outdoor"]["alltime"]  = alltime

        n_events = len(set(p["event"] for p in current))
        log(f"  {gender_label.capitalize()}: {len(current)} current marks across {n_events} events, {len(alltime)} alltime")

    # Recruiting
    recruiting = scrape_runcruit(
        school["id"], school["name"],
        school.get("runcruit_url", f"https://runcruit.com/standards/{school['id']}")
    )
    out["recruiting"] = recruiting
    log(f"  Recruiting: {len(recruiting['men'])} men's events, {len(recruiting['women'])} women's events")
    return out


# ─── MAIN LOOP ────────────────────────────────
def run_scrape(to_scrape, data):
    total = len(to_scrape)
    ok = fail = 0
    for i, school in enumerate(to_scrape):
        if already_scraped(data, school["id"]):
            log(f"[{i+1}/{total}] Skipping {school['name']} (already done)")
            continue
        log(f"[{i+1}/{total}] Scraping {school['name']}...")
        try:
            updated = scrape_school(school)
            upsert_school(data, updated)
            save_data(data)
            ok += 1
        except Exception as e:
            log(f"  ERROR: {e}")
            log(traceback.format_exc(), also_print=False)
            fail += 1
        time.sleep(random.uniform(1.0, 2.0))
    log(f"\nDone — scraped: {ok}, failed: {fail}, total in data: {len(data['schools'])}")


# ─── ENTRY POINT ──────────────────────────────
def main():
    print("\n" + "="*52)
    print("   SPLITS — Track & Field Data Scraper")
    print("="*52 + "\n")
    print("  test  — scrape 10 schools (~10 min)")
    print("  full  — scrape all D3 schools (~2-3 hrs)")
    print("  reset — clear data and start fresh")
    print()
    choice = input("Type 'test', 'full', or 'reset': ").strip().lower()
    if choice not in ("test", "full", "reset"):
        print("Invalid choice. Exiting.")
        return

    data = {"meta": {"last_updated": None, "division": "D3", "season": "outdoor"}, "schools": []} \
        if choice == "reset" else load_data()

    all_schools = get_d3_school_list()
    pending     = [s for s in all_schools if not already_scraped(data, s["id"])]

    if choice == "test":
        to_scrape = pending[:20]
        print(f"\nTest mode — scraping {len(to_scrape)} schools\n")
    else:
        to_scrape = pending
        done = len(all_schools) - len(pending)
        mins = len(to_scrape) * 5 / 60
        print(f"\nFull mode — {len(to_scrape)} to scrape ({done} already done)")
        print(f"Estimated time: {mins:.0f}–{mins*1.5:.0f} minutes")
        print("Ctrl+C to pause anytime — re-run to resume.\n")
        input("Press Enter to start...")

    print()
    run_scrape(to_scrape, data)
    print(f"\n✓ data.json → {DATA_FILE}")
    print(f"✓ data.js   → {SCRIPT_DIR / 'data.js'}")
    print(f"✓ Log       → {LOG_FILE}")
    print("\nOpen splits.html in Chrome to see the results.")
    print("(Chrome works with file:// — for Safari/Firefox you need GitHub Pages)")
    input("\nPress Enter to close...")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nStopped. Progress saved — re-run to resume.")
        input("\nPress Enter to close...")
