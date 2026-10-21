/** @odoo-module **/

import { Component, onWillDestroy, onWillStart, toRaw, useState } from "@odoo/owl";
import { Chatter } from "@mail/chatter/web_portal/chatter";
import { Composer } from "@mail/core/common/composer";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import { user } from "@web/core/user";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { NcBrowserApp } from "@bf_nextcloud_browser/js/nc_browser";

const GROUP = "bf_nextcloud_browser.group_nc_browser_user";

/**
 * Whether the Nextcloud buttons show, asked once per page: the composer and
 * the chatter are mounted on every record opened, the answer does not change
 * between them. Same rule as the systray button (group + usable configuration).
 *
 * 🔴 The shared promise must NOT come from a component's `orm`: `useService`
 * wraps its calls so that they never settle once that component is destroyed.
 * Leave the first record before the answer and the cached promise hung for the
 * whole page; every chatter awaiting it in `onWillStart` then kept its form
 * from rendering (adversarial review of 18.0.4.1.0, proved on the bench). The
 * plain `rpc` belongs to no component, nothing waits on it in `onWillStart`,
 * and a failure is not kept.
 */
let availability = null;
function nextcloudAvailable() {
    if (!availability) {
        availability = user
            .hasGroup(GROUP)
            .then((inGroup) =>
                inGroup
                    ? rpc("/web/dataset/call_kw/bf.nc.browser/get_panel_config", {
                          model: "bf.nc.browser",
                          method: "get_panel_config",
                          args: [],
                          kwargs: {},
                      }).then((cfg) => !!(cfg && cfg.available))
                    : false
            )
            .catch(() => {
                availability = null;
                return false;
            });
    }
    return availability;
}

/** Show the button once the answer is there, without holding the component back. */
function useNextcloudAvailable() {
    const state = useState({ available: false });
    let alive = true;
    onWillDestroy(() => {
        alive = false;
    });
    nextcloudAvailable().then((available) => {
        if (alive) {
            state.available = available;
        }
    });
    return state;
}

/** A chatter, not a Discuss channel, on a saved record. */
function isRecordThread(thread) {
    return !!thread && !!thread.id && !!thread.model && thread.model !== "discuss.channel";
}

function errorMessage(e) {
    return (e && e.data && e.data.message) || (e && e.message) || _t("Error.");
}

/**
 * The browser in a dialog, in picker mode: a file or folder picked, then the
 * kind of link chosen, comes back to the composer as a link to insert.
 */
export class NcPickerDialog extends Component {
    static template = "bf_nextcloud_browser.NcPickerDialog";
    static components = { Dialog, NcBrowserApp };
    static props = { close: Function, onInsert: Function };

    // A Dialog title written in the template would never be translated.
    get title() {
        return _t("Insert a Nextcloud link");
    }

    onPick(link) {
        this.props.onInsert(link);
        this.props.close();
    }
}

/**
 * The Nextcloud files already linked in a chatter, read again from its
 * messages each time: nothing is stored, so a removed message takes its link
 * with it and a deleted file shows as not found.
 */
export class NcLinkedFilesDialog extends Component {
    static template = "bf_nextcloud_browser.NcLinkedFilesDialog";
    static components = { Dialog };
    static props = { close: Function, model: String, resId: Number };

