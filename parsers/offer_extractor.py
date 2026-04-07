"""Extract structured offer data from raw PDF text using Claude API."""
import json
import re
from typing import Optional
import anthropic
import config


EXTRACTION_PROMPT = """Du bist ein Experte für Bauangebote im deutschen Bauwesen. Extrahiere ALLE Positionen aus diesem Lieferanten-Angebot.

WICHTIG bei gescannten Dokumenten: Unterscheide zwischen GEDRUCKTEN und HANDSCHRIFTLICHEN Preisen!
Wenn ein Preis handschriftlich eingetragen ist (unregelmäßige Schrift, Kugelschreiber, nicht in Druckschrift), extrahiere ihn trotzdem, aber setze "handschriftlich": true bei dieser Position.
Wenn der Preis gedruckt/getippt ist, setze "handschriftlich": false.
Handschrift erkennt man an: unregelmäßiger Schrift, Kursivität, Stift-Unterstreichungen, Zahlen die neben (nicht in) Tabellenzeilen stehen.

Angebot von: {supplier_name}
Datei: {filename}

TEXT:
{text}

Extrahiere JEDE Position als JSON-Array. Für jede Position:
- "pos": Positionsnummer im Angebot (wenn vorhanden, sonst "")
- "lv_pos_nr": LV-Positionsnummer wenn im Angebot angegeben (z.B. "LV-POS-NR.: 01.04.0020" → "01.04.0020"). WICHTIG: Suche nach "LV-Pos", "LV-POS-NR", "Pos.-Nr.", "zu Pos." oder ähnlichen Verweisen auf LV-Positionen. ACHTUNG: Die LV-Nummer steht oft UNTER oder NACH der Produktbeschreibung/Preis — sie gehört zur Position DARÜBER, nicht zur nächsten!
REGEL: Lies die LV-Nummer und weise sie der Position zu, die VOR der LV-Nummer steht. Dann VERERBE diese LV-Nummer an alle folgenden Positionen BIS eine neue LV-Nummer erscheint.
Beispiel 1: "Pos 30 Passlänge 80,00 / LV-POS-NR.: 2.3.20 / Pos 40 Doppelsteckmuffe 91,00 / LV-POS-NR.: 2.3.30" → Passlänge=2.3.20, Doppelsteckmuffe=2.3.30
Beispiel 2: "LV-Pos. 04.1 / Pos 30 Rinne 54,73 / Pos 40 Gitterrost 69,47 / LV-Pos. 04.2 / Pos 60 Rinne 54,73" → Rinne(30)=04.1, Gitterrost(40)=04.1 (geerbt!), Rinne(60)=04.2. Die Gitterrost gehört zu 04.1 weil LV-Pos 04.2 erst DANACH kommt!
Wenn nicht angegeben und nicht vererbbar, setze "".
- "text": Produktbezeichnung/Beschreibung (vollständig!). Wenn die Position als "Alternativ" oder "Alternative" markiert ist, schreibe "ALTERNATIV: " vor die Beschreibung.
- "menge": Angebotene Menge (Zahl, 0 wenn nicht angegeben)
- "einheit": Einheit (m, m², m³, t, St, kg, lfm, ps, etc.)
- "ep": EINZELPREIS (EP) netto in EUR. KRITISCH:
  * EP steht in der VORLETZTEN Preis-Spalte ("E.-Preis", "EP", "Einzelpreis")
  * GP/Betrag steht in der LETZTEN Spalte ("Betrag", "GP", "Gesamtpreis")
  * Kontrolliere: EP x Menge = GP (ungefaehr). Wenn nicht, Spalten verwechselt!
  * Wenn kein EP angegeben, setze 0
- "gp": Gesamtpreis (GP) netto in EUR (wie im Dokument angegeben, 0 wenn nicht angegeben)
- "rabatt": Rabatt/Skonto in Prozent (z.B. 3 für 3%), 0 wenn nicht angegeben
- "handschriftlich": true wenn der EP/GP handschriftlich eingetragen wurde, false wenn gedruckt
- "stueck_laenge": Länge/Baulänge pro Stück in Metern wenn in Beschreibung angegeben (z.B. "BL=12 m" → 12, "Stablänge 6m" → 6, "Länge 2,00m" → 2, "L=1m" → 1). 0 wenn nicht angegeben. WICHTIG: Suche nach BL=, Baulänge, Stablänge, Länge, L= in der Artikelbeschreibung.

Zusätzlich extrahiere Nebenkosten (NUR allgemein gültige Kosten für das gesamte Angebot):
- "fracht": Frachtkosten in EUR — NUR wenn sie für ALLE/die meisten Positionen gelten. Wenn Frachtkosten nur für einen bestimmten Produktblock gelten (z.B. nur für Alternativ-Positionen), setze 0. Wenn "frei Baustelle", "Kommission Zufuhr" oder "Lieferung inklusive" steht, ist die Fracht bereits im Preis enthalten → setze 0.
- "verpackung": Verpackungskosten — NUR allgemeine Verpackungskosten. Wenn nur für einen bestimmten Produktblock (z.B. "Pauschale für Fortis-Produkte"), setze 0.
- "kran": Kranentladung (0 wenn nicht angegeben)
- "sonstige_nk": Sonstige Nebenkosten (0 wenn nicht angegeben)
- "logistik_pct": Logistikzuschlag in Prozent (z.B. 4.4 für 4,40%), 0 wenn nicht angegeben

WICHTIG: Extrahiere den FIRMENNAMEN des Lieferanten! Suche im Briefkopf, Logo, Absender, Fusszeile. Setze in "lieferant_name".
WICHTIG: Alle Zahlenwerte MÜSSEN Zahlen sein (nicht null, nicht ""). Wenn unbekannt, setze 0.
WICHTIG SELBSTKONTROLLE: Pruefe JEDEN Preis: EP x Menge sollte ungefaehr GP ergeben!
WICHTIG ZAHLENFORMAT: Beachte das DEUTSCHE Zahlenformat — Komma ist DEZIMALTRENNZEICHEN, Punkt ist Tausendertrennzeichen:
  - "2,000 Stück" = 2 Stück (Komma = Dezimaltrenner, drei Nachkommastellen)
  - "1,000 Stück" = 1 Stück
  - "600,00" EUR = 600 EUR (sechshundert)
  - "2.920,00" EUR = 2920 EUR (zweitausendneunhundertzwanzig)
  - "257,50" EUR = 257,50 EUR
  Mengen im Bauwesen sind fast immer 1-99 Stück. "2,000" ist 2 Stück, NICHT zweitausend!
  Einzelpreise (E.-Preis) stehen in der vorletzten Spalte, Gesamtbetrag (Betrag) in der letzten Spalte. Lies die E.-Preis Spalte für den EP — verwechsle NICHT E.-Preis mit Betrag!
WICHTIG: Wenn MEHRERE Artikel zur GLEICHEN LV-Position gehören (z.B. Rohr + Muffen), extrahiere JEDEN Artikel einzeln mit der gleichen lv_pos_nr.
WICHTIG: Frachtkosten/Transportkosten die als eigene Position aufgeführt sind (z.B. "Frachtkosten Großhandel 190€"): Extrahiere sie als normale Position mit dem gleichen lv_pos_nr wie die Positionen, zu denen sie gehören (Vererbungsregel gilt!). So werden sie automatisch dem richtigen Produktblock zugeordnet.
WICHTIG: Verpackungskosten die als eigene Position aufgeführt sind (z.B. "Verpackungs-Kosten lt. Werk 35€ Pauschale"): Ebenfalls als normale Position extrahieren mit vererbtem lv_pos_nr.
WICHTIG: Antworte NUR mit validem JSON, keine Kommentare, kein Markdown.

{{"positionen": [...], "nebenkosten": {{"fracht": 0, "verpackung": 0, "kran": 0, "sonstige_nk": 0, "logistik_pct": 0, "nk_hinweis": ""}}, "angebots_summe": 0, "gueltig_bis": "", "lieferzeit": "", "lieferant_name": "Firmenname aus Briefkopf/Logo/Absender"}}"""


