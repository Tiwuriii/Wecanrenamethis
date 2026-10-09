"""Optional: replace the Landsat surface temperature with NASA ECOSTRESS LST.

ECOSTRESS (on the ISS) samples at different times of day, so it can show afternoon heat
that Landsat's ~11 a.m. overpass misses. It needs a free NASA Earthdata login
(https://urs.earthdata.nasa.gov), stored in ~/.netrc or entered when prompted.

    .venv/Scripts/python -m pip install earthaccess
    .venv/Scripts/python pipeline/ecostress_optional.py
    .venv/Scripts/python pipeline/build.py

Updates pipeline/cache/tract_heat.json with lst_c from ECOSTRESS afternoon granules
(about 12:00-18:00 local), keeping the NLCD fields from heat.py. Run heat.py first.
"""
import json

import numpy as np
import rasterio
from shapely.geometry import shape

from common import CACHE, SJ_BBOX, load_cache
from heat import zonal_mean

SHORT_NAME = "ECO_L2T_LSTE"  # ECOSTRESS tiled LST, 70 m, Collection 2
SUMMERS = [("2023-06-01", "2023-09-30"), ("2024-06-01", "2024-09-30"), ("2025-06-01", "2025-09-30")]
AFTERNOON_UTC_HOURS = {"19", "20", "21", "22", "23", "00"}  # ~12:00-18:00 Pacific daylight time


def main():
    import earthaccess
    earthaccess.login()
    granules = []
    for start, end in SUMMERS:
        granules += earthaccess.search_data(short_name=SHORT_NAME, bounding_box=SJ_BBOX, temporal=(start, end))
    afternoon = [g for g in granules
                 if g["umm"]["TemporalExtent"]["RangeDateTime"]["BeginningDateTime"][11:13] in AFTERNOON_UTC_HOURS]
    print(f"  {len(afternoon)} afternoon granules of {len(granules)}")
    files = [str(f) for f in earthaccess.download(afternoon, str(CACHE / "ecostress"))]
    lst_files = [f for f in files if f.endswith("_LST.tif")]
    cloud_files = {f for f in files if f.endswith("_cloud.tif")}

    tracts = [(str(f["properties"]["tract"]), shape(f["geometry"])) for f in load_cache("ces4.json")["features"]]
    per_tract = {tid: [] for tid, _ in tracts}
    for path in lst_files:
        with rasterio.open(path) as src:
            arr = src.read(1).astype("float32")
            prof = {"crs": src.crs, "transform": src.transform}
        cloud = path.replace("_LST.tif", "_cloud.tif")
        if cloud in cloud_files:
            with rasterio.open(cloud) as c:
                arr[c.read(1) > 0] = np.nan
        arr[(arr <= 0) | (arr > 400)] = np.nan  # fill values; LST is in Kelvin
        arr -= 273.15
        for tid, v in zonal_mean(arr, prof, tracts).items():
            if v is not None:
                per_tract[tid].append(v)

    heat = json.loads((CACHE / "tract_heat.json").read_text())
    for tid, vals in per_tract.items():
        if vals:
            heat.setdefault(tid, {})["lst_c"] = float(np.median(vals))
            heat[tid]["lst_source"] = "ECOSTRESS"
    (CACHE / "tract_heat.json").write_text(json.dumps(heat))
    print("  updated tract_heat.json with ECOSTRESS afternoon LST; now run build.py")


if __name__ == "__main__":
    main()
