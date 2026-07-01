from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import geopandas as gpd
import requests
import tempfile
import zipfile
import json
import re
import time
from pathlib import Path

app = FastAPI(title="WatchWarn")

IEM_CURRENT_WW_ZIP = "https://mesonet.agron.iastate.edu/data/gis/shape/4326/us/current_ww.zip"
NWS_ACTIVE_ALERTS_API = "https://api.weather.gov/alerts/active"
COUNTY_FILE = Path("static/us_counties.geojson")

NWS_HEADERS = {
    "User-Agent": "WatchWarn/1.0 contact: jdisharoon"
}

NWS_TAG_CACHE = {
    "timestamp": 0,
    "data": {}
}

NWS_TAG_CACHE_SECONDS = 10

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def home():
    return FileResponse("static/index.html")


@app.get("/panel")
def panel():
    return FileResponse("static/panel.html")


def load_counties():
    counties = gpd.read_file(COUNTY_FILE)

    if counties.crs is None:
        counties = counties.set_crs(epsg=4326)
    else:
        counties = counties.to_crs(epsg=4326)

    counties["NAME"] = counties["NAME"].astype(str).str.strip()
    return counties


def enrich_alert_counties(alerts, counties):
    alerts["COUNTY_NAMES"] = None

    if alerts.empty or counties.empty:
        return alerts

    joined = gpd.sjoin(
        counties,
        alerts,
        how="inner",
        predicate="intersects"
    )

    county_lookup = {}

    for _, row in joined.iterrows():
        alert_index = row["index_right"]
        county_name = row.get("NAME")

        if county_name:
            county_lookup.setdefault(alert_index, set()).add(str(county_name))

    for alert_index, county_names in county_lookup.items():
        alerts.at[alert_index, "COUNTY_NAMES"] = sorted(county_names)

    return alerts


def as_text(value):
    if value is None:
        return ""

    if isinstance(value, list):
        return " ".join(str(v) for v in value if v is not None)

    return str(value)


def get_parameter(parameters, key):
    if not parameters:
        return ""

    key_lower = key.lower()

    for param_key, param_value in parameters.items():
        if str(param_key).lower() == key_lower:
            return as_text(param_value)

    return ""


def make_vtec_key(wfo, phenom, sig, etn):
    if not wfo or not phenom or not sig or etn is None:
        return None

    try:
        etn_int = int(etn)
    except Exception:
        return None

    return (
        str(wfo).strip().upper(),
        str(phenom).strip().upper(),
        str(sig).strip().upper(),
        str(etn_int)
    )


def extract_vtec_keys(vtec_values):
    keys = []

    if not vtec_values:
        return keys

    if isinstance(vtec_values, str):
        vtec_values = [vtec_values]

    for vtec in vtec_values:
        matches = re.findall(
            r"\.K([A-Z]{3})\.([A-Z]{2})\.([A-Z])\.(\d{4})\.",
            str(vtec)
        )

        for wfo, phenom, sig, etn in matches:
            keys.append((
                wfo.upper(),
                phenom.upper(),
                sig.upper(),
                str(int(etn))
            ))

    return keys


def degrees_to_cardinal(degrees):
    dirs = [
        "N", "NNE", "NE", "ENE",
        "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW",
        "W", "WNW", "NW", "NNW"
    ]

    idx = round(degrees / 22.5) % 16
    return dirs[idx]


def parse_event_motion(motion_text):
    if not motion_text:
        return "", ""

    parts = motion_text.split("...")

    if len(parts) < 4:
        return "", ""

    try:
        deg_part = parts[2].replace("DEG", "").strip()
        kt_part = parts[3].replace("KT", "").strip()

        degrees = int(deg_part)
        knots = int(kt_part)

        direction = degrees_to_cardinal(degrees)
        mph = round((knots * 1.15078) / 5) * 5

        return direction, str(mph)

    except Exception:
        return "", ""


