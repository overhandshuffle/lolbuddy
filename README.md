# lolbuddy

Eine lokale Website für deinen League-Draft. Während der Champion-Auswahl zeigt
sie deinen Pick, beide Teams nach Rollen gegenübergestellt, Bans, Hauptrune und
Summoner Spells. Sobald das Spiel startet, erscheint der vollständige OP.GG-Build
mit Bildern für Items, Hauptrune und Summoner Spells.

## Starten

Python 3.10 oder neuer:

```powershell
python -m pip install -r requirements.txt
python app.py
```

Der Browser öffnet **http://127.0.0.1:5000**. Gleichzeitig ist lolbuddy im
lokalen Netzwerk für Handy und Tablet erreichbar. League kannst du vorher oder
danach starten. Nur `app.py` muss laufen: Es verwendet `lolclient.py` und
`opgg.py` direkt. Bei einem Start über Python erscheint das lolbuddy-Symbol im
Windows-Infobereich; über dessen Menü lässt sich die Anwendung beenden.

Falls Port 5000 schon belegt ist:

```powershell
python app.py --port 5050
```

## Windows-EXE bauen

Auf Windows erzeugt das Build-Skript standardmäßig eine einzelne ausführbare
Datei. Python muss dafür installiert sein; League-Spieler benötigen Python später
nicht mehr zum Starten der fertigen EXE.

```powershell
powershell -ExecutionPolicy Bypass -File .\build-exe.ps1
```

Das Ergebnis liegt unter `dist\lolbuddy.exe`. Das Skript verwendet eine eigene
`.build-venv`, installiert die benötigten Pakete, führt die Tests aus und bündelt
`templates` und `static`. Folgende Varianten sind möglich:

```powershell
# Schnellerer Start und meist weniger Fehlalarme durch Virenscanner, aber ein Ordner statt einer Datei
.\build-exe.ps1 -OneDir

# Tests bei einem wiederholten lokalen Build auslassen
.\build-exe.ps1 -SkipTests

# Diagnose-Build mit zusätzlichem Konsolenfenster
.\build-exe.ps1 -Console
```

Beim ersten Start kann die Windows-Firewall nach Netzwerkzugriff fragen. Für die
Nutzung nur auf diesem PC genügt privater bzw. lokaler Zugriff; für den Zugriff
vom Handy muss die EXE im privaten Heimnetz zugelassen sein. Die EXE läuft ohne
Konsolenfenster im Windows-Infobereich. Ein Doppelklick auf das Tray-Symbol öffnet
lolbuddy; das Kontextmenü bietet außerdem **QR-Code anzeigen**,
**Logs / Fehler anzeigen** und **Beenden**. Nicht behebbare Startfehler erscheinen
als Windows-Dialog. Die rotierenden Logs liegen unter
`%LOCALAPPDATA%\lolbuddy\logs\lolbuddy.log`. Die ID der von lolbuddy verwalteten
Runenseite liegt dauerhaft unter `%LOCALAPPDATA%\lolbuddy\rune-page.json` und wird nicht in die EXE gepackt.
Eine vorhandene ID aus der Python-Version übernimmt das Build-Skript einmalig.
Wird die EXE ein zweites Mal geöffnet, erkennt sie die laufende Instanz, öffnet
nur deren Browseroberfläche und beendet den zweiten Prozess wieder.

Beim Start wird auch die WLAN-Adresse ausgegeben, beispielsweise
`http://192.168.178.31:5000`. Am PC zeigt **Am Handy öffnen** einen lokal erzeugten
QR-Code für diese Adresse. Das Handy muss sich im selben WLAN befinden; `--lan`
ist nicht mehr nötig.

## Benutzung

- Vor der Champion-Auswahl zeigt lolbuddy den ausgewählten Spielmodus und alle
  beigetretenen Gruppenmitglieder. Während der Spielsuche erscheinen die bisherige
  Suchzeit und, sofern vom Client geliefert, die geschätzte Wartezeit.
- Wenn noch keine Lobby besteht, kannst du einen verfügbaren Modus auswählen und
  die Lobby direkt auf der Website erstellen.
- Als Gruppenleiter kannst du in dieser Ansicht einen aktuell verfügbaren Spielmodus
  wählen und die Spielsuche starten oder abbrechen. In Modi mit Positionswahl lassen
  sich die primäre und sekundäre Position direkt dort setzen.
- Sobald ein Match gefunden wurde, erscheint auf der Website eine eigene
  **Spiel gefunden**-Ansicht. **Match annehmen** bestätigt den Ready Check im
  League-Client; anschließend öffnet sich automatisch wieder die Draft-Ansicht.
- In der Champion-Auswahl stehen dein eigener Pick und dessen Hauptrune oben.
  Darunter werden Top, Jungle, Mid, ADC und Support beider Teams direkt
  gegenübergestellt. Die übrigen Champions sind reine Anzeige und nicht anklickbar.
- Neben der empfohlenen Hauptrune übernimmt **Runen einspielen** die vollständige
  Runenseite in den League-Client. Zwei Summoner Spells lassen sich ebenfalls direkt
  auswählen und gemeinsam in den Client übertragen.