MAX_IMAGES = 10  # Allow up to 10 pages for scanned PDFs


async def extract_offers_from_text(
    text: str,
    supplier_name: str = "Unbekannt",
    filename: str = "",
    images: Optional[list] = None,
) -> dict:
    """Use Claude to extract structured offer data from raw text/images."""
    if not config.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY not set. Add it to .env file.")

    client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

    is_scan_only = not text.strip() and images

    # For scanned PDFs with many pages, process in batches of MAX_IMAGES
    if images and len(images) > MAX_IMAGES:
        # Process in batches and merge results
        return await _extract_in_batches(client, text, supplier_name, filename, images)

    # Build message content
    content = []

    # Add images for scanned pages
    if images:
        for img in images[:MAX_IMAGES]:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img["mime"],
                    "data": img["b64"],
                }
            })

    prompt_text = EXTRACTION_PROMPT.format(
        supplier_name=supplier_name,
        filename=filename,
        text=text[:15000] if text.strip() else "(Gescanntes Dokument — bitte aus den Bildern extrahieren)",
    )
    content.append({"type": "text", "text": prompt_text})

    response = await client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=8192,
        messages=[{"role": "user", "content": content}],
    )

    # Parse response
    response_text = response.content[0].text.strip()
    print(f"[OFFER_EXTRACT] {filename} ({supplier_name}): response {len(response_text)} chars, stop_reason={response.stop_reason}")

    # If response was cut off (max_tokens hit), try to salvage partial JSON
    if response.stop_reason == "max_tokens":
        print(f"[OFFER_EXTRACT] WARNING: Response truncated for {filename}, attempting partial parse")
        response_text = response_text + "]}}"  # Close open arrays/objects

    # Clean JSON from markdown fences
    if "```" in response_text:
        parts = response_text.split("```")
        for part in parts:
            p = part.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                response_text = p
                break

    # Try parsing JSON
    data = _parse_json_response(response_text)

    # If parse failed and this was a scan, it means Claude returned an error message — retry with explicit image-only prompt
    if data.get("error") and is_scan_only:
        print(f"[OFFER_EXTRACT] Scan parse failed, retrying with image-only prompt for {filename}")
        data = await _retry_scan_extraction(client, supplier_name, filename, images)

    data["supplier"] = supplier_name
    data["filename"] = filename

    # Post-process: fix LV-POS-NR inheritance (Claude sometimes misses it)
    last_lv = ""
    for pos in data.get("positionen", []):
        lv = (pos.get("lv_pos_nr") or "").strip()
        text = pos.get("text", "")
        if lv:
            last_lv = lv
        elif last_lv and "ALTERNATIV" not in text.upper():
            pos["lv_pos_nr"] = last_lv

    # Sanitize all positions — ensure numeric fields are numbers
    for pos in data.get("positionen", []):
        pos["ep"] = _to_float(pos.get("ep"))
        pos["gp"] = _to_float(pos.get("gp"))
        pos["menge"] = _to_float(pos.get("menge"))

        # Fix German number format misreads from scans:
        # "2.000 Stück" misread as menge=2000 instead of menge=2
        # Detect: menge >= 100 AND ep < 1.0 AND gp makes sense with corrected values
        ep = pos["ep"]
        menge = pos["menge"]
        gp = pos["gp"]
        if menge >= 100 and ep < 1.0 and ep > 0:
            # Try correcting: divide menge by 1000, multiply ep by 1000
            corrected_menge = menge / 1000
            corrected_ep = ep * 1000
            # Validate: corrected menge should be reasonable (1-99)
            # and corrected_ep * corrected_menge should ≈ gp
            if 0.5 <= corrected_menge <= 99:
                expected_gp = corrected_ep * corrected_menge
                if gp > 0 and abs(expected_gp - gp) / gp < 0.05:
                    # GP matches with correction — apply fix
                    pos["ep"] = round(corrected_ep, 2)
                    pos["menge"] = round(corrected_menge, 1)
                    print(f"[OFFER_EXTRACT] Fixed scan misread: {pos.get('text', '')[:40]} — EP {ep}→{pos['ep']}, Menge {menge}→{pos['menge']}")
                elif gp == 0:
                    # No GP to validate, but pattern strongly suggests misread
                    pos["ep"] = round(corrected_ep, 2)
                    pos["menge"] = round(corrected_menge, 1)
                    print(f"[OFFER_EXTRACT] Fixed scan misread (no GP): {pos.get('text', '')[:40]} — EP {ep}→{pos['ep']}, Menge {menge}→{pos['menge']}")

    # EP/GP Cross-Validation
    for pos in data.get('positionen', []):
        _ep = pos.get('ep', 0) or 0
        _gp = pos.get('gp', 0) or 0
        _menge = pos.get('menge', 0) or 0
        _text = pos.get('text', '')[:60]
        if _ep <= 0 or _menge <= 0: continue
        if _gp > 0:
            _expected = _ep * _menge
            _ratio = _expected / _gp if _gp > 0 else 999
            if _ratio > 5 and _menge > 1:
                if abs(_ep - _gp) / max(_gp, 0.01) < 0.05:
                    _c = round(_ep / _menge, 2)
                    print(f"[EPGP] Swap: {_text} EP={_ep} ~GP, corrected={_c}")
                    pos["ep"] = _c
                    pos["gp"] = _ep
                else:
                    _c = round(_gp / _menge, 2)
                    if _c > 0:
                        print(f"[EPGP] Fix: {_text} EP*M={_expected:.0f} vs GP={_gp}, EP->{_c}")
                        pos["ep"] = _c
        else:
            _einheit = (pos.get('einheit') or '').lower().strip()
            _lim = {'m2':300,'m3':400,'m':500,'t':250,'st':5000,'kg':100}
            _eu = _einheit.replace(chr(178),'2').replace(chr(179),'3')
            _mx = _lim.get(_eu, 10000)
            if _ep > _mx and _menge > 1:
                _c = round(_ep / _menge, 2)
                if _c < _mx:
                    print(f"[EPGP] Too high: {_text} {_ep}->{_c}")
                    pos["gp"] = _ep
                    pos["ep"] = _c

    # Post-process: detect Fracht/Transport/Verpackung positions and move to nebenkosten
    FRACHT_KEYWORDS = ("fracht", "mautanteil", "transport", "lieferkosten", "anlieferung", "zustellung", "spedition")
    VERPACKUNG_KEYWORDS = ("verpackung", "verpackungs-kosten", "verpackungspauschale")
    nk = data.get("nebenkosten", {}) or {}
    if not nk:
        nk = {"fracht": 0, "verpackung": 0, "kran": 0, "sonstige_nk": 0, "logistik_pct": 0, "nk_hinweis": ""}
        data["nebenkosten"] = nk

    positions_to_keep = []
    for pos in data.get("positionen", []):
        pos_text = (pos.get("text") or "").lower()
        gp = _to_float(pos.get("gp")) or (_to_float(pos.get("ep")) * _to_float(pos.get("menge")))
        if gp > 0 and any(kw in pos_text for kw in FRACHT_KEYWORDS):
            nk["fracht"] = _to_float(nk.get("fracht")) + gp
            print(f"[OFFER_EXTRACT] Moved to Fracht-NK: '{pos.get('text', '')[:50]}' = {gp:.2f}€")
            continue
        if gp > 0 and any(kw in pos_text for kw in VERPACKUNG_KEYWORDS):
            nk["verpackung"] = _to_float(nk.get("verpackung")) + gp
            print(f"[OFFER_EXTRACT] Moved to Verpackung-NK: '{pos.get('text', '')[:50]}' = {gp:.2f}€")
            continue
        positions_to_keep.append(pos)
    data["positionen"] = positions_to_keep

    # Calculate nebenkosten percentage
    total_nk = sum(_to_float(nk.get(k)) for k in ("fracht", "verpackung", "kran", "sonstige_nk"))
    material_sum = sum(
        _to_float(p.get("gp")) or (_to_float(p.get("ep")) * _to_float(p.get("menge")))
        for p in data.get("positionen", [])
    )
    if material_sum > 0 and total_nk > 0:
        data["nk_zuschlag_pct"] = round(total_nk / material_sum * 100, 2)
    else:
        data["nk_zuschlag_pct"] = 0

    return data


