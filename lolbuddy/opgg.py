"""Kleiner Client fuer oeffentliche Champion-Daten von OP.GG.

OP.GG bietet fuer Champion-Builds keine dokumentierte, oeffentliche API an.
Die Champion-Seiten enthalten die benoetigten Daten jedoch bereits im vom
Server gelieferten HTML. Dieses Modul liest einen kleinen, stabilen Ausschnitt
daraus aus und kapselt die Implementierungsdetails hinter ``get_champion_info``.

Beispiel::

    info = get_champion_info("Ahri", position="mid")
    print(info.win_rate)
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from html import unescape
from typing import Any
from urllib.parse import urlencode

import requests

BASE_URL = "https://op.gg/lol/champions"
ARAM_BASE_URL = "https://op.gg/lol/modes/aram"
DEFAULT_TIMEOUT = 15.0
VALID_POSITIONS = {"top", "jungle", "mid", "adc", "support"}
VALID_MODES = {"classic", "aram"}
VALID_TIERS = {
    "all", "challenger", "grandmaster", "master_plus", "master",
    "diamond_plus", "diamond", "emerald_plus", "emerald",
    "platinum_plus", "platinum", "gold_plus", "gold", "silver",
    "bronze", "iron",
}


class OpggError(RuntimeError):
    """Basisklasse fuer Fehler beim OP.GG-Abruf."""


class ChampionNotFoundError(OpggError):
    """Der angefragte Champion wurde auf OP.GG nicht gefunden."""


class BuildNotFoundError(OpggError):
    """OP.GG hat für die angefragte Champion-/Rollen-Kombination keine Daten."""


class OpggResponseError(OpggError):
    """OP.GG lieferte keine erwartungsgemaesse Antwort."""


@dataclass(frozen=True)
class Rune:
    id: int
    name: str
    image_url: str


@dataclass(frozen=True)
class RuneBuild:
    primary_style: str
    secondary_style: str
    keystone: str
    runes: tuple[str, ...]
    shards: tuple[str, ...]
    pick_rate: float
    win_rate: float
    games: int
    primary_runes: tuple[Rune, ...] = ()
    secondary_runes: tuple[Rune, ...] = ()
    shard_details: tuple[Rune, ...] = ()
    primary_style_image: str = ""
    secondary_style_image: str = ""
    primary_style_id: int = 0
    secondary_style_id: int = 0
    selected_perk_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class Item:
    id: int
    name: str
    image_url: str
    count: int = 1


@dataclass(frozen=True)
class ItemBuild:
    items: tuple[Item, ...]
    pick_rate: float | None
    win_rate: float | None


@dataclass(frozen=True)
class SummonerSpell:
    id: int
    name: str
    image_url: str


@dataclass(frozen=True)
class SpellBuild:
    spells: tuple[SummonerSpell, ...]
    pick_rate: float | None
    win_rate: float | None


@dataclass(frozen=True)
class ChampionInfo:
    name: str
    champion: str
    position: str
    patch: str
    tier: int | None
    win_rate: float
    pick_rate: float
    ban_rate: float
    image_url: str
    rune_builds: tuple[RuneBuild, ...]
    starter_builds: tuple[ItemBuild, ...]
    boot_builds: tuple[ItemBuild, ...]
    core_builds: tuple[ItemBuild, ...]
    source_url: str
    region: str
    rank_tier: str
    spell_builds: tuple[SpellBuild, ...] = ()
    later_builds: tuple[ItemBuild, ...] = ()
    game_mode: str = "classic"

    def to_dict(self) -> dict[str, Any]:
        """Liefert eine JSON-kompatible Darstellung."""

        return asdict(self)


def _slugify_champion(name: str) -> str:
    """Wandelt einen Championnamen in den von OP.GG verwendeten Slug um."""

    normalized = unicodedata.normalize("NFKD", name.strip())
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]", "", ascii_name.lower())

    if not slug:
        raise ValueError("Der Championname darf nicht leer sein.")

    # OP.GG/Riot-Sonderfaelle, deren Slug nicht rein mechanisch entsteht.
    aliases = {
        "belveth": "belveth",
        "chogath": "chogath",
        "drmundo": "drmundo",
        "jarvaniv": "jarvaniv",
        "kaisa": "kaisa",
        "khazix": "khazix",
        "kogmaw": "kogmaw",
        "leesin": "leesin",
        "masteryi": "masteryi",
        "missfortune": "missfortune",
        "monkeyking": "monkeyking",
        "nunuandwillump": "nunu",
        "nunuwillump": "nunu",
        "reksai": "reksai",
        "renataglasc": "renata",
        "tahmkench": "tahmkench",
        "twistedfate": "twistedfate",
        "velkoz": "velkoz",
        "wukong": "monkeyking",
        "xinzhao": "xinzhao",
    }
    return aliases.get(slug, slug)


def _extract(pattern: str, text: str, field: str) -> str:
    match = re.search(pattern, text)
    if match is None:
        raise OpggResponseError(
            f"Das Feld '{field}' wurde in der OP.GG-Antwort nicht gefunden. "
            "Moeglicherweise hat OP.GG die Seite geaendert."
        )
    return unescape(match.group(1))


def _extract_optional(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    return unescape(match.group(1)) if match else None


def _decode_embedded_text(value: str | None) -> str | None:
    if value is None:
        return None
    # Die Werte liegen innerhalb eines JSON-Strings in einem script-Tag.
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value.replace(r"\n", "\n").replace(r"\"", '"')


class _FlightData:
    """Liest Next.js-Daten inklusive ausgelagerter Tabellenzellen.

    Ein Textfenster nach einer Tabellenzeile ist nicht ausreichend: OP.GG
    streamt z. B. die Boots-Statistiken als separate $L-Referenzen.
    """

    def __init__(self, html: str):
        decoder = json.JSONDecoder()
        chunks = re.findall(
            r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', html
        )
        self.text = "".join(json.loads(chunk) for chunk in chunks)
        self.records = {}
        for match in re.finditer(r"(?:^|\n)([0-9a-f]+):", self.text):
            try:
                value, _ = decoder.raw_decode(self.text, match.end())
                self.records[match.group(1)] = value
            except json.JSONDecodeError:
                continue

    def extract(self, key: str) -> Any:
        match = re.search(rf'"{re.escape(key)}":(?=[\[{{])', self.text)
        if not match:
            return None
        try:
            return json.JSONDecoder().raw_decode(self.text, match.end())[0]
        except json.JSONDecodeError as error:
            raise OpggResponseError(f"OP.GG-Datenblock '{key}' ist unvollstaendig.") from error

    def walk(self, value, seen=frozenset()):
        if isinstance(value, str) and re.fullmatch(r"\$L?[0-9a-f]+", value):
            key = value[2:] if value.startswith("$L") else value[1:]
            if key not in seen and key in self.records:
                yield from self.walk(self.records[key], seen | {key})
        elif isinstance(value, dict):
            yield value
            for child in value.values():
                yield from self.walk(child, seen)
        elif isinstance(value, list):
            for child in value:
                yield from self.walk(child, seen)

    def rows(self, prefix: str, element: str = "tr"):
        pattern = rf'\["\$","{element}","{re.escape(prefix)}_\d+",'
        for match in re.finditer(pattern, self.text):
            try:
                yield json.JSONDecoder().raw_decode(self.text, match.start())[0]
            except json.JSONDecodeError:
                continue


def _extract_embedded_json(html: str, key: str) -> Any:
    return _FlightData(html).extract(key)


def _parse_rune_builds(data: _FlightData) -> tuple[RuneBuild, ...]:
    rune_pages = data.extract("rune_pages") or []
    result: list[RuneBuild] = []

    for page in rune_pages[:2]:
        builds = page.get("builds") or []
        if not builds:
            continue
        build = builds[0]
        import_data = page.get("importClientData") or {}

        selected_runes = tuple(
            rune["name"]
            for group_name in ("main_runes", "sub_runes")
            for row in build.get(group_name, [])
            for rune in row
            if rune.get("isActive")
        )
        selected_shards = tuple(
            shard["name"]
            for row in build.get("shards", [])
            for shard in row
            if shard.get("isActive")
        )

        def details(group):
            return tuple(
                Rune(id=int(rune["id"]), name=rune["name"], image_url=rune["image_url"])
                for row in build.get(group, []) for rune in row if rune.get("isActive")
            )

        result.append(
            RuneBuild(
                primary_style=page["primary_perk_style"]["name"],
                secondary_style=page["perk_sub_style"]["name"],
                keystone=page["primary_rune"]["name"],
                runes=selected_runes,
                shards=selected_shards,
                pick_rate=float(page.get("pick_rate", 0)) * 100,
                win_rate=float(page.get("win_rate", 0)) * 100,
                games=int(page.get("play", 0)),
                primary_runes=details("main_runes"),
                secondary_runes=details("sub_runes"),
                shard_details=details("shards"),
                primary_style_image=page["primary_perk_style"].get("image_url", ""),
                secondary_style_image=page["perk_sub_style"].get("image_url", ""),
                primary_style_id=int(
                    import_data.get("primaryStyleId")
                    or page["primary_perk_style"].get("id", 0)
                ),
                secondary_style_id=int(
                    import_data.get("subStyleId")
                    or page["perk_sub_style"].get("id", 0)
                ),
                selected_perk_ids=tuple(
                    int(perk_id)
                    for perk_id in import_data.get("selectedPerkIds", ())
                ),
            )
        )

    return tuple(result)


def _rates(data: _FlightData, row) -> tuple[float | None, float | None]:
    values = [
        float(node["children"].removesuffix("%"))
        for node in data.walk(row)
        if isinstance(node.get("children"), str)
        and re.fullmatch(r"[\d.]+%", node["children"])
    ]
    if len(values) == 1:
        return None, values[0]
    return (values[0] if values else None, values[1] if len(values) > 1 else None)


def _assets(data: _FlightData, row, kind: str):
    for node in data.walk(row):
        if node.get("metaType") != kind:
            continue
        for child in data.walk(node.get("children")):
            if child.get("src") and child.get("alt"):
                yield int(node["metaId"]), child["alt"], child["src"]
                break


def _parse_item_builds(data: _FlightData, row_prefix: str, *, limit: int):
    result = []
    for row in list(data.rows(row_prefix))[:limit]:
        quantities = {}
        for node in data.walk(row):
            if node.get("className") == "relative":
                assets = list(_assets(data, node, "item"))
                counts = [n["children"] for n in data.walk(node)
                          if type(n.get("children")) is int]
                if assets and counts:
                    quantities[assets[0][0]] = counts[0]
        items = tuple(Item(i, name, image, quantities.get(i, 1))
                      for i, name, image in _assets(data, row, "item"))
        if items:
            result.append(ItemBuild(items, *_rates(data, row)))
    return tuple(result)


def _parse_spell_builds(
    data: _FlightData, row_prefix: str = "spells_table"
) -> tuple[SpellBuild, ...]:
    result = []
    for row in data.rows(row_prefix, r"[^\"]+"):
        spells = tuple(SummonerSpell(*asset) for asset in _assets(data, row, "spell"))
        if spells:
            result.append(SpellBuild(spells, *_rates(data, row)))
    return tuple(result[:2])


def _parse_champion_page(
    html: str,
    *,
    source_url: str,
    champion_slug: str,
    region: str,
    rank_tier: str,
    game_mode: str = "classic",
) -> ChampionInfo:
    data = _FlightData(html)
    if game_mode == "aram":
        champion_meta = next(
            (
                champion for champion in (data.extract("champions") or [])
                if champion.get("key") == champion_slug
            ),
            None,
        )
        if champion_meta is None:
            raise ChampionNotFoundError(
                f"Champion '{champion_slug}' wurde auf OP.GG nicht gefunden."
            )
        title = champion_meta["name"]
        image_url = champion_meta["image_url"]
        patch_match = re.search(r"/lol/([^/]+)/champion/", image_url)
        patch = patch_match.group(1).rsplit(".", 1)[0] if patch_match else "-"
        position = "aram"
        tier_value = int(champion_meta["tier"]) if champion_meta.get("tier") else None
        win_rate = float(champion_meta.get("win_rate", 0)) * 100
        pick_rate = float(champion_meta.get("pick_rate", 0)) * 100
        ban_rate = 0.0
    else:
        title = _extract_optional(r"<title>([^<]+?) Build(?: -| for)", html)
        if title is None:
            raise ChampionNotFoundError(
                f"Champion '{champion_slug}' wurde auf OP.GG nicht gefunden."
            )

        # Dieser Block speist direkt OP.GGs Win-/Pick-/Ban-Anzeige im Seitenkopf.
        rate_match = re.search(
            r'\\"rateWin\\":(?P<win>[\d.]+),'
            r'\\"ratePick\\":(?P<pick>[\d.]+),'
            r'\\"rateBan\\":(?P<ban>[\d.]+).*?'
            r'\\"fallbackPosition\\":\\"(?P<position>[^\\"]+)\\".*?'
            r'\\"version\\":\\"(?P<patch>[^\\"]+)\\"',
            html,
        )
        if rate_match is None:
            raise BuildNotFoundError(
                "OP.GG hat für diese Champion-/Rollen-Kombination keine Build-Daten."
            )
        image_url = _extract(
            r'\\"src\\":\\"([^\\"]+/champion/[^\\"]+\.png)\\",'
            r'\\"width\\":80,\\"height\\":80,\\"alt\\":',
            html,
            "image_url",
        )
        tier_text = _extract_optional(r'\\"children\\":\\"(\d+) Tier\\"', html)
        patch = rate_match.group("patch")
        position = rate_match.group("position")
        tier_value = int(tier_text) if tier_text else None
        win_rate = float(rate_match.group("win"))
        pick_rate = float(rate_match.group("pick"))
        ban_rate = float(rate_match.group("ban"))

    core_builds = _parse_item_builds(data, "core_items", limit=2)
    core_ids = {item.id for item in core_builds[0].items} if core_builds else set()
    later_builds = tuple(
        build for build in _parse_item_builds(data, "depth_4_item", limit=5)
        if not any(item.id in core_ids for item in build.items)
    )[:4]

    return ChampionInfo(
        name=title,
        champion=champion_slug,
        position=position,
        patch=patch,
        tier=tier_value,
        win_rate=win_rate,
        pick_rate=pick_rate,
        ban_rate=ban_rate,
        image_url=image_url,
        rune_builds=_parse_rune_builds(data),
        starter_builds=_parse_item_builds(data, "starter_items", limit=1),
        boot_builds=_parse_item_builds(data, "boots", limit=1),
        core_builds=core_builds,
        source_url=source_url,
        region=region,
        rank_tier=rank_tier,
        spell_builds=_parse_spell_builds(
            data, "spell_table" if game_mode == "aram" else "spells_table"
        ),
        later_builds=later_builds,
        game_mode=game_mode,
    )


def get_champion_info(
    champion: str,
    *,
    position: str | None = None,
    region: str = "euw",
    tier: str = "emerald_plus",
    mode: str = "classic",
    timeout: float = DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
) -> ChampionInfo:
    """Ruft aktuelle Build-Statistiken eines Champions von OP.GG ab.

    ``position`` kann ``top``, ``jungle``, ``mid``, ``adc`` oder ``support``
    sein. Ohne Position verwendet OP.GG die meistgespielte Rolle.
    """

    slug = _slugify_champion(champion)
    normalized_mode = mode.lower()
    if normalized_mode not in VALID_MODES:
        raise ValueError(f"Ungueltiger Modus: {mode}")
    if tier not in VALID_TIERS:
        raise ValueError(f"Ungueltige Tier: {tier}")
    normalized_position = position.lower() if position else None
    if normalized_position and normalized_position not in VALID_POSITIONS:
        allowed = ", ".join(sorted(VALID_POSITIONS))
        raise ValueError(f"Ungueltige Position. Erlaubt sind: {allowed}")

    if normalized_mode == "aram":
        normalized_position = None
        request_region = "global"
        request_tier = "all"
        path = f"{ARAM_BASE_URL}/{slug}/build"
        url = f"{path}?{urlencode({'region': request_region})}"
    else:
        request_region = region
        request_tier = tier
        path = f"{BASE_URL}/{slug}/build"
        if normalized_position:
            path += f"/{normalized_position}"
        url = f"{path}?{urlencode({'region': request_region, 'tier': request_tier})}"

    http = session or requests.Session()
    try:
        response = http.get(
            url,
            headers={
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": "lolbuddy/1.0 (personal OP.GG champion lookup)",
            },
            timeout=timeout,
        )
        if response.status_code == 404:
            raise ChampionNotFoundError(
                f"Champion '{champion}' wurde auf OP.GG nicht gefunden."
            )
        response.raise_for_status()
    except requests.RequestException as error:
        raise OpggError(f"OP.GG konnte nicht abgerufen werden: {error}") from error
    finally:
        if session is None:
            http.close()

    return _parse_champion_page(
        response.text,
        source_url=response.url,
        champion_slug=slug,
        region=request_region,
        rank_tier=request_tier,
        game_mode=normalized_mode,
    )
