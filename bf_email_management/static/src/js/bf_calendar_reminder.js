/** @odoo-module **/

/*
 * Blue Fox calendar reminder — replaces the standard `calendarNotification`
 * service so the popup buttons (Snooze 5/15/60min/Demain/Custom, Dismiss,
 * Détails) hit real server endpoints (`bf_snooze` / `bf_dismiss` on
 * `calendar.attendee`) instead of merely closing the toast.
 *
 * The bus.bus channel `calendar.alarm` is unchanged; only this client-side
 * receiver is overridden, and the standard service is replaced (force: true).
 */

import { _t } from "@web/core/l10n/translation";
import { browser } from "@web/core/browser/browser";
import { ConnectionLostError, rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";

// Labels use non-breaking spaces ( ) so Odoo's notification toast
// doesn't wrap them mid-word (e.g. "1 h" -> "1\nh"). Keep them short
// because the toast renders 7+ buttons in a narrow strip.
const SNOOZE_PRESETS = [
    { label: _t("5 min"), minutes: 5 },
    { label: _t("15 min"), minutes: 15 },
    { label: _t("1 h"), minutes: 60 },
    { label: _t("Demain"), minutes: null, kind: "tomorrow_8" },
];

function tomorrow8AmIso() {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(8, 0, 0, 0);
    // Convert to UTC ISO without ms, format Odoo expects: YYYY-MM-DD HH:MM:SS
    const pad = (n) => String(n).padStart(2, "0");
    return (
        `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
        `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`
    );
}

export const bfCalendarNotificationService = {
    dependencies: ["action", "bus_service", "notification", "orm"],

    start(env, { action, bus_service, notification, orm }) {
        let calendarNotifTimeouts = {};
        let nextCalendarNotifTimeout = null;
        const displayedNotifications = new Set();

        bus_service.subscribe("calendar.alarm", (payload) => {
            displayCalendarNotification(payload);
        });
        bus_service.start();

        // On service start, proactively pull any pending alarms via
        // /calendar/notify. Without this, refreshing the page when an
        // alarm has already fired (but is still pending — not snoozed
        // or dismissed) silently drops it: bus.bus only re-emits when
        // _notify_next_alarm is called server-side, not on client
        // connect.
        getNextCalendarNotif();

        function buildSnoozeButtons(notif, notificationRemove) {
            const buttons = SNOOZE_PRESETS.map((preset) => ({
                name: preset.label,
                onClick: async () => {
                    const args = [notif.event_id];
                    const kwargs =
                        preset.kind === "tomorrow_8"
                            ? { until: tomorrow8AmIso() }
                            : { minutes: preset.minutes };
                    try {
                        await orm.call("calendar.attendee", "bf_snooze", args, kwargs);
                    } catch (e) {
                        notification.add(_t("Snooze a échoué"), { type: "danger" });
                        throw e;
                    }
                    notificationRemove();
                },
            }));
            buttons.push({
                name: _t("Autre…"),
                onClick: async () => {
                    const value = browser.prompt(
                        _t("Reporter de combien de minutes ?"),
                        "30"
                    );
                    if (!value) {
                        return;
                    }
                    const minutes = parseInt(value, 10);
                    if (!minutes || minutes <= 0) {
                        return;
                    }
                    await orm.call(
                        "calendar.attendee",
                        "bf_snooze",
                        [notif.event_id],
                        { minutes }
                    );
                    notificationRemove();
                },
            });
            buttons.push({
                name: _t("Ignorer"),
                onClick: async () => {
                    await orm.call("calendar.attendee", "bf_dismiss", [notif.event_id]);
                    await rpc("/calendar/notify_ack");
                    notificationRemove();
                },
            });
            buttons.push({
                name: _t("Ouvrir"),
                onClick: async () => {
                    await action.doAction({
                        type: "ir.actions.act_window",
                        res_model: "calendar.event",
                        res_id: notif.event_id,
                        views: [[false, "form"]],
                    });
                    notificationRemove();
                },
            });
            return buttons;
        }

        function displayCalendarNotification(notifications) {
            let lastNotifTimer = 0;

            browser.clearTimeout(nextCalendarNotifTimeout);
            Object.values(calendarNotifTimeouts).forEach((id) => browser.clearTimeout(id));
            calendarNotifTimeouts = {};

            notifications.forEach(function (notif) {
                const key = notif.event_id + "," + notif.alarm_id;
                if (displayedNotifications.has(key)) {
                    return;
                }
                calendarNotifTimeouts[key] = browser.setTimeout(function () {
                    let notificationRemove = () => {};
                    notificationRemove = notification.add(notif.message, {
                        title: notif.title,
                        type: "warning",
                        sticky: true,
                        onClose: () => {
                            displayedNotifications.delete(key);
                        },
                        buttons: buildSnoozeButtons(notif, () => notificationRemove()),
                    });
                    displayedNotifications.add(key);
                }, notif.timer * 1000);
                lastNotifTimer = Math.max(lastNotifTimer, notif.timer);
            });

            if (lastNotifTimer > 0) {
                nextCalendarNotifTimeout = browser.setTimeout(
                    getNextCalendarNotif,
                    lastNotifTimer * 1000
                );
            }
        }

        async function getNextCalendarNotif() {
            try {
                const result = await rpc("/calendar/notify", {}, { silent: true });
                displayCalendarNotification(result);
            } catch (error) {
                if (!(error instanceof ConnectionLostError)) {
                    throw error;
                }
            }
        }
    },
};

// Replace the standard service with ours (Odoo registry supports force overwrite).
registry
    .category("services")
    .add("calendarNotification", bfCalendarNotificationService, { force: true });
