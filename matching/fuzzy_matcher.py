"""Spec-aware fuzzy matching for German construction (GaLaBau/Tiefbau).

Improvements over simple text matching:
1. Spec extraction & comparison (DN, NW, dimensions, DIN, material codes)
2. Material type classification (Betonpflaster != Natursteinpflaster)
3. German compound word handling
4. GAEB Langtext integration
5. Hard penalties for spec mismatches
6. Unit compatibility check
"""
import re
from typing import List, Dict, Optional, Tuple, Set
from rapidfuzz import fuzz
import config


# ── Material Type Classification ────────────────────────────────────────────

MATERIAL_GROUPS = {
    "schuettgut": [
        "schotter", "kies", "splitt", "sand", "brechsand", "frostschutz",
        "tragschicht", "mineralbeton", "mineralgemisch", "recycling", "rcl",
        "fuellsand", "kiessand", "edelsplitt", "ziersplitt", "zierkies",
    ],
    "beton": [
        "beton", "magerbeton", "frischbeton", "fundament", "unterbeton",
        "rueckenstuetze", "punktfundament",
    ],
    "erde_substrat": [
        "mutterboden", "oberboden", "unterboden", "humus", "kompost",
        "substrat", "vegetationssubstrat", "rasensubstrat", "baumsubstrat",
        "pflanzsubstrat", "dachsubstrat", "erde",
    ],
    "bordstein": [
        "bordstein", "hochbord", "tiefbord", "flachbord", "rundbord",
        "randstein", "rasenkante", "rasenkantenstein", "einfassung",
        "maehkante", "betonbord", "granitbord", "natursteinbord",
    ],
    "l_stein": ["l-stein", "winkelst", "winkelstuetz", "stuetzelement"],
    "palisade": ["palisade", "betonpalisade"],
    "betonpflaster": [
        "betonpflaster", "betonsteinpflaster", "verbundpflaster",
        "betonstein", "pflasterstein",
    ],
    "natursteinpflaster": [
        "natursteinpflaster", "granitpflaster", "mosaikpflaster",
        "grosspflaster", "kleinpflaster",
    ],
    "klinker": ["klinker", "klinkerpflaster"],
    "betonplatte": ["betonplatte", "gehwegplatte", "terrassenplatte"],
    "natursteinplatte": ["natursteinplatte", "granitplatte", "sandsteinplatte"],
    "asphalt": ["asphalt", "asphalttragschicht", "asphaltdeckschicht", "asphaltbinder"],
    "rohr_kg": ["kg-rohr", "kg rohr", "kanalrohr"],
    "rohr_pe": ["pe-rohr", "pe rohr", "pe-hd", "pe100"],
    "rohr_pp": ["pp-rohr", "pp rohr", "pp-md"],
    "drainage": ["drainrohr", "drainagerohr", "drainage", "sickerrohr", "drainmatte"],
    "rohr_formteile": [
        "muffe", "schweissmuffe", "bogen", "abzweig", "passstueck",
        "passlaenge", "doppelsteckmuffe", "formteil", "fitting",
    ],
    "rinne": [
        "rinne", "kastenrinne", "muldenrinne", "pendelrinne",
        "fassadenrinne", "terrassenrinne", "entwasserungsrinne", "schlitzrinne",
    ],
    "rost_abdeckung": ["gitterrost", "gussrost", "maschenrost", "rost", "abdeckung"],
    "schacht": [
        "schacht", "schachtring", "schachtkonus", "kontrollschacht",
        "revisionsschacht", "sinkkasten",
    ],
    "einlauf": ["strassenablauf", "hofablauf", "einlauf", "einlaufrost", "ablaufkoerper"],
    "rigole": ["rigole", "rigolenkoerper", "versickerung", "sickerblock"],
    "laubbaum": ["laubbaum", "hochstamm", "solitaer", "alleebaum", "strassenbaum"],
    "obstbaum": ["obstbaum", "apfel", "birne", "kirsche", "pflaume", "zwetschge"],
    "nadelbaum": ["nadelbaum", "kiefer", "fichte", "tanne", "eibe", "thuja", "taxus"],
    "strauch": ["strauch", "zierstrauch", "blueten", "wildstrauch", "gehoelz"],
    "hecke": ["hecke", "heckenpflanze", "schnitthecke", "hainbuche", "liguster", "rotbuche"],
    "staude": ["staude", "gras", "ziergras", "farn", "bodendecker", "bodendecke"],
    "rasen": [
        "rasen", "rasensaatgut", "saatgut", "ansaat", "rollrasen",
        "fertigrasen", "rsm", "blumenwiese", "landschaftsrasen",
    ],
    "fallschutz": ["hackschnitzel", "fallschutz", "fallschutzplatte", "fallschutzbelag"],
    "spielgeraet": [
        "spielgeraet", "schaukel", "rutsche", "klettergeruest", "wippe",
        "sandkasten", "federwipp", "karussell", "balancierparcours",
        "matschanlage", "kletterstein", "reck",
    ],
    "zaun": ["zaun", "doppelstabmatte", "maschendraht", "holzzaun", "staketenzaun", "zaunpfosten"],
    "gelaender": ["gelaender", "handlauf"],
    "vlies_folie": [
        "vlies", "geotextil", "filtervlies", "trennvlies", "unkrautvlies",
        "folie", "teichfolie", "wurzelschutzfolie", "noppenbahn",
        "schutzlage", "drainagematte", "dichtungsbahn",
    ],
    "bewaesserung": ["bewaesserung", "tropfschlauch", "regner", "beregnungsanlage", "ventil"],
    "beleuchtung": ["leuchte", "mastleuchte", "pollerleuchte", "bodeneinbauleuchte", "lichtmast"],
    "ausstattung": [
        "bank", "sitzbank", "parkbank", "tisch", "papierkorb",
        "abfallbehaelter", "fahrradstaender", "fahrradbuegel", "sonnenschirm", "pergola",
    ],
}