async def _retry_scan_extraction(client, supplier_name: str, filename: str, images: list) -> dict:
    """Retry extraction with a simplified prompt focused purely on image reading."""
    SIMPLE_PROMPT = """Extrahiere alle Positionen aus diesem gescannten Angebot von {supplier_name}.
Gib NUR valides JSON aus:
{{"positionen": [{{"pos": "", "lv_pos_nr": "", "text": "", "menge": 0, "einheit": "", "ep": 0, "gp": 0, "rabatt": 0, "stueck_laenge": 0}}],
"nebenkosten": {{"fracht": 0, "verpackung": 0, "kran": 0, "sonstige_nk": 0, "logistik_pct": 0, "nk_hinweis": ""}},
"angebots_summe": 0, "gueltig_bis": "", "lieferzeit": "", "lieferant_name": "Firmenname aus Briefkopf/Logo/Absender"}}""".format(supplier_name=supplier_name)

    content = []
    for img in images[:MAX_IMAGES]:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": img["mime"], "data": img["b64"]}
        })
    content.append({"type": "text", "text": SIMPLE_PROMPT})

    try:
        response = await client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=8192,
            messages=[{"role": "user", "content": content}],
        )
        text = response.content[0].text.strip()
        print(f"[OFFER_EXTRACT] Retry for {filename}: {len(text)} chars")
        return _parse_json_response(text)
    except Exception as e:
        print(f"[OFFER_EXTRACT] Retry failed for {filename}: {e}")
        return {"positionen": [], "error": f"Scan extraction failed: {e}"}


