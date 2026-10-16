#!/usr/bin/env python3
"""Erzeugt Beispiel-Rohpakete, um Verarbeitung und Dashboard zu pruefen.

Simuliert drei Tage im Minutentakt mit einem realistischen Tagesgang:
Der Keller bleibt kuehl und traege, draussen schwankt es deutlich. Die
Entscheidungslogik wird dabei exakt so nachgebildet wie auf dem ESP32.

    python werkzeuge/beispieldaten.py

Danach:

    python werkzeuge/verarbeiten.py
"""

import math
import random
from datetime import datetime, timedelta
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
ROH = WURZEL / "roh"

SCHWELLE_EIN = 2.0
SCHWELLE_AUS = 0.5
FROSTGRENZE = 3.0
HITZEGRENZE = 22.0
MAX_UEBER = 3.0
MINDESTLAUFZEIT_MIN = 5

KOPF = ("zeitstempel;zeitstufe;status;t_innen;rh_innen;ah_innen;"
        "t_aussen;rh_aussen;ah_aussen;differenz;alter_s;luefter;grund")


def absolute_feuchte(t, rh):
    es = 6.112 * math.exp(17.62 * t / (243.12 + t))
    return 216.7 * (rh / 100.0 * es) / (t + 273.15)


def entscheiden(ti, ri, ta, ra, an, laufzeit):
    ai, aa = absolute_feuchte(ti, ri), absolute_feuchte(ta, ra)
    diff = ai - aa
    if ta <= FROSTGRENZE:
        return False, f"Aussen {ta:.1f} degC <= Frostgrenze {FROSTGRENZE:.1f} degC", ai, aa, diff
    if ta >= HITZEGRENZE:
        return False, f"Aussen {ta:.1f} degC >= Hitzegrenze {HITZEGRENZE:.1f} degC", ai, aa, diff
    if ta - ti >= MAX_UEBER:
        return False, (f"Aussen {ta - ti:.1f} K waermer als innen "
                       f"(Grenze {MAX_UEBER:.1f} K) -- Kondensationsgefahr"), ai, aa, diff
    if an and laufzeit < MINDESTLAUFZEIT_MIN:
        return True, f"Mindestlaufzeit {laufzeit * 60} s von 300 s", ai, aa, diff
    if an:
        if diff < SCHWELLE_AUS:
            return False, f"Differenz {diff:.2f} g/m3 < Ausschaltschwelle {SCHWELLE_AUS:.2f}", ai, aa, diff
        return True, f"laeuft weiter, Differenz {diff:.2f} g/m3", ai, aa, diff
    if diff >= SCHWELLE_EIN:
        return True, f"Differenz {diff:.2f} g/m3 >= Einschaltschwelle {SCHWELLE_EIN:.2f}", ai, aa, diff
    return False, f"Differenz {diff:.2f} g/m3 < Einschaltschwelle {SCHWELLE_EIN:.2f}", ai, aa, diff


def erzeugen(tage=3):
    random.seed(20261016)
    jetzt = datetime.now().replace(second=0, microsecond=0)
    start = jetzt - timedelta(days=tage)

    an, laufzeit = False, 0
    zeilen = []
    t = start
    while t <= jetzt:
        stunde = t.hour + t.minute / 60.0
        tagesanteil = (t - start).total_seconds() / (tage * 86400)

        # Keller: traege, kuehl, leicht steigende Feuchte
        ti = 12.5 + 0.6 * math.sin(2 * math.pi * (tagesanteil)) + random.gauss(0, 0.03)
        ri = 74 + 4 * math.sin(2 * math.pi * tagesanteil * 1.3) + random.gauss(0, 0.2)

        # Draussen: deutlicher Tagesgang, nachts kalt und feucht
        ta = 9 + 8 * math.sin(math.pi * (stunde - 6) / 12) + random.gauss(0, 0.25)
        ra = 88 - 32 * max(0.0, math.sin(math.pi * (stunde - 6) / 12)) + random.gauss(0, 1.2)
        ra = min(99.0, max(28.0, ra))

        an, grund, ai, aa, diff = entscheiden(ti, ri, ta, ra, an, laufzeit)
        laufzeit = laufzeit + 1 if an else 0

        zeilen.append(";".join([
            t.strftime("%Y-%m-%d %H:%M:%S"), "3", "ok",
            f"{ti:.1f}", f"{ri:.1f}", f"{ai:.2f}",
            f"{ta:.1f}", f"{ra:.1f}", f"{aa:.2f}",
            f"{diff:.2f}", "3", "1" if an else "0", grund,
        ]))
        t += timedelta(minutes=1)

    # In Paketen zu je 15 Minuten ablegen, wie es das Aussengeraet tut
    ROH.mkdir(exist_ok=True)
    pakete = 0
    for i in range(0, len(zeilen), 15):
        stueck = zeilen[i:i + 15]
        zeit = datetime.strptime(stueck[-1].split(";")[0], "%Y-%m-%d %H:%M:%S")
        ordner = ROH / zeit.strftime("%Y-%m-%d")
        ordner.mkdir(exist_ok=True)
        (ordner / zeit.strftime("%H%M%S.csv")).write_text(
            KOPF + "\n" + "\n".join(stueck) + "\n", encoding="utf-8")
        pakete += 1

    lief = sum(1 for z in zeilen if z.split(";")[11] == "1")
    print(f"{len(zeilen)} Messungen in {pakete} Paketen erzeugt.")
    print(f"Luefter lief {lief} Minuten ({100 * lief / len(zeilen):.1f} %).")


if __name__ == "__main__":
    erzeugen()