_KEYWORD_TO_GROUP = {}
for group, keywords in MATERIAL_GROUPS.items():
    for kw in keywords:
        _KEYWORD_TO_GROUP[kw] = group

# Compatible group pairs
_COMPATIBLE_GROUPS = {
    frozenset({"schuettgut", "erde_substrat"}),
    frozenset({"betonpflaster", "betonplatte"}),
    frozenset({"natursteinpflaster", "natursteinplatte"}),
    frozenset({"rohr_kg", "rohr_pe", "rohr_pp"}),
    frozenset({"laubbaum", "obstbaum"}),
    frozenset({"strauch", "hecke"}),
    frozenset({"strauch", "staude"}),
    frozenset({"einlauf", "schacht"}),
}


def _extract_specs(text):
    """Extract technical specifications from text."""
    t = text.lower()
    specs = {"dn": set(), "dims": set(), "din": set(),
             "thickness": None, "width": None, "length": None,
             "material_kw": set(), "color": set(), "strength": None}

    for m in re.finditer(r'(?:dn|nw|dn/od)\s*(\d+)', t):
        specs["dn"].add(int(m.group(1)))

    for m in re.finditer(r'(\d+)\s*[/x]\s*(\d+)(?:\s*[/x]\s*(\d+))?', t):
        dim = re.sub(r'[x]', '/', re.sub(r'\s+', '', m.group(0)))
        specs["dims"].add(dim)

    for m in re.finditer(r'(?:din|en)\s*(?:en\s*)?(\d[\d\s\-]*\d)', t):
        specs["din"].add(m.group(1).strip())

    for m in re.finditer(r'(?:d\s*[=:]\s*|staerke\s*|dicke\s*)(\d+(?:[.,]\d+)?)\s*(mm|cm|m)', t):
        val = float(m.group(1).replace(',', '.'))
        u = m.group(2)
        specs["thickness"] = val / 10 if u == "mm" else (val * 100 if u == "m" else val)
    for m in re.finditer(r'(\d+(?:[.,]\d+)?)\s*(mm|cm)\s+(?:stark|dick)', t):
        val = float(m.group(1).replace(',', '.'))
        specs["thickness"] = val / 10 if m.group(2) == "mm" else val

    for m in re.finditer(r'(?:b\s*[=:]\s*|breite\s*)(\d+(?:[.,]\d+)?)\s*(mm|cm|m)', t):
        val = float(m.group(1).replace(',', '.'))
        u = m.group(2)
        specs["width"] = val / 10 if u == "mm" else (val * 100 if u == "m" else val)

    for m in re.finditer(r'(?:l\s*[=:]\s*|laenge\s*|bl\s*[=:]\s*)(\d+(?:[.,]\d+)?)\s*(mm|cm|m)', t):
        val = float(m.group(1).replace(',', '.'))
        u = m.group(2)
        specs["length"] = val / 10 if u == "mm" else (val * 100 if u == "m" else val)

    m = re.search(r'(c\s*\d+\s*/\s*\d+)', t)
    if m:
        specs["strength"] = re.sub(r'\s+', '', m.group(1)).upper()

    for kw in ["stahl", "alu", "aluminium", "kunststoff", "pvc", "pe", "pp",
               "guss", "beton", "granit", "sandstein", "basalt", "kalkstein",
               "holz", "laerche", "eiche", "robinie", "edelstahl", "verzinkt", "corten"]:
        if kw in t:
            specs["material_kw"].add(kw)

    for c in ["grau", "anthrazit", "schwarz", "weiss", "rot", "braun", "gelb", "natur", "steingrau"]:
        if c in t:
            specs["color"].add(c)

    return specs


