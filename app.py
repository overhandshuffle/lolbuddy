"""Lokales, live aktualisiertes League-Dashboard. Start: python app.py"""

from __future__ import annotations

import argparse
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from io import BytesIO
import json
import logging
import socket
import threading
import time
import webbrowser

from flask import Flask, Response, jsonify, render_template, request
import requests
import qrcode
from qrcode.image.svg import SvgPathImage

import lolclient
import opgg
import runeclient

log = logging.getLogger("lolbuddy")
POSITIONS = {"TOP": "top", "JUNGLE": "jungle", "MID": "mid", "BOT": "adc", "SUPPORT": "support"}
LCU_POSITIONS = {"top": "TOP", "jungle": "JUNGLE", "mid": "MIDDLE", "adc": "BOTTOM",
                 "support": "UTILITY", "fill": "FILL"}
KEEP_DRAFT_PHASES = {"GameStart", "InProgress", "Reconnect", "WaitingForStats", "PreEndOfGame", "EndOfGame"}
LOBBY_ACTION_ERRORS = (
    lolclient.LobbyActionError, FileNotFoundError, OSError, RuntimeError,
    requests.RequestException,
)


def empty_state():
    return {"connected": False, "phase": "Offline", "own_team": [], "enemy_team": [],
            "bans": {"own": [], "enemy": []}, "timer": None, "queue": "", "game_mode": "",
            "queue_description": "", "party": [],
            "queue_id": 0, "available_queues": [], "can_manage_lobby": False,
            "show_position_selector": False, "local_positions": {"first": "", "second": ""},
            "available_champions": [], "pickable_champion_ids": [], "pick_action": None,
            "matchmaking": {"active": False, "elapsed_seconds": 0, "estimated_seconds": 0},
            "logged_in": False}


class LiveState:
    def __init__(self):
        self.condition = threading.Condition()
        self.value = empty_state()
        self.revision = 0

    def publish(self, value):
        with self.condition:
            if value != self.value:
                self.value = value
                self.revision += 1
                self.condition.notify_all()

    def read(self):
        with self.condition:
            return self.value

    def events(self):
        revision = -1
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.revision != revision, timeout=15)
                changed = revision != self.revision
                revision, value = self.revision, self.value
            if changed:
                yield f"id: {revision}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n"
            else:
                yield ": heartbeat\n\n"


def champion_data(champion_id, champions):
    info = champions.get(champion_id, {})
    alias = info.get("alias", "")
    return {"champion_id": champion_id, "name": info.get("name", f"Champion {champion_id}" if champion_id else "Noch offen"),
            "alias": alias, "image_url": f"https://opgg-static.akamaized.net/meta/images/lol/latest/champion/{alias}.png" if alias else ""}


def draft_state(selection, champions):
    actions = lolclient.get_pick_actions(selection)
    teams = {}
    for source, target in (("myTeam", "own_team"), ("theirTeam", "enemy_team")):
        teams[target] = [
            {**champion_data(champ, champions), "cell_id": cell, "status": status,
             "position": POSITIONS.get(position, ""), "is_local": local}
            for cell, champ, status, position, local in lolclient.get_team_state(selection, source, actions)
        ]
    bans = selection.get("bans") or {}
    teams["bans"] = {target: [champion_data(int(c), champions) for c in bans.get(source, []) if c and int(c) > 0]
                     for source, target in (("myTeamBans", "own"), ("theirTeamBans", "enemy"))}
    timer = selection.get("timer") or {}
    teams["timer"] = {"phase": timer.get("phase", ""), "seconds": max(0, int(timer.get("adjustedTimeLeftInPhase", 0) / 1000))}
    return teams