    get title() {
        return _t("Nextcloud files linked in this thread");
    }

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.state = useState({ loading: true, error: "", links: [], truncated: false });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            const res = await this.orm.call("bf.nc.browser", "linked_files", [
                this.props.model,
                this.props.resId,
            ]);
            this.state.links = res.links || [];
            this.state.truncated = !!res.truncated;
            this.state.error = "";
        } catch (e) {
            const name = (e && e.data && e.data.name) || "";
            this.state.error =
                name.endsWith(".NcNotConnected") || name.endsWith(".NcTokenRejected")
                    ? _t("Connect your Nextcloud (Nextcloud button in the top bar) to see the status of these files."
                      )
                    : errorMessage(e);
        } finally {
            this.state.loading = false;
        }
    }

    label(link) {
        if (link.entry) {
            return link.entry.name;
        }
        if (link.share && link.share.path) {
            return link.share.path.split("/").filter(Boolean).pop() || link.share.path;
        }
        return link.kind === "internal" ? _t("File no. %s", link.key) : _t("Share %s", link.key);
    }

    where(link) {
        if (link.entry) {
            return link.entry.parent_rel || _t("Root folder");
        }
        if (link.share && link.share.rel) {
            const parts = link.share.rel.split("/");
            parts.pop();
            return parts.join("/") || _t("Root folder");
        }
        return "";
    }

    stateLabel(link) {
        if (!link.found) {
            return link.kind === "internal"
                ? _t("Not found: deleted, or not shared with you")
                : _t("Not in your shares: removed, or created by someone else");
        }
        if (link.kind === "internal") {
            return _t("Internal link");
        }
        const sh = link.share;
        const parts = [sh.kind];
        parts.push(
            sh.expiration
                ? _t("expires on %s", sh.expiration)
                : sh.expiry_checked
                  ? _t("no expiry")
                  : _t("expiry not checked")
        );
        if (sh.has_password) {
            parts.push(_t("password"));
        }
        if (sh.writable) {
            parts.push(_t("write allowed"));
        }
        return parts.join(" · ");
    }

    open(link) {
        window.open(link.url, "_blank", "noopener,noreferrer");
    }

    async copy(link) {
        try {
            await navigator.clipboard.writeText(link.url);
            this.notification.add(_t("Link copied."), { type: "success" });
        } catch {
            this.notification.add(link.url, { title: _t("Link address"), sticky: true });
        }
    }

    revoke(link) {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Remove share"),
            body: _t("The public link to \"%s\" will stop working for everyone who received it, including in messages already sent.",
                this.label(link)
            ),
            confirmLabel: _t("Remove"),
            confirm: async () => {
                try {
                    const res = await this.orm.call("bf.nc.browser", "linked_revoke_share", [
                        this.props.model,
                        this.props.resId,
                        link.share.id,
                    ]);
                    if (res && res.gone) {
                        this.notification.add(
                            _t("This share no longer exists, or Nextcloud does not show it to you: nothing was removed."),
                            { type: "warning" }
                        );
                    } else {
                        this.notification.add(_t("Share removed."), { type: "success" });
                    }
                } catch (e) {
                    this.notification.add(errorMessage(e), { type: "danger" });
                }
                await this.load();
            },
            cancel: () => {},
        });
    }
}

/**
 * File chatter attachments in Nextcloud (18.0.4.3.0): the attachments to tick
 * on the left, the browser on the right, opened on the record's client folder.
 * The attachment is COPIED and stays in Odoo.
 */
export class NcFileDialog extends Component {
    static template = "bf_nextcloud_browser.NcFileDialog";
    static components = { Dialog, NcBrowserApp };
    static props = { close: Function, model: String, resId: Number, onDone: { type: Function, optional: true } };

    get title() {
        return _t("File attachments in the client folder");
    }

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            error: "",
            attachments: [],
            selected: [],
            startRel: "",
            label: "",
            rel: null,
            crumbs: [],
            busy: false,
            batchMax: 50,
        });
        onWillStart(() => this.load());
    }

    async load() {
        try {
            const [list, start] = await Promise.all([
                this.orm.call("bf.nc.browser", "thread_attachments", [this.props.model, this.props.resId]),
                this.orm.call("bf.nc.browser", "root_record_path", [this.props.model, this.props.resId]),
            ]);
            this.state.attachments = list.attachments || [];
            this.state.batchMax = list.batch_max || this.state.batchMax;
            this.state.startRel = (start && start.rel_path) || "";
            this.state.label = (start && start.label) || "";
        } catch (e) {
            this.state.error = errorMessage(e);
        } finally {
            this.state.loading = false;
        }
    }

    toggle(att) {
        if (att.too_big) {
            return;
        }
        const i = this.state.selected.indexOf(att.id);
        if (i >= 0) {
            this.state.selected.splice(i, 1);
        } else if (this.state.selected.length < this.state.batchMax) {
            this.state.selected.push(att.id);
        }
    }

    isLocked(att) {
        return (
            att.too_big ||
            (!this.state.selected.includes(att.id) && this.state.selected.length >= this.state.batchMax)
        );
    }

    onNavigate(rel, crumbs) {
        this.state.rel = rel || "";
        this.state.crumbs = (crumbs || []).map((c) => c.name);
    }

    get where() {
        return this.state.crumbs.slice(1).join(" / ") || _t("Root folder");
    }

    get canFile() {
        return this.state.selected.length > 0 && this.state.rel !== null && !this.state.busy;
    }

    async file() {
        if (!this.canFile) {
            return;
        }
        this.state.busy = true;
        try {
            const res = await this.orm.call("bf.nc.browser", "root_file_attachments", [
                this.props.model,
                this.props.resId,
                [...this.state.selected],
                this.state.rel,
            ]);
            const n = (res.done || []).length;
            const failed = res.failed || [];
            if ((res.skipped || []).length) {
                this.notification.add(
                    _t("Too large, left in Odoo: %s", res.skipped.join(", ")),
                    { type: "warning", sticky: true }
                );
            }
            if (failed.length) {
                this.notification.add(
                    failed.map((f) => _t("%(name)s: %(error)s", { name: f.name, error: f.error })).join("; "),
                    { title: _t("Not copied"), type: "danger", sticky: true }
                );
            }
            if (!n) {
                // Rien n'est parti : la boite reste ouverte, rien ne dit « fait ».
                return;
            }
            this.notification.add(
                _t("%(n)s attachment(s) copied to %(where)s.", { n, where: this.where }),
                { type: "success" }
            );
            this.props.onDone?.();
            this.props.close();
        } catch (e) {
            this.notification.add(errorMessage(e), { type: "danger" });
            // Un proxy qui coupe (504) n'arrete pas le serveur : la note de ce
            // qui est passe peut arriver quand meme, on relit le fil.
            this.props.onDone?.();
        } finally {
            this.state.busy = false;
        }
    }
}

