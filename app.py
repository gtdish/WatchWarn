from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import requests
import json
import re
from pathlib import Path
from datetime import datetime, timezone

app = FastAPI(title="WatchWarn")

NWS_ACTIVE_ALERTS_API = "https://api.weather.gov/alerts/active?status=actual&message_type=alert,update"

COUNTY_FILE = Path("static/us_counties.geojson")
ZONE_FILE = Path("static/zone_pop.json")

NWS_HEADERS = {
    "User-Agent": "WatchWarn/1.0 contact: jdisharoon",
    "Accept": "application/geo+json"
}

LOCAL_COUNTIES = {
    "GA": {
        "banks", "barrow", "bartow", "butts", "carroll", "chattooga",
        "cherokee", "clarke", "clayton", "cobb", "coweta", "dawson",
        "dekalb", "douglas", "fannin", "fayette", "floyd", "forsyth",
        "fulton", "gilmer", "gordon", "greene", "gwinnett", "habersham", "hall",
        "haralson", "heard", "henry", "jackson", "jasper", "lamar", "lumpkin",
        "madison", "meriwether", "morgan", "newton", "oconee",
        "oglethorpe", "paulding", "pickens", "pike", "polk", "putnam",
        "rabun", "rockdale", "spalding", "towns", "troup", "union",
        "upson", "walton", "white"
    },
    "AL": {
        "randolph",
        "cleburne"
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
    "Heat Advisory": ("HT", "Y"),
    "Special Weather Statement": ("SP", "S")
}

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def home():
    return FileResponse("static/index.html")


@app.get("/panel")
def panel():
    return FileResponse("static/panel.html")


def prop(props, *names):
    lower_lookup = {str(k).lower(): v for k, v in (props or {}).items()}

    for name in names:
        key = str(name).lower()
        if key in lower_lookup:
            return lower_lookup[key]

    return None


def normalize_county_name(value):
    return (
        str(value or "")
        .lower()
        .replace(" county", "")
        .strip()
    )


def display_county_name(value):
    text = str(value or "").strip()

    if text.lower().endswith(" county"):
        text = text[:-7].strip()

    return text


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


def find_state_abbr(props):
    geoid = prop(
        props,
        "GEOID", "GEOID20", "GEOID10",
        "GEO_ID", "GEO_ID20", "GEO_ID10",
        "geoid", "geoid20", "geoid10",
        "geo_id", "geo_id20", "geo_id10"
    )

    if geoid:
        text = re.sub(r"\D", "", str(geoid))
        if len(text) >= 5:
            state = normalize_state(text[-5:-3])
            if state:
                return state

    state_value = prop(
        props,
        "STUSPS", "STATE", "STATE_ABBR", "STATECODE", "STATE_CODE",
        "STATE_NAME", "STATENAME", "NAME_1", "STATEFP", "STATE_FIPS",
        "STATEFP10", "STATEFP20", "statefp", "statefp10", "statefp20"
    )

    return normalize_state(state_value)


def make_county_label(name, state_abbr):
    clean_name = display_county_name(name)

    if state_abbr and state_abbr != "GA":
        return f"{clean_name}, {state_abbr}"

    return clean_name


def build_county_ugc(props, state_abbr):
    geoid = prop(
        props,
        "GEOID", "GEOID20", "GEOID10",
        "GEO_ID", "GEO_ID20", "GEO_ID10",
        "geoid", "geoid20", "geoid10",
        "geo_id", "geo_id20", "geo_id10"
    )

    if geoid:
        text = re.sub(r"\D", "", str(geoid))
        if len(text) >= 5:
            county_fips = text[-3:]
            return f"{state_abbr}C{county_fips}"

    countyfp = prop(
        props,
        "COUNTY", "COUNTYFP", "COUNTYFP20", "COUNTYFP10",
        "county", "countyfp", "countyfp20", "countyfp10",
        "COUNTY_FIPS", "FIPS"
    )

    if countyfp:
        county_fips = re.sub(r"\D", "", str(countyfp)).zfill(3)[-3:]
        return f"{state_abbr}C{county_fips}"

    return ""


def load_local_ugc_lookup():
    lookup = {}

    if COUNTY_FILE.exists():
        with open(COUNTY_FILE, "r") as f:
            county_data = json.load(f)

        for feature in county_data.get("features", []):
            props = feature.get("properties", {}) or {}

            county_name = prop(
                props,
                "NAME", "NAME20", "NAME10", "NAMELSAD", "NAMELSAD20",
                "name", "name20", "name10", "namelsad", "namelsad20"
            )

            county_key = normalize_county_name(county_name)
            state_abbr = find_state_abbr(props)

            if not county_key or not state_abbr:
                continue

            if county_key not in LOCAL_COUNTIES.get(state_abbr, set()):
                continue

            ugc = build_county_ugc(props, state_abbr)

            if ugc:
                lookup[ugc] = make_county_label(county_name, state_abbr)

    if ZONE_FILE.exists():
        with open(ZONE_FILE, "r") as f:
            zone_data = json.load(f)

        for feature in zone_data.get("features", []):
            props = feature.get("properties", {}) or {}

            zone_code = prop(
                props,
                "STATE_ZONE", "UGC", "ZONE", "ID",
                "state_zone", "ugc", "zone", "id"
            )

            zone_code = str(zone_code or "").strip().upper()

            if not zone_code:
                continue

            state_abbr = zone_code[:2]

            zone_name = prop(
                props,
                "SHORTNAME", "NAME", "NAME_ZONE",
                "shortname", "name", "name_zone"
            )

            zone_key = normalize_county_name(zone_name)

            if not state_abbr or not zone_key:
                continue

            if zone_key not in LOCAL_COUNTIES.get(state_abbr, set()):
                continue

            lookup[zone_code] = make_county_label(zone_name, state_abbr)

    return lookup


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
        return EVENT_CODE_LOOKUP[event_text]

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
    if "SPECIAL WEATHER STATEMENT" in upper:
        return "SP", "S"

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

        direction = degrees_to_cardinal((degrees + 180) % 360)
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


def extract_alert_ugcs(properties):
    geocode = properties.get("geocode") or {}
    ugcs = geocode.get("UGC") or []

    if isinstance(ugcs, str):
        ugcs = [ugcs]

    return [str(ugc).strip().upper() for ugc in ugcs if str(ugc).strip()]


def get_matching_local_counties(properties, local_ugc_lookup):
    ugcs = extract_alert_ugcs(properties)
    labels = []

    for ugc in ugcs:
        label = local_ugc_lookup.get(ugc)
        if label and label not in labels:
            labels.append(label)

    return sorted(labels)


def convert_nws_feature_to_watchwarn_feature(feature, local_ugc_lookup):
    properties = feature.get("properties", {}) or {}
    geometry = feature.get("geometry")

    county_names = get_matching_local_counties(properties, local_ugc_lookup)

    if not county_names:
        return None

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

    watchwarn_props = {
        "ID": properties.get("id") or feature.get("id") or "",
        "TYPE": phenom,
        "PHENOM": phenom,
        "SIG": sig,
        "GTYPE": "C",
        "WFO": wfo,
        "ETN": etn,
        "ISSUED": issued,
        "UPDATED": updated,
        "INIT_ISS": issued,
        "INIT_EXP": expires,
        "BEGIN": begins,
        "EXPIRED": expires,
        "NWS_UGC": ", ".join(extract_alert_ugcs(properties)),
        "EVENT": event,
        "HEADLINE": properties.get("headline") or "",
        "AREA_DESC": properties.get("areaDesc") or "",
        "DESCRIPTION": properties.get("description") or "",
        "INSTRUCTION": properties.get("instruction") or "",
        "COUNTY_NAMES": county_names,
        "LOCAL_COUNTY_NAMES": county_names,
        **metadata
    }

    return {
        "type": "Feature",
        "properties": watchwarn_props,
        "geometry": geometry
    }


def fetch_nws_alerts():
    response = requests.get(
        NWS_ACTIVE_ALERTS_API,
        headers=NWS_HEADERS,
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def build_local_alerts_geojson():
    local_ugc_lookup = load_local_ugc_lookup()
    nws_data = fetch_nws_alerts()

    features = []

    for feature in nws_data.get("features", []):
        converted = convert_nws_feature_to_watchwarn_feature(
            feature,
            local_ugc_lookup
        )

        if converted:
            features.append(converted)

    return {
        "type": "FeatureCollection",
        "features": features
    }


@app.get("/api/alerts")
def get_alerts():
    try:
        return JSONResponse(content=build_local_alerts_geojson())

    except Exception as error:
        return JSONResponse(
            status_code=500,
            content={"error": str(error)}
        )


@app.get("/api/debug/local-ugc")
def debug_local_ugc():
    try:
        lookup = load_local_ugc_lookup()

        rows = [
            {
                "ugc": ugc,
                "label": label
            }
            for ugc, label in sorted(lookup.items())
        ]

        return JSONResponse(content=rows)

    except Exception as error:
        return JSONResponse(
            status_code=500,
            content={"error": str(error)}
        )


@app.get("/api/debug/alerts-summary")
def debug_alerts_summary():
    try:
        data = build_local_alerts_geojson()

        rows = []

        for feature in data.get("features", []):
            props = feature.get("properties", {}) or {}

            rows.append({
                "event": props.get("EVENT"),
                "phenom": props.get("PHENOM"),
                "sig": props.get("SIG"),
                "wfo": props.get("WFO"),
                "etn": props.get("ETN"),
                "issued": props.get("ISSUED"),
                "begins": props.get("BEGIN"),
                "expires": props.get("EXPIRED"),
                "ugc": props.get("NWS_UGC"),
                "counties": props.get("COUNTY_NAMES"),
                "headline": props.get("NWS_HEADLINE") or props.get("HEADLINE")
            })

        return JSONResponse(content=rows)

    except Exception as error:
        return JSONResponse(
            status_code=500,
            content={"error": str(error)}
        )