const map = L.map('map');

const southeastBounds = L.latLngBounds(
    [24, -92],
    [38, -75]
);

map.fitBounds(southeastBounds);

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors'
}).addTo(map);

let alertsLayer = null;
let firstLoad = true;

function getAlertColor(phenom, sig) {
    const alertCode = `${phenom}.${sig}`;

    const colorMap = {
        "TO.W": "#ff0000",
        "SV.W": "#ffd700",
        "FF.W": "#00aa00",
        "MA.W": "#ff66cc",
        "WI.W": "#00bfff",
        "BZ.W": "#66ccff",
        "WS.W": "#3399ff",
        "EH.W": "#ff8800",
        "HU.W": "#9900ff",
        "TR.W": "#ff3399",
        "FA.W": "#228b22",
        "FW.W": "#ff4500",
        "DS.W": "#8b4513"
    };

    if (colorMap[alertCode]) return colorMap[alertCode];

    if (sig === "W") return "#666666";
    if (sig === "A") return "#3399ff";
    if (sig === "Y") return "#ccaa00";

    return "#888888";
}

function loadAlerts() {
    fetch('/api/alerts')
        .then(response => response.json())
        .then(data => {
            console.log("Alert feature count:", data.features.length);

            if (alertsLayer) {
                map.removeLayer(alertsLayer);
            }

            alertsLayer = L.geoJSON(data, {
                style: function(feature) {
                    const props = feature.properties || {};
                    const color = getAlertColor(props.PHENOM, props.SIG);

                    return {
                        color: color,
                        weight: 3,
                        opacity: 1,
                        fillColor: color,
                        fillOpacity: 0.35
                    };
                },

                onEachFeature: function(feature, layer) {
                    const props = feature.properties || {};

                    const popup = `
                        <b>${props.PHENOM}.${props.SIG}</b><br>
                        Type: ${props.TYPE}<br>
                        GTYPE: ${props.GTYPE}<br>
                        WFO: ${props.WFO}<br>
                        ETN: ${props.ETN}<br>
                        Status: ${props.STATUS}<br>
                        Issued: ${props.ISSUED}<br>
                        Expires: ${props.EXPIRED}<br>
                        UGC: ${props.NWS_UGC}
                    `;

                    layer.bindPopup(popup);
                }
            }).addTo(map);

            if (firstLoad) {
                map.fitBounds(southeastBounds);
                firstLoad = false;
            }
        })
        .catch(error => {
            console.error("Error loading alerts:", error);
        });
}

loadAlerts();

setInterval(loadAlerts, 30000);