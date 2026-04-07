"""Unit conversion for construction materials."""
import re
import json
from pathlib import Path
from typing import Optional, Tuple
import config

# Learned conversions cache file
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

    # Try direct conversion
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
        # Save to cache so we never ask again
        _learned_cache[key] = factor
        _save_cache()
        converted = round(ep * factor, 2)
        explanation = f"{ep:.2f} €/{from_unit} × {factor:.3f} = {converted:.2f} €/{to_unit} (Claude)"
        return converted, explanation

    return None, f"Keine Umrechnung möglich: {from_unit} → {to_unit}"


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
        # Extract number from response
        match = re.search(r'[\d]+[.,]?[\d]*', text)
        if match:
            return float(match.group().replace(',', '.'))
    except Exception:
        pass
    return None


def _ask_claude_conversion(from_u: str, to_u: str, material_hint: str) -> Optional[float]:
    """Sync wrapper — runs the async version in a new event loop."""
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
    """Normalize unit strings."""
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
    """Get conversion factor. Returns multiplier to go from_u → to_u."""
    hint = material_hint.lower()
    
    # Volume ↔ Weight (using density)
    if from_u == "t" and to_u == "m³":
        density = _guess_density(hint)
        if density:
            return 1.0 / density  # t → m³: divide by density
    
    if from_u == "m³" and to_u == "t":
        density = _guess_density(hint)
        if density:
            return density  # m³ → t: multiply by density
    
    # kg ↔ t
    if from_u == "kg" and to_u == "t":
        return 0.001
    if from_u == "t" and to_u == "kg":
        return 1000.0
    
    # m ↔ m (linear, just check names)
    # m² ↔ m² already caught by same-unit check
    
    # Sack → m² (for seeds, etc.)
    # This requires specific knowledge, return None to trigger Claude
    
    return None


def _guess_density(hint: str) -> Optional[float]:
    """Guess material density from description text."""
    for keyword, density in config.DENSITY.items():
        if keyword in hint:
            return density
    
    # Broader matching
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
    """Apply Nebenkosten percentage to unit price.
    
    Returns: (new_ep, explanation)
    """
    if zuschlag_pct <= 0:
        return ep, f"{ep:.2f} €"
    
    new_ep = round(ep * (1 + zuschlag_pct / 100), 2)
    explanation = f"{ep:.2f} € + {zuschlag_pct:.1f}% NK = {new_ep:.2f} €"
    return new_ep, explanation
