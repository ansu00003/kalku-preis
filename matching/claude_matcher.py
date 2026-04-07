"""Claude-powered matching: primary matcher for offer-to-LV assignments."""
import asyncio
import json
import re
from typing import List, Optional
import anthropic
import config


MATCH_PROMPT = """Du bist ein erfahrener Kalkulator im deutschen Bauwesen (Außenanlagen, Tiefbau, GaLaBau).

Ordne die Angebotspositionen den LV-Positionen zu.

REGELN:
- "lv_oz" MUSS EXAKT einer der OZ-Werte aus der LV-Liste unten sein. Verwende GENAU die Schreibweise wie in der Liste (z.B. "1.  10" mit Leerzeichen)!
- PRÜFE GENAU ob das angebotene Produkt zur LV-Beschreibung UND zum GAEB-Langtext passt:
  - Vergleiche Material (Stahl vs. Alu, Beton vs. Kunststoff) — verschiedenes Material = KEIN Match!
  - KRITISCH: Vergleiche ALLE Spezifikationen — wenn auch nur EINE nicht passt, setze warning!
  - Vergleiche Maße, Nennweiten (DN/NW), Stärken, Abmessungen
  - Vergleiche Typ/Art (z.B. Betonpflaster vs. Natursteinpflaster)
- Wenn das Angebot ÄHNLICH aber NICHT EXAKT passt (z.B. andere Größe, anderes Material, andere Stärke):
  - Setze match=true ABER schreibe eine WARNING mit der genauen Abweichung
  - z.B. "Angebot: DN200, LV verlangt: DN150" oder "Angebot: 8cm Stärke, LV verlangt: 6cm"
- MATERIALGRUPPEN: Ordne NUR innerhalb gleicher Materialgruppe zu!
  * Betonpflaster != Natursteinpflaster != Klinkerpflaster
  * KG-Rohr != PE-Rohr != PP-Rohr (verschiedene Materialien!)
  * Schotter != Kies != Splitt (verschiedene Koernungen!)
  * Granit != Sandstein != Basalt
  * Betonbord != Granitbord != Natursteinbord
  Verschiedenes Material = KEIN Match!
- Wenn das Angebot NICHT passt → nicht aufführen
- Positionen mit "ALTERNATIV:" im Text sind alternative Produktvorschläge. Sie sind KONKURRIERENDE Angebote (Hauptmaterial), NICHT Nebenmaterial! Wenn eine Alternativ-Position das GLEICHE Produkt in anderer Variante ist (z.B. Bogen 30 Grad als Alternative zu Bogen 15 Grad), ordne sie NICHT zu — nur die passende Hauptvariante zuordnen.
- Wenn eine LV-Position MEHRERE Materialien braucht (siehe Langtext), ordne ALLE passenden Angebote zu
- BAUWISSEN — diese Positionen brauchen IMMER zusätzliche Materialien:
  - L-Steine / Winkelstützelemente → brauchen UNTERBETON (Magerbeton/Fundament C12/15)
  - Bordsteine / Einfassungen → brauchen Rückenstütze aus Beton
  - Pflaster / Plattenbeläge → brauchen Bettungsmaterial (Splitt/Sand) und ggf. Tragschicht
  - Rohrleitungen (PE, KG, etc.) → Rohr + Formstücke + Muffen/Schweißmuffen. Wenn Angebot Rohr+Muffen separat listet, BEIDE zuordnen!
  - Entwässerungsrinnen (D400, NW100 etc.) → Rinnenkörper + Gussrost/Abdeckung + Zargen + Befestigung. ALLE Teile zuordnen!
  - Schachtbauwerke → Schachtringe + Konus + Abdeckung + Steighilfen
  - Zaunpfosten → brauchen Punktfundamente (Beton)
  - Straßenabläufe / Einlaufbauwerke → Ablaufkörper + Rost + Aufsatzstück + Anschlussrohr
  Ordne bei solchen Positionen ALLE zugehörigen Materialangebote zu, auch wenn im Langtext nicht explizit erwähnt!
- Bestimme ob Stoffe (Spalte X) oder Nachunternehmer (Spalte M):
  - Asphalt, Metallbau, Elektro, Zaunbau, Bewässerung, Steinmetz → M (Nachunternehmer)
  - Liefermaterialien (Schotter, Rohre, Pflaster, Sand, etc.) → X (Stoffe)

LV-POSITIONEN (mit GAEB-Langtext wenn verfügbar):
{lv_context}

ANGEBOTSPOSITIONEN:
{offers_json}

Antworte als JSON-Array. Für JEDE sinnvolle Zuordnung:
{{
  "lv_oz": "EXAKTE OZ aus der LV-Liste oben",
  "offer_idx": <Index des Angebots>,
  "match": true,
  "confidence": 0-100,
  "column": "X" oder "M",
  "material_type": "Hauptmaterial" oder "Nebenmaterial" — wenn z.B. L-Stein das Hauptmaterial ist und Unterbeton das Nebenmaterial, dann ist der Unterbeton "Nebenmaterial". Verschiedene Angebote für das GLEICHE Produkt (z.B. zwei Lieferanten für L-Steine) sind beide "Hauptmaterial".
  "reason": "Was passt: Produkt X = LV-Position Y weil ...",
  "warning": "PFLICHT wenn Abweichung! z.B. 'Angebot: 40mm, LV: 45mm' oder 'Angebot: Kunststoff, LV: Stahl'. Leer wenn exakte Übereinstimmung."
}}

Wenn KEIN Angebot zu einer LV-Position passt, NICHT aufführen.
Antwort NUR als JSON-Array:"""


