"use strict";

const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const roles = {top:"Top", jungle:"Jungle", mid:"Mid", adc:"Bot / ADC", support:"Support", fill:"Fill", aram:"ARAM"};
const positionChoices = [["top","Top"],["jungle","Jungle"],["mid","Mid"],["adc","Bot / ADC"],["support","Support"],["fill","Fill"]];
const phases = {
  Offline: ["Deine Lobby", "Öffne League of Legends. Die Verbindung entsteht automatisch."],
  None: ["Bereit für die nächste Runde", "Starte eine Lobby im League-Client."],
  Lobby: ["Deine Lobby", "Stelle deine Gruppe zusammen und starte die Spielsuche."],
  Matchmaking: ["Spielsuche läuft", "Deine Lobby ist bereit. Gleich geht’s in die Champion-Auswahl."],
  ReadyCheck: ["Spiel gefunden", "Bestätige das Match im League-Client."],
  ChampSelect: ["Champion-Auswahl", "Dein Pick, deine Spells und alle Matchups auf einen Blick."],
  GameStart: ["Das Spiel startet", "Deine Items bleiben während des Spiels geöffnet."],
  InProgress: ["Ab in die Kluft", "Deine Items bleiben während des Spiels geöffnet."],
  Reconnect: ["Zurück ins Spiel", "Deine Items bleiben hier verfügbar."],
  WaitingForStats: ["Spiel beendet", "Deine Items bleiben bis zur nächsten Lobby geöffnet."],
  PreEndOfGame: ["Spiel beendet", "Deine Items bleiben bis zur nächsten Lobby geöffnet."],
  EndOfGame: ["Spiel beendet", "Bereit für die nächste Runde?"],
};
let state = null;
let localSignature = "";
let teamsSignature = "";
let activeKey = "";
let requestVersion = 0;
let readyCheckActive = false;
let readyCheckAccepted = false;
let selectedChampionId = 0;
let championPickPending = false;
let championPickLocking = false;
let pickerSignature = "";
let draftRecommendationKey = "";
let draftRecommendationVersion = 0;
let draftTimerDeadline = 0;
let draftTimerPhase = "";
const buildPhases = new Set(["GameStart", "InProgress", "Reconnect", "WaitingForStats", "PreEndOfGame", "EndOfGame"]);
const roleOrder = ["top", "jungle", "mid", "adc", "support"];

const compactLayout = window.matchMedia("(max-width: 820px)");

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

function queueTime(seconds) {
  const total = Math.max(0, Number(seconds) || 0);
  return `${Math.floor(total / 60)}:${String(Math.floor(total % 60)).padStart(2, "0")}`;
}

function paintDraftTimer() {
  const timer = $("#draft-timer");
  if (!draftTimerDeadline || state?.phase !== "ChampSelect") {
    timer.hidden = true;
    return;
  }
  timer.hidden = false;
  timer.textContent = `${Math.max(0, Math.ceil((draftTimerDeadline - performance.now()) / 1000))}s`;
}

function syncDraftTimer(timerState) {
  if (state?.phase !== "ChampSelect" || !timerState) {
    draftTimerDeadline = 0;
    draftTimerPhase = "";
    paintDraftTimer();
    return;
  }
  const phase = timerState.phase || "draft";
  const proposed = performance.now() + Math.max(0, Number(timerState.seconds) || 0) * 1000;
  if (phase !== draftTimerPhase || !draftTimerDeadline || Math.abs(proposed - draftTimerDeadline) > 1500) {
    draftTimerDeadline = proposed;
    draftTimerPhase = phase;
  }
  paintDraftTimer();
}

window.setInterval(paintDraftTimer, 200);

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
if (phoneDialog?.dataset.autoOpen === "true") {
  phoneDialog.showModal();
  const cleanUrl = new URL(window.location.href);
  cleanUrl.searchParams.delete("show_qr");
  window.history.replaceState(null, "", `${cleanUrl.pathname}${cleanUrl.search}${cleanUrl.hash}`);
}

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
}

