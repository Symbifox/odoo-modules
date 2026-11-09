/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";
import { rpc } from "@web/core/network/rpc";

// Le tableau prend la place du classement du cœur dans son propre cadre, et
// respecte son contrat : `showLeaderboard` et `hideLeaderboard`. C'est ce qui
// permet de ne RIEN changer à la machine d'écrans de la séance : elle croit
// montrer son classement, et c'est le nôtre qui s'affiche.
publicWidget.registry.BfLiveScoreboard = publicWidget.Widget.extend({
    init(parent, options) {
        this._super(...arguments);
        this.surveyAccessToken = options.surveyAccessToken;
        this.$sessionResults = options.sessionResults;
        this.final = !!options.final;
        this.titre = options.titre;
    },

    //--------------------------------------------------------------------------
    // Public
    //--------------------------------------------------------------------------

    showLeaderboard(fadeOut) {
        let resoudre;
        let effacement;
        if (fadeOut) {
            effacement = new Promise((resolve) => {
                resoudre = resolve;
            });
            this.$sessionResults.fadeOut(400, () => resoudre());
        } else {
            effacement = Promise.resolve();
            this.$sessionResults.hide();
            this.$(".o_survey_session_leaderboard_container").empty();
        }

        const tableau = rpc(`/bf_survey_live/scoreboard/${this.surveyAccessToken}`, {
            final: this.final,
        });

        return Promise.all([effacement, tableau]).then(([, html]) => {
            this.$(".o_survey_session_leaderboard_container").empty().append($(html));
            if (this.titre) {
                // Le titre vit dans notre propre cadre, et le cœur vient d'y
                // écrire « Classement final » : on repasse derrière lui.
                this.$(".o_survey_session_leaderboard_title").text(this.titre);
            }
            this.$el.fadeIn(400);
        });
    },

    hideLeaderboard() {
        this.$el.fadeOut(400, () => {
            this.$(".o_survey_session_leaderboard_container").empty();
            this.$sessionResults.fadeIn(400);
        });
    },
});

export default publicWidget.registry.BfLiveScoreboard;
