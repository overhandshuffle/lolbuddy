"""Deterministische Tests ohne laufenden League-Client oder Internet."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

import app
import lolclient
import opgg
import runeclient

TEST_CHAMPIONS = {
    103: {"name": "Ahri", "alias": "Ahri"},
    222: {"name": "Jinx", "alias": "Jinx"},
}


def flight(records, split=False):
    text = "\n".join(f"{key}:{json.dumps(value, separators=(',', ':'))}" for key, value in records.items())
    chunks = [text[:len(text)//2], text[len(text)//2:]] if split else [text]
    return "".join(f"<script>self.__next_f.push([1,{json.dumps(chunk)}])</script>" for chunk in chunks)


def node(tag, children, key=None, **props):
    return ["$", tag, key, {**props, "children": children}]


def asset(kind, ident, name):
    return node("$L99", node("img", None, src=f"https://opgg-static.akamaized.net/{kind}/{ident}.png", alt=name), metaType=kind, metaId=ident)


class OpggTests(unittest.TestCase):
    def test_json_chunks_with_quotes_and_brackets(self):
        data = opgg._FlightData(flight({"1": {"rune_pages": [{"name": 'A ] [ " rune'}]}}, split=True))
        self.assertEqual(data.extract("rune_pages")[0]["name"], 'A ] [ " rune')

    def test_item_row_resolves_rates_without_bleeding_into_next_row(self):
        html = flight({
            "1": node("tr", [asset("item", 3020, "Shoes"), "$L2", "$L3"], "boots_0"),
            "2": node("strong", "57.11%"), "3": node("strong", "50.95%"),
            "4": node("tr", [asset("item", 3006, "Other"), node("strong", "12.00%"), node("strong", "99.99%")], "boots_1"),
        })
        builds = opgg._parse_item_builds(opgg._FlightData(html), "boots", limit=1)
        self.assertEqual([item.id for item in builds[0].items], [3020])
        self.assertEqual(builds[0].pick_rate, 57.11)
        self.assertEqual(builds[0].win_rate, 50.95)

    def test_item_quantities(self):
        row = node("tr", node("div", [asset("item", 2003, "Potion"), node("span", 2)], className="relative"), "starter_items_0")
        builds = opgg._parse_item_builds(opgg._FlightData(flight({"1": row})), "starter_items", limit=1)
        self.assertEqual(builds[0].items[0].count, 2)

    def test_spells_include_images_and_statistics(self):
        row = node("$L55", [asset("spell", 4, "Flash"), asset("spell", 14, "Ignite"), node("strong", "60.20%"), node("strong", "51.40%")], "spells_table_0")
        build = opgg._parse_spell_builds(opgg._FlightData(flight({"1": row})))[0]
        self.assertEqual([spell.name for spell in build.spells], ["Flash", "Ignite"])
        self.assertEqual(build.win_rate, 51.4)
        self.assertTrue(build.spells[0].image_url.endswith("/4.png"))

    def test_only_active_runes_and_duplicate_shards_are_retained(self):
        selected = {"id": 5008, "name": "Adaptive Force", "image_url": "https://example.com/rune.png", "isActive": True}
        page = {"primary_perk_style": {"id": 8100, "name": "Domination"}, "perk_sub_style": {"id": 8200, "name": "Sorcery"},
                "primary_rune": {"name": "Electrocute"}, "play": 100, "pick_rate": .8, "win_rate": .51,
                "importClientData": {"primaryStyleId": 8100, "subStyleId": 8200, "selectedPerkIds": list(range(1, 10))},
                "builds": [{"main_runes": [[selected, {**selected, "isActive": False}]], "sub_runes": [], "shards": [[selected], [selected]]}]}
        build = opgg._parse_rune_builds(opgg._FlightData(flight({"1": {"rune_pages": [page]}})))[0]
        self.assertEqual(len(build.primary_runes), 1)
        self.assertEqual(len(build.shard_details), 2)
        self.assertEqual(build.pick_rate, 80)
        self.assertEqual(build.primary_style_id, 8100)
        self.assertEqual(build.secondary_style_id, 8200)
        self.assertEqual(build.selected_perk_ids, tuple(range(1, 10)))

    def test_aram_summary_uses_mode_specific_champion_stats(self):
        html = flight({"1": {"champions": [{
            "key": "ahri", "name": "Ahri",
            "image_url": "https://opgg-static.akamaized.net/meta/images/lol/16.18.1/champion/Ahri.png",
            "win_rate": .517, "pick_rate": .099, "tier": 2,
        }]}})
        info = opgg._parse_champion_page(
            html,
            source_url="https://op.gg/lol/modes/aram/ahri/build",
            champion_slug="ahri",
            region="global",
            rank_tier="all",
            game_mode="aram",
        )
        self.assertEqual(info.position, "aram")
        self.assertEqual(info.game_mode, "aram")
        self.assertEqual(info.patch, "16.18")
        self.assertAlmostEqual(info.win_rate, 51.7)
        self.assertEqual(info.ban_rate, 0)


def sample_runes(perk_count=9):
    return opgg.RuneBuild(
        primary_style="Domination",
        secondary_style="Sorcery",
        keystone="Electrocute",
        runes=(),
        shards=(),
        pick_rate=50,
        win_rate=51,
        games=100,
        primary_style_id=8100,
        secondary_style_id=8200,
        selected_perk_ids=tuple(range(8001, 8001 + perk_count)),
    )


def json_response(data, status=200):
    response = Mock()
    response.ok = 200 <= status < 300
    response.status_code = status
    response.json.return_value = data
    return response


class RuneClientTests(unittest.TestCase):
    def test_page_name_is_recognizable_and_limited(self):
        name = runeclient.make_page_name("Aurelion Sol With Extra Text", "mid")
        self.assertFalse(name.startswith(runeclient.LEGACY_PAGE_PREFIX))
        self.assertTrue(name.endswith(" Mid"))
        self.assertLessEqual(len(name), runeclient.MAX_PAGE_NAME_LENGTH)

    def test_creates_managed_page_without_touching_personal_pages(self):
        session = Mock()
        session.request.side_effect = [
            json_response([{"id": 7, "name": "Meine Runen", "isEditable": True}]),
            json_response({"id": 42, "name": "Ahri Mid"}),
        ]
        result = runeclient.import_rune_page(
            sample_runes(), "Ahri", "mid", session=session, base_url="https://lcu",
            state_path=None,
        )
        self.assertTrue(result.created)
        self.assertEqual(result.id, 42)
        self.assertEqual(session.request.call_args_list[1].args[:2], (
            "POST", "https://lcu/lol-perks/v1/pages"
        ))
        payload = session.request.call_args_list[1].kwargs["json"]
        self.assertEqual(payload["selectedPerkIds"], list(range(8001, 8010)))
        self.assertTrue(payload["current"])

    def test_updates_only_existing_lolbuddy_page(self):
        session = Mock()
        session.request.side_effect = [
            json_response([
                {"id": 7, "name": "Meine Runen", "isEditable": True},
                {"id": 42, "name": "LOLBUDDY | Jinx ADC", "isEditable": True},
            ]),
            json_response({"id": 42, "name": "Ahri Mid"}),
        ]
        result = runeclient.import_rune_page(
            sample_runes(), "Ahri", "mid", session=session, base_url="https://lcu",
            state_path=None,
        )
        self.assertFalse(result.created)
        self.assertEqual(session.request.call_args_list[1].args[:2], (
            "PUT", "https://lcu/lol-perks/v1/pages/42"
        ))
        self.assertNotIn("DELETE", [call.args[0] for call in session.request.call_args_list])

    def test_saved_page_id_updates_page_after_prefix_is_removed(self):
        with TemporaryDirectory() as directory:
            state_path = Path(directory) / "rune-page.json"
            state_path.write_text('{"page_id": 42}', encoding="utf-8")
            session = Mock()
            session.request.side_effect = [
                json_response([
                    {"id": 42, "name": "Jinx ADC", "isEditable": True},
                    {"id": 7, "name": "Meine Runen", "isEditable": True},
                ]),
                json_response({"id": 42, "name": "Ahri Mid"}),
            ]
            result = runeclient.import_rune_page(
                sample_runes(), "Ahri", "mid", session=session,
                base_url="https://lcu", state_path=state_path,
            )
            self.assertFalse(result.created)
            self.assertEqual(
                session.request.call_args_list[1].args[1],
                "https://lcu/lol-perks/v1/pages/42",
            )
            self.assertEqual(json.loads(state_path.read_text())["page_id"], 42)

    def test_rejects_incomplete_page_before_contacting_client(self):
        session = Mock()
        with self.assertRaises(runeclient.RuneImportError):
            runeclient.import_rune_page(
                sample_runes(8), "Ahri", "mid", session=session, base_url="https://lcu",
                state_path=None,
            )
        session.request.assert_not_called()


class ClientTests(unittest.TestCase):
    def test_select_champion_uses_local_pick_action_and_completes_lock(self):
        session = Mock()
        session.patch.return_value.ok = True
        selection = {
            "localPlayerCellId": 3,
            "actions": [[
                {"id": 7, "actorCellId": 2, "type": "pick", "isInProgress": True, "completed": False},
                {"id": 8, "actorCellId": 3, "type": "pick", "isInProgress": True, "completed": False},
            ]],
        }
        with patch.object(lolclient, "connect_to_lcu", return_value=(session, "https://127.0.0.1:1234")), \
             patch.object(lolclient, "get_phase", return_value="ChampSelect"), \
             patch.object(lolclient, "get_champ_select_session", return_value=selection), \
             patch.object(lolclient, "get_pickable_champion_ids", return_value={103}):
            lolclient.select_champion(103, lock=True)
        session.patch.assert_called_once_with(
            "https://127.0.0.1:1234/lol-champ-select/v1/session/actions/8",
            json={"championId": 103, "completed": True}, timeout=3,
        )
        session.post.assert_not_called()
        session.close.assert_called_once_with()

    def test_select_champion_hover_does_not_complete_pick(self):
        session = Mock()
        session.patch.return_value.ok = True
        selection = {
            "localPlayerCellId": 3,
            "actions": [[
                {"id": 8, "actorCellId": 3, "type": "pick", "isInProgress": False, "completed": False},
            ]],
        }
        with patch.object(lolclient, "connect_to_lcu", return_value=(session, "https://127.0.0.1:1234")), \
             patch.object(lolclient, "get_phase", return_value="ChampSelect"), \
             patch.object(lolclient, "get_champ_select_session", return_value=selection), \
             patch.object(lolclient, "get_pickable_champion_ids", return_value={103}):
            lolclient.select_champion(103)
        session.patch.assert_called_once_with(
            "https://127.0.0.1:1234/lol-champ-select/v1/session/actions/8",
            json={"championId": 103}, timeout=3,
        )
        session.post.assert_not_called()

    def test_accept_ready_check_posts_to_lcu_and_closes_session(self):
        session = Mock()
        session.post.return_value.status_code = 204
        with patch.object(
            lolclient, "connect_to_lcu", return_value=(session, "https://127.0.0.1:1234")
        ), patch.object(lolclient, "get_phase", return_value="ReadyCheck"):
            lolclient.accept_ready_check()
        session.post.assert_called_once_with(
            "https://127.0.0.1:1234/lol-matchmaking/v1/ready-check/accept",
            timeout=3,
        )
        session.post.return_value.raise_for_status.assert_called_once_with()
        session.close.assert_called_once_with()

    def test_accept_ready_check_refuses_other_phases(self):
        session = Mock()
        with patch.object(
            lolclient, "connect_to_lcu", return_value=(session, "https://127.0.0.1:1234")
        ), patch.object(lolclient, "get_phase", return_value="Matchmaking"):
            with self.assertRaises(lolclient.ReadyCheckUnavailable):
                lolclient.accept_ready_check()
        session.post.assert_not_called()
        session.close.assert_called_once_with()

    def selection(self, player, action=None):
        return {"localPlayerCellId": 0, "myTeam": [{"cellId": 0, **player}], "actions": [[action]] if action else []}

    def team(self, selection):
        return lolclient.get_team_state(selection, "myTeam", lolclient.get_pick_actions(selection))

    def test_hover_intent_before_pick_turn(self):
        selection = self.selection({"championPickIntent": 103, "assignedPosition": "middle"})
        self.assertEqual(self.team(selection)[0], (0, 103, "HOVER", "MID", True))

    def test_active_pick_takes_priority_over_old_intent(self):
        selection = self.selection({"championPickIntent": 103}, {"actorCellId": 0, "type": "pick", "championId": 222, "completed": False})
        self.assertEqual(self.team(selection)[0][1:3], (222, "HOVER"))

    def test_trade_uses_current_champion_not_old_completed_action(self):
        selection = self.selection({"championId": 222}, {"actorCellId": 0, "type": "pick", "championId": 103, "completed": True})
        self.assertEqual(self.team(selection)[0][1:3], (222, "LOCKED"))

    def test_bans_never_become_picks(self):
        selection = self.selection({}, {"actorCellId": 0, "type": "ban", "championId": 103, "completed": False})
        self.assertEqual(self.team(selection)[0][1:3], (0, "OFFEN"))

    def test_snapshot_filters_empty_bans_and_normalizes_role(self):
        selection = self.selection({"championId": 103, "assignedPosition": "bottom"})
        selection.update(bans={"myTeamBans": [0, -1, 222]}, timer={"adjustedTimeLeftInPhase": 14500})
        state = app.draft_state(selection, TEST_CHAMPIONS)
        self.assertEqual(state["own_team"][0]["position"], "adc")
        self.assertEqual(state["timer"]["seconds"], 14)
        self.assertEqual([c["champion_id"] for c in state["bans"]["own"]], [222])

    def test_custom_lobby_places_local_team_first(self):
        lobby = {"localMember": {"puuid": "local"}, "gameConfig": {"isCustom": True, "gameMode": "ARAM",
                 "customTeam100": [{"isBot": True, "botChampionId": 103}],
                 "customTeam200": [{"puuid": "local", "firstPositionPreference": "TOP"}]}}
        state = app.lobby_state(lobby, TEST_CHAMPIONS)
        self.assertTrue(state["own_team"][0]["is_local"])
        self.assertEqual(state["enemy_team"][0]["champion_id"], 103)
        self.assertEqual(state["game_mode"], "ARAM")

    def test_monitor_disconnects_and_reconnects(self):
        state = app.LiveState()
        monitor = app.LcuMonitor(state)
        monitor.stop = Mock()
        monitor.stop.is_set.side_effect = [False, False, False, True]
        session = Mock()
        session.get.return_value.ok = False
        with patch.object(lolclient, "connect_to_lcu", return_value=(session, "https://127.0.0.1:1")) as connect, \
             patch.object(lolclient, "load_champion_details", return_value={}), \
             patch.object(lolclient, "get_playable_champions", return_value=[]), \
             patch.object(lolclient, "get_available_queues", return_value=[]), \
             patch.object(lolclient, "get_phase", side_effect=["None", requests.ConnectionError(), "None"]), \
             patch.object(state, "publish", wraps=state.publish) as publish:
            monitor.run()
        self.assertEqual(connect.call_count, 2)
        self.assertEqual([call.args[0]["connected"] for call in publish.call_args_list], [True, False, True])


class CacheAndWebTests(unittest.TestCase):
    def test_cache_deduplicates_parallel_requests_and_separates_roles(self):
        cache = app.BuildCache()
        entered, release = threading.Event(), threading.Event()
        def fetch(*args, **kwargs):
            entered.set()
            release.wait(2)
            return kwargs["position"]
        try:
            with patch.object(opgg, "get_champion_info", side_effect=fetch) as get:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(cache.get, "Ahri", "mid")
                    self.assertTrue(entered.wait(1))
                    second = pool.submit(cache.get, "Ahri", "mid")
                    release.set()
                    self.assertEqual(first.result(), second.result())
                self.assertEqual(get.call_count, 1)
                self.assertEqual(cache.get("Ahri", "support"), "support")
                self.assertEqual(get.call_count, 2)
        finally:
            cache.close()

    def test_cache_separates_tiers_and_game_modes(self):
        cache = app.BuildCache()
        try:
            with patch.object(
                opgg,
                "get_champion_info",
                side_effect=lambda *args, **kwargs: (kwargs["tier"], kwargs["mode"]),
            ) as get:
                self.assertEqual(
                    cache.get("Ahri", "mid", tier="gold_plus"),
                    ("gold_plus", "classic"),
                )
                self.assertEqual(
                    cache.get("Ahri", None, tier="gold_plus", mode="aram"),
                    ("gold_plus", "aram"),
                )
                self.assertEqual(get.call_count, 2)
        finally:
            cache.close()

    def test_unavailable_role_is_not_replaced_by_another_role(self):
        cache = app.BuildCache()
        try:
            with patch.object(
                opgg,
                "get_champion_info",
                side_effect=opgg.BuildNotFoundError("no top data"),
            ) as get:
                with self.assertRaises(opgg.BuildNotFoundError):
                    cache.get("Talon", "top")
                self.assertEqual(get.call_count, 1)
                self.assertEqual(get.call_args_list[0].kwargs["position"], "top")
        finally:
            cache.close()

    def test_network_error_does_not_trigger_fallback_request(self):
        cache = app.BuildCache()
        try:
            with patch.object(
                opgg, "get_champion_info", side_effect=opgg.OpggError("offline")
            ) as get:
                with self.assertRaises(opgg.OpggError):
                    cache.get("Talon", "top")
                self.assertEqual(get.call_count, 1)
        finally:
            cache.close()

    def test_failed_cache_entries_retry_after_backoff(self):
        cache = app.BuildCache()
        try:
            with patch.object(opgg, "get_champion_info", side_effect=[opgg.OpggError("down"), "recovered"]) as get:
                with self.assertRaises(opgg.OpggError):
                    cache.get("Ahri", "mid")
                key = ("ahri", "mid", "emerald_plus", "classic")
                created, future = cache.entries[key]
                cache.entries[key] = (created - 11, future)
                self.assertEqual(cache.get("Ahri", "mid"), "recovered")
                self.assertEqual(get.call_count, 2)
        finally:
            cache.close()

    def test_http_validation_and_error_response(self):
        builds = Mock(region="euw", tier="emerald_plus")
        builds.get.side_effect = opgg.OpggError("private internal details")
        client = app.create_app(builds=builds).test_client()
        self.assertEqual(client.get("/").status_code, 200)
        self.assertEqual(client.get("/api/build?champion=Ahri&position=invalid").status_code, 400)
        self.assertEqual(client.get("/api/build?champion=Ahri&tier=wood").status_code, 400)
        self.assertEqual(client.get("/api/build?champion=Ahri&mode=arena").status_code, 400)
        self.assertEqual(client.get("/api/build").status_code, 400)
        response = client.get("/api/build?champion=Ahri")
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private", response.get_data(as_text=True))
        self.assertEqual(client.post("/api/runes", json={"champion": "Ahri", "tier": {"bad": True}}).status_code, 400)

    def test_ready_check_can_only_be_accepted_in_ready_check_phase(self):
        builds = Mock(region="euw", tier="emerald_plus")
        state = app.LiveState()
        client = app.create_app(state=state, builds=builds).test_client()

        self.assertEqual(client.post("/api/ready-check/accept", json={}).status_code, 503)
        state.publish({**app.empty_state(), "connected": True, "phase": "Lobby"})
        self.assertEqual(client.post("/api/ready-check/accept", json={}).status_code, 409)

        state.publish({**app.empty_state(), "connected": True, "phase": "ReadyCheck"})
        with patch.object(app.lolclient, "accept_ready_check") as accept:
            response = client.post("/api/ready-check/accept", json={})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["accepted"])
        accept.assert_called_once_with()

    def test_ready_check_reports_expired_and_rejects_non_json(self):
        builds = Mock(region="euw", tier="emerald_plus")
        state = app.LiveState()
        state.publish({**app.empty_state(), "connected": True, "phase": "ReadyCheck"})
        client = app.create_app(state=state, builds=builds).test_client()
        self.assertEqual(client.post("/api/ready-check/accept").status_code, 415)
        with patch.object(
            app.lolclient,
            "accept_ready_check",
            side_effect=lolclient.ReadyCheckUnavailable("abgelaufen"),
        ):
            response = client.post("/api/ready-check/accept", json={})
        self.assertEqual(response.status_code, 409)
        self.assertIn("abgelaufen", response.json["error"])

    def test_lobby_controls_validate_and_forward_actions(self):
        builds = Mock(region="euw", tier="emerald_plus")
        state = app.LiveState()
        lobby = {
            **app.empty_state(), "connected": True, "phase": "Lobby",
            "can_manage_lobby": True, "show_position_selector": True,
            "available_queues": [{"id": 420, "name": "Solo/Duo"}],
        }
        state.publish(lobby)
        client = app.create_app(state=state, builds=builds).test_client()
        with patch.object(app.lolclient, "change_lobby_queue") as queue, \
             patch.object(app.lolclient, "set_position_preferences") as positions, \
             patch.object(app.lolclient, "start_matchmaking") as start:
            self.assertEqual(client.post("/api/lobby/queue", json={"queue_id": 999}).status_code, 400)
            self.assertEqual(client.post("/api/lobby/queue", json={"queue_id": 420}).status_code, 200)
            self.assertEqual(client.post("/api/lobby/positions", json={"first": "mid", "second": "mid"}).status_code, 400)
            self.assertEqual(client.post("/api/lobby/positions", json={"first": "mid", "second": "jungle"}).status_code, 200)
            self.assertEqual(client.post("/api/matchmaking/start", json={}).status_code, 200)
        queue.assert_called_once_with(420)
        positions.assert_called_once_with("MIDDLE", "JUNGLE")
        start.assert_called_once_with()

        state.publish({**lobby, "phase": "Matchmaking"})
        with patch.object(app.lolclient, "stop_matchmaking") as stop:
            self.assertEqual(client.post("/api/matchmaking/stop", json={}).status_code, 200)
        stop.assert_called_once_with()

    def test_lobby_can_be_created_from_none_phase_and_lan_qr_is_local(self):
        builds = Mock(region="euw", tier="emerald_plus")
        state = app.LiveState()
        state.publish({
            **app.empty_state(), "connected": True, "logged_in": True, "phase": "None",
            "available_queues": [{"id": 450, "name": "ARAM"}],
        })
        client = app.create_app(
            state=state, builds=builds, lan_url="http://192.168.1.20:5000"
        ).test_client()
        with patch.object(app.lolclient, "change_lobby_queue") as create:
            response = client.post("/api/lobby/queue", json={"queue_id": 450})
        self.assertEqual(response.status_code, 200)
        create.assert_called_once_with(450)
        qr = client.get("/api/lan-qr.svg")
        self.assertEqual(qr.status_code, 200)
        self.assertEqual(qr.mimetype, "image/svg+xml")
        self.assertIn(b"<svg", qr.data)

        page = client.get("/").get_data(as_text=True)
        self.assertIn('id="phone-button"', page)
        self.assertIn("http://192.168.1.20:5000", page)

    def test_local_network_url_uses_the_selected_port(self):
        probe = Mock()
        probe.__enter__ = Mock(return_value=probe)
        probe.__exit__ = Mock(return_value=False)
        probe.getsockname.return_value = ("192.168.1.42", 12345)
        with patch.object(app.socket, "socket", return_value=probe):
            self.assertEqual(app.local_network_url(5050), "http://192.168.1.42:5050")
        probe.connect.assert_called_once_with(("8.8.8.8", 80))

    def test_champion_select_route_hovers_and_locks_only_own_active_pick(self):
        builds = Mock(region="euw", tier="emerald_plus")
        state = app.LiveState()
        draft = {
            **app.empty_state(), "connected": True, "phase": "ChampSelect",
            "available_champions": [{"id": 103, "name": "Ahri"}],
            "pickable_champion_ids": [103],
            "pick_action": {"available": True, "active": True, "champion_id": 0},
        }
        state.publish(draft)
        client = app.create_app(state=state, builds=builds).test_client()
        with patch.object(app.lolclient, "select_champion") as select:
            hover = client.post("/api/champion-select", json={"champion_id": 103, "lock": False})
            lock = client.post("/api/champion-select", json={"champion_id": 103, "lock": True})
        self.assertEqual(hover.status_code, 200)
        self.assertEqual(lock.status_code, 200)
        self.assertEqual(select.call_args_list[0].kwargs, {"lock": False})
        self.assertEqual(select.call_args_list[1].kwargs, {"lock": True})

        state.publish({**draft, "pick_action": {"available": True, "active": False, "champion_id": 0}})
        self.assertEqual(client.post(
            "/api/champion-select", json={"champion_id": 103, "lock": True}
        ).status_code, 409)

    def test_missing_role_returns_specific_message(self):
        builds = Mock(region="euw", tier="emerald_plus")
        builds.get.side_effect = opgg.BuildNotFoundError("no role data")
        client = app.create_app(builds=builds).test_client()
        response = client.get("/api/build?champion=Talon&position=top")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json["kind"], "no_role_data")
        self.assertIn("Talon auf Top", response.json["error"])

    def test_import_runes_uses_server_side_build_and_returns_page(self):
        builds = Mock(region="euw", tier="emerald_plus")
        builds.get.return_value = SimpleNamespace(
            name="Ahri", position="mid", rune_builds=(sample_runes(),)
        )
        client = app.create_app(builds=builds).test_client()
        with patch.object(
            app.runeclient,
            "import_rune_page",
            return_value=runeclient.ImportResult(42, "Ahri Mid", True),
        ) as import_page:
            response = client.post(
                "/api/runes",
                json={"champion": "ahri", "position": "mid", "rune_index": 0},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["name"], "Ahri Mid")
        builds.get.assert_called_once_with(
            "ahri", "mid", tier="emerald_plus", mode="classic"
        )
        import_page.assert_called_once()

    def test_import_runes_validates_input_and_client_error(self):
        builds = Mock(region="euw", tier="emerald_plus")
        builds.get.return_value = SimpleNamespace(
            name="Ahri", position="mid", rune_builds=(sample_runes(),)
        )
        client = app.create_app(builds=builds).test_client()
        self.assertEqual(client.post("/api/runes", json=[]).status_code, 400)
        self.assertEqual(client.post(
            "/api/runes", json={"champion": "Ahri", "position": "mid", "rune_index": True}
        ).status_code, 400)
        with patch.object(
            app.runeclient,
            "import_rune_page",
            side_effect=runeclient.RuneImportError("Client nicht erreichbar"),
        ):
            response = client.post(
                "/api/runes",
                json={"champion": "Ahri", "position": "mid", "rune_index": 0},
            )
        self.assertEqual(response.status_code, 502)
        self.assertIn("Client nicht erreichbar", response.json["error"])

    def test_aram_rune_import_ignores_lane_and_uses_aram_build(self):
        builds = Mock(region="euw", tier="emerald_plus")
        builds.get.return_value = SimpleNamespace(
            name="Ahri", position="aram", rune_builds=(sample_runes(),)
        )
        client = app.create_app(builds=builds).test_client()
        with patch.object(
            app.runeclient,
            "import_rune_page",
            return_value=runeclient.ImportResult(42, "Ahri ARAM", False),
        ) as import_page:
            response = client.post("/api/runes", json={
                "champion": "ahri", "position": "aram", "tier": "gold_plus",
                "mode": "aram", "rune_index": 0,
            })
        self.assertEqual(response.status_code, 200)
        builds.get.assert_called_once_with(
            "ahri", None, tier="gold_plus", mode="aram"
        )
        self.assertEqual(import_page.call_args.args[2], "aram")

    def test_event_stream_pushes_changes(self):
        state = app.LiveState()
        events = state.events()
        self.assertIn('"phase": "Offline"', next(events))
        state.publish({**app.empty_state(), "connected": True, "phase": "ChampSelect"})
        self.assertIn('"phase": "ChampSelect"', next(events))
        events.close()


if __name__ == "__main__":
    unittest.main()
