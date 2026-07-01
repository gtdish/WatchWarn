const alertList = document.getElementById("alert-list");
const panelUpdated = document.getElementById("panel-updated");

const SUPPRESSED_ALERTS = new Set([
    "MA.W",
    "SC.Y",
    "RP.S",
    "BH.S",
    "BW.Y",
    "FW.A",
    "LW.Y",
    "FA.Y",
    "FL.Y",
    "FA.W",
    "FL.W"
]);

const EXPIRED_GRACE_MINUTES = 5;

let zoneLookup = {};

function getAlertCode(props) {
    return `${props.PHENOM}.${props.SIG}`;
}

function shouldSuppress(props) {
    return SUPPRESSED_ALERTS.has(getAlertCode(props));
}

function getAlertLabel(props) {
    const labels = {
        "TO.W": "TORNADO WARNING",
        "SV.W": "SEVERE THUNDERSTORM WARNING",
        "FF.W": "FLASH FLOOD WARNING",
        "FF.A": "FLOOD WATCH",
        "FA.A": "FLOOD WATCH",
        "SV.A": "SEVERE THUNDERSTORM WATCH",
        "TO.A": "TORNADO WATCH",
        "XH.W": "EXTREME HEAT WARNING",
        "XH.A": "EXTREME HEAT WATCH",
        "HT.Y": "HEAT ADVISORY"
    };

    return labels[getAlertCode(props)] || getAlertCode(props);
}

function getAlertClass(props) {
    const code = getAlertCode(props);

    if (code === "TO.W") return "alert-tor";
    if (code === "SV.W") return "alert-svr";
    if (code === "FF.W") return "alert-ffw";
    if (code === "FF.A" || code === "FA.A") return "alert-watch";
    if (code === "SV.A" || code === "TO.A") return "alert-watch";
    if (code === "XH.W" || code === "XH.A") return "alert-excessive-heat";
    if (code === "HT.Y") return "alert-heat-advisory";
    if (props.SIG === "A") return "alert-watch";
    if (props.SIG === "Y") return "alert-advisory";

    return "alert-other";
}

function isFloodWatch(props) {
    const code = getAlertCode(props);
    return code === "FF.A" || code === "FA.A";
}

function isFutureEffectiveProduct(props) {
    const code = getAlertCode(props);

    return (
        code === "FF.A" ||
        code === "FA.A" ||
        code === "HT.Y" ||
        code === "XH.W" ||
        code === "XH.A"
    );
}

function parseIemDate(value) {
    if (!value) return null;

    const s = String(value);
    if (s.length !== 12) return null;

    return new Date(Date.UTC(
        Number(s.slice(0, 4)),
        Number(s.slice(4, 6)) - 1,
        Number(s.slice(6, 8)),
        Number(s.slice(8, 10)),
        Number(s.slice(10, 12))
    ));
}

function getTimestamp(value) {
    const d = parseIemDate(value);
    return d ? d.getTime() : 0;
}

function formatTime(value) {
    const d = parseIemDate(value);

    if (!d) return "N/A";

    return d.toLocaleTimeString([], {
        hour: "numeric",
        minute: "2-digit"
    });
}

function formatDayTime(value) {
    const d = parseIemDate(value);

    if (!d) return "N/A";

    const day = d.toLocaleDateString([], {
        weekday: "short"
    }).toUpperCase();

    const time = d.toLocaleTimeString([], {
        hour: "numeric",
        minute: "2-digit"
    });

    return `${day} ${time}`;
}

function isSameLocalDate(dateA, dateB) {
    if (!dateA || !dateB) return false;

    return (
        dateA.getFullYear() === dateB.getFullYear() &&
        dateA.getMonth() === dateB.getMonth() &&
        dateA.getDate() === dateB.getDate()
    );
}

function isFutureEffective(props) {
    if (!isFutureEffectiveProduct(props)) return false;

    const beginsDate = parseIemDate(props.ISSUED);
    if (!beginsDate) return false;

    return beginsDate.getTime() > Date.now();
}

