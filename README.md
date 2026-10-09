# San José Data Center Burden Atlas

An interactive map for San José City Council members and staff. It shows who lives next to the city's data center pipeline and what environmental and health burdens those neighborhoods already carry. Everything is built from public data and can be rebuilt with one command.

## What's on the map

**Project sites.** The city's own list of data center and large-energy permit applications: 8 under review, 3 approved, and 2 non-data-center large energy users. A partial list of operating facilities is also shown. Each site gets 1,000-ft, ½-mile and 1-mile ring statistics.

**Indicators, chosen by how a data center affects a neighborhood:**

| Pathway | Why it matters for data centers | Indicators |
|---|---|---|
| Air | Diesel backup generators are tested monthly and run during outages; NOx feeds ozone | CalEnviroScreen diesel PM, PM2.5, ozone, traffic |
| Health | The people most harmed by added particulates | CES asthma and heart-attack ER visits, low birth weight; CDC PLACES adult asthma, COPD, heart disease |
| Heat | Large roofs, waste heat, paving and tree removal | Landsat 8/9 summer surface temperature (30 m), NLCD impervious surface and tree canopy |
| Water & land | Cooling demand, fuel storage, contaminated sites | CES groundwater threats, drinking water, impaired waters, cleanups, hazardous waste; FEMA flood zones, wetlands, Cortese list |
| Social & ratepayer | Who bears the costs and who is reached by public notice | Poverty, linguistic isolation, education, unemployment, housing burden; SB 535 disadvantaged communities |
| Sensitive receptors | CARB and BAAQMD screen sources within 1,000 ft of homes, schools and hospitals | CDE schools with enrollment, CDPH hospitals and care facilities, residential zoning share |

**Cumulative burden index.** Six domains (air, health, heat, social, water, proximity), each ranked 0–100 across San José tracts. Viewers set the weights, so the index is transparent and adjustable instead of a black box.

**Council districts.** Project counts, stated MW and population-weighted indicators for each district.

## Run it

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # macOS/Linux: .venv/bin/python
.venv/Scripts/python pipeline/run_all.py                   # ~5 minutes; downloads into pipeline/cache
python -m http.server 8000 -d web                          # open http://localhost:8000
```

The page has to be served over http; opening `index.html` directly from disk blocks the data files.

## Keeping it current

- **New or changed permits:** edit `pipeline/sites.json`, delete `pipeline/cache/sites_geocoded.json`, and rerun. Leave a field `null` when the filing doesn't state it. Add `"lon"`/`"lat"` to any address the geocoder misses.
- **Refresh every source:** delete `pipeline/cache/` and rerun.
- **ECOSTRESS instead of Landsat heat:** see `pipeline/ecostress_optional.py` (needs a free NASA Earthdata login).

## Limits to state when presenting

- Indicators describe existing burden. They are not a model of any project's emissions; that requires a BAAQMD health risk assessment for each site.
- CalEnviroScreen 4.0 uses 2010 tracts and 2015–2019 ACS population. Ring populations are apportioned by area, so 1,000-ft counts are approximate.
- Landsat measures ground surface temperature at about 11 a.m., which runs hotter than air temperature.
- The operating data center list is partial (operator-published addresses only).
- The city's project list was read from its July 13, 2026 update (via an August 2026 web archive snapshot because the live site blocks automated access). Check it against sjpermits.org before citing.

## Layout

```
pipeline/  fetch.py (sources) → heat.py (Landsat + NLCD) → build.py (joins, rings, districts)
           sites.json is the curated permit list
web/       index.html (the atlas) and data/ (generated layers)
```