def _specs_compatible(spec_a, spec_b):
    """Check spec compatibility. Returns (compatible, penalty, warning)."""
    penalty = 0
    warnings = []

    if spec_a["dn"] and spec_b["dn"]:
        if spec_a["dn"] != spec_b["dn"]:
            return False, 100, f"DN mismatch: {spec_a['dn']} vs {spec_b['dn']}"

    if spec_a["dims"] and spec_b["dims"]:
        norm_a = _normalize_dims(spec_a["dims"])
        norm_b = _normalize_dims(spec_b["dims"])
        if norm_a and norm_b and not norm_a.intersection(norm_b):
            return False, 100, f"Dims mismatch: {spec_a['dims']} vs {spec_b['dims']}"

    if spec_a["thickness"] and spec_b["thickness"]:
        ratio = spec_a["thickness"] / spec_b["thickness"]
        if ratio < 0.7 or ratio > 1.3:
            return False, 80, f"Thickness: {spec_a['thickness']}cm vs {spec_b['thickness']}cm"
        elif ratio < 0.9 or ratio > 1.1:
            penalty += 15
            warnings.append(f"Thickness: {spec_a['thickness']} vs {spec_b['thickness']}cm")

    if spec_a["material_kw"] and spec_b["material_kw"]:
        conflicts = [
            ({"stahl", "edelstahl", "verzinkt", "guss"}, {"kunststoff", "pvc", "pe", "pp"}),
            ({"stahl", "edelstahl"}, {"alu", "aluminium"}),
            ({"beton"}, {"kunststoff", "pvc", "pe", "pp"}),
            ({"granit"}, {"sandstein", "basalt", "kalkstein"}),
            ({"holz", "laerche", "eiche", "robinie"}, {"stahl", "edelstahl", "alu"}),
        ]
        for g1, g2 in conflicts:
            if (spec_a["material_kw"] & g1 and spec_b["material_kw"] & g2) or \
               (spec_a["material_kw"] & g2 and spec_b["material_kw"] & g1):
                return False, 100, f"Material conflict: {spec_a['material_kw']} vs {spec_b['material_kw']}"

    if spec_a["strength"] and spec_b["strength"] and spec_a["strength"] != spec_b["strength"]:
        penalty += 20
        warnings.append(f"Strength: {spec_a['strength']} vs {spec_b['strength']}")

    if spec_a["color"] and spec_b["color"] and not spec_a["color"] & spec_b["color"]:
        penalty += 5

    return True, penalty, " | ".join(warnings)


