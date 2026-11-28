/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";
import { _t } from "@web/core/l10n/translation";
import "@survey/js/survey_form";

/**
 * "One row per column" on a matrix: checked here with the same rule as the
 * server (survey.question._validate_matrix). The server saves every valid
 * question of the page before it answers with the others' errors; when the
 * survey does not let respondents go back, sending the page again would then
 * fail on the questions already saved. Catching it here first keeps the page
 * alive.
 */
publicWidget.registry.SurveyFormWidget.include({
    _validateForm: function ($form, formData) {
        // The native checks run first: they reset the errors and the red rows.
        const valid = this._super(...arguments);
        const inactiveQuestionIds = this.options.sessionInProgress
            ? []
            : this._getInactiveConditionalQuestionIds();
        const errors = {};
        $form[0]
            .querySelectorAll(".o_survey_question_matrix[data-bf-one-per-column]")
            .forEach((table) => {
                const questionWrapper = table.closest(".js_question-wrapper");
                const questionId = questionWrapper.id;
                if (inactiveQuestionIds.includes(parseInt(questionId))) {
                    return;
                }
                const rowsByColumn = {};
                table.querySelectorAll("input:checked").forEach((input) => {
                    (rowsByColumn[input.value] ||= []).push(input.dataset.rowId);
                });
                const crowded = Object.values(rowsByColumn).filter((rows) => rows.length > 1);
                if (!crowded.length) {
                    return;
                }
                errors[questionId] = _t(
                    "Choose only one row in each column: uncheck the extra rows."
                );
                for (const rowId of crowded.flat()) {
                    table
                        .querySelector(`tbody > tr[id="${rowId}"] > th`)
                        ?.classList.add("bg-danger");
                }
            });
        if (Object.keys(errors).length) {
            this._showErrors(errors);
            return false;
        }
        return valid;
    },
});