function floodWatchNeedsLongExpire(props) {
    if (!isFloodWatch(props)) return false;

    const expireDate = parseIemDate(props.EXPIRED);
    if (!expireDate) return false;

    return !isSameLocalDate(expireDate, new Date());
}

function getMinutesRemaining(expiredValue) {
    const expireDate = parseIemDate(expiredValue);
    if (!expireDate) return null;

    return Math.ceil((expireDate.getTime() - Date.now()) / 60000);
}

function formatTimeRemaining(minutesLeft) {
    if (minutesLeft === null) return "N/A";
    if (minutesLeft <= 0) return "Expired";

    if (minutesLeft >= 60) {
        const hours = Math.floor(minutesLeft / 60);
        const mins = minutesLeft % 60;
        return `${hours} h ${mins} min`;
    }

    return `${minutesLeft} min`;
}

function getMinutesSinceUpdated(updatedValue) {
    const updatedDate = parseIemDate(updatedValue);
    if (!updatedDate) return null;

    return Math.floor((Date.now() - updatedDate.getTime()) / 60000);
}

function isNewlyUpdated(props) {
    const mins = getMinutesSinceUpdated(props.UPDATED);
    return mins !== null && mins >= 0 && mins < 2;
}

function shouldKeepItem(minutesLeft) {
    if (minutesLeft === null) return true;
    return minutesLeft > -EXPIRED_GRACE_MINUTES;
}

function normalizeCode(value) {
    if (!value) return "";
    return String(value).trim();
}

function getZoneName(ugcCode) {
    const code = normalizeCode(ugcCode);
    return zoneLookup[code] || code;
}

function getCountyNamesFromProps(props) {
    if (Array.isArray(props.COUNTY_NAMES) && props.COUNTY_NAMES.length > 0) {
        return props.COUNTY_NAMES;
    }

    if (props.NWS_UGC) {
        return [getZoneName(props.NWS_UGC)];
    }

    return [];
}

function shouldMergeFeature(props) {
    return props.GTYPE === "C";
}

function getMergeKey(props) {
    return [
        props.ETN || "",
        props.PHENOM || "",
        props.SIG || ""
    ].join("-");
}

function mergeFeatures(features) {
    const mergedItems = [];
    const groups = new Map();

    for (const feature of features) {
        const props = feature.properties || {};

        if (!shouldMergeFeature(props)) {
            mergedItems.push({
                props,
                countyNames: getCountyNamesFromProps(props)
            });
            continue;
        }

        const key = getMergeKey(props);

        if (!groups.has(key)) {
            groups.set(key, {
                props: { ...props },
                countyNames: []
            });
        }

        const group = groups.get(key);

        for (const countyName of getCountyNamesFromProps(props)) {
            if (!group.countyNames.includes(countyName)) {
                group.countyNames.push(countyName);
            }
        }
    }

    for (const group of groups.values()) {
        group.countyNames.sort();
        mergedItems.push(group);
    }

    return mergedItems;
}

function buildAreaText(item) {
    const names = item.countyNames || [];

    if (!names.length) {
        return "No county match";
    }

    const noun = names.length === 1 ? "County" : "Counties";
    return `${names.length} ${noun}: ${names.join(", ")}`;
}

function buildLeftTimesHtml(props) {
    const showUpdated = props.UPDATED && props.UPDATED !== props.ISSUED;

    if (isFutureEffective(props)) {
        return `<div class="alert-left-times alert-left-times-empty"></div>`;
    }

    if (showUpdated) {
        return `
            <div class="alert-left-times">
                <div class="alert-time-block alert-primary-time">
                    <span>UPDATED</span>
                    <strong>${formatTime(props.UPDATED)}</strong>
                </div>

                <div class="alert-time-block alert-secondary-time">
                    <span>ISSUED</span>
                    <strong>${formatTime(props.ISSUED)}</strong>
                </div>
            </div>
        `;
    }

    return `
        <div class="alert-left-times">
            <div class="alert-time-block alert-primary-time">
                <span>ISSUED</span>
                <strong>${formatTime(props.ISSUED)}</strong>
            </div>
        </div>
    `;
}