def lobby_state(lobby, champions, member_names=None):
    config = lobby.get("gameConfig") or {}
    local = lobby.get("localMember") or {}
    member_names = member_names or {}

    def is_local(member):
        return bool(local.get("puuid") and member.get("puuid") == local["puuid"]
                    or local.get("summonerId") and member.get("summonerId") == local["summonerId"])

    def team(members):
        result = []
        for index, member in enumerate(members):
            champion = int(member.get("botChampionId", 0) or 0)
            position = str(member.get("botPosition") if champion else member.get("firstPositionPreference") or "").lower()
            player = {**champion_data(champion, champions), "cell_id": index, "status": "BOT" if champion else "LOBBY",
                      "position": POSITIONS.get(lolclient.POSITION_NAMES.get(position), ""), "is_local": is_local(member)}
            if not champion:
                player["name"] = "Dein Platz" if player["is_local"] else "Mitspieler"
            result.append(player)
        return result

    first, second = config.get("customTeam100", []), config.get("customTeam200", [])
    if first or second:
        if any(is_local(member) for member in second):
            first, second = second, first
    else:
        first = lobby.get("members", [])
    party = []
    for index, member in enumerate(lobby.get("members", [])):
        summoner_id = str(member.get("summonerId") or "")
        direct_name = member.get("gameName") or member.get("summonerName") or member.get("displayName")
        party.append({
            "name": direct_name or member_names.get(summoner_id) or ("Du" if is_local(member) else "Mitspieler"),
            "is_local": is_local(member),
            "is_leader": bool(member.get("isLeader")),
            "position": POSITIONS.get(lolclient.POSITION_NAMES.get(
                str(member.get("firstPositionPreference") or "").lower()
            ), ""),
            "index": index,
        })
    first_position = str(local.get("firstPositionPreference") or "").lower()
    second_position = str(local.get("secondPositionPreference") or "").lower()
    return {"own_team": team(first), "enemy_team": team(second), "party": party,
            "game_mode": config.get("gameMode", ""),
            "queue": "Freies Spiel" if config.get("isCustom") else "Lobby",
            "queue_id": int(config.get("queueId", 0) or 0),
            "can_manage_lobby": bool(local.get("isLeader")),
            "show_position_selector": bool(config.get("showPositionSelector")),
            "local_positions": {
                "first": POSITIONS.get(lolclient.POSITION_NAMES.get(first_position), "fill" if first_position == "fill" else ""),
                "second": POSITIONS.get(lolclient.POSITION_NAMES.get(second_position), "fill" if second_position == "fill" else ""),
            }}


