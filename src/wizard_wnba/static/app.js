const state = {
  snapshot: null,
  status: "PUBLISHED",
  market: "ALL",
  grade: "WATCH",
  selected: null,
};
const gradeRank = { WATCH: 0, C: 1, B: 2, A: 3 };

const pct = value => `${(Number(value) * 100).toFixed(1)}%`;
const odds = value => {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n >= 2 ? `+${Math.round((n - 1) * 100)}` : `${Math.round(-100 / (n - 1))}`;
};
const seconds = value => `${Number(value).toFixed(1)}s`;
const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
}[char]));

function setConnection(live, label) {
  document.getElementById("live-dot").className = `status-dot ${live ? "live" : "down"}`;
  document.getElementById("connection-label").textContent = label;
}

function render(snapshot) {
  state.snapshot = snapshot;
  const generated = new Date(snapshot.generated_at);
  document.getElementById("last-update").textContent =
    `Updated ${generated.toLocaleTimeString([], {hour:"2-digit", minute:"2-digit", second:"2-digit"})}`;
  document.getElementById("published-count").textContent =
    snapshot.metrics?.published_count ?? 0;
  document.getElementById("simulation-count").textContent =
    Number(snapshot.metrics?.simulation_count ?? 0).toLocaleString();
  renderGames(snapshot.games || []);
  renderMarketOptions(snapshot.recommendations || []);
  renderRecommendations();
}

function renderGames(games) {
  const target = document.getElementById("game-strip");
  target.innerHTML = games.map(game => `
    <article class="game-pill">
      <div>
        <strong>${esc(game.away_team)} at ${esc(game.home_team)}</strong>
        <small>Q${esc(game.period)} · ${formatClock(game.clock_seconds)} · Seq ${esc(game.event_sequence)}</small>
      </div>
      <div class="game-score">${esc(game.away_score)}–${esc(game.home_score)}</div>
    </article>
  `).join("");
}

function formatClock(total) {
  const secondsValue = Math.max(0, Math.round(Number(total)));
  return `${Math.floor(secondsValue / 60)}:${String(secondsValue % 60).padStart(2, "0")}`;
}

function renderMarketOptions(items) {
  const select = document.getElementById("market-filter");
  const current = select.value;
  const markets = [...new Set(items.map(item => item.market_key))].sort();
  select.innerHTML = `<option value="ALL">All markets</option>` +
    markets.map(market => `<option value="${esc(market)}">${labelMarket(market)}</option>`).join("");
  select.value = markets.includes(current) ? current : "ALL";
}

function labelMarket(key) {
  const labels = {
    player_points: "Player points",
    player_rebounds: "Player rebounds",
    player_assists: "Player assists",
    player_threes: "Player threes",
    player_points_rebounds_assists: "Player PRA",
    totals: "Game total",
    spreads: "Point spread",
    h2h: "Moneyline",
  };
  return labels[key] || key.replaceAll("_", " ");
}

function filteredItems() {
  const items = state.snapshot?.recommendations || [];
  return items.filter(item => {
    const statusMatch = state.status === "ALL" || item.status === state.status;
    const marketMatch = state.market === "ALL" || item.market_key === state.market;
    const gradeMatch = gradeRank[item.confidence_grade] >= gradeRank[state.grade];
    return statusMatch && marketMatch && gradeMatch;
  });
}