def build_alert_metadata(properties):
    parameters = properties.get("parameters") or {}
    headline = as_text(properties.get("headline"))
    description = as_text(properties.get("description"))

    tornado_damage = get_parameter(parameters, "tornadoDamageThreat").upper()
    tornado_detection = get_parameter(parameters, "tornadoDetection").upper()

    thunderstorm_damage = get_parameter(parameters, "thunderstormDamageThreat").upper()
    tornado_possible = get_parameter(parameters, "tornadoDetection").upper()

    max_wind = get_parameter(parameters, "maxWindGust").upper()
    max_hail = get_parameter(parameters, "maxHailSize").upper()

    motion_text = get_parameter(parameters, "eventMotionDescription")
    storm_direction, storm_speed = parse_event_motion(motion_text)

    primary_tag = ""
    stacked_tags = []

    full_text = f"{headline} {description}".upper()

    # Tornado Warning priority
    if "TORNADO WARNING" in full_text:
        if "TORNADO EMERGENCY" in full_text:
            primary_tag = "EMERGENCY"
        elif "CONSIDERABLE" in tornado_damage:
            primary_tag = "CONSIDERABLE"
        elif "OBSERVED" in tornado_detection:
            primary_tag = "OBSERVED"
        elif "RADAR" in tornado_detection:
            primary_tag = "RADAR INDICATED"

    # Severe Thunderstorm Warning stacked tags
    elif "SEVERE THUNDERSTORM WARNING" in full_text:
        if tornado_possible == "POSSIBLE":
            stacked_tags.append("TOR POSSIBLE")

        if "DESTRUCTIVE" in thunderstorm_damage:
            stacked_tags.append("DESTRUCTIVE")
        elif "CONSIDERABLE" in thunderstorm_damage:
            stacked_tags.append("CONSIDERABLE")

    return {
        "PRIMARY_TAG": primary_tag,
        "STACKED_TAGS": stacked_tags,
        "MAX_WIND": max_wind,
        "MAX_HAIL": max_hail,
        "STORM_DIRECTION": storm_direction,
        "STORM_SPEED": storm_speed,
        "NWS_HEADLINE": headline
    }


def fetch_nws_tag_lookup():
    now = time.time()

    if now - NWS_TAG_CACHE["timestamp"] < NWS_TAG_CACHE_SECONDS:
        return NWS_TAG_CACHE["data"]

    response = requests.get(
        NWS_ACTIVE_ALERTS_API,
        headers=NWS_HEADERS,
        timeout=30
    )
    response.raise_for_status()

    data = response.json()
    lookup = {}

    for feature in data.get("features", []):
        properties = feature.get("properties", {})
        parameters = properties.get("parameters") or {}

        vtec_values = parameters.get("VTEC") or []
        keys = extract_vtec_keys(vtec_values)

        if not keys:
            continue

        metadata = build_alert_metadata(properties)

        for key in keys:
            lookup[key] = metadata

    NWS_TAG_CACHE["timestamp"] = now
    NWS_TAG_CACHE["data"] = lookup

    return lookup


def enrich_alert_tags(alerts):
    alerts["PRIMARY_TAG"] = None
    alerts["STACKED_TAGS"] = None
    alerts["MAX_WIND"] = None
    alerts["MAX_HAIL"] = None
    alerts["STORM_DIRECTION"] = None
    alerts["STORM_SPEED"] = None
    alerts["NWS_HEADLINE"] = None

    tag_lookup = fetch_nws_tag_lookup()

    for idx, row in alerts.iterrows():
        key = make_vtec_key(
            row.get("WFO"),
            row.get("PHENOM"),
            row.get("SIG"),
            row.get("ETN")
        )

        if not key:
            continue

        metadata = tag_lookup.get(key)

        if not metadata:
            continue

        for field in metadata:
            alerts.at[idx, field] = metadata[field]

    return alerts


@app.get("/api/alerts")
def get_alerts():
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            zip_path = tmpdir / "current_ww.zip"

            response = requests.get(IEM_CURRENT_WW_ZIP, timeout=30)
            response.raise_for_status()
            zip_path.write_bytes(response.content)

            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(tmpdir)

            shp_files = list(tmpdir.glob("*.shp"))

            if not shp_files:
                return JSONResponse(
                    status_code=500,
                    content={"error": "No shapefile found"}
                )

            alerts = gpd.read_file(shp_files[0])

            if alerts.empty:
                return {"type": "FeatureCollection", "features": []}

            if alerts.crs is None:
                alerts = alerts.set_crs(epsg=4326)
            else:
                alerts = alerts.to_crs(epsg=4326)

            alerts["SIG"] = alerts["SIG"].astype(str).str.strip()
            alerts["GTYPE"] = alerts["GTYPE"].astype(str).str.strip()

            remove_warning_counties = (
                (alerts["SIG"] == "W") &
                (alerts["GTYPE"] == "C")
            )

            alerts = alerts.loc[~remove_warning_counties].copy()

            if COUNTY_FILE.exists():
                counties = load_counties()
                alerts = enrich_alert_counties(alerts, counties)

            alerts = enrich_alert_tags(alerts)

            geojson = json.loads(alerts.to_json(na="null"))
            return JSONResponse(content=geojson)

    except Exception as error:
        return JSONResponse(
            status_code=500,
            content={"error": str(error)}
        )