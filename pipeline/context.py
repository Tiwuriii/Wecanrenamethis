"""Step 4: statewide and national context for the 'Beyond San José' tab.

- EIA residential electricity prices by state, 2015 onward (EIA API v2, public DEMO_KEY;
  set EIA_API_KEY for heavier use).
- Curated precedents from pipeline/precedents.json.

Output: web/data/context.json
"""
import json
import os

from common import ROOT, cached, get_json, write_out

EIA = "https://api.eia.gov/v2/electricity/retail-sales/data/"
# states with major data center build-outs or policy actions referenced in precedents.json
FOCUS = ["CA", "VA", "TX", "GA", "OR", "AZ", "IN", "TN"]
START_YEAR = 2015

CA_FACTS = [
    {"value": "1.8 → 4.9 GW", "label": "added data center load forecast for California's main grid by 2030 and by 2040 (CEC 2025 forecast)",
     "source": "CAISO large load issue paper, Jan 2026", "url": "https://www.caiso.com/documents/issue-paper-large-load-consideration-jan-20-2026.pdf"},
    {"value": "12.7 GW", "label": "PG&E data center queue in July 2026, with only 490 MW under signed agreements",
     "source": "Utility Dive, July 24, 2026", "url": "https://www.utilitydive.com/news/pge-claims-127-gw-in-data-center-pipeline-as-utility-courts-smaller-loads/826099/"},
    {"value": "55", "label": "operating data centers in neighboring Santa Clara, where the grid now caps new ones",
     "source": "San José Spotlight, May 30, 2025", "url": "https://sanjosespotlight.com/santa-clara-data-centers-hit-max-energy-capacity/"},
]


def fetch_prices():
    params = {
        "api_key": os.environ.get("EIA_API_KEY", "DEMO_KEY"),
        "frequency": "annual", "data[0]": "price", "facets[sectorid][]": "RES",
        "start": str(START_YEAR), "length": 5000,
    }
    rows = get_json(EIA, params=params)["response"]["data"]
    return [{"state": r["stateid"], "name": r["stateDescription"], "year": int(r["period"]),
             "price": float(r["price"])} for r in rows if r.get("price") not in (None, "")]


def main():
    rows = cached("eia_res_prices.json", fetch_prices)
    series = {}
    for r in rows:
        series.setdefault(r["state"], {"name": r["name"], "prices": {}})["prices"][r["year"]] = r["price"]
    latest = max(series["US"]["prices"])
    # rank among the 50 states + DC (EIA also returns regional aggregates and the US total)
    states = {k: v for k, v in series.items() if len(k) == 2 and k != "US" and latest in v["prices"]}
    ranked = sorted(states, key=lambda k: -states[k]["prices"][latest])
    contiguous = [k for k in ranked if k not in ("HI", "AK")]

    def pack(code):
        s = series[code]
        p = s["prices"]
        years = sorted(p)
        return {"code": code, "name": s["name"], "years": years, "prices": [p[y] for y in years],
                "change_since_2020_pct": round(100 * (p[latest] / p[2020] - 1), 1) if 2020 in p else None}

    out = {
        "latest_year": latest,
        "us": pack("US"),
        "focus": [pack(c) for c in FOCUS if c in series],
        "ca_rank_all": ranked.index("CA") + 1,
        "ca_rank_contiguous": contiguous.index("CA") + 1,
        "n_states": len(ranked),
        "ca_facts": CA_FACTS,
        "precedents": json.loads((ROOT / "pipeline" / "precedents.json").read_text(encoding="utf-8"))["precedents"],
        "price_source": {"name": "U.S. Energy Information Administration, retail sales of electricity (residential)",
                         "url": "https://www.eia.gov/electricity/data/browser/"},
    }
    write_out("context.json", out, precision=2)
    ca, us = out["focus"][0], out["us"]
    print(f"  {len(states)} states; CA {latest}: {ca['prices'][-1]} c/kWh (rank {out['ca_rank_all']} of "
          f"{out['n_states']}, {out['ca_rank_contiguous']} among contiguous); US {us['prices'][-1]} c/kWh; "
          f"CA +{ca['change_since_2020_pct']}% since 2020 vs US +{us['change_since_2020_pct']}%")


if __name__ == "__main__":
    main()
