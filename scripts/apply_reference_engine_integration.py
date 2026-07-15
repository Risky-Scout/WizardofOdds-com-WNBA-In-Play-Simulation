#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "src" / "wizard_wnba" / "api.py"
APP = ROOT / "src" / "wizard_wnba" / "static" / "app.js"
HTML = ROOT / "src" / "wizard_wnba" / "static" / "index.html"


def replace_region(
    source: str,
    start: str,
    end: str,
    replacement: str,
) -> str:
    start_index = source.find(start)
    end_index = source.find(end, start_index + len(start))
    if start_index < 0 or end_index < 0:
        raise RuntimeError(
            f"markers not found: {start!r} -> {end!r}"
        )
    return (
        source[:start_index]
        + replacement.rstrip()
        + "\n\n"
        + source[end_index:]
    )


def patch_api() -> None:
    text = API.read_text(encoding="utf-8")

    if "from pydantic import BaseModel, Field" not in text:
        marker = "import uvicorn\n"
        if marker not in text:
            raise RuntimeError("uvicorn import marker not found")
        text = text.replace(
            marker,
            marker + "from pydantic import BaseModel, Field\n",
            1,
        )

    if "from .live_markets import build_live_market_feed" not in text:
        marker = "from .demo import build_demo_snapshot\n"
        if marker not in text:
            raise RuntimeError("demo import marker not found")
        text = text.replace(
            marker,
            marker
            + "from .live_markets import build_live_market_feed\n",
            1,
        )

    if "from .live_reference import" not in text:
        marker = "from .live_markets import build_live_market_feed\n"
        text = text.replace(
            marker,
            marker
            + "from .live_reference import (\n"
            + "    LiveReferenceError,\n"
            + "    simulate_live_reference,\n"
            + ")\n",
            1,
        )

    request_class = '''class LiveReferenceScenarioRequest(BaseModel):
    market_id: str
    side: str | None = None
    line: float | None = None
    american_odds: int | None = None
    current_total: float | None = Field(default=None, ge=0)
    remaining_minutes: float | None = Field(
        default=None,
        ge=0,
        le=50,
    )
    simulations: int = Field(default=20_000, ge=1_000, le=100_000)


'''

    if "class LiveReferenceScenarioRequest" not in text:
        marker = "publication_store = AtomicPublicationStore(\n"
        start = text.find(marker)
        if start < 0:
            raise RuntimeError("publication store marker not found")
        close = text.find("\n)\n", start)
        if close < 0:
            raise RuntimeError("publication store closing marker not found")
        close += len("\n)\n")
        text = text[:close] + "\n\n" + request_class + text[close:]

    route = '''@app.post("/api/v1/live-reference-simulation")
async def live_reference_simulation(
    request: LiveReferenceScenarioRequest,
) -> dict[str, Any]:
    snapshot_value = publication_store.read()
    market_feed = build_live_market_feed(
        settings.data_dir,
        list(snapshot_value.get("games", [])),
    )
    try:
        return simulate_live_reference(
            data_dir=settings.data_dir,
            snapshot=snapshot_value,
            market_feed=market_feed,
            market_id=request.market_id,
            side=request.side,
            line=request.line,
            american_odds=request.american_odds,
            current_total=request.current_total,
            remaining_minutes=request.remaining_minutes,
            simulations=request.simulations,
            seed=settings.random_seed,
        )
    except LiveReferenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


'''

    if '@app.post("/api/v1/live-reference-simulation")' not in text:
        markers = (
            '@app.post("/api/v1/scenario-lab")',
            '@app.get("/api/v1/model-card")',
        )
        position = -1
        for marker in markers:
            position = text.find(marker)
            if position >= 0:
                break
        if position < 0:
            raise RuntimeError("API insertion marker not found")
        text = text[:position] + route + text[position:]

    API.write_text(text, encoding="utf-8")


