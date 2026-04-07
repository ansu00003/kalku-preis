"""Local fuzzy matching between LV positions and offer items."""
import re
from typing import List, Tuple
from rapidfuzz import fuzz, process
import config


def fuzzy_match_positions(lv_positions: list, offer_items: list, supplier: str = "") -> dict:
    """Match offer items to LV positions using fuzzy text matching.
    
    Returns: {
        sure: [{lv_pos, offer_item, score, match_type}],
        maybe: [{lv_pos, offer_item, score}],
        no_match: [offer_items that didn't match anything]
    }
    """
    results = {"sure": [], "maybe": [], "no_match": []}
    
    if not lv_positions or not offer_items:
        results["no_match"] = offer_items
        return results
    
    # Pre-process LV positions for matching
    lv_texts = []
    lv_skip = set()  # indices to skip
    for i, pos in enumerate(lv_positions):
        text = _clean_for_matching(pos["bezeichnung"])
        lv_texts.append(text)
        # Skip if both stoffe and NU already have prices
        has_stoffe = pos.get("stoffe_ep") and pos["stoffe_ep"] > 0
        has_nu = pos.get("nu_ep") and pos["nu_ep"] > 0
        if has_stoffe and has_nu:
            lv_skip.add(i)
        # Skip internal/generic positions that suppliers don't offer
        if _is_internal_position(pos["bezeichnung"]):
            lv_skip.add(i)
    
    matched_lv_indices = set()
    
    for item in offer_items:
        offer_text = _clean_for_matching(item.get("text", ""))
        if not offer_text:
            results["no_match"].append(item)
            continue
        
        best_score = 0
        best_idx = -1
        match_type = ""
        
        for i, lv_text in enumerate(lv_texts):
            # Skip already-filled positions
            if i in lv_skip:
                continue
            # Multiple matching strategies
            score_partial = fuzz.partial_ratio(offer_text, lv_text)
            score_token = fuzz.token_sort_ratio(offer_text, lv_text)
            score_set = fuzz.token_set_ratio(offer_text, lv_text)
            
            # Also check key product terms
            keyword_bonus = _keyword_bonus(offer_text, lv_text)
            
            # Weighted score
            score = max(score_partial, score_token, score_set) + keyword_bonus
            
            if score > best_score:
                best_score = score
                best_idx = i
                if score_set >= score_partial and score_set >= score_token:
                    match_type = "token_set"
                elif score_token >= score_partial:
                    match_type = "token_sort"
                else:
                    match_type = "partial"
        
        if best_idx < 0:
            results["no_match"].append(item)
            continue
        
        match_data = {
            "lv_pos": lv_positions[best_idx],
            "offer_item": item,
            "score": round(best_score, 1),
            "match_type": match_type,
            "supplier": supplier,
        }
        
        if best_score >= config.FUZZY_THRESHOLD_SURE:
            results["sure"].append(match_data)
            matched_lv_indices.add(best_idx)
        elif best_score >= config.FUZZY_THRESHOLD_MAYBE:
            results["maybe"].append(match_data)
        else:
            results["no_match"].append(item)
    
    return results


def _clean_for_matching(text: str) -> str:
    """Normalize text for fuzzy matching."""
    text = text.lower().strip()
    # Remove common noise words
    noise = ["ca.", "ca", "gem.", "gemäß", "lt.", "laut", "inkl.", "inkl", "nach", "din", "en"]
    for n in noise:
        text = text.replace(n, " ")
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text)
    # Normalize units
    text = text.replace("m²", "m2").replace("m³", "m3")
    return text.strip()


def _keyword_bonus(offer_text: str, lv_text: str) -> int:
    """Bonus points for matching key construction product terms."""
    bonus = 0
    
    # Extract key technical terms
    keywords = _extract_keywords(offer_text)
    lv_keywords = _extract_keywords(lv_text)
    
    # Matching keywords get a bonus
    common = keywords & lv_keywords
    bonus += len(common) * 5
    
    # Specific product matches get extra bonus
    product_pairs = [
        ("bordstein", "bordstein"), ("pflaster", "pflaster"),
        ("rinne", "rinne"), ("schacht", "schacht"),
        ("rohr", "rohr"), ("geotextil", "geotextil"),
        ("asphalt", "asphalt"), ("beton", "beton"),
        ("zaun", "zaun"), ("geländer", "geländer"),
        ("rasen", "rasen"), ("saatgut", "saatgut"),
        ("baum", "baum"), ("strauch", "strauch"),
        ("splitt", "splitt"), ("schotter", "schotter"),
        ("kies", "kies"), ("sand", "sand"),
        ("rigole", "rigole"), ("mulde", "mulde"),
        ("noppenbahn", "noppenbahn"), ("folie", "folie"),
    ]
    
    for offer_kw, lv_kw in product_pairs:
        if offer_kw in offer_text and lv_kw in lv_text:
            bonus += 10
    
    # Dimension/size matching (e.g., "NW 150", "DN 200", "30x30")
    offer_dims = set(re.findall(r'\b(?:nw|dn|d)\s*(\d+)', offer_text))
    lv_dims = set(re.findall(r'\b(?:nw|dn|d)\s*(\d+)', lv_text))
    if offer_dims & lv_dims:
        bonus += 15
    
    size_pattern = r'(\d+)\s*[x×]\s*(\d+)'
    offer_sizes = set(re.findall(size_pattern, offer_text))
    lv_sizes = set(re.findall(size_pattern, lv_text))
    if offer_sizes & lv_sizes:
        bonus += 15
    
    return min(bonus, 25)  # Cap bonus


def _extract_keywords(text: str) -> set:
    """Extract meaningful technical keywords from text."""
    # Split into words, keep only meaningful ones (>3 chars)
    words = set(re.findall(r'[a-zäöüß]{4,}', text.lower()))
    # Remove very common words
    stopwords = {"nach", "oder", "auch", "wird", "sein", "eine", "einem", "eines",
                 "haben", "diese", "dieser", "dieses", "werden", "nicht", "sind",
                 "soll", "muss", "kann", "darf", "gemäß", "laut", "circa"}
    return words - stopwords


def _is_internal_position(bezeichnung: str) -> bool:
    """Check if a position is internal (not something a supplier would offer)."""
    bez_lower = bezeichnung.lower()
    internal_keywords = [
        "baustelle einrichten", "baustelle räumen",
        "baustelleneinrichtung", "baustellenverordnung",
        "vorankündigung", "sige-plan", "sige-koordinator",
        "lastplattendruckversuch", "proctordichte",
        "probegefäße", "kontrollprüfung",
        "grenzsteine", "vermessungspunkt",
        "baumwurzeln", "baumstamm",
        "lichtbilder", "bestandsplan",
        "regiearbeiten", "stundenlohn",
    ]
    return any(kw in bez_lower for kw in internal_keywords)
