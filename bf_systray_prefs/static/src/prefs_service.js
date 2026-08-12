/** @odoo-module **/
import { registry } from "@web/core/registry";
import { reactive } from "@odoo/owl";
import { user } from "@web/core/user";

// Per-browser, per-uid cache so the navbar paints the right set on first frame,
// before the authoritative server value comes back.
const cacheKey = () => `bf_systray_hidden_${user.userId}`;

export const bfSystrayPrefsService = {
    dependencies: ["orm"],
    start(env, { orm }) {
        const state = reactive({ hidden: [] });

        try {
            const cached = JSON.parse(localStorage.getItem(cacheKey()) || "null");
            if (Array.isArray(cached)) {
                state.hidden = cached.map(String);
            }
        } catch {
            // ignore malformed cache
        }

        orm.call("res.users", "bf_get_systray_prefs", [])
            .then((keys) => {
                state.hidden = Array.isArray(keys) ? keys.map(String) : [];
                try {
                    localStorage.setItem(cacheKey(), JSON.stringify(state.hidden));
                } catch {
                    // storage unavailable — server stays the source of truth
                }
            })
            .catch(() => {
                // never block the navbar on a failed fetch; keep cached/empty
            });

        return {
            state,
            isHidden: (key) => state.hidden.includes(key),
            async toggle(key, hidden) {
                const set = new Set(state.hidden);
                if (hidden) {
                    set.add(key);
                } else {
                    set.delete(key);
                }
                state.hidden = [...set];
                try {
                    localStorage.setItem(cacheKey(), JSON.stringify(state.hidden));
                } catch {
                    // ignore
                }
                try {
                    await orm.call("res.users", "bf_set_systray_prefs", [state.hidden]);
                } catch {
                    // local state already applied; will resync on next change
                }
            },
        };
    },
};

registry.category("services").add("bf_systray_prefs", bfSystrayPrefsService);
