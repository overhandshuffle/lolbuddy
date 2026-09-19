"use strict";

const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const roles = {top:"Top", jungle:"Jungle", mid:"Mid", adc:"Bot / ADC", support:"Support", fill:"Fill", aram:"ARAM"};
const positionChoices = [["top","Top"],["jungle","Jungle"],["mid","Mid"],["adc","Bot / ADC"],["support","Support"],["fill","Fill"]];
const phases = {
  Offline: ["Deine Lobby", "Öffne League of Legends. Die Verbindung entsteht automatisch."],
  None: ["Bereit für die nächste Runde", "Starte eine Lobby im League-Client."],
  Lobby: ["Deine Lobby", "Sobald du einen Champion hoverst, erscheint hier dein Build."],
  Matchmaking: ["Spielsuche läuft", "Deine Lobby ist bereit. Gleich geht’s in die Champion-Auswahl."],
  ReadyCheck: ["Spiel gefunden", "Bestätige das Match im League-Client."],
  ChampSelect: ["Champion-Auswahl", "Dein Draft in Echtzeit. Dein Build passend zum Pick."],
  GameStart: ["Das Spiel startet", "Dein Build bleibt während des Spiels geöffnet."],
  InProgress: ["Ab in die Kluft", "Dein Build bleibt während des Spiels geöffnet."],
  Reconnect: ["Zurück ins Spiel", "Dein letzter Draft und Build bleiben hier verfügbar."],
  WaitingForStats: ["Spiel beendet", "Dein Build bleibt bis zur nächsten Lobby geöffnet."],
  PreEndOfGame: ["Spiel beendet", "Dein Build bleibt bis zur nächsten Lobby geöffnet."],
  EndOfGame: ["Spiel beendet", "Bereit für die nächste Runde?"],
};
let state = null;
let preview = null;
let pinned = null;
let restoringFocus = false;
let localSignature = "";
let teamsSignature = "";
let activeKey = "";
let requestVersion = 0;
let hoverTimer;
let readyCheckActive = false;
let readyCheckAccepted = false;
let selectedChampionId = 0;
let championPickPending = false;
let pickerSignature = "";
let buildSection = "items";

const compactLayout = window.matchMedia("(max-width: 760px)");
const stackedBuild = window.matchMedia("(max-width: 1100px)");
$("#draft-details").open = !compactLayout.matches;
compactLayout.addEventListener("change", event => { $("#draft-details").open = !event.matches; });

