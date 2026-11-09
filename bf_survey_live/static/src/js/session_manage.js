/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";
import { rpc } from "@web/core/network/rpc";
import { _t } from "@web/core/l10n/translation";
import "@survey/js/survey_session_manage";
import BfLiveWordCloud from "@bf_survey_live/js/word_cloud";
import BfLiveScoreboard from "@bf_survey_live/js/scoreboard";

publicWidget.registry.SurveySessionManage.include({
    /**
     * ⚠️ Les trois crochets ci-dessous lisent les attributs de la balise, et non
     * les propriétés du widget : le cœur appelle `_setupTextAnswers` et
     * `_setupLeaderboard` DANS son propre `start`, donc avant que le nôtre ait
     * pu poser quoi que ce soit.
     */
    start() {
        return this._super(...arguments).then(() => {
            if (!this.bfKeyBound) {
                // Une seule fois par widget : le cœur réutilise la même instance
                // d'une question à l'autre (`attachTo`), et deux écoutes
                // ouvriraient puis fermeraient le tableau au même appui.
                this.bfKeyBound = true;
                this.bfKeyHandler = this._onBfKeyDown.bind(this);
                document.addEventListener("keydown", this.bfKeyHandler);
            }
        });
    },

    destroy() {
        if (this.bfKeyHandler) {
            document.removeEventListener("keydown", this.bfKeyHandler);
            this.bfKeyHandler = null;
        }
        this._bfCloseOverlay();
        this._super(...arguments);
    },

    //--------------------------------------------------------------------------
    // Les écrans
    //--------------------------------------------------------------------------

    _setupTextAnswers() {
        if (this.bfWordCloud) {
            this.bfWordCloud.setElement(null);
            this.bfWordCloud.destroy();
            delete this.bfWordCloud;
        }
        if (!this.isStartScreen && this.$el.data("bfShowsWordCloud")) {
            // 🔴 Le composant du cœur est détruit À LA MAIN, parce qu'on ne peut
            // pas lui rendre la main : son `_setupTextAnswers` détruit l'ancien
            // puis en RECRÉE un, et `show_text_answers` est vrai sur une
            // question à nuage — il s'accrocherait à la zone que le nuage vient
            // de prendre. Sans cette démolition, enchaîner une question dont on
            // écrit les réponses (texte long, nombre) puis une question marquée
            // nuage laissait son composant VIVANT, accroché à un cadre que le
            // cœur venait de détacher de la page : `_refreshResults` continuait
            // d'y écrire les réponses de la salle, où personne ne les voit.
            if (this.textAnswers) {
                this.textAnswers.setElement(null);
                this.textAnswers.destroy();
                delete this.textAnswers;
            }
            // Le nuage prend la place de la liste de réponses du cœur : la même
            // zone, le même moment, un autre écran.
            this.bfWordCloud = new BfLiveWordCloud(this, {
                surveyAccessToken: this.surveyAccessToken,
            });
            return this.bfWordCloud.attachTo(this.$(".o_bf_live_cloud"));
        }
        return this._super(...arguments);
    },

    _setupLeaderboard() {
        const parEquipe = this.$el.data("bfScoreboardMode") === "team";
        const podium = !!this.$el.data("bfPodium") && !!this.isLastQuestion;
        let pose;
        if (!parEquipe && !podium) {
            pose = this._super(...arguments);
        } else if (!this.isScoredQuestion && !this.isLastQuestion) {
            // La même condition que le cœur : le classement n'existe que sur une
            // question notée ou à la toute fin. Notre tableau ne change pas QUAND
            // l'écran apparaît, seulement CE QU'IL montre.
            pose = this._super(...arguments);
        } else {
            if (this.leaderBoard) {
                this.leaderBoard.setElement(null);
                this.leaderBoard.destroy();
                delete this.leaderBoard;
            }
            this.leaderBoard = new BfLiveScoreboard(this, {
                surveyAccessToken: this.surveyAccessToken,
                sessionResults: this.$(".o_survey_session_results"),
                final: podium,
                titre: podium ? _t("Podium") : _t("Classement par équipe"),
            });
            pose = this.leaderBoard.attachTo(this.$(".o_survey_session_leaderboard"));
        }
        // ⚠️ Un seul `_super` par appel, et dans CETTE méthode : le `_super` des
        // classes d'Odoo est la méthode de MÊME NOM du parent, posée le temps de
        // l'appel. Sorti dans une méthode à nous, il vaudrait `undefined`.
        return Promise.resolve(pose).then((valeur) => {
            this._bfRangerLaCarteAvecLeClassement();
            return valeur;
        });
    },

    /**
     * 🔴 La carte du code restait à l'écran du CLASSEMENT et sur le PODIUM —
     * l'écran qu'on photographie en fin d'atelier — parce que le cœur n'y masque
     * que `.o_survey_session_results`, et la carte vit dans l'en-tête, pas
     * dedans. Elle part donc par le contrat du classement lui-même,
     * `showLeaderboard`/`hideLeaderboard`, que notre tableau respecte déjà : une
     * seule garniture, posée sur le tableau du cœur comme sur le nôtre, et
     * aucune classe du cœur touchée.
     *
     * ⚠️ Une seule fois par tableau : `_setupLeaderboard` repasse à chaque
     * question, et deux garnitures empêcheraient le retour de la carte.
     */
    _bfRangerLaCarteAvecLeClassement() {
        const tableau = this.leaderBoard;
        if (!tableau || tableau.bfCarteRangee) {
            return;
        }
        tableau.bfCarteRangee = true;
        const montrer = tableau.showLeaderboard.bind(tableau);
        const cacher = tableau.hideLeaderboard.bind(tableau);
        tableau.showLeaderboard = (...args) => {
            this._bfCarteAuMur(false);
            return montrer(...args);
        };
        tableau.hideLeaderboard = (...args) => {
            this._bfCarteAuMur(true);
            return cacher(...args);
        };
    },

    _bfCarteAuMur(visible) {
        const carte = this.el && this.el.querySelector(".o_bf_live_join");
        if (carte) {
            carte.classList.toggle("o_bf_live_join_rangee", !visible);
        }
    },

    //--------------------------------------------------------------------------
    // L'écran d'attente : les équipes
    //--------------------------------------------------------------------------

    /**
     * Le cœur rafraîchit le compte des participants toutes les deux secondes
     * sur l'écran d'attente. La bande des équipes suit la même cadence : le
     * tirage se fait au bureau, pendant que l'écran est DÉJÀ projeté, donc une
     * bande rendue au seul chargement de la page n'aurait rien montré.
     */
    _refreshAttendeesCount() {
        const promesse = this._super(...arguments);
        this._bfRefreshTeams();
        return promesse;
    },

    async _bfRefreshTeams() {
        const hote = this.el.querySelector(".o_bf_live_teams_strip");
        if (!hote) {
            return;
        }
        let html;
        try {
            html = await rpc(`/bf_survey_live/teams/${this.surveyAccessToken}`, {});
        } catch {
            // Un appel qui échoue laisse la bande telle quelle : un écran
            // projeté ne doit pas se vider parce qu'un paquet s'est perdu.
            return;
        }
        hote.innerHTML = html || "";
    },

    //--------------------------------------------------------------------------
    // Le tableau à la demande (touche T)
    //--------------------------------------------------------------------------

    _onBfKeyDown(ev) {
        if (ev.key !== "t" && ev.key !== "T") {
            return;
        }
        const cible = ev.target;
        if (cible && ["INPUT", "TEXTAREA", "SELECT"].includes(cible.tagName)) {
            return;
        }
        ev.preventDefault();
        this._bfToggleOverlay();
    },

    async _bfToggleOverlay() {
        if (this.bfOverlay && this.bfOverlay.isConnected) {
            this._bfCloseOverlay();
            return;
        }
        this.bfOverlay = null;
        let html;
        try {
            html = await rpc(`/bf_survey_live/scoreboard/${this.surveyAccessToken}`, {
                final: false,
            });
        } catch {
            return;
        }
        if (!html) {
            return;
        }
        const voile = document.createElement("div");
        voile.className = "o_bf_live_overlay";
        const cadre = document.createElement("div");
        cadre.className = "o_bf_live_overlay_panel";
        const titre = document.createElement("h1");
        titre.className = "o_bf_live_overlay_title";
        titre.textContent =
            this.$el.data("bfScoreboardMode") === "team"
                ? _t("Les équipes")
                : _t("Le classement");
        const corps = document.createElement("div");
        corps.className = "o_bf_live_overlay_body";
        corps.innerHTML = html;
        const aide = document.createElement("p");
        aide.className = "o_bf_live_overlay_hint";
        aide.textContent = _t("T pour fermer");
        cadre.append(titre, corps, aide);
        voile.appendChild(cadre);
        // Posé DANS l'écran de la séance : une transition de question remplace
        // cet élément, et le voile part avec lui au lieu de rester au mur.
        this.el.appendChild(voile);
        this.bfOverlay = voile;
    },

    _bfCloseOverlay() {
        if (this.bfOverlay) {
            this.bfOverlay.remove();
            this.bfOverlay = null;
        }
    },
});

export default publicWidget.registry.SurveySessionManage;