// ----------------------------------------------------------------------
// Composer: « Insérer un lien Nextcloud »
// ----------------------------------------------------------------------
patch(Composer.prototype, {
    setup() {
        super.setup(...arguments);
        this.bfNc = useNextcloudAvailable();
        this.bfNcDialog = useService("dialog");
        this.bfNcNotification = useService("notification");
    },

    get bfNcVisible() {
        return (
            this.bfNc.available &&
            !this.props.composer?.message &&
            isRecordThread(this.props.composer?.thread)
        );
    },

    onClickBfNcInsert() {
        this.bfNcDialog.add(NcPickerDialog, {
            onInsert: (link) => this.bfNcInsertLink(link),
        });
    },

    /** The link on a line of its own, where the cursor was. */
    bfNcInsertLink(link) {
        const composer = toRaw(this.props.composer);
        // Written into the message being composed: in the language of its
        // author, who writes it (the recipients' language is not known here).
        let line = _t("%(name)s: %(url)s", { name: link.name, url: link.url });
        if (link.kind === "share" && link.expire_date) {
            line += " " + _t("(expires on %s)", link.expire_date);
        }
        const text = composer.text || "";
        const start = composer.selection?.start ?? text.length;
        const end = composer.selection?.end ?? text.length;
        const before = text.slice(0, start);
        const after = text.slice(end);
        const lead = before && !before.endsWith("\n") ? "\n" : "";
        const trail = after && !after.startsWith("\n") ? "\n" : "";
        composer.text = before + lead + line + trail + after;
        this.selection.moveCursor((before + lead + line).length);
        composer.autofocus++;
        if (link.password) {
            // Jamais dans le message : le mot de passe se transmet a part.
            this.bfNcNotification.add(
                _t("Share password, to send separately: %s", link.password),
                { type: "warning", sticky: true }
            );
        }
    },
});

// ----------------------------------------------------------------------
// Chatter: « Fichiers Nextcloud liés »
// ----------------------------------------------------------------------
patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        this.bfNcLinks = useNextcloudAvailable();
        this.bfNcLinksDialog = useService("dialog");
    },

    get bfNcLinksVisible() {
        return this.bfNcLinks.available && isRecordThread(this.state.thread);
    },

    onClickBfNcLinkedFiles() {
        const thread = this.state.thread;
        if (!isRecordThread(thread)) {
            return;
        }
        this.bfNcLinksDialog.add(NcLinkedFilesDialog, { model: thread.model, resId: thread.id });
    },

    onClickBfNcFile() {
        const thread = this.state.thread;
        if (!isRecordThread(thread)) {
            return;
        }
        this.bfNcLinksDialog.add(NcFileDialog, {
            model: thread.model,
            resId: thread.id,
            // La note laissee au fil par le serveur paraît sans recharger la page.
            onDone: () => thread.fetchNewMessages?.(),
        });
    },
});
