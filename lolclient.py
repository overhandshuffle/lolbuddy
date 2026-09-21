from pathlib import Path
import time
from uuid import uuid4

import psutil
import requests
import urllib3

# ============================================================
# Einstellungen
# ============================================================

POLL_INTERVAL = 0.25  # Sekunden
START_RETRY_INTERVAL = 2.0  # Sekunden
SUMMONER_SPELL_IDS = (4, 14, 12, 6, 7, 21, 3, 1, 11, 13, 32)
MAX_ITEM_SETS = 100


# League benutzt lokal ein selbstsigniertes Zertifikat.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ============================================================
# League Client finden
# ============================================================


def find_lockfile():
    for process in psutil.process_iter(["name", "exe"]):
        try:
            name = process.info["name"]

            if not name:
                continue

            if name.lower() != "leagueclient.exe":
                continue

            exe = process.info["exe"]

            if not exe:
                continue

            exe_path = Path(exe)
            lockfile = exe_path.parent / "lockfile"

            if lockfile.exists():
                return lockfile

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    raise FileNotFoundError(
        "League Client wurde nicht gefunden.\n"
        "Bitte League of Legends öffnen und einloggen."
    )


# ============================================================
# Verbindung zur LCU herstellen
# ============================================================


def connect_to_lcu():
    lockfile = find_lockfile()

    content = lockfile.read_text(encoding="utf-8").strip()

    parts = content.split(":")

    if len(parts) != 5:
        raise RuntimeError("Ungültiges Format der League-Lockfile.")

    process_name, pid, port, password, protocol = parts

    session = requests.Session()
    session.trust_env = False  # Lokale Zugangsdaten nie an einen Proxy senden.

    # LCU Basic Authentication
    session.auth = ("riot", password)

    # Lokales selbstsigniertes Zertifikat akzeptieren
    session.verify = False

    if not port.isdigit() or not 1 <= int(port) <= 65535:
        session.close()
        raise RuntimeError("Ungültiger LCU-Port.")
    base_url = f"https://127.0.0.1:{port}"

    return session, base_url


# ============================================================
# Championnamen laden
# ============================================================


def load_champion_details(session, base_url):
    url = f"{base_url}" "/lol-game-data/assets/v1/champion-summary.json"

    response = session.get(url, timeout=3)

    response.raise_for_status()

    champions = {}

    for champion in response.json():

        champion_id = int(champion["id"])

        name = champion.get("name", champion.get("alias", str(champion_id)))

        if champion_id > 0:
            champions[champion_id] = {
                "name": name,
                "alias": champion.get("alias", name),
            }

    return champions


# ============================================================
# Gameflow Phase
# ============================================================


def get_phase(session, base_url):
    response = session.get(f"{base_url}/lol-gameflow/v1/gameflow-phase", timeout=2)

    response.raise_for_status()
    return response.json()


def is_logged_in(session, base_url):
    """Prüft, ob die LCU bereits bei einem League-Konto angemeldet ist."""

    response = session.get(f"{base_url}/lol-login/v1/session", timeout=2)
    if not response.ok:
        return False
    return response.json().get("state") == "SUCCEEDED"


def get_queue_details(session, base_url, queue_id):
    """Liest den lokalisierten Namen und die Beschreibung einer Queue."""

    if not queue_id:
        return {}
    response = session.get(f"{base_url}/lol-game-queues/v1/queues/{int(queue_id)}", timeout=2)
    if not response.ok:
        return {}
    data = response.json()
    return {
        "id": int(data.get("id", queue_id)),
        "name": data.get("name") or data.get("shortName") or "League of Legends",
        "description": data.get("description") or "",
        "game_mode": data.get("gameMode") or "",
    }


