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
    return (e && e.data && e.data.message) || (e && e.message) || _t("Erreur.");
}

/**
 * The browser in a dialog, in picker mode: a file or folder picked, then the
 * kind of link chosen, comes back to the composer as a link to insert.
 */
export class NcPickerDialog extends Component {
    static template = "bf_nextcloud_browser.NcPickerDialog";
    static components = { Dialog, NcBrowserApp };
    static props = { close: Function, onInsert: Function };

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
                    ? _t(
                          "Connectez votre Nextcloud (bouton Nextcloud de la barre du haut) pour voir l'état de ces fichiers."
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
        return link.kind === "internal" ? _t("Fichier n° %s", link.key) : _t("Partage %s", link.key);
    }

    where(link) {
        if (link.entry) {
            return link.entry.parent_rel || _t("Racine");
        }
        if (link.share && link.share.rel) {
            const parts = link.share.rel.split("/");
            parts.pop();
            return parts.join("/") || _t("Racine");
        }
        return "";
    }

    stateLabel(link) {
        if (!link.found) {
            return link.kind === "internal"
                ? _t("Introuvable : supprimé, ou pas partagé avec vous")
                : _t("Pas dans vos partages : retiré, ou créé par quelqu'un d'autre");
        }
        if (link.kind === "internal") {
            return _t("Lien interne");
        }
        const sh = link.share;
        const parts = [sh.kind];
        parts.push(
            sh.expiration
                ? _t("expire le %s", sh.expiration)
                : sh.expiry_checked
                  ? _t("sans expiration")
                  : _t("expiration non vérifiée")
        );
        if (sh.has_password) {
            parts.push(_t("mot de passe"));
        }
        if (sh.writable) {
            parts.push(_t("écriture permise"));
        }
        return parts.join(" · ");
    }

    open(link) {
        window.open(link.url, "_blank", "noopener,noreferrer");
    }

    async copy(link) {
        try {
            await navigator.clipboard.writeText(link.url);
            this.notification.add(_t("Lien copié."), { type: "success" });
        } catch {
            this.notification.add(link.url, { title: _t("Lien"), sticky: true });
        }
    }

    revoke(link) {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Retirer le partage"),
            body: _t(
                "Le lien public vers « %s » cessera de fonctionner pour tous ceux qui l'ont reçu, y compris dans les messages déjà envoyés.",
                this.label(link)
            ),
            confirmLabel: _t("Retirer"),
            confirm: async () => {
                try {
                    const res = await this.orm.call("bf.nc.browser", "linked_revoke_share", [
                        this.props.model,
                        this.props.resId,
                        link.share.id,
                    ]);
                    if (res && res.gone) {
                        this.notification.add(
                            _t("Ce partage n'existe plus, ou Nextcloud ne vous le montre pas : rien n'a été retiré."),
                            { type: "warning" }
                        );
                    } else {
                        this.notification.add(_t("Partage retiré."), { type: "success" });
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
        let line = `${link.name} : ${link.url}`;
        if (link.kind === "share" && link.expire_date) {
            line += " " + _t("(expire le %s)", link.expire_date);
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
                _t("Mot de passe du partage, à transmettre séparément : %s", link.password),
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
});
