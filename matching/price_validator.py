"""Price validation for GaLaBau / Tiefbau materials.

Comprehensive price ranges based on German market prices 2025/2026 (netto).
Sources: Muffenrohr Angebot 1006360357, RHG.de, BayWa, BKI Baupreise 2026.

NOTE: These are MATERIAL prices (Lieferantenpreise), not EP inkl. Einbau.
Ranges are intentionally wide to account for quality/brand/quantity differences.
"""

from typing import Tuple, Optional
import re

# ══════════════════════════════════════════════════════════════════════
# PRICE RANGES: (min, max) in €/unit — NETTO Materialpreise
# ══════════════════════════════════════════════════════════════════════

PRICE_RANGES = {

    # ── ROHRE PP/KG (Kanalrohre) ──────────────────────────────────────
    "rohr_pp_dn110_m":          (5.00, 15.00),
    "rohr_pp_dn160_m":          (10.00, 25.00),
    "rohr_pp_dn200_m":          (15.00, 35.00),
    "rohr_pp_dn250_m":          (25.00, 60.00),
    "rohr_pp_dn315_m":          (35.00, 85.00),
    "rohr_pp_dn400_m":          (55.00, 130.00),
    "rohr_pp_dn500_m":          (90.00, 200.00),
    "rohr_kg_dn110_m":          (2.50, 8.00),
    "rohr_kg_dn160_m":          (4.00, 14.00),
    "rohr_kg_dn200_m":          (6.00, 22.00),
    "rohr_kg_dn250_m":          (10.00, 35.00),
    "rohr_kg_dn315_m":          (18.00, 55.00),
    "rohr_drain_dn65_m":        (0.50, 3.00),
    "rohr_drain_dn100_m":       (1.50, 5.00),
    "rohr_drain_dn150_m":       (3.00, 10.00),

    # ── ROHRFORMTEILE ─────────────────────────────────────────────────
    "bogen_pp_dn110_st":        (5.00, 15.00),
    "bogen_pp_dn160_st":        (10.00, 25.00),
    "bogen_pp_dn200_st":        (15.00, 35.00),
    "bogen_pp_dn250_st":        (25.00, 55.00),
    "bogen_pp_dn315_st":        (35.00, 80.00),
    "abzweig_pp_dn110_st":      (8.00, 18.00),
    "abzweig_pp_dn160_st":      (18.00, 40.00),
    "abzweig_pp_dn200_st":      (30.00, 65.00),
    "abzweig_pp_dn250_st":      (40.00, 85.00),
    "abzweig_pp_dn315_st":      (60.00, 120.00),
    "reduktion_pp_st":          (12.00, 35.00),
    "verschlussstopfen_st":     (0.80, 5.00),
    "dranflex_tstk_st":         (6.00, 18.00),
    "dranflex_übergang_st":     (6.00, 18.00),
    "reduktionsmuffe_st":       (6.00, 20.00),

    # ── SCHÄCHTE ──────────────────────────────────────────────────────
    "schacht_beton_dn1000_st":  (250.00, 600.00),
    "schacht_beton_dn800_st":   (180.00, 450.00),
    "schacht_pe_dn800_boden":   (350.00, 650.00),
    "schacht_pe_dn800_ring":    (200.00, 450.00),
    "schacht_pe_dn800_konus":   (200.00, 450.00),
    "schacht_pe_auflagering":   (100.00, 250.00),
    "schacht_bohrung_st":       (40.00, 120.00),
    "anschlussdichtung_dn110":  (15.00, 45.00),
    "anschlussdichtung_dn200":  (25.00, 70.00),
    "anschlussdichtung_dn315":  (35.00, 85.00),
    "drosseleinheit_st":        (250.00, 650.00),
    "beton_auflagering_st":     (8.00, 25.00),

    # ── STRASSENABLÄUFE / SINKKÄSTEN ──────────────────────────────────
    "strassenablauf_beton_st":  (40.00, 120.00),
    "strassenablauf_boden_st":  (10.00, 25.00),
    "strassenablauf_muffe_st":  (15.00, 35.00),
    "strassenablauf_schaft_st": (8.00, 18.00),
    "strassenablauf_ring_st":   (3.00, 10.00),
    "schmutzfänger_st":         (10.00, 30.00),
    "schlammeimer_st":          (8.00, 25.00),

    # ── SCHACHTABDECKUNGEN ────────────────────────────────────────────
    "schachtabdeckung_d400_st": (120.00, 350.00),
    "aufsatz_vgr_d400_st":     (120.00, 280.00),

    # ── ENTWÄSSERUNGSRINNEN ───────────────────────────────────────────
    "rinne_aco_v100_1m_st":    (80.00, 180.00),
    "rinne_aco_v150_1m_st":    (100.00, 250.00),
    "rinne_aco_v200_1m_st":    (150.00, 350.00),
    "rinne_rost_v100_1m_st":   (60.00, 160.00),
    "rinne_rost_v150_1m_st":   (70.00, 220.00),
    "rinne_einlaufkasten_st":  (100.00, 250.00),
    "rinne_stirnwand_st":      (12.00, 80.00),
    "rinne_profiline_keil_st": (60.00, 150.00),
    "rinne_profiline_rost_st": (60.00, 160.00),
    "rinne_profiline_stirn_st":(12.00, 35.00),
    "rinne_einsteckstutzen_st":(20.00, 55.00),
    "muldenrinne_beton_m":     (8.00, 25.00),

    # ── BORDSTEINE ────────────────────────────────────────────────────
    "tiefbord_8x30_m":         (3.00, 8.00),
    "rundbord_15x22_m":        (4.00, 10.00),
    "hochbord_15x30_m":        (4.50, 12.00),
    "hochbord_kurve_st":       (8.00, 25.00),
    "busbordstein_m":          (50.00, 130.00),
    "übergangsstein_st":       (80.00, 200.00),
    "übergangssteinset_set":   (20.00, 50.00),

    # ── PFLASTER / PLATTEN ────────────────────────────────────────────
    "pflaster_beton_standard_m2":  (10.00, 25.00),
    "pflaster_beton_system_m2":    (12.00, 30.00),
    "pflaster_rasenfuge_m2":       (12.00, 30.00),
    "pflaster_siliton_m2":         (18.00, 40.00),
    "platte_beton_40x40_m2":      (15.00, 40.00),
    "platte_belino_m2":            (25.00, 55.00),
    "platte_cassero_40x40_m2":    (10.00, 22.00),
    "bodenindikator_m2":          (40.00, 100.00),
    "bodenindikator_4cm_st":      (3.00, 10.00),
    "begleitstreifen_m2":         (40.00, 90.00),
    "fugenkreuz_100st":           (15.00, 45.00),
    "waterline_m":                (18.00, 45.00),
    "rainplus_m2":                (10.00, 22.00),

    # ── BLOCKSTUFEN / TREPPEN ─────────────────────────────────────────
    "blockstufe_beton_m":         (80.00, 220.00),
    "blockstufe_standard_st":     (60.00, 200.00),
    "zulage_kontraststreifen_m":  (30.00, 120.00),

    # ── WINKELSTÜTZEN / MAUERN ────────────────────────────────────────
    "winkelstütze_h80_100cm_st":  (50.00, 140.00),
    "winkelstütze_h80_50cm_st":   (30.00, 80.00),
    "winkelstütze_h80_ecke_st":   (80.00, 180.00),
    "winkelstütze_h100_st":       (60.00, 160.00),
    "ltec_winkel_55cm_st":        (35.00, 90.00),
    "ltec_ecke_90_st":            (120.00, 300.00),

    # ── RIGOLENSYSTEM ─────────────────────────────────────────────────
    "rigolentunnel_sc800_st":     (250.00, 550.00),
    "rigolentunnel_sc310_st":     (120.00, 280.00),
    "rigolen_endkappe_sc800_st":  (70.00, 170.00),
    "rigolen_endkappe_sc310_st":  (30.00, 80.00),

    # ── GEOTEXTIL / VLIES / FOLIEN ────────────────────────────────────
    "geotextil_grk3_m2":          (0.50, 2.50),
    "geotextil_grk5_m2":          (2.00, 8.00),
    "bändchengewebe_m2":          (1.50, 5.00),
    "pehd_folie_2mm_m2":          (5.00, 15.00),
    "pe_trennfolie_m2":           (0.30, 1.50),
    "schutzgleitlage_m2":         (3.00, 9.00),
    "dranelement_m2":             (4.00, 12.00),
    "pflasterfugenband_rol":      (35.00, 110.00),

    # ── SCHÜTTGÜTER ───────────────────────────────────────────────────
    "schotter_t":                 (12.00, 28.00),
    "schotter_m3":                (18.00, 42.00),
    "splitt_t":                   (15.00, 35.00),
    "splitt_m3":                  (22.00, 52.00),
    "kies_t":                     (10.00, 25.00),
    "kies_m3":                    (15.00, 38.00),
    "sand_m3":                    (12.00, 30.00),
    "sand_t":                     (8.00, 20.00),
    "frostschutz_t":              (8.00, 18.00),
    "frostschutz_m3":             (12.00, 28.00),
    "tragschicht_t":              (8.00, 18.00),
    "tragschicht_m3":             (12.00, 28.00),
    "recycling_t":                (4.00, 12.00),
    "recycling_m3":               (6.00, 18.00),
    "oberboden_m3":               (8.00, 25.00),
    "brechsand_t":                (10.00, 25.00),
    "fugensand_t":                (80.00, 200.00),
    "bettungssplitt_m3":          (25.00, 55.00),

    # ── BETON ─────────────────────────────────────────────────────────
    "transportbeton_c25_m3":     (85.00, 140.00),
    "transportbeton_c30_m3":     (90.00, 155.00),
    "magerbeton_m3":             (70.00, 120.00),

    # ── BEWEHRUNG ─────────────────────────────────────────────────────
    "betonstahl_t":              (700.00, 1100.00),
    "betonstahlmatte_m2":       (3.00, 10.00),

    # ── BÄUME / PFLANZEN ──────────────────────────────────────────────
    "baum_hochstamm_14_16":      (120.00, 350.00),
    "baum_hochstamm_16_18":      (180.00, 500.00),
    "baum_hochstamm_18_20":      (250.00, 700.00),
    "baum_hochstamm_20_25":      (400.00, 1200.00),
    "strauch_st":                (3.00, 25.00),
    "hecke_st":                  (5.00, 35.00),
    "bodendecker_st":            (1.00, 6.00),
    "staude_st":                 (2.00, 12.00),
    "rasen_saatgut_m2":          (0.20, 0.80),
    "rollrasen_m2":              (3.00, 9.00),
    "rindenmulch_m3":            (15.00, 40.00),
    "hackschnitzel_m3":          (12.00, 35.00),
    "baumsubstrat_m3":           (30.00, 80.00),
    "pflanzerde_m3":             (20.00, 50.00),

    # ── SONSTIGES ─────────────────────────────────────────────────────
    "trassenwarnband_st":        (5.00, 15.00),
    "fracht_pauschale_st":       (50.00, 200.00),
    "entladung_to":              (4.00, 12.00),
    "energiekostenzuschlag_st":  (10.00, 50.00),
    "transportschlaufenset_st":  (20.00, 60.00),
    "zaun_doppelstab_m":         (25.00, 80.00),
    "geländer_m":                (80.00, 250.00),
    "pollerleuchte_st":          (150.00, 600.00),
    "leerrohr_m":                (1.00, 5.00),
    "noppenbahn_m2":             (2.00, 8.00),
    "bitumenbahn_m2":            (5.00, 18.00),
}