def _normalize_dims(dims):
    normalized = set()
    for d in dims:
        parts = re.split(r'[/x]', d)
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            normalized.add(d)
            continue
        normalized.add("/".join(str(n) for n in nums))
        if all(n >= 50 for n in nums):
            normalized.add("/".join(str(n // 10) for n in nums))
        if all(n < 50 for n in nums):
            normalized.add("/".join(str(n * 10) for n in nums))
    return normalized


def _detect_group(text):
    t = text.lower()
    for kw, group in sorted(_KEYWORD_TO_GROUP.items(), key=lambda x: -len(x[0])):
        if kw in t:
            return group
    return None


def _groups_compatible(ga, gb):
    if ga is None or gb is None:
        return True
    if ga == gb:
        return True
    return frozenset({ga, gb}) in _COMPATIBLE_GROUPS


_COMPOUND_PARTS = [
    "schotter", "trag", "schicht", "schutz", "frost", "mineral",
    "beton", "pflaster", "stein", "natur", "granit", "sand",
    "bord", "hoch", "tief", "rand", "rasen", "kante",
    "rohr", "kanal", "drain", "drainage", "leitung",
    "rinne", "mulden", "kasten", "schlitz", "fassaden",
    "schacht", "kontroll", "revision",
    "baum", "strauch", "hecke", "staude", "gras",
    "pflanz", "substrat", "vegetation",
    "fall", "hack", "spiel",
    "zaun", "gitter", "matten", "draht", "pfosten",
    "vlies", "folie", "gewebe", "matte",
    "wasser", "regen", "sicker",
]


def _split_compound(word):
    w = word.lower()
    parts = {w}
    for part in _COMPOUND_PARTS:
        if part in w and len(part) < len(w):
            parts.add(part)
            remainder = w.replace(part, "", 1).strip()
            if len(remainder) >= 3:
                parts.add(remainder)
    return parts


# ── Main Matching ───────────────────────────────────────────────────────────

def fuzzy_match_positions(lv_positions, offer_items, supplier="", gaeb_lookup=None):
    """Match offer items to LV positions using spec-aware fuzzy matching.

    Args:
        lv_positions: LV positions from Excel
        offer_items: Extracted offer items
        supplier: Supplier name
        gaeb_lookup: Optional {oz_normalized: langtext}

    Returns: {sure: [...], maybe: [...], no_match: [...]}
    """
    results = {"sure": [], "maybe": [], "no_match": []}
    if not lv_positions or not offer_items:
        results["no_match"] = list(offer_items)
        return results

    # Pre-process LV
    lv_data = []
    lv_skip = set()
    for i, pos in enumerate(lv_positions):
        bez = pos["bezeichnung"]
        combined = bez
        if gaeb_lookup:
            oz_norm = _normalize_oz(pos["oz"])
            lt = gaeb_lookup.get(oz_norm, "")
            if lt:
                combined = bez + " " + lt

        clean = _clean(combined)
        specs = _extract_specs(combined)
        group = _detect_group(combined)
        kws = _extract_keywords(clean)
        compounds = set()
        for w in clean.split():
            compounds.update(_split_compound(w))

        lv_data.append({
            "idx": i, "pos": pos, "clean": _clean(bez), "clean_full": clean,
            "specs": specs, "group": group, "keywords": kws, "compounds": compounds,
        })

        has_x = pos.get("stoffe_ep") and pos["stoffe_ep"] > 0
        has_m = pos.get("nu_ep") and pos["nu_ep"] > 0
        if has_x and has_m:
            lv_skip.add(i)
        if _is_internal(bez):
            lv_skip.add(i)

    for item in offer_items:
        text = item.get("text", "")
        if not text or not text.strip():
            results["no_match"].append(item)
            continue

        oc = _clean(text)
        os = _extract_specs(text)
        og = _detect_group(text)
        ok = _extract_keywords(oc)
        ocomp = set()
        for w in oc.split():
            ocomp.update(_split_compound(w))

        best_score = 0
        best_idx = -1
        best_warn = ""
        best_type = ""

        for lv in lv_data:
            if lv["idx"] in lv_skip:
                continue

            # 1. Group check
            if not _groups_compatible(og, lv["group"]):
                continue

            # 2. Spec check
            sok, spen, swarn = _specs_compatible(os, lv["specs"])
            if not sok:
                continue

            # 3. Text similarity
            sp = fuzz.partial_ratio(oc, lv["clean_full"])
            st = fuzz.token_sort_ratio(oc, lv["clean_full"])
            ss = fuzz.token_set_ratio(oc, lv["clean_full"])
            base = max(sp, st, ss)

            # 4. Bonuses
            kw_bonus = min(len(ok & lv["keywords"]) * 4, 20)
            comp_bonus = min(len(ocomp & lv["compounds"]) * 3, 15)

            spec_bonus = 0
            if os["dn"] and lv["specs"]["dn"] and os["dn"] == lv["specs"]["dn"]:
                spec_bonus += 15
            if os["dims"] and lv["specs"]["dims"]:
                if _normalize_dims(os["dims"]) & _normalize_dims(lv["specs"]["dims"]):
                    spec_bonus += 15
            if os["strength"] and lv["specs"]["strength"] and os["strength"] == lv["specs"]["strength"]:
                spec_bonus += 10

            grp_bonus = 10 if og and og == lv["group"] else 0

            total = base + kw_bonus + comp_bonus + spec_bonus + grp_bonus - spen

            if total > best_score:
                best_score = total
                best_idx = lv["idx"]
                best_warn = swarn
                best_type = "token_set" if ss >= sp and ss >= st else ("token_sort" if st >= sp else "partial")

        if best_idx < 0:
            results["no_match"].append(item)
            continue

        md = {
            "lv_pos": lv_positions[best_idx],
            "offer_item": item,
            "score": round(best_score, 1),
            "match_type": best_type,
            "supplier": supplier,
            "warning": best_warn,
        }

        if best_score >= config.FUZZY_THRESHOLD_SURE:
            results["sure"].append(md)
        elif best_score >= config.FUZZY_THRESHOLD_MAYBE:
            results["maybe"].append(md)
        else:
            results["no_match"].append(item)

    return results


def _clean(text):
    text = text.lower().strip()
    for n in ["ca.", "ca", "gem.", "lt.", "laut", "inkl.", "inkl", "nach", "din", "en",
              "bzw.", "ggf.", "mind.", "max.", "bis", "von", "mit", "und", "fuer",
              "auf", "aus", "das", "der", "die", "den", "dem", "ein", "eine"]:
        text = re.sub(r'\b' + re.escape(n) + r'\b', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.replace("m\u00b2", "m2").replace("m\u00b3", "m3").strip()


def _extract_keywords(text):
    words = set(re.findall(r'[a-z\u00e4\u00f6\u00fc\u00df]{4,}', text.lower()))
    stop = {"nach", "oder", "auch", "wird", "sein", "eine", "einem", "eines",
            "haben", "diese", "dieser", "dieses", "werden", "nicht", "sind",
            "soll", "muss", "kann", "darf", "circa", "sowie",
            "liefern", "einbauen", "setzen", "verlegen", "herstellen"}
    return words - stop


def _is_internal(bez):
    b = bez.lower()
    return any(k in b for k in [
        "baustelle einrichten", "baustelle raeumen", "baustelleneinrichtung",
        "vorankuendigung", "sige-plan", "lastplattendruckversuch",
        "kontrollpruefung", "grenzsteine", "lichtbilder", "bestandsplan",
        "regiearbeiten", "stundenlohn",
    ])


def _normalize_oz(oz):
    oz = re.sub(r'\s+', '', oz.strip().rstrip('.'))
    return '.'.join(p.lstrip('0') or '0' for p in oz.split('.'))