class LcuMonitor:
    def __init__(self, state):
        self.state = state
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name="league-monitor", daemon=True)

    def start(self):
        self.thread.start()

    def run(self):
        session = None
        champions, playable_champions = {}, []
        previous_phase = None
        queue, game_mode = "", ""
        queue_description = ""
        queue_cache, member_names, available_queues = {}, {}, []
        pickable_ids, last_pickable_check = None, 0.0
        while not self.stop.is_set():
            delay = lolclient.POLL_INTERVAL
            try:
                if session is None:
                    session, base_url = lolclient.connect_to_lcu()
                    champions = lolclient.load_champion_details(session, base_url)
                    playable_champions = lolclient.get_playable_champions(session, base_url)
                    queue_cache, member_names = {}, {}
                    available_queues = lolclient.get_available_queues(session, base_url)
                    previous_phase = None
                phase = lolclient.get_phase(session, base_url)
                if phase != previous_phase:
                    response = session.get(f"{base_url}/lol-gameflow/v1/session", timeout=2)
                    if response.ok:
                        flow = response.json()
                        queue_info = flow.get("gameData", {}).get("queue") or {}
                        queue = queue_info.get("name") or "League of Legends"
                        game_mode = queue_info.get("gameMode") or ""
                    else:
                        queue, game_mode = "", ""
                    previous_phase = phase
                logged_in = phase != "None" or lolclient.is_logged_in(session, base_url)
                value = {**empty_state(), "connected": True, "logged_in": logged_in,
                         "phase": phase, "queue": queue, "queue_description": queue_description,
                         "game_mode": game_mode, "available_queues": available_queues,
                         "available_champions": playable_champions}
                if phase == "ChampSelect":
                    selection = lolclient.get_champ_select_session(session, base_url)
                    if selection is not None:
                        value.update(draft_state(selection, champions))
                        action = lolclient.get_local_pick_action(selection)
                        value["pick_action"] = ({
                            "available": True,
                            "active": bool(action.get("isInProgress")),
                            "champion_id": int(action.get("championId", 0) or 0),
                        } if action else None)
                        now = time.monotonic()
                        if now - last_pickable_check >= .75:
                            pickable_ids = lolclient.get_pickable_champion_ids(session, base_url)
                            last_pickable_check = now
                        value["pickable_champion_ids"] = sorted(pickable_ids or [])
                    else:
                        # Der Phasenwechsel und die Session erscheinen nicht atomar.
                        self.stop.wait(delay)
                        continue
                elif phase in {"Lobby", "Matchmaking", "ReadyCheck"}:
                    pickable_ids, last_pickable_check = None, 0.0
                    response = session.get(f"{base_url}/lol-lobby/v2/lobby", timeout=2)
                    if response.ok:
                        lobby = response.json()
                        config = lobby.get("gameConfig") or {}
                        queue_id = int(config.get("queueId", 0) or 0)
                        if queue_id and queue_id not in queue_cache:
                            queue_cache[queue_id] = lolclient.get_queue_details(session, base_url, queue_id)
                        details = queue_cache.get(queue_id, {})
                        if details:
                            queue = details.get("name") or queue
                            queue_description = details.get("description") or ""
                            game_mode = details.get("game_mode") or config.get("gameMode") or game_mode
                        for member in lobby.get("members", []):
                            summoner_id = str(member.get("summonerId") or "")
                            if summoner_id and summoner_id not in member_names:
                                member_names[summoner_id] = lolclient.get_summoner_display_name(
                                    session, base_url, summoner_id
                                )
                        value.update(lobby_state(lobby, champions, member_names))
                        value["queue"] = queue or value["queue"]
                        value["queue_description"] = queue_description
                        value["game_mode"] = game_mode or value["game_mode"]
                    if phase == "Matchmaking":
                        value["matchmaking"] = lolclient.get_matchmaking_search(session, base_url)
                    delay = 1
                elif phase in KEEP_DRAFT_PHASES:
                    previous = self.state.read()
                    for key in ("own_team", "enemy_team", "bans"):
                        value[key] = previous[key]
                    delay = 1
                else:
                    delay = 1
                self.state.publish(value)
            except (OSError, RuntimeError, requests.RequestException, ValueError, KeyError, TypeError):
                if session is not None:
                    session.close()
                session = None
                self.state.publish(empty_state())
                delay = lolclient.START_RETRY_INTERVAL
            self.stop.wait(delay)
        if session is not None:
            session.close()


class BuildCache:
    """Parallele OP.GG-Abfragen; gleiche Anfragen teilen sich ein Ergebnis."""

    def __init__(self, region="euw", tier="emerald_plus"):
        self.region, self.tier = region, tier
        self.lock = threading.Lock()
        self.entries = OrderedDict()
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="opgg")

    def get(self, champion, position, *, tier=None, mode="classic"):
        requested_tier = tier or self.tier
        key = (opgg._slugify_champion(champion), position, requested_tier, mode)
        with self.lock:
            now = time.monotonic()
            entry = self.entries.get(key)
            if entry:
                created, future = entry
                ttl = 10 if future.done() and future.exception() else 900
                if future.done() and now - created > ttl:
                    entry = None
            if entry is None:
                if len(self.entries) >= 128:
                    removable = next((k for k, (_, f) in self.entries.items() if f.done()), None)
                    if removable is None:
                        raise opgg.OpggError("Es werden gerade viele Builds geladen. Bitte kurz warten.")
                    self.entries.pop(removable)
                future = self.pool.submit(
                    opgg.get_champion_info,
                    champion,
                    position=position,
                    region=self.region,
                    tier=requested_tier,
                    mode=mode,
                )
                self.entries[key] = (now, future)
            self.entries.move_to_end(key)
        return future.result(timeout=25)

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


