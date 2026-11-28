/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";
import { _t } from "@web/core/l10n/translation";
import "@survey/js/survey_form";

// The upload box of a "File upload" question. Everything is delegated from
// the survey form itself: the form swaps the HTML of each page in place, and
// a widget started on its own selector would not be started on the next page.
// Loaded in survey.survey_assets, where @survey/js/survey_form lives (in
// web.assets_frontend the import is missing, and this file never ran).

function box(el) {
    return el.closest(".o_survey_form_file_upload");
}

function settings(wrapper) {
    const d = wrapper.dataset;
    return {
        questionId: d.questionId,
        surveyToken: d.surveyToken,
        answerToken: d.answerToken,
        multiple: d.multiple === "1",
        maxSizeMb: parseInt(d.maxSizeMb || "25", 10),
        allowedExts: (d.allowedExtensions || "")
            .split(",")
            .map((s) => s.trim().toLowerCase().replace(/^\./, ""))
            .filter(Boolean),
    };
}

function showError(wrapper, msg) {
    const errorEl = wrapper.querySelector(".o_survey_form_file_upload_error");
    if (!errorEl) return;
    errorEl.textContent = msg || "";
    errorEl.classList.toggle("d-none", !msg);
}

function escapeHtml(str) {
    const d = document.createElement("div");
    d.textContent = str || "";
    return d.innerHTML;
}

publicWidget.registry.SurveyFormWidget.include({
    events: Object.assign({}, publicWidget.registry.SurveyFormWidget.prototype.events, {
        "change .o_survey_form_file_upload_picker": "_onBfUploadChange",
        "click .o_survey_form_file_upload_remove": "_onBfUploadRemove",
    }),

    // Collect the uploaded attachment ids under the question id: the survey
    // only knows how to send its own question types.
    _prepareSubmitValues: function (formData, params) {
        this._super(...arguments);
        this.el.querySelectorAll(".o_survey_form_file_upload").forEach((wrapper) => {
            const questionId = wrapper.dataset.questionId;
            if (!questionId) return;
            const ids = Array.from(
                wrapper.querySelectorAll(".o_survey_form_file_upload_input")
            ).map((n) => n.value);
            if (!ids.length) return;
            ids.forEach((v) => {
                this._prepareSubmitAnswer(params, questionId, v);
            });
        });
    },

    _bfValidateLocal: function (file, conf) {
        if (file.size > conf.maxSizeMb * 1024 * 1024) {
            return _t('The file "%(name)s" exceeds %(max)s MB.', {
                name: file.name,
                max: conf.maxSizeMb,
            });
        }
        if (conf.allowedExts.length) {
            const ext = file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
            if (!conf.allowedExts.includes(ext)) {
                return _t('The ".%(ext)s" format is not accepted. Formats: %(formats)s.', {
                    ext: ext || "?",
                    formats: conf.allowedExts.join(", "),
                });
            }
        }
        return null;
    },

    _onBfUploadChange: async function (ev) {
        const input = ev.currentTarget;
        const wrapper = box(input);
        const conf = settings(wrapper);
        showError(wrapper, "");
        const files = Array.from(input.files || []);
        if (!files.length) return;
        if (!conf.multiple) {
            // One file only: the new one replaces the current selection.
            this._bfClearAll(wrapper);
        }
        for (const file of files) {
            const localErr = this._bfValidateLocal(file, conf);
            if (localErr) {
                showError(wrapper, localErr);
                continue;
            }
            try {
                const att = await this._bfUploadFile(file, conf);
                if (att) {
                    this._bfAddAttachment(wrapper, conf, att);
                }
            } catch (e) {
                showError(wrapper, e.message || _t("Upload failed."));
            }
        }
        // Reset the picker so that choosing the same file again fires change.
        input.value = "";
    },

    _bfUploadFile: async function (file, conf) {
        const url = `/survey/upload/${conf.surveyToken}/${conf.answerToken}/${conf.questionId}`;
        const formData = new FormData();
        formData.append("file", file);
        const resp = await fetch(url, { method: "POST", body: formData, credentials: "same-origin" });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            throw new Error(data.message || _t("Error %s", resp.status));
        }
        return (data.attachments || [])[0];
    },

    _bfAddAttachment: function (wrapper, conf, att) {
        const safeId = Number(att.id);
        if (!Number.isInteger(safeId) || safeId <= 0) return;

        const hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = conf.questionId;
        hidden.value = String(safeId);
        hidden.classList.add("o_survey_form_file_upload_input");
        wrapper.appendChild(hidden);

        const listEl = wrapper.querySelector(".o_survey_form_file_upload_list");
        if (listEl) {
            const li = document.createElement("li");
            li.className =
                "d-flex align-items-center justify-content-between border rounded px-2 py-1 mt-1";
            li.dataset.attachmentId = String(safeId);
            li.innerHTML = `
                <span class="text-truncate"><i class="fa fa-file-o me-2"></i>${escapeHtml(att.name)}</span>
                <button type="button" class="btn btn-sm btn-link text-danger o_survey_form_file_upload_remove" data-attachment-id="${safeId}">
                    <i class="fa fa-trash"></i>
                </button>
            `;
            listEl.appendChild(li);
        }
    },

    _onBfUploadRemove: async function (ev) {
        ev.preventDefault();
        const btn = ev.currentTarget;
        const wrapper = box(btn);
        const conf = settings(wrapper);
        const attId = btn.dataset.attachmentId;
        if (!attId) return;
        try {
            const resp = await fetch(
                `/survey/upload/${conf.surveyToken}/${conf.answerToken}/delete/${attId}`,
                { method: "POST", credentials: "same-origin" }
            );
            if (!resp.ok) {
                console.warn("bf_survey_upload: delete returned", resp.status);
            }
        } catch (e) {
            console.warn("bf_survey_upload: delete failed", e);
        }
        // By value: the inputs of files saved earlier are rendered by the
        // server without a data-attachment-id.
        wrapper
            .querySelectorAll(".o_survey_form_file_upload_input")
            .forEach((n) => n.value === String(attId) && n.remove());
        wrapper
            .querySelectorAll(`li[data-attachment-id="${attId}"]`)
            .forEach((n) => n.remove());
    },

    _bfClearAll: function (wrapper) {
        wrapper.querySelectorAll(".o_survey_form_file_upload_input").forEach((n) => n.remove());
        const listEl = wrapper.querySelector(".o_survey_form_file_upload_list");
        if (listEl) {
            listEl.innerHTML = "";
        }
    },
});