- **Itemsets laden** erstellt aus demselben OP.GG-Build ein Gegenstandsset für den
  aktuellen Champion. Vorhandene championbezogene Sets werden ersetzt; globale Sets
  und Zuordnungen für andere Champions bleiben erhalten.
- Der vollständige Build wird erst nach dem Spielstart angezeigt. **Build anpassen**
  öffnet dann die Auswahl für Rolle und Tier; weitere Statistiken und situative
  Items lassen sich bei Bedarf aufklappen.
- **Rolle**: Standardmäßig die vom Client zugewiesene Rolle. Ohne Rollendaten
  verwendet OP.GG die meistgespielte Rolle. Du kannst sie manuell ändern. Falls
  OP.GG für eine Champion-/Rollen-Kombination keine Daten veröffentlicht, zeigt
  lolbuddy das ausdrücklich an und ersetzt die Rolle nicht durch eine andere.
- **Tier**: Die Rangstufe der OP.GG-Daten lässt sich unter **Build anpassen**
  auswählen. Die Auswahl bleibt beim Wechsel des Champions erhalten.
- **Modus**: lolbuddy erkennt ARAM über den League-Client. In ARAM verwendet es
  automatisch OP.GGs globale ARAM-Builds; Rolle und Rang-Tier sind dort deaktiviert.
- Während der Champion-Auswahl öffnet **Champion wählen** eine durchsuchbare Liste
  deiner im aktuellen Draft spielbaren Champions. Ein Klick setzt sofort den Hover
  im Client und schließt die Liste. Anschließend loggt **Fest wählen** neben
  **Champion ändern** den Champion während deines Pick-Zugs verbindlich ein.
- Alternative Core-Builds und Spells lassen sich nach dem Spielstart aufklappen.
- Auf dem Handy bleibt die Pick-Ansicht einspaltig und die Rollenpaarungen kompakt.
  Die Champion-Liste scrollt innerhalb der Auswahl; Suche und **Fest wählen**
  bleiben erreichbar.
- **Runen einspielen** erstellt beim ersten Mal eine Seite wie
  `Ahri Mid` oder `Ahri ARAM` und aktiviert sie. Weitere Importe aktualisieren
  über die gespeicherte Seiten-ID nur diese verwaltete Seite; eigene Seiten
  bleiben unverändert.
- Dein letzter Draft und Build bleiben im Spiel sichtbar. Nach einem Neustart
  der Website mitten im Spiel ist ein vorheriger Draft nicht mehr vorhanden.

Die Oberfläche verbindet sich nach Client- oder Server-Unterbrechungen erneut.
Die Champion-Auswahl wird alle 250 ms gelesen; Änderungen werden per
Server-Sent Events direkt an den Browser gesendet. OP.GG-Abfragen laufen separat
und werden pro Champion, Rolle, Tier und Modus für 15 Minuten zwischengespeichert. Fehler werden
für zehn Sekunden zwischengespeichert. Schnelle Championwechsel können keinen
alten Build über den aktuellen schreiben.

Weitere Optionen:

```powershell
python app.py --region euw --tier emerald_plus --no-browser
```

## Daten und Grenzen

„Empfohlen“ bedeutet den zuerst von OP.GG gelisteten populären Build für die
gewählte Rolle und das gewählte Tier, standardmäßig EUW / Emerald+. Angezeigt werden dessen tatsächliche
Pick- und Winraten, keine errechnete Garantie für den besten Build in jedem Match.
Spätere Items sind situative Alternativen; die ersten drei Core-Items werden in
Kaufreihenfolge angezeigt. Für ARAM werden stattdessen automatisch OP.GGs globale
ARAM-Daten mit den dortigen Items, Runen und Summoner Spells geladen.

OP.GG wird aus den öffentlich ausgelieferten Seitendaten gelesen. Änderungen an
deren Seitenformat können eine Anpassung des Parsers nötig machen. Bilder werden
vom OP.GG-CDN geladen. Item- und Runennamen entsprechen der englischen Datenquelle.

Die Website ist auf dem PC und im lokalen WLAN erreichbar und liest die lokale
League-API. Zugangsdaten bleiben im Python-Prozess. Schreibende Aktionen werden
nur durch die entsprechenden Buttons ausgelöst. Die Flask-Instanz ist für den
lokalen Betrieb in einem vertrauenswürdigen Netzwerk gedacht.

## Prüfen

```powershell
python -m unittest discover -s tests -v
```

Die Tests prüfen unter anderem Hover und Champion-Tausch, Wiederverbindung,
OP.GG-Tabellenreferenzen, Item-Mengen, Spells, Rune-Auswahl, Cache und Live-Events.
Sie benötigen keinen League-Client und keine Internetverbindung.

Datenquellen: [OP.GG](https://op.gg/lol/champions) und die
[lokale League Client API](https://developer.riotgames.com/docs/lol#league-client-api).

lolbuddy is not endorsed by Riot Games and does not reflect the views or opinions
of Riot Games or anyone officially involved in producing or managing Riot Games
properties. Riot Games and all associated properties are trademarks or registered
trademarks of Riot Games, Inc.
