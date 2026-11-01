/** @odoo-module **/
import { Component, markup, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { ConnectionLostError, RPCError } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import { RouteMap } from "../route_map/route_map";

const RETRY_MS = 30000;
// A refused mark stays on the phone this long at most (the office was told about it).
const REFUSED_DAYS = 7;
const SENT_KEPT = 300;
// A fresh reading only: the notice says "at that moment".
const POSITION_OPTIONS = { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 };

function storeKey(name) {
    return `bf_route_${name}_v1_${user.userId}`;
}

function readStore(name) {
    try {
        return JSON.parse(window.localStorage.getItem(storeKey(name)) || "[]");
    } catch {
        return [];
    }
}

function writeStore(name, items) {
    try {
        window.localStorage.setItem(storeKey(name), JSON.stringify(items));
    } catch {
        // Private browsing or full storage: the marks stay in memory until sent.
    }
}

function newKey() {
    if (window.crypto && window.crypto.randomUUID) {
        return window.crypto.randomUUID();
    }
    return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** One fresh reading of the phone's position, or null. Never throws, never waits more than 10 s. */
function readPosition() {
    return new Promise((resolve) => {
        if (!navigator.geolocation) {
            resolve(null);
            return;
        }
        navigator.geolocation.getCurrentPosition(
            (pos) =>
                resolve({
                    latitude: pos.coords.latitude,
                    longitude: pos.coords.longitude,
                    accuracy: pos.coords.accuracy,
                }),
            () => resolve(null),
            POSITION_OPTIONS
        );
    });
}

/** The phone's system: the maps apps it can open differ. */
function platform() {
    const agent = navigator.userAgent || "";
    // iPadOS presents itself as a Mac; it is the Mac with a touch screen.
    if (/iPhone|iPad|iPod/.test(agent) || (/Macintosh/.test(agent) && navigator.maxTouchPoints > 1)) {
        return "ios";
    }
    return /Android/.test(agent) ? "android" : "other";
}

/** "HH:MM" of the phone's clock, shown until the server answers with its own. */
function clockNow() {
    const now = new Date();
    return `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
}

/** The server said "your session is over": like no network, the marks wait. */
function isSessionExpired(error) {
    return (
        error instanceof RPCError &&
        (error.code === 100 || String(error.data?.name || "").includes("SessionExpired"))
    );
}

/**
 * "My route": the worker's day on the phone (/odoo/my-route, installable as an app
 * through /scoped_app?app_id=bf_route&path=odoo/my-route).
 *
 * ⚠️ A mark is never thrown away. It is shown at once and queued; the queue is sent
 * when the network is there, with the phone's time of the mark and a key, so a mark
 * sent twice counts once. No network or an expired session: the mark waits. A mark the
 * server REFUSES (day closed by the office, stop removed...) is kept on the phone with
 * its data and the reason, the stop goes back to "to do", and the worker sees it until
 * they dismiss it or mark the stop again.
 */
export class MyRoute extends Component {
    static template = "bf_route.MyRoute";
    static components = { RouteMap };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.sent = new Set(readStore("sent"));
        // A refused mark is put back on screen once per opening: after that, what the worker
        // corrects is theirs, and another tab's write must not bring the old values back.
        this.restored = new Set();
        const fresh = Date.now() - REFUSED_DAYS * 86400000;
        writeStore("refused", readStore("refused").filter((r) => (r.at || 0) > fresh));
        this.state = useState({
            loading: true,
            days: [],
            notice: {},
            queue: readStore("queue"),
            refused: readStore("refused"),
            online: navigator.onLine,
            sessionExpired: false,
            busy: false,
            reading: false,
            forms: {},
            openNote: null,
            openNav: null,
            openMap: {},
            mapRefresh: 0,
            notes: {},
        });
        this.platform = platform();
        this.onOnline = () => {
            this.state.online = true;
            this.flush();
        };
        this.onOffline = () => {
            this.state.online = false;
        };
        // Another tab of the same app wrote the queue: take its marks too. This handler
        // never writes unless something changed, so two tabs do not wake each other forever.
        this.onStorage = (ev) => {
            if (ev.key === storeKey("queue") || ev.key === storeKey("sent")) {
                this.mergeQueue();
                this.applyQueue();
            } else if (ev.key === storeKey("refused")) {
                this.state.refused = readStore("refused");
                this.applyQueue();
            }
        };
        onWillStart(() => this.load());
        onMounted(() => {
            window.addEventListener("online", this.onOnline);
            window.addEventListener("offline", this.onOffline);
            window.addEventListener("storage", this.onStorage);
            this.timer = setInterval(() => this.state.queue.length && this.flush(), RETRY_MS);
            this.flush();
        });
        onWillUnmount(() => {
            window.removeEventListener("online", this.onOnline);
            window.removeEventListener("offline", this.onOffline);
            window.removeEventListener("storage", this.onStorage);
            clearInterval(this.timer);
        });
    }

    async load() {
        try {
            const data = await this.orm.call("bf.route.day", "app_load", []);
            this.state.days = data.days;
            this.state.notice = data.notice;
            this.state.today = data.today;
            this.applyQueue();
        } catch (error) {
            if (error instanceof ConnectionLostError) {
                this.state.online = false;
            } else if (isSessionExpired(error)) {
                this.state.sessionExpired = true;
            } else {
                throw error;
            }
        } finally {
            this.state.loading = false;
        }
    }

    // ------------------------------------------------------------------
    // The queue, kept on the phone
    // ------------------------------------------------------------------

    /** The queue in memory and the one stored by another tab, minus what any tab sent. */
    mergeQueue() {
        for (const key of readStore("sent")) {
            this.sent.add(key);
        }
        const stored = readStore("queue");
        const known = new Set(this.state.queue.map((item) => item.key));
        for (const item of stored) {
            if (!known.has(item.key) && !this.sent.has(item.key)) {
                this.state.queue.push(item);
                known.add(item.key);
            }
        }
        this.state.queue = this.state.queue.filter((item) => !this.sent.has(item.key));
        const want = JSON.stringify(this.state.queue.map((item) => item.key));
        if (want !== JSON.stringify(stored.map((item) => item.key))) {
            writeStore("queue", this.state.queue);
        }
    }

    markSent(key) {
        this.sent.add(key);
        writeStore("sent", [...this.sent].slice(-SENT_KEPT));
    }

    /** The refused marks live in the phone's storage, shared by its tabs. */
    addRefused(item) {
        const list = readStore("refused").filter((r) => r.key !== item.key);
        list.push(item);
        writeStore("refused", list);
        this.state.refused = list;
    }

    removeRefused(predicate) {
        const list = readStore("refused").filter((r) => !predicate(r));
        writeStore("refused", list);
        this.state.refused = list;
    }

    /** Marks not sent yet are shown as made; refused ones show their reason. */
    applyQueue() {
        for (const item of this.state.queue) {
            const stop = this.findStop(item.stopId);
            if (stop && item.kind === "arrive") {
                stop.arrived_at = stop.arrived_at || item.clock;
                stop.pending = true;
            } else if (stop) {
                stop.state = item.state;
                stop.pending = true;
            }
        }
        for (const item of this.state.refused) {
            const stop = this.findStop(item.stopId);
            if (stop && stop.state === "todo") {
                stop.refusal = item.reason;
                stop.refusalKind = item.kind || false;
                if (!this.restored.has(item.key)) {
                    this.restored.add(item.key);
                    this.restoreItem(stop, item);
                }
            }
        }
    }

    dayOf(stopId) {
        return this.state.days.find((day) => day.stops.some((s) => s.id === stopId));
    }

    findStop(stopId) {
        for (const day of this.state.days) {
            const stop = day.stops.find((s) => s.id === stopId);
            if (stop) {
                return stop;
            }
        }
        return null;
    }

    noticeText() {
        return markup(this.state.notice.text || "");
    }

    form(day) {
        if (!this.state.forms[day.id]) {
            this.state.forms[day.id] = { odometer: "", safety: false, odometerEnd: "" };
        }
        return this.state.forms[day.id];
    }

    progress(day) {
        const marked = day.stops.filter((s) => s.state !== "todo").length;
        return `${marked} / ${day.stops.length}`;
    }

    nextStop(day) {
        return day.stops.find((s) => s.state === "todo");
    }

    /**
     * The links that start the navigation to a stop, in the phone's own apps. Android: one geo:
     * link, the phone asks which app (Maps, Waze...). An iPhone does not open geo: links: Apple
     * Plans, Google Maps and Waze, each by its https link (the app opens when it is installed,
     * the web page otherwise; CarPlay follows the phone). A computer: Google Maps on the web.
     * What leaves the phone is the stop's coordinates, or its address when it has none, and
     * only when the worker taps. ⚠️ Never the customer's name alone (Quebec Law 25): without
     * coordinates nor address, no link at all. ``external``: an https link opens in a new tab;
     * a geo: link is handed to the phone's apps (a new tab would stay empty on Android).
     */
    navLinks(stop) {
        const point = stop.latitude && stop.longitude ? `${stop.latitude},${stop.longitude}` : null;
        if (!point && !stop.address) {
            return [];
        }
        const where = encodeURIComponent(point || stop.address);
        const google = `https://www.google.com/maps/dir/?api=1&destination=${where}`;
        if (this.platform === "android") {
            const label = encodeURIComponent(stop.partner);
            return [{
                label: _t("Directions"),
                url: point ? `geo:${point}?q=${point}(${label})` : `geo:0,0?q=${where}`,
                external: false,
            }];
        }
        if (this.platform === "ios") {
            return [
                { label: _t("Apple Maps"), url: `https://maps.apple.com/?daddr=${where}`, external: true },
                { label: _t("Google Maps"), url: google, external: true },
                {
                    label: _t("Waze"),
                    url: point
                        ? `https://waze.com/ul?ll=${point}&navigate=yes`
                        : `https://waze.com/ul?q=${where}&navigate=yes`,
                    external: true,
                },
            ];
        }
        return [{ label: _t("Directions"), url: google, external: true }];
    }

    toggleNav(stop) {
        this.state.openNav = this.state.openNav === stop.id ? null : stop.id;
    }

    toggleMap(day) {
        this.state.openMap[day.id] = !this.state.openMap[day.id];
    }

    stateLabel(stop) {
        return {
            todo: _t("To do"),
            done: _t("Done"),
            absent: _t("Customer absent"),
            postponed: _t("Postponed"),
            missed: _t("Missed"),
        }[stop.state];
    }

    async acknowledge() {
        this.state.busy = true;
        try {
            this.state.notice = await this.orm.call("bf.route.notice.ack", "app_acknowledge", [
                this.state.notice.version,
                this.state.notice.company_id,
            ]);
        } catch (error) {
            this.explain(error);
        } finally {
            this.state.busy = false;
        }
    }

    async start(day) {
        const form = this.form(day);
        this.state.busy = true;
        try {
            const fresh = await this.orm.call("bf.route.day", "app_start", [day.id], {
                odometer: form.odometer || null,
                safety_check: form.safety,
                client_time: new Date().toISOString(),
            });
            Object.assign(day, fresh);
            this.applyQueue();
        } catch (error) {
            this.explain(error);
        } finally {
            this.state.busy = false;
        }
    }

    async finish(day) {
        const left = day.stops.filter((s) => s.state === "todo");
        // An arrival alone does not close a stop: said apart, it is easy to forget.
        const arrived = left.filter((s) => s.arrived_at).length;
        const others = left.length - arrived;
        let question = null;
        if (arrived) {
            question = [
                _t("%s stop(s) where you arrived are not marked yet: they will be counted as missed.", arrived),
                others ? _t("%s other stop(s) not marked will be counted as missed.", others) : "",
                _t("Finish anyway?"),
            ].filter(Boolean).join("\n");
        } else if (others) {
            question = _t("%s stop(s) not marked will be counted as missed. Finish anyway?", others);
        }
        if (question && !window.confirm(question)) {
            return;
        }
        await this.flush();
        if (this.state.queue.length) {
            this.notification.add(_t("Some marks are still waiting for the network. Finish once they are sent."), {
                type: "warning",
            });
            return;
        }
        const form = this.form(day);
        this.state.busy = true;
        try {
            const fresh = await this.orm.call("bf.route.day", "app_finish", [day.id], {
                odometer: form.odometerEnd || null,
                client_time: new Date().toISOString(),
            });
            Object.assign(day, fresh);
        } catch (error) {
            this.explain(error);
        } finally {
            this.state.busy = false;
        }
    }

    toggleNote(stop) {
        this.state.openNote = this.state.openNote === stop.id ? null : stop.id;
        if (this.state.notes[stop.id] === undefined) {
            this.state.notes[stop.id] = stop.note || "";
        }
    }

    /** "I'm here": the arrival at the stop, queued and sent like a mark. No position. */
    async arrive(stop) {
        if (stop.arrived_at || stop.state !== "todo") {
            return;
        }
        const item = {
            kind: "arrive",
            stopId: stop.id,
            dayId: this.dayOf(stop.id)?.id,
            label: stop.partner,
            state: "arrived",
            prevState: stop.state,
            note: null,
            clock: clockNow(),
            clientTime: new Date().toISOString(),
            key: newKey(),
        };
        // A new arrival replaces an old refused one; a refused mark of the stop stays.
        this.removeRefused((r) => r.stopId === stop.id && r.kind === "arrive");
        const left = this.state.refused.find((r) => r.stopId === stop.id);
        stop.refusal = left ? left.reason : false;
        stop.refusalKind = left ? left.kind : false;
        stop.arrived_at = item.clock;
        stop.pending = true;
        this.state.queue.push(item);
        this.mergeQueue();
        await this.flush();
    }

    async mark(stop, newState) {
        const extra = this.extraFor(stop, newState);
        if (extra === false) {
            return;
        }
        let position = null;
        if (this.state.notice.may_read_position) {
            this.state.reading = true;
            position = await readPosition();
            this.state.reading = false;
        }
        const item = {
            stopId: stop.id,
            dayId: this.dayOf(stop.id)?.id,
            label: stop.partner,
            state: newState,
            prevState: stop.state,
            extra,
            note: this.state.notes[stop.id] ?? null,
            position,
            clientTime: new Date().toISOString(),
            key: newKey(),
        };
        // A new mark of this stop replaces an old refusal.
        this.removeRefused((r) => r.stopId === stop.id);
        stop.refusal = false;
        stop.refusalKind = false;
        stop.state = newState;
        stop.pending = true;
        this.state.openNote = null;
        this.state.queue.push(item);
        this.mergeQueue();
        await this.flush();
    }

    /**
     * What a satellite adds to a mark (a sale, for instance). Return false to stop the
     * mark (the satellite explains why). The base adds nothing.
     */
    extraFor(stop, newState) {
        return {};
    }

    /** Put back on screen what a refused mark carried, so it can be sent again. */
    restoreItem(stop, item) {
        if (item.note !== null && item.note !== undefined) {
            this.state.notes[stop.id] = item.note;
        }
    }

    dismissRefusal(item) {
        this.removeRefused((r) => r.key === item.key);
        const stop = this.findStop(item.stopId);
        if (stop && !this.state.refused.some((r) => r.stopId === stop.id)) {
            stop.refusal = false;
        }
    }

    dismissStopRefusal(stop) {
        this.removeRefused((r) => r.stopId === stop.id);
        stop.refusal = false;
    }

    /**
     * Tell the office about a refused mark, on the DAY (the stop may have been deleted).
     * Kept for later when there is no network; the same key counts once on the server.
     */
    async reportRefusal(item) {
        if (!item.dayId) {
            return;
        }
        try {
            const recorded = await this.orm.call("bf.route.day", "app_report_refusal", [item.dayId], {
                key: item.key,
                stop_id: item.stopId,
                label: item.label,
                reason: item.reason,
                state: item.state,
                note: item.note,
                ...(item.extra || {}),
            });
            if (recorded === false) {
                this.notification.add(_t("The office could not be told about a refused mark: call them."), {
                    type: "warning",
                    sticky: true,
                });
            }
        } catch (error) {
            if (error instanceof ConnectionLostError || isSessionExpired(error)) {
                return;
            }
            // The day is not ours any more: the refusal stays listed on the phone.
            this.notification.add(_t("The office could not be told about a refused mark: call them."), {
                type: "warning",
                sticky: true,
            });
        }
        const list = readStore("refused").map((r) => (r.key === item.key ? { ...r, reported: true } : r));
        writeStore("refused", list);
        this.state.refused = list;
    }

    /** Send the queued marks, oldest first. Stops at the first network or session failure. */
    async flush() {
        if (this.flushing) {
            return this.flushing;
        }
        this.flushing = (async () => {
            this.mergeQueue();
            let recorded = false;
            while (this.state.queue.length) {
                const item = this.state.queue[0];
                try {
                    const fresh = item.kind === "arrive"
                        ? await this.orm.call("bf.route.day.stop", "app_arrive", [item.stopId], {
                              client_time: item.clientTime,
                              app_key: item.key,
                          })
                        : await this.orm.call("bf.route.day.stop", "app_mark", [item.stopId], {
                              state: item.state,
                              note: item.note,
                              position: item.position,
                              client_time: item.clientTime,
                              app_key: item.key,
                              ...(item.extra || {}),
                          });
                    const stop = this.findStop(item.stopId);
                    if (fresh._notice) {
                        this.state.notice = fresh._notice;
                        delete fresh._notice;
                    }
                    if (stop) {
                        Object.assign(stop, fresh, { pending: false });
                    }
                    recorded = true;
                    this.state.online = true;
                    this.state.sessionExpired = false;
                } catch (error) {
                    if (error instanceof ConnectionLostError) {
                        this.state.online = false;
                        break;
                    }
                    if (isSessionExpired(error)) {
                        if (!this.state.sessionExpired) {
                            this.notification.add(
                                _t("Sign in again to send your marks. They are kept on this phone."),
                                { type: "warning", sticky: true }
                            );
                        }
                        this.state.sessionExpired = true;
                        break;
                    }
                    // Refused by the server: kept on the phone with the reason (not the
                    // position: a new mark reads a new one), and reported to the office.
                    const reason = (error instanceof RPCError && (error.data?.message || error.message)) ||
                        String(error);
                    const refused = { ...item, position: null, reason, at: Date.now(), reported: false };
                    this.addRefused(refused);
                    await this.reportRefusal(refused);
                    const stop = this.findStop(item.stopId);
                    if (stop) {
                        stop.state = item.prevState || "todo";
                        if (item.kind === "arrive") {
                            stop.arrived_at = false;
                        }
                        stop.pending = false;
                        stop.refusal = reason;
                        stop.refusalKind = item.kind || false;
                        this.restored.add(item.key);
                        this.restoreItem(stop, item);
                    }
                    const told = item.kind === "arrive"
                        ? _t("An arrival was not recorded: %s", reason)
                        : _t("A mark was not recorded: %s", reason);
                    this.notification.add(told, {
                        type: "danger",
                        sticky: true,
                    });
                }
                this.markSent(item.key);
                this.state.queue.shift();
                this.mergeQueue();
                // ⚠️ The server's answer is the stop as it stands: another queued mark of the
                // same stop (an arrival sent, "Absent" still waiting) is shown again over it.
                this.applyQueue();
            }
            if (recorded) {
                // The map's pins follow the marks: redrawn once per pass, not per mark.
                this.state.mapRefresh++;
            }
            if (this.state.online && !this.state.sessionExpired) {
                for (const item of this.state.refused.filter((r) => !r.reported)) {
                    await this.reportRefusal(item);
                }
            }
        })();
        try {
            await this.flushing;
        } finally {
            this.flushing = null;
        }
    }

    explain(error) {
        if (error instanceof ConnectionLostError) {
            this.notification.add(_t("No network. Try again when it is back."), { type: "warning" });
            return;
        }
        if (isSessionExpired(error)) {
            this.state.sessionExpired = true;
            this.notification.add(_t("Sign in again to continue."), { type: "warning", sticky: true });
            return;
        }
        if (error instanceof RPCError) {
            this.notification.add(error.data?.message || error.message, { type: "danger" });
            return;
        }
        throw error;
    }

    async reload() {
        await this.flush();
        this.state.loading = true;
        await this.load();
        this.state.mapRefresh++;
    }
}

registry.category("actions").add("bf_route.my_route", MyRoute);