async def _extract_in_batches(client, text: str, supplier_name: str, filename: str, images: list) -> dict:
    """Process a large scanned PDF in batches of MAX_IMAGES pages, merge results."""
    all_positionen = []
    merged_nk = {"fracht": 0, "verpackung": 0, "kran": 0, "sonstige_nk": 0, "logistik_pct": 0, "nk_hinweis": ""}

    for batch_start in range(0, len(images), MAX_IMAGES):
        batch = images[batch_start:batch_start + MAX_IMAGES]
        batch_num = batch_start // MAX_IMAGES + 1
        print(f"[OFFER_EXTRACT] {filename}: processing image batch {batch_num} ({len(batch)} pages)")

        content = []
        for img in batch:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": img["mime"], "data": img["b64"]}
            })
        prompt_text = EXTRACTION_PROMPT.format(
            supplier_name=supplier_name,
            filename=f"{filename} (Seiten {batch_start+1}-{batch_start+len(batch)})",
            text="(Gescanntes Dokument — bitte aus den Bildern extrahieren)",
        )
        content.append({"type": "text", "text": prompt_text})

        try:
            response = await client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=8192,
                messages=[{"role": "user", "content": content}],
            )
            batch_text = response.content[0].text.strip()
            batch_data = _parse_json_response(batch_text)
            all_positionen.extend(batch_data.get("positionen", []))
            # Merge nebenkosten from last batch that has them
            nk = batch_data.get("nebenkosten", {})
            if nk:
                for k in merged_nk:
                    if nk.get(k):
                        merged_nk[k] = nk[k]
        except Exception as e:
            print(f"[OFFER_EXTRACT] Batch {batch_num} failed for {filename}: {e}")
            continue

    # Post-process: fix LV-POS-NR inheritance gaps across batches
    # If a position has no lv_pos_nr, inherit from the previous position that has one
    last_lv = ""
    for pos in all_positionen:
        lv = (pos.get("lv_pos_nr") or "").strip()
        text = pos.get("text", "")
        if lv:
            last_lv = lv
        elif last_lv and "ALTERNATIV" not in text.upper():
            pos["lv_pos_nr"] = last_lv
            print(f"[OFFER_EXTRACT] Inherited lv_pos_nr {last_lv} → {text[:40]}")

    return {
        "positionen": all_positionen,
        "nebenkosten": merged_nk,
        "angebots_summe": 0,
        "supplier": supplier_name,
        "filename": filename,
    }


