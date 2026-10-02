/** @odoo-module **/
import { Component, useState, useRef, onMounted } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { user } from "@web/core/user";
import { _t } from "@web/core/l10n/translation";

export class BfNoteQuickCreateDialog extends Component {
    static nextId = 1;
    static template = "bf_bloc_notes.QuickCreateDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        resModel: { type: [String, Boolean], optional: true },
        resId: { type: [Number, Boolean], optional: true },
        onSaved: { type: Function, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        this.title = _t("New quick note");
        // Ids propres au dialogue : Alt+N peut en empiler deux, et un libellé
        // viserait sinon la case du premier.
        this.domId = `bf_note_qc_${BfNoteQuickCreateDialog.nextId++}`;
        this.state = useState({
            name: "",
            body: "",
            pinned: false,
            saving: false,
            linkedLabel: "",
            // Lien vers la fiche active : proposé coché, l'usager peut le refuser.
            linkEnabled: true,
            reminderKey: "today",
            reminderDate: this._isoToday(),
        });
        this.bodyRef = useRef("body");
        this.titleRef = useRef("title");

        this._fetchDefaultReminder();
        if (this.hasLink) {
            this._fetchLinkedLabel();
        }
        // Alt+L bascule le lien sans quitter le champ en cours de saisie.
        useHotkey("alt+l", () => this.toggleLink(), { bypassEditableProtection: true });
        onMounted(() => {
            const el = this.titleRef.el;
            if (el) {
                el.focus();
            }
        });
    }

    /** Le dialogue a été ouvert depuis une fiche : un lien est proposé. */
    get hasLink() {
        return Boolean(this.props.resModel && this.props.resId);
    }

    /** Le lien proposé est gardé : la note, le rappel et la tâche le suivent. */
    get linkActive() {
        return this.hasLink && this.state.linkEnabled;
    }

    toggleLink() {
        if (this.hasLink) {
            this.state.linkEnabled = !this.state.linkEnabled;
        }
    }

