"""KALKU Preiseintragung — FastAPI Web Application."""
import os
import re
import json
import shutil
import asyncio
import uuid
from pathlib import Path
from typing import Optional
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

import config
from parsers.pdf_parser import parse_pdf, get_full_text, get_scan_images
from parsers.excel_parser import parse_lv_excel, parse_offer_excel
from parsers.gaeb_parser import parse_gaeb
from parsers.offer_extractor import extract_offers_from_text
from matching.fuzzy_matcher import fuzzy_match_positions
from matching.claude_matcher import claude_match_all, determine_column
from matching.unit_converter import convert_unit_price, apply_nk_zuschlag
from matching.price_estimator import estimate_missing_prices, save_learned_price
from matching.price_database import add_prices_from_offer, add_price
from matching.rules_engine import load_rules, save_rule, delete_rule, apply_rules
from matching.price_validator import validate_match, validate_component_addition
from writer.excel_writer import write_prices_to_lv

app = FastAPI(title="KALKU Preiseintragung", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# In-memory project store
projects = {}


# ─── Pages ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse("static/index.html")


# ─── Project Management ────────────────────────────────────────────────────

@app.post("/api/project/create")
async def create_project(name: str = Form(...)):
    project_id = str(uuid.uuid4())[:8]
    project_dir = config.UPLOAD_DIR / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "angebote").mkdir(exist_ok=True)
    
    projects[project_id] = {
        "id": project_id,
        "name": name,
        "created": datetime.now().isoformat(),
        "lv_file": None,
        "lv_data": None,
        "gaeb_file": None,
        "offers": [],
        "matches": [],
        "report": None,
        "status": "created",
        "progress": {"pct": 0, "step": ""},
    }
    
    return {"project_id": project_id, "name": name}


@app.get("/api/project/{project_id}")
async def get_project(project_id: str):
    if project_id not in projects:
        raise HTTPException(404, "Project not found")
    proj = projects[project_id]
    return {
        "id": proj["id"],
        "name": proj["name"],
        "status": proj["status"],
        "lv_file": proj["lv_file"],
        "offers": [{"filename": o["filename"], "supplier": o.get("supplier", ""), "items": len(o.get("positionen", []))} for o in proj["offers"]],
        "lv_stats": proj["lv_data"]["stats"] if proj.get("lv_data") else None,
        "match_summary": _get_match_summary(proj) if proj.get("matches") else None,
        "report": proj.get("report"),
    }


# ─── File Upload (save only, no parsing) ──────────────────────────────────

@app.post("/api/project/{project_id}/upload-files")
async def upload_files(
    project_id: str,
    lv_file: Optional[UploadFile] = File(None),
    gaeb_file: Optional[UploadFile] = File(None),
    offer_files: list[UploadFile] = File([]),
    sheet_name: str = Form("Kalkulation"),
    supplier_names: str = Form(""),
):
    """Upload all files at once (just saves, no parsing)."""
    if project_id not in projects:
        raise HTTPException(404, "Project not found")

    proj = projects[project_id]
    project_dir = config.UPLOAD_DIR / project_id
    saved = []

    # Save LV
    if lv_file and lv_file.filename:
        filepath = project_dir / lv_file.filename
        with open(filepath, "wb") as f:
            f.write(await lv_file.read())
        proj["lv_file"] = str(filepath)
        proj["sheet_name"] = sheet_name
        saved.append({"filename": lv_file.filename, "type": "lv"})

    # Save GAEB
    if gaeb_file and gaeb_file.filename:
        filepath = project_dir / gaeb_file.filename
        with open(filepath, "wb") as f:
            f.write(await gaeb_file.read())
        proj["gaeb_file"] = str(filepath)
        saved.append({"filename": gaeb_file.filename, "type": "gaeb"})

    # Save offers
    offer_dir = project_dir / "angebote"
    suppliers = [s.strip() for s in supplier_names.split(",")] if supplier_names else []
    offer_metas = []
    # Patterns for non-offer files to skip (only mail-related)
    _SKIP_PATTERNS = [
        re.compile(r'mail', re.IGNORECASE),  # mailempfang, _Mail.pdf, etc.
    ]
    for i, file in enumerate(offer_files):
        if not file.filename:
            continue
        # Flatten path — browser may send "04_Angebote/supplier/file.pdf"
        safe_name = Path(file.filename).name
        # Skip non-offer files
        if any(p.search(safe_name) for p in _SKIP_PATTERNS):
            print(f"[UPLOAD] Skipping non-offer file: {safe_name}")
            continue
        supplier = suppliers[i] if i < len(suppliers) else _guess_supplier(safe_name)
        # Save in supplier subfolder to avoid name collisions
        supplier_dir = offer_dir / supplier.replace(" ", "_")
        supplier_dir.mkdir(parents=True, exist_ok=True)
        filepath = supplier_dir / safe_name
        with open(filepath, "wb") as f:
            f.write(await file.read())
        offer_metas.append({"filename": safe_name, "filepath": str(filepath), "supplier": supplier})
        saved.append({"filename": safe_name, "type": "offer", "supplier": supplier})

    proj["offer_metas"] = proj.get("offer_metas", []) + offer_metas
    proj["status"] = "uploaded"

    return {"saved": saved}