async def claude_match_all(
    lv_positions: list,
    all_offer_items: list,
    gaeb_data: Optional[dict] = None,
) -> list:
    """Use Claude as primary matcher for all offer-to-LV assignments.

    Args:
        lv_positions: LV positions from Excel
        all_offer_items: All extracted offer items with supplier info
        gaeb_data: Optional GAEB data for Langtext context

    Returns: List of match dicts
    """
    if not config.ANTHROPIC_API_KEY:
        return []

    client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

    # Build GAEB Langtext lookup
    gaeb_lookup = {}
    if gaeb_data and gaeb_data.get("positions"):
        for gp in gaeb_data["positions"]:
            oz = _normalize_oz(gp.get("oz", ""))
            if oz:
                gaeb_lookup[oz] = gp.get("langtext", "")

    # Build LV context with Langtext
    lv_lines = []
    for pos in lv_positions:
        oz = pos["oz"]
        line = f"OZ {oz}: {pos['bezeichnung']} | Menge: {pos['menge']} {pos['einheit']}"
        langtext = gaeb_lookup.get(_normalize_oz(oz), "")
        if langtext:
            # Truncate long Langtext
            langtext_short = langtext[:300] + "..." if len(langtext) > 300 else langtext
            line += f"\n  Langtext: {langtext_short}"
        lv_lines.append(line)

    lv_with_langtext = sum(1 for l in lv_lines if "Langtext:" in l)
    print(f"[CLAUDE_MATCH] GAEB lookup: {len(gaeb_lookup)} entries, {lv_with_langtext}/{len(lv_positions)} LV positions have Langtext")
    lv_context = "\n".join(lv_lines)

    # Build flat offer list with indices
    offers_lines = []
    for i, item in enumerate(all_offer_items):
        ep = item.get("ep", 0) or 0
        gp = item.get("gp", 0) or 0
        offers_lines.append({
            "idx": i,
            "supplier": item.get("supplier", ""),
            "text": item.get("text", "")[:200],
            "ep": ep,
            "einheit": item.get("einheit", ""),
            "menge": item.get("menge", 0),
            "gp": gp,
        })

    # Split into batches and run in parallel
    batch_size = 50
    batches = []
    for batch_start in range(0, len(offers_lines), batch_size):
        batches.append((batch_start, offers_lines[batch_start:batch_start + batch_size]))

    async def _match_batch(batch_start, batch):
        prompt = MATCH_PROMPT.format(
            lv_context=lv_context,
            offers_json=json.dumps(batch, ensure_ascii=False, indent=2),
        )
        try:
            response = await client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            print(f"[CLAUDE_MATCH] Batch {batch_start}: response length={len(text)}, first 200 chars: {text[:200]}")
            results = _parse_json_array(text)
            print(f"[CLAUDE_MATCH] Batch {batch_start}: parsed {len(results)} matches")
            return results
        except Exception as e:
            print(f"Claude matching error (batch {batch_start}): {e}")
            return []

    batch_results = await asyncio.gather(*[_match_batch(s, b) for s, b in batches])

    all_results = []
    for results in batch_results:
        all_results.extend(results)
    return all_results


def determine_column(bezeichnung: str) -> str:
    """Determine if a position is Stoffe (X) or NU (M) based on description."""
    text = bezeichnung.lower()

    for trade in config.NU_TRADES:
        if trade in text:
            return "M"

    # GaLaBau labor positions → NU
    NU_WORK = (
        "pflanzen", "pflanzung", "ansaat", "einsaat", "rasen anlegen",
        "fertigstellungspflege", "entwicklungspflege",
        "abnahme", "erstinspektion", "prüfbericht",
        "planum", "verdichten", "planieren",
        "nassschneiden", "schneiden",
        "abbruch", "rückbau", "rodung", "fällung", "baumfällung",
        # Gutachter / Labor / Dienstleistungen
        "untersuchung", "probenahme", "beprobung", "laboruntersuchung",
        "gutachten", "gutachter", "sachverständig",
        "verrechnungssatz", "verrechnungsatz", "stundensatz",
        "projektleitung", "projektbearbeitung", "geschäftsleitung",
        "baustellentermin", "bauüberwachung", "baubegleitung",
        "kampfmittel", "kampfmittelsondierung",
        "baugrunduntersuchung", "bodengutachten", "rammsondierung",
        "geotechnik", "abfallrechtl",
    )
    if any(k in text for k in NU_WORK):
        # Exception: "liefern und pflanzen" has material component → still Stoffe
        # But pure "pflanzen" without "liefern" → NU
        if any(k in text for k in ("liefer", "material", "liefern und")):
            return "X"
        return "M"

    if any(k in text for k in ("komplett", "liefern und", "einbauen", "montage", "installation")):
        if any(k in text for k in ("asphalt", "metall", "elektr", "zaun", "geländer")):
            return "M"

    return "X"


def _normalize_oz(oz: str) -> str:
    """Normalize OZ for comparison: '01.04.0010' → '1.4.10', '4.1.10.' → '4.1.10'"""
    oz = re.sub(r'\s+', '', oz.strip().rstrip('.'))
    # Strip leading zeros from each segment
    parts = oz.split('.')
    return '.'.join(p.lstrip('0') or '0' for p in parts)


def _parse_json_array(text: str) -> list:
    """Robustly parse a JSON array from Claude's response."""
    # Clean markdown fences
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            p = part.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("["):
                text = p
                break

    # Try direct parse
    try:
        result = json.loads(text)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        pass

    # Find array in text
    match = re.search(r'\[[\s\S]*\]', text)
    if match:
        try:
            cleaned = match.group()
            cleaned = re.sub(r',\s*]', ']', cleaned)
            cleaned = re.sub(r',\s*}', '}', cleaned)
            result = json.loads(cleaned)
            return result if isinstance(result, list) else []
        except json.JSONDecodeError:
            pass

    return []
