# Boord Owner - the starting point for a separate app

This is the Owner View, lifted out of Boord whole. Nothing here has been
refactored, renamed or tidied on the way out: it is the code as it ran on the
farm server, so that the app built from it starts from something known to work
rather than from a rewrite nobody has tested.

It is **not a running application**. It is a folder of parts. Boord does not
serve it - `backend/main.py` mounts only `frontend/`, `templates/` and
`photos/`, and none of them is here - and no route in Boord reaches it.

Delete this folder once the separate app exists.

## What is here

```
frontend/
  index.html          the four-tab screen: Dashboard, Analysis, Weather, Risk
  owner.js            token handling, data loading, the dashboard cache
  service-worker.js   offline shell, cache prefix "boord-owner-"
  shared/
    analysis-tab.js   LWAnalysisTab - season pace, block yield, heatmaps
    weather-tab.js    LWWeatherTab  - the 1987-present chart
    risk-tab.js       LWRiskTab     - risk score and harvest forecast
    charts.js         LWCharts, used only by those three
    vendor/html2canvas, vendor/jspdf   used only by charts.js exportPDF()
backend/
  owner_view.py       the /api/owner-view router, token-gated
docs/
  TRAINING_OWNER.md         the owner's guide
  GUIDE_WEATHER_AND_RISK.md how the three tabs work, and the risk model
```

## What is *not* here, and has to come from somewhere

These stayed in Boord because Boord still uses them. The new app needs its own
copy of each.

**Frontend.** `shared/api.js` (`Boord.api`, `Boord.isNetworkError`, the VERSION
constant `scripts/release.sh` checks), `shared/styles.css`, `shared/tailwind.js`,
`shared/ptr.js` (pull-to-refresh), `shared/vendor/fontawesome/`, and
`admin/icons/icon-192.png` - the favicon `frontend/index.html` still points at
as `../admin/icons/icon-192.png`.

**Backend.** `owner_view.py` is a thin token-gated wrapper; every figure it
returns is built somewhere else:

| It imports | From |
| --- | --- |
| `build_analysis_summary` | `backend/routers/analysis.py` |
| `build_risk_summary`, `build_harvest_forecast` | `backend/routers/risk.py` |
| `build_weather_history` | `backend/routers/weather.py` |
| `sync_recent_weather` | `backend/weather.py` |
| `_supplier_display_name`, `_worker_ids_for_supplier`, `_worker_totals` | `backend/routers/payments.py` |
| `get_own_supplier_id`, `get_session` | `backend/db.py` |
| `Block`, `HarvestRecord`, `Supplier`, `Worker` | `backend/models.py` |
| `get_current_admin` | `backend/security.py` |
| `day_bounds` | `backend/timeutil.py` |

`OwnerViewToken` went with it. It was a single-row table holding one shared
secret, and it is written out here so it does not have to be recovered from
git:

```python
class OwnerViewToken(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    token: str
```

`backend/db.py`'s `seed_defaults()` created the row on first run if it was
missing, and `_get_token()` in `owner_view.py` does the same thing lazily.

**Endpoints it reads that were never owner-specific.** `owner.js` calls these
directly rather than through `/api/owner-view`, and they are still
unauthenticated in Boord: `/api/lots/pending`, `/api/lots/in-transit`,
`/api/lots/received`, `/api/suppliers`, `/api/system-settings`,
`/api/weather/current`.

## The shape it takes

Decided, and Boord has already been cut to match. The Owner app is **part of
the farm's setup, running beside Boord on the farm server** - not a hosted
service, and not a screen inside Boord.

**It reads Boord's SQLite file directly, read-only.** `data/boord.db` holds
the live harvest: crates, lots, workers, blocks, suppliers, payments. Open it
read-only and do not write to it - Boord is the only writer, and two writers
on one SQLite file is exactly the kind of bug that shows up first on a farm
during a harvest. Two things to respect:

- **Its schema is Boord's, and Boord migrates it on every startup.** There is
  no stable API between you; a column can be renamed by a migration in Boord's
  `backend/migrations/versions/`. Read defensively and pin what you depend on.
- **The nightly backup and the pre-migration snapshot both copy the file.**
  Neither takes a lock this app needs to worry about, but a long read held
  open across a migration will see the schema change underneath it.

**It owns its own data for weather and history.** Its own database, separate
from Boord's:

- `WeatherHistory` - the hourly record. `weather.py` and `weather_router.py`
  here are Boord's complete originals, including the location columns and the
  1987-onward backfill.
- `HistoricalHarvest` / `HistoricalAnnualYield` - the seasons before Boord.
  Import them with `scripts/import_historical_*.py` against this app's
  database; `docs/HISTORICAL_DATA.md` covers the workbooks and the one real
  caveat in the figures.

## What Boord no longer has

All of it moved here, and Boord's migration `748269cfa3ea` drops the three
tables. Boord kept exactly two pieces of weather, both of which call
Open-Meteo live and never touched `WeatherHistory`: the header readout, and
the conditions stamped onto each crate at dispatch.

Gone from Boord, and yours now:

| Was | Now here |
| --- | --- |
| `routers/analysis.py` | `backend/analysis.py` |
| `routers/risk.py` | `backend/risk.py` |
| `routers/historical.py` | `backend/historical.py` |
| the history half of `weather.py` | `backend/weather.py` (whole file) |
| `/api/weather/history` + backfill | `backend/weather_router.py` (whole file) |
| the Historical Harvest Data report | `backend/historical_report.py` |
| the four import scripts | `scripts/` |
| the two CSV templates | `templates/` |
| ~1,000 lines of selftest | not moved - see below |

**The tests did not come with it.** Boord's `scripts/selftest.py` lost about
a thousand lines when this left: the Risk indicator's scoring, the Harvest
Forecast's projections, the driver configuration, the stored-weather
integrity checks, and the report's sheet reconciliation. They are in Boord's
git history at commit `2226750` and worth recovering rather than
rewriting - that arithmetic is the part of this code where a wrong answer
looks exactly like a right one.
