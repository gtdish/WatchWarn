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
NWS_ACTIVE_ALERTS_API = "https://api.weather.gov/alerts/active?status=actual&message_type=alert,update"
COUNTY_FILE = Path("static/us_counties.geojson")

NWS_HEADERS = {
    "User-Agent": "WatchWarn/1.0 contact: jdisharoon"
}

LOCAL_COUNTIES = {
    "GA": {
        "banks", "barrow", "bartow", "butts", "carroll", "catoosa", "chattooga",
        "cherokee", "clarke", "clayton", "cobb", "coweta", "dade", "dawson",
        "dekalb", "douglas", "fannin", "fayette", "floyd", "forsyth", "franklin",
        "fulton", "gilmer", "gordon", "greene", "gwinnett", "habersham", "hall",
        "haralson", "harris", "heard", "henry", "jackson", "lamar", "lumpkin",
        "madison", "meriwether", "morgan", "murray", "newton", "oconee",
        "oglethorpe", "paulding", "pickens", "pike", "polk", "putnam",
        "rabun", "rockdale", "spalding", "talbot", "towns", "troup", "union",
        "upson", "walker", "walton", "white", "whitfield"
    },
    "AL": {
        "randolph",
        "cleburne",
        "clebourne"
    },
    "NC": {
        "clay"
    }
}

STATEFP_TO_ABBR = {
    "01": "AL",
    "13": "GA",
    "37": "NC"
}

STATE_NAME_TO_ABBR = {
    "alabama": "AL",
    "georgia": "GA",
    "north carolina": "NC"
}

NWS_CACHE = {
    "timestamp": 0,
    "active_keys": set(),
    "metadata": {}
}

NWS_CACHE_SECONDS = 10

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def home():
    return FileResponse("static/index.html")


@app.get("/panel")
def panel():
    return FileResponse("static/panel.html")


def normalize_county_name(value):
    return (
        str(value or "")
        .lower()
        .replace(" county", "")
        .strip()
    )


def normalize_state(value):
    if value is None:
        return ""

    text = str(value).strip()
    if not text:
        return ""

    upper = text.upper()
    lower = text.lower()

    if upper in {"AL", "GA", "NC"}:
        return upper

    if lower in STATE_NAME_TO_ABBR:
        return STATE_NAME_TO_ABBR[lower]

    digits = re.sub(r"\D", "", text)
    if digits:
        digits = digits.zfill(2)
        return STATEFP_TO_ABBR.get(digits, "")

    return ""


def find_state_abbr(row):
    possible_state_fields = [
        "STUSPS", "STATE", "STATE_ABBR", "STATECODE", "STATE_CODE",
        "STATE_NAME", "STATENAME", "NAME_1", "STATEFP", "STATE_FIPS",
        "STATEFP10", "STATEFP20", "GEOID", "GEOID10", "GEOID20"
    ]

    for field in possible_state_fields:
        if field not in row:
            continue

        value = row.get(field)

        if field in {"GEOID", "GEOID10", "GEOID20"}:
            text = str(value or "").strip()
            if len(text) >= 2:
                state = normalize_state(text[:2])
                if state:
                    return state

        state = normalize_state(value)
        if state:
            return state

    return ""


def county_is_local(row):
    county_name = normalize_county_name(row.get("NAME"))
    state_abbr = find_state_abbr(row)

    if not county_name or not state_abbr:
        return False

    return county_name in LOCAL_COUNTIES.get(state_abbr, set())


def load_counties():
    counties = gpd.read_file(COUNTY_FILE)

    if counties.crs is None:
        counties = counties.set_crs(epsg=4326)
    else:
        counties = counties.to_crs(epsg=4326)

    counties["NAME"] = counties["NAME"].astype(str).str.strip()
    counties["NAME_KEY"] = counties["NAME"].apply(normalize_county_name)
    counties["STATE_ABBR"] = counties.apply(find_state_abbr, axis=1)
    counties["IS_LOCAL"] = counties.apply(county_is_local, axis=1)

    return counties


def filter_local_alerts(alerts, counties):
    local_counties = counties.loc[counties["IS_LOCAL"]].copy()

    if alerts.empty or local_counties.empty:
        return alerts.iloc[0:0].copy()

    joined = gpd.sjoin(
        alerts,
        local_counties,
        how="inner",
        predicate="intersects"
    )

    if joined.empty:
        return alerts.iloc[0:0].copy()

    local_alert_indexes = sorted(set(joined.index))
    return alerts.loc[local_alert_indexes].copy()


