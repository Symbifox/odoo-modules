/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";
import "@survey/js/survey_form";

// Collect uploaded attachment ids and inject them into submit params under the
// question id key. Survey natively only handles known types; file_upload is ours.
publicWidget.registry.SurveyFormWidget.include({
    _prepareSubmitValues: function () {
        this._super(...arguments);
        const params = arguments[1];
        const self = this;
        this.$(".o_survey_form_file_upload").each(function () {
            const questionId = this.dataset.questionId;
            if (!questionId) return;
            const ids = Array.from(
                this.querySelectorAll(".o_survey_form_file_upload_input")
            ).map((n) => n.value);
            if (!ids.length) return;
            // _prepareSubmitAnswer mutates params in place; the return value is
            // the same reference. We ignore the return to keep params const.
            ids.forEach((v) => {
                self._prepareSubmitAnswer(params, questionId, v);
            });
        });
    },
});

publicWidget.registry.BfSurveyFileUpload = publicWidget.Widget.extend({
    selector: ".o_survey_form_file_upload",
    events: {
        "change .o_survey_form_file_upload_picker": "_onFileChange",
        "click .o_survey_form_file_upload_remove": "_onRemoveClick",
    },

    start: function () {
        this.questionId = this.el.dataset.questionId;
        this.surveyToken = this.el.dataset.surveyToken;
        this.answerToken = this.el.dataset.answerToken;
        this.multiple = this.el.dataset.multiple === "1";
        this.maxSizeMb = parseInt(this.el.dataset.maxSizeMb || "25", 10);
        this.allowedExts = (this.el.dataset.allowedExtensions || "")
            .split(",")
            .map((s) => s.trim().toLowerCase().replace(/^\./, ""))
            .filter(Boolean);
        this.errorEl = this.el.querySelector(".o_survey_form_file_upload_error");
        this.listEl = this.el.querySelector(".o_survey_form_file_upload_list");
        return this._super.apply(this, arguments);
    },

    _showError: function (msg) {
        if (!this.errorEl) return;
        this.errorEl.textContent = msg || "";
        this.errorEl.classList.toggle("d-none", !msg);
    },

    _validateLocal: function (file) {
        if (file.size > this.maxSizeMb * 1024 * 1024) {
            return `Le fichier « ${file.name} » dépasse ${this.maxSizeMb} Mo.`;
        }
        if (this.allowedExts.length) {
            const ext = file.name.includes(".")
                ? file.name.split(".").pop().toLowerCase()
                : "";
            if (!this.allowedExts.includes(ext)) {
                return `Le format « .${ext || "?"} » n'est pas accepté. Formats : ${this.allowedExts.join(", ")}.`;
            }
        }
        return null;
    },

    _onFileChange: async function (ev) {
        this._showError("");
        const input = ev.currentTarget;
        const files = Array.from(input.files || []);
        if (!files.length) return;

        if (!this.multiple) {
            // Replace existing selection: clear hidden inputs + list
            this._clearAll();
        }

        for (const file of files) {
            const localErr = this._validateLocal(file);
            if (localErr) {
                this._showError(localErr);
                continue;
            }
            try {
                const att = await this._uploadFile(file);
                if (att) {
                    this._addAttachment(att);
                }
            } catch (e) {
                this._showError(e.message || "Échec du téléversement.");
            }
        }
        // Reset input so re-selecting the same file fires change
        input.value = "";
    },

    _uploadFile: async function (file) {
        const url = `/survey/upload/${this.surveyToken}/${this.answerToken}/${this.questionId}`;
        const formData = new FormData();
        formData.append("file", file);
        const resp = await fetch(url, {
            method: "POST",
            body: formData,
            credentials: "same-origin",
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            throw new Error(data.message || `Erreur ${resp.status}`);
        }
        const atts = data.attachments || [];
        return atts[0];
    },

    _addAttachment: function (att) {
        const safeId = Number(att.id);
        if (!Number.isInteger(safeId) || safeId <= 0) return;

        const hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = this.questionId;
        hidden.value = String(safeId);
        hidden.classList.add("o_survey_form_file_upload_input");
        hidden.dataset.attachmentId = String(safeId);
        this.el.appendChild(hidden);

        if (this.listEl) {
            const li = document.createElement("li");
            li.className =
                "d-flex align-items-center justify-content-between border rounded px-2 py-1 mt-1";
            li.dataset.attachmentId = String(safeId);
            li.innerHTML = `
                <span class="text-truncate"><i class="fa fa-file-o me-2"></i>${this._escape(att.name)}</span>
                <button type="button" class="btn btn-sm btn-link text-danger o_survey_form_file_upload_remove" data-attachment-id="${safeId}">
                    <i class="fa fa-trash"></i>
                </button>
            `;
            this.listEl.appendChild(li);
        }
    },

    _onRemoveClick: async function (ev) {
        ev.preventDefault();
        const btn = ev.currentTarget;
        const attId = btn.dataset.attachmentId;
        if (!attId) return;
        try {
            const resp = await fetch(
                `/survey/upload/${this.surveyToken}/${this.answerToken}/delete/${attId}`,
                { method: "POST", credentials: "same-origin" }
            );
            if (!resp.ok) {
                console.warn("bf_survey_upload: delete returned", resp.status);
            }
        } catch (e) {
            console.warn("bf_survey_upload: delete failed", e);
        }
        this._removeAttachment(attId);
    },

    _removeAttachment: function (attId) {
        this.el.querySelectorAll(`input[data-attachment-id="${attId}"]`).forEach((n) =>
            n.remove()
        );
        this.el.querySelectorAll(`li[data-attachment-id="${attId}"]`).forEach((n) =>
            n.remove()
        );
    },

    _clearAll: function () {
        this.el
            .querySelectorAll(".o_survey_form_file_upload_input")
            .forEach((n) => n.remove());
        if (this.listEl) {
            this.listEl.innerHTML = "";
        }
    },

    _escape: function (str) {
        const d = document.createElement("div");
        d.textContent = str || "";
        return d.innerHTML;
    },
});

export default publicWidget.registry.BfSurveyFileUpload;