# ══════════════════════════════════════════════════════════════════════
# MATERIAL GROUP DETECTION
# ══════════════════════════════════════════════════════════════════════

_MATERIAL_PATTERNS = [
    # Rohre PP — specific DN first
    (r"(awadukt|pp.?rohr|kgem).*(dn\s*315|od\s*315)", "m", "rohr_pp_dn315_m"),
    (r"(awadukt|pp.?rohr|kgem).*(dn\s*250|od\s*250)", "m", "rohr_pp_dn250_m"),
    (r"(awadukt|pp.?rohr|kgem).*(dn\s*200|od\s*200)", "m", "rohr_pp_dn200_m"),
    (r"(awadukt|pp.?rohr|kgem).*(dn\s*160|od\s*160)", "m", "rohr_pp_dn160_m"),
    (r"(awadukt|pp.?rohr|kgem).*(dn\s*110|od\s*110)", "m", "rohr_pp_dn110_m"),
    # Rohre KG
    (r"kg.?rohr.*(dn\s*315)", "m", "rohr_kg_dn315_m"),
    (r"kg.?rohr.*(dn\s*200)", "m", "rohr_kg_dn200_m"),
    (r"kg.?rohr.*(dn\s*160)", "m", "rohr_kg_dn160_m"),
    (r"kg.?rohr.*(dn\s*110)", "m", "rohr_kg_dn110_m"),
    # Dränrohr
    (r"dr(ä|ae)n.*(dn\s*65|dn65)", "m", "rohr_drain_dn65_m"),
    (r"dr(ä|ae)n.*(dn\s*100|dn100)", "m", "rohr_drain_dn100_m"),
    # Formteile
    (r"bogen.*(dn\s*315|od\s*315)", "st", "bogen_pp_dn315_st"),
    (r"bogen.*(dn\s*200|od\s*200)", "st", "bogen_pp_dn200_st"),
    (r"bogen.*(dn\s*160|od\s*160)", "st", "bogen_pp_dn160_st"),
    (r"bogen.*(dn\s*110|od\s*110)", "st", "bogen_pp_dn110_st"),
    (r"abzweig.*(dn\s*315|od\s*315)", "st", "abzweig_pp_dn315_st"),
    (r"abzweig.*(dn\s*250|od\s*250)", "st", "abzweig_pp_dn250_st"),
    (r"abzweig.*(dn\s*200|od\s*200)", "st", "abzweig_pp_dn200_st"),
    (r"abzweig.*(dn\s*160|od\s*160)", "st", "abzweig_pp_dn160_st"),
    (r"abzweig.*(dn\s*110|od\s*110)", "st", "abzweig_pp_dn110_st"),
    (r"reduktion", "st", "reduktion_pp_st"),
    (r"verschluss.?stopfen", "st", "verschlussstopfen_st"),
    # Schächte
    (r"system.?schacht.*boden|sandfang.*schacht", "st", "schacht_pe_dn800_boden"),
    (r"system.?schacht.*ring|schacht.?ring", "st", "schacht_pe_dn800_ring"),
    (r"system.?schacht.*konus|schacht.?konus", "st", "schacht_pe_dn800_konus"),
    (r"(system.?schacht|birco).*auflagering", "st", "schacht_pe_auflagering"),
    (r"bohrung.*dn|dn.*bohrung", "st", "schacht_bohrung_st"),
    (r"anschluss.?dichtung.*(dn\s*315|dn315)", "st", "anschlussdichtung_dn315"),
    (r"anschluss.?dichtung.*(dn\s*200|dn200)", "st", "anschlussdichtung_dn200"),
    (r"anschluss.?dichtung.*(dn\s*1[15]0)", "st", "anschlussdichtung_dn110"),
    (r"drossel.?einheit", "st", "drosseleinheit_st"),
    (r"beton.?auflagering|auflagering.*beton|auflagering.*dn\s*625", "st", "beton_auflagering_st"),
    # Abläufe
    (r"schmutzf(ä|ae)nger", "st", "schmutzfänger_st"),
    (r"(sad|schachtabdeckung|schacht.?deckel).*d\s*400", "st", "schachtabdeckung_d400_st"),
    (r"(vgr|aufsatz).*d\s*400", "st", "aufsatz_vgr_d400_st"),
    (r"schlam.?eimer", "st", "schlammeimer_st"),
    # Rinnen
    (r"(multiline|seal.?in).*v\s*150", "st", "rinne_aco_v150_1m_st"),
    (r"(multiline|seal.?in).*v\s*100", "st", "rinne_aco_v100_1m_st"),
    (r"drainlock.*rost.*150", "st", "rinne_rost_v150_1m_st"),
    (r"drainlock.*rost.*100", "st", "rinne_rost_v100_1m_st"),
    (r"einlaufkasten", "st", "rinne_einlaufkasten_st"),
    (r"(kombi)?stirnwand", "st", "rinne_stirnwand_st"),
    (r"profiline.*keil", "st", "rinne_profiline_keil_st"),
    (r"profiline.*rost", "st", "rinne_profiline_rost_st"),
    (r"profiline.*stirn", "st", "rinne_profiline_stirn_st"),
    (r"einsteckstutzen", "st", "rinne_einsteckstutzen_st"),
    (r"muldenrinne", "m", "muldenrinne_beton_m"),
    # Bordsteine
    (r"tiefbord", "m", "tiefbord_8x30_m"),
    (r"rundbord", "m", "rundbord_15x22_m"),
    (r"hochbord.*(kurve|r\s*=)|kurvenstein", "st", "hochbord_kurve_st"),
    (r"hochbord", "m", "hochbord_15x30_m"),
    (r"busbord", "m", "busbordstein_m"),
    (r"(ü|ue)bergangs.?stein.*set", "set", "übergangssteinset_set"),
    (r"(ü|ue)bergangs.?stein", "st", "übergangsstein_st"),
    # Pflaster
    (r"(cassero|system\s*16).*pflaster", "m2", "pflaster_beton_system_m2"),
    (r"stato.*rasenfuge", "m2", "pflaster_rasenfuge_m2"),
    (r"siliton", "m2", "pflaster_siliton_m2"),
    (r"belino", "m2", "platte_belino_m2"),
    (r"cassero.*platte", "m2", "platte_cassero_40x40_m2"),
    (r"betonplatte", "m2", "platte_beton_40x40_m2"),
    (r"bodenindikator.*30.*30.*8", "m2", "bodenindikator_m2"),
    (r"bodenindikator.*30.*30.*4", "st", "bodenindikator_4cm_st"),
    (r"begleitstreifen", "m2", "begleitstreifen_m2"),
    (r"fugen.?t|fugen.?kreuz|volfi", "st", "fugenkreuz_100st"),
    (r"water.?line", "m", "waterline_m"),
    (r"rainplus", "m2", "rainplus_m2"),
    # Stufen
    (r"blockstufe.*ma(ß|ss)", "m", "blockstufe_beton_m"),
    (r"blockstufe", "st", "blockstufe_standard_st"),
    (r"kontraststreifen|bicolor", "m", "zulage_kontraststreifen_m"),
    # Winkelstützen
    (r"l.?tec.*(ae|ecke|90)", "st", "ltec_ecke_90_st"),
    (r"l.?tec.*winkel", "st", "ltec_winkel_55cm_st"),
    (r"(privant|winkelst(ü|ue)tze).*h\s*=?\s*100", "st", "winkelstütze_h100_st"),
    (r"(privant|winkelst(ü|ue)tze).*ecke", "st", "winkelstütze_h80_ecke_st"),
    (r"(privant|winkelst(ü|ue)tze).*bl\s*=?\s*50", "st", "winkelstütze_h80_50cm_st"),
    (r"(privant|winkelst(ü|ue)tze)", "st", "winkelstütze_h80_100cm_st"),
    # Rigolen
    (r"rigolentunnel.*sc.?800", "st", "rigolentunnel_sc800_st"),
    (r"rigolentunnel.*sc.?310", "st", "rigolentunnel_sc310_st"),
    (r"endkappe.*(sc.?800|dn\s*600)", "st", "rigolen_endkappe_sc800_st"),
    (r"endkappe.*(sc.?310|dn\s*300)", "st", "rigolen_endkappe_sc310_st"),
    # Geotextil / Vlies
    (r"(geotextil|vlies).*(grk\s*3|165\s*g)", "m2", "geotextil_grk3_m2"),
    (r"(schutz)?vlies.*(grk\s*5|650\s*g)", "m2", "geotextil_grk5_m2"),
    (r"b(ä|ae)ndchengewebe", "m2", "bändchengewebe_m2"),
    (r"(pehd|pe.?hd|dichtungsbahn|kunststoffdichtung)", "m2", "pehd_folie_2mm_m2"),
    (r"(trenn.?folie|gleitfolie|tgf)", "m2", "pe_trennfolie_m2"),
    (r"(schutz.?gleit|sgl\s*500)", "m2", "schutzgleitlage_m2"),
    (r"(dr(ä|ae)n.?element|fkd\s*10)", "m2", "dranelement_m2"),
    (r"pflasterfugenband", "rol", "pflasterfugenband_rol"),
    # Schüttgüter
    (r"schotter", "t", "schotter_t"),
    (r"splitt", "t", "splitt_t"),
    (r"frostschutz", "t", "frostschutz_t"),
    (r"tragschicht", "t", "tragschicht_t"),
    (r"sand", "m3", "sand_m3"),
    (r"oberboden|mutterboden", "m3", "oberboden_m3"),
    (r"kies", "t", "kies_t"),
    # Beton
    (r"(transport)?beton.*c\s*30", "m3", "transportbeton_c30_m3"),
    (r"(transport)?beton.*c\s*25", "m3", "transportbeton_c25_m3"),
    (r"magerbeton", "m3", "magerbeton_m3"),
    # Sonstiges
    (r"trassenwarnband", "st", "trassenwarnband_st"),
    (r"fracht", "st", "fracht_pauschale_st"),
    (r"entladung", "to", "entladung_to"),
    (r"energiekosten", "st", "energiekostenzuschlag_st"),
]