function updateBuildSections() {
  document.querySelectorAll("[data-build-section]").forEach(button => {
    const selected = button.dataset.buildSection === buildSection;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  document.querySelectorAll("[data-build-pane]").forEach(pane => {
    pane.hidden = stackedBuild.matches && pane.dataset.buildPane !== buildSection;
    if (stackedBuild.matches) {
      pane.setAttribute("role", "tabpanel");
      pane.setAttribute("aria-labelledby", `${pane.dataset.buildPane}-tab`);
      pane.tabIndex = 0;
    } else {
      pane.removeAttribute("role");
      pane.removeAttribute("aria-labelledby");
      pane.removeAttribute("tabindex");
    }
  });
}
stackedBuild.addEventListener("change", updateBuildSections);

function updateDialogViewport() {
  document.documentElement.style.setProperty("--dialog-viewport", `${window.visualViewport?.height || window.innerHeight}px`);
}
updateDialogViewport();
window.visualViewport?.addEventListener("resize", updateDialogViewport);
window.addEventListener("resize", updateDialogViewport);

function image(url, name, className = "") {
  // All asset URLs must come from OP.GG's image host.
  if (!/^https:\/\/opgg-static\.akamaized\.net\//.test(url || "")) return "";
  return `<img src="${esc(url)}" alt="${esc(name)}" class="${className}" decoding="async" referrerpolicy="no-referrer">`;
}
document.addEventListener("error", (event) => {
  if (event.target instanceof HTMLImageElement) event.target.classList.add("image-missing");
}, true);

const rate = (number) => Number.isFinite(number) ? `${number.toLocaleString("de-DE", {minimumFractionDigits:1, maximumFractionDigits:1})} %` : "–";
const localPlayer = () => state?.own_team.find((player) => player.is_local) || null;
const currentMode = () => /ARAM/i.test(`${state?.game_mode || ""} ${state?.queue || ""}`) ? "aram" : "classic";
const playerKey = (player) => player ? `${player.team || "own"}:${player.cell_id}` : "";
function allPlayers() {
  return [...(state?.own_team || []).map(p => ({...p, team:"own"})), ...(state?.enemy_team || []).map(p => ({...p, team:"enemy"}))];
}
function findPlayer(key) { return allPlayers().find(p => playerKey(p) === key); }

function queueTime(seconds) {
  const total = Math.max(0, Number(seconds) || 0);
  return `${Math.floor(total / 60)}:${String(Math.floor(total % 60)).padStart(2, "0")}`;
}

function queueOptions() {
  return (state.available_queues || []).map(queue => {
    const detail = queue.description && queue.description !== queue.name ? ` · ${queue.description}` : "";
    return `<option value="${queue.id}" data-positions="${queue.positions ? "true" : "false"}">${esc(queue.name + detail)}</option>`;
  }).join("");
}

function renderPregame() {
  const searching = state.phase === "Matchmaking";
  const party = state.party || [];
  $("#pregame-eyebrow").textContent = searching ? "SPIELSUCHE" : "DEINE GRUPPE";
  $("#pregame-title").textContent = state.queue || "Spielmodus ausgewählt";
  $("#pregame-description").textContent = state.queue_description || (currentMode() === "aram" ? "ARAM" : "Kluft der Beschwörer");
  $("#queue-state").classList.toggle("searching", searching);
  $("#queue-status").textContent = searching ? "Spielsuche läuft" : "Bereit zur Spielsuche";
  const estimate = Number(state.matchmaking?.estimated_seconds) || 0;
  $("#queue-hint").textContent = searching
    ? estimate > 0 ? `Geschätzte Wartezeit ${queueTime(estimate)}` : "Suche nach passenden Spielern …"
    : "Starte die Suche im League-Client.";
  const timer = $("#queue-time");
  timer.hidden = !searching;
  timer.textContent = queueTime(state.matchmaking?.elapsed_seconds);
  $("#party-count").textContent = `${party.length} ${party.length === 1 ? "Spieler" : "Spieler"}`;
  $("#party-list").innerHTML = party.length ? party.map(member => {
    const initial = (member.name || "?").trim().charAt(0).toUpperCase();
    const role = roles[member.position] || "Rolle offen";
    return `<div class="party-member"><span class="party-avatar" aria-hidden="true">${esc(initial)}</span><span class="party-copy"><strong>${esc(member.name)}</strong><small>${esc(role)}${member.is_leader ? " · Gruppenleiter" : ""}</small></span>${member.is_local ? '<span class="party-you">DU</span>' : ""}</div>`;
  }).join("") : '<p class="party-empty">Gruppenmitglieder werden geladen …</p>';

  const queueSelect = $("#queue-select");
  queueSelect.innerHTML = queueOptions();
  queueSelect.value = String(state.queue_id || "");
  queueSelect.disabled = searching;
  $("#queue-control-row").hidden = !state.can_manage_lobby;
  $("#apply-queue").disabled = searching;

  const positionOptions = positionChoices.map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
  for (const [selector, value] of [["#first-position", state.local_positions?.first], ["#second-position", state.local_positions?.second]]) {
    const select = $(selector);
    select.innerHTML = positionOptions;
    select.value = value || "fill";
    select.disabled = searching;
  }
  $("#positions-row").hidden = !state.show_position_selector;
  $("#apply-positions").disabled = searching;

  const matchmakingButton = $("#matchmaking-button");
  matchmakingButton.hidden = !state.can_manage_lobby;
  matchmakingButton.disabled = false;
  matchmakingButton.textContent = searching ? "Spielsuche abbrechen" : "Spielsuche starten";
  matchmakingButton.classList.toggle("cancel", searching);
}

async function lobbyAction(url, body, button, pendingText, successText, status = $("#lobby-action-status")) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = pendingText;
  status.textContent = "";
  status.classList.remove("error");
  try {
    const response = await fetch(url, {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Der League-Client hat die Aktion abgelehnt.");
    status.textContent = successText;
  } catch (error) {
    status.textContent = error.message;
    status.classList.add("error");
  } finally {
    button.disabled = false;
    if (button.textContent === pendingText) button.textContent = original;
  }
}

function renderIdleControls(offline) {
  const controls = $("#create-lobby");
  controls.hidden = offline;
  if (offline) return;
  const select = $("#create-queue-select");
  const previous = select.value;
  select.innerHTML = queueOptions();
  if (previous && [...select.options].some(option => option.value === previous)) select.value = previous;
  const available = select.options.length > 0;
  select.disabled = !available;
  $("#create-lobby-button").disabled = !available;
}

$("#queue-select").addEventListener("change", () => {
  const positions = $("#queue-select").selectedOptions[0]?.dataset.positions === "true";
  $("#positions-row").hidden = !positions;
});

$("#apply-queue").addEventListener("click", () => lobbyAction(
  "/api/lobby/queue",
  {queue_id:Number($("#queue-select").value)},
  $("#apply-queue"),
  "Wird geändert …",
  "Spielmodus geändert.",
));

$("#apply-positions").addEventListener("click", () => lobbyAction(
  "/api/lobby/positions",
  {first:$("#first-position").value, second:$("#second-position").value},
  $("#apply-positions"),
  "Wird gespeichert …",
  "Positionen gespeichert.",
));

$("#matchmaking-button").addEventListener("click", () => {
  const searching = state?.phase === "Matchmaking";
  return lobbyAction(
    searching ? "/api/matchmaking/stop" : "/api/matchmaking/start",
    {},
    $("#matchmaking-button"),
    searching ? "Wird abgebrochen …" : "Suche wird gestartet …",
    searching ? "Spielsuche abgebrochen." : "Spielsuche gestartet.",
  );
});

$("#create-lobby-button").addEventListener("click", () => lobbyAction(
  "/api/lobby/queue",
  {queue_id:Number($("#create-queue-select").value)},
  $("#create-lobby-button"),
  "Lobby wird erstellt …",
  "Lobby erstellt.",
  $("#create-lobby-status"),
));

const phoneButton = $("#phone-button");
const phoneDialog = $("#phone-dialog");
phoneButton?.addEventListener("click", () => phoneDialog.showModal());
$("#phone-dialog-close")?.addEventListener("click", () => phoneDialog.close());
phoneDialog?.addEventListener("click", event => {
  if (event.target === phoneDialog) phoneDialog.close();
});

function pickerChampions() {
  const pickable = new Set(state?.pickable_champion_ids || []);
  const query = $("#champion-search").value.trim().toLocaleLowerCase("de-DE");
  return (state?.available_champions || []).filter(champion =>
    (!pickable.size || pickable.has(champion.id))
    && (!query || `${champion.name} ${champion.alias}`.toLocaleLowerCase("de-DE").includes(query))
  );
}

function renderChampionPicker() {
  const champions = pickerChampions();
  $("#champion-result-count").textContent = `${champions.length} ${champions.length === 1 ? "Champion" : "Champions"}`;
  $("#pick-turn-label").textContent = state?.pick_action?.active ? "Champion auswählen" : "Pick vormerken";
  const signature = JSON.stringify(champions);
  const grid = $("#champion-grid");
  if (signature !== pickerSignature) {
    pickerSignature = signature;
    const focused = document.activeElement?.dataset?.championId;
    grid.innerHTML = champions.map(champion => {
      const url = `https://opgg-static.akamaized.net/meta/images/lol/latest/champion/${encodeURIComponent(champion.alias)}.png`;
      return `<button class="champion-option ${champion.id === selectedChampionId ? "selected" : ""}" type="button" data-champion-id="${champion.id}" title="${esc(champion.name)}">
        <img src="${url}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer"><span>${esc(champion.name)}</span>${champion.free_to_play && !champion.owned ? '<small>FREI</small>' : ""}
      </button>`;
    }).join("") || '<p class="champion-empty">Kein passender Champion gefunden.</p>';
    if (focused) {
      const replacement = [...grid.querySelectorAll("[data-champion-id]")].find(button => button.dataset.championId === focused);
      (replacement || $("#champion-search")).focus({preventScroll:true});
    }
  }
  grid.setAttribute("aria-busy", String(championPickPending));
  grid.querySelectorAll("[data-champion-id]").forEach(button => {
    const selected = Number(button.dataset.championId) === selectedChampionId;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
    button.setAttribute("aria-disabled", String(championPickPending));
  });
  const selected = (state?.available_champions || []).find(champion => champion.id === selectedChampionId);
  $("#selected-champion-name").textContent = selected?.name || "Noch keinen Champion gewählt";
  const lock = $("#lock-champion");
  const pickable = state?.pickable_champion_ids || [];
  lock.disabled = championPickPending || !selected || (pickable.length > 0 && !pickable.includes(selectedChampionId)) || !state?.pick_action?.active;
  lock.title = state?.pick_action?.active ? "" : "Du bist noch nicht mit deinem Pick an der Reihe.";
}

async function chooseChampion(championId, lock) {
  if (championPickPending) return;
  championPickPending = true;
  const status = $("#champion-pick-status");
  const lockButton = $("#lock-champion");
  status.textContent = lock ? "Wird fest gewählt …" : "Hover wird gesetzt …";
  status.classList.remove("error");
  lockButton.disabled = true;
  renderChampionPicker();
  try {
    const response = await fetch("/api/champion-select", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({champion_id:championId, lock}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Der Champion konnte nicht gewählt werden.");
    selectedChampionId = championId;
    status.textContent = lock ? "Champion fest gewählt." : "Hover im Client gesetzt.";
    if (lock) $("#champion-dialog").close();
  } catch (error) {
    status.textContent = error.message;
    status.classList.add("error");
  } finally {
    championPickPending = false;
    renderChampionPicker();
  }
}

$("#champion-picker-button").addEventListener("click", () => {
  selectedChampionId = state?.pick_action?.champion_id || localPlayer()?.champion_id || 0;
  $("#champion-search").value = "";
  $("#champion-pick-status").textContent = "";
  $("#champion-pick-status").classList.remove("error");
  renderChampionPicker();
  updateDialogViewport();
  $("#champion-dialog").showModal();
  document.body.classList.add("modal-open");
  $("#champion-grid").scrollTop = 0;
  const focusSearch = !compactLayout.matches && window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  $(focusSearch ? "#champion-search" : "#champion-dialog-close").focus({preventScroll:true});
});
$("#champion-dialog").addEventListener("close", () => document.body.classList.remove("modal-open"));
$("#champion-dialog-close").addEventListener("click", () => $("#champion-dialog").close());
$("#champion-dialog").addEventListener("click", event => {
  if (event.target === $("#champion-dialog")) $("#champion-dialog").close();
});
$("#champion-search").addEventListener("input", () => {
  renderChampionPicker();
  $("#champion-grid").scrollTop = 0;
});
$("#champion-grid").addEventListener("click", event => {
  const option = event.target.closest("[data-champion-id]");
  if (option) chooseChampion(Number(option.dataset.championId), false);
});
$("#lock-champion").addEventListener("click", () => {
  if (selectedChampionId) chooseChampion(selectedChampionId, true);
});

function playerRow(player, index, team) {
  const detail = {LOCKED:"Gewählt", HOVER:"Hover", OFFEN:"Wartet", LOBBY:"In der Lobby", BOT:"Bot"}[player?.status] || "Wartet";
  const name = player?.name || "Noch offen";
  return `<button class="player ${player?.is_local ? "local" : ""}" data-player="${team}:${player?.cell_id ?? index}" ${!player?.champion_id ? "disabled" : ""} aria-label="${esc(name)}${player?.position ? `, ${esc(roles[player.position])}` : ""}: Build ansehen">
    <span class="portrait">${player?.image_url ? image(player.image_url, "") : "·"}</span>
    <span class="player-copy"><span class="player-name">${esc(name)}${player?.is_local ? '<span class="you">DU</span>' : ""}</span><span class="player-detail">${esc(roles[player?.position] || "Rolle offen")} <span aria-hidden="true">·</span> <span class="${player?.status === "HOVER" ? "hover-status" : ""}">${detail}</span></span></span>
    ${player?.status === "LOCKED" ? '<span class="lock" aria-label="Fest gewählt">✓</span>' : ""}</button>`;
}

function renderTeams() {
  const signature = JSON.stringify([state.own_team, state.enemy_team, state.bans]);
  if (signature === teamsSignature) return;
  teamsSignature = signature;
  const focused = document.activeElement?.dataset?.player;
  $("#teams").innerHTML = [["Dein Team", "own", state.own_team], ["Gegner", "enemy", state.enemy_team]].map(([label, side, team]) => {
    const rows = Array.from({length:Math.max(5, team.length)}, (_, index) => playerRow(team[index], index, side)).join("");
    const bans = (state.bans?.[side] || []).map(ban => `<span class="ban" title="${esc(ban.name)} gebannt">${image(ban.image_url, `${ban.name} gebannt`)}</span>`).join("");
    const inLobby = ["Lobby", "Matchmaking", "ReadyCheck"].includes(state.phase);
    const count = inLobby ? team.length : team.filter(p => p.champion_id).length;
    return `<section class="team-section"><h3 class="team-title">${label}<small title="${inLobby ? "Spieler" : "Champions gewählt oder gehovert"}">${count} / ${Math.max(5, team.length)}</small></h3>${rows}${bans ? `<div class="bans" aria-label="Bans">${bans}</div>` : ""}</section>`;
  }).join("");
  restoringFocus = true;
  if (focused) [...document.querySelectorAll("[data-player]")].find(row => row.dataset.player === focused)?.focus({preventScroll:true});
  restoringFocus = false;
}

function applyState(value) {
  state = value;
  $("#server-notice").hidden = true;
  $("#connection").classList.toggle("online", state.connected);
  $("#connection-text").textContent = state.connected ? "Mit League verbunden" : "Warte auf League";
  const phase = phases[state.phase] || [state.phase || "Deine Lobby", "Der aktuelle Stand aus deinem League-Client."];
  $("#phase-title").textContent = phase[0];
  $("#phase-description").textContent = phase[1];
  $("#queue-label").textContent = state.queue;
  $("#champion-picker-button").hidden = state.phase !== "ChampSelect" || !state.pick_action;
  const isReadyCheck = state.phase === "ReadyCheck";
  const clientUnavailable = !state.connected || state.logged_in === false;
  const isIdle = clientUnavailable || state.phase === "None";
  const isPregame = ["Lobby", "Matchmaking"].includes(state.phase);
  if (isIdle) {
    const offline = clientUnavailable;
    $("#idle-eyebrow").textContent = offline ? "LEAGUE OF LEGENDS" : "BEREIT WENN DU ES BIST";
    $("#idle-title").textContent = offline ? "League of Legends starten" : "Noch keine Spielsuche";
    $("#idle-copy").textContent = offline
      ? "Öffne den League-Client und melde dich an. Die Verbindung zu lolbuddy wird anschließend automatisch hergestellt."
      : "Wähle einen Spielmodus und erstelle deine Lobby direkt hier.";
    renderIdleControls(offline);
  }
  if (isReadyCheck && !readyCheckActive) {
    readyCheckAccepted = false;
    $("#accept-ready-check").disabled = false;
    $("#accept-ready-check").textContent = "Match annehmen";
    $("#ready-check-status").textContent = "";
    $("#ready-check-status").classList.remove("error");
  }
  readyCheckActive = isReadyCheck;
  $("#ready-check-view").hidden = !isReadyCheck;
  $("#idle-view").hidden = !isIdle;
  $("#pregame-view").hidden = !isPregame;
  $("#dashboard-view").hidden = isReadyCheck || isIdle || isPregame;
  $("#ready-check-queue").textContent = state.queue || "League of Legends";
  if (isPregame) renderPregame();
  if ($("#champion-dialog").open) {
    if (state.phase !== "ChampSelect" || !state.pick_action) $("#champion-dialog").close();
    else {
      const clientChampion = state.pick_action?.champion_id || localPlayer()?.champion_id || 0;
      if (!championPickPending) selectedChampionId = clientChampion;
      renderChampionPicker();
    }
  }
  $("#mode-label").textContent = currentMode() === "aram" ? "ARAM" : "Kluft der Beschwörer";
  $("#draft-label").textContent = state.connected ? "LIVE" : "OFFLINE";
  const timer = $("#draft-timer");
  timer.hidden = !state.timer?.seconds;
  timer.textContent = state.timer ? `${state.timer.seconds}s` : "";
  const local = localPlayer();
  const signature = `${local?.champion_id || 0}:${local?.position || ""}:${local?.cell_id ?? ""}`;
  if (signature !== localSignature) {
    localSignature = signature;
    preview = pinned = null;
    clearTimeout(hoverTimer);
    $("#role-select").value = "";
  }
  if (pinned && !findPlayer(pinned)?.champion_id) pinned = null;
  if (preview && !findPlayer(preview)?.champion_id) preview = null;
  renderTeams();
  updateBuild();
}

$("#accept-ready-check").addEventListener("click", async () => {
  if (!readyCheckActive || readyCheckAccepted) return;
  const button = $("#accept-ready-check");
  const status = $("#ready-check-status");
  button.disabled = true;
  button.textContent = "Wird angenommen …";
  status.textContent = "";
  status.classList.remove("error");
  try {
    const response = await fetch("/api/ready-check/accept", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:"{}",
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Das Match konnte nicht angenommen werden.");
    readyCheckAccepted = true;
    button.textContent = "✓ Angenommen";
    status.textContent = "Warte auf die anderen Spieler …";
  } catch (error) {
    button.disabled = false;
    button.textContent = "Erneut versuchen";
    status.textContent = error.message;
    status.classList.add("error");
  }
});

function hero(player, info = null) {
  const mode = info?.game_mode || currentMode();
  const position = mode === "aram" ? "aram" : info?.position || $("#role-select").value || player.position;
  const banStat = mode === "aram" ? "" : `<div class="stat"><strong>${rate(info?.ban_rate)}</strong><span>Banrate</span></div>`;
  const stats = info ? `<details class="champion-stats"><summary>Statistiken<span class="disclosure-chevron" aria-hidden="true"></span></summary><div class="hero-stats"><div class="stat"><strong>${rate(info.win_rate)}</strong><span>Winrate</span></div><div class="stat"><strong>${rate(info.pick_rate)}</strong><span>Pickrate</span></div>${banStat}</div></details>` : "";
  const dataset = mode === "aram" ? "ARAM · Global" : `${info?.region?.toUpperCase()} · ${info?.rank_tier?.replace("_plus", "+").replaceAll("_", " ")}`;
  return `<div class="hero">${image(info?.image_url || player.image_url, player.name, "hero-portrait")}<div class="hero-copy"><p class="eyebrow">${esc(roles[position] || "EMPFOHLENE ROLLE")}${info?.tier ? ` · TIER ${info.tier}` : ""}</p><h2>${esc(info?.name || player.name)}</h2><div class="hero-meta">${info ? `<span>Patch ${esc(info.patch)}</span><span>·</span><span>${esc(dataset)}</span>` : "<span>Build wird von OP.GG geladen</span>"}</div></div>${stats}</div>`;
}

function asset(item) {
  return `<div class="asset" title="${esc(item.name)}"><div class="asset-picture">${image(item.image_url, item.name)}${item.count > 1 ? `<span class="asset-count">${item.count}×</span>` : ""}</div><span class="asset-name">${esc(item.name)}</span></div>`;
}
function items(build, core = false) {
  if (!build?.items?.length) return '<p class="no-data">Keine Daten verfügbar.</p>';
  return `<div class="items ${core ? "core-items" : ""}">${build.items.map(asset).join(core ? '<span class="item-arrow" aria-hidden="true">→</span>' : "")}</div>`;
}
function buildRate(build) {
  if (!build) return "";
  return `<p class="build-rate"><span><b>${rate(build.win_rate)}</b> Winrate</span><span>${rate(build.pick_rate)} Pickrate</span></p>`;
}
function runeRow(rune, key = false) {
  return `<div class="rune ${key ? "keystone" : ""}">${image(rune.image_url, "")}<span>${esc(rune.name)}</span></div>`;
}
function runeBuild(build, index, info) {
  if (!build) return '<p class="no-data">Für diese Rolle sind keine Runen verfügbar.</p>';
  return `<div class="rune-styles"><div><div class="rune-style-title">${image(build.primary_style_image, "")}${esc(build.primary_style)}</div>${build.primary_runes.map((rune, i) => runeRow(rune, i === 0)).join("")}</div><div><div class="rune-style-title">${image(build.secondary_style_image, "")}${esc(build.secondary_style)}</div><div class="secondary-runes">${build.secondary_runes.map(rune => runeRow(rune)).join("")}</div></div></div><div class="shards">${build.shard_details.map(shard => `<div class="shard" title="${esc(shard.name)}">${image(shard.image_url, "")}<span>${esc(shard.name)}</span></div>`).join("")}</div>${buildRate(build)}<p class="build-rate">${Number(build.games).toLocaleString("de-DE")} Spiele</p><div class="rune-import"><button class="button import-runes" data-champion="${esc(info.champion)}" data-position="${esc(info.position)}" data-tier="${esc(info.rank_tier)}" data-mode="${esc(info.game_mode)}" data-rune-index="${index}">Runen in League übernehmen</button><p class="rune-import-status" role="status"></p></div>`;
}
function spellBuild(build) {
  if (!build) return '<p class="no-data">Keine Summoner-Spells verfügbar.</p>';
  return `<div class="spell-list">${build.spells.map(spell => `<div class="spell">${image(spell.image_url, spell.name)}<span>${esc(spell.name)}</span></div>`).join("")}</div>${buildRate(build)}`;
}

function renderBuild(player, info) {
  const safeSource = /^https:\/\/op\.gg\/lol\/(?:champions|modes\/aram)\//.test(info.source_url) ? info.source_url : "https://op.gg/lol/champions";
  const requestedRole = info.game_mode === "aram" ? "aram" : $("#role-select").value || player.position;
  const mismatch = requestedRole && requestedRole !== info.position ? `OP.GG liefert hier ${roles[info.position] || info.position} statt ${roles[requestedRole]}. ` : "";
  $("#build").innerHTML = `${hero(player, info)}
    <div class="build-tabs" role="tablist" aria-label="Build-Ansicht"><button id="items-tab" type="button" role="tab" data-build-section="items" aria-controls="items-pane">Items &amp; Spells</button><button id="runes-tab" type="button" role="tab" data-build-section="runes" aria-controls="runes-pane">Runen</button></div>
    <div class="build-columns"><div id="items-pane" data-build-pane="items" aria-labelledby="items-tab" tabindex="0"><section aria-label="Items"><div class="section-heading"><h3>Items</h3><span>Empfohlener Build</span></div>
    <div class="item-start"><div class="item-group"><div class="group-label">Zum Start</div>${items(info.starter_builds[0])}</div><div class="item-group boots-group"><div class="group-label">Schuhe</div>${items(info.boot_builds[0])}</div></div>
    <div class="item-group"><div class="group-label">Deine ersten drei Items <span>In dieser Reihenfolge</span></div>${items(info.core_builds[0], true)}${buildRate(info.core_builds[0])}</div>
    ${info.later_builds.length ? `<details class="alternatives later-items"><summary>Situative Items</summary><div class="items">${info.later_builds.map(build => build.items.map(asset).join("")).join("")}</div></details>` : ""}
    ${info.core_builds[1] ? `<details class="alternatives"><summary>Alternativen Core-Build ansehen</summary>${items(info.core_builds[1], true)}${buildRate(info.core_builds[1])}</details>` : ""}</section>
    <section class="spells-section" aria-label="Summoner Spells"><div class="section-heading"><h3>Summoner Spells</h3></div>${spellBuild(info.spell_builds[0])}${info.spell_builds[1] ? `<details class="alternatives"><summary>Alternative Spells</summary>${spellBuild(info.spell_builds[1])}</details>` : ""}</section></div>
    <section class="runes-section" id="runes-pane" data-build-pane="runes" aria-labelledby="runes-tab" tabindex="0"><div class="section-heading"><h3>Runen</h3><span>Empfohlene Seite</span></div>${runeBuild(info.rune_builds[0], 0, info)}${info.rune_builds[1] ? `<details class="alternatives"><summary>Alternative Runenseite</summary>${runeBuild(info.rune_builds[1], 1, info)}</details>` : ""}</section></div>
    <p class="build-note">${esc(mismatch)}Empfehlung nach OP.GG-Popularität. <a href="${esc(safeSource)}" target="_blank" rel="noopener noreferrer">Auf OP.GG ansehen ↗</a></p>`;
  updateBuildSections();
}

function emptyBuild() {
  const connected = state?.connected;
  const text = state?.phase === "ChampSelect" ? "Hover einen Champion im League-Client" : connected ? "Warte auf deinen Champion" : "Warte auf den League-Client";
  $("#build").innerHTML = `<div class="empty-state"><span class="empty-symbol" aria-hidden="true">↳</span><p class="eyebrow">CHAMPION-AUSWAHL</p><h2>Noch kein Champion gewählt</h2><p>Hover einen Champion im League-Client.<br>Items, Runen und Spells erscheinen hier automatisch.</p><span class="waiting"><span class="status-dot"></span>${text}</span></div>`;
}

async function loadBuild(player, position, tier, mode, key, version) {
  try {
    const query = new URLSearchParams({champion:player.alias || player.name, tier, mode});
    if (position) query.set("position", position);
    const response = await fetch(`/api/build?${query}`, {signal:AbortSignal.timeout(30000)});
    const info = await response.json();
    if (!response.ok) {
      const error = new Error(info.error || "OP.GG ist gerade nicht erreichbar.");
      error.kind = info.kind;
      throw error;
    }
    if (version === requestVersion) renderBuild(player, info);
  } catch (error) {
    if (version !== requestVersion) return;
    const message = ["TimeoutError", "AbortError"].includes(error.name) ? "OP.GG antwortet gerade zu langsam. Bitte versuche es gleich noch einmal." : error.message;
    const title = error.kind === "no_role_data" ? "Keine Daten für diese Rolle" : "Build gerade nicht verfügbar";
    $("#build").innerHTML = `${hero(player)}<div class="error-state"><p class="eyebrow">OP.GG</p><h3>${title}</h3><p>${esc(message)}</p><button class="button" id="retry-build">Erneut versuchen</button></div>`;
    $("#retry-build").addEventListener("click", () => { activeKey = ""; updateBuild(); });
  }
}

function updateBuild() {
  const player = findPlayer(preview) || findPlayer(pinned) || localPlayer();
  const inspecting = Boolean(preview || pinned);
  const mode = currentMode();
  const isAram = mode === "aram";
  $("#follow-button").hidden = !inspecting;
  $("#follow-label").hidden = inspecting;
  $("#role-select").disabled = !player?.champion_id || isAram;
  $("#tier-select").disabled = isAram;
  const clientRoleOption = $("#role-select").querySelector('option[value=""]');
  clientRoleOption.textContent = isAram
    ? "ARAM"
    : player?.position
    ? `${roles[player.position]} · League-Client`
    : "OP.GG-Standardrolle";
  $("#footer-region").textContent = isAram ? "GLOBAL" : "EUW";
  $("#footer-tier").textContent = isAram
    ? "ARAM"
    : $("#tier-select").selectedOptions[0].textContent;
  document.querySelectorAll("[data-player]").forEach(row => row.classList.toggle("viewing", Boolean(player?.champion_id) && row.dataset.player === playerKey(player)));
  if (!player?.champion_id) {
    const key = `empty:${state?.phase}`;
    if (key !== activeKey) { activeKey = key; requestVersion++; emptyBuild(); }
    return;
  }
  const position = isAram ? "" : $("#role-select").value || player.position || "";
  const tier = $("#tier-select").value;
  const key = `${player.alias || player.name}:${position}:${tier}:${mode}`;
  if (key === activeKey) return;
  activeKey = key;
  const version = ++requestVersion;
  $("#build").innerHTML = `${hero(player)}<div class="loading-body" role="status"><span class="loader" aria-hidden="true"></span><span>Build von OP.GG wird geladen …</span></div>`;
  loadBuild(player, position, tier, mode, key, version);
}

function schedulePreview(row) {
  clearTimeout(hoverTimer);
  if (!row || row.disabled) return;
  hoverTimer = setTimeout(() => { preview = row.dataset.player; updateBuild(); }, 180);
}
$("#teams").addEventListener("pointerover", event => {
  if (event.pointerType === "touch") return;
  const row = event.target.closest("[data-player]");
  if (row && !row.contains(event.relatedTarget)) schedulePreview(row);
});
$("#teams").addEventListener("pointerout", event => {
  const row = event.target.closest("[data-player]");
  if (row && !row.contains(event.relatedTarget)) {
    clearTimeout(hoverTimer);
    preview = null;
    updateBuild();
  }
});
$("#teams").addEventListener("focusin", event => {
  if (!restoringFocus) schedulePreview(event.target.closest("[data-player]"));
});
$("#teams").addEventListener("focusout", () => { clearTimeout(hoverTimer); preview = null; updateBuild(); });
$("#teams").addEventListener("click", event => {
  const row = event.target.closest("[data-player]");
  if (!row || row.disabled) return;
  clearTimeout(hoverTimer);
  pinned = row.dataset.player;
  preview = null;
  $("#role-select").value = "";
  updateBuild();
  if (compactLayout.matches) {
    $("#draft-details").open = false;
    $(".build-panel").scrollIntoView({block:"start"});
  }
});
$("#follow-button").addEventListener("click", () => {
  clearTimeout(hoverTimer);
  preview = pinned = null;
  $("#role-select").value = "";
  updateBuild();
});
$("#role-select").addEventListener("change", () => updateBuild());
$("#tier-select").addEventListener("change", () => updateBuild());
$("#build").addEventListener("keydown", event => {
  const tab = event.target.closest("[data-build-section]");
  if (!tab || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  buildSection = event.key === "Home" ? "items" : event.key === "End" ? "runes" : buildSection === "items" ? "runes" : "items";
  updateBuildSections();
  document.querySelector(`[data-build-section="${buildSection}"]`).focus();
});
$("#build").addEventListener("click", async event => {
  const tab = event.target.closest("[data-build-section]");
  if (tab) {
    buildSection = tab.dataset.buildSection;
    updateBuildSections();
    return;
  }
  const button = event.target.closest(".import-runes");
  if (!button || button.disabled) return;
  const status = button.parentElement.querySelector(".rune-import-status");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Wird übertragen …";
  status.textContent = "";
  try {
    const response = await fetch("/api/runes", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        champion:button.dataset.champion,
        position:button.dataset.position || null,
        tier:button.dataset.tier,
        mode:button.dataset.mode,
        rune_index:Number(button.dataset.runeIndex),
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Import fehlgeschlagen.");
    button.textContent = "✓ Im Client aktiviert";
    status.textContent = data.name;
  } catch (error) {
    button.disabled = false;
    button.textContent = originalText;
    status.textContent = error.message;
    status.classList.add("error");
  }
});
const events = new EventSource("/api/events");
events.onmessage = event => applyState(JSON.parse(event.data));
events.onerror = () => {
  $("#server-notice").hidden = false;
  $("#connection").classList.remove("online");
  $("#connection-text").textContent = "Verbindung unterbrochen";
};
