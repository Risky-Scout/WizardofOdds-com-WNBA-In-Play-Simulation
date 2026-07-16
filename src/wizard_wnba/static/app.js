"use strict";

const state = {
  snapshot: null,
  options: null,
  liveMarkets: [],
};

const playerProps = new Set([
  "player_points",
  "player_rebounds",
  "player_assists",
  "player_threes",
  "player_points_rebounds_assists",
]);

const byId = id => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    character => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    })[character],
  );
}

function percentage(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const normalized = Math.abs(number) <= 1 ? number * 100 : number;
  return `${normalized.toFixed(1)}%`;
}

function gameClock(seconds) {
  const value = Math.max(0, Math.round(Number(seconds || 0)));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
}

function minutesRemaining(game) {
  const period = Number(game?.period);
  const seconds = Number(game?.clock_seconds);

  if (!Number.isFinite(period) || !Number.isFinite(seconds)) {
    return null;
  }

  return period > 4
    ? seconds / 60
    : (seconds + Math.max(4 - period, 0) * 600) / 60;
}

function formatRemaining(minutes) {
  if (!Number.isFinite(minutes)) return "";
  return gameClock(minutes * 60);
}

function parseRemaining(value) {
  const text = String(value || "").trim();

  if (!text) return null;

  if (!text.includes(":")) {
    const numeric = Number(text);
    return Number.isFinite(numeric) ? numeric : null;
  }

  const [minutes, seconds] = text.split(":").map(Number);

  if (
    !Number.isFinite(minutes)
    || !Number.isFinite(seconds)
    || seconds < 0
    || seconds >= 60
  ) {
    return null;
  }

  return minutes + seconds / 60;
}

