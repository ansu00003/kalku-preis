"""Persistent price database — stores all scraped, learned, and offer prices for reuse."""
import json
import re
from pathlib import Path
from datetime import datetime

DB_FILE = Path("data/price_database.json")


def _ensure_db():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not DB_FILE.exists():
        DB_FILE.write_text('{"prices": [], "last_updated": ""}', encoding="utf-8")


def load_db() -> dict:
    _ensure_db()
    try:
        return json.loads(DB_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return {"prices": [], "last_updated": ""}


def save_db(db: dict):
    db["last_updated"] = datetime.now().isoformat()
    DB_FILE.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")



def normalize_material(text: str) -> str:
    """Normalize material name for better database matching.

    Strips action words, keeps only material-relevant terms.
    'Awadukt PP Rohr SN10 blau OD 315 mm BL=1m' → 'pp rohr sn10 dn315'
    """
    import re as _re
    t = text.lower().strip()
    # Remove action words
    for w in ("liefern", "einbauen", "setzen", "verlegen", "herstellen",
              "und", "mit", "für", "aus", "von", "der", "die", "das",
              "inkl", "inkl.", "einschl", "einschl.", "ca.", "ca",
              "steckmuffe", "en 1852", "en1852", "en 1338", "en 1339",
              "en 1340", "din 4034", "din 18500",
              "blau", "orange", "grau", "grün", "schwarz", "weiß",
              "anthrazit", "natur", "rot", "braun", "gelb",
              "nach", "gem.", "gemäß", "lt.", "laut", "entspr.",
              "liefern und einbauen", "frei baustelle"):
        t = t.replace(w, " ")
    # Normalize DN/OD
    t = _re.sub(r'od\s*(\d+)', r'dn\1', t)
    # Remove BL= (Baulänge — varies, not material-defining)
    t = _re.sub(r'bl\s*=?\s*\d+\s*m', '', t)
    # Remove article numbers
    t = _re.sub(r'\b\d{5,}\b', '', t)
    # Clean up
    t = _re.sub(r'\s+', ' ', t).strip()
    # Keep only words with 2+ chars
    words = [w for w in t.split() if len(w) >= 2]
    return " ".join(words)


def extract_plz(text: str) -> str:
    """Extract German PLZ (5-digit postal code) from text.

    Searches in order:
    1. Delivery address (Warenempfänger, Lieferadresse)
    2. Sender address (Briefkopf)
    3. Any 5-digit number that looks like a PLZ

    Returns: PLZ string or "" if not found
    """
    import re as _re
    if not text:
        return ""
    # Common patterns: "65201 Wiesbaden", "D-55122 Mainz"
    # Search near address keywords first
    addr_keywords = ("warenempfänger", "lieferadresse", "lieferanschrift",
                     "versandanschrift", "objekt", "baustelle", "bauvorhaben")
    lines = text.split("\n")
    for i, line in enumerate(lines):
        ll = line.lower()
        if any(kw in ll for kw in addr_keywords):
            # Search next 5 lines for PLZ
            for j in range(i, min(i + 6, len(lines))):
                m = _re.search(r'\b(?:D-?)?(\d{5})\b', lines[j])
                if m:
                    plz = m.group(1)
                    if plz[0] in "0123456789" and 1000 <= int(plz) <= 99999:
                        return plz
    # Fallback: find any PLZ pattern (after a city prefix or standalone)
    # Prioritize PLZ that appears with a city name
    for m in _re.finditer(r'\b(?:D-?)?(\d{5})\s+[A-ZÄÖÜ][a-zäöüß]', text):
        plz = m.group(1)
        if 1000 <= int(plz) <= 99999:
            return plz
    return ""


def plz_distance_score(plz_a: str, plz_b: str) -> float:
    """Score how close two PLZ regions are (0.0 = far, 1.0 = same).

    German PLZ system:
    - Same PLZ = 1.0 (exact same area)
    - Same 3 digits = 0.8 (same city/Kreis, ~20km)
    - Same 2 digits = 0.5 (same region, ~50-100km)
    - Same 1 digit = 0.2 (same PLZ-Zone, ~100-300km)
    - Different = 0.0
    """
    if not plz_a or not plz_b:
        return 0.3  # Unknown → neutral score
    if plz_a == plz_b:
        return 1.0
    if plz_a[:3] == plz_b[:3]:
        return 0.8
    if plz_a[:2] == plz_b[:2]:
        return 0.5
    if plz_a[:1] == plz_b[:1]:
        return 0.2
    return 0.0



def add_price(material: str, ep: float, einheit: str, source: str,
              region: str = "", details: str = "", url: str = ""):
    """Add a price entry to the database.

    Args:
        material: Material name/description
        ep: Price per unit (netto)
        einheit: Unit (t, m³, m², m, St, etc.)
        source: Where the price came from (web-scrape, offer, user-correction, sirados)
        region: Region/PLZ if known
        details: Additional details (specs, dimensions, etc.)
        url: Source URL if scraped
    """
    db = load_db()
    entry = {
        "material": material.strip(),
        "material_lower": material.strip().lower(),
        "material_normalized": normalize_material(material),
        "ep": ep,
        "einheit": einheit.strip(),
        "source": source,
        "region": region,
        "details": details,
        "url": url,
        "date": datetime.now().isoformat(),
    }

    # Avoid exact duplicates (same material, price, unit, source)
    for existing in db["prices"]:
        if (existing.get("material_lower") == entry["material_lower"]
            and abs(existing.get("ep", 0) - ep) < 0.01
            and existing.get("einheit") == einheit
            and existing.get("source") == source):
            return  # Skip duplicate

    db["prices"].append(entry)
    save_db(db)
    print(f"[PRICE_DB] Added: {material[:50]} = {ep:.2f} €/{einheit} ({source})")


def add_prices_from_offer(offer_items: list, supplier: str, offer_text: str = ""):
    """Bulk-add prices from a parsed offer into the database.
    
    Args:
        offer_items: List of extracted offer positions
        supplier: Supplier name
        offer_text: Full offer text (for PLZ extraction)
    """
    # Extract PLZ from offer text (supplier location or delivery address)
    plz = extract_plz(offer_text) if offer_text else ""
    if plz:
        print(f"[PRICE_DB] Extracted PLZ {plz} from offer '{supplier}'")
    count = 0
    for item in offer_items:
        ep = float(item.get("ep", 0) or 0)
        if ep <= 0:
            continue
        text = item.get("text", "").strip()
        einheit = item.get("einheit", "").strip()
        if not text or not einheit:
            continue
        add_price(
            material=text,
            ep=ep,
            einheit=einheit,
            source=f"offer:{supplier}",
            region=plz,
            details=f"Menge: {item.get('menge', '')}, GP: {item.get('gp', '')}",
        )
        count += 1
    print(f"[PRICE_DB] Added {count} prices from offer '{supplier}'")


def find_price(search_term: str, einheit: str = "", max_results: int = 5, project_plz: str = "") -> list:
    """Search the database for matching prices.

    Returns list of matching entries, sorted by relevance and recency.
    """
    db = load_db()
    search_lower = search_term.lower().strip()

    # Extract key words
    skip = {"und", "mit", "für", "aus", "von", "der", "die", "das", "den",
            "dem", "ein", "eine", "nach", "bis", "zum", "zur", "ca", "liefern",
            "einbauen", "setzen", "verlegen", "herstellen"}
    search_words = {w for w in search_lower.split() if len(w) > 2 and w not in skip}

    # Extract spec tokens
    spec_pat = re.compile(r'\d+[/x×]\d+(?:[/x×]\d+)*|\d+(?:[.,]\d+)?|DIN\s*EN?\s*\d+|DN\s*\d+')
    search_specs = set(spec_pat.findall(search_lower))

    search_normalized = normalize_material(search_term)
    search_norm_words = set(search_normalized.split())

    scored = []
    for entry in db["prices"]:
        mat_lower = entry.get("material_lower", "")
        mat_words = {w for w in mat_lower.split() if len(w) > 2 and w not in skip}

        # Also try normalized matching
        mat_normalized = entry.get("material_normalized", normalize_material(mat_lower))
        mat_norm_words = set(mat_normalized.split())

        if not search_words and not search_norm_words:
            continue
        if not mat_words and not mat_norm_words:
            continue

        # Word overlap score (try both raw and normalized)
        overlap_raw = len(search_words & mat_words) if search_words and mat_words else 0
        overlap_norm = len(search_norm_words & mat_norm_words) if search_norm_words and mat_norm_words else 0

        # Use the better of the two
        if overlap_raw > 0 and search_words and mat_words:
            score_raw = overlap_raw / min(len(search_words), len(mat_words))
        else:
            score_raw = 0
        if overlap_norm > 0 and search_norm_words and mat_norm_words:
            score_norm = overlap_norm / min(len(search_norm_words), len(mat_norm_words))
        else:
            score_norm = 0

        score = max(score_raw, score_norm)

        if score < 0.4:
            continue

        # Region bonus: prefer prices from nearby locations
        entry_plz = entry.get("region", "")
        if project_plz:
            plz_score = plz_distance_score(project_plz, entry_plz)
            score += plz_score * 0.25  # Up to +0.25 for same PLZ
            if plz_score == 0 and entry_plz:
                score -= 0.1  # Small penalty for distant regions

        # Bonus for spec match
        mat_specs = set(spec_pat.findall(mat_lower))
        if search_specs and mat_specs:
            if search_specs.issubset(mat_specs):
                score += 0.3  # Exact spec match bonus
            elif search_specs & mat_specs:
                score += 0.1  # Partial spec match

        # Bonus for unit match
        if einheit and entry.get("einheit", "").lower() == einheit.lower():
            score += 0.2

        # Bonus for recency (newer = better)
        try:
            age_days = (datetime.now() - datetime.fromisoformat(entry.get("date", "2020-01-01"))).days
            recency_bonus = max(0, 0.1 - age_days / 3650)  # Up to 0.1 bonus for recent
            score += recency_bonus
        except (ValueError, TypeError):
            pass

        # Bonus for user-corrected prices (most trustworthy)
        if entry.get("source") == "user-correction":
            score += 0.3

        scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [entry for _, entry in scored[:max_results]]


def get_stats() -> dict:
    """Get database statistics."""
    db = load_db()
    prices = db["prices"]
    sources = {}
    for p in prices:
        src = p.get("source", "unknown").split(":")[0]
        sources[src] = sources.get(src, 0) + 1
    return {
        "total": len(prices),
        "sources": sources,
        "last_updated": db.get("last_updated", ""),
    }