def get_available_queues(session, base_url):
    """Liefert nur momentan sichtbare und spielbare Matchmaking-Queues."""

    response = session.get(f"{base_url}/lol-game-queues/v1/queues", timeout=4)
    response.raise_for_status()
    queues = []
    for data in response.json():
        if (
            not data.get("isEnabled")
            or not data.get("isVisible")
            or data.get("queueAvailability") != "Available"
            or data.get("isCustom")
        ):
            continue
        queue_id = int(data.get("id", 0) or 0)
        if queue_id <= 0:
            continue
        queues.append({
            "id": queue_id,
            "name": data.get("name") or data.get("shortName") or f"Queue {queue_id}",
            "description": data.get("description") or "",
            "game_mode": data.get("gameMode") or "",
            "positions": bool(data.get("showPositionSelector")),
            "priority": int(data.get("gameSelectPriority", 9999) or 9999),
        })
    queues.sort(key=lambda queue: (queue["priority"], queue["name"], queue["id"]))
    return queues


def get_matchmaking_search(session, base_url):
    """Gibt Suchstatus und Wartezeit der laufenden Queue zurück."""

    response = session.get(f"{base_url}/lol-matchmaking/v1/search", timeout=2)
    if response.status_code == 404:
        return {"active": False, "elapsed_seconds": 0, "estimated_seconds": 0}
    response.raise_for_status()
    data = response.json()
    return {
        "active": bool(data.get("isCurrentlyInQueue") or data.get("searchState") == "Searching"),
        "elapsed_seconds": max(0, int(float(data.get("timeInQueue", 0) or 0))),
        "estimated_seconds": max(0, int(float(data.get("estimatedQueueTime", 0) or 0))),
    }


def get_summoner_display_name(session, base_url, summoner_id):
    """Löst eine Summoner-ID in den aktuellen Riot-Namen auf."""

    if not summoner_id:
        return ""
    response = session.get(f"{base_url}/lol-summoner/v1/summoners/{int(summoner_id)}", timeout=2)
    if not response.ok:
        return ""
    data = response.json()
    game_name = data.get("gameName") or data.get("displayName") or ""
    tag_line = data.get("tagLine") or ""
    return f"{game_name} #{tag_line}" if game_name and tag_line else game_name


def get_playable_champions(session, base_url):
    """Liest eigene und aktuell kostenlose Champions aus dem League-Client."""

    response = session.get(f"{base_url}/lol-champions/v1/owned-champions-minimal", timeout=4)
    response.raise_for_status()
    champions = []
    for data in response.json():
        ownership = data.get("ownership") or {}
        rental = ownership.get("rental") or {}
        playable = bool(
            ownership.get("owned")
            or ownership.get("loyaltyReward")
            or ownership.get("xboxGPReward")
            or rental.get("rented")
            or data.get("freeToPlay")
        )
        champion_id = int(data.get("id", 0) or 0)
        if champion_id <= 0 or not playable or not data.get("isVisibleInClient", True):
            continue
        champions.append({
            "id": champion_id,
            "name": data.get("name") or data.get("alias") or str(champion_id),
            "alias": data.get("alias") or data.get("name") or str(champion_id),
            "owned": bool(ownership.get("owned")),
            "free_to_play": bool(data.get("freeToPlay")),
        })
    champions.sort(key=lambda champion: champion["name"].casefold())
    return champions


def get_summoner_spells(session, base_url):
    """Liest die normalen Summoner Spells samt erlaubter Spielmodi aus dem Client."""

    response = session.get(
        f"{base_url}/lol-game-data/assets/v1/summoner-spells.json", timeout=4
    )
    response.raise_for_status()
    by_id = {
        int(spell.get("id", 0) or 0): {
            "id": int(spell.get("id", 0) or 0),
            "name": spell.get("name") or str(spell.get("id", "")),
            "game_modes": tuple(spell.get("gameModes") or ()),
        }
        for spell in response.json()
        if int(spell.get("id", 0) or 0) in SUMMONER_SPELL_IDS
    }
    return tuple(by_id[spell_id] for spell_id in SUMMONER_SPELL_IDS if spell_id in by_id)


