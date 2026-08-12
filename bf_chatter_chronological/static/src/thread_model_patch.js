/** @odoo-module **/

import { Thread } from "@mail/core/common/thread_model";
import { compareDatetime } from "@mail/utils/common/misc";
import { patch } from "@web/core/utils/patch";

// Display order for the chatter's `messages` array: oldest-first (the array
// is iterated by the chatter template; visual reverse happens in template /
// CSS). Tie-break on id when dates match.
function compareByDateAsc(m1, m2) {
    const c = compareDatetime(m1.date, m2.date);
    return c !== 0 ? c : m1.id - m2.id;
}

patch(Thread.prototype, {
    /**
     * Pagination + load-more — same as upstream, but adds a final
     * date-based sort so backdated imports land at the right spot
     * regardless of their insertion id.
     */
    async fetchMoreMessages(epoch = "older") {
        await super.fetchMoreMessages(epoch);
        this.messages.sort(compareByDateAsc);
    },

    /**
     * New-message fetch (called periodically or on focus). Final sort
     * keeps the array chronological after splicing newcomers in.
     */
    async fetchNewMessages() {
        await super.fetchNewMessages();
        this.messages.sort(compareByDateAsc);
    },

    /**
     * Initial chatter open — server already returns date-desc (via our
     * Python _order override) which gets reversed inside fetchMessages
     * to date-asc. This patch adds a safety re-sort after the upstream
     * fetch completes, in case any subsequent merge step disturbs order.
     */
    async fetchMessages(opts = {}) {
        const r = await super.fetchMessages(opts);
        if (Array.isArray(this.messages) && this.messages.length > 1) {
            this.messages.sort(compareByDateAsc);
        }
        return r;
    },
});
