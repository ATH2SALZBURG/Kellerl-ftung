#!/usr/bin/env python3
"""Rohpakete des Aussengeraets zu Tagesdateien und Dashboard-Daten verarbeiten.

Ablauf:
  1. alle CSV-Dateien unter roh/ einlesen
  2. nach Tagen sortiert an tage/JJJJ-MM-TT.csv anhaengen, Dubletten entfernen
  3. verarbeitete Rohdateien loeschen
  4. daten.json fuer das Dashboard bauen

Wird von der GitHub-Action aufgerufen, laeuft aber auch von Hand:
    python werkzeuge/verarbeiten.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# Die Geraete schreiben oesterreichische Ortszeit. Die GitHub-Action laeuft
# dagegen in UTC -- ohne Umrechnung waere das 24-Stunden-Fenster um ein bis
# zwei Stunden verschoben und der Zeitstempel im Dashboard falsch.
try:
    from zoneinfo import ZoneInfo
    ZONE = ZoneInfo("Europe/Vienna")
except Exception:          # pragma: no cover -- Notnagel ohne Zeitzonendaten
    ZONE = None


def jetzt_ortszeit():
    if ZONE is None:
        return datetime.now()
    return datetime.now(ZONE).replace(tzinfo=None)

WURZEL = Path(__file__).resolve().parent.parent
ROH = WURZEL / "roh"
TAGE = WURZEL / "tage"
ZIEL_JSON = WURZEL / "daten.json"

SPALTEN = [
    "zeitstempel", "zeitstufe", "status",
    "t_innen", "rh_innen", "ah_innen",
    "t_aussen", "rh_aussen", "ah_aussen",
    "differenz", "alter_s", "luefter", "grund",
]

# Wie viele Tage das Dashboard zeigt
TAGE_IM_DASHBOARD = 7
# Auflösung der Wochenansicht in Minuten (sonst wird die Datei unnötig groß)
WOCHE_RASTER_MIN = 15


def zahl(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def zeile_zerlegen(zeile: str):
    teile = zeile.rstrip("\n").split(";")
    if len(teile) < 12:
        return None
    # Das Grund-Feld kann fehlen (aeltere Firmware)
    while len(teile) < len(SPALTEN):
        teile.append("")
    satz = dict(zip(SPALTEN, teile[:len(SPALTEN)]))
    if not satz["zeitstempel"] or satz["zeitstempel"].startswith("#"):
        return None
    if satz["zeitstempel"] == "zeitstempel":
        return None
    try:
        datetime.strptime(satz["zeitstempel"], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return satz


def rohdaten_einlesen():
    saetze = []
    dateien = sorted(ROH.rglob("*.csv")) if ROH.exists() else []
    for pfad in dateien:
        for zeile in pfad.read_text(encoding="utf-8").splitlines():
            satz = zeile_zerlegen(zeile)
            if satz:
                saetze.append(satz)
    return saetze, dateien


def tagesdateien_aktualisieren(saetze):
    """Saetze in tage/JJJJ-MM-TT.csv einsortieren, Dubletten entfernen."""
    TAGE.mkdir(exist_ok=True)
    nach_tag = defaultdict(list)
    for satz in saetze:
        nach_tag[satz["zeitstempel"][:10]].append(satz)

    beruehrt = []
    for tag, neue in nach_tag.items():
        pfad = TAGE / f"{tag}.csv"
        vorhanden = {}
        if pfad.exists():
            for zeile in pfad.read_text(encoding="utf-8").splitlines():
                satz = zeile_zerlegen(zeile)
                if satz:
                    vorhanden[satz["zeitstempel"]] = satz
        for satz in neue:
            vorhanden[satz["zeitstempel"]] = satz

        zeilen = [";".join(SPALTEN)]
        for stempel in sorted(vorhanden):
            satz = vorhanden[stempel]
            zeilen.append(";".join(satz[s] for s in SPALTEN))
        pfad.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
        beruehrt.append(pfad.name)

    return beruehrt


def alle_saetze_laden(tage_zurueck):
    grenze = jetzt_ortszeit() - timedelta(days=tage_zurueck)
    saetze = []
    if not TAGE.exists():
        return saetze
    for pfad in sorted(TAGE.glob("*.csv")):
        try:
            tag = datetime.strptime(pfad.stem, "%Y-%m-%d")
        except ValueError:
            continue
        if tag < grenze - timedelta(days=1):
            continue
        for zeile in pfad.read_text(encoding="utf-8").splitlines():
            satz = zeile_zerlegen(zeile)
            if satz:
                saetze.append(satz)
    saetze.sort(key=lambda s: s["zeitstempel"])
    return saetze


def punkt(satz):
    return {
        "t": satz["zeitstempel"],
        "ti": zahl(satz["t_innen"]),
        "ri": zahl(satz["rh_innen"]),
        "ai": zahl(satz["ah_innen"]),
        "ta": zahl(satz["t_aussen"]),
        "ra": zahl(satz["rh_aussen"]),
        "aa": zahl(satz["ah_aussen"]),
        "d": zahl(satz["differenz"]),
        "l": 1 if satz["luefter"] == "1" else 0,
        "s": satz["status"],
    }


def mitteln(gruppe):
    """Mehrere Punkte zu einem zusammenfassen (Mittelwert, Luefter = Anteil)."""
    def schnitt(feld):
        werte = [p[feld] for p in gruppe if p[feld] is not None]
        return round(sum(werte) / len(werte), 2) if werte else None

    return {
        "t": gruppe[0]["t"],
        "ti": schnitt("ti"), "ri": schnitt("ri"), "ai": schnitt("ai"),
        "ta": schnitt("ta"), "ra": schnitt("ra"), "aa": schnitt("aa"),
        "d": schnitt("d"),
        "l": round(sum(p["l"] for p in gruppe) / len(gruppe), 2),
        "s": "ok" if all(p["s"] == "ok" for p in gruppe) else gruppe[-1]["s"],
    }


def rastern(punkte, minuten):
    if not punkte:
        return []
    eimer = defaultdict(list)
    for p in punkte:
        zeit = datetime.strptime(p["t"], "%Y-%m-%d %H:%M:%S")
        schluessel = zeit.replace(
            minute=(zeit.minute // minuten) * minuten, second=0)
        eimer[schluessel].append(p)
    return [mitteln(eimer[k]) for k in sorted(eimer)]


GRUND_GRUPPEN = [
    ("frost", ("Frostgrenze",)),
    ("hitze", ("Hitzegrenze",)),
    ("kondensation", ("Kondensationsgefahr",)),
    ("mindestlaufzeit", ("Mindestlaufzeit",)),
    ("zu_geringe_differenz", ("Einschaltschwelle",)),
    ("hysterese_aus", ("Ausschaltschwelle",)),
    ("sensorfehler", ("unplausibel",)),
    ("funkausfall", ("Empfang", "Aussengeraet empfangen")),
]


def grund_gruppe(satz):
    grund = satz.get("grund") or ""
    status = satz.get("status")
    if status == "funk":
        return "funkausfall"
    if status == "sensor":
        return "sensorfehler"
    for name, stichworte in GRUND_GRUPPEN:
        if any(w in grund for w in stichworte):
            return name
    return "sonstiges"


def statistik_bauen(saetze):
    """Pro Tag: Laufzeit und warum nicht gelueftet wurde."""
    nach_tag = defaultdict(lambda: {"minuten": 0, "luefter_min": 0,
                                    "gruende": defaultdict(int)})
    for satz in saetze:
        tag = satz["zeitstempel"][:10]
        eintrag = nach_tag[tag]
        eintrag["minuten"] += 1
        if satz["luefter"] == "1":
            eintrag["luefter_min"] += 1
        else:
            eintrag["gruende"][grund_gruppe(satz)] += 1

    ergebnis = []
    for tag in sorted(nach_tag):
        e = nach_tag[tag]
        ergebnis.append({
            "tag": tag,
            "minuten": e["minuten"],
            "luefter_min": e["luefter_min"],
            "gruende": dict(e["gruende"]),
        })
    return ergebnis


def hauptprogramm():
    saetze, rohdateien = rohdaten_einlesen()
    print(f"{len(saetze)} Datensaetze aus {len(rohdateien)} Rohdateien gelesen.")

    if saetze:
        beruehrt = tagesdateien_aktualisieren(saetze)
        print("Tagesdateien aktualisiert:", ", ".join(beruehrt))

    # Rohdaten sind eingearbeitet und koennen weg.
    for pfad in rohdateien:
        pfad.unlink()
    if ROH.exists():
        for ordner in sorted(ROH.glob("*"), reverse=True):
            if ordner.is_dir() and not any(ordner.iterdir()):
                ordner.rmdir()
    ROH.mkdir(exist_ok=True)
    (ROH / ".gitkeep").write_text("", encoding="utf-8")

    alle = alle_saetze_laden(TAGE_IM_DASHBOARD)
    punkte = [punkt(s) for s in alle]

    grenze_tag = jetzt_ortszeit() - timedelta(hours=24)
    letzte_24h = [p for p in punkte
                  if datetime.strptime(p["t"], "%Y-%m-%d %H:%M:%S") >= grenze_tag]

    # Fuer die Kacheln den letzten Satz mit echten Messwerten nehmen: Ist der
    # juengste Eintrag ein Funkausfall, stehen dort keine Zahlen, und die
    # Kacheln zeigen sonst nur Striche.
    letzte_messung = None
    for p in reversed(punkte):
        if p["ai"] is not None and p["aa"] is not None:
            letzte_messung = p
            break

    daten = {
        "erzeugt": jetzt_ortszeit().strftime("%Y-%m-%d %H:%M:%S"),
        "aktuell": punkte[-1] if punkte else None,
        "letzte_messung": letzte_messung,
        "zeitstufe": alle[-1]["zeitstufe"] if alle else None,
        "grund": alle[-1].get("grund") if alle else None,
        "tag": letzte_24h,
        "woche": rastern(punkte, WOCHE_RASTER_MIN),
        "statistik": statistik_bauen(alle),
    }

    ZIEL_JSON.write_text(
        json.dumps(daten, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    print(f"daten.json geschrieben: {len(punkte)} Punkte, "
          f"{len(daten['woche'])} gerastert, "
          f"{ZIEL_JSON.stat().st_size // 1024} KB")


if __name__ == "__main__":
    hauptprogramm()