    _isoToday() {
        const d = new Date();
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, "0");
        const day = String(d.getDate()).padStart(2, "0");
        return `${y}-${m}-${day}`;
    }

    async _fetchDefaultReminder() {
        try {
            const [data] = await this.orm.read(
                "res.users",
                [user.userId],
                ["bf_note_default_reminder"],
            );
            if (data && data.bf_note_default_reminder) {
                this.state.reminderKey = data.bf_note_default_reminder;
            }
        } catch (_e) {
            /* keep default "today" */
        }
    }

    setReminder(key) {
        this.state.reminderKey = key;
        if (key === "custom" && !this.state.reminderDate) {
            this.state.reminderDate = this._isoToday();
        }
    }

    /**
     * « Type · Nom », par exemple « Tâche · Rappeler la clinique ». `name_get`
     * n'existe plus en 18 : le bandeau retombait toujours sur le nom technique.
     */
    async _fetchLinkedLabel() {
        const { resModel, resId } = this.props;
        const fallback = `${resModel} #${resId}`;
        try {
            const [models, records] = await Promise.all([
                this.orm.call("ir.model", "display_name_for", [[resModel]]),
                this.orm.read(resModel, [resId], ["display_name"]),
            ]);
            const type = models?.find((m) => m.model === resModel)?.display_name;
            const name = records?.[0]?.display_name;
            if (!name) {
                this.state.linkedLabel = fallback;
            } else {
                this.state.linkedLabel = type && type !== resModel ? `${type} · ${name}` : name;
            }
        } catch (_e) {
            this.state.linkedLabel = fallback;
        }
    }

    onKeydownBody(ev) {
        if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") {
            ev.preventDefault();
            this.onSave();
        }
    }

    onKeydownTitle(ev) {
        if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") {
            ev.preventDefault();
            this.onSave();
        }
    }

    onPasteBody(ev) {
        const items = ev.clipboardData?.items || [];
        for (const item of items) {
            if (item.kind === "file" && item.type.startsWith("image/")) {
                ev.preventDefault();
                this._uploadAndInsertImage(item.getAsFile());
                return;
            }
        }
    }

    onDropBody(ev) {
        const files = ev.dataTransfer?.files || [];
        const images = [...files].filter((f) => f.type.startsWith("image/"));
        if (images.length) {
            ev.preventDefault();
            for (const f of images) this._uploadAndInsertImage(f);
        }
    }

    onDragOverBody(ev) {
        if (ev.dataTransfer?.types?.includes("Files")) {
            ev.preventDefault();
        }
    }

    async _uploadAndInsertImage(file) {
        if (!file) return;
        const reader = new FileReader();
        reader.onload = async () => {
            const base64 = reader.result.split(",")[1];
            try {
                const attachment = await this.orm.call("ir.attachment", "create", [{
                    name: file.name || "note-image.png",
                    datas: base64,
                    res_model: "bf.note",
                    res_id: 0,
                    mimetype: file.type || "image/png",
                }]);
                const url = `/web/image/${attachment}`;
                const img = `<img src="${url}" alt="" style="max-width: 100%; height: auto;"/>`;
                this._insertHtmlAtCursor(img);
            } catch (e) {
                this.notification.add(_t("The image upload failed."), { type: "danger" });
                throw e;
            }
        };
        reader.readAsDataURL(file);
    }

    _insertHtmlAtCursor(html) {
        const el = this.bodyRef.el;
        if (!el) return;
        el.focus();
        const sel = window.getSelection();
        if (sel && sel.rangeCount > 0) {
            const range = sel.getRangeAt(0);
            range.deleteContents();
            const tmp = document.createElement("div");
            tmp.innerHTML = html;
            const frag = document.createDocumentFragment();
            let node, last;
            while ((node = tmp.firstChild)) {
                last = frag.appendChild(node);
            }
            range.insertNode(frag);
            if (last) {
                range.setStartAfter(last);
                range.collapse(true);
                sel.removeAllRanges();
                sel.addRange(range);
            }
        } else {
            el.insertAdjacentHTML("beforeend", html);
        }
    }

    async onSave() {
        if (this.state.saving) return;
        const html = this.bodyRef.el?.innerHTML?.trim() || "";
        const text = this.bodyRef.el?.innerText?.trim() || "";
        if (!text && !this.state.name.trim()) {
            this.notification.add(_t("The note is empty."), { type: "warning" });
            return;
        }
        this.state.saving = true;
        try {
            const vals = {
                name: this.state.name.trim() || false,
                body: html,
                pinned: this.state.pinned,
                reminder_key: this.state.reminderKey,
            };
            if (this.state.reminderKey === "custom") {
                vals.reminder_date = this.state.reminderDate;
            }
            if (this.linkActive) {
                vals.res_model = this.props.resModel;
                vals.res_id = this.props.resId;
            }
            const result = await this.orm.call("bf.note", "quick_create_from_context", [vals]);
            if (this.props.onSaved) this.props.onSaved(result.id);
            this.notification.add(_t("Note saved"), { type: "success" });
            this.props.close();
        } catch (e) {
            this.state.saving = false;
            throw e;
        }
    }

    onSaveAndOpen() {
        this.onSave().then(async () => {
            // close already triggered; open list
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "bf.note",
                views: [[false, "kanban"], [false, "list"], [false, "form"]],
                context: { search_default_my_notes: 1 },
            });
        });
    }

    async onCreateTask() {
        if (this.state.saving) return;
        const html = this.bodyRef.el?.innerHTML?.trim() || "";
        const text = this.bodyRef.el?.innerText?.trim() || "";
        const title = this.state.name.trim();
        if (!title && !text) {
            this.notification.add(_t("Enter a title or content to create the task."), { type: "warning" });
            return;
        }
        this.state.saving = true;
        try {
            const ctx = {
                default_name: title || text.slice(0, 80),
                default_description: html,
                default_user_ids: [user.userId],
            };
            const linkModel = this.linkActive ? this.props.resModel : false;
            const linkId = this.linkActive ? this.props.resId : false;
            if (linkModel && linkId) {
                if (linkModel === "project.task") {
                    const [task] = await this.orm.read(
                        "project.task",
                        [linkId],
                        ["project_id", "partner_id"],
                    );
                    if (task?.project_id) ctx.default_project_id = task.project_id[0];
                    if (task?.partner_id) ctx.default_partner_id = task.partner_id[0];
                    ctx.default_parent_id = linkId;
                } else if (linkModel === "project.project") {
                    ctx.default_project_id = linkId;
                    const [project] = await this.orm.read(
                        "project.project",
                        [linkId],
                        ["partner_id"],
                    );
                    if (project?.partner_id) ctx.default_partner_id = project.partner_id[0];
                } else if (linkModel === "res.partner") {
                    ctx.default_partner_id = linkId;
                }
            }
            await this.action.doAction({
                type: "ir.actions.act_window",
                name: _t("New task"),
                res_model: "project.task",
                views: [[false, "form"]],
                target: "current",
                context: ctx,
            });
            this.props.close();
        } catch (e) {
            this.state.saving = false;
            throw e;
        }
    }

    onCancel() {
        this.props.close();
    }
}