def _to_float(val) -> float:
    """Safely convert any value to float."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        # Handle German number format: "1.234,56" → 1234.56
        val = val.strip().replace(" ", "")
        if "," in val and "." in val:
            val = val.replace(".", "").replace(",", ".")
        elif "," in val:
            val = val.replace(",", ".")
        try:
            return float(val)
        except ValueError:
            return 0.0
    return 0.0


def _parse_json_response(text: str) -> dict:
    """Robustly parse JSON from Claude's response."""
    text = text.strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find the outermost JSON object
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Try fixing common JSON issues: trailing commas, single quotes
    cleaned = text
    cleaned = re.sub(r',\s*}', '}', cleaned)
    cleaned = re.sub(r',\s*]', ']', cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Last resort: try to fix with re-extraction of the JSON block
    try:
        # Find first { and last }
        start = text.index('{')
        end = text.rindex('}') + 1
        block = text[start:end]
        block = re.sub(r',\s*}', '}', block)
        block = re.sub(r',\s*]', ']', block)
        return json.loads(block)
    except (ValueError, json.JSONDecodeError):
        pass

    print(f"[OFFER_EXTRACT] Could not parse JSON. Response (first 500 chars): {text[:500]}")
    return {"positionen": [], "error": "Could not parse Claude response"}


def extract_offers_from_text_sync(text: str, supplier_name: str = "Unbekannt", filename: str = "", images=None) -> dict:
    """Synchronous wrapper for extract_offers_from_text."""
    import asyncio
    return asyncio.run(extract_offers_from_text(text, supplier_name, filename, images))