@app.post("/api/project/{project_id}/process")
async def process_files(project_id: str):
    """Parse and extract all uploaded files."""
    if project_id not in projects:
        raise HTTPException(404, "Project not found")

    proj = projects[project_id]
    proj["status"] = "processing"
    proj["progress"] = {"pct": 0, "step": "Starte Verarbeitung…"}
    results = {"lv": None, "gaeb": None, "offers": []}

    # Parse LV (run in thread to avoid blocking event loop)
    if proj.get("lv_file"):
        proj["progress"] = {"pct": 5, "step": "LV-Excel wird gelesen…"}
        await asyncio.sleep(0)  # yield to event loop so progress poll can respond
        try:
            sheet_name = proj.get("sheet_name", "Kalkulation")
            lv_data = await asyncio.to_thread(parse_lv_excel, proj["lv_file"], sheet_name)
            proj["lv_data"] = lv_data
            results["lv"] = {
                "filename": Path(proj["lv_file"]).name,
                "stats": lv_data["stats"],
                "sheet": lv_data["sheet"],
            }
        except Exception as e:
            results["lv"] = {"error": str(e)}

    # Parse GAEB (run in thread to avoid blocking event loop)
    if proj.get("gaeb_file"):
        proj["progress"] = {"pct": 10, "step": "GAEB-Datei wird gelesen…"}
        await asyncio.sleep(0)
        try:
            gaeb_data = await asyncio.to_thread(parse_gaeb, proj["gaeb_file"])
            proj["gaeb_data"] = gaeb_data
            results["gaeb"] = {
                "filename": Path(proj["gaeb_file"]).name,
                "format": gaeb_data["format"],
                "total_positions": gaeb_data["total"],
            }
        except Exception as e:
            results["gaeb"] = {"error": str(e)}

    # Parse offers in parallel
    offer_metas = proj.get("offer_metas", [])
    total_offers = len(offer_metas)
    proj["progress"] = {"pct": 15, "step": f"Extrahiere {total_offers} Angebote parallel\u2026"}
    await asyncio.sleep(0)

    async def _extract_one(meta):
        filepath = meta["filepath"]
        supplier = meta["supplier"]
        suffix = Path(filepath).suffix.lower()
        try:
            if suffix in (".xlsx", ".xls"):
                offer_data = await asyncio.to_thread(parse_offer_excel, filepath)
                offer_data["supplier"] = supplier
                offer_data["source_type"] = "excel"
            elif suffix == ".pdf":
                pdf_data = await asyncio.to_thread(parse_pdf, filepath)
                full_text = get_full_text(pdf_data)
                scan_images = get_scan_images(pdf_data)
                if full_text.strip() or scan_images:
                    offer_data = await extract_offers_from_text(
                        full_text, supplier, meta["filename"], scan_images or None
                    )
                    offer_data["source_type"] = "pdf"
                pdf_name = offer_data.get("lieferant_name", "").strip()
                if pdf_name and len(pdf_name) > 2:
                    offer_data["supplier"] = pdf_name
                    print(f"[EXTRACT] Supplier from PDF: {pdf_name}")
                else:
                    offer_data = {"positionen": [], "error": "Empty PDF", "supplier": supplier}
            else:
                offer_data = {"positionen": [], "error": f"Unsupported format: {suffix}", "supplier": supplier}
        except Exception as e:
            offer_data = {"positionen": [], "error": str(e), "supplier": supplier}
        offer_data["filename"] = meta["filename"]
        offer_data["filepath"] = filepath
        return offer_data

    offer_results = await asyncio.gather(*[_extract_one(m) for m in offer_metas])

    for offer_data in offer_results:
        proj["offers"].append(offer_data)
        items = offer_data.get("positionen", offer_data.get("items", []))
        # Save offer prices to database for future reuse
        supplier = offer_data.get("supplier", "Unbekannt")
        if items:
            add_prices_from_offer(items, supplier)
        results["offers"].append({
            "filename": offer_data["filename"],
            "supplier": supplier,
            "items_found": len(items),
            "nk_zuschlag": offer_data.get("nk_zuschlag_pct", 0),
            "error": offer_data.get("error"),
        })

    proj["status"] = "processed"
    proj["progress"] = {"pct": 100, "step": "Fertig!"}
    return results


# ─── Matching ──────────────────────────────────────────────────────────────

