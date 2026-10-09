"""Step 3: join every source onto San José census tracts, analyze each project site,
summarize council districts, and write web/data/*.json for the map.

Geography: CalEnviroScreen 4.0 uses 2010 census tracts, which are the master unit here.
CDC PLACES (2020 tracts) is area-weighted onto them.
"""
import datetime
import json
import math
from collections import defaultdict

from shapely.geometry import Point
from shapely.ops import unary_union
from shapely.strtree import STRtree

from common import FT, MILE, arcgis_query, cached, feature, geoms, load_cache, to_utm, to_wgs, write_out

ZONING = "https://geo.sanjoseca.gov/server/rest/services/PLN/PLN_LandDesignations/MapServer/5"
RESIDENTIAL = ("R-", "UR")
MIXED_RES = ("MUN", "MUC", "UV", "UVC", "DC", "DC-NT1")

# Domain definitions: each is the mean of component percentiles (0-100, higher = worse).
# CES fields are statewide percentiles; "*_city" fields are percentiles within San José.
DOMAINS = {
    "air": ["dieselP", "pmP", "ozoneP", "trafficP"],
    "health": ["asthmaP", "cvdP", "lbwP", "copd_city"],
    "heat": ["lst_city", "imperv_city", "canopy_deficit_city"],
    "social": ["povP", "lingP", "eduP", "unempP", "housingBP"],
    "water": ["gwthreatsP", "drinkP", "iwbP", "cleanupsP", "hazP"],
}
CES_KEEP = ["CIscoreP", "PollutionP", "PopCharP", "dieselP", "pmP", "ozoneP", "trafficP", "asthmaP",
            "cvdP", "lbwP", "povP", "lingP", "eduP", "unempP", "housingBP", "gwthreatsP", "drinkP",
            "iwbP", "cleanupsP", "hazP", "diesel", "pm", "asthma", "pov", "ling",
            "Children_under10_pct", "Elderly_65over_pct", "Hispanic_pct", "Asian_American_pct",
            "White_pct", "African_American_pct"]
PLACES_KEYS = {"CASTHMA": "asthma_adult", "COPD": "copd", "CHD": "chd", "STROKE": "stroke",
               "BPHIGH": "bphigh", "DIABETES": "diabetes", "ACCESS2": "uninsured", "DISABILITY": "disability"}
RING_KEYS = ["CIscoreP", "dieselP", "pmP", "asthmaP", "cvdP", "povP", "lingP", "lst_c",
             "canopy_pct", "copd", "asthma_adult"]


def pct_rank(values):
    """Percentile rank (0-100) of each value among the non-null values."""
    valid = sorted(v for v in values if v is not None)
    n = len(valid)
    out = []
    for v in values:
        if v is None:
            out.append(None)
            continue
        lo = sum(1 for x in valid if x < v)
        eq = sum(1 for x in valid if x == v)
        out.append(100.0 * (lo + 0.5 * eq) / n)
    return out


def mean(xs):
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return sum(xs) / len(xs) if xs else None


def wmean(pairs):
    pairs = [(v, w) for v, w in pairs if v is not None and w > 0]
    tw = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / tw if tw else None


def load_tracts(city_utm):
    ces = load_cache("ces4.json")
    dac = {str(r["properties"]["Tract"]): r["properties"]["DAC_category"]
           for r in load_cache("sb535.json")["features"]}
    heat = load_cache("tract_heat.json")
    tracts = []
    for g, p in geoms(ces):
        gu = to_utm(g)
        share = gu.intersection(city_utm).area / gu.area
        if share < 0.05:
            continue
        tid = str(p["tract"])
        # CES uses -999 for suppressed values
        props = {k: (None if p.get(k) is None or p.get(k) < -998 else p.get(k)) for k in CES_KEEP}
        pop = p["ACS2019TotalPop"] or 0
        props.update({
            "tract": tid, "geoid": "0" + tid, "pop": pop, "city_share": round(share, 3),
            "kids": round(pop * (p.get("Children_under10_pct") or 0) / 100),
            "elders": round(pop * (p.get("Elderly_65over_pct") or 0) / 100),
            "dac": dac.get(tid), **heat.get(tid, {}),
        })
        tracts.append({"geom": g, "utm": gu, "p": props})
    return tracts


