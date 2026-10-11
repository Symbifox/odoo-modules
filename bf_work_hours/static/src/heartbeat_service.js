/** @odoo-module **/
// Working time (bf_work_hours): every minute, tell the server whether the
// person used the keyboard or the mouse in Odoo during the last two minutes.
// The server keeps it only for the person of this session, and only if they
// turned "Propose my hours" on; otherwise it answers to stop asking.
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { session } from "@web/session";

const EVERY_MS = 60 * 1000;
const RECENT_MS = 2 * 60 * 1000;
const LOCK_KEY = "bf_work_hours.lastBeat";

export const workHoursHeartbeatService = {
    dependencies: ["presence"],
    start(env, { presence }) {
        if (session.is_public) {
            return;
        }
        let timer = null;
        const beat = async () => {
            const inactivity = presence.getInactivityPeriod();
            if (inactivity > RECENT_MS) {
                return;
            }
            // One tab reports for all: the inactivity is shared between tabs.
            const now = Date.now();
            const last = Number(browser.localStorage.getItem(LOCK_KEY) || 0);
            // A stamp in the future (a clock set back, another script) is stale, not a lock.
            if (last <= now && now - last < EVERY_MS - 5000) {
                return;
            }
            browser.localStorage.setItem(LOCK_KEY, String(now));
            try {
                const result = await rpc("/bf_work_hours/heartbeat", { inactivity_ms: inactivity }, { silent: true });
                if (result && result.enabled === false && timer) {
                    browser.clearInterval(timer);
                    timer = null;
                }
            } catch {
                // Offline or logged out: the next beat tries again.
            }
        };
        timer = browser.setInterval(beat, EVERY_MS);
        browser.setTimeout(beat, 5000);
    },
};

registry.category("services").add("bf_work_hours_heartbeat", workHoursHeartbeatService);