@app.post("/api/project/{project_id}/match")
async def run_matching(project_id: str):
    """Run matching pipeline: Claude as primary matcher."""
    if project_id not in projects:
        raise HTTPException(404, "Project not found")

    proj = projects[project_id]
    if not proj.get("lv_data"):
        raise HTTPException(400, "No LV loaded")
    if not proj.get("offers"):
        raise HTTPException(400, "No offers loaded")

    proj["status"] = "matching"
    proj["progress"] = {"pct": 5, "step": "Matching vorbereiten…"}
    lv_positions = proj["lv_data"]["positions"]
    all_warnings = []

    # Collect ALL offer items with supplier + Nebenkosten info
    all_offer_items = []
    offer_nk = {}  # offer_filename → nk_pct (per-offer, NOT per-supplier)
    items_without_lvpos = []  # items that need Claude matching

    for offer in proj["offers"]:
        supplier = offer.get("supplier", "Unbekannt")
        offer_filename = offer.get("filename", "")
        items = offer.get("positionen", offer.get("items", []))
        nk_pct = offer.get("nk_zuschlag_pct", 0)
        nk = offer.get("nebenkosten", {}) or {}

        if not items:
            all_warnings.append(f"Angebot {offer_filename}: Keine Positionen gefunden")
            continue

        # Calculate total NK percentage (Fracht + Logistik) per offer
        total_nk = sum(
            float(nk.get(k, 0) or 0)
            for k in ("fracht", "verpackung", "kran", "sonstige_nk")
        )
        logistik_pct = float(nk.get("logistik_pct", 0) or 0)
        material_sum = sum(
            float(p.get("gp", 0) or 0) or (float(p.get("ep", 0) or 0) * float(p.get("menge", 0) or 0))
            for p in items
        )
        # Combine: Fracht as % of material + explicit Logistikzuschlag %
        fracht_pct = round(total_nk / material_sum * 100, 2) if material_sum > 0 and total_nk > 0 else 0
        combined_nk_pct = fracht_pct + logistik_pct if logistik_pct > 0 else (fracht_pct or nk_pct)
        offer_nk[offer_filename] = combined_nk_pct

        for item in items:
            item["supplier"] = supplier
            item["_offer_filename"] = offer_filename
            # Apply Rabatt directly to EP if present (abs for negative notation like -85%)
            rabatt = abs(float(item.get("rabatt", 0) or 0))
            ep = float(item.get("ep", 0) or 0)
            if rabatt > 0 and ep > 0:
                item["ep_original"] = ep
                item["ep"] = round(ep * (1 - rabatt / 100), 2)
                item["rabatt_applied"] = rabatt
            all_offer_items.append(item)

    if not all_offer_items:
        raise HTTPException(400, f"No offer items extracted from {len(proj['offers'])} offers")

    # ── Phase 1: Direct LV-POS-NR matching ──
    proj["progress"] = {"pct": 20, "step": "Direkte Positions-Zuordnung (LV-POS-NR)\u2026"}
    await asyncio.sleep(0)
    # Items with explicit lv_pos_nr → group by (supplier, lv_pos_nr), convert units, sum EPs
    oz_lookup = _build_oz_lookup(lv_positions)
    # Also keep simple dicts for Claude result matching later
    lv_by_oz = {}
    lv_by_oz_norm = {}
    for pos in lv_positions:
        lv_by_oz[pos["oz"]] = pos
        lv_by_oz_norm[_normalize_oz(pos["oz"])] = pos

    direct_groups = {}  # (supplier, lv_oz_norm) → [items]
    items_for_claude = []  # items without lv_pos_nr

    for i, item in enumerate(all_offer_items):
        lv_pos_nr = (item.get("lv_pos_nr") or "").strip()
        if lv_pos_nr:
            lv_pos = _find_lv_position(lv_pos_nr, oz_lookup)
            if lv_pos:
                key = (item["supplier"], lv_pos["oz"])
                if key not in direct_groups:
                    direct_groups[key] = {"lv_pos": lv_pos, "items": [], "supplier": item["supplier"]}
                direct_groups[key]["items"].append(item)
                continue
            else:
                all_warnings.append(
                    f"LV-POS-NR '{lv_pos_nr}' aus Angebot ({item['supplier']}) nicht in LV gefunden"
                )
        items_for_claude.append(item)

    # Process direct groups: convert units + sum EPs per LV position per supplier
    direct_matches = []
    for (supplier, lv_oz), group in direct_groups.items():
        lv_pos = group["lv_pos"]
        lv_unit = lv_pos.get("einheit", "")
        lv_menge = lv_pos.get("menge", 0)
        # NK is per-offer, get from first item's filename
        _offer_fn = group["items"][0].get("_offer_filename", "") if group["items"] else ""
        nk_pct = offer_nk.get(_offer_fn, 0)

        # Deduplicate items: same EP + same unit + similar text = duplicate from repeated PDF pages
        seen = set()
        deduped_items = []
        for item in group["items"]:
            ep = float(item.get("ep", 0) or 0)
            unit = (item.get("einheit") or "").lower().strip()
            text_key = (item.get("text") or "")[:50].strip().lower()
            dedup_key = (round(ep, 2), unit, text_key)
            if dedup_key in seen:
                print(f"[MATCH] Dedup: skipping duplicate item for {lv_oz} — {text_key[:40]} EP={ep}")
                continue
            seen.add(dedup_key)
            deduped_items.append(item)
        group["items"] = deduped_items

        total_ep_per_lv_unit = 0
        explanation_parts = []

        for item in group["items"]:
            ep = float(item.get("ep", 0) or 0)
            if ep <= 0:
                continue

            offer_unit = item.get("einheit", "")
            offer_menge = float(item.get("menge", 0) or 0)
            stueck_laenge = float(item.get("stueck_laenge", 0) or 0)
            item_text = item.get("text", "")[:60]

            # Convert to LV unit
            if offer_unit and lv_unit and offer_unit.lower() != lv_unit.lower():
                lv_u = offer_unit.lower().strip()
                # St → m conversion using Stücklänge from description
                if lv_u in ("st", "stk", "stück") and lv_unit.lower() in ("m", "lfm") and stueck_laenge > 0:
                    # EP per St ÷ Stücklänge = EP per m
                    ep_per_lv_unit = round(ep / stueck_laenge, 2)
                    explanation_parts.append(f"{item_text}: {ep:.2f}€/St ÷ {stueck_laenge}m/St = {ep_per_lv_unit:.2f}€/{lv_unit}")
                    total_ep_per_lv_unit += ep_per_lv_unit
                else:
                    # Try unit converter
                    converted, conv_expl = convert_unit_price(ep, offer_unit, lv_unit, lv_pos["bezeichnung"])
                    if converted is not None:
                        explanation_parts.append(f"{item_text}: {conv_expl}")
                        total_ep_per_lv_unit += converted
                    else:
                        # Last resort: warn about unit mismatch
                        explanation_parts.append(f"{item_text}: {ep:.2f}€/{offer_unit} (Einheit ≠ {lv_unit})")
                        all_warnings.append(f"Einheit-Abweichung: {lv_pos['oz']} — {item_text}: {offer_unit} ≠ {lv_unit}")
            else:
                explanation_parts.append(f"{item_text}: {ep:.2f}€/{lv_unit}")
                total_ep_per_lv_unit += ep

        if total_ep_per_lv_unit <= 0:
            # Still create match with ep=0 so user can fill in price manually
            explanation_parts.append("⚠ Kein Preis im Angebot gefunden — bitte manuell eintragen")
            all_warnings.append(f"{lv_pos['oz']}: Kein EP im Angebot von {supplier} — manuell prüfen")

        # Calculate components with Rabatt and Fracht breakdown
        components = []
        total_material_ep = 0  # Sum of material EPs (for fracht proration)
        
        # First pass: calculate material totals (EP already has rabatt applied from line 313)
        for item in group["items"]:
            ep = float(item.get("ep", 0) or 0)
            if ep <= 0:
                continue
            total_material_ep += ep
        
        # Calculate Fracht per item (prorated by material value)
        fracht_per_item = {}
        if nk_pct > 0 and total_material_ep > 0:
            total_fracht = total_material_ep * (nk_pct / 100)
            for item in group["items"]:
                ep = float(item.get("ep", 0) or 0)
                if ep <= 0:
                    continue
                # Prorate fracht by material share (EP already rabatted)
                item_fracht = round(total_fracht * (ep / total_material_ep), 2)
                fracht_per_item[id(item)] = item_fracht
        
        # Second pass: build components
        for item in group["items"]:
            ep = float(item.get("ep", 0) or 0)
            if ep <= 0:
                continue
            
            item_text = item.get("text", "Material")[:30]

            # Add main component (EP already has rabatt applied)
            components.append({
                "name": item_text,
                "ep": ep,
                "einheit": item.get("einheit", lv_unit)
            })
            
            # Add fracht component if exists
            item_fracht = fracht_per_item.get(id(item), 0)
            if item_fracht > 0:
                components.append({
                    "name": f"Fracht ({item_text[:20]}...)",
                    "ep": item_fracht,
                    "einheit": lv_unit
                })
        
        # Calculate total EP for comparison (material + fracht)
        total_ep_with_fracht = sum(c["ep"] for c in components)
        
        # Build explanation
        if nk_pct > 0:
            explanation_parts.append(f"+{nk_pct:.1f}% Fracht/Logistik auf Material")
        
        column = determine_column(lv_pos["bezeichnung"])

        # Check if any item had handwritten prices
        has_handwritten = any(item.get("handschriftlich", False) for item in group["items"] if float(item.get("ep", 0) or 0) > 0)
        warning = "⚠ Preis handschriftlich — bitte prüfen!" if has_handwritten else ""

        direct_matches.append({
            "row": lv_pos["row"],
            "oz": lv_pos["oz"],
            "bezeichnung": lv_pos["bezeichnung"],
            "lv_einheit": lv_unit,
            "lv_menge": lv_menge,
            "column": column,
            "ep": round(total_ep_with_fracht, 2),
            "supplier": supplier,
            "offer_text": " + ".join(item.get("text", "")[:40] for item in group["items"] if item.get("ep", 0)),
            "offer_ep_original": total_ep_per_lv_unit,
            "offer_einheit": lv_unit,
            "explanation": " | ".join(explanation_parts),
            "confidence": 100,
            "warning": warning,
            "material_type": "Hauptmaterial",
            "match_source": "LV-POS-NR",
            "components": components,
        })

    print(f"[MATCH] Direct LV-POS-NR matches: {len(direct_matches)} from {len(direct_groups)} groups")

    # ── Phase 2: Claude matching for remaining items ──
    gaeb_data = proj.get("gaeb_data")

    # Build GAEB Langtext lookup
    gaeb_lookup = {}
    if gaeb_data and gaeb_data.get('positions'):
        import re as _re
        for gp in gaeb_data['positions']:
            _oz = gp.get('oz', '').strip()
            if _oz:
                _oz_n = _re.sub(r'\s+', '', _oz.rstrip('.'))
                _parts = _oz_n.split('.')
                _oz_n = '.'.join(pp.lstrip('0') or '0' for pp in _parts)
                gaeb_lookup[_oz_n] = gp.get('langtext', '')

    gaeb_count = len(gaeb_data.get("positions", [])) if gaeb_data else 0
    print(f"[MATCH] {len(lv_positions)} LV positions, {len(items_for_claude)} items for Claude, GAEB: {gaeb_count} positions")

    claude_results = []
    if items_for_claude:
        proj["progress"] = {"pct": 40, "step": f"Claude AI Matching ({len(items_for_claude)} Positionen)\u2026"}
        await asyncio.sleep(0)
        try:
            claude_results = await claude_match_all(lv_positions, items_for_claude, gaeb_data)
            print(f"[MATCH] Claude returned {len(claude_results)} results")
        except Exception as e:
            print(f"[MATCH] Claude error: {e}")
            all_warnings.append(f"Claude matching error: {str(e)}")
            claude_results = []
    else:
        print("[MATCH] All items matched via LV-POS-NR, skipping Claude")

    # Process Claude results into matches
    all_matches = list(direct_matches)  # Start with direct matches
    skipped_reasons = {"no_match": 0, "oz_miss": 0, "idx_miss": 0, "no_ep": 0}
    for cr in claude_results:
        if not cr.get("match", False):
            skipped_reasons["no_match"] += 1
            continue

        lv_oz = str(cr.get("lv_oz", ""))
        lv_oz_norm = _normalize_oz(lv_oz)
        offer_idx = cr.get("offer_idx", -1)

        lv_pos = _find_lv_position(lv_oz, oz_lookup)
        if not lv_pos:
            skipped_reasons["oz_miss"] += 1
            print(f"[MATCH] OZ miss: '{lv_oz}' / '{lv_oz_norm}'")
            continue
        if offer_idx < 0 or offer_idx >= len(items_for_claude):
            skipped_reasons["idx_miss"] += 1
            continue
        offer_item = items_for_claude[offer_idx]
        supplier = offer_item.get("supplier", "")
        column = cr.get("column", determine_column(lv_pos["bezeichnung"]))
        material_type = cr.get("material_type", "Hauptmaterial")

        ep = float(offer_item.get("ep", 0) or 0)
        offer_unit = offer_item.get("einheit", "")
        lv_unit = lv_pos.get("einheit", "")
        explanation_parts = []
        warning = cr.get("warning", "")

        if ep <= 0:
            warning = "Kein Preis im Angebot — bitte manuell eintragen"
        elif offer_item.get("handschriftlich", False):
            warning = (warning + " | " if warning else "") + "⚠ Preis handschriftlich — bitte prüfen!"

        # Unit conversion if needed
        if offer_unit and lv_unit and offer_unit.lower() != lv_unit.lower():
            converted, conv_expl = convert_unit_price(
                ep, offer_unit, lv_unit, lv_pos["bezeichnung"]
            )
            if converted is not None:
                ep = converted
                explanation_parts.append(conv_expl)
            else:
                warning = f"Einheit {offer_unit} → {lv_unit}: Umrechnung nicht möglich"

        # Calculate Rabatt and Fracht components (NK is per-offer, not per-supplier)
        nk_pct = offer_nk.get(offer_item.get("_offer_filename", ""), 0)
        rabatt = float(offer_item.get("rabatt_applied", 0) or 0)
        
        # Get rabatted EP (already applied in extraction, but get original for display)
        ep_rabatted = ep
        if offer_item.get("ep_original"):
            ep_original = float(offer_item.get("ep_original"))
            ep_rabatted = round(ep_original * (1 - rabatt / 100), 2)
        
        # Calculate Fracht
        fracht_amount = 0
        if nk_pct > 0:
            fracht_amount = round(ep_rabatted * (nk_pct / 100), 2)
        
        # Total EP for comparison
        ep_total = ep_rabatted + fracht_amount
        
        # Build explanation
        if rabatt > 0:
            explanation_parts.append(f"-{rabatt:.1f}% Rabatt: {ep_original:.2f} → {ep_rabatted:.2f}")
        if nk_pct > 0:
            explanation_parts.append(f"+{nk_pct:.1f}% Fracht: {ep_rabatted:.2f} + {fracht_amount:.2f} = {ep_total:.2f}")
        
        # Build components array
        components = [{
            "name": offer_item.get("text", "Material")[:30],
            "ep": ep_rabatted,
            "einheit": lv_unit
        }]
        if fracht_amount > 0:
            components.append({
                "name": "Fracht/Logistik",
                "ep": fracht_amount,
                "einheit": lv_unit
            })

        all_matches.append({
            "row": lv_pos["row"],
            "oz": lv_pos["oz"],
            "bezeichnung": lv_pos["bezeichnung"],
            "lv_einheit": lv_unit,
            "lv_menge": lv_pos["menge"],
            "column": column,
            "ep": round(ep_total, 2),
            "supplier": supplier,
            "offer_text": offer_item.get("text", "")[:100],
            "offer_ep_original": offer_item.get("ep_original", offer_item.get("ep", 0)),
            "offer_einheit": offer_unit,
            "explanation": " | ".join(explanation_parts) if explanation_parts else f"{ep_total:.2f} €/{lv_unit}",
            "confidence": cr.get("confidence", 0),
            "warning": warning,
            "material_type": material_type,
            "components": components,
        })

    print(f"[MATCH] After processing: {len(all_matches)} valid matches, skipped: {skipped_reasons}")

    # ── Smart selection: exact spec match + supplier consolidation ──
    # Separate Hauptmaterial (competing offers) from Nebenmaterial (additive)

    # Group by (row, column, material_type)
    haupt_candidates = {}  # (row, col) → [matches] — competing, pick best
    neben_matches = []      # additive, pick cheapest per material then add to EP

    for m in all_matches:
        key = (m["row"], m["column"])
        if m.get("material_type") == "Nebenmaterial":
            neben_matches.append(m)
        else:
            if key not in haupt_candidates:
                haupt_candidates[key] = []
            haupt_candidates[key].append(m)

    # Count how many positions each supplier covers (for consolidation)
    supplier_coverage = {}
    for key, cands in haupt_candidates.items():
        for c in cands:
            s = c["supplier"]
            supplier_coverage[s] = supplier_coverage.get(s, 0) + 1

    # For each position, pick the best Hauptmaterial candidate
    best_matches = {}
    SUPPLIER_PREF_THRESHOLD = 0.05

    for key, cands in haupt_candidates.items():
        exact = [c for c in cands if not c.get("warning")]
        with_warning = [c for c in cands if c.get("warning")]

        pool = exact if exact else with_warning
        if not pool:
            continue

        # Direct LV-POS-NR matches take priority over Claude-matched alternatives
        direct = [c for c in pool if c.get("match_source") == "LV-POS-NR"]
        if direct:
            direct.sort(key=lambda c: c["ep"])
            cheapest = direct[0]
            # Warn if Claude found cheaper alternatives
            claude_cheaper = [c for c in pool if c.get("match_source") != "LV-POS-NR" and c["ep"] < cheapest["ep"]]
            if claude_cheaper:
                best_alt = min(claude_cheaper, key=lambda c: c["ep"])
                all_warnings.append(
                    f"Direkt-Match bevorzugt: {cheapest['oz']} {cheapest['supplier']} {cheapest['ep']:.2f}€ "
                    f"(günstigere Alternative: {best_alt['supplier']} {best_alt['ep']:.2f}€)"
                )
        else:
            pool.sort(key=lambda c: c["ep"])
            cheapest = pool[0]

        # Supplier consolidation
        chosen = cheapest
        if len(pool) > 1 and supplier_coverage:
            top_supplier = max(supplier_coverage, key=supplier_coverage.get)
            for c in pool:
                if c["supplier"] == top_supplier and c["supplier"] != cheapest["supplier"]:
                    price_diff = (c["ep"] - cheapest["ep"]) / cheapest["ep"] if cheapest["ep"] > 0 else 1
                    if price_diff <= SUPPLIER_PREF_THRESHOLD:
                        chosen = c
                        all_warnings.append(
                            f"Lieferantenbündelung: {chosen['oz']} {chosen['supplier']} {chosen['ep']:.2f}€ "
                            f"(+{price_diff*100:.1f}%) statt {cheapest['supplier']} {cheapest['ep']:.2f}€"
                        )
                        break

        if exact and with_warning:
            cheapest_any = min(cands, key=lambda c: c["ep"])
            if cheapest_any["ep"] < chosen["ep"]:
                all_warnings.append(
                    f"Spec-Abweichung ignoriert: {chosen['oz']} — {cheapest_any['supplier']} wäre {cheapest_any['ep']:.2f}€ "
                    f"aber: {cheapest_any.get('warning', '')}"
                )

        best_matches[key] = chosen

    # Add Nebenmaterial: create components array for multi-material positions
    neben_by_key = {}
    for m in neben_matches:
        key = (m["row"], m["column"])
        if key not in neben_by_key or m["ep"] < neben_by_key[key]["ep"]:
            neben_by_key[key] = m

    for key, neben in neben_by_key.items():
        if key in best_matches:
            haupt = best_matches[key]
            # Create components array for multi-material position
            components = [
                {
                    "name": haupt.get("offer_text", "Hauptmaterial")[:30],
                    "ep": haupt["ep"],
                    "einheit": haupt.get("lv_einheit", "")
                },
                {
                    "name": neben.get("offer_text", "Nebenmaterial")[:30],
                    "ep": neben["ep"],
                    "einheit": neben.get("lv_einheit", "")
                }
            ]
            haupt["components"] = components
            haupt["ep"] = round(haupt["ep"] + neben["ep"], 2)  # Keep total for backward compat
            haupt["explanation"] += f" + Nebenmaterial ({neben['offer_text'][:40]}): {neben['ep']:.2f}€"
            all_warnings.append(
                f"Nebenmaterial addiert: {haupt['oz']} — {neben['offer_text'][:50]} ({neben['supplier']}) "
                f"{neben['ep']:.2f}€ → Gesamt-EP: {haupt['ep']:.2f}€"
            )
        else:
            # No Hauptmaterial yet, use Nebenmaterial as-is (single component)
            neben["components"] = [{
                "name": neben.get("offer_text", "Material")[:30],
                "ep": neben["ep"],
                "einheit": neben.get("lv_einheit", "")
            }]
            best_matches[key] = neben

    # Add components array for single-material positions too
    for key, match in best_matches.items():
        if "components" not in match:
            match["components"] = [{
                "name": match.get("offer_text", "Material")[:30],
                "ep": match["ep"],
                "einheit": match.get("lv_einheit", "")
            }]

    final_matches = list(best_matches.values())
    matched_rows = {m["row"] for m in final_matches}

    # ── Phase 3: AI Price Estimation for unmatched material positions ──
    # Identify material positions with no match
    WORK_KEYWORDS = (
        "erstellen", "verdichten", "herstellen", "einbauen und verdichten",
        "nassschneiden", "schneiden", "planum", "abbruch", "rückbau",
        "abfuhr", "entsorg", "rodung", "fällung",
        "baustelleneinrichtung", "vorhalten", "bereithalten",
        "baustelle einrichten", "baustelle räumen",
        "verkehrssicherung", "absperrung", "beschilderung",
        "abbrechen", "aufnehmen", "aufbrechen", "ausbauen", "abtragen",
        "lösen", "laden", "fördern", "lagern",
        "planieren", "profilieren", "begradigen",
        "verdichtungsprüfung", "kontrollprüfung",
    )
    unmatched_material = []
    for pos in lv_positions:
        if pos["row"] in matched_rows:
            continue
        bez_lower = pos["bezeichnung"].lower()
        # Skip pure work/labor positions
        is_work = any(kw in bez_lower for kw in WORK_KEYWORDS)
        # But keep "liefern und einbauen" (has material component)
        _MAT_NAMES = ("sand", "kies", "schotter", "splitt", "boden",
                      "beton", "verfüllung", "bettung", "substrat",
                      "frostschutz", "oberboden", "mutterboden",
                      "tragschicht", "schicht", "rohr", "folie",
                      "pflaster", "bordstein", "rinne", "liefer",
                      "material", "mulch", "hackschnitzel")
        if any(mk in bez_lower for mk in _MAT_NAMES):
            is_work = False
        if is_work:
            continue
        # Skip group headers (no unit/menge)
        if not pos.get("einheit") or not pos.get("menge"):
            continue
        unmatched_material.append(pos)

    if unmatched_material:
        proj["progress"] = {"pct": 80, "step": f"AI Preisschätzung für {len(unmatched_material)} fehlende Positionen…"}
        await asyncio.sleep(0)
        try:
            estimates = await estimate_missing_prices(unmatched_material, gaeb_data)
            for est in estimates:
                if est.get("skip", False):
                    continue
                est_ep = float(est.get("ep", 0) or 0)
                # Find matching LV position
                target_pos = None
                for pos in unmatched_material:
                    if pos["oz"] == est.get("oz"):
                        target_pos = pos
                        break
                if not target_pos:
                    continue

                column = determine_column(target_pos["bezeichnung"])
                final_matches.append({
                    "row": target_pos["row"],
                    "oz": target_pos["oz"],
                    "bezeichnung": target_pos["bezeichnung"],
                    "lv_einheit": target_pos.get("einheit", ""),
                    "lv_menge": target_pos.get("menge", 0),
                    "column": column,
                    "ep": round(est_ep, 2),
                    "supplier": "AI Schätzung",
                    "offer_text": est.get("reasoning", "Geschätzter Marktpreis"),
                    "offer_ep_original": est_ep,
                    "offer_einheit": target_pos.get("einheit", ""),
                    "explanation": est.get("reasoning", ""),
                    "confidence": 50,
                    "warning": "⚠ Preis geschätzt — bitte prüfen!",
                    "material_type": "Hauptmaterial",
                    "match_source": "AI-Schätzung",
                    "components": [{"name": "Geschätzter Marktpreis", "ep": round(est_ep, 2), "einheit": target_pos.get("einheit", "")}],
                })
                matched_rows.add(target_pos["row"])
            print(f"[MATCH] AI estimated {len([e for e in estimates if not e.get('skip')])} prices")
        except Exception as e:
            print(f"[MATCH] AI estimation error: {e}")
            all_warnings.append(f"AI Preisschätzung Fehler: {str(e)}")

    # ── Phase 4: Apply saved rules ──
    final_matches = apply_rules(final_matches, lv_positions)

    # ── Sort by row (LV order) ──
    # Plausibility validation
    validated = []
    for m in final_matches:
        ok, reason = validate_match(m)
        if ok:
            validated.append(m)
        else:
            src = m.get("match_source", "")
            if "Sch" in src and "tzung" in src:
                all_warnings.append(f"AI verworfen: {m['oz']} {m['ep']:.2f} - {reason}")
                continue
            w = m.get("warning", "")
            m["warning"] = (w + " | " if w else "") + f"PREIS PRUEFEN: {reason}"
            m["confidence"] = min(m.get("confidence", 100), 25)
            validated.append(m)
            all_warnings.append(f"Preis check: {m['oz']} {m['ep']:.2f}/{m.get('lv_einheit','')} - {reason}")
    final_matches = validated

    final_matches.sort(key=lambda m: m.get("row", 0))

    proj["matches"] = final_matches
    proj["match_warnings"] = all_warnings
    proj["status"] = "matched"
    proj["progress"] = {"pct": 100, "step": f"Fertig! {len(final_matches)} Zuordnungen"}

    return {
        "total_matches": len(final_matches),
        "by_column": {
            "stoffe_X": sum(1 for m in final_matches if m["column"] == "X"),
            "nu_M": sum(1 for m in final_matches if m["column"] == "M"),
        },
        "warnings": all_warnings,
        "matches": final_matches,
    }


