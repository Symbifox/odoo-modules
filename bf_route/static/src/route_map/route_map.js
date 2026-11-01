/** @odoo-module **/
import { Component, onMounted, onWillStart, onWillUnmount, onWillUpdateProps, useRef, useState } from "@odoo/owl";
import { loadCSS, loadJS } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { ConnectionLostError } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { escape } from "@web/core/utils/strings";

const LEAFLET = "/bf_route/static/lib/leaflet/leaflet";

/**
 * The map of one route day: the start, the stops numbered in order (coloured by their
 * state), the return point, and the road between them from the self-hosted OSRM (straight
 * lines without it). Customers' places only, never the worker's positions.
 * Leaflet is loaded the first time a map opens, not with the backend.
 */
export class RouteMap extends Component {
    static template = "bf_route.RouteMap";
    static props = { dayId: Number, refresh: { type: [String, Number], optional: true } };

    setup() {
        this.orm = useService("orm");
        this.mapRef = useRef("map");
        this.state = useState({ loading: true, error: false, missing: [] });
        onWillStart(async () => {
            await Promise.all([loadJS(`${LEAFLET}.js`), loadCSS(`${LEAFLET}.css`)]);
        });
        onMounted(() => this.draw(this.props.dayId));
        onWillUpdateProps((next) => {
            if (next.dayId !== this.props.dayId || next.refresh !== this.props.refresh) {
                this.draw(next.dayId);
            }
        });
        onWillUnmount(() => this.map && this.map.remove());
    }

    async draw(dayId) {
        // Two calls may cross: only the answer to the last one is drawn.
        const request = (this.request = (this.request || 0) + 1);
        this.state.loading = true;
        this.state.error = false;
        let data;
        try {
            data = await this.orm.call("bf.route.day", "app_map", [dayId]);
        } catch (error) {
            if (request !== this.request) {
                return;
            }
            this.state.loading = false;
            this.state.error = error instanceof ConnectionLostError
                ? _t("No network: the map will show when it is back.")
                : _t("The map could not be loaded.");
            return;
        }
        if (request !== this.request) {
            return;
        }
        this.state.loading = false;
        const L = window.L;
        if (!this.mapRef.el || !L) {
            return;
        }
        if (this.map) {
            this.map.remove();
        }
        const map = L.map(this.mapRef.el, { scrollWheelZoom: false });
        this.map = map;
        const credit = escape(data.tiles.attribution || "");
        // A link only to an https page: never javascript: nor data:, whatever the server says.
        const creditUrl = String(data.tiles.attribution_url || "");
        L.tileLayer(data.tiles.url, {
            maxZoom: 19,
            // The tile server learns the site, not the page (the path names the day).
            referrerPolicy: "strict-origin-when-cross-origin",
            attribution: creditUrl.startsWith("https://")
                ? `<a href="${escape(creditUrl)}" target="_blank" rel="noopener">${credit}</a>`
                : credit,
        }).addTo(map);
        map.attributionControl.setPrefix(false);
        const placed = data.points.filter((p) => p.lat !== null && p.lat !== false);
        this.state.missing = data.points.filter((p) => p.lat === null || p.lat === false).map((p) => p.label);
        const path = data.line || placed.map((p) => [p.lat, p.lon]).concat(
            placed.length && placed[0].kind === "depot" ? [[placed[0].lat, placed[0].lon]] : []
        );
        if (path.length >= 2) {
            L.polyline(path, { className: "o_bf_route_line", weight: 5, opacity: 0.8 }).addTo(map);
        }
        for (const p of placed) {
            const text = p.kind === "stop" ? String(p.rank) : p.kind === "end" ? "■" : "▶";
            const icon = L.divIcon({
                className: `o_bf_route_pin o_bf_pin_${p.kind} o_bf_pin_${p.state || "place"}`,
                html: `<span>${escape(text)}</span>`,
                iconSize: [28, 28],
                iconAnchor: [14, 14],
            });
            const title = p.kind === "stop" ? `${p.rank}. ${p.label}` : p.label;
            L.marker([p.lat, p.lon], { icon, title, keyboard: true }).bindTooltip(escape(title)).addTo(map);
        }
        const bounds = path.length ? L.latLngBounds(path) : null;
        if (bounds && bounds.isValid()) {
            map.fitBounds(bounds, { padding: [24, 24], maxZoom: 15 });
        } else {
            map.setView([46.8, -71.2], 6);
        }
        // The box may have been sized after Leaflet measured it (a tab, a card opening).
        setTimeout(() => map.invalidateSize(), 200);
    }
}

/** The day's map on its form, as a view widget: <widget name="bf_route_day_map"/>. */
export class RouteDayMapWidget extends Component {
    static template = "bf_route.RouteDayMapWidget";
    static components = { RouteMap };
    static props = ["*"];

    get refresh() {
        const data = this.props.record.data;
        return String(data.write_date || "");
    }
}

registry.category("view_widgets").add("bf_route_day_map", {
    component: RouteDayMapWidget,
});
