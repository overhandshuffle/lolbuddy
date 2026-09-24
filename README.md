# lolbuddy

Control League of Legends from your phone while you are away from your PC. Queue
up, accept a match, pick and lock your champion, import recommended runes, and set
summoner spells through a mobile-friendly local website. Once the match starts,
lolbuddy switches to a compact item-only build view.

lolbuddy runs on your Windows PC and communicates with the locally running League
Client. Your phone only needs a browser and access to the same Wi-Fi network.

## Use the Windows executable

The recommended way to run lolbuddy is `lolbuddy.exe`. Download it from the
repository's **Releases** page when a prebuilt release is available, or build it
yourself using the instructions below.

1. Start the League Client and sign in.
2. Run `lolbuddy.exe`.
3. Allow private-network access if Windows Firewall asks.
4. The dashboard and phone QR code open automatically in your browser.
5. Scan the QR code with a phone connected to the same Wi-Fi network.

Python is not required to use the finished executable. The executable is not
code-signed, so Windows or antivirus software may show a warning for a newly built
version.

## System tray

lolbuddy continues running in the Windows system tray when the browser is closed.
Use the tray icon to:

- open the dashboard;
- show the phone QR code;
- open logs for troubleshooting;
- exit lolbuddy completely.

Starting the executable a second time does not create another server. It opens the
dashboard of the already running instance instead.

## Build the executable yourself

Requirements: Windows, PowerShell, and Python 3.10 or newer.

```powershell
git clone https://github.com/overhandshuffle/lolbuddy.git
cd lolbuddy
powershell -ExecutionPolicy Bypass -File .\build-exe.ps1
```

The script creates an isolated `.build-venv`, installs all dependencies, runs the
tests, and packages the application with PyInstaller. The result is:

```text
dist\lolbuddy.exe
```

`dist` is intentionally excluded from Git. To offer the executable as a download,
create a GitHub Release and attach `dist\lolbuddy.exe` to it.

Optional build modes:

```powershell
.\build-exe.ps1 -OneDir     # Folder-based build with faster startup
.\build-exe.ps1 -SkipTests  # Skip tests during repeated local builds
.\build-exe.ps1 -Console    # Keep a console window for diagnostics
```

## Run from source

```powershell
python -m pip install -r requirements.txt
python app.py
```

Useful options:

```powershell
python app.py --port 5050 --region euw --tier emerald_plus --no-browser
```

## Project structure

```text
lolbuddy/
  app.py            Application, web server, and League monitor
  lolclient.py      Local League Client integration
  opgg.py           OP.GG build data parser
  runeclient.py     Managed rune-page import
  static/           Browser JavaScript, CSS, and icon
  templates/        Web interface
tests/              Automated backend and parser tests
app.py              Source launcher
build-exe.ps1       Windows executable build script
```

Run the test suite with:

```powershell
python -m unittest discover -s tests -v
```

## Local data and privacy

The dashboard is intended for a trusted private network. League credentials stay
inside the local League Client connection and are never sent to the phone. Build
data and images come from [OP.GG](https://op.gg/lol/champions).

Logs and the managed rune-page ID are stored under `%LOCALAPPDATA%\lolbuddy`.

lolbuddy is not endorsed by Riot Games and does not reflect the views or opinions
of Riot Games or anyone officially involved in producing or managing Riot Games
properties. Riot Games and all associated properties are trademarks or registered
trademarks of Riot Games, Inc.