function renderRecommendations() {
  const items = filteredItems();
  const target = document.getElementById("recommendation-list");
  document.getElementById("result-count").textContent = `${items.length} opportunities`;

  if (!items.length) {
    target.innerHTML = `
      <div class="empty-state">
        No opportunities currently clear these filters. The engine will publish automatically
        when conservative ROI and all integrity gates pass.
      </div>`;
    return;
  }

  target.innerHTML = items.map(item => `
    <button class="rec-card ${state.selected === item.recommendation_id ? "selected" : ""}"
            data-id="${esc(item.recommendation_id)}">
      <span class="grade">${esc(item.confidence_grade)}</span>
      <span class="rec-name">
        <strong>${esc(item.player_name || labelMarket(item.market_key))}</strong>
        <small>${esc(item.side.toUpperCase())} ${item.line ?? ""} · ${esc(item.bookmaker_title)} ${item.american_odds > 0 ? "+" : ""}${esc(item.american_odds)}</small>
      </span>
      <span class="metric">
        <span class="metric-label">MODEL</span>
        <span class="metric-value">${pct(item.model_probability)}</span>
      </span>
      <span class="metric">
        <span class="metric-label">CONSERVATIVE ROI</span>
        <span class="metric-value ${item.conservative_roi > 0 ? "roi-positive" : ""}">${pct(item.conservative_roi)}</span>
      </span>
      <span class="metric optional">
        <span class="metric-label">FAIR / MARKET</span>
        <span class="metric-value">${odds(item.fair_decimal_odds)} / ${item.american_odds > 0 ? "+" : ""}${esc(item.american_odds)}</span>
      </span>
      <span class="status-badge ${esc(item.status)}">${esc(item.status)}</span>
    </button>
  `).join("");

  target.querySelectorAll(".rec-card").forEach(card => {
    card.addEventListener("click", () => selectRecommendation(card.dataset.id));
  });
}

function selectRecommendation(id) {
  state.selected = id;
  const item = (state.snapshot?.recommendations || []).find(value => value.recommendation_id === id);
  if (!item) return;
  document.getElementById("detail-empty").hidden = true;
  document.getElementById("detail-content").hidden = false;
  document.getElementById("detail-title").textContent =
    `${item.player_name || labelMarket(item.market_key)} · ${item.side.toUpperCase()} ${item.line ?? ""}`;
  document.getElementById("detail-projection").textContent =
    `${Number(item.projected_mean).toFixed(1)} ± ${Number(item.projected_sd).toFixed(1)}`;
  document.getElementById("detail-probability").textContent = pct(item.model_probability);
  document.getElementById("detail-fair").textContent = odds(item.fair_decimal_odds);
  document.getElementById("detail-price").textContent =
    `${item.bookmaker_title} ${item.american_odds > 0 ? "+" : ""}${item.american_odds}`;
  document.getElementById("detail-roi").textContent = pct(item.expected_roi);
  document.getElementById("detail-conservative").textContent = pct(item.conservative_roi);
  document.getElementById("detail-required").textContent = pct(item.required_roi);
  document.getElementById("detail-uncertainty").textContent = pct(item.total_uncertainty);
  document.getElementById("detail-age").textContent = seconds(item.market_age_seconds);
  renderDistribution(item);
  renderRecommendations();
}

function renderDistribution(item) {
  const target = document.getElementById("distribution");
  const mean = Number(item.projected_mean);
  const sd = Math.max(Number(item.projected_sd), .7);
  const start = Math.max(0, Math.floor(mean - 3 * sd));
  const end = Math.ceil(mean + 3 * sd);
  const values = [];
  for (let x = start; x <= end; x++) {
    const density = Math.exp(-.5 * Math.pow((x - mean) / sd, 2));
    values.push({x, density});
  }
  const max = Math.max(...values.map(value => value.density));
  target.innerHTML = values.map(value => `
    <span class="bar ${Math.round(Number(item.line)) === value.x ? "line" : ""}"
          style="height:${Math.max(3, value.density / max * 100)}%"
          data-label="${value.x}: relative ${Math.round(value.density / max * 100)}%"></span>
  `).join("");
}

document.querySelectorAll(".tab").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(item => item.classList.remove("active"));
    button.classList.add("active");
    state.status = button.dataset.status;
    renderRecommendations();
  });
});
document.getElementById("market-filter").addEventListener("change", event => {
  state.market = event.target.value;
  renderRecommendations();
});
document.getElementById("grade-filter").addEventListener("change", event => {
  state.grade = event.target.value;
  renderRecommendations();
});

async function fallbackFetch() {
  try {
    const response = await fetch("/api/v1/snapshot", {cache: "no-store"});
    render(await response.json());
    setConnection(true, "Live API");
  } catch {
    setConnection(false, "Reconnecting");
  }
}

function connect() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/ws/live`);
  socket.onopen = () => setConnection(true, "Live stream");
  socket.onmessage = event => render(JSON.parse(event.data));
  socket.onclose = () => {
    setConnection(false, "Reconnecting");
    setTimeout(connect, 2000);
  };
  socket.onerror = () => socket.close();
}

fallbackFetch();
connect();