# ─── Progress ─────────────────────────────────────────────────────────────

@app.get("/api/project/{project_id}/progress")
async def get_progress(project_id: str):
    if project_id not in projects:
        return {"pct": 0, "step": ""}
    prog = projects[project_id].get("progress", {"pct": 0, "step": ""})
    return prog


# ─── Write & Export ────────────────────────────────────────────────────────

@app.post("/api/project/{project_id}/write")
async def write_to_excel(
    project_id: str,
    match_ids: Optional[str] = None,  # Comma-separated indices as query param
):
    """Write matched prices into the LV Excel and return the file."""
    if project_id not in projects:
        raise HTTPException(404, "Project not found")
    
    proj = projects[project_id]
    if not proj.get("matches"):
        raise HTTPException(400, "No matches to write")
    if not proj.get("lv_file"):
        raise HTTPException(400, "No LV file")
    
    # Filter matches if specific IDs given
    matches = proj["matches"]
    if match_ids:
        ids = [int(x) for x in match_ids.split(",")]
        matches = [matches[i] for i in ids if i < len(matches)]
    
    output_path = config.OUTPUT_DIR / f"{proj['name']}_{project_id}_filled.xlsx"
    
    report = write_prices_to_lv(
        proj["lv_file"],
        matches,
        str(output_path),
        source_type="angebot",
    )
    
    proj["report"] = report
    proj["output_file"] = str(output_path)
    proj["status"] = "completed"
    
    return {
        "report": report,
        "download_url": f"/api/project/{project_id}/download",
    }


