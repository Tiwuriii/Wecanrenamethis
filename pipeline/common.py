"""Shared helpers for the San José data center impact pipeline."""
import json
import time
from pathlib import Path

import requests
from shapely.geometry import shape, mapping
from shapely.ops import transform
from pyproj import Transformer

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "pipeline" / "cache"
OUT = ROOT / "web" / "data"
CACHE.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "sj-datacenter-impact-map/1.0 (civic research)"}

# San José study bbox (lon/lat), padded to catch tracts straddling the city edge
SJ_BBOX = (-122.05, 37.12, -121.58, 37.47)

# Metric CRS for distance/area work (UTM 10N)
_to_utm = Transformer.from_crs(4326, 32610, always_xy=True).transform
_to_wgs = Transformer.from_crs(32610, 4326, always_xy=True).transform


def to_utm(geom):
    return transform(_to_utm, geom)


def to_wgs(geom):
    return transform(_to_wgs, geom)


FT = 0.3048
MILE = 1609.344


def get_json(url, params=None, data=None, retries=4, timeout=120):
    for attempt in range(retries):
        try:
            if data is not None:
                r = requests.post(url, data=data, headers=UA, timeout=timeout)
            else:
                r = requests.get(url, params=params, headers=UA, timeout=timeout)
            r.raise_for_status()
            js = r.json()
            if isinstance(js, dict) and "error" in js:
                raise RuntimeError(f"{url}: {js['error']}")
            return js
        except Exception as exc:  # public GIS servers are flaky; retry with backoff
            if attempt == retries - 1:
                raise
            print(f"  retry {attempt + 1} for {url}: {exc}")
            time.sleep(3 * (attempt + 1))


def arcgis_query(layer_url, where="1=1", out_fields="*", bbox=None, page=1000, extra=None):
    """Page through an ArcGIS REST layer and return a GeoJSON FeatureCollection in WGS84."""
    features, offset = [], 0
    while True:
        params = {
            "where": where,
            "outFields": out_fields,
            "outSR": 4326,
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": page,
            "returnGeometry": "true",
        }
        if bbox:
            params.update({
                "geometry": ",".join(map(str, bbox)),
                "geometryType": "esriGeometryEnvelope",
                "inSR": 4326,
                "spatialRel": "esriSpatialRelIntersects",
            })
        if extra:
            params.update(extra)
        js = get_json(f"{layer_url}/query", data=params)
        batch = js.get("features", [])
        features.extend(batch)
        exceeded = js.get("exceededTransferLimit") or js.get("properties", {}).get("exceededTransferLimit")
        if not batch or (len(batch) < page and not exceeded):
            break
        offset += len(batch)
    return {"type": "FeatureCollection", "features": features}


def cached(name, fn):
    """Run fn() once and cache its JSON result under pipeline/cache/name."""
    path = CACHE / name
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    print(f"  fetching {name} ...")
    result = fn()
    path.write_text(json.dumps(result), encoding="utf-8")
    return result


def load_cache(name):
    return json.loads((CACHE / name).read_text(encoding="utf-8"))


def write_out(name, obj, precision=5):
    """Write JSON to web/data, rounding floats to keep files small."""
    def rnd(o):
        if isinstance(o, float):
            return round(o, precision)
        if isinstance(o, (list, tuple)):
            return [rnd(x) for x in o]
        if isinstance(o, dict):
            return {k: rnd(v) for k, v in o.items()}
        return o
    path = OUT / name
    path.write_text(json.dumps(rnd(obj), separators=(",", ":")), encoding="utf-8")
    print(f"  wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1024:.0f} KB)")


def feature(geom, props):
    return {"type": "Feature", "geometry": mapping(geom), "properties": props}


def geoms(fc):
    return [(shape(f["geometry"]), f["properties"]) for f in fc["features"] if f.get("geometry")]
