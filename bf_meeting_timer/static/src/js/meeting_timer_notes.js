/** @odoo-module **/

/**
 * Les notes en direct, par sujet : les sujets à gauche, les notes du sujet
 * choisi à droite.
 *
 * Pourquoi : « Créer le compte rendu » verse les notes de CHAQUE SUJET dans ses
 * points, et le fil continu dans le résumé. Tapées au fil, les notes n'allaient
 * jamais aux points : ceux-ci ne recevaient que le contexte pré-rempli.
 *
 * Trois règles :
 *
 * 1. **Les notes s'enregistrent avec la fiche**, comme avant. L'éditeur est le
 *    champ HTML d'Odoo, posé sur la ligne du sujet que le formulaire a déjà
 *    chargée : Alt+S, la sauvegarde automatique et « Annuler » valent pour lui
 *    comme pour tout champ. Aucune écriture parallèle.
 * 2. **Le chronomètre choisit le sujet ; un clic à gauche en ouvre un autre sans
 *    toucher au chronomètre.** La discussion saute souvent d'un sujet à l'autre
 *    sans qu'on veuille couper la mesure.
 * 3. **Changer de sujet commet d'abord ce qui est tapé.** L'éditeur ne commet
 *    qu'à la perte du focus ; un raccourci clavier démonte l'éditeur sans la
 *    provoquer.
 * 4. **Quitter l'onglet commet aussi.** Mesuré au banc : une note tapée puis un
 *    changement d'onglet sans perte du focus était perdue, l'éditeur démonté
 *    avant d'avoir rien remis à la fiche. 🔴 Le signal urgent d'Odoo
 *    (`WILL_SAVE_URGENTLY`) ne convient pas : après sa première remise, il
 *    relit l'éditeur, déjà détruit, et plante (« Oops! »). L'éditeur des notes
 *    remet donc son contenu lui-même, d'un seul geste synchrone, au moment où il
 *    quitte l'écran : l'éditeur n'est détruit qu'après (`onWillDestroy`).
 */

import { Component, onWillStart, onWillUnmount, status, useEffect, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { _t } from "@web/core/l10n/translation";
import { magasin, entree, poserCurseur } from "@bf_meeting_timer/js/meeting_timer_store";

const MODELE = "meeting.agenda";
const GENERAL = "general";

/**
 * Le champ HTML d'Odoo, qui remet ce qui est tapé à la fiche en quittant
 * l'écran. Construit à la demande : le registre des champs est complet au
 * montage, pas forcément au chargement de ce fichier.
 */
let EditeurDesNotes = null;
function editeurDesNotes(HtmlField) {
    if (!EditeurDesNotes) {
        EditeurDesNotes = class extends HtmlField {
            setup() {
                super.setup();
                onWillUnmount(() => {
                    if (this.isDirty && this.editor && status(this) !== "destroyed") {
                        this.updateValue(this.editor.getContent());
                    }
                });
            }
        };
    }
    return EditeurDesNotes;
}

export class MeetingTimerNotes extends Component {
    static template = "bf_meeting_timer.Notes";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.racine = useRef("racine");
        this.magasin = useState(magasin);
        this.state = useState({ choisi: null });
        this.sautVu = 0;
        const html = registry.category("fields").get("html");
        this.HtmlField = editeurDesNotes(html.component);
        this.propsEditeur = html.extractProps(
            { attrs: { placeholder: _t("Notes de ce sujet, prises pendant la rencontre…") }, options: {} },
            {}
        );
        this.propsGeneral = html.extractProps(
            { attrs: { placeholder: _t("Ce qui ne va à aucun sujet : tour de table, annonces…") }, options: {} },
            {}
        );

        onWillStart(async () => {
            if (!this.resId) {
                return;
            }
            const e = entree(this.resId);
            if (!e.payload) {
                e.payload = await this.orm.call(MODELE, "timer_payload", [[this.resId]]);
            }
            this.sautVu = e.saut;
            const courant = e.payload.current_topic_id;
            const premier = e.payload.topics.length ? e.payload.topics[0].id : null;
            this.state.choisi = e.sautVers || courant || premier || GENERAL;
            // Un saut demandé avant notre montage (l'onglet vient d'être ouvert
            // pour nous) se joue une fois l'éditeur en place.
            this.sautEnAttente = Boolean(e.sautVers) && e.sautVers === this.state.choisi;
        });

        useEffect(
            (saut) => {
                const e = this.resId && this.magasin[this.resId];
                if (!e) {
                    return;
                }
                if (saut !== this.sautVu) {
                    this.sautVu = saut;
                    this.choisir(e.sautVers, { curseur: true });
                } else if (this.sautEnAttente) {
                    this.sautEnAttente = false;
                    this.placerCurseur();
                }
            },
            () => [this.resId && this.magasin[this.resId] ? this.magasin[this.resId].saut : 0]
        );
    }

    get resId() {
        return this.props.record.resId;
    }

    get payload() {
        const e = this.resId && this.magasin[this.resId];
        return e ? e.payload : null;
    }

    /** Les sujets de la course, dans l'ordre, avec la ligne que la fiche a chargée. */
    get sujets() {
        const p = this.payload;
        if (!p) {
            return [];
        }
        const lignes = this.props.record.data.topic_ids.records;
        return p.topics
            .map((t) => ({ ...t, record: lignes.find((r) => r.resId === t.id) }))
            .filter((t) => t.record);
    }

    get sujetChoisi() {
        return this.sujets.find((t) => t.id === this.state.choisi) || null;
    }

    get recordChoisi() {
        if (this.state.choisi === GENERAL) {
            return this.props.record;
        }
        const t = this.sujetChoisi;
        return t ? t.record : null;
    }

    get cleEditeur() {
        return `${this.state.choisi}`;
    }

    estCourant(sujet) {
        return Boolean(this.payload && this.payload.current_topic_id === sujet.id);
    }

    /** Commettre ce que l'éditeur ouvert a en mémoire, avant de le démonter. */
    async commettre() {
        const proms = [];
        this.props.record.model.bus.trigger("NEED_LOCAL_CHANGES", { proms });
        await Promise.all(proms);
    }

    async choisir(id, { curseur = false } = {}) {
        if (!id) {
            return;
        }
        if (id !== this.state.choisi) {
            await this.commettre();
            this.state.choisi = id;
        }
        if (curseur) {
            await this.placerCurseur();
        }
    }

    async placerCurseur() {
        let editable = null;
        for (let essai = 0; essai < 30 && !editable; essai++) {
            await new Promise((resolve) => setTimeout(resolve, 50));
            const racine = this.racine.el;
            editable =
                racine &&
                racine.querySelector(
                    `.o_bf_meeting_timer_notes_editeur[data-cle="${this.cleEditeur}"] .odoo-editor-editable`
                );
        }
        if (editable) {
            await poserCurseur(editable);
        }
    }
}

export const meetingTimerNotes = {
    component: MeetingTimerNotes,
};

registry.category("view_widgets").add("bf_meeting_timer_notes", meetingTimerNotes);