@app.get("/api/project/{project_id}/download")
async def download_result(project_id: str):
    if project_id not in projects:
        raise HTTPException(404, "Project not found")
    
    proj = projects[project_id]
    output_file = proj.get("output_file")
    if not output_file or not Path(output_file).exists():
        raise HTTPException(404, "No output file available")
    
    return FileResponse(
        output_file,
        filename=Path(output_file).name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ─── Match Review (Edit before writing) ───────────────────────────────────

@app.post("/api/project/{project_id}/match/{match_index}/update")
async def update_match(
    project_id: str,
    match_index: int,
    ep: float = Form(...),
    column: str = Form("X"),
    reason: str = Form(""),
    save_as_rule: bool = Form(False),
):
    if project_id not in projects:
        raise HTTPException(404)
    proj = projects[project_id]
    if match_index >= len(proj.get("matches", [])):
        raise HTTPException(404)

    match = proj["matches"][match_index]
    old_ep = match["ep"]
    match["ep"] = ep
    match["column"] = column
    if reason:
        match["edit_reason"] = reason
        match["warning"] = (match.get("warning", "") + " | " if match.get("warning") else "") + f"Manuell: {reason}"

    # Save as permanent rule if requested
    if save_as_rule and reason:
        save_rule({
            "type": "price_override",
            "oz": match["oz"],
            "bezeichnung": match.get("bezeichnung", ""),
            "ep": ep,
            "old_ep": old_ep,
            "column": column,
            "description": reason,
        })

    # Always save as learned price so future AI estimates improve
    if ep != old_ep and ep > 0:
        save_learned_price({
            "bezeichnung": match.get("bezeichnung", ""),
            "einheit": match.get("lv_einheit", ""),
            "ep": ep,
            "old_ep": old_ep,
            "reason": reason,
            "project": proj.get("name", ""),
            "oz": match["oz"],
            "source": match.get("match_source", ""),
        })
        # Also save to persistent price database
        add_price(
            material=match.get("bezeichnung", ""),
            ep=ep,
            einheit=match.get("lv_einheit", ""),
            source="user-correction",
            details=reason,
        )

    return {"ok": True, "old_ep": old_ep, "new_ep": ep}


@app.delete("/api/project/{project_id}/match/{match_index}")
async def remove_match(project_id: str, match_index: int):
    if project_id not in projects:
        raise HTTPException(404)
    proj = projects[project_id]
    if match_index >= len(proj.get("matches", [])):
        raise HTTPException(404)

    proj["matches"].pop(match_index)
    return {"ok": True}


# ─── Rules API ─────────────────────────────────────────────────────────────

@app.get("/api/rules")
async def get_rules():
    return {"rules": load_rules()}


@app.post("/api/rules")
async def add_rule(
    rule_type: str = Form("price_override"),
    oz: str = Form(""),
    ep: float = Form(0),
    column: str = Form(""),
    keyword: str = Form(""),
    description: str = Form(""),
    note: str = Form(""),
):
    rule = {
        "type": rule_type,
        "oz": oz,
        "ep": ep,
        "column": column,
        "keyword": keyword,
        "description": description,
        "note": note,
    }
    rules = save_rule(rule)
    return {"rules": rules}


@app.delete("/api/rules/{rule_id}")
async def remove_rule(rule_id: int):
    rules = delete_rule(rule_id)
    return {"rules": rules}


# ─── Helpers ───────────────────────────────────────────────────────────────

def _normalize_oz(oz: str) -> str:
    """Basic normalization: strip spaces, trailing dots, leading zeros per segment."""
    import re
    oz = oz.strip()
    oz = re.sub(r'^OZ\s*', '', oz, flags=re.IGNORECASE)
    oz = re.sub(r'\s+', '', oz)
    oz = oz.rstrip('.')
    parts = oz.split('.')
    oz = '.'.join(p.lstrip('0') or '0' for p in parts)
    return oz


def _build_oz_lookup(lv_positions: list) -> dict:
    """Build a comprehensive lookup that maps many possible OZ representations to LV positions.

    The LV OZ format varies per project (e.g., '4.1.10.', '04.01.0010', '4. 1. 10').
    Offers may reference positions in a completely different format (e.g., '01.04.0020' where
    '01' is the LV number prefix). This builds multiple keys for each position to maximize
    matching success.
    """
    lookup = {}

    for pos in lv_positions:
        oz = pos["oz"]
        # 1. Exact match
        lookup[oz] = pos
        # 2. Basic normalized
        norm = _normalize_oz(oz)
        lookup[norm] = pos
        # 3. Digits-only signature: "4.1.20" → "4120"
        digits_only = re.sub(r'[^\d]', '', oz)
        lookup[digits_only] = pos
        # 4. With trailing dot variations
        lookup[norm + '.'] = pos
        lookup[oz.rstrip('.')] = pos

    return lookup


def _find_lv_position(lv_pos_nr: str, oz_lookup: dict) -> object:
    """Try multiple strategies to match an offer's LV-POS-NR to an LV position.

    Strategies tried in order:
    1. Exact match
    2. Basic normalization (strip zeros, spaces, dots)
    3. Drop first segment (might be LV number prefix) — e.g., '01.04.0020' → '04.0020'
    4. Reinterpret long segments: '01.04.0020' → split '0020' into '00'+'20' → try '4.0.20', '4.20'
    5. Digits-only comparison
    6. Try all sub-segment splits of the last segment
    """
    import re
    lv_pos_nr = lv_pos_nr.strip()
    if not lv_pos_nr:
        return None

    # Strategy 1: Exact
    if lv_pos_nr in oz_lookup:
        return oz_lookup[lv_pos_nr]

    # Strategy 2: Basic normalized
    norm = _normalize_oz(lv_pos_nr)
    if norm in oz_lookup:
        return oz_lookup[norm]

    parts = norm.split('.')

    # Strategy 3: Drop first segment (LV number prefix like "01" in "01.04.0020")
    if len(parts) >= 2:
        without_prefix = '.'.join(parts[1:])
        without_prefix_norm = _normalize_oz(without_prefix)
        if without_prefix_norm in oz_lookup:
            return oz_lookup[without_prefix_norm]

    # Strategy 4: Expand long segments — "0020" might encode "00.20" or "0.20" or subsection+position
    # E.g., "01.04.0020" → parts=['1','4','20'], also try expanding: '1.4.0.20', '4.0.20', '4.00.20'
    expanded_variants = _expand_segments(parts)
    for variant in expanded_variants:
        v_norm = _normalize_oz(variant)
        if v_norm in oz_lookup:
            return oz_lookup[v_norm]

    # Strategy 5: Drop prefix + expand
    if len(parts) >= 2:
        for variant in _expand_segments(parts[1:]):
            v_norm = _normalize_oz(variant)
            if v_norm in oz_lookup:
                return oz_lookup[v_norm]

    # Strategy 6: Digits-only match
    digits = re.sub(r'[^\d]', '', lv_pos_nr)
    if digits in oz_lookup:
        return oz_lookup[digits]

    # Strategy 7: Try digits without leading zeros as a last resort
    digits_stripped = digits.lstrip('0') or '0'
    if digits_stripped in oz_lookup:
        return oz_lookup[digits_stripped]

    return None


def _expand_segments(parts: list) -> list:
    """Generate variants by splitting long numeric segments.

    E.g., ['4', '0020'] → ['4.0020', '4.00.20', '4.0.020', '4.002.0', '4.0.0.20']
    This handles cases where '0020' might actually be two sub-segments like '00'+'20'.
    """
    results = ['.'.join(parts)]

    # Find the longest segment and try splitting it
    for i, seg in enumerate(parts):
        if len(seg) >= 4:
            # Try splitting at each position
            for split_pos in range(2, len(seg)):
                left = seg[:split_pos]
                right = seg[split_pos:]
                new_parts = parts[:i] + [left, right] + parts[i+1:]
                results.append('.'.join(new_parts))
                # Also try stripping zeros from the split parts
                new_stripped = parts[:i] + [left.lstrip('0') or '0', right.lstrip('0') or '0'] + parts[i+1:]
                results.append('.'.join(new_stripped))

    return results


def _guess_supplier(filename: str) -> str:
    """Guess supplier from filename. Reference numbers -> Unbekannt."""
    import re
    name = Path(filename).stem
    for pat in ["angebot", "Angebot", "offerte", "Offerte", "AG_", "ag_",
                "_2024", "_2025", "_2026", "2024", "2025", "2026"]:
        name = name.replace(pat, "")
    name = name.strip("_- ")
    if not name: return "Unbekannt"
    digits = sum(1 for c in name if c.isdigit())
    letters = sum(1 for c in name if c.isalpha())
    total = len(name.replace(" ", "").replace("-", "").replace("_", ""))
    if total > 0 and (digits / total > 0.5 or letters < 3):
        return "Unbekannt"
    name = re.sub(r"[_\\-]+", " ", name).strip()
    name = re.sub(r"\\s+\\d[\\d\\-]*$", "", name).strip()
    return name or "Unbekannt"

def _get_match_summary(proj: dict) -> dict:
    matches = proj.get("matches", [])
    return {
        "total": len(matches),
        "stoffe": sum(1 for m in matches if m["column"] == "X"),
        "nu": sum(1 for m in matches if m["column"] == "M"),
        "warnings": len(proj.get("match_warnings", [])),
        "suppliers": list(set(m["supplier"] for m in matches)),
    }


# ─── Run ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
