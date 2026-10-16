# Kellerlüftung — Datenablage und Dashboard

Dieses Repository nimmt die Messdaten der Kellerlüftungssteuerung auf und zeigt
sie als Dashboard. Es wird vom **Außengerät** befüllt, das per WLAN alle
15 Minuten ein Datenpaket hochlädt.

```
Außensensor ──funk──▶ Innensensor ──entscheidet──▶ Lüfter
     │                      │
     │                      └──funk──▶ Außengerät ──WLAN──▶ dieses Repository
     └── misst                                                     │
                                                          GitHub Action
                                                                   │
                                                              Dashboard
```

## Inhalt

| Ordner / Datei | Zweck |
|---|---|
| `roh/` | Rohpakete, wie das Außengerät sie ablegt. Werden nach der Verarbeitung gelöscht. |
| `tage/` | Eine CSV je Tag, dauerhaft. Das ist die Langzeitablage. |
| `daten.json` | Aufbereitete Daten für das Dashboard. Wird automatisch erzeugt. |
| `index.html` | Das Dashboard. |
| `werkzeuge/verarbeiten.py` | Fügt Rohpakete zu Tagesdateien zusammen und baut `daten.json`. |
| `werkzeuge/beispieldaten.py` | Erzeugt Testdaten, um das Dashboard ohne Hardware zu prüfen. |
| `.github/workflows/daten.yml` | Führt die Verarbeitung aus und veröffentlicht die Seite. |

## Einrichtung

### 1. Repository anlegen

Ein neues Repository auf GitHub erstellen und den Inhalt dieses Ordners
hineinlegen. Der Branch muss `main` heißen (oder `GITHUB_BRANCH` im Gerät
entsprechend anpassen).

### 2. GitHub Pages einschalten

**Settings → Pages → Build and deployment → Source: GitHub Actions.**

Danach ist das Dashboard erreichbar unter
`https://<benutzername>.github.io/<repository>/`.

> Bei einem **privaten** Repository ist GitHub Pages nur in bezahlten Tarifen
> verfügbar. Ein öffentliches Repository bedeutet umgekehrt, dass die Messwerte
> für jeden im Netz sichtbar sind — bei Kellerfeuchte normalerweise unkritisch,
> aber es ist eine bewusste Entscheidung.

### 3. Zugriffstoken erzeugen

**Settings → Developer settings → Personal access tokens → Fine-grained tokens.**

- *Repository access*: nur dieses eine Repository
- *Permissions → Repository permissions → Contents*: **Read and write**
- sonst nichts

Das Token beginnt mit `github_pat_`. Es kommt in `aussen/config.py` auf dem
Gerät — **niemals in dieses Repository.** Die `.gitignore` verhindert das
versehentliche Einchecken.

### 4. Gerät konfigurieren

In `aussen/config.py`:

```python
WLAN_SSID    = "Mein-WLAN"
WLAN_PASSWORT = "..."
GITHUB_REPO  = "benutzername/repositoryname"
GITHUB_TOKEN = "github_pat_..."
```

Dann aufspielen:

```bash
.\aufspielen.ps1 -Rolle aussen -Port COM5
```

## Ohne Hardware ausprobieren

```bash
python werkzeuge/beispieldaten.py
python werkzeuge/verarbeiten.py
python -m http.server 8777
```

Dann `http://localhost:8777/` öffnen. Die Beispieldaten bilden drei Tage mit
realistischem Tagesgang ab und durchlaufen dieselbe Entscheidungslogik wie das
Gerät.

## Was das Dashboard zeigt

**Statuszeile** — läuft der Lüfter gerade, und wenn nicht, warum. Meldet
außerdem, wenn die Daten veraltet sind oder die Funkverbindung abgerissen ist.

**Vier Kacheln** — absolute Feuchte innen und außen, der Trocknungsgewinn
(die Differenz, nach der entschieden wird) und die Lüfterlaufzeit des Tages.

**Absolute Luftfeuchtigkeit** — das entscheidende Diagramm. Hinterlegte Flächen
zeigen, wann der Lüfter lief.

**Temperatur** — zur Beurteilung von Frost-, Hitze- und Kondensationsgrenze.

**Relative Luftfeuchtigkeit** — bewusst mit dabei, obwohl *nicht* danach
entschieden wird: Hier sieht man, wie irreführend sie ist. Kalte Außenluft mit
90 % relativer Feuchte ist oft deutlich trockener als Kellerluft mit 55 %.

**Lüfterlaufzeit je Tag** — wie viele Stunden tatsächlich gelüftet wurde.

**Warum nicht gelüftet wurde** — Minuten je Tag, aufgeschlüsselt nach der
Regel, die das Lüften verhindert hat. Das ist das Werkzeug zum Nachjustieren:
Wenn über Wochen etwa die Hitzegrenze den größten Anteil hat, war 22 °C zu
streng gewählt.

**Messwerte** — die jüngsten Messungen im Klartext, dazu die Tagesdateien zum
Herunterladen.

## Datenformat

`tage/JJJJ-MM-TT.csv`, Semikolon getrennt:

```
zeitstempel;zeitstufe;status;t_innen;rh_innen;ah_innen;t_aussen;rh_aussen;ah_aussen;differenz;alter_s;luefter;grund
```

- `zeitstufe` — 3 = Funk/Internet, 2 = am Rechner gestellt, 1 = nach Stromausfall fortgesetzt, 0 = unbekannt
- `status` — `ok`, `funk` (Verbindungsfehler), `sensor` (Messfehler), `start` (Neustart)
- `alter_s` — Sekunden seit dem letzten empfangenen Funkpaket
- `luefter` — 1 = ein, 0 = aus
- `grund` — Klartext der Entscheidung

## Warum Pakete statt einzelner Messungen

Jeder Upload ist ein Commit. Eine Messung pro Minute wären 1440 Commits am Tag
— die Historie würde unbrauchbar und GitHub würde drosseln. Deshalb sammelt das
Außengerät 15 Minuten lang und lädt dann ein Paket hoch: 96 Commits am Tag.

Die Action fügt die Pakete sofort zu Tagesdateien zusammen und löscht die
Rohdaten wieder, damit `roh/` nicht zuwächst.
