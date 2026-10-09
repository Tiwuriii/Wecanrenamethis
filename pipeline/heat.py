"""Step 2: urban heat and land cover per census tract.

- Summer land surface temperature (LST): median of cloud-free Landsat 8/9 Collection 2
  Level-2 surface-temperature scenes, Jun-Sep 2023-2025, via Microsoft Planetary Computer
  (no login required). Neighborhood-scale proxy for the ECOSTRESS signal; see
  ecostress_optional.py to substitute ECOSTRESS LST.
- Impervious surface and tree canopy: NLCD 2021 via the MRLC WCS (30 m).

Outputs:
  pipeline/cache/tract_heat.json   {tract_id: {lst_c, imperv_pct, canopy_pct}}
  web/data/lst.png + lst_bounds.json  (map overlay)
"""
import json

import numpy as np
import planetary_computer
import pystac_client
import rasterio
import requests
from PIL import Image
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds, transform_geom
from rasterio.windows import from_bounds
from shapely.geometry import shape, mapping

from common import CACHE, OUT, SJ_BBOX, load_cache

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
WCS = ("https://www.mrlc.gov/geoserver/mrlc_download/wcs?service=WCS&version=2.0.1&request=GetCoverage"
       "&coverageId={cov}&subset=X(-2264290,-2214008)&subset=Y(1865398,1914255)&format=image/geotiff")
NLCD = {
    "imperv_pct": "mrlc_download__NLCD_2021_Impervious_L48",
    "canopy_pct": "mrlc_download__nlcd_tcc_conus_2021_v2021-4",
}
MAX_SCENES = 12


def landsat_lst():
    """Return (median summer LST in deg C, grid profile) on the scenes' UTM 10N grid."""
    catalog = pystac_client.Client.open(STAC, modifier=planetary_computer.sign_inplace)
    items = []
    for year in (2023, 2024, 2025):
        search = catalog.search(
            collections=["landsat-c2-l2"], bbox=SJ_BBOX,
            datetime=f"{year}-06-01/{year}-09-30",
            query={"eo:cloud_cover": {"lt": 10}, "platform": {"in": ["landsat-8", "landsat-9"]}})
        items += list(search.items())
    # one WRS tile (path 44 / row 34) covers the whole city; avoid mixing grids
    items = [i for i in items if i.properties.get("landsat:wrs_path") == "044"
             and i.properties.get("landsat:wrs_row") == "034"]
    items = sorted(items, key=lambda i: i.properties["eo:cloud_cover"])[:MAX_SCENES]
    if not items:
        raise RuntimeError("No Landsat scenes found")
    print(f"  using {len(items)} Landsat scenes: " + ", ".join(i.datetime.strftime('%Y-%m-%d') for i in items))

    stack, profile = [], None
    for item in items:
        with rasterio.open(item.assets["lwir11"].href) as st, rasterio.open(item.assets["qa_pixel"].href) as qa:
            bounds = transform_bounds(4326, st.crs, *SJ_BBOX)
            win = from_bounds(*bounds, transform=st.transform).round_offsets().round_lengths()
            dn = st.read(1, window=win).astype("float32")
            q = qa.read(1, window=win)
            if profile is None:
                profile = {"crs": st.crs, "transform": st.window_transform(win),
                           "height": dn.shape[0], "width": dn.shape[1]}
            elif dn.shape != (profile["height"], profile["width"]):
                continue
        # QA_PIXEL bits: 1 dilated cloud, 3 cloud, 4 cloud shadow, 7 water
        bad = (q & ((1 << 1) | (1 << 3) | (1 << 4) | (1 << 7))) > 0
        lst = dn * 0.00341802 + 149.0 - 273.15
        lst[(dn == 0) | bad] = np.nan
        stack.append(lst)
    return np.nanmedian(np.stack(stack), axis=0), profile


def zonal_mean(arr, profile, tracts):
    """Mean of arr per tract (tracts: list of (id, shapely geom in WGS84))."""
    shapes = [(transform_geom("EPSG:4326", profile["crs"], mapping(g)), i + 1)
              for i, (_, g) in enumerate(tracts)]
    zones = rasterize(shapes, out_shape=arr.shape, transform=profile["transform"], fill=0, dtype="int32")
    valid = np.isfinite(arr) & (zones > 0)
    sums = np.bincount(zones[valid], weights=arr[valid], minlength=len(tracts) + 1)
    counts = np.bincount(zones[valid], minlength=len(tracts) + 1)
    return {tid: (float(sums[i + 1] / counts[i + 1]) if counts[i + 1] else None)
            for i, (tid, _) in enumerate(tracts)}


def write_overlay(lst_utm, profile):
    """Color-ramped PNG of LST in Web Mercator plus its lon/lat bounds for a map image overlay."""
    dst_crs = "EPSG:3857"
    left, bottom, right, top = rasterio.transform.array_bounds(profile["height"], profile["width"], profile["transform"])
    dst_tr, w, h = calculate_default_transform(profile["crs"], dst_crs, profile["width"], profile["height"],
                                               left, bottom, right, top)
    lst = np.full((h, w), np.nan, dtype="float32")
    reproject(lst_utm, lst, src_transform=profile["transform"], src_crs=profile["crs"],
              dst_transform=dst_tr, dst_crs=dst_crs, resampling=Resampling.bilinear,
              src_nodata=np.nan, dst_nodata=np.nan)
    lo, hi = np.nanpercentile(lst, 2), np.nanpercentile(lst, 98)
    t = np.clip((lst - lo) / (hi - lo), 0, 1)
    stops = np.array([[255, 245, 200], [254, 196, 79], [236, 112, 20], [204, 46, 38], [128, 0, 38]], float)
    idx = np.nan_to_num(t) * (len(stops) - 1)
    i0 = np.floor(idx).astype(int).clip(0, len(stops) - 2)
    f = (idx - i0)[..., None]
    rgb = stops[i0] * (1 - f) + stops[i0 + 1] * f
    alpha = np.where(np.isfinite(lst), 210, 0)
    img = np.dstack([rgb.astype("uint8"), alpha.astype("uint8")])
    Image.fromarray(img, "RGBA").save(OUT / "lst.png", optimize=True)
    to_ll = Transformer.from_crs(dst_crs, 4326, always_xy=True)
    west, north = to_ll.transform(*(dst_tr * (0, 0)))
    east, south = to_ll.transform(*(dst_tr * (w, h)))
    (OUT / "lst_bounds.json").write_text(json.dumps({
        "bounds": [[south, west], [north, east]], "min_c": round(float(lo), 1), "max_c": round(float(hi), 1)}))
    print(f"  wrote web/data/lst.png ({w}x{h}, range {lo:.1f}-{hi:.1f} C)")


def main():
    ces = load_cache("ces4.json")
    tracts = [(str(f["properties"]["tract"]), shape(f["geometry"])) for f in ces["features"]]

    lst, profile = landsat_lst()
    result = {tid: {"lst_c": v} for tid, v in zonal_mean(lst, profile, tracts).items()}
    write_overlay(lst, profile)

    for key, cov in NLCD.items():
        path = CACHE / f"{cov}.tif"
        if not path.exists():
            print(f"  downloading {cov}")
            path.write_bytes(requests.get(WCS.format(cov=cov), timeout=300).content)
        with rasterio.open(path) as src:
            arr = src.read(1).astype("float32")
            prof = {"crs": src.crs, "transform": src.transform}
        arr[arr > 100] = np.nan  # codes above 100 are nodata/background
        for tid, v in zonal_mean(arr, prof, tracts).items():
            result[tid][key] = v

    (CACHE / "tract_heat.json").write_text(json.dumps(result))
    vals = [r["lst_c"] for r in result.values() if r["lst_c"] is not None]
    print(f"  tract LST: {min(vals):.1f} - {max(vals):.1f} C across {len(vals)} tracts")


if __name__ == "__main__":
    main()
