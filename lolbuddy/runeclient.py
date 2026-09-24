"""OP.GG-Runen als verwaltete Runenseite in den League-Client importieren.

Das Modul merkt sich die ID genau einer von ihm erstellten Seite. Andere
Runenseiten werden weder verändert noch gelöscht.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

import requests

from . import lolclient, opgg


LEGACY_PAGE_PREFIX = "LOLBUDDY |"
MAX_PAGE_NAME_LENGTH = 25


def _default_state_path() -> Path:
    """Return a persistent, writable state path for source and frozen builds."""

    configured = os.environ.get("LOLBUDDY_DATA_DIR")
    if configured:
        data_dir = Path(configured).expanduser()
    elif os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        data_dir = Path(os.environ["LOCALAPPDATA"]) / "lolbuddy"
    else:
        data_dir = Path.home() / ".local" / "state" / "lolbuddy"
    return data_dir / "rune-page.json"


STATE_PATH = _default_state_path()
ROLE_NAMES = {
    "top": "Top",
    "jungle": "Jungle",
    "mid": "Mid",
    "adc": "ADC",
    "support": "Support",
    "aram": "ARAM",
}


class RuneImportError(RuntimeError):
    """Eine Runenseite konnte nicht sicher in den Client importiert werden."""


@dataclass(frozen=True)
class ImportResult:
    id: int
    name: str
    created: bool

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "created": self.created}


def make_page_name(champion: str, position: str | None) -> str:
    """Erzeugt einen kurzen, im Client gut erkennbaren Seitennamen."""

    role = ROLE_NAMES.get(position or "", "")
    suffix = f" {role}" if role else ""
    available = MAX_PAGE_NAME_LENGTH - len(suffix)
    safe_champion = " ".join(champion.strip().split()) or "Champion"
    safe_champion = safe_champion[: max(1, available)].rstrip()
    return f"{safe_champion}{suffix}"[:MAX_PAGE_NAME_LENGTH]


def _load_managed_id(state_path: Path | None) -> int | None:
    if state_path is None:
        return None
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
        return int(value["page_id"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _save_managed_id(state_path: Path | None, page_id: int) -> None:
    if state_path is None:
        return
    temporary = state_path.with_suffix(state_path.suffix + ".tmp")
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps({"page_id": page_id}, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(state_path)
    except OSError as error:
        raise RuneImportError(
            "Die ID der verwalteten Runenseite konnte nicht gespeichert werden."
        ) from error


def _payload(runes: opgg.RuneBuild, champion: str, position: str | None):
    perk_ids = tuple(int(perk_id) for perk_id in runes.selected_perk_ids)
    if not perk_ids:
        perk_ids = tuple(
            rune.id
            for rune in (
                *runes.primary_runes,
                *runes.secondary_runes,
                *runes.shard_details,
            )
        )
    if len(perk_ids) != 9:
        raise RuneImportError(
            f"Die OP.GG-Runenseite enthält {len(perk_ids)} statt 9 Runen."
        )
    if runes.primary_style_id <= 0 or runes.secondary_style_id <= 0:
        raise RuneImportError("Die Runenpfade von OP.GG sind unvollständig.")
    return {
        "name": make_page_name(champion, position),
        "primaryStyleId": runes.primary_style_id,
        "subStyleId": runes.secondary_style_id,
        "selectedPerkIds": list(perk_ids),
        "current": True,
    }


def _request_json(session, method: str, url: str, **kwargs):
    try:
        response = session.request(method, url, timeout=5, **kwargs)
    except requests.RequestException as error:
        raise RuneImportError(f"League-Client nicht erreichbar: {error}") from error
    if not response.ok:
        detail = ""
        try:
            body = response.json()
            detail = body.get("message") or body.get("errorCode") or ""
        except (ValueError, AttributeError):
            pass
        if response.status_code in {400, 409, 500}:
            message = (
                "Der League-Client konnte die Runenseite nicht anlegen. "
                "Möglicherweise ist die maximale Anzahl eigener Seiten erreicht."
            )
        else:
            message = f"League-Client antwortete mit HTTP {response.status_code}."
        if detail:
            message += f" ({detail})"
        raise RuneImportError(message)
    try:
        return response.json()
    except ValueError as error:
        raise RuneImportError("Der League-Client lieferte keine gültige Antwort.") from error


def import_rune_page(
    runes: opgg.RuneBuild,
    champion: str,
    position: str | None,
    *,
    session=None,
    base_url: str | None = None,
    state_path: Path | None = STATE_PATH,
) -> ImportResult:
    """Erstellt oder aktualisiert ausschließlich lolbuddys eigene Runenseite."""

    owns_connection = session is None
    if owns_connection:
        try:
            session, base_url = lolclient.connect_to_lcu()
        except (FileNotFoundError, OSError, RuntimeError) as error:
            raise RuneImportError(
                "League-Client wurde nicht gefunden. Bitte League öffnen und einloggen."
            ) from error
    if not base_url:
        if owns_connection:
            session.close()
        raise RuneImportError("Die Adresse des League-Clients fehlt.")

    try:
        payload = _payload(runes, champion, position)
        pages = _request_json(session, "GET", f"{base_url}/lol-perks/v1/pages")
        managed_id = _load_managed_id(state_path)
        managed = next(
            (
                page
                for page in pages
                if (
                    page.get("id") == managed_id
                    or str(page.get("name", "")).startswith(LEGACY_PAGE_PREFIX)
                )
                and page.get("isEditable", True)
            ),
            None,
        )
        if managed is None:
            page = _request_json(
                session,
                "POST",
                f"{base_url}/lol-perks/v1/pages",
                json=payload,
            )
            created = True
        else:
            page_id = int(managed["id"])
            page = _request_json(
                session,
                "PUT",
                f"{base_url}/lol-perks/v1/pages/{page_id}",
                json={**payload, "id": page_id},
            )
            created = False
        page_id = int(page.get("id", managed.get("id") if managed else 0))
        _save_managed_id(state_path, page_id)
        return ImportResult(
            id=page_id,
            name=str(page.get("name") or payload["name"]),
            created=created,
        )
    finally:
        if owns_connection:
            session.close()
