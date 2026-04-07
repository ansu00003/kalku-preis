"""Parse GAEB files (X83/X84 XML and D83/P83 ASCII)."""
from pathlib import Path
from typing import Optional
import re
from lxml import etree


def parse_gaeb(filepath: str) -> dict:
    """Auto-detect GAEB format and parse. Returns {positions: [...], format: str}."""
    filepath = Path(filepath)
    suffix = filepath.suffix.lower()
    
    if suffix in (".x83", ".x84"):
        return _parse_xml_gaeb(filepath)
    elif suffix in (".d83", ".p83"):
        return _parse_ascii_gaeb(filepath)
    else:
        # Try XML first, then ASCII
        try:
            return _parse_xml_gaeb(filepath)
        except Exception:
            return _parse_ascii_gaeb(filepath)


def _parse_xml_gaeb(filepath: Path) -> dict:
    """Parse GAEB DA XML (X83/X84). Handles both GAEB 3.2 and 3.3 formats."""
    tree = etree.parse(str(filepath))
    root = tree.getroot()

    # Handle namespaces
    nsmap = root.nsmap
    ns = nsmap.get(None, "")
    prefix = f"{{{ns}}}" if ns else ""

    positions = []

    # Build category OZ path (BoQCtgy hierarchy gives the OZ prefix)
    def _collect_items(parent, oz_prefix=""):
        """Recursively collect items from BoQCtgy hierarchy."""
        # Process categories
        for ctgy in parent.findall(f"{prefix}BoQCtgy"):
            # Get category OZ part
            cat_rno = ctgy.get("RNoPart", "")
            if not cat_rno:
                rno_el = ctgy.find(f"{prefix}RNoPart")
                if rno_el is not None and rno_el.text:
                    cat_rno = rno_el.text.strip()

            new_prefix = f"{oz_prefix}{cat_rno}." if cat_rno else oz_prefix

            # Recurse into sub-categories
            boq_body = ctgy.find(f"{prefix}BoQBody")
            if boq_body is not None:
                _collect_items(boq_body, new_prefix)
            else:
                _collect_items(ctgy, new_prefix)

        # Process item lists
        for itemlist in parent.findall(f"{prefix}Itemlist"):
            for item in itemlist.findall(f"{prefix}Item"):
                _process_item(item, oz_prefix)

        # Also check for items directly under parent
        for item in parent.findall(f"{prefix}Item"):
            _process_item(item, oz_prefix)

    def _process_item(item, oz_prefix):
        """Extract data from a single Item element."""
        # OZ: check attribute first, then child element
        item_rno = item.get("RNoPart", "")
        if not item_rno:
            rno_el = item.find(f"{prefix}RNoPart")
            if rno_el is not None and rno_el.text:
                item_rno = rno_el.text.strip()

        oz = f"{oz_prefix}{item_rno}".rstrip(".")

        kurztext = ""
        langtext = ""
        menge = 0
        einheit = ""

        # Extract text from Description
        for desc in item.iter(f"{prefix}Description"):
            # OutlineText = Kurztext
            outline = desc.find(f"{prefix}OutlineText")
            if outline is not None:
                spans = [s.text.strip() for s in outline.iter(f"{prefix}span") if s.text]
                if spans:
                    kurztext = spans[-1] if spans else ""  # Last span is usually the short text

            # DetailTxt = Langtext
            detail = desc.find(f"{prefix}DetailTxt")
            if detail is not None:
                texts = [s.text.strip() for s in detail.iter(f"{prefix}span") if s.text]
                if texts:
                    langtext = "\n".join(texts)

            # CompleteText (GAEB 3.3) = full text including Kurztext + Langtext
            complete = desc.find(f"{prefix}CompleteText")
            if complete is not None:
                texts = [s.text.strip() for s in complete.iter(f"{prefix}span") if s.text]
                if texts:
                    if not kurztext:
                        kurztext = texts[-1] if texts else ""  # Last line often is Kurztext
                    langtext = "\n".join(texts)

        # Menge
        qty = item.find(f"{prefix}Qty")
        if qty is not None and qty.text:
            try:
                menge = float(qty.text)
            except ValueError:
                pass

        # Einheit
        qu = item.find(f"{prefix}QU")
        if qu is not None and qu.text:
            einheit = qu.text.strip()

        if oz or kurztext or langtext:
            positions.append({
                "oz": oz,
                "kurztext": kurztext,
                "langtext": langtext,
                "menge": menge,
                "einheit": einheit,
            })

    # Start from BoQBody
    for boq_body in root.iter(f"{prefix}BoQBody"):
        _collect_items(boq_body)
        break  # Only first BoQBody

    # Fallback: if no positions found, try flat iteration
    if not positions:
        for item in root.iter(f"{prefix}Item"):
            _process_item(item, "")

    return {
        "filename": filepath.name,
        "format": "GAEB-XML",
        "positions": positions,
        "total": len(positions),
    }


def _parse_ascii_gaeb(filepath: Path) -> dict:
    """Parse GAEB 83 ASCII (D83/P83) - fixed-width 80 chars/line."""
    # Try multiple encodings
    content = None
    for enc in ("cp437", "cp1252", "iso-8859-1", "utf-8"):
        try:
            content = filepath.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    
    if not content:
        raise ValueError(f"Could not decode {filepath}")
    
    lines = content.split("\n")
    positions = []
    current_pos = {}
    
    for line in lines:
        if len(line) < 2:
            continue
        
        satzart = line[:2].strip()
        
        if satzart == "25":  # Position header
            if current_pos and current_pos.get("oz"):
                positions.append(current_pos)
            current_pos = {"oz": "", "kurztext": "", "langtext": "", "menge": 0, "einheit": ""}
            # Extract OZ from columns
            if len(line) >= 20:
                current_pos["oz"] = line[2:11].strip()
        
        elif satzart == "26":  # Kurztext
            if len(line) >= 72:
                text = line[2:72].strip()
                if current_pos:
                    current_pos["kurztext"] += " " + text if current_pos["kurztext"] else text
        
        elif satzart == "21":  # Langtext
            if len(line) >= 72:
                text = line[2:72].strip()
                if current_pos:
                    current_pos["langtext"] += " " + text if current_pos["langtext"] else text
        
        elif satzart == "31":  # Menge + Einheit
            if len(line) >= 30 and current_pos:
                try:
                    menge_str = line[2:17].strip().replace(",", ".")
                    current_pos["menge"] = float(menge_str) if menge_str else 0
                except ValueError:
                    pass
                current_pos["einheit"] = line[17:22].strip()
    
    # Don't forget the last position
    if current_pos and current_pos.get("oz"):
        positions.append(current_pos)
    
    return {
        "filename": filepath.name,
        "format": "GAEB-ASCII",
        "positions": positions,
        "total": len(positions),
    }
