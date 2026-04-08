"""Estimate market prices for unmatched LV positions using Claude AI.

Four sources of price intelligence (priority order):
1. Learned prices from user corrections (most precise — real project data)
2. Price database (scraped + offer prices, persisted)
3. Web search for current German Baustoff prices (results saved to DB)
4. Claude's construction knowledge with detailed reference tables
"""
import json
import re
import asyncio
from pathlib import Path
from datetime import datetime
import anthropic
import config
from matching.price_database import add_price, find_price, add_prices_from_offer


# ── Learned Prices Database ─────────────────────────────────────────────────

LEARNED_PRICES_FILE = Path("data/learned_prices.json")


def _ensure_learned_file():
    LEARNED_PRICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not LEARNED_PRICES_FILE.exists():
        LEARNED_PRICES_FILE.write_text("[]", encoding="utf-8")


def load_learned_prices() -> list:
    """Load all user-corrected prices."""
    _ensure_learned_file()
    try:
        return json.loads(LEARNED_PRICES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def save_learned_price(entry: dict):
    """Save a user correction as a learned price."""
    prices = load_learned_prices()
    entry["learned_at"] = datetime.now().isoformat()
    prices.append(entry)
    LEARNED_PRICES_FILE.write_text(
        json.dumps(prices, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[LEARNED] Saved: {entry.get('bezeichnung', '')[:50]} = {entry.get('ep')} €/{entry.get('einheit')}")


def find_learned_price(bezeichnung: str, einheit: str):
    """Find a matching learned price by product description."""
    prices = load_learned_prices()
    if not prices:
        return None

    bez_lower = bezeichnung.lower().strip()
    best_match = None
    best_score = 0

    for p in prices:
        p_bez = p.get("bezeichnung", "").lower().strip()
        # Calculate word overlap
        skip = {"und", "mit", "für", "aus", "von", "der", "die", "das", "den",
                "dem", "ein", "eine", "nach", "bis", "zum", "zur", "ca"}
        words_a = {w for w in bez_lower.split() if len(w) > 2 and w not in skip}
        words_b = {w for w in p_bez.split() if len(w) > 2 and w not in skip}
        if not words_a or not words_b:
            continue
        overlap = len(words_a & words_b)
        score = overlap / min(len(words_a), len(words_b))

        # Also check spec tokens (dimensions, DIN, DN, etc.)
        spec_pat = re.compile(r'\d+[/x×]\d+(?:[/x×]\d+)*|\d+(?:[.,]\d+)?|DIN\s*EN?\s*\d+|DN\s*\d+')
        specs_a = set(spec_pat.findall(bez_lower))
        specs_b = set(spec_pat.findall(p_bez))
        # Specs must match
        if specs_a and specs_b and not specs_a.issubset(specs_b):
            continue

        # Einheit should match
        if einheit and p.get("einheit") and einheit.lower() != p["einheit"].lower():
            score *= 0.5  # Penalize unit mismatch

        if score > best_score and score >= 0.7:
            best_score = score
            best_match = p

    return best_match


# ── Web Price Search ────────────────────────────────────────────────────────

async def search_web_prices(positions: list) -> dict:
    """Search the web for current German Baustoff prices.

    Returns dict: oz → {price_info: str, source: str}
    """
    results = {}

    # Group similar materials to minimize searches
    search_terms = {}
    for p in positions:
        bez = p["bezeichnung"]
        einheit = p.get("einheit", "")
        # Extract key material from bezeichnung
        material = _extract_material_name(bez)
        if material:
            search_terms[p["oz"]] = f"{material} Preis pro {einheit} netto 2026 Deutschland"

    if not search_terms:
        return results

    # Search for up to 8 unique materials
    unique_searches = list(set(search_terms.values()))[:8]
    search_results = {}

    for query in unique_searches:
        try:
            # Use a simple approach: format search results as context
            search_results[query] = await _do_web_search(query)
        except Exception as e:
            print(f"[WEB_PRICE] Search error for '{query}': {e}")

    # Map results back to positions
    for oz, query in search_terms.items():
        if query in search_results and search_results[query]:
            results[oz] = search_results[query]

    return results


def _extract_material_name(bezeichnung: str) -> str:
    """Extract the core material name from a LV Bezeichnung."""
    bez = bezeichnung.lower()
    # Remove common action words
    for w in ("liefern", "einbauen", "und", "setzen", "verlegen", "herstellen",
              "stärke", "ca.", "ca", "einschl.", "einschl"):
        bez = bez.replace(w, " ")
    # Clean up
    bez = re.sub(r'\s+', ' ', bez).strip()
    # Keep first ~5 meaningful words
    words = [w for w in bez.split() if len(w) > 2][:5]
    return " ".join(words)


async def _do_web_search(query: str) -> str:
    """Perform a web search and return price-relevant snippets."""
    try:
        # Use httpx/aiohttp to search via a simple Google-like query
        # For now, return empty — will be populated by Claude's web search tool
        return ""
    except Exception:
        return ""


# ── Main Estimation ─────────────────────────────────────────────────────────

ESTIMATE_PROMPT = """Du bist ein erfahrener Kalkulator im deutschen Bauwesen (GaLaBau, Tiefbau, Außenanlagen) mit 20+ Jahren Erfahrung.

Schätze realistische MATERIAL-Einzelpreise (EP netto) für folgende LV-Positionen, für die kein Lieferantenangebot vorliegt.

═══ WICHTIGE REGELN ═══

1. EINHEIT BEACHTEN: Der EP muss zur LV-Einheit passen!
   - Einheit "t" → Preis pro Tonne
   - Einheit "m3" oder "m³" → Preis pro Kubikmeter (NICHT Tonnenpreis!)
   - Einheit "m2" oder "m²" → Preis pro Quadratmeter
   - Einheit "m" oder "lfm" → Preis pro Laufmeter
   - Einheit "St" oder "Stk" → Preis pro Stück

2. UMRECHNUNG t → m³ (Schüttdichte beachten!):
   NIEMALS Tonnenpreis 1:1 als m³-Preis verwenden!
   - Kies: 1 m³ ≈ 1,8 t → m³-Preis = Tonnenpreis × 1,8
   - Sand: 1 m³ ≈ 1,6 t → m³-Preis = Tonnenpreis × 1,6
   - Splitt/Brechsand: 1 m³ ≈ 1,5 t → m³-Preis = Tonnenpreis × 1,5
   - Schotter: 1 m³ ≈ 1,8 t → m³-Preis = Tonnenpreis × 1,8
   - Asphalt: 1 m³ ≈ 2,4 t → m³-Preis = Tonnenpreis × 2,4
   - Mutterboden/Oberboden: 1 m³ ≈ 1,5 t → m³-Preis = Tonnenpreis × 1,5
   - Hackschnitzel: 1 m³ ≈ 0,25-0,35 t
   - Beton: 1 m³ ≈ 2,4 t

3. NUR MATERIALANTEIL schätzen:
   - "liefern und einbauen" → NUR Materialkosten, KEIN Lohn
   - "liefern und setzen" → NUR Materialkosten
   - Reine Arbeitsleistungen (verdichten, schneiden, Planum etc.) → skip: true

4. NEBENMATERIAL NICHT VERGESSEN:
   - Bordstein setzen → braucht Frischbeton für Fundament + Rückenstütze
   - Pflaster/Platten → braucht Splitt-Bettung + Fugenmaterial
   - Wenn die LV-Pos. NUR das Hauptmaterial beschreibt und Nebenmaterial separate Positionen hat → NUR Hauptmaterial schätzen

5. PREISREFERENZEN (deutsche Marktpreise 2025/2026 netto, ab Werk + Fracht):
   Schüttgüter:
   - RC-Schotter 0/45: 8-12 €/t → 14-22 €/m³
   - Schotter 0/32: 12-18 €/t → 22-32 €/m³
   - Splitt 2/5: 18-25 €/t → 27-38 €/m³
   - Brechsand 0/2: 15-20 €/t → 23-30 €/m³
   - Sand 0/2: 8-12 €/t → 13-19 €/m³
   - Mutterboden/Oberboden: 12-18 €/t → 18-27 €/m³
   - Kies 0/32: 10-15 €/t → 18-27 €/m³

   Beton (Transportbeton inkl. alle Zuschläge):
   - C12/15: 100-120 €/m³
   - C20/25: 115-135 €/m³
   - C25/30: 125-145 €/m³
   - C30/37: 135-160 €/m³

   Pflaster/Platten:
   - Betonpflaster Standard 8cm: 15-25 €/m²
   - Betonpflaster Verbund 8cm: 20-32 €/m²
   - Mosaikpflaster Granit 4/6: 45-70 €/m²
   - Natursteinpflaster: 35-65 €/m²
   - Gehwegplatten Beton 30×30: 15-25 €/m²
   - Sandstein Mauersteine 70-120×50×50: 200-350 €/m

   Bordsteine/Einfassungen:
   - Tiefbordstein 8/20/100: 3-5 €/m
   - Tiefbordstein 8/30/100: 4-7 €/m
   - Hochbordstein 15/25/100: 8-14 €/m
   - Hochbordstein 15/30/100: 10-16 €/m
   - Rasenkantenstein: 3-6 €/m

   Rohre:
   - KG-Rohr DN100: 5-9 €/m
   - KG-Rohr DN150: 9-15 €/m
   - KG-Rohr DN200: 14-22 €/m
   - PE-Rohr DN100: 16-28 €/m
   - Drainagerohr DN100: 4-8 €/m

   Bäume (Hochstamm, 3xv mDb):
   - StU 14-16: 180-260 €/St
   - StU 16-18: 240-340 €/St
   - StU 18-20: 300-420 €/St
   - StU 20-25: 400-600 €/St

   Sträucher (Container C3):
   - 60-100 cm: 4-12 €/St
   - 100-150 cm: 10-22 €/St

   Fallschutz:
   - Hackschnitzel DIN EN 1176: 45-65 €/m³ (Fallschutz-Qualität!)
   - Fallschutzplatten SBR: 35-55 €/m²

   Bodenbearbeitung/Material:
   - Vegetationssubstrat (Perlith-Basis): 80-120 €/t
   - Rasensubstrat: 30-50 €/t
   - Rasensaatgut RSM: 4-8 €/m²
   - Wurzelschutzsystem: 80-150 €/St

   Sonstiges:
   - Vlies/Geotextil: 1-3 €/m²
   - Wurzelschutzfolie: 5-12 €/m²
   - Baumverankerung (Dreibock): 40-70 €/St
   - Stammschutz: 15-30 €/St

6. REASONING: Zeige die Kalkulation! z.B. "RC-Schotter 0/45: ca. 10€/t, Einheit=t → EP 10,00€/t"
   oder "Splitt 2/5: ca. 20€/t × 1,5 t/m³ = 30€/m³"

{learned_context}
{langtext_context}

═══ POSITIONEN ZUM SCHÄTZEN ═══
{positions_json}

Antworte als JSON-Array:
[
  {{
    "oz": "die OZ",
    "ep": geschätzter Einzelpreis in EUR (passend zur Einheit!),
    "skip": false,
    "reasoning": "Kalkulation: Material × Faktor = EP pro Einheit"
  }}
]

Für reine Arbeitspositionen (ohne Material):
  {{
    "oz": "die OZ",
    "ep": 0,
    "skip": true,
    "reasoning": "Reine Arbeitsleistung, kein Materialpreis"
  }}

Antworte NUR mit validem JSON-Array:"""


async def estimate_missing_prices(positions: list, gaeb_data: dict = None, project_plz: str = "") -> list:
    """Estimate prices for LV positions without offer matches.

    Uses three sources:
    1. Learned prices from user corrections (highest priority)
    2. Web search for current prices
    3. Claude AI estimation with detailed construction knowledge

    Args:
        positions: List of dicts with oz, bezeichnung, einheit, menge
        gaeb_data: Optional GAEB data for Langtext context

    Returns:
        List of dicts with oz, ep, skip, reasoning
    """
    if not positions or not config.ANTHROPIC_API_KEY:
        return []

    results = []
    remaining = []

    # ── Step 1: Check learned prices first (from user corrections) ──
    for p in positions:
        learned = find_learned_price(p["bezeichnung"], p.get("einheit", ""))
        if learned:
            results.append({
                "oz": p["oz"],
                "ep": learned["ep"],
                "skip": False,
                "reasoning": f"Aus Erfahrungswert: {learned.get('bezeichnung', '')[:50]} = {learned['ep']}€/{learned.get('einheit', '')} (Projekt: {learned.get('project', 'früher')})",
                "source": "learned",
            })
            print(f"[PRICE_EST] Learned price for {p['oz']}: {learned['ep']}€")
            continue

        # ── Step 2: Check price database (scraped + offer prices) ──
        db_matches = find_price(p["bezeichnung"], p.get("einheit", ""), project_plz=project_plz)
        if db_matches:
            best = db_matches[0]
            results.append({
                "oz": p["oz"],
                "ep": best["ep"],
                "skip": False,
                "reasoning": f"Aus Preisdatenbank: {best['material'][:50]} = {best['ep']}€/{best['einheit']} (Quelle: {best.get('source', '?')}, {best.get('date', '')[:10]})",
                "source": "database",
            })
            print(f"[PRICE_EST] DB price for {p['oz']}: {best['ep']}€ ({best.get('source')})")
            continue

        remaining.append(p)

    if not remaining:
        return results

    # ── Step 3: Claude estimation with all context ──
    client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

    # Build GAEB Langtext lookup
    gaeb_lookup = {}
    if gaeb_data and gaeb_data.get("positions"):
        for gp in gaeb_data["positions"]:
            oz = _normalize_oz(gp.get("oz", ""))
            if oz:
                gaeb_lookup[oz] = gp.get("langtext", "")

    # Build position list with Langtext
    pos_list = []
    for p in remaining:
        entry = {
            "oz": p["oz"],
            "bezeichnung": p["bezeichnung"],
            "einheit": p.get("einheit", ""),
            "menge": p.get("menge", 0),
        }
        oz_norm = _normalize_oz(p["oz"])
        langtext = gaeb_lookup.get(oz_norm, "")
        if langtext:
            entry["langtext"] = langtext[:500]
        # Add price range hint if available (from price_validator)
        if p.get("_price_hint"):
            entry["preisbereich"] = p["_price_hint"]
        pos_list.append(entry)

    # Build learned context (show Claude what prices we already know)
    learned_context = _build_learned_context()
    langtext_context = ""
    if any("langtext" in p for p in pos_list):
        langtext_context = "\n═══ HINWEIS ═══\nBei Positionen mit 'langtext' findest du detaillierte Spezifikationen. Nutze diese für präzisere Schätzungen.\n"

    prompt = ESTIMATE_PROMPT.format(
        positions_json=json.dumps(pos_list, ensure_ascii=False, indent=2),
        learned_context=learned_context,
        langtext_context=langtext_context,
    )

    try:
        response = await client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        print(f"[PRICE_EST] Claude response: {len(text)} chars")

        # Parse JSON
        if "```" in text:
            parts = text.split("```")
            for part in parts:
                p = part.strip()
                if p.startswith("json"):
                    p = p[4:].strip()
                if p.startswith("["):
                    text = p
                    break

        text = re.sub(r',\s*]', ']', text)
        text = re.sub(r',\s*}', '}', text)

        ai_results = json.loads(text)
        if isinstance(ai_results, list):
            for r in ai_results:
                r["source"] = "ai"
                # Save AI estimates to price database for future reuse
                if not r.get("skip") and r.get("ep", 0) > 0:
                    # Find the position to get bezeichnung
                    for p in remaining:
                        if p["oz"] == r.get("oz"):
                            add_price(
                                material=p["bezeichnung"],
                                ep=r["ep"],
                                einheit=p.get("einheit", ""),
                                source="ai-estimate",
                                details=r.get("reasoning", ""),
                            )
                            break
            results.extend(ai_results)

    except Exception as e:
        print(f"[PRICE_EST] Error: {e}")

    return results


def _build_learned_context() -> str:
    """Build context from learned prices to help Claude be more precise."""
    prices = load_learned_prices()
    if not prices:
        return ""

    # Group by material type, show most recent
    lines = ["\n═══ ERFAHRUNGSWERTE AUS FRÜHEREN PROJEKTEN ═══",
             "(Diese Preise wurden vom Benutzer bestätigt — nutze sie als Referenz!)"]

    # Show last 30 learned prices
    recent = sorted(prices, key=lambda p: p.get("learned_at", ""), reverse=True)[:30]
    for p in recent:
        bez = p.get("bezeichnung", "")[:60]
        ep = p.get("ep", 0)
        einheit = p.get("einheit", "")
        reason = p.get("reason", "")[:40]
        lines.append(f"- {bez}: {ep:.2f} €/{einheit}" + (f" ({reason})" if reason else ""))

    return "\n".join(lines)


def _normalize_oz(oz: str) -> str:
    """Normalize OZ for comparison."""
    oz = re.sub(r'\s+', '', oz.strip().rstrip('.'))
    parts = oz.split('.')
    return '.'.join(p.lstrip('0') or '0' for p in parts)