def join_places(tracts):
    """Area-weight CDC PLACES (2020 tracts) onto 2010 tracts."""
    by_geoid = defaultdict(dict)
    for r in load_cache("places.json"):
        if r.get("data_value") not in (None, ""):
            by_geoid[r["locationname"]][r["measureid"]] = float(r["data_value"])
    t20 = [(to_utm(g), p["GEOID"]) for g, p in geoms(load_cache("tracts2020.json"))]
    tree = STRtree([g for g, _ in t20])
    for t in tracts:
        acc = defaultdict(list)
        for idx in tree.query(t["utm"]):
            g20, gid = t20[idx]
            w = g20.intersection(t["utm"]).area
            if w <= 0:
                continue
            for mid, key in PLACES_KEYS.items():
                if mid in by_geoid.get(gid, {}):
                    acc[key].append((by_geoid[gid][mid], w))
        for key in PLACES_KEYS.values():
            t["p"][key] = wmean(acc[key])


def score_domains(tracts):
    ps = [t["p"] for t in tracts]
    for src, dst, invert in [("lst_c", "lst_city", False), ("imperv_pct", "imperv_city", False),
                             ("canopy_pct", "canopy_deficit_city", True), ("copd", "copd_city", False)]:
        vals = [(-p[src] if invert and p.get(src) is not None else p.get(src)) for p in ps]
        for p, r in zip(ps, pct_rank(vals)):
            p[dst] = r
    for name, comps in DOMAINS.items():
        raw = [mean([p.get(c) for c in comps]) for p in ps]
        for p, r in zip(ps, pct_rank(raw)):
            p[f"d_{name}"] = r  # re-ranked within San José so domains are comparable


def residential_share(pt_utm, radius):
    """Share (%) of zoned land within radius that is residential / mixed-use residential."""
    buf = pt_utm.buffer(radius)
    bb = to_wgs(buf).bounds
    key = f"zoning_{bb[0]:.4f}_{bb[1]:.4f}.json"
    fc = cached(key, lambda: arcgis_query(ZONING, out_fields="ZONINGABBREV", bbox=bb))
    res = mixed = total = 0.0
    for g, p in geoms(fc):
        a = to_utm(g.buffer(0)).intersection(buf).area
        z = (p.get("ZONINGABBREV") or "").split("(")[0]
        total += a
        if z.startswith(RESIDENTIAL):
            res += a
        elif z in MIXED_RES:
            mixed += a
    if not total:
        return None, None
    return round(res / total * 100, 1), round(mixed / total * 100, 1)


def analyze_sites(sites, tracts, districts, schools, health, cortese, wetlands_u, flood_u):
    out = []
    tract_tree = STRtree([t["utm"] for t in tracts])
    for s in sites:
        pt = Point(s["lon"], s["lat"])
        pu = to_utm(pt)
        rec = {k: s.get(k) for k in ["file", "address", "kind", "status", "submitted", "approved", "sqft",
                                     "mw", "generators", "generator_mw", "trees_removed", "acres",
                                     "cooling", "summary", "operator"]}
        rec["district"] = next((d["DISTRICT"] for g, d in districts if g.contains(pt)), None)
        host = next((t["p"] for t in tracts if t["geom"].contains(pt)), None)
        rec["host_tract"] = host["tract"] if host else None
        rec["host_dac"] = host["dac"] if host else None
        rings = {}
        for label, radius in [("ft1000", 1000 * FT), ("mi05", 0.5 * MILE), ("mi1", MILE)]:
            buf = pu.buffer(radius)
            pop = kids = elders = dac_pop = 0.0
            wpairs = defaultdict(list)
            for idx in tract_tree.query(buf):
                t = tracts[idx]
                frac = t["utm"].intersection(buf).area / t["utm"].area
                if frac <= 0:
                    continue
                w = t["p"]["pop"] * frac
                pop += w
                kids += t["p"]["kids"] * frac
                elders += t["p"]["elders"] * frac
                if t["p"]["dac"]:
                    dac_pop += w
                for k in RING_KEYS:
                    wpairs[k].append((t["p"].get(k), w))
            sch = [p for g, p in schools if g.distance(pu) <= radius]
            rings[label] = {
                "pop": round(pop), "kids": round(kids), "elders": round(elders),
                "dac_pop_pct": round(100 * dac_pop / pop, 1) if pop else 0,
                "schools": len(sch), "students": sum(p.get("EnrollTotal") or 0 for p in sch),
                "health": sum(1 for g, _ in health if g.distance(pu) <= radius),
                "cortese": sum(1 for g, _ in cortese if g.distance(pu) <= radius),
                **{f"w_{k}": wmean(v) for k, v in wpairs.items()},
            }
            if label == "mi1":
                rings[label]["school_names"] = sorted(p["SchoolName"] for p in sch)[:40]
                rings[label]["wetland_acres"] = round(wetlands_u.intersection(buf).area / 4046.86, 1)
        rec["rings"] = rings
        rec["in_flood_zone"] = bool(flood_u.contains(pu))
        rec["flood_within_1000ft"] = bool(flood_u.intersects(pu.buffer(1000 * FT)))
        rec["res_zoned_1000ft"], rec["mixed_zoned_1000ft"] = residential_share(pu, 1000 * FT)
        dist, nearest = min(((g.distance(pu), p) for g, p in schools), key=lambda x: x[0])
        rec["nearest_school"] = {"name": nearest["SchoolName"], "ft": round(dist / FT)}
        out.append(feature(pt, rec))
        print(f"    {s['address']:30s} D{rec['district']}  1mi pop={rings['mi1']['pop']:>6}  "
              f"schools(0.5mi)={rings['mi05']['schools']}  res<1000ft={rec['res_zoned_1000ft'] or 0:.0f}%")
    return out


