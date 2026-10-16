#!/usr/bin/env python3
"""Rohpakete des Aussengeraets zu Tagesdateien und Dashboard-Daten verarbeiten.

Ablauf:
  1. alle CSV-Dateien unter roh/ einlesen
  2. nach Tagen sortiert an tage/JJJJ-MM-TT.csv anhaengen, Dubletten entfernen
  3. verarbeitete Rohdateien loeschen
  4. Tagesstatistik fortschreiben (tagesstatistik.json)
  5. daten.json fuer das Dashboard bauen

Zur Auflösung: Ein Jahr im Minutentakt waeren ueber 500000 Messpunkte. Deshalb
wird je Zeitraum unterschiedlich grob gerastert, und die langen Zeitraeume
kommen aus der Tagesstatistik statt aus den Rohzeilen.

Die Tagesstatistik wird nur fuer die Tage neu berechnet, die sich geaendert
haben -- sonst muesste die Action mit wachsender Historie jedes Mal laenger
rechnen.

Wird von der GitHub-Action aufgerufen, laeuft aber auch von Hand:
    python werkzeuge/verarbeiten.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# Die Geraete schreiben oesterreichische Ortszeit. Die GitHub-Action laeuft
# dagegen in UTC -- ohne Umrechnung waeren die Zeitfenster verschoben und der
# Zeitstempel im Dashboard falsch.
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
STATISTIK_JSON = WURZEL / "tagesstatistik.json"

SPALTEN = [
    "zeitstempel", "zeitstufe", "status",
    "t_innen", "rh_innen", "ah_innen",
    "t_aussen", "rh_aussen", "ah_aussen",
    "differenz", "alter_s", "luefter", "grund",
]

# Zeitraum -> (Tage zurueck, Raster in Minuten). None = aus der Tagesstatistik.
ZEITRAEUME = {
    "tag":   (1, 1),
    "woche": (7, 15),
    "monat": (31, 60),
}
JAHR_TAGE = 366


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
            zeilen.append(";".join(vorhanden[stempel][s] for s in SPALTEN))
        pfad.write_text("\n".join(zeilen) + "\n", encoding="utf-8")

    return sorted(nach_tag)


def tag_laden(tag):
    """Alle Saetze eines Tages aus der Tagesdatei."""
    pfad = TAGE / f"{tag}.csv"
    if not pfad.exists():
        return []
    saetze = []
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        satz = zeile_zerlegen(zeile)
        if satz:
            saetze.append(satz)
    return saetze


def vorhandene_tage():
    if not TAGE.exists():
        return []
    tage = []
    for pfad in TAGE.glob("*.csv"):
        try:
            datetime.strptime(pfad.stem, "%Y-%m-%d")
        except ValueError:
            continue
        tage.append(pfad.stem)
    return sorted(tage)


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


FELDER = ("ti", "ri", "ai", "ta", "ra", "aa", "d")


def mitteln(gruppe, zeitstempel=None):
    """Mehrere Punkte zu einem zusammenfassen (Mittelwert, Luefter = Anteil)."""
    ergebnis = {"t": zeitstempel or gruppe[0]["t"]}
    for feld in FELDER:
        werte = [p[feld] for p in gruppe if p.get(feld) is not None]
        ergebnis[feld] = round(sum(werte) / len(werte), 2) if werte else None
    ergebnis["l"] = round(sum(p.get("l", 0) for p in gruppe) / len(gruppe), 3)
    ergebnis["s"] = "ok" if all(p.get("s") == "ok" for p in gruppe) else gruppe[-1].get("s", "ok")
    return ergebnis


def rastern(punkte, minuten):
    if not punkte:
        return []
    if minuten <= 1:
        return punkte
    eimer = defaultdict(list)
    for p in punkte:
        zeit = datetime.strptime(p["t"], "%Y-%m-%d %H:%M:%S")
        schluessel = zeit.replace(minute=(zeit.minute // minuten) * minuten,
                                  second=0) if minuten < 60 else \
            zeit.replace(hour=(zeit.hour // (minuten // 60)) * (minuten // 60),
                         minute=0, second=0)
        eimer[schluessel].append(p)
    return [mitteln(eimer[k], k.strftime("%Y-%m-%d %H:%M:%S"))
            for k in sorted(eimer)]


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
    status = satz.get("status")
    if status == "funk":
        return "funkausfall"
    if status == "sensor":
        return "sensorfehler"
    grund = satz.get("grund") or ""
    for name, stichworte in GRUND_GRUPPEN:
        if any(w in grund for w in stichworte):
            return name
    return "sonstiges"


def tag_auswerten(tag):
    """Kennzahlen und Tagesmittel fuer einen Tag."""
    saetze = tag_laden(tag)
    if not saetze:
        return None
    punkte = [punkt(s) for s in saetze]
    gruende = defaultdict(int)
    luefter_min = 0
    for satz in saetze:
        if satz["luefter"] == "1":
            luefter_min += 1
        else:
            gruende[grund_gruppe(satz)] += 1

    mittel = mitteln(punkte, tag + " 12:00:00")
    eintrag = {
        "tag": tag,
        "minuten": len(saetze),
        "luefter_min": luefter_min,
        "gruende": dict(gruende),
    }
    for feld in FELDER:
        eintrag[feld] = mittel[feld]
    return eintrag


def statistik_fortschreiben(geaenderte_tage):
    """Tagesstatistik laden, geaenderte Tage neu rechnen, zurueckschreiben."""
    try:
        gespeichert = json.loads(STATISTIK_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        gespeichert = {}

    # Fehlt ein Tag noch ganz, wird er ebenfalls berechnet -- so faellt auch
    # ein nachtraeglich eingespielter Tag nicht durch.
    zu_rechnen = set(geaenderte_tage) | (set(vorhandene_tage()) - set(gespeichert))
    for tag in sorted(zu_rechnen):
        eintrag = tag_auswerten(tag)
        if eintrag:
            gespeichert[tag] = eintrag

    # Tage, deren Datei geloescht wurde, fliegen raus.
    for tag in list(gespeichert):
        if not (TAGE / f"{tag}.csv").exists():
            del gespeichert[tag]

    STATISTIK_JSON.write_text(
        json.dumps(gespeichert, ensure_ascii=False, separators=(",", ":"),
                   sort_keys=True),
        encoding="utf-8")
    return gespeichert


def punkte_laden(tage_zurueck):
    """Alle Messpunkte der letzten n Tage aus den Tagesdateien."""
    grenze = (jetzt_ortszeit() - timedelta(days=tage_zurueck)).strftime("%Y-%m-%d")
    punkte = []
    for tag in vorhandene_tage():
        if tag < grenze:
            continue
        punkte.extend(punkt(s) for s in tag_laden(tag))
    punkte.sort(key=lambda p: p["t"])
    return punkte


def tagesreihe(statistik, tage_zurueck=None):
    """Ein Punkt je Tag, aus der Tagesstatistik."""
    tage = sorted(statistik)
    if tage_zurueck is not None:
        grenze = (jetzt_ortszeit() - timedelta(days=tage_zurueck)).strftime("%Y-%m-%d")
        tage = [t for t in tage if t >= grenze]
    reihe = []
    for tag in tage:
        e = statistik[tag]
        p = {"t": tag + " 12:00:00",
             "l": round(e["luefter_min"] / max(1, e["minuten"]), 3),
             "s": "ok"}
        for feld in FELDER:
            p[feld] = e.get(feld)
        reihe.append(p)
    return reihe


def hauptprogramm():
    saetze, rohdateien = rohdaten_einlesen()
    print(f"{len(saetze)} Datensaetze aus {len(rohdateien)} Rohdateien gelesen.")

    geaendert = []
    if saetze:
        geaendert = tagesdateien_aktualisieren(saetze)
        print("Tagesdateien aktualisiert:", ", ".join(geaendert))

    for pfad in rohdateien:
        pfad.unlink()
    if ROH.exists():
        for ordner in sorted(ROH.glob("*"), reverse=True):
            if ordner.is_dir() and not any(ordner.iterdir()):
                ordner.rmdir()
    ROH.mkdir(exist_ok=True)
    (ROH / ".gitkeep").write_text("", encoding="utf-8")

    statistik = statistik_fortschreiben(geaendert)
    print(f"Tagesstatistik: {len(statistik)} Tage.")

    # Die feinen Zeitraeume kommen aus den Rohzeilen, die groben aus der
    # Tagesstatistik -- sonst waere daten.json nach einem Jahr unbrauchbar gross.
    monat_tage, monat_raster = ZEITRAEUME["monat"]
    alle = punkte_laden(monat_tage)

    grenze_tag = jetzt_ortszeit() - timedelta(hours=24)
    letzte_24h = [p for p in alle
                  if datetime.strptime(p["t"], "%Y-%m-%d %H:%M:%S") >= grenze_tag]
    grenze_woche = jetzt_ortszeit() - timedelta(days=7)
    letzte_woche = [p for p in alle
                    if datetime.strptime(p["t"], "%Y-%m-%d %H:%M:%S") >= grenze_woche]

    letzte_messung = None
    for p in reversed(alle):
        if p["ai"] is not None and p["aa"] is not None:
            letzte_messung = p
            break

    gesamt_minuten = sum(e["minuten"] for e in statistik.values())
    gesamt_luefter = sum(e["luefter_min"] for e in statistik.values())

    daten = {
        "erzeugt": jetzt_ortszeit().strftime("%Y-%m-%d %H:%M:%S"),
        "aktuell": alle[-1] if alle else None,
        "letzte_messung": letzte_messung,
        "zeitstufe": None,
        "grund": None,
        "tag": letzte_24h,
        "woche": rastern(letzte_woche, ZEITRAEUME["woche"][1]),
        "monat": rastern(alle, monat_raster),
        "jahr": tagesreihe(statistik, JAHR_TAGE),
        "gesamt": tagesreihe(statistik),
        "statistik": [statistik[t] for t in sorted(statistik)],
        "summe": {
            "tage": len(statistik),
            "minuten": gesamt_minuten,
            "luefter_min": gesamt_luefter,
            "erster_tag": min(statistik) if statistik else None,
        },
    }

    # Zeitstufe und Begruendung des juengsten Satzes
    if geaendert or vorhandene_tage():
        letzte_tage = vorhandene_tage()
        if letzte_tage:
            letzte_saetze = tag_laden(letzte_tage[-1])
            if letzte_saetze:
                daten["zeitstufe"] = letzte_saetze[-1]["zeitstufe"]
                daten["grund"] = letzte_saetze[-1].get("grund")

    ZIEL_JSON.write_text(
        json.dumps(daten, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    print(f"daten.json geschrieben: Tag {len(daten['tag'])}, "
          f"Woche {len(daten['woche'])}, Monat {len(daten['monat'])}, "
          f"Jahr {len(daten['jahr'])}, Gesamt {len(daten['gesamt'])} Punkte, "
          f"{ZIEL_JSON.stat().st_size // 1024} KB")


if __name__ == "__main__":
    hauptprogramm()
