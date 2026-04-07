import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

# Unit conversion factors (density: t/m³)
DENSITY = {
    "schotter": 1.82, "kies": 1.80, "splitt": 1.75,
    "frostschutz": 1.85, "frostschutzschicht": 1.85,
    "sand": 1.55, "brechsand": 1.60,
    "mutterboden": 1.45, "oberboden": 1.45,
    "beton": 2.35, "magerbeton": 2.30,
    "asphalt": 2.40, "asphalttragschicht": 2.40,
    "mineralbeton": 1.90, "schottertragschicht": 1.85,
    "recyclingmaterial": 1.70, "rcl": 1.70,
}

# Color codes for Excel
COLOR_GREEN = "E7FFDE"   # Real offer prices
COLOR_ORANGE = "FFA500"  # Internal price DB
COLOR_RED = "FF0000"     # Internet research

# Column mapping for LV3 Excel
LV_COLUMNS = {
    "oz": 1,          # A - Position number
    "bezeichnung": 2, # B - Description
    "menge": 3,       # C - Quantity
    "einheit": 4,     # D - Unit
    "nu_ep": 13,      # M - EP EK (Nachunternehmer/Subcontractor unit price)
    "lieferant": 20,  # T - Supplier name
    # F-columns for component breakdown (F7 to F2) - Q to V
    "f7": 17,         # Q - Component 1 (first material)
    "f6": 18,         # R - Component 2
    "f5": 19,         # S - Component 3
    "f4": 20,         # T - Component 4 (also Lieferant column - shared)
    "f3": 21,         # U - Component 5
    "f2": 22,         # V - Component 6
    # "f1": 23,        # W - LEAVE EMPTY (not used)
    "stoffe_kosten": 24,  # X - Stoffe-Kosten (total material cost)
}

# Subcontractor trades (go to column M, not X)
NU_TRADES = [
    "asphalt", "asphaltarbeiten", "tragschicht", "deckschicht", "bindemittel",
    "metallbau", "schlosser", "geländer", "handlauf", "zaun",
    "elektro", "beleuchtung", "kabel", "leuchte",
    "bewässerung", "beregnungsanlage",
    "steinmetz", "naturstein",
    "spezialtiefbau", "bohrpfahl", "spundwand",
    "vermessung", "planunterlagen", "dokumentation", "prüfung",
    "untersuchung", "probenahme", "beprobung", "laboruntersuchung",
    "gutachten", "gutachter", "sachverständig",
    "schadstoff", "altlasten", "abfallrechtl", "abfallrechtlich",
    "verrechnungssatz", "verrechnungsatz", "stundensatz", "honorar",
    "projektleitung", "projektbearbeitung", "geschäftsleitung",
    "baustellentermin", "bauüberwachung", "baubegleitung",
    "kampfmittel", "kampfmittelsondierung",
    "baugrunduntersuchung", "bodengutachten", "geotechnik",
    "verkehrssicherung", "absperrung", "beschilderung",
]

# Fuzzy match thresholds
FUZZY_THRESHOLD_SURE = 85    # Auto-assign (high confidence)
FUZZY_THRESHOLD_MAYBE = 70   # Send to Claude for verification