def proximity(tracts, sites):
    """Distance (mi) from each tract to the nearest planned/approved/existing facility."""
    pts = [to_utm(Point(s["lon"], s["lat"])) for s in sites]
    for t in tracts:
        d = min(t["utm"].distance(p) for p in pts) / MILE
        t["p"]["dc_dist_mi"] = round(d, 2)
        # 100 within 0.5 mi, linear decay to 0 at 3 mi
        t["p"]["d_proximity"] = round(max(0.0, min(100.0, 100 * (3 - d) / 2.5)), 1)
        t["p"]["dc_within_1mi"] = sum(1 for p in pts if t["utm"].distance(p) <= MILE)


def summarize_districts(districts, tracts, site_feats):
    out = []
    for g, d in districts:
        gu = to_utm(g)
        pairs = defaultdict(list)
        pop = dac_pop = 0.0
        for t in tracts:
            frac = t["utm"].intersection(gu).area / t["utm"].area
            if frac <= 0.01:
                continue
            w = t["p"]["pop"] * frac
            pop += w
            if t["p"]["dac"]:
                dac_pop += w
            for k in ["CIscoreP", "dieselP", "asthmaP", "povP", "lingP", "lst_c", "canopy_pct",
                      "d_air", "d_health", "d_heat", "d_social", "d_water"]:
                pairs[k].append((t["p"].get(k), w))
        mine = [f["properties"] for f in site_feats if f["properties"]["district"] == d["DISTRICT"]]
        planned = [p for p in mine if p["status"] != "existing"]
        label_pt = g.representative_point()
        props = {
            "district": d["DISTRICT"], "member": d["COUNCILMEMBER"],
            "label": [label_pt.y, label_pt.x],
            "population": int(d["POPULATION"]) if d.get("POPULATION") else round(pop),
            "dac_pop_pct": round(100 * dac_pop / pop, 1) if pop else 0,
            "projects_review": sum(1 for p in planned if p["status"] == "under_review"),
            "projects_approved": sum(1 for p in planned if p["status"] == "approved"),
            "existing": len(mine) - len(planned),
            "mw_known": sum(p["mw"] or 0 for p in planned),
            "sqft": sum(p["sqft"] or 0 for p in planned),
            **{f"w_{k}": wmean(v) for k, v in pairs.items()},
        }
        out.append(feature(g.simplify(0.0002), props))
    return out


def fc(features):
    return {"type": "FeatureCollection", "features": features}


