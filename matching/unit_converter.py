"""Unit conversion for construction materials.

KEY FIX: Added volume→area conversion using layer thickness extracted from LV description.
e.g., "Schotter einbauen d=20cm" → thickness=0.20m → t-price × density × 0.20 = m²-price
"""
import re
import json
from pathlib import Path
from typing import Optional, Tuple
import config

_CACHE_FILE = Path(__file__).parent.parent / "learned_conversions.json"
_learned_cache = {}


def _load_cache():
    global _learned_cache
    if _CACHE_FILE.exists():
        try:
            _learned_cache = json.loads(_CACHE_FILE.read_text())
        except Exception:
            _learned_cache = {}


def _save_cache():
    _CACHE_FILE.write_text(json.dumps(_learned_cache, indent=2, ensure_ascii=False))


def _cache_key(from_u: str, to_u: str, material: str) -> str:
    return f"{from_u}|{to_u}|{material.lower().strip()}"


_load_cache()


def convert_unit_price(
    ep: float,
    from_unit: str,
    to_unit: str,
    material_hint: str = "",
) -> Tuple[Optional[float], str]:
    """Convert a unit price from one unit to another.

    Returns: (converted_price, explanation_string)
    If conversion not possible, returns (None, reason).
    """
    from_u = _normalize_unit(from_unit)
    to_u = _normalize_unit(to_unit)

    if from_u == to_u:
        return ep, f"{ep:.2f} €/{to_unit} (gleiche Einheit)"

    # ── NEW: Volume/Weight → Area conversion (t→m², m³→m²) ──
    # Requires layer thickness from the LV description
    if to_u == "m²" and from_u in ("t", "m³"):
        thickness = _extract_thickness(material_hint)
        if thickness and thickness > 0:
            if from_u == "t":
                density = _guess_density(material_hint.lower())
                if density:
                    # t → m³ → m²
                    # EP/t × (1/density) = EP/m³
                    # EP/m³ × thickness = EP/m²
                    ep_per_m3 = ep / density
                    ep_per_m2 = ep_per_m3 * thickness
                    converted = round(ep_per_m2, 2)
                    explanation = (f"{ep:.2f}€/t ÷ {density}t/m³ = {ep_per_m3:.2f}€/m³ "
                                   f"× {thickness}m Stärke = {converted:.2f}€/m²")
                    return converted, explanation
            elif from_u == "m³":
                # m³ → m²: EP/m³ × thickness = EP/m²
                ep_per_m2 = ep * thickness
                converted = round(ep_per_m2, 2)
                explanation = f"{ep:.2f}€/m³ × {thickness}m Stärke = {converted:.2f}€/m²"
                return converted, explanation

    # ── NEW: Area → Volume/Weight (m²→t, m²→m³) — reverse ──
    if from_u == "m²" and to_u in ("t", "m³"):
        thickness = _extract_thickness(material_hint)
        if thickness and thickness > 0:
            if to_u == "m³":
                ep_per_m3 = ep / thickness
                converted = round(ep_per_m3, 2)
                explanation = f"{ep:.2f}€/m² ÷ {thickness}m = {converted:.2f}€/m³"
                return converted, explanation
            elif to_u == "t":
                density = _guess_density(material_hint.lower())
                if density:
                    ep_per_m3 = ep / thickness
                    ep_per_t = ep_per_m3 * density
                    converted = round(ep_per_t, 2)
                    explanation = f"{ep:.2f}€/m² ÷ {thickness}m × {density}t/m³ = {converted:.2f}€/t"
                    return converted, explanation

    # ── Standard conversions ──
    factor = _get_conversion_factor(from_u, to_u, material_hint)
    if factor:
        converted = round(ep * factor, 2)
        explanation = f"{ep:.2f} €/{from_unit} × {factor:.3f} = {converted:.2f} €/{to_unit}"
        return converted, explanation

    # Try inverse
    factor_inv = _get_conversion_factor(to_u, from_u, material_hint)
    if factor_inv:
        converted = round(ep / factor_inv, 2)
        explanation = f"{ep:.2f} €/{from_unit} ÷ {factor_inv:.3f} = {converted:.2f} €/{to_unit}"
        return converted, explanation

    # Check learned cache
    key = _cache_key(from_u, to_u, material_hint)
    if key in _learned_cache:
        factor = _learned_cache[key]
        converted = round(ep * factor, 2)
        explanation = f"{ep:.2f} €/{from_unit} × {factor:.3f} = {converted:.2f} €/{to_unit} (gelernt)"
        return converted, explanation

    # Ask Claude for unknown conversions
    factor = _ask_claude_conversion(from_u, to_u, material_hint)
    if factor:
        _learned_cache[key] = factor
        _save_cache()
        converted = round(ep * factor, 2)
        explanation = f"{ep:.2f} €/{from_unit} × {factor:.3f} = {converted:.2f} €/{to_unit} (Claude)"
        return converted, explanation

    return None, f"Keine Umrechnung möglich: {from_unit} → {to_unit}"