def build_parameters(data, default_tier):
    """Validate the fields shared by build lookup and rune import requests."""
    champion = str(data.get("champion", "")).strip()
    position = data.get("position") or None
    tier = data.get("tier") or default_tier
    mode = data.get("mode") or "classic"
    if mode == "aram":
        position = None
    if (
        not champion
        or len(champion) > 60
        or position is not None
        and (not isinstance(position, str) or position not in opgg.VALID_POSITIONS)
        or not isinstance(tier, str)
        or tier not in opgg.VALID_TIERS
        or not isinstance(mode, str)
        or mode not in opgg.VALID_MODES
    ):
        return None
    return champion, position, tier, mode


def create_app(state=None, builds=None, *, lan_url=""):
    app = Flask(__name__)
    state = state or LiveState()
    builds = builds or BuildCache()
    app.config.update(MAX_CONTENT_LENGTH=1024, LAN_URL=lan_url)
    app.extensions.update(live_state=state, build_cache=builds)
    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' https://opgg-static.akamaized.net; "
            "style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            region=builds.region.upper(),
            default_tier=builds.tier,
            tier=builds.tier.replace("_plus", "+").replace("_", " ").title(),
            lan_url=lan_url,
        )

    @app.get("/api/lan-qr.svg")
    def lan_qr():
        if not lan_url:
            return jsonify(error="Der LAN-Modus ist nicht aktiv."), 404
        qr = qrcode.make(lan_url, image_factory=SvgPathImage, box_size=9, border=3)
        output = BytesIO()
        qr.save(output)
        return Response(output.getvalue(), mimetype="image/svg+xml")

    @app.get("/api/state")
    def get_state():
        return jsonify(state.read())

    @app.get("/api/events")
    def events():
        return Response(state.events(), mimetype="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.post("/api/ready-check/accept")
    def accept_ready_check():
        current = state.read()
        if not request.is_json:
            return jsonify(error="Ungültige Anfrage."), 415
        if not current.get("connected"):
            return jsonify(error="Der League-Client ist nicht verbunden."), 503
        if current.get("phase") != "ReadyCheck":
            return jsonify(error="Es ist gerade kein Match zum Annehmen offen."), 409
        try:
            lolclient.accept_ready_check()
            return jsonify(accepted=True)
        except lolclient.ReadyCheckUnavailable as error:
            return jsonify(error=str(error)), 409
        except (FileNotFoundError, OSError, RuntimeError, requests.RequestException):
            return jsonify(error="Das Match konnte im League-Client nicht angenommen werden."), 502

    def lobby_action_error(error):
        if isinstance(error, lolclient.LobbyActionError):
            return jsonify(error=str(error)), 409
        log.warning("League-Lobby-Aktion fehlgeschlagen: %s", error)
        return jsonify(error="Der League-Client konnte die Aktion nicht ausführen."), 502

    def perform_lobby_action(action, *args):
        try:
            action(*args)
            return jsonify(ok=True)
        except LOBBY_ACTION_ERRORS as error:
            return lobby_action_error(error)

    @app.post("/api/lobby/queue")
    def change_lobby_queue():
        data = request.get_json(silent=True)
        queue_id = data.get("queue_id") if isinstance(data, dict) else None
        current = state.read()
        allowed = {queue["id"] for queue in current.get("available_queues", [])}
        if type(queue_id) is not int or queue_id not in allowed:
            return jsonify(error="Dieser Spielmodus ist derzeit nicht verfügbar."), 400
        if current.get("phase") not in {"None", "Lobby"}:
            return jsonify(error="Eine Lobby kann gerade nicht erstellt oder geändert werden."), 409
        if current.get("phase") == "Lobby" and not current.get("can_manage_lobby"):
            return jsonify(error="Nur der Gruppenleiter kann den Spielmodus in der Lobby ändern."), 409
        return perform_lobby_action(lolclient.change_lobby_queue, queue_id)

    @app.post("/api/lobby/positions")
    def change_positions():
        data = request.get_json(silent=True)
        first = data.get("first") if isinstance(data, dict) else None
        second = data.get("second") if isinstance(data, dict) else None
        current = state.read()
        if first not in LCU_POSITIONS or second not in LCU_POSITIONS or first == second:
            return jsonify(error="Bitte zwei unterschiedliche Positionen auswählen."), 400
        if current.get("phase") != "Lobby" or not current.get("show_position_selector"):
            return jsonify(error="Für diesen Spielmodus können keine Positionen gewählt werden."), 409
        return perform_lobby_action(
            lolclient.set_position_preferences, LCU_POSITIONS[first], LCU_POSITIONS[second]
        )

    @app.post("/api/matchmaking/start")
    def start_matchmaking():
        current = state.read()
        if not request.is_json:
            return jsonify(error="Ungültige Anfrage."), 415
        if current.get("phase") != "Lobby" or not current.get("can_manage_lobby"):
            return jsonify(error="Nur der Gruppenleiter kann die Spielsuche starten."), 409
        return perform_lobby_action(lolclient.start_matchmaking)

    @app.post("/api/matchmaking/stop")
    def stop_matchmaking():
        current = state.read()
        if not request.is_json:
            return jsonify(error="Ungültige Anfrage."), 415
        if current.get("phase") != "Matchmaking" or not current.get("can_manage_lobby"):
            return jsonify(error="Die Spielsuche kann gerade nicht abgebrochen werden."), 409
        return perform_lobby_action(lolclient.stop_matchmaking)

    @app.post("/api/champion-select")
    def champion_select():
        data = request.get_json(silent=True)
        champion_id = data.get("champion_id") if isinstance(data, dict) else None
        lock = data.get("lock", False) if isinstance(data, dict) else False
        current = state.read()
        known = {champion["id"] for champion in current.get("available_champions", [])}
        pickable = set(current.get("pickable_champion_ids", []))
        if type(champion_id) is not int or type(lock) is not bool or champion_id not in known:
            return jsonify(error="Ungültiger Champion."), 400
        if current.get("phase") != "ChampSelect" or not current.get("pick_action"):
            return jsonify(error="Für dich ist gerade keine Champion-Auswahl offen."), 409
        if pickable and champion_id not in pickable:
            return jsonify(error="Dieser Champion ist im aktuellen Draft nicht verfügbar."), 409
        if lock and not current["pick_action"].get("active"):
            return jsonify(error="Du bist noch nicht mit deinem Pick an der Reihe."), 409
        try:
            lolclient.select_champion(champion_id, lock=lock)
            return jsonify(ok=True)
        except lolclient.ChampionSelectError as error:
            return jsonify(error=str(error)), 409
        except (FileNotFoundError, OSError, RuntimeError, requests.RequestException):
            return jsonify(error="Der Champion konnte im League-Client nicht ausgewählt werden."), 502

    @app.get("/api/build")
    def build():
        parameters = build_parameters(request.args, builds.tier)
        if parameters is None:
            return jsonify(error="Bitte einen gültigen Champion und eine gültige Rolle wählen."), 400
        champion, position, tier, mode = parameters
        try:
            info = builds.get(champion, position, tier=tier, mode=mode)
            if not (info.core_builds or info.rune_builds or info.spell_builds):
                raise opgg.OpggResponseError("OP.GG liefert für diesen Champion aktuell keine Build-Daten.")
            return jsonify(info.to_dict())
        except opgg.BuildNotFoundError:
            role_names = {
                "top": "Top",
                "jungle": "Jungle",
                "mid": "Mid",
                "adc": "Bot / ADC",
                "support": "Support",
            }
            role = role_names.get(position, "der Standardrolle")
            return jsonify(
                error=f"OP.GG hat für {champion} auf {role} aktuell keine Build-Daten.",
                kind="no_role_data",
            ), 404
        except opgg.ChampionNotFoundError:
            return jsonify(error="Für diesen Champion wurde kein OP.GG-Build gefunden."), 404
        except (opgg.OpggError, FutureTimeout, ValueError, KeyError, TypeError) as error:
            log.warning(
                "OP.GG-Build für %s (%s) nicht verfügbar: %s",
                champion,
                position or "auto",
                error,
            )
            return jsonify(error="Der Build konnte gerade nicht von OP.GG geladen werden. Bitte erneut versuchen."), 502

    @app.post("/api/runes")
    def import_runes():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="Ungültige Anfrage."), 400
        parameters = build_parameters(data, builds.tier)
        rune_index = data.get("rune_index", 0)
        if (
            parameters is None
            or type(rune_index) is not int
            or rune_index not in {0, 1}
        ):
            return jsonify(error="Champion, Rolle oder Runenseite ist ungültig."), 400
        champion, position, tier, mode = parameters
        try:
            info = builds.get(champion, position, tier=tier, mode=mode)
            if rune_index >= len(info.rune_builds):
                return jsonify(error="OP.GG bietet diese Runenseite nicht an."), 404
            result = runeclient.import_rune_page(
                info.rune_builds[rune_index], info.name, info.position
            )
            return jsonify(**result.to_dict())
        except opgg.BuildNotFoundError:
            return jsonify(error="OP.GG hat für diese Rolle keine Runen."), 404
        except opgg.OpggError:
            return jsonify(error="Die Runen konnten gerade nicht von OP.GG geladen werden."), 502
        except runeclient.RuneImportError as error:
            return jsonify(error=str(error), kind="rune_import_error"), 502
        except (FutureTimeout, ValueError, KeyError, TypeError):
            return jsonify(error="Die Runen konnten nicht importiert werden."), 502

    return app


