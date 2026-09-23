/** @odoo-module **/

/**
 * Les notes en direct, par sujet : les sujets à gauche, les notes du sujet
 * choisi au milieu, et ce que l'ordre du jour en dit à droite.
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

import { Component, onMounted, onWillStart, onWillUnmount, status, useEffect, useRef, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";
import { _t } from "@web/core/l10n/translation";
import { magasin, entree, poserCurseur } from "@bf_meeting_timer/js/meeting_timer_store";

const MODELE = "meeting.agenda";
const GENERAL = "general";

/**
 * Les volets. On tape des notes en lisant le détail du sujet : sans lui,
 * l'écran ne montrait que le titre, et le détail (rempli sur presque tous les
 * sujets, quelques lignes le plus souvent) restait dans un autre onglet. La colonne du milieu est l'éditeur : elle prend ce qui reste et ne
 * descend jamais sous `MIN.editeur`. Avec le fil de discussion ouvert, le
 * formulaire est étroit : quand même les minimums ne tiennent pas, le détail
 * passe au-dessus de l'éditeur plutôt que de l'écraser.
 */
const DEFAUT = { sujets: 256, detail: 320 };
const MIN = { sujets: 160, editeur: 320, detail: 200 };
const POIGNEE = 12;
const PAS_CLAVIER = 24;
const CLE_VOLETS = "bf_meeting_timer.notes.volets";
// Au-delà, le détail empilé se replie à quelques lignes.
const DETAIL_LONG = 300;