async function chooseChampion(championId, lock) {
  if (championPickPending) return;
  championPickPending = true;
  championPickLocking = lock;
  selectedChampionId = championId;
  const status = $("#own-pick-status");
  const lockButton = $("#lock-champion");
  status.textContent = lock ? "Wird fest gewählt …" : "Hover wird gesetzt …";
  status.classList.remove("error");
  lockButton.disabled = true;
  if (!lock && $("#champion-dialog").open) $("#champion-dialog").close();
  renderChampionPicker();
  try {
    const response = await fetch("/api/champion-select", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({champion_id:championId, lock}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Der Champion konnte nicht gewählt werden.");
    status.textContent = lock ? "Champion fest gewählt." : "Hover im Client gesetzt.";
  } catch (error) {
    selectedChampionId = state?.pick_action?.champion_id || localPlayer()?.champion_id || 0;
    status.textContent = error.message;
    status.classList.add("error");
  } finally {
    championPickPending = false;
    championPickLocking = false;
    renderChampionPicker();
    renderOwnPick();
  }
}

$("#champion-picker-button").addEventListener("click", () => {
  selectedChampionId = state?.pick_action?.champion_id || localPlayer()?.champion_id || 0;
  $("#champion-search").value = "";
  $("#own-pick-status").textContent = "";
  $("#own-pick-status").classList.remove("error");
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

function teamSlots(team) {
  const slots = Object.fromEntries(roleOrder.map(role => [role, null]));
  const unassigned = [];
  for (const player of team || []) {
    if (roleOrder.includes(player.position) && !slots[player.position]) slots[player.position] = player;
    else unassigned.push(player);
  }
  for (const role of roleOrder) {
    if (!slots[role] && unassigned.length) slots[role] = unassigned.shift();
  }
  return slots;
}

function matchupPlayer(player, side) {
  const picked = Boolean(player?.champion_id);
  const name = picked ? player.name : "Noch offen";
  const status = player?.status === "HOVER" ? "Hover" : picked ? "Gewählt" : "Wartet";
  const portrait = `<span class="matchup-portrait">${picked && player.image_url ? image(player.image_url, "") : "·"}</span>`;
  const copy = `<span class="matchup-copy"><strong>${esc(name)}${player?.is_local ? '<span class="you">DU</span>' : ""}</strong><small class="${player?.status === "HOVER" ? "hover-status" : ""}">${status}</small></span>`;
  return `<div class="matchup-player ${side} ${player?.is_local ? "local" : ""}" aria-label="${esc(name)}, ${status}">${side === "enemy" ? copy + portrait : portrait + copy}</div>`;
}

function banStrip(side, label) {
  const bans = state.bans?.[side] || [];
  return `<div class="ban-strip"><span>${label}</span><div>${bans.length ? bans.map(ban => `<span class="ban" title="${esc(ban.name)} gebannt">${image(ban.image_url, `${ban.name} gebannt`)}</span>`).join("") : '<small>Noch keine Bans</small>'}</div></div>`;
}

function renderTeams() {
  const signature = JSON.stringify([state.own_team, state.enemy_team, state.bans]);
  if (signature === teamsSignature) return;
  teamsSignature = signature;
  const own = teamSlots(state.own_team);
  const enemy = teamSlots(state.enemy_team);
  $("#teams").innerHTML = `<div class="matchup-labels"><span>Dein Team</span><span>Gegner</span></div>${roleOrder.map(role => `
    <div class="matchup-row">
      ${matchupPlayer(own[role], "own")}
      <span class="matchup-role">${esc(roles[role])}</span>
      ${matchupPlayer(enemy[role], "enemy")}
    </div>`).join("")}`;
  $("#draft-bans").innerHTML = banStrip("own", "Deine Bans") + banStrip("enemy", "Gegnerische Bans");
}

function renderOwnPick() {
  const player = localPlayer();
  const picked = Boolean(player?.champion_id);
  const status = player?.status === "LOCKED" ? "Fest gewählt" : picked ? "Aktueller Hover" : "Noch offen";
  $("#own-pick").innerHTML = `
    <span class="own-pick-portrait">${picked && player.image_url ? image(player.image_url, "") : "?"}</span>
    <span class="own-pick-copy"><span class="eyebrow">DEIN CHAMPION · ${esc(status.toUpperCase())}</span><strong>${esc(picked ? player.name : "Champion auswählen")}</strong><small>${esc(roles[player?.position] || "Rolle noch offen")}</small></span>`;
  const button = $("#champion-picker-button");
  button.hidden = !state?.pick_action;
  button.disabled = championPickPending;
  button.textContent = picked ? "Champion ändern" : "Champion wählen";
  const lock = $("#lock-champion");
  lock.hidden = !picked || !state?.pick_action || player?.status === "LOCKED";
  lock.disabled = championPickPending || !state?.pick_action?.active;
  lock.textContent = championPickLocking ? "Wird fest gewählt …" : "Fest wählen";
  lock.title = state?.pick_action?.active ? "" : "Du bist noch nicht mit deinem Pick an der Reihe.";
}

let spellOptionsSignature = "";
let spellStateSignature = "";
let spellPickPending = false;

function renderSpellPicker() {
  const spells = state?.available_summoner_spells || [];
  const signature = JSON.stringify(spells);
  if (signature !== spellOptionsSignature) {
    spellOptionsSignature = signature;
    const options = spells.map(spell => `<option value="${spell.id}">${esc(spell.name)}</option>`).join("");
    $("#first-spell").innerHTML = options;
    $("#second-spell").innerHTML = options;
  }
  const incoming = `${state?.summoner_spells?.first || 0}:${state?.summoner_spells?.second || 0}`;
  if (!spellPickPending && incoming !== spellStateSignature) {
    spellStateSignature = incoming;
    $("#first-spell").value = String(state?.summoner_spells?.first || spells[0]?.id || "");
    $("#second-spell").value = String(state?.summoner_spells?.second || spells[1]?.id || "");
  }
  const unavailable = spells.length < 2;
  $("#first-spell").disabled = spellPickPending || unavailable;
  $("#second-spell").disabled = spellPickPending || unavailable;
  $("#apply-spells").disabled = spellPickPending || unavailable;
}

async function applySummonerSpells() {
  if (spellPickPending) return;
  const first = Number($("#first-spell").value);
  const second = Number($("#second-spell").value);
  const status = $("#spell-picker-status");
  if (!first || !second || first === second) {
    status.textContent = "Bitte zwei unterschiedliche Spells wählen.";
    status.classList.add("error");
    return;
  }
  spellPickPending = true;
  status.textContent = "Wird im Client gesetzt …";
  status.classList.remove("error");
  renderSpellPicker();
  try {
    const response = await fetch("/api/champion-select/spells", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({first, second}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Die Spells konnten nicht gesetzt werden.");
    status.textContent = "✓ Im League-Client gesetzt";
  } catch (error) {
    status.textContent = error.message;
    status.classList.add("error");
  } finally {
    spellPickPending = false;
    renderSpellPicker();
  }
}

$("#first-spell").addEventListener("change", () => { $("#spell-picker-status").textContent = ""; });
$("#second-spell").addEventListener("change", () => { $("#spell-picker-status").textContent = ""; });
$("#apply-spells").addEventListener("click", applySummonerSpells);

function runeImportButton(info, index = 0, label = "Runen einspielen") {
  return `<button class="button pick-action-button import-runes" data-champion="${esc(info.champion)}" data-position="${esc(info.request_position ?? info.position)}" data-tier="${esc(info.request_tier || info.rank_tier)}" data-mode="${esc(info.request_mode || info.game_mode)}" data-rune-index="${index}">${label}</button>`;
}

function keystoneQuick(build, info) {
  if (!build) return '<p class="no-data">Keine Runenempfehlung verfügbar.</p>';
  const keystone = build.primary_runes?.[0];
  if (!keystone) return '<p class="no-data">Keine Hauptrune verfügbar.</p>';
  return `<div class="keystone-quick">
    <div class="keystone-info">${image(keystone.image_url, "")}<span><small>Hauptrune</small><strong>${esc(keystone.name)}</strong></span></div>
    ${runeImportButton(info)}
    <p class="rune-import-status" role="status"></p>
  </div>`;
}

function recommendedSpells(build) {
  if (!build?.spells?.length) return '<p class="pick-placeholder">Keine Spell-Empfehlung verfügbar.</p>';
  return `<div class="spell-recommendation">
    <span class="recommendation-label">OP.GG empfiehlt</span>
    <div class="spell-list">${build.spells.map(spell => `<div class="spell">${image(spell.image_url, spell.name)}<span>${esc(spell.name)}</span></div>`).join("")}</div>
  </div>`;
}

async function loadDraftRecommendations(player, position, tier, mode, key, version) {
  try {
    const query = new URLSearchParams({champion:player.alias || player.name, tier, mode});
    if (position) query.set("position", position);
    const response = await fetch(`/api/build?${query}`, {signal:AbortSignal.timeout(30000)});
    const info = await response.json();
    if (!response.ok) throw new Error(info.error || "Empfehlungen nicht verfügbar.");
    if (version === draftRecommendationVersion && state?.phase === "ChampSelect") {
      info.request_position = position || "";
      info.request_tier = tier;
      info.request_mode = mode;
      $("#pick-rune-content").innerHTML = keystoneQuick(info.rune_builds?.[0], info);
      $("#pick-spell-recommendation").innerHTML = recommendedSpells(info.spell_builds?.[0]);
    }
  } catch (error) {
    if (version !== draftRecommendationVersion) return;
    const message = ["TimeoutError", "AbortError"].includes(error.name) ? "OP.GG antwortet gerade zu langsam." : error.message;
    $("#pick-rune-content").innerHTML = `<p class="pick-inline-error">${esc(message)}</p>`;
    $("#pick-spell-recommendation").innerHTML = `<p class="pick-inline-error">${esc(message)}</p>`;
  }
}

function updateDraftSetup() {
  if (state?.phase !== "ChampSelect") return;
  const player = localPlayer();
  if (!player?.champion_id) {
    draftRecommendationKey = "";
    draftRecommendationVersion++;
    $("#pick-rune-content").innerHTML = '<p class="pick-placeholder">Wähle oder hover zuerst deinen Champion.</p>';
    $("#pick-spell-recommendation").innerHTML = '<p class="pick-placeholder">Wähle oder hover zuerst deinen Champion.</p>';
    return;
  }
  const mode = currentMode();
  const position = mode === "aram" ? "" : player.position || "";
  const tier = $("#tier-select").value;
  const key = `${player.alias || player.name}:${position}:${tier}:${mode}`;
  if (key === draftRecommendationKey) return;
  draftRecommendationKey = key;
  const version = ++draftRecommendationVersion;
  $("#pick-rune-content").innerHTML = '<div class="pick-rune-loading"><span class="loader" aria-hidden="true"></span><span>Hauptrune wird geladen …</span></div>';
  $("#pick-spell-recommendation").innerHTML = '<div class="pick-rune-loading"><span class="loader" aria-hidden="true"></span><span>Spells werden geladen …</span></div>';
  loadDraftRecommendations(player, position, tier, mode, key, version);
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
  const isReadyCheck = state.phase === "ReadyCheck";
  const clientUnavailable = !state.connected || state.logged_in === false;
  const isIdle = clientUnavailable || state.phase === "None";
  const isPregame = ["Lobby", "Matchmaking"].includes(state.phase);
  const isChampSelect = state.phase === "ChampSelect";
  const isGame = buildPhases.has(state.phase);
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
  $("#pick-view").hidden = !isChampSelect;
  $("#build-panel").hidden = !isGame;
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
  syncDraftTimer(state.timer);
  const local = localPlayer();
  const signature = `${local?.champion_id || 0}:${local?.position || ""}:${local?.cell_id ?? ""}`;
  if (signature !== localSignature) {
    localSignature = signature;
    $("#role-select").value = "";
    activeKey = "";
  }
  if (!isChampSelect && draftRecommendationKey) {
    draftRecommendationKey = "";
    draftRecommendationVersion++;
  }
  if (isChampSelect) {
    renderOwnPick();
    renderTeams();
    renderSpellPicker();
    updateDraftSetup();
  } else if (isGame) {
    updateBuild();
  } else {
    requestVersion++;
    activeKey = "";
  }
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
  const dataset = mode === "aram" ? "ARAM · Global" : `${info?.region?.toUpperCase()} · ${info?.rank_tier?.replace("_plus", "+").replaceAll("_", " ")}`;
  return `<div class="hero">${image(info?.image_url || player.image_url, player.name, "hero-portrait")}<div class="hero-copy"><p class="eyebrow">${esc(roles[position] || "EMPFOHLENE ROLLE")}${info?.tier ? ` · TIER ${info.tier}` : ""}</p><h2>${esc(info?.name || player.name)}</h2><div class="hero-meta">${info ? `<span>Patch ${esc(info.patch)}</span><span>·</span><span>${esc(dataset)}</span>` : "<span>Build wird von OP.GG geladen</span>"}</div></div></div>`;
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
function renderBuild(player, info) {
  const safeSource = /^https:\/\/op\.gg\/lol\/(?:champions|modes\/aram)\//.test(info.source_url) ? info.source_url : "https://op.gg/lol/champions";
  const requestedRole = info.game_mode === "aram" ? "aram" : $("#role-select").value || player.position;
  const mismatch = requestedRole && requestedRole !== info.position ? `OP.GG liefert hier ${roles[info.position] || info.position} statt ${roles[requestedRole]}. ` : "";
  $("#build").innerHTML = `${hero(player, info)}
    <section class="item-build" aria-label="Items"><div class="section-heading"><h3>Items</h3><span>Empfohlener Build</span></div>
    <div class="item-groups">
      <div class="item-group"><div class="group-label">Zum Start</div>${items(info.starter_builds[0])}</div>
      <div class="item-group"><div class="group-label">Schuhe</div>${items(info.boot_builds[0])}</div>
      <div class="item-group wide"><div class="group-label">Core-Build <span>In dieser Reihenfolge</span></div>${items(info.core_builds[0], true)}${buildRate(info.core_builds[0])}</div>
      ${info.later_builds.length ? `<div class="item-group wide"><div class="group-label">Situative Items</div><div class="items">${info.later_builds.map(build => build.items.map(asset).join("")).join("")}</div></div>` : ""}
      ${info.core_builds[1] ? `<div class="item-group wide"><div class="group-label">Alternativer Core-Build</div>${items(info.core_builds[1], true)}${buildRate(info.core_builds[1])}</div>` : ""}
    </div></section>
    <p class="build-note">${esc(mismatch)}Empfehlung nach OP.GG-Popularität. <a href="${esc(safeSource)}" target="_blank" rel="noopener noreferrer">Auf OP.GG ansehen ↗</a></p>`;
}

function emptyBuild() {
  const connected = state?.connected;
  const text = connected ? "Warte auf deinen Champion" : "Warte auf den League-Client";
  $("#build").innerHTML = `<div class="empty-state"><span class="empty-symbol" aria-hidden="true">↳</span><p class="eyebrow">IN-GAME ITEMBUILD</p><h2>Noch kein Champion verfügbar</h2><p>Sobald League deinen Champion meldet, erscheinen hier die empfohlenen Items.</p><span class="waiting"><span class="status-dot"></span>${text}</span></div>`;
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
  if (!buildPhases.has(state?.phase)) return;
  const player = localPlayer();
  const mode = currentMode();
  const isAram = mode === "aram";
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
$("#role-select").addEventListener("change", () => updateBuild());
$("#tier-select").addEventListener("change", () => updateBuild());
document.addEventListener("click", async event => {
  const button = event.target.closest(".import-runes");
  if (!button || button.disabled) return;
  const status = button.parentElement.querySelector(".rune-import-status");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Wird übertragen …";
  status.textContent = "";
  status.classList.remove("error");
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