def main():
    districts = geoms(load_cache("districts.json"))
    city = unary_union([g.buffer(0) for g, _ in districts])
    city_utm = to_utm(city)
    print("  tracts ...")
    tracts = load_tracts(city_utm)
    join_places(tracts)
    score_domains(tracts)
    sites = load_cache("sites_geocoded.json")
    proximity(tracts, sites)

    near_city = city_utm.buffer(2 * MILE)

    def to_pts(data):
        return [(to_utm(g), p) for g, p in geoms(data) if near_city.contains(to_utm(g))]

    schools = to_pts(load_cache("schools.json"))
    health = [(g, p) for g, p in to_pts(load_cache("health.json"))
              if (p.get("FACILITY_STATUS_DESC") or "").lower() == "open"]
    cortese = to_pts(load_cache("cortese.json"))
    wetlands = [to_utm(g).buffer(0) for g, _ in geoms(load_cache("wetlands.json"))]
    wetlands_u = unary_union(wetlands)
    flood_u = unary_union([to_utm(g).buffer(0) for g, _ in geoms(load_cache("flood.json"))])

    print("  sites ...")
    site_feats = analyze_sites(sites, tracts, districts, schools, health, cortese, wetlands_u, flood_u)
    print("  districts ...")
    district_feats = summarize_districts(districts, tracts, site_feats)

    # ---- map layers ----
    near1 = city_utm.buffer(MILE)
    write_out("tracts.geojson", fc([
        feature(t["geom"].simplify(0.00015), {k: (round(v, 2) if isinstance(v, float) else v)
                                              for k, v in t["p"].items()}) for t in tracts]))
    write_out("sites.geojson", fc(site_feats))
    write_out("districts.geojson", fc(district_feats))
    write_out("city.geojson", fc([feature(city.simplify(0.0002), {})]))
    write_out("schools.geojson", fc([
        feature(to_wgs(g), {"name": p["SchoolName"], "district": p["DistrictName"], "level": p["SchoolLevel"],
                            "enroll": p["EnrollTotal"], "addr": p["Street"]})
        for g, p in schools if near1.contains(g)]))
    write_out("health.geojson", fc([
        feature(to_wgs(g), {"name": p["FACILITY_NAME"].title(), "type": p["LICENSE_CATEGORY_DESC"],
                            "beds": p["TOTAL_NUMBER_BEDS"]})
        for g, p in health if near1.contains(g)]))
    write_out("cortese.geojson", fc([
        feature(to_wgs(g), {"name": p["SITEFACILITYNAME"], "program": p["PROGRAMTYPE"], "status": p["STATUS"]})
        for g, p in cortese]))
    big_wet = unary_union([w for w in wetlands if w.area > 4046.86]).intersection(near1)  # > 1 acre
    write_out("wetlands.geojson", fc([feature(to_wgs(big_wet.simplify(15)), {})]))
    write_out("flood.geojson", fc([feature(to_wgs(flood_u.intersection(near1).simplify(20)), {})]))

    # basemap context: freeways, major arterials, bay / reservoirs
    view = to_wgs(city_utm.buffer(1.5 * MILE))
    for name, src, keep_name in [("roads_primary", "roads_primary.json", True),
                                 ("roads_secondary", "roads_secondary.json", False),
                                 ("water", "water.json", False)]:
        feats = []
        for g, p in geoms(load_cache(src)):
            clipped = g.intersection(view)
            if not clipped.is_empty:
                feats.append(feature(clipped, {"name": p.get("NAME")} if keep_name else {}))
        write_out(f"{name}.geojson", fc(feats), precision=4)

    # ---- headline numbers ----
    proj = [f["properties"] for f in site_feats if f["properties"]["status"] != "existing"]
    in_1mi = unary_union([to_utm(Point(s["lon"], s["lat"])).buffer(MILE) for s in sites
                          if s["status"] != "existing"])
    pop_1mi = sum(t["p"]["pop"] * t["utm"].intersection(in_1mi).area / t["utm"].area for t in tracts)
    kids_1mi = sum(t["p"]["kids"] * t["utm"].intersection(in_1mi).area / t["utm"].area for t in tracts)
    school_pts = [(g, p) for g, p in schools if in_1mi.contains(g)]
    summary = {
        "generated": datetime.date.today().isoformat(),
        "city_population": sum(int(d["POPULATION"]) for _, d in districts if d.get("POPULATION")),
        "n_review": sum(1 for p in proj if p["status"] == "under_review"),
        "n_approved": sum(1 for p in proj if p["status"] == "approved"),
        "n_review_dc": sum(1 for p in proj if p["status"] == "under_review" and p["kind"] == "data_center"),
        "n_approved_dc": sum(1 for p in proj if p["status"] == "approved" and p["kind"] == "data_center"),
        "n_existing_mapped": len(site_feats) - len(proj),
        "mw_known_total": sum(p["mw"] or 0 for p in proj),
        "mw_known_datacenters": sum(p["mw"] or 0 for p in proj if p["kind"] == "data_center"),
        "n_mw_unstated": sum(1 for p in proj if not p["mw"]),
        "sqft_total": sum(p["sqft"] or 0 for p in proj),
        "trees_removed_known": sum(p["trees_removed"] or 0 for p in proj),
        "generators_known": sum(p["generators"] or 0 for p in proj),
        "pop_within_1mi": round(pop_1mi),
        "kids_within_1mi": round(kids_1mi),
        "schools_within_1mi": len(school_pts),
        "students_within_1mi": sum(p.get("EnrollTotal") or 0 for _, p in school_pts),
        "pge_deal_mw": 880,
        "domains": DOMAINS,
    }
    write_out("summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "domains"}, indent=1))


if __name__ == "__main__":
    main()