def get_pickable_champion_ids(session, base_url):
    """Liest die für den aktuellen Draft tatsächlich erlaubten Champions."""

    for path in (
        "/lol-champ-select/v1/pickable-champion-ids",
        "/lol-lobby-team-builder/champ-select/v1/pickable-champion-ids",
    ):
        response = session.get(f"{base_url}{path}", timeout=2)
        if response.ok:
            return {int(champion_id) for champion_id in response.json() if int(champion_id) > 0}
        if response.status_code != 404:
            response.raise_for_status()
    return None


def get_local_pick_action(champ_select):
    """Findet ausschließlich die noch offene Pick-Aktion des lokalen Spielers."""

    local_cell = champ_select.get("localPlayerCellId")
    actions = [
        action
        for group in champ_select.get("actions", [])
        for action in group
        if action.get("type") == "pick"
        and action.get("actorCellId") == local_cell
        and not action.get("completed")
    ]
    if not actions:
        return None
    return next((action for action in actions if action.get("isInProgress")), actions[-1])


class ChampionSelectError(RuntimeError):
    """Der gewünschte Champion kann im aktuellen Draft nicht gewählt werden."""


def select_champion(champion_id, *, lock=False):
    """Setzt den eigenen Hover oder schließt den eigenen aktiven Pick ab."""

    session = None
    try:
        session, base_url = connect_to_lcu()
        if get_phase(session, base_url) != "ChampSelect":
            raise ChampionSelectError("Die Champion-Auswahl ist nicht mehr aktiv.")
        champ_select = get_champ_select_session(session, base_url)
        action = get_local_pick_action(champ_select or {})
        if not action:
            raise ChampionSelectError("Für dich ist keine offene Champion-Auswahl vorhanden.")
        if lock and not action.get("isInProgress"):
            raise ChampionSelectError("Du bist noch nicht mit deinem Pick an der Reihe.")
        pickable = get_pickable_champion_ids(session, base_url)
        champion_id = int(champion_id)
        if pickable is not None and champion_id not in pickable:
            raise ChampionSelectError("Dieser Champion ist im aktuellen Draft nicht verfügbar.")
        path = f"{base_url}/lol-champ-select/v1/session/actions/{int(action['id'])}"
        payload = {"championId": champion_id}
        if lock:
            payload["completed"] = True
        response = session.patch(path, json=payload, timeout=3)
        if not response.ok:
            message = "fest gewählt" if lock else "ausgewählt"
            raise ChampionSelectError(f"Der Champion konnte nicht {message} werden.")
    finally:
        if session is not None:
            session.close()


class SummonerSpellError(RuntimeError):
    """Die gewünschten Summoner Spells können gerade nicht gesetzt werden."""


def set_summoner_spells(first_spell_id, second_spell_id):
    """Setzt beide Summoner Spells der lokalen Champ-Select-Auswahl."""

    first_spell_id = int(first_spell_id)
    second_spell_id = int(second_spell_id)
    if (
        first_spell_id not in SUMMONER_SPELL_IDS
        or second_spell_id not in SUMMONER_SPELL_IDS
        or first_spell_id == second_spell_id
    ):
        raise SummonerSpellError("Bitte zwei unterschiedliche Summoner Spells wählen.")

    session = None
    try:
        session, base_url = connect_to_lcu()
        if get_phase(session, base_url) != "ChampSelect":
            raise SummonerSpellError("Die Champion-Auswahl ist nicht mehr aktiv.")
        response = session.patch(
            f"{base_url}/lol-champ-select/v1/session/my-selection",
            json={"spell1Id": first_spell_id, "spell2Id": second_spell_id},
            timeout=3,
        )
        if not response.ok:
            raise SummonerSpellError("Der League-Client hat diese Spell-Kombination abgelehnt.")
    finally:
        if session is not None:
            session.close()


class ItemSetError(RuntimeError):
    """Ein OP.GG-Itemset konnte nicht in den League-Client geschrieben werden."""