function buildRightTimesHtml(props, minutesLeft) {
    const under10Class =
        minutesLeft !== null && minutesLeft > 0 && minutesLeft < 10
            ? "time-under-10"
            : "";

    if (isFutureEffective(props)) {
        return `
            <div class="alert-right-times">
                <div class="alert-time-block">
                    <span>BEGINS</span>
                    <strong>${formatDayTime(props.ISSUED)}</strong>
                </div>

                <div class="alert-time-block">
                    <span>EXPIRES</span>
                    <strong>${formatDayTime(props.EXPIRED)}</strong>
                </div>
            </div>
        `;
    }

    if (floodWatchNeedsLongExpire(props)) {
        return `
            <div class="alert-right-times">
                <div class="alert-time-block">
                    <span>EXPIRES</span>
                    <strong>${formatDayTime(props.EXPIRED)}</strong>
                </div>
            </div>
        `;
    }

    return `
        <div class="alert-right-times">
            <div class="alert-time-block">
                <span>EXPIRES</span>
                <strong>${formatTime(props.EXPIRED)}</strong>
            </div>

            <div class="alert-time-block alert-remaining ${under10Class}">
                <span>TIME LEFT</span>
                <strong>${formatTimeRemaining(minutesLeft)}</strong>
            </div>
        </div>
    `;
}

function renderAlerts(data) {
    let features = (data.features || []).filter(feature => {
        return !shouldSuppress(feature.properties || {});
    });

    let items = mergeFeatures(features);

    items = items.filter(item => {
        const minutesLeft = getMinutesRemaining(item.props.EXPIRED);
        return shouldKeepItem(minutesLeft);
    });

    items.sort((a, b) => {
        return getTimestamp(b.props.UPDATED) - getTimestamp(a.props.UPDATED);
    });

    alertList.innerHTML = "";

    if (items.length === 0) {
        alertList.innerHTML = `
            <div class="empty-state">
                <div class="empty-title">NO ACTIVE ALERTS</div>
                <div class="empty-subtitle">Waiting for new watches and warnings</div>
            </div>
        `;
        panelUpdated.textContent = `Updated ${new Date().toLocaleTimeString()}`;
        return;
    }

    for (const item of items) {
        const props = item.props;
        const minutesLeft = getMinutesRemaining(props.EXPIRED);

        const card = document.createElement("div");
        card.className = `alert-card ${getAlertClass(props)} ${isNewlyUpdated(props) ? "alert-new" : ""}`;

        card.innerHTML = `
            <div class="alert-top-row">
                <div class="alert-title">${getAlertLabel(props)}</div>
            </div>

            <div class="alert-timing-row">
                ${buildLeftTimesHtml(props)}
                ${buildRightTimesHtml(props, minutesLeft)}
            </div>

            <div class="alert-ugc alert-area-list">${buildAreaText(item)}</div>
        `;

        alertList.appendChild(card);
    }

    panelUpdated.textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

async function loadZoneLookup() {
    try {
        const response = await fetch("/static/zone_pop.json");
        const data = await response.json();

        zoneLookup = {};

        for (const feature of data.features || []) {
            const props = feature.properties || {};
            const code = normalizeCode(props.STATE_ZONE);
            const name = props.SHORTNAME || props.NAME || code;

            if (code && name) {
                zoneLookup[code] = name;
            }
        }
    } catch {
        zoneLookup = {};
    }
}

async function loadPanelAlerts() {
    try {
        const response = await fetch("/api/alerts");
        const data = await response.json();

        if (data.error) {
            throw new Error(data.error);
        }

        renderAlerts(data);
    } catch (error) {
        console.error("Error loading panel alerts:", error);
        panelUpdated.textContent = "Error loading alerts";
    }
}

async function initPanel() {
    await loadZoneLookup();
    await loadPanelAlerts();
    setInterval(loadPanelAlerts, 30000);
}

initPanel();