"""Step 1: download every vector source into pipeline/cache (idempotent)."""
import json

from common import ROOT, SJ_BBOX, arcgis_query, cached, get_json

SJ_GIS = "https://geo.sanjoseca.gov/server/rest/services/PLN"
CES4 = "https://services1.arcgis.com/PCHfdHz4GlDNAhBb/arcgis/rest/services/CalEnviroScreen_4_0_Results_/FeatureServer/0"
SB535 = "https://geodata.dot.ca.gov/arcgis/rest/services/SB1/ePPR_Project_Ref_Layer/FeatureServer/7"
TIGER_TRACTS = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Tracts_Blocks/MapServer/0"
TIGER_TRANSPORT = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Transportation/MapServer"
TIGER_WATER = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Hydro/MapServer/1"
SCHOOLS = "https://services3.arcgis.com/fdvHcZVgB2QSRNkL/arcgis/rest/services/SchoolSites2425/FeatureServer/0"
HEALTH = "https://services2.arcgis.com/wi1yEacfYjH5viqb/arcgis/rest/services/licensed_healthcare_facility_listing_december_31_2024/FeatureServer/0"
FEMA = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28"
PLACES = "https://data.cdc.gov/resource/cwsq-ngmh.json"  # CDC PLACES, census tract, latest release
GEOCODER = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
ARCGIS_GEOCODER = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"

# CDC PLACES measures that matter for diesel/PM exposure and heat
PLACES_MEASURES = ["CASTHMA", "COPD", "CHD", "STROKE", "BPHIGH", "DIABETES", "ACCESS2", "DISABILITY"]


def fetch_places():
    rows, offset = [], 0
    ids = ",".join(f"'{m}'" for m in PLACES_MEASURES)
    while True:
        batch = get_json(PLACES, params={
            "$select": "locationname,measureid,data_value,year,totalpopulation",
            "$where": f"countyfips='06085' AND measureid in ({ids})",
            "$limit": 50000, "$offset": offset,
        })
        rows.extend(batch)
        if len(batch) < 50000:
            return rows
        offset += len(batch)


def geocode_sites():
    spec = json.loads((ROOT / "pipeline" / "sites.json").read_text(encoding="utf-8"))
    out = []
    for s in spec["sites"]:
        if "lon" in s:  # manual override in sites.json
            out.append({**s, "matched": "manual"})
            continue
        query = f"{s['address']}, San Jose, CA"
        js = get_json(ARCGIS_GEOCODER, params={
            "SingleLine": query, "f": "json", "maxLocations": 1, "outFields": "Match_addr"})
        cands = [c for c in js.get("candidates", []) if c["score"] >= 90]
        if cands:
            loc, matched = cands[0]["location"], cands[0]["address"]
        else:
            js = get_json(GEOCODER, params={"address": query, "benchmark": "Public_AR_Current", "format": "json"})
            matches = js["result"]["addressMatches"]
            if not matches:
                raise RuntimeError(f"Could not geocode {s['address']} - add lon/lat to sites.json")
            loc, matched = matches[0]["coordinates"], matches[0]["matchedAddress"]
        out.append({**s, "lon": loc["x"], "lat": loc["y"], "matched": matched})
        print(f"    {s['address']:32s} -> {matched}")
    return out


def main():
    cached("districts.json", lambda: arcgis_query(
        f"{SJ_GIS}/PLN_AdministrativeBoundaries/MapServer/1",
        out_fields="DISTRICT,DISTRICTINT,COUNCILMEMBER,POPULATION,HOUSING_UNITS"))
    cached("ces4.json", lambda: arcgis_query(
        CES4, where="tract >= 6085000000 AND tract < 6086000000"))
    cached("sb535.json", lambda: arcgis_query(
        SB535, where="County = 'Santa Clara'", out_fields="Tract,DAC_category",
        extra={"returnGeometry": "false"}))
    cached("tracts2020.json", lambda: arcgis_query(
        TIGER_TRACTS, where="STATE='06' AND COUNTY='085'", out_fields="GEOID"))
    cached("places.json", fetch_places)
    cached("schools.json", lambda: arcgis_query(
        SCHOOLS, where="CountyName = 'Santa Clara'",
        out_fields="SchoolName,DistrictName,SchoolType,SchoolLevel,Street,City,EnrollTotal,GradeLow,GradeHigh"))
    cached("health.json", lambda: arcgis_query(
        HEALTH, where="COUNTY_NAME = 'SANTA CLARA'",
        out_fields="FACILITY_NAME,LICENSE_CATEGORY_DESC,FACILITY_LEVEL_DESC,DBA_ADDRESS1,DBA_CITY,TOTAL_NUMBER_BEDS,FACILITY_STATUS_DESC"))
    cached("cortese.json", lambda: arcgis_query(
        f"{SJ_GIS}/PLN_LandDesignations/MapServer/31",
        out_fields="SITEFACILITYNAME,PROGRAMTYPE,STATUS,ADDRESSDESCRIPTION"))
    cached("wetlands.json", lambda: arcgis_query(
        f"{SJ_GIS}/PLN_LandDesignations/MapServer/36", out_fields="CLASSIFICATION,WETTYPE,ACRES",
        extra={"maxAllowableOffset": 0.00005}))
    cached("flood.json", lambda: arcgis_query(
        FEMA, where="SFHA_TF = 'T'", out_fields="FLD_ZONE,ZONE_SUBTY", bbox=SJ_BBOX, page=500,
        extra={"maxAllowableOffset": 0.0001}))
    # basemap context drawn from our own data (artifact viewers block third-party tiles)
    cached("roads_primary.json", lambda: arcgis_query(
        f"{TIGER_TRANSPORT}/2", out_fields="NAME", bbox=SJ_BBOX, extra={"maxAllowableOffset": 0.0002}))
    cached("roads_secondary.json", lambda: arcgis_query(
        f"{TIGER_TRANSPORT}/5", out_fields="NAME", bbox=SJ_BBOX, extra={"maxAllowableOffset": 0.0002}))
    cached("water.json", lambda: arcgis_query(
        TIGER_WATER, where="AREAWATER > 200000", out_fields="NAME", bbox=SJ_BBOX,
        extra={"maxAllowableOffset": 0.0003}))
    cached("sites_geocoded.json", geocode_sites)
    print("fetch complete")


if __name__ == "__main__":
    main()