def import_item_set(champion_id, title, blocks, *, map_ids=None):
    """Ersetzt Itemsets des Champions und lässt globale bzw. fremde Sets bestehen."""

    champion_id = int(champion_id)
    normalized_blocks = []
    for block in blocks:
        items = [
            {"id": str(int(item["id"])), "count": max(1, int(item.get("count", 1)))}
            for item in block.get("items", [])
            if int(item.get("id", 0) or 0) > 0
        ]
        if items:
            normalized_blocks.append({
                "type": str(block.get("type") or "Items")[:60],
                "items": items,
                "hideIfSummonerSpell": "",
                "showIfSummonerSpell": "",
            })
    if champion_id <= 0 or not normalized_blocks:
        raise ItemSetError("Für diesen Champion sind keine Item-Daten verfügbar.")

    session = None
    try:
        session, base_url = connect_to_lcu()
        summoner_response = session.get(
            f"{base_url}/lol-summoner/v1/current-summoner", timeout=3
        )
        if not summoner_response.ok:
            raise ItemSetError("Das League-Konto konnte nicht gelesen werden.")
        summoner = summoner_response.json()
        account_id = int(summoner.get("accountId") or summoner.get("summonerId") or 0)
        if account_id <= 0:
            raise ItemSetError("Das League-Konto konnte nicht gelesen werden.")

        endpoint = f"{base_url}/lol-item-sets/v1/item-sets/{account_id}/sets"
        sets_response = session.get(endpoint, timeout=3)
        if not sets_response.ok:
            raise ItemSetError("Die vorhandenen Itemsets konnten nicht gelesen werden.")
        current = sets_response.json()
        item_sets = current.get("itemSets")
        if not isinstance(item_sets, list):
            raise ItemSetError("Der League-Client hat ungültige Itemset-Daten geliefert.")

        retained = []
        replaced = 0
        for item_set in item_sets:
            associated = item_set.get("associatedChampions") or []
            associated_ids = []
            for item in associated:
                try:
                    associated_ids.append(int(item))
                except (TypeError, ValueError):
                    continue
            if champion_id not in associated_ids:
                retained.append(item_set)
                continue
            replaced += 1
            remaining = [item for item in associated_ids if item != champion_id]
            if remaining:
                retained.append({**item_set, "associatedChampions": remaining})

        if len(retained) >= MAX_ITEM_SETS:
            raise ItemSetError(
                "Dein League-Konto hat bereits die maximale Anzahl an Itemsets erreicht."
            )

        clean_title = str(title).strip()[:60] or "lolbuddy Build"
        retained.append({
            "uid": str(uuid4()),
            "title": clean_title,
            "type": "custom",
            "map": "any",
            "mode": "any",
            "sortrank": 0,
            "startedFrom": "blank",
            "associatedChampions": [champion_id],
            "associatedMaps": [int(map_id) for map_id in (map_ids or [])],
            "blocks": normalized_blocks,
            "preferredItemSlots": [],
        })
        payload = {
            "accountId": account_id,
            "itemSets": retained,
            "timestamp": int(time.time() * 1000),
        }
        update_response = session.put(endpoint, json=payload, timeout=5)
        if not update_response.ok:
            raise ItemSetError("Der League-Client hat das Itemset abgelehnt.")
        return {"name": clean_title, "replaced": replaced}
    finally:
        if session is not None:
            session.close()


class ReadyCheckUnavailable(RuntimeError):
    """Der Ready Check ist nicht mehr aktiv oder kann nicht angenommen werden."""


def accept_ready_check():
    """Nimmt den aktuell sichtbaren Ready Check über die lokale LCU an."""

    session = None
    try:
        session, base_url = connect_to_lcu()
        if get_phase(session, base_url) != "ReadyCheck":
            raise ReadyCheckUnavailable("Es ist gerade kein Match zum Annehmen offen.")
        response = session.post(
            f"{base_url}/lol-matchmaking/v1/ready-check/accept",
            timeout=3,
        )
        if response.status_code in {404, 409}:
            raise ReadyCheckUnavailable("Der Ready Check ist bereits abgelaufen.")
        response.raise_for_status()
    finally:
        if session is not None:
            session.close()