def _extract_thickness(text: str) -> Optional[float]:
    """Extract layer thickness from LV description.

    Looks for patterns like:
    - "d=20cm", "d 20 cm", "Stärke 20 cm"
    - "20 cm stark", "d=0,20m"
    - "d bis 20 cm", "d 15-20 cm" (takes the larger value)

    Returns thickness in METERS (0.20 for 20cm).
    """
    if not text:
        return None

    text_lower = text.lower()

    # Pattern 1: "d=20cm", "d 20cm", "d=0,20m", "d bis 20 cm", "d 15-20 cm"
    patterns = [
        # "d=20 cm" or "d 20cm" or "d=0,20 m"
        r'd\s*[=:]\s*(?:\d+[\-–]\s*)?(\d+(?:[.,]\d+)?)\s*(cm|m)\b',
        r'\bd\s+(?:bis\s+)?(?:\d+[\-–]\s*)?(\d+(?:[.,]\d+)?)\s*(cm|m)\b',
        # "Stärke 20 cm", "Dicke 0,20m"
        r'(?:stärke|dicke|schichtdicke|schichtstärke)\s*(?:ca\.?\s*)?(?:\d+[\-–]\s*)?(\d+(?:[.,]\d+)?)\s*(cm|m)\b',
        # "20 cm stark", "20cm dick"
        r'(\d+(?:[.,]\d+)?)\s*(cm|m)\s+(?:stark|dick)\b',
        # "d bis 20 cm" pattern with range
        r'd\s+(?:bis\s+)?(\d+(?:[.,]\d+)?)\s*(cm|m)',
    ]

    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            value = float(match.group(1).replace(',', '.'))
            unit = match.group(2)
            if unit == "cm":
                return value / 100  # cm → m
            else:
                return value  # already in m

    return None


async def _ask_claude_conversion_async(from_u: str, to_u: str, material_hint: str) -> Optional[float]:
    """Ask Claude API for a conversion factor and cache it (async)."""
    if not config.ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": (
                f"Bauwesen Einheitenumrechnung: Was ist der Umrechnungsfaktor von {from_u} nach {to_u} "
                f"für das Material '{material_hint}'?\n"
                f"Antworte NUR mit einer Zahl (der Faktor, z.B. 1.82). "
                f"Beispiel: 1 t Schotter = 0.549 m³, also Faktor t→m³ = 0.549\n"
                f"Faktor {from_u}→{to_u}:"
            )}],
        )
        text = response.content[0].text.strip()
        match = re.search(r'[\d]+[.,]?[\d]*', text)
        if match:
            return float(match.group().replace(',', '.'))
    except Exception:
        pass
    return None


def _ask_claude_conversion(from_u: str, to_u: str, material_hint: str) -> Optional[float]:
    """Sync wrapper."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, _ask_claude_conversion_async(from_u, to_u, material_hint)).result()
    return asyncio.run(_ask_claude_conversion_async(from_u, to_u, material_hint))


def _normalize_unit(unit: str) -> str:
    u = unit.lower().strip().replace(".", "")
    mapping = {
        "m2": "m²", "m3": "m³", "qm": "m²", "cbm": "m³",
        "stk": "st", "stck": "st", "stück": "st",
        "tonne": "t", "to": "t",
        "kg": "kg", "kilogramm": "kg",
        "lfm": "m", "lfdm": "m", "lm": "m",
        "meter": "m", "pauschal": "ps", "psch": "ps",
        "liter": "l", "sack": "sack",
    }
    return mapping.get(u, u)


def _get_conversion_factor(from_u: str, to_u: str, material_hint: str) -> Optional[float]:
    hint = material_hint.lower()

    if from_u == "t" and to_u == "m³":
        density = _guess_density(hint)
        if density:
            return 1.0 / density

    if from_u == "m³" and to_u == "t":
        density = _guess_density(hint)
        if density:
            return density

    if from_u == "kg" and to_u == "t":
        return 0.001
    if from_u == "t" and to_u == "kg":
        return 1000.0

    return None


def _guess_density(hint: str) -> Optional[float]:
    for keyword, density in config.DENSITY.items():
        if keyword in hint:
            return density

    if any(k in hint for k in ("schotter", "mineralgem", "mineralgemisch")):
        return 1.82
    if any(k in hint for k in ("kies", "kiessand")):
        return 1.80
    if any(k in hint for k in ("splitt", "edelsplitt")):
        return 1.75
    if any(k in hint for k in ("sand", "füllsand")):
        return 1.55
    if any(k in hint for k in ("erde", "boden", "humus")):
        return 1.45
    if any(k in hint for k in ("beton", "zement")):
        return 2.35

    return None


def apply_nk_zuschlag(ep: float, zuschlag_pct: float) -> Tuple[float, str]:
    if zuschlag_pct <= 0:
        return ep, f"{ep:.2f} €"
    new_ep = round(ep * (1 + zuschlag_pct / 100), 2)
    explanation = f"{ep:.2f} € + {zuschlag_pct:.1f}% NK = {new_ep:.2f} €"
    return new_ep, explanation