def enrich_alert_counties(alerts, counties):
    alerts["COUNTY_NAMES"] = None
    alerts["LOCAL_COUNTY_NAMES"] = None

    if alerts.empty or counties.empty:
        return alerts

    local_counties = counties.loc[counties["IS_LOCAL"]].copy()

    if local_counties.empty:
        return alerts

    joined = gpd.sjoin(
        local_counties,
        alerts,
        how="inner",
        predicate="intersects"
    )

    county_lookup = {}

    for _, row in joined.iterrows():
        alert_index = row["index_right"]
        county_name = row.get("NAME")
        state_abbr = row.get("STATE_ABBR")

        if county_name:
            label = str(county_name).strip()
            if state_abbr and state_abbr != "GA":
                label = f"{label}, {state_abbr}"

            county_lookup.setdefault(alert_index, set()).add(label)

    for alert_index, county_names in county_lookup.items():
        sorted_counties = sorted(county_names)
        alerts.at[alert_index, "COUNTY_NAMES"] = sorted_counties
        alerts.at[alert_index, "LOCAL_COUNTY_NAMES"] = sorted_counties

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

    if "TORNADO WARNING" in full_text:
        if "TORNADO EMERGENCY" in full_text:
            primary_tag = "EMERGENCY"
        elif "CONSIDERABLE" in tornado_damage:
            primary_tag = "CONSIDERABLE"
        elif "OBSERVED" in tornado_detection:
            primary_tag = "OBSERVED"
        elif "RADAR" in tornado_detection:
            primary_tag = "RADAR INDICATED"

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


def fetch_nws_active_data():
    now = time.time()

    if now - NWS_CACHE["timestamp"] < NWS_CACHE_SECONDS:
        return NWS_CACHE["active_keys"], NWS_CACHE["metadata"]

    response = requests.get(
        NWS_ACTIVE_ALERTS_API,
        headers=NWS_HEADERS,
        timeout=30
    )
    response.raise_for_status()

    data = response.json()

    active_keys = set()
    metadata_lookup = {}

    for feature in data.get("features", []):
        properties = feature.get("properties", {})
        parameters = properties.get("parameters") or {}

        vtec_values = parameters.get("VTEC") or []
        keys = extract_vtec_keys(vtec_values)

        if not keys:
            continue

        metadata = build_alert_metadata(properties)

        for key in keys:
            active_keys.add(key)
            metadata_lookup[key] = metadata

    NWS_CACHE["timestamp"] = now
    NWS_CACHE["active_keys"] = active_keys
    NWS_CACHE["metadata"] = metadata_lookup

    return active_keys, metadata_lookup


def filter_to_nws_active_alerts(alerts):
    if alerts.empty:
        return alerts

    active_keys, _ = fetch_nws_active_data()

    if not active_keys:
        return alerts.iloc[0:0].copy()

    keep_indexes = []

    for idx, row in alerts.iterrows():
        key = make_vtec_key(
            row.get("WFO"),
            row.get("PHENOM"),
            row.get("SIG"),
            row.get("ETN")
        )

        if key and key in active_keys:
            keep_indexes.append(idx)

    return alerts.loc[keep_indexes].copy()


def enrich_alert_tags(alerts):
    alerts["PRIMARY_TAG"] = None
    alerts["STACKED_TAGS"] = None
    alerts["MAX_WIND"] = None
    alerts["MAX_HAIL"] = None
    alerts["STORM_DIRECTION"] = None
    alerts["STORM_SPEED"] = None
    alerts["NWS_HEADLINE"] = None

    _, tag_lookup = fetch_nws_active_data()

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

            alerts = filter_to_nws_active_alerts(alerts)

            remove_warning_counties = (
                (alerts["SIG"] == "W") &
                (alerts["GTYPE"] == "C")
            )

            alerts = alerts.loc[~remove_warning_counties].copy()

            if COUNTY_FILE.exists():
                counties = load_counties()
                alerts = filter_local_alerts(alerts, counties)
                alerts = enrich_alert_counties(alerts, counties)

            alerts = enrich_alert_tags(alerts)

            geojson = json.loads(alerts.to_json(na="null"))
            return JSONResponse(content=geojson)

    except Exception as error:
        return JSONResponse(
            status_code=500,
            content={"error": str(error)}
        )


@app.get("/api/debug/local-counties")
def debug_local_counties():
    try:
        counties = load_counties()
        local = counties.loc[counties["IS_LOCAL"], ["NAME", "STATE_ABBR"]].copy()
        local = local.sort_values(["STATE_ABBR", "NAME"])
        return JSONResponse(content=local.to_dict(orient="records"))
    except Exception as error:
        return JSONResponse(
            status_code=500,
            content={"error": str(error)}
        )