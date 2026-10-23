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
        wann = datetime.strptime(satz["zeitstempel"], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

    # Datensaetze ohne gestellte Uhr aussortieren.
    #
    # Startet das Innengeraet neu, laeuft seine Uhr bis zur naechsten
    # Zeitnachricht vom Aussengeraet auf dem 1.1.2000. Solche Zeilen sind
    # messtechnisch in Ordnung, lassen sich aber nirgends einordnen: Sie
    # erzeugen einen Phantomtag im Jahr 2000, der im Zeitraum "Gesamt"
    # auftaucht, und sie ueberschreiben einander, weil mehrere Neustarts
    # dieselben Uhrzeiten vergeben.
    #
    # Hier und nicht erst beim Abholen am Laptop: Das Aussengeraet laedt
    # seinen Puffer auch selbst hoch, und dieser Weg filterte bisher nichts.
    # Dreimal ist dadurch derselbe Phantomtag entstanden.
    if wann.year < 2020:
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


def ist_messung(satz):
    """Echter Messdatensatz des Innengeraets -- kein Lebenszeichen.

    Das Aussengeraet legt alle paar Minuten eine Statuszeile mit status='lebt'
    ab, damit aus der Ferne erkennbar ist, dass es laeuft. Darin stehen keine
    Innenwerte, und in der Luefterspalte steht eine 0 -- das Aussengeraet weiss
    gar nicht, ob der Luefter laeuft.

    Werden diese Zeilen wie Messungen behandelt, ist die Folge sichtbar und
    falsch: Im Diagramm reisst jede davon das "Luefter laeuft"-Band auf, so
    dass ein durchlaufender Luefter wie wildes Takten aussieht, die
    Tageslaufzeit faellt zu niedrig aus, und unter "Warum nicht gelueftet
    wurde" erscheinen Statusmeldungen als Gruende.
    """
    return satz.get("status") != "lebt"


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


# Seit dem 22.10.2026 schickt das Innengeraet die Begruendung als EINEN
# Buchstaben statt als Satz. Der ausgeschriebene Text machte 41 Prozent der
# gespeicherten Daten aus und wiederholte dabei nur Zahlen, die ohnehin in
# eigenen Spalten stehen.
#
# Die alten Texte muessen weiter verstanden werden: In tage/ liegen Dateien
# aus der Zeit davor, und die sollen nicht ploetzlich als "sonstiges" gelten.
GRUND_CODES = {
    "T": ("lueftet", "Außenluft ist trockener"),
    "M": ("mindestlaufzeit", "Mindestlaufzeit läuft noch"),
    "F": ("frost", "Keller zu kalt"),
    "H": ("hitze", "draußen zu warm"),
    "K": ("kondensation", "Kondensationsgefahr"),
    "D": ("zu_geringe_differenz", "Unterschied zu klein"),
    "U": ("funkausfall", "kein Kontakt zum Außengerät"),
    "S": ("sensorfehler", "Messwerte unbrauchbar"),
}

# Alte Langtexte -> dieselben Gruppen.
GRUND_GRUPPEN = [
    ("frost", ("Frostgrenze",)),
    ("hitze", ("Hitzegrenze",)),
    ("kondensation", ("Kondensationsgefahr",)),
    ("mindestlaufzeit", ("Mindestlaufzeit",)),
    ("zu_geringe_differenz", ("Einschaltschwelle", "Unterschied")),
    ("hysterese_aus", ("Ausschaltschwelle",)),
    ("sensorfehler", ("unplausibel",)),
    ("funkausfall", ("Empfang", "Aussengeraet empfangen", "Kontakt")),
]


def grund_gruppe(satz):
    status = satz.get("status")
    if status == "funk":
        return "funkausfall"
    if status == "sensor":
        return "sensorfehler"
    grund = (satz.get("grund") or "").strip()
    if grund in GRUND_CODES:
        return GRUND_CODES[grund][0]
    for name, stichworte in GRUND_GRUPPEN:
        if any(w in grund for w in stichworte):
            return name
    return "sonstiges"


def grund_lesbar(satz):
    """Begruendung in Worten, fuer die Anzeige im Dashboard.

    Aus dem Kennbuchstaben und den Zahlen des Datensatzes wird wieder ein
    ganzer Satz -- dieselbe Auskunft wie frueher, nur nicht gespeichert.
    """
    grund = (satz.get("grund") or "").strip()
    if grund not in GRUND_CODES:
        return grund or None          # alter Langtext, unveraendert zeigen

    text = GRUND_CODES[grund][1]
    diff = zahl(satz.get("differenz"))
    if grund in ("T", "D") and diff is not None:
        return "%s (%.2f g/m³)" % (text, diff)
    if grund == "F":
        t = zahl(satz.get("t_innen"))
        return "%s (%.1f °C)" % (text, t) if t is not None else text
    if grund == "H":
        t = zahl(satz.get("t_aussen"))
        return "%s (%.1f °C)" % (text, t) if t is not None else text
    if grund == "K":
        ti, ta = zahl(satz.get("t_innen")), zahl(satz.get("t_aussen"))
        if ti is not None and ta is not None:
            return "%s (außen %.1f K wärmer)" % (text, ta - ti)
    if grund == "U":
        alter = zahl(satz.get("alter_s"))
        return "%s (seit %d s)" % (text, alter) if alter is not None else text
    return text


def tag_auswerten(tag):
    """Kennzahlen und Tagesmittel fuer einen Tag.

    Nur echte Messungen -- Lebenszeichen wuerden die Laufzeit druecken und
    unter den Abschaltgruenden auftauchen. Siehe ist_messung().
    """
    saetze = [s for s in tag_laden(tag) if ist_messung(s)]
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
        punkte.extend(punkt(s) for s in tag_laden(tag) if ist_messung(s))
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
    # Die Ordner muessen existieren, bevor irgendetwas anderes passiert.
    # Sonst scheitert spaeter "git add -A tage ... roh" in der Action mit
    # Exitcode 128, weil der Pfad nicht gefunden wird -- GitHub entfernt
    # leere Ordner, und nach dem Loeschen aller Messdaten sind sie leer.
    TAGE.mkdir(exist_ok=True)
    (TAGE / ".gitkeep").write_text("", encoding="utf-8")
    ROH.mkdir(exist_ok=True)
    (ROH / ".gitkeep").write_text("", encoding="utf-8")

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
        "letztes_lebenszeichen": None,
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

    # Zeitstufe und Begruendung des juengsten Satzes -- und getrennt davon das
    # juengste Lebenszeichen. Beides aus demselben Durchlauf: Das Lebenszeichen
    # zaehlt nicht als Messung, ist aber die einzige Auskunft darueber, ob das
    # Aussengeraet ueberhaupt noch laeuft.
    letzte_tage = vorhandene_tage()
    if letzte_tage:
        letzte_saetze = tag_laden(letzte_tage[-1])
        messungen = [s for s in letzte_saetze if ist_messung(s)]
        if messungen:
            daten["zeitstufe"] = messungen[-1]["zeitstufe"]
            daten["grund"] = grund_lesbar(messungen[-1])
        for satz in reversed(letzte_saetze):
            if not ist_messung(satz):
                daten["letztes_lebenszeichen"] = {
                    "t": satz["zeitstempel"],
                    "grund": satz.get("grund"),
                    "ta": zahl(satz["t_aussen"]),
                    "ra": zahl(satz["rh_aussen"]),
                }
                break

    ZIEL_JSON.write_text(
        json.dumps(daten, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    print(f"daten.json geschrieben: Tag {len(daten['tag'])}, "
          f"Woche {len(daten['woche'])}, Monat {len(daten['monat'])}, "
          f"Jahr {len(daten['jahr'])}, Gesamt {len(daten['gesamt'])} Punkte, "
          f"{ZIEL_JSON.stat().st_size // 1024} KB")


if __name__ == "__main__":
    hauptprogramm()