# ══════════════════════════════════════════════════════════════════════
# SAME-TYPE GROUPS — must NOT be added as Nebenmaterial
# ══════════════════════════════════════════════════════════════════════

SAME_TYPE_GROUPS = [
    {"rohr", "awadukt", "kgem", "kanalrohr", "pp rohr", "kg rohr", "pp-rohr"},
    {"pflaster", "cassero", "siliton", "stato", "rasenfuge", "betonpflaster", "rainplus"},
    {"platte", "betonplatte", "belino", "gehwegplatte"},
    {"bordstein", "tiefbord", "hochbord", "rundbord", "busbord", "busbordstein"},
    {"vlies", "geotextil", "folie", "dichtungsbahn", "noppenbahn", "drainage",
     "bändchengewebe", "schutzgleitlage", "trennfolie", "dranelement", "pehd"},
    {"rinne", "multiline", "profiline", "aco", "entwässerungsrinne"},
    {"schotter", "splitt", "kies", "sand", "brechsand"},
    {"beton", "transportbeton", "magerbeton", "estrich"},
    {"blockstufe", "stufe", "keilstufe", "treppenstufe"},
    {"winkelstütze", "privant", "l-tec", "ltec", "stützwand", "stützmauer"},
    {"rigole", "rigolentunnel", "stormtech", "birco"},
    {"schacht", "systemschacht", "schachtring", "schachtkonus", "schachtboden"},
]


