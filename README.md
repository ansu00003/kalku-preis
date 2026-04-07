# KALKU — Preiseintragung

Automatische Preiseintragung für Bau-Leistungsverzeichnisse. Liest Lieferanten-Angebote (PDF/Excel), ordnet sie LV-Positionen zu und schreibt die günstigsten Preise in die Kalkulations-Excel.

## Features

- **PDF-Angebote lesen** — Text-PDFs + Scans (Claude Vision)
- **Excel-Angebote lesen** — Kostenlos, kein API-Call nötig
- **GAEB-Dateien** — X83/X84 (XML) + D83/P83 (ASCII)
- **Hybrid-Matching** — Lokales Fuzzy-Matching + Claude AI für ambivalente Positionen
- **Einheiten-Umrechnung** — t↔m³ mit Materialdichten, Pauschal→EP
- **Nebenkosten-Verteilung** — Fracht, Verpackung proportional auf Positionen
- **Farbcodierung** — Grün (Angebot), Orange (PDB), Rot (Internet)
- **Prioritätssystem** — Grün > Orange > Rot, günstiger gewinnt

## Setup

```bash
# 1. Abhängigkeiten installieren
pip install -r requirements.txt

# 2. API Key konfigurieren
cp .env.example .env
# → ANTHROPIC_API_KEY eintragen

# 3. Starten
python app.py
# → http://localhost:8000
```

## Workflow

1. **Projekt erstellen** — Name eingeben
2. **LV hochladen** — Excel mit "Kalkulation"-Sheet (Spalten A/B/C/D/M/T/X)
3. **Angebote hochladen** — PDF oder Excel, mehrere gleichzeitig
4. **Matching prüfen** — Zuordnungen reviewen, ggf. korrigieren
5. **Excel herunterladen** — Fertige LV mit eingetragenen Preisen

## Spalten-Mapping (LV3-mani.xlsx)

| Spalte | Inhalt |
|--------|--------|
| A (1) | OZ — Positionsnummer |
| B (2) | Bezeichnung |
| C (3) | Menge |
| D (4) | Einheit |
| M (13) | NU EP — Nachunternehmer |
| T (20) | Lieferant-Name |
| X (24) | Stoffe EP — Material |

## Architektur

```
kalku-preis/
├── app.py              # FastAPI Server
├── config.py           # Einstellungen, Dichten, Spalten-Map
├── parsers/
│   ├── pdf_parser.py       # pdfplumber + PyMuPDF + Vision
│   ├── excel_parser.py     # LV + Angebots-Excel lesen
│   ├── gaeb_parser.py      # X83/X84/D83/P83
│   └── offer_extractor.py  # Claude API → strukturierte Daten
├── matching/
│   ├── fuzzy_matcher.py    # rapidfuzz lokales Matching
│   ├── claude_matcher.py   # Claude für ambivalente Positionen
│   └── unit_converter.py   # Einheiten + Nebenkosten
├── writer/
│   └── excel_writer.py     # Preise in LV schreiben
└── static/
    ├── index.html
    ├── styles.css
    └── app.js
```

## API-Kosten

- **Excel-Angebote**: $0.00 (kein API-Call)
- **Text-PDFs**: ~$0.01–0.03 pro Angebot (Extraktion)
- **Scan-PDFs**: ~$0.01/Seite (Vision)
- **Matching**: ~$0.01–0.03 pro Batch (nur ambivalente Positionen)
- **Typisches Projekt (10 Angebote)**: ~$0.10–0.30 gesamt