class LobbyActionError(RuntimeError):
    """Eine gewünschte Änderung ist im aktuellen Lobbyzustand nicht möglich."""


def _lobby_action(method, path, *, phases, payload=None):
    session = None
    try:
        session, base_url = connect_to_lcu()
        if get_phase(session, base_url) not in phases:
            raise LobbyActionError("Die Lobby befindet sich nicht mehr im passenden Zustand.")
        response = session.request(method, f"{base_url}{path}", json=payload, timeout=4)
        if not response.ok:
            try:
                data = response.json()
                message = data.get("message") or data.get("errorCode")
            except (ValueError, TypeError, AttributeError):
                message = None
            raise LobbyActionError(message or "Der League-Client hat die Aktion abgelehnt.")
    finally:
        if session is not None:
            session.close()


def change_lobby_queue(queue_id):
    _lobby_action("POST", "/lol-lobby/v2/lobby", phases={"None", "Lobby"}, payload={"queueId": int(queue_id)})


def set_position_preferences(first, second):
    _lobby_action(
        "PUT",
        "/lol-lobby/v2/lobby/members/localMember/position-preferences",
        phases={"Lobby"},
        payload={"firstPreference": first, "secondPreference": second},
    )


def start_matchmaking():
    _lobby_action("POST", "/lol-lobby/v2/lobby/matchmaking/search", phases={"Lobby"})


def stop_matchmaking():
    _lobby_action("DELETE", "/lol-lobby/v2/lobby/matchmaking/search", phases={"Matchmaking"})


# ============================================================
# Champ Select Session
# ============================================================


def get_champ_select_session(session, base_url):
    response = session.get(f"{base_url}/lol-champ-select/v1/session", timeout=2)

    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


# ============================================================
# Teamstatus erkennen
# ============================================================


POSITION_NAMES = {
    "top": "TOP",
    "jungle": "JUNGLE",
    "middle": "MID",
    "mid": "MID",
    "bottom": "BOT",
    "bot": "BOT",
    "utility": "SUPPORT",
    "support": "SUPPORT",
}


def get_pick_actions(champ_select):
    """Ordnet jedem Spieler seine aktuell sichtbare Pick-Action zu."""

    pick_actions = {}

    for action_group in champ_select.get("actions", []):
        for action in action_group:
            if action.get("type") != "pick":
                continue

            cell_id = action.get("actorCellId")

            if cell_id is not None:
                pick_actions[cell_id] = action

    return pick_actions


def get_team_state(champ_select, team_key, pick_actions):
    """Liefert Champion, Status und Position aller Spieler eines Teams."""

    local_cell_id = champ_select.get("localPlayerCellId")
    team_state = []

    for player in champ_select.get(team_key, []):
        cell_id = player.get("cellId")
        action = pick_actions.get(cell_id, {})
        action_champion_id = int(action.get("championId", 0) or 0)
        selected_champion_id = int(player.get("championId", 0) or 0)
        pick_intent = int(player.get("championPickIntent", 0) or 0)

        if action_champion_id and not action.get("completed", False):
            champion_id = action_champion_id
            status = "HOVER"
        elif selected_champion_id:
            champion_id = selected_champion_id
            status = "LOCKED"
        elif action_champion_id:
            champion_id = action_champion_id
            status = "LOCKED"
        elif pick_intent:
            champion_id = pick_intent
            status = "HOVER"
        else:
            champion_id = 0
            status = "OFFEN"

        raw_position = (
            player.get("assignedPosition")
            or player.get("selectedPosition")
            or player.get("position")
            or ""
        )
        position = POSITION_NAMES.get(
            str(raw_position).lower(), str(raw_position).upper()
        )

        team_state.append(
            (
                cell_id,
                champion_id,
                status,
                position,
                team_key == "myTeam" and cell_id == local_cell_id,
            )
        )

    return tuple(team_state)
