"""Rules engine: store, load, and apply user-defined pricing rules."""
import json
from pathlib import Path
from datetime import datetime

RULES_FILE = Path("data/pricing_rules.json")


def _ensure_file():
    """Ensure the rules file and directory exist."""
    RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not RULES_FILE.exists():
        RULES_FILE.write_text("[]", encoding="utf-8")


def load_rules() -> list:
    """Load all pricing rules."""
    _ensure_file()
    try:
        return json.loads(RULES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def save_rule(rule: dict) -> list:
    """Add a new rule and return all rules."""
    rules = load_rules()
    rule["created"] = datetime.now().isoformat()
    rule["id"] = len(rules) + 1
    rules.append(rule)
    RULES_FILE.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[RULES] Saved rule #{rule['id']}: {rule.get('description', '')}")
    return rules


def delete_rule(rule_id: int) -> list:
    """Delete a rule by ID and return remaining rules."""
    rules = load_rules()
    rules = [r for r in rules if r.get("id") != rule_id]
    RULES_FILE.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    return rules


def apply_rules(matches: list, lv_positions: list) -> list:
    """Apply saved rules to matches before finalizing.

    Rules can:
    - Override a specific position's price
    - Set column (Stoffe/NU) for certain position types
    - Add warnings/notes

    Returns modified matches list.
    """
    rules = load_rules()
    if not rules:
        return matches

    for rule in rules:
        rule_type = rule.get("type", "")

        if rule_type == "price_override":
            # Match by bezeichnung (product name), not OZ — OZ changes between projects
            target_bez = rule.get("bezeichnung", "").lower().strip()
            for m in matches:
                match_bez = m.get("bezeichnung", "").lower().strip()
                # Match if bezeichnung contains the rule's key words or is very similar
                if target_bez and (target_bez in match_bez or match_bez in target_bez or _words_match(target_bez, match_bez)):
                    old_ep = m["ep"]
                    m["ep"] = rule["ep"]
                    m["explanation"] = f"Regel #{rule['id']}: {rule.get('description', '')} (vorher: {old_ep:.2f})"
                    m["warning"] = (m.get("warning", "") + " | " if m.get("warning") else "") + f"Regel #{rule['id']} angewandt"
                    print(f"[RULES] Applied rule #{rule['id']} to {m['oz']} ({match_bez[:40]}): {old_ep:.2f} -> {rule['ep']:.2f}")

        elif rule_type == "keyword_rule":
            keyword = rule.get("keyword", "").lower()
            for m in matches:
                if keyword in m.get("bezeichnung", "").lower():
                    if rule.get("column"):
                        m["column"] = rule["column"]
                    if rule.get("note"):
                        m["warning"] = (m.get("warning", "") + " | " if m.get("warning") else "") + rule["note"]

    return matches


def _words_match(a: str, b: str, threshold: float = 0.8) -> bool:
    """Check if two descriptions match including specs (dimensions, standards, materials).

    Must match BOTH:
    1. Product name words (70%+ overlap)
    2. ALL specs (numbers, dimensions, DIN standards) from the rule must appear in the target
    """
    import re
    skip = {"und", "mit", "für", "aus", "von", "der", "die", "das", "den", "dem",
            "ein", "eine", "nach", "bis", "zum", "zur", "ca", "cm", "mm", "liefern",
            "einbauen", "herstellen", "setzen", "verlegen"}

    # Extract spec tokens: numbers, dimensions (8/30/100), DIN refs, material codes
    spec_pattern = re.compile(r'\d+[/x×]\d+(?:[/x×]\d+)*|\d+(?:[.,]\d+)?|DIN\s*EN?\s*\d+|DN\s*\d+|NW\s*\d+|[A-Z]\d+/\d+')
    specs_a = set(spec_pattern.findall(a))
    specs_b = set(spec_pattern.findall(b))

    # ALL specs from the rule (a) must be present in the match target (b)
    if specs_a and not specs_a.issubset(specs_b):
        return False

    # Word overlap check for product name
    words_a = {w for w in a.split() if len(w) > 2 and w not in skip}
    words_b = {w for w in b.split() if len(w) > 2 and w not in skip}
    if not words_a or not words_b:
        return False
    overlap = len(words_a & words_b)
    smaller = min(len(words_a), len(words_b))
    return (overlap / smaller) >= threshold if smaller > 0 else False


def build_rules_context() -> str:
    """Build a text summary of rules for the AI matching prompt."""
    rules = load_rules()
    if not rules:
        return ""

    lines = ["GESPEICHERTE REGELN (vom Benutzer definiert):"]
    for r in rules:
        if r.get("type") == "price_override":
            lines.append(f"- OZ {r.get('oz')}: EP = {r.get('ep')} EUR -- {r.get('description', '')}")
        elif r.get("type") == "keyword_rule":
            lines.append(f"- Keyword '{r.get('keyword')}': {r.get('description', '')}")
        else:
            lines.append(f"- {r.get('description', str(r))}")

    return "\n".join(lines)