async function fetchJson(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);

  try {
    const response = await fetch(url, {
      ...options,
      cache: "no-store",
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    return await response.json();
  } finally {
    clearTimeout(timeout);
  }
}

function setConnection(status, text) {
  const element = byId("connection");
  element.className = `connection ${status}`;
  element.querySelector("strong").textContent = text;
}

function renderSystem(snapshot) {
  const games = snapshot.games || [];
  const recommendations = snapshot.recommendations || [];
  const dataStatus = snapshot.data_status || "UNKNOWN";
  const modelMissing = (snapshot.metrics?.engine_errors || []).some(
    error => String(error).includes("MODEL_BUNDLE_NOT_READY"),
  );

  byId("game-count").textContent = games.length;
  byId("edge-count").textContent = recommendations.length;
  byId("engine-status").textContent =
    snapshot.engine_status || "UNKNOWN";
  byId("data-status").textContent =
    dataStatus.replaceAll("_", " ");

  if (modelMissing) {
    byId("engine-message").textContent =
      "Live collection and the Scenario Lab simulator are active. Official automated selections remain paused until out-of-sample calibrators are attached.";
  } else if (dataStatus === "LIVE") {
    byId("engine-message").textContent =
      "Live game state and modeled opportunities are updating automatically.";
  } else {
    byId("engine-message").textContent =
      "Live collection is active; publication safety gates remain in effect.";
  }

  const generatedAt = new Date(snapshot.generated_at);

  byId("last-update").textContent =
    Number.isNaN(generatedAt.getTime())
      ? "Live data received"
      : `Updated ${generatedAt.toLocaleTimeString()}`;
}

function renderGames(snapshot) {
  const games = snapshot.games || [];
  const target = byId("games");

  if (!games.length) {
    target.innerHTML =
      '<div class="empty">No active WNBA game detected.</div>';
    return;
  }

  target.innerHTML = games.map(game => `
    <article class="game-card">
      <div class="game-card-header">
        <span>Q${escapeHtml(game.period)} · ${gameClock(game.clock_seconds)}</span>
        <strong>LIVE</strong>
      </div>

      <div class="team-row">
        <span>${escapeHtml(game.away_team)}</span>
        <strong>${escapeHtml(game.away_score)}</strong>
      </div>

      <div class="team-row">
        <span>${escapeHtml(game.home_team)}</span>
        <strong>${escapeHtml(game.home_score)}</strong>
      </div>
    </article>
  `).join("");
}

function recommendationBook(rec) {
  return (
    rec.bookmaker_title
    || rec.bookmaker
    || rec.book
    || rec.sportsbook
    || "—"
  );
}

function recommendationMarket(rec) {
  return rec.market_title || rec.market || rec.market_key || "—";
}

function recommendationSelection(rec) {
  return (
    rec.player_name
    || rec.entity_name
    || rec.selection
    || rec.description
    || `${rec.side || ""} ${rec.line ?? ""}`.trim()
    || "Selection"
  );
}

function filteredRecommendations() {
  const markets = state.liveMarkets || [];
  const book = byId("book-filter").value;
  const market = byId("market-filter").value;

  return markets.filter(item => {
    return (
      (!book || item.bookmaker_key === book)
      && (!market || item.market_key === market)
    );
  });
}

function renderRecommendations() {
  const target = byId("recommendations");
  const markets = filteredRecommendations();

  if (!markets.length) {
    target.innerHTML = `
      <tr>
        <td colspan="7" class="empty">
          No current sportsbook lines were found for the live game.
        </td>
      </tr>`;
    return;
  }

  target.innerHTML = markets.map(item => `
    <tr>
      <td>
        ${escapeHtml(item.selection)}
        ${item.side ? ` · ${escapeHtml(item.side)}` : ""}
      </td>
      <td>${escapeHtml(item.market_title || item.market_key)}</td>
      <td>${escapeHtml(item.bookmaker_title)}</td>
      <td>${escapeHtml(item.line ?? "—")}</td>
      <td>${escapeHtml(item.american_odds)}</td>
      <td>Pending model</td>
      <td>Pending model</td>
    </tr>
  `).join("");
}

function populateOptions() {
  if (!state.options) return;

  const book = byId("book-filter");
  const market = byId("market-filter");

  const selectedBook = book.value;
  const selectedMarket = market.value;

  book.innerHTML =
    '<option value="">All books</option>'
    + (state.options.bookmakers || []).map(item => `
      <option value="${escapeHtml(item.key)}">
        ${escapeHtml(item.title)}
      </option>
    `).join("");

  market.innerHTML =
    '<option value="">All markets</option>'
    + (state.options.markets || []).map(item => `
      <option value="${escapeHtml(item.key)}">
        ${escapeHtml(item.title)}
      </option>
    `).join("");

  book.value = selectedBook;
  market.value = selectedMarket;
}

function findGameForRecommendation(rec) {
  const games = state.snapshot?.games || [];
  const id = rec.canonical_game_id || rec.game_id;

  return games.find(game =>
    game.canonical_game_id === id || game.game_id === id
  ) || games[0] || null;
}

function populateScenarioSelections() {
  const select = byId("scenario-selection");
  const submit = byId("scenario-submit");
  const markets = state.liveMarkets || [];
  const previous = select.value;

  if (!markets.length) {
    select.innerHTML =
      '<option value="">No current live sportsbook lines</option>';
    select.disabled = true;
    submit.disabled = true;
    return;
  }

  select.innerHTML =
    '<option value="">Select a live sportsbook line</option>'
    + markets.map(item => {
      const line = item.line == null ? "" : ` ${item.line}`;
      const label = [
        item.selection,
        item.market_title || item.market_key,
        `${item.side}${line}`,
        item.bookmaker_title,
        item.american_odds > 0
          ? `+${item.american_odds}`
          : item.american_odds,
      ].filter(Boolean).join(" · ");

      return `
        <option value="${escapeHtml(item.market_id)}">
          ${escapeHtml(label)}
        </option>`;
    }).join("");

  select.disabled = false;

  if (markets.some(item => item.market_id === previous)) {
    select.value = previous;
  }

  submit.disabled = true;
}

function selectedRecommendation() {
  const id = byId("scenario-selection").value;

  return (state.liveMarkets || []).find(
    item => String(item.market_id) === id,
  );
}

function prepareScenario() {
  const item = selectedRecommendation();
  const submit = byId("scenario-submit");
  const error = byId("scenario-error");

  error.textContent = "";
  submit.disabled = !item;

  if (!item) {
    byId("current-total-field").classList.add("hidden");
    byId("scenario-result").textContent =
      "Select a live sportsbook line to run the simulator.";
    return;
  }

  // Bind the form to this exact market_id so a submit can never silently use
  // a different book's line.
  byId("scenario-form").dataset.marketId = String(item.market_id);

  const isMoneyline = String(item.market_key || "") === "h2h";

  const sideValues = new Set(["over", "under", "home", "away"]);
  byId("scenario-side").value = sideValues.has(item.side)
    ? item.side
    : isMoneyline
      ? "home"
      : "over";

  // Live line/odds are populated read-only from the selected sportsbook, and
  // the line field is hidden entirely for two-way moneyline (h2h) markets.
  const lineField = byId("scenario-line-field");
  if (lineField) {
    lineField.classList.toggle("hidden", isMoneyline);
  }
  byId("scenario-line").value = isMoneyline
    ? ""
    : item.line == null
      ? ""
      : item.line;

  byId("scenario-odds").value = item.american_odds ?? "";

  const prop = playerProps.has(String(item.market_key || ""));
  byId("current-total-field").classList.toggle("hidden", !prop);
  byId("scenario-current-total").value = "";

  const game = findGameForRecommendation(item);
  const remaining = minutesRemaining(game);

  byId("scenario-time").value = formatRemaining(remaining);
  byId("scenario-time").dataset.baseline =
    Number.isFinite(remaining) ? String(remaining) : "";

  byId("scenario-result").textContent =
    "Line loaded. Run the possession-based live simulation.";
}

function renderScenarioResult(result) {
  const cells = [
    `<div>
        <small>Win probability</small>
        <strong>${percentage(result.win_probability)}</strong>
      </div>`,
    `<div>
        <small>Push probability</small>
        <strong>${percentage(result.push_probability)}</strong>
      </div>`,
    `<div>
        <small>Fair American odds</small>
        <strong>${
          result.fair_american_odds > 0
            ? `+${result.fair_american_odds}`
            : result.fair_american_odds ?? "—"
        }</strong>
      </div>`,
    `<div>
        <small>Expected ROI</small>
        <strong>${percentage(result.expected_roi)}</strong>
      </div>`,
    `<div>
        <small>Conservative ROI</small>
        <strong>${percentage(result.conservative_roi)}</strong>
      </div>`,
    `<div>
        <small>Simulations</small>
        <strong>${Number(result.simulation_count).toLocaleString()}</strong>
      </div>`,
  ];

  // "Projected mean" is a point projection only for line-based markets. For a
  // two-way moneyline the server returns null, so we omit the card entirely
  // rather than show a meaningless Bernoulli mean.
  if (result.projected_mean !== null && result.projected_mean !== undefined) {
    cells.push(
      `<div>
        <small>Projected mean</small>
        <strong>${Number(result.projected_mean).toFixed(2)}</strong>
      </div>`,
    );
  }

  // This card describes Monte Carlo precision (simulation stability), not the
  // model's confidence in the bet — relabel it accordingly.
  cells.push(
    `<div>
        <small>Monte Carlo precision</small>
        <strong>${
          Number(result.total_uncertainty) <= 0.04
            ? "High"
            : Number(result.total_uncertainty) <= 0.08
              ? "Medium"
              : "Low"
        }</strong>
      </div>`,
  );

  const calibration = result.calibration_status
    ? `<p class="calibration-badge">${escapeHtml(result.calibration_status)}</p>`
    : "";

  byId("scenario-result").innerHTML = `
    <div class="result-grid">
      ${cells.join("")}
    </div>
    ${calibration}
    <p class="simulation-note">
      ${escapeHtml(result.note || "")}
    </p>`;
}


async function fetchSimulation(url, options = {}) {
  const controller = new AbortController();

  const timeout = window.setTimeout(
    () => controller.abort(),
    300000,
  );

  try {
    const response = await fetch(url, {
      ...options,
      cache: "no-store",
      signal: controller.signal,
    });

    const text = await response.text();
    let payload = {};

    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = {detail: text};
      }
    }

    if (!response.ok) {
      throw new Error(
        payload.detail
        || payload.message
        || `Simulation failed: HTTP ${response.status}`,
      );
    }

    return payload;
  } catch (error) {
    if (
      error?.name === "AbortError"
      || String(error?.message || error)
        .toLowerCase()
        .includes("aborted")
    ) {
      throw new Error(
        "The simulation exceeded five minutes.",
      );
    }

    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

async function runScenario(event) {
  event.preventDefault();

  const error = byId("scenario-error");
  error.textContent = "";

  const selectedId = String(byId("scenario-selection").value || "");
  if (!selectedId) {
    error.textContent = "Select a live sportsbook line.";
    return;
  }

  // Always refresh the live feed immediately before submitting so a line that
  // has expired is caught here. We never silently fall back to another book's
  // line — the user must reselect a current one.
  try {
    const feed = await fetchJson(
      `api/v1/live-markets?refresh=${Date.now()}`,
    );
    state.liveMarkets = feed.markets || [];
  } catch (refreshError) {
    error.textContent =
      `Could not refresh live lines: ${refreshError.message}`;
    return;
  }

  const item = (state.liveMarkets || []).find(
    entry => String(entry.market_id) === selectedId,
  );

  populateScenarioSelections();

  if (!item) {
    error.textContent =
      "This sportsbook line expired. Reselect a current line.";
    byId("scenario-result").textContent =
      "The selected sportsbook line is no longer available. "
      + "Please reselect a current line.";
    byId("scenario-submit").disabled = true;
    return;
  }

  // Keep the current selection active and the form bound to this exact id.
  byId("scenario-selection").value = selectedId;
  byId("scenario-form").dataset.marketId = selectedId;

  const isMoneyline = String(item.market_key || "") === "h2h";
  const odds = Number(byId("scenario-odds").value || item.american_odds);

  // Moneyline is a two-way market with no line; only line-based markets
  // require a numeric line.
  let line = null;
  if (!isMoneyline) {
    line = Number(byId("scenario-line").value);
    if (!Number.isFinite(line)) {
      error.textContent = "The selected line is unavailable.";
      return;
    }
  }

  if (
    !Number.isInteger(odds)
    || odds === 0
    || (odds > -100 && odds < 100)
  ) {
    error.textContent =
      "The selected sportsbook odds are invalid.";
    return;
  }

  const remaining = parseRemaining(byId("scenario-time").value);
  if (remaining === null) {
    error.textContent =
      "Enter time remaining as 8:24 or decimal minutes.";
    return;
  }

  const payload = {
    market_id: item.market_id,
    side: byId("scenario-side").value,
    american_odds: odds,
    remaining_minutes: remaining,
    simulations: 20000,
  };
  if (!isMoneyline) {
    payload.line = line;
  }

  if (playerProps.has(String(item.market_key || ""))) {
    const current = byId("scenario-current-total").value.trim();
    if (current !== "") {
      const currentTotal = Number(current);
      if (!Number.isFinite(currentTotal) || currentTotal < 0) {
        error.textContent =
          "Enter a valid player's current total.";
        return;
      }
      payload.current_total = currentTotal;
    }
  }

  const submit = byId("scenario-submit");
  submit.disabled = true;
  submit.textContent = "Running 20,000 simulations…";
  byId("scenario-result").textContent =
    "Running the live possession simulation…";

  try {
    const result = await fetchSimulation(
      "api/v1/live-reference-simulation",
      {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify(payload),
      },
    );
    renderScenarioResult(result);
  } catch (requestError) {
    error.textContent = `Simulation failed: ${requestError.message}`;
    byId("scenario-result").textContent =
      "The simulation could not be completed.";
  } finally {
    submit.disabled = false;
    submit.textContent = "Run custom scenario";
  }
}

function render(snapshot) {
  state.snapshot = snapshot;
  renderSystem(snapshot);
  renderGames(snapshot);
  renderRecommendations();
  populateScenarioSelections();
}

async function refresh() {
  try {
    if (!state.options) {
      state.options = await fetchJson(
        `api/v1/product-options?refresh=${Date.now()}`,
      );

      populateOptions();
    }

    const [snapshot, marketFeed] = await Promise.all([
      fetchJson(`api/v1/snapshot?refresh=${Date.now()}`),
      fetchJson(`api/v1/live-markets?refresh=${Date.now()}`),
    ]);

    state.liveMarkets = marketFeed.markets || [];

    render(snapshot);
    setConnection("live", "Live");
  } catch (error) {
    console.error(error);
    setConnection("error", "Reconnecting");
    byId("engine-message").textContent =
      `Live update failed: ${error.message}. Retrying automatically.`;
  }
}

function bindControls() {
  byId("book-filter").addEventListener(
    "change",
    renderRecommendations,
  );

  byId("market-filter").addEventListener(
    "change",
    renderRecommendations,
  );

  byId("scenario-selection").addEventListener(
    "change",
    prepareScenario,
  );

  byId("scenario-form").addEventListener(
    "submit",
    runScenario,
  );
}

document.addEventListener("DOMContentLoaded", () => {
  bindControls();
  refresh();
  window.setInterval(refresh, 5000);
});