def _find_price_key(text: str) -> Optional[str]:
    """Find the best matching price range key for a material description."""
    t = text.lower()
    for pattern, _, key in _MATERIAL_PATTERNS:
        if re.search(pattern, t):
            return key
    return None


def validate_match(match: dict) -> Tuple[bool, str]:
    """Validate a match's EP against known price ranges.

    Returns: (is_valid, reason_string)
    """
    ep = float(match.get("ep", 0) or 0)
    if ep <= 0:
        return True, ""

    text = f"{match.get('offer_text', '')} {match.get('lv_text', '')}".lower()
    key = _find_price_key(text)

    if not key or key not in PRICE_RANGES:
        return True, ""

    min_p, max_p = PRICE_RANGES[key]

    # 30% tolerance for quantity discounts / premium brands
    tol_min = min_p * 0.7
    tol_max = max_p * 1.3

    if ep < tol_min:
        return False, f"EP {ep:.2f}€ zu niedrig (Bereich {min_p:.0f}-{max_p:.0f}€ für {key})"
    elif ep > tol_max:
        return False, f"EP {ep:.2f}€ zu hoch (Bereich {min_p:.0f}-{max_p:.0f}€ für {key})"

    if ep < min_p or ep > max_p:
        return True, f"EP {ep:.2f}€ am Rand ({min_p:.0f}-{max_p:.0f}€ für {key})"

    return True, ""


def validate_component_addition(main_text: str, component_text: str) -> Tuple[bool, str]:
    """Check if adding a component to a main material makes sense."""
    m = main_text.lower()
    c = component_text.lower()

    for group in SAME_TYPE_GROUPS:
        m_in = any(kw in m for kw in group)
        c_in = any(kw in c for kw in group)
        if m_in and c_in:
            found = [kw for kw in group if kw in m or kw in c]
            return False, f"Gleicher Typ — nicht addieren ({', '.join(found[:3])})"

    return True, ""


def get_price_range(text: str) -> Optional[Tuple[float, float, str]]:
    """Get price range for a material description."""
    key = _find_price_key(text)
    if key and key in PRICE_RANGES:
        mn, mx = PRICE_RANGES[key]
        return mn, mx, key
    return None
