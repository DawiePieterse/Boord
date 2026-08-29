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

## The decision still to make

Boord kept the endpoints this app was built on - `/api/analysis/summary`,
`/api/weather/history`, `/api/risk/summary`, `/api/risk/forecast`. They are
admin-JWT authenticated and, since the Owner View left, nothing in Boord's own
UI calls them. They were left in place precisely so this app has something to
talk to over the network on day one.

So there are two shapes available, and picking one is the first real decision:

1. **A frontend against the farm server.** Keep `owner_view.py`, put it back on
   the farm server, and the new app is the `frontend/` folder plus its own copy
   of the shared files. Least work. The owner's device has to be able to reach
   the farm server - which is what the Tailscale section of `MANUAL.md` is for.
2. **Its own backend.** The new app carries its own copy of the analysis, risk
   and weather builders, and gets farm data some other way. More work, and the
   builders would then exist twice, but it does not require the farm server to
   be reachable from outside the farm.

If (2) wins, Boord should drop those four endpoints and probably `WeatherHistory`
with them - it is roughly 42 MB of a 42.4 MB database, and the whole
empty-and-fingerprint design in `backend/backup.py` exists only because of its
size.