/** Ce qu'un champ HTML dit en texte, pour savoir s'il est vide ou long. */
export function texteDe(html) {
    return String(html || "")
        .replace(/<[^>]*>/g, " ")
        .replace(/&nbsp;|&#160;/g, " ")
        .replace(/\s+/g, " ")
        .trim();
}

/**
 * Les largeurs qui tiennent dans `largeur`. On rogne d'abord le détail, puis
 * les sujets, jusqu'à leur minimum ; si l'éditeur n'a toujours pas le sien, on
 * empile (`null`).
 */
export function largeursQuiTiennent(largeur, voulu, replie) {
    const poignees = replie ? POIGNEE : 2 * POIGNEE;
    const dispo = largeur - poignees - MIN.editeur;
    let sujets = Math.max(MIN.sujets, voulu.sujets);
    let detail = replie ? 0 : Math.max(MIN.detail, voulu.detail);
    let trop = sujets + detail - dispo;
    if (trop > 0 && !replie) {
        const pris = Math.min(trop, detail - MIN.detail);
        detail -= pris;
        trop -= pris;
    }
    if (trop > 0) {
        const pris = Math.min(trop, sujets - MIN.sujets);
        sujets -= pris;
        trop -= pris;
    }
    return trop > 0 ? null : { sujets, detail };
}

function lireVolets() {
    try {
        const v = JSON.parse(browser.localStorage.getItem(CLE_VOLETS) || "null");
        if (v && typeof v === "object") {
            return {
                sujets: Number.isFinite(v.sujets) ? v.sujets : DEFAUT.sujets,
                detail: Number.isFinite(v.detail) ? v.detail : DEFAUT.detail,
                replie: Boolean(v.replie),
            };
        }
    } catch {
        // Stockage refusé ou illisible : les largeurs par défaut suffisent.
    }
    return { ...DEFAUT, replie: false };
}

function ecrireVolets(v) {
    try {
        browser.localStorage.setItem(CLE_VOLETS, JSON.stringify(v));
    } catch {
        // Idem : une préférence perdue n'empêche pas de prendre des notes.
    }
}

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
        this.state = useState({
            choisi: null,
            volets: lireVolets(),
            largeur: 0,
            detailDeplie: false,
        });
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

        // La largeur qui compte est celle du formulaire, pas de la fenêtre : le
        // fil de discussion ouvert la réduit d'un tiers.
        onMounted(() => {
            if (!this.racine.el) {
                return;
            }
            this.state.largeur = this.racine.el.clientWidth;
            this.observateur = new ResizeObserver((entrees) => {
                this.state.largeur = entrees[0].contentRect.width;
            });
            this.observateur.observe(this.racine.el);
        });
        onWillUnmount(() => {
            if (this.observateur) {
                this.observateur.disconnect();
            }
            this.arreterGlisse();
        });

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

    // ---- le détail ----------------------------------------------------------

    /** Ce que l'ordre du jour dit du sujet choisi, ou de la rencontre entière. */
    get detail() {
        if (this.state.choisi === GENERAL) {
            const d = this.props.record.data;
            const objectifs = (d.objectives || "").trim();
            const blocs = [
                objectifs && { cle: "objectifs", titre: _t("Objectifs"), texte: objectifs },
                texteDe(d.context_html) && { cle: "contexte", titre: _t("Contexte"), html: d.context_html },
                texteDe(d.preparation_html) && { cle: "preparation", titre: _t("Préparation"), html: d.preparation_html },
            ].filter(Boolean);
            const longueur = objectifs.length + texteDe(d.context_html).length + texteDe(d.preparation_html).length;
            return {
                blocs,
                vide: _t("L'ordre du jour n'a ni objectifs, ni contexte, ni préparation."),
                long: longueur > DETAIL_LONG,
            };
        }
        const t = this.sujetChoisi;
        const html = t && t.record.data.description;
        const texte = texteDe(html);
        return {
            blocs: texte ? [{ cle: "description", html }] : [],
            vide: _t("L'ordre du jour ne dit rien de plus sur ce sujet."),
            long: texte.length > DETAIL_LONG,
        };
    }

    /** Les largeurs affichées, ou `null` quand le détail passe au-dessus de l'éditeur. */
    get largeurs() {
        if (!this.state.largeur) {
            return null;
        }
        return largeursQuiTiennent(this.state.largeur, this.state.volets, this.state.volets.replie);
    }

    get empile() {
        return !this.largeurs;
    }

    styleSujets() {
        const l = this.largeurs;
        return l ? `flex: 0 0 ${l.sujets}px; width: ${l.sujets}px;` : "";
    }

    styleDetail() {
        const l = this.largeurs;
        return l ? `flex: 0 0 ${l.detail}px; width: ${l.detail}px;` : "";
    }

    basculerDetail() {
        this.state.volets.replie = !this.state.volets.replie;
        ecrireVolets({ ...this.state.volets });
    }

    basculerDeplie() {
        this.state.detailDeplie = !this.state.detailDeplie;
    }

    // ---- les poignées -------------------------------------------------------

    /**
     * Le volet de gauche grandit quand on tire vers la droite ; celui de
     * droite, quand on tire vers la gauche. On part de la largeur AFFICHÉE,
     * pas de la voulue : un volet rogné par un écran étroit ne doit pas sauter
     * au premier mouvement.
     */
    commencerGlisse(ev, volet) {
        if (ev.button !== 0 || !this.largeurs) {
            return;
        }
        ev.preventDefault();
        const depart = ev.clientX;
        const base = this.largeurs[volet];
        const signe = volet === "sujets" ? 1 : -1;
        this.arreterGlisse();
        this.glisse = {
            bouger: (e) => this.poser(volet, base + signe * (e.clientX - depart), false),
            lacher: () => this.arreterGlisse(true),
        };
        document.body.classList.add("o_bf_meeting_timer_glisse");
        window.addEventListener("pointermove", this.glisse.bouger);
        window.addEventListener("pointerup", this.glisse.lacher);
        window.addEventListener("pointercancel", this.glisse.lacher);
    }

    arreterGlisse(retenir = false) {
        if (!this.glisse) {
            return;
        }
        window.removeEventListener("pointermove", this.glisse.bouger);
        window.removeEventListener("pointerup", this.glisse.lacher);
        window.removeEventListener("pointercancel", this.glisse.lacher);
        document.body.classList.remove("o_bf_meeting_timer_glisse");
        this.glisse = null;
        if (retenir) {
            ecrireVolets({ ...this.state.volets });
        }
    }

    /** Poser une largeur, bornée pour que l'éditeur garde son minimum. */
    poser(volet, voulu, retenir = true) {
        const l = this.largeurs;
        if (!l) {
            return;
        }
        const autre = volet === "sujets" ? "detail" : "sujets";
        const poignees = this.state.volets.replie ? POIGNEE : 2 * POIGNEE;
        const max = this.state.largeur - poignees - MIN.editeur - l[autre];
        const v = Math.round(Math.min(Math.max(voulu, MIN[volet]), Math.max(max, MIN[volet])));
        // L'autre volet garde ce qu'il affiche : sinon, rogné par l'écran, il
        // reprendrait sa largeur voulue au détriment de celui qu'on tire.
        this.state.volets[autre] = l[autre];
        this.state.volets[volet] = v;
        if (retenir) {
            ecrireVolets({ ...this.state.volets });
        }
    }

    toucheGlisse(ev, volet) {
        const l = this.largeurs;
        if (!l) {
            return;
        }
        const signe = volet === "sujets" ? 1 : -1;
        if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") {
            ev.preventDefault();
            const sens = ev.key === "ArrowRight" ? 1 : -1;
            this.poser(volet, l[volet] + signe * sens * PAS_CLAVIER);
        } else if (ev.key === "Home" || ev.key === "End") {
            ev.preventDefault();
            this.poser(volet, ev.key === "Home" ? MIN[volet] : DEFAUT[volet]);
        }
    }

    /** Double-clic sur une poignée : le volet revient à sa largeur par défaut. */
    reinitialiser(volet) {
        this.poser(volet, DEFAUT[volet]);
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