def patch_app() -> None:
    text = APP.read_text(encoding="utf-8")

    prepare = r'''function prepareScenario() {
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

  const sideValues = new Set(["over", "under", "home", "away"]);
  byId("scenario-side").value = sideValues.has(item.side)
    ? item.side
    : "over";

  byId("scenario-line").value =
    item.line == null
      ? item.market_key === "h2h" ? 0.5 : ""
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
}'''

    text = replace_region(
        text,
        "function prepareScenario() {",
        "function renderScenarioResult(result) {",
        prepare,
    )

    render_result = r'''function renderScenarioResult(result) {
  byId("scenario-result").innerHTML = `
    <div class="result-grid">
      <div>
        <small>Win probability</small>
        <strong>${percentage(result.win_probability)}</strong>
      </div>
      <div>
        <small>Push probability</small>
        <strong>${percentage(result.push_probability)}</strong>
      </div>
      <div>
        <small>Fair American odds</small>
        <strong>${
          result.fair_american_odds > 0
            ? `+${result.fair_american_odds}`
            : result.fair_american_odds ?? "—"
        }</strong>
      </div>
      <div>
        <small>Expected ROI</small>
        <strong>${percentage(result.expected_roi)}</strong>
      </div>
      <div>
        <small>Conservative ROI</small>
        <strong>${percentage(result.conservative_roi)}</strong>
      </div>
      <div>
        <small>Simulations</small>
        <strong>${Number(result.simulation_count).toLocaleString()}</strong>
      </div>
      <div>
        <small>Projected mean</small>
        <strong>${Number(result.projected_mean).toFixed(2)}</strong>
      </div>
      <div>
        <small>Model confidence</small>
        <strong>${
          Number(result.total_uncertainty) <= 0.04
            ? "High"
            : Number(result.total_uncertainty) <= 0.08
              ? "Medium"
              : "Low"
        }</strong>
      </div>
    </div>
    <p class="simulation-note">
      ${escapeHtml(result.note || "")}
    </p>`;
}'''

    text = replace_region(
        text,
        "function renderScenarioResult(result) {",
        "async function runScenario(event) {",
        render_result,
    )

    run = r'''async function runScenario(event) {
  event.preventDefault();

  const item = selectedRecommendation();
  const error = byId("scenario-error");
  error.textContent = "";

  if (!item) {
    error.textContent = "Select a live sportsbook line.";
    return;
  }

  const line = Number(byId("scenario-line").value);
  const odds = Number(byId("scenario-odds").value);

  if (!Number.isFinite(line)) {
    error.textContent = "Enter a valid custom line.";
    return;
  }

  if (
    !Number.isInteger(odds)
    || odds === 0
    || (odds > -100 && odds < 100)
  ) {
    error.textContent =
      "Enter American odds such as -110 or +105.";
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
    line,
    american_odds: odds,
    remaining_minutes: remaining,
    simulations: 20000,
  };

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
    const result = await fetchJson(
      "/api/v1/live-reference-simulation",
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
}'''

    text = replace_region(
        text,
        "async function runScenario(event) {",
        "function render(snapshot) {",
        run,
    )

    text = text.replace(
        '"Live game collection is working. Modeled selections are paused because the validated production model is not installed."',
        '"Live collection and the Scenario Lab simulator are active. Official automated selections remain paused until out-of-sample calibrators are attached."',
    )

    APP.write_text(text, encoding="utf-8")


def patch_html() -> None:
    text = HTML.read_text(encoding="utf-8")
    text = re.sub(
        r'/static/app\.js\?v=[^"]+',
        "/static/app.js?v=reference-engine-live-v1",
        text,
        count=1,
    )
    text = text.replace(
        "Select a modeled live market to evaluate a custom line and price.",
        "Select a live sportsbook line to run the possession-based simulator.",
    )
    text = text.replace(
        "Select a live sportsbook line to load its side, line, American odds, and live game clock.",
        "Select a live sportsbook line to run the possession-based simulator.",
    )
    HTML.write_text(text, encoding="utf-8")


def main() -> int:
    for path in (API, APP, HTML):
        if not path.exists():
            print(f"ERROR: missing file: {path}", file=sys.stderr)
            return 2

    patch_api()
    patch_app()
    patch_html()
    print("REFERENCE SIMULATOR INTEGRATION APPLIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
