/** @odoo-module */
/**
 * Dropping a dragged line in the survey's question list saves the survey.
 *
 * The numbers of the questions (bf_survey_numbering) are computed from the
 * saved order. Between a drop and a save the web client only refreshes the
 * lines whose own values changed, so the "#" column showed a mix of old and
 * new numbers (1.3 above 1.2, a section numbered 2 at the end). The survey
 * editor already saves the survey after every question edited in its dialog;
 * a drop now does the same, and the numbers are right as soon as the line
 * lands.
 */
import { patch } from "@web/core/utils/patch";
import { registry } from "@web/core/registry";
import { useSubEnv } from "@odoo/owl";
import { QuestionPageListRenderer } from "@survey/question_page/question_page_list_renderer";

const field = registry.category("fields").get("question_page_one2many");

patch(field.component.prototype, {
    setup() {
        super.setup(...arguments);
        useSubEnv({ bfSaveSurvey: () => this.props.record.save() });
    },
});

patch(QuestionPageListRenderer.prototype, {
    async sortDrop() {
        await super.sortDrop(...arguments);
        if (this.env.bfSaveSurvey) {
            await this.env.bfSaveSurvey();
        }
    },
});
