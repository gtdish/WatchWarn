from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import geopandas as gpd
import pandas as pd
import requests
import json
import re
from pathlib import Path
from datetime import datetime, timezone

app = FastAPI(title="WatchWarn")

NWS_ACTIVE_ALERTS_API = "https://api.weather.gov/alerts/active?status=actual&message_type=alert,update"
COUNTY_FILE = Path("static/us_counties.geojson")

NWS_HEADERS = {
    "User-Agent": "WatchWarn/1.0 contact: jdisharoon",
    "Accept": "application/geo+json"
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

EVENT_CODE_LOOKUP = {
    "Tornado Warning": ("TO", "W"),
    "Tornado Watch": ("TO", "A"),
    "Severe Thunderstorm Warning": ("SV", "W"),
    "Severe Thunderstorm Watch": ("SV", "A"),
    "Flash Flood Warning": ("FF", "W"),
    "Flash Flood Watch": ("FF", "A"),
    "Flood Watch": ("FA", "A"),
    "Extreme Heat Warning": ("XH", "W"),
    "Extreme Heat Watch": ("XH", "A"),
    "Heat Advisory": ("HT", "Y")
}

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


def iso_to_iem(value):
    if not value:
        return ""

    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y%m%d%H%M")
    except Exception:
        return ""


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
            keys.append({
                "WFO": wfo.upper(),
                "PHENOM": phenom.upper(),
                "SIG": sig.upper(),
                "ETN": str(int(etn))
            })

    return keys


def event_to_code(event):
    event_text = str(event or "").strip()

    if event_text in EVENT_CODE_LOOKUP:
        phenom, sig = EVENT_CODE_LOOKUP[event_text]
        return phenom, sig

    upper = event_text.upper()

    if "TORNADO WARNING" in upper:
        return "TO", "W"
    if "TORNADO WATCH" in upper:
        return "TO", "A"
    if "SEVERE THUNDERSTORM WARNING" in upper:
        return "SV", "W"
    if "SEVERE THUNDERSTORM WATCH" in upper:
        return "SV", "A"
    if "FLASH FLOOD WARNING" in upper:
        return "FF", "W"
    if "FLASH FLOOD WATCH" in upper:
        return "FF", "A"
    if "FLOOD WATCH" in upper:
        return "FA", "A"
    if "EXTREME HEAT WARNING" in upper:
        return "XH", "W"
    if "EXTREME HEAT WATCH" in upper:
        return "XH", "A"
    if "HEAT ADVISORY" in upper:
        return "HT", "Y"

    return "", ""


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

    parts = str(motion_text).split("...")

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


def make_local_area_text(row):
    state_abbr = row.get("STATE_ABBR", "")
    county_name = row.get("NAME", "")

    if state_abbr and state_abbr != "GA":
        return f"{county_name}, {state_abbr}"

    return county_name


def area_desc_mentions_local_county(area_desc, county_name, state_abbr):
    area = str(area_desc or "").lower()
    county = normalize_county_name(county_name)

    if not area or not county:
        return False

    candidates = [
        f"{county}, {state_abbr.lower()}",
        f"{county} county",
        f"{county};",
        f"{county} "
    ]

    return any(candidate in area for candidate in candidates) or area == county


def build_synthetic_geometry_from_area_desc(properties, local_counties):
    area_desc = properties.get("areaDesc", "")
    matches = []

    for idx, county in local_counties.iterrows():
        if area_desc_mentions_local_county(
            area_desc,
            county.get("NAME"),
            county.get("STATE_ABBR")
        ):
            matches.append(idx)

    if not matches:
        return None, []

    matched = local_counties.loc[matches].copy()
    geometry = matched.geometry.union_all()
    county_names = sorted(make_local_area_text(row) for _, row in matched.iterrows())

    return geometry, county_names


def convert_nws_feature_to_record(feature, local_counties):
    properties = feature.get("properties", {}) or {}
    geometry = feature.get("geometry")

    event = properties.get("event", "")
    parameters = properties.get("parameters") or {}
    vtec_keys = extract_vtec_keys(parameters.get("VTEC") or [])

    if vtec_keys:
        key = vtec_keys[0]
        phenom = key["PHENOM"]
        sig = key["SIG"]
        etn = key["ETN"]
        wfo = key["WFO"]
    else:
        phenom, sig = event_to_code(event)
        etn = ""
        wfo = ""

    if not phenom or not sig:
        return None

    metadata = build_alert_metadata(properties)

    issued = iso_to_iem(properties.get("sent"))
    updated = iso_to_iem(properties.get("sent"))
    begins = iso_to_iem(
        properties.get("effective") or
        properties.get("onset") or
        properties.get("sent")
    )
    expires = iso_to_iem(
        properties.get("expires") or
        properties.get("ends")
    )

    record = {
        "ID": properties.get("id") or feature.get("id") or "",
        "TYPE": phenom,
        "PHENOM": phenom,
        "SIG": sig,
        "GTYPE": "P" if sig == "W" else "C",
        "WFO": wfo,
        "ETN": etn,
        "ISSUED": issued,
        "UPDATED": updated,
        "INIT_ISS": issued,
        "INIT_EXP": expires,
        "BEGIN": begins,
        "EXPIRED": expires,
        "NWS_UGC": "",
        "EVENT": event,
        "HEADLINE": properties.get("headline") or "",
        "AREA_DESC": properties.get("areaDesc") or "",
        "COUNTY_NAMES": [],
        "LOCAL_COUNTY_NAMES": [],
        **metadata
    }

    if geometry:
        return {
            "record": record,
            "geometry": geometry
        }

    synthetic_geometry, county_names = build_synthetic_geometry_from_area_desc(
        properties,
        local_counties
    )

    if synthetic_geometry is None:
        return None

    record["COUNTY_NAMES"] = county_names
    record["LOCAL_COUNTY_NAMES"] = county_names

    return {
        "record": record,
        "geometry": synthetic_geometry.__geo_interface__
    }


def fetch_nws_features():
    response = requests.get(
        NWS_ACTIVE_ALERTS_API,
        headers=NWS_HEADERS,
        timeout=30
    )
    response.raise_for_status()
    return response.json().get("features", [])


def build_nws_geodataframe(local_counties):
    nws_features = fetch_nws_features()

    records = []
    geometries = []

    for feature in nws_features:
        converted = convert_nws_feature_to_record(feature, local_counties)

        if not converted:
            continue

        records.append(converted["record"])
        geometries.append(converted["geometry"])

    if not records:
        return gpd.GeoDataFrame(records, geometry=[], crs="EPSG:4326")

    gdf = gpd.GeoDataFrame.from_features(
        [
            {
                "type": "Feature",
                "properties": record,
                "geometry": geometry
            }
            for record, geometry in zip(records, geometries)
        ],
        crs="EPSG:4326"
    )

    return gdf


def filter_local_alerts(alerts, local_counties):
    if alerts.empty or local_counties.empty:
        return alerts.iloc[0:0].copy()

    alerts = alerts.copy()
    alerts["_ALERT_INDEX"] = alerts.index

    joined = gpd.sjoin(
        alerts,
        local_counties,
        how="inner",
        predicate="intersects"
    )

    if joined.empty:
        return alerts.iloc[0:0].copy()

    keep_indexes = sorted(set(joined["_ALERT_INDEX"]))
    local_alerts = alerts.loc[keep_indexes].copy()

    if "_ALERT_INDEX" in local_alerts.columns:
        local_alerts = local_alerts.drop(columns=["_ALERT_INDEX"])

    return local_alerts


def enrich_alert_counties(alerts, local_counties):
    alerts["COUNTY_NAMES"] = [[] for _ in range(len(alerts))]
    alerts["LOCAL_COUNTY_NAMES"] = [[] for _ in range(len(alerts))]

    if alerts.empty or local_counties.empty:
        return alerts

    alerts = alerts.copy()
    alerts["_ALERT_INDEX"] = alerts.index

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

    if "_ALERT_INDEX" in alerts.columns:
        alerts = alerts.drop(columns=["_ALERT_INDEX"])

    return alerts


@app.get("/api/alerts")
def get_alerts():
    try:
        counties = load_counties()
        local_counties = counties.loc[counties["IS_LOCAL"]].copy()

        alerts = build_nws_geodataframe(local_counties)

        if alerts.empty:
            return {"type": "FeatureCollection", "features": []}

        if alerts.crs is None:
            alerts = alerts.set_crs(epsg=4326)
        else:
            alerts = alerts.to_crs(epsg=4326)

        alerts = filter_local_alerts(alerts, local_counties)
        alerts = enrich_alert_counties(alerts, local_counties)

        if alerts.empty:
            return {"type": "FeatureCollection", "features": []}

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


@app.get("/api/debug/alerts-summary")
def debug_alerts_summary():
    try:
        counties = load_counties()
        local_counties = counties.loc[counties["IS_LOCAL"]].copy()

        alerts = build_nws_geodataframe(local_counties)

        if alerts.empty:
            return JSONResponse(content=[])

        alerts = filter_local_alerts(alerts, local_counties)
        alerts = enrich_alert_counties(alerts, local_counties)

        rows = []

        for _, row in alerts.iterrows():
            rows.append({
                "event": row.get("EVENT"),
                "phenom": row.get("PHENOM"),
                "sig": row.get("SIG"),
                "wfo": row.get("WFO"),
                "etn": row.get("ETN"),
                "issued": row.get("ISSUED"),
                "begins": row.get("BEGIN"),
                "expires": row.get("EXPIRED"),
                "counties": row.get("COUNTY_NAMES"),
                "headline": row.get("NWS_HEADLINE") or row.get("HEADLINE")
            })

        return JSONResponse(content=rows)

    except Exception as error:
        return JSONResponse(
            status_code=500,
            content={"error": str(error)}
        )