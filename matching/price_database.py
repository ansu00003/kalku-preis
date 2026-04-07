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


def add_prices_from_offer(offer_items: list, supplier: str):
    """Bulk-add prices from a parsed offer into the database."""
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
            details=f"Menge: {item.get('menge', '')}, GP: {item.get('gp', '')}",
        )
        count += 1
    print(f"[PRICE_DB] Added {count} prices from offer '{supplier}'")


def find_price(search_term: str, einheit: str = "", max_results: int = 5) -> list:
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

    scored = []
    for entry in db["prices"]:
        mat_lower = entry.get("material_lower", "")
        mat_words = {w for w in mat_lower.split() if len(w) > 2 and w not in skip}

        if not search_words or not mat_words:
            continue

        # Word overlap score
        overlap = len(search_words & mat_words)
        score = overlap / min(len(search_words), len(mat_words)) if min(len(search_words), len(mat_words)) > 0 else 0

        if score < 0.5:
            continue

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