def local_network_url(port):
    """Return the address that other devices on the local network can reach."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            # UDP connect does not send data; it only lets the OS select the
            # interface that would be used for traffic outside this machine.
            probe.connect(("8.8.8.8", 80))
            lan_ip = probe.getsockname()[0]
    except OSError:
        lan_ip = socket.gethostbyname(socket.gethostname())
    return f"http://{lan_ip}:{port}"


def main():
    parser = argparse.ArgumentParser(description="LOLBUDDY – lokales Live-Dashboard")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--region", default="euw")
    parser.add_argument("--tier", default="emerald_plus")
    # Kept as a no-op so existing shortcuts using --lan continue to work.
    parser.add_argument("--lan", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-browser", action="store_true", help="Browser nicht automatisch öffnen")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Der Port muss zwischen 1 und 65535 liegen.")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    host = "0.0.0.0"
    url = f"http://127.0.0.1:{args.port}"
    lan_url = local_network_url(args.port)
    print(f"LOLBUDDY läuft lokal auf {url}\nHandy im selben WLAN: {lan_url}", flush=True)
    print("Beenden mit Strg+C.", flush=True)
    state, builds = LiveState(), BuildCache(args.region, args.tier)
    app = create_app(state, builds, lan_url=lan_url)
    monitor = LcuMonitor(state)
    monitor.start()
    if not args.no_browser:
        opener = threading.Timer(1, webbrowser.open, args=(url,))
        opener.daemon = True
        opener.start()
    try:
        app.run(host=host, port=args.port, threaded=True, use_reloader=False)
    finally:
        monitor.stop.set()
        monitor.thread.join(timeout=5)
        builds.close()


if __name__ == "__main__":
    main()
