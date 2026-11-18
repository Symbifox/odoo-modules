/** @odoo-module **/
/**
 * The elevation of a fixture, drawn in SVG from the records.
 *
 * Read only, and not for want of time: there is nothing on an elevation a mouse could
 * move. A position's place on the shelf is its order and the widths before it, so moving a
 * product is reordering a list. The drawing shows what the records say, and says where they
 * disagree with the shelf: past the end of the board, taller than the gap above, or sitting
 * on a product nobody measured.
 *
 * Nothing is computed here but the conversion from centimetres to the SVG box: the server
 * sends x, y in centimetres of the fixture, exactly as the floor plans do. No third party
 * library.
 */
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useRecordObserver } from "@web/model/relational_model/utils";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

/** The server's readable refusal travels in `data.message`. */
function messageOf(e) {
    return e.data?.message || e.message || String(e);
}

const BLUE = "#29ABE2";
const AMBER = "#D69921";
const RED = "#C0392B";

export class PlanogramElevation extends Component {
    static template = "bf_planogram.Elevation";
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null, error: null, zoom: 1, labels: true });
        // Ce que le dessin a deja lu, et si l'enregistrement etait sale la derniere fois
        // qu'on l'a regarde : les deux signaux de `suivre` ci-dessous.
        this.lu = null;
        this.empreinteLue = null;
        this.sale = false;
        if (this.props.record) {
            // L'observateur appelle `suivre` une premiere fois a `onWillStart` : c'est
            // lui qui charge le dessin, et c'est lui qui le recharge ensuite.
            useRecordObserver((record) => this.suivre(record));
        } else {
            onWillStart(() => this.load());
        }
    }

    /**
     * Ce que le dessin montre de l'enregistrement, et rien d'autre. Lire ces proprietes
     * ICI, c'est les declarer a l'effet reactif qui appelle `suivre` : il ne rappelle que
     * si l'une d'elles change.
     */
    empreinte(record) {
        const d = record.data || {};
        return [record.resId, record.dirty ? 1 : 0, d.width, d.height, d.depth,
                d.locked ? 1 : 0, d.capacity, d.linear_width, d.position_count,
                d.shelf_count, d.unmeasured_count,
                d.shelf_ids && d.shelf_ids.count].join("|");
    }

    /**
     * 🔴 le composant ne chargeait qu'a `onWillStart`. Sauver SANS quitter
     * l'onglet laissait le dessin d'AVANT -- le champ affichait 400,00 et l'elevation
     * juste dessous dessinait encore « 300.0 × 220.0 × 45.0 cm », `rendu` appele une seule
     * fois. (L'hypothese du etait fausse, mesuree dans Chrome : revenir d'un onglet
     * voisin recharge bien, le composant etant demonte entre-temps.)
     *
     * ⚠️ `onWillUpdateProps` ne suffit PAS, mesure au vrai navigateur : le
     * formulaire ne re-rend pas ses widgets de vue en cours de saisie, donc la transition
     * « sale → propre » n'etait jamais VUE et le dessin d'avant restait a l'ecran. C'est
     * `useRecordObserver` qui le voit : un effet reactif sur les proprietes que
     * `empreinte` lit, et rien d'autre. Taper dans un champ laisse l'enregistrement
     * sale, donc n'appelle rien ; sauver le rend propre, et le dessin se relit.
     */
    suivre(record) {
        if (!record) {
            return;
        }
        const empreinte = this.empreinte(record);
        const sale = Boolean(record.dirty);
        const sauve = this.sale && !sale;
        this.sale = sale;
        if (record.resId !== this.lu) {
            return this.load(record.resId);
        }
        // Propre, et quelque chose que le dessin montre a change : c'est une sauvegarde.
        // Taper dans un champ laisse l'enregistrement SALE, donc n'appelle rien.
        if (!sale && (sauve || empreinte !== this.empreinteLue)) {
            return this.load(record.resId);
        }
    }

    get resId() {
        return this.props.record ? this.props.record.resId : this.props.resId;
    }

    async load(resId) {
        // L'identifiant est passe par `suivre` : dans `onWillUpdateProps`, `this.props`
        // porte encore les ANCIENNES proprietes, et changer d'enregistrement par le
        // pagineur donne un autre objet `record`.
        const id = resId || this.resId;
        if (!id) {
            this.state.error = _t("Save first, the drawing follows.");
            return;
        }
        try {
            this.state.data = await this.orm.call("bf.planogram.fixture", "rendu", [id]);
            this.lu = id;
            if (this.props.record) {
                this.empreinteLue = this.empreinte(this.props.record);
            }
            this.state.error = null;
        } catch (e) {
            this.state.error = messageOf(e);
        }
    }

    // --- the box ----------------------------------------------------------------------
    /** The margin in centimetres of the plan, computed by the server with the box itself. */
    get margin() {
        return this.state.data.margin;
    }

    /**
     * The box the server sends, and no arithmetic of our own: it spans what is really
     * DRAWN, which is the fixture unless a shelf is wider than it or a stack rises past
     * its top. The paper reads the same string.
     */
    get viewBox() {
        return this.state.data.view_box;
    }

    /** Centimetres from the floor, turned into the SVG axis, which runs down. */
    yOf(height) {
        return this.state.data.height - height;
    }

    /** A position sits ON its shelf: its bottom is the shelf's height. */
    yOfPosition(shelf, position) {
        return this.yOf(shelf.height + position.h);
    }

    /** Text at the scale of the drawing: a three metre run needs bigger letters than a
     * one metre one, in units of the plan. Sized by the server, with the box. */
    get fontSize() {
        return this.state.data.font_size;
    }

    colorOf(position) {
        if (position.outside) {
            return RED;
        }
        if (position.too_tall || position.nominal) {
            return AMBER;
        }
        return BLUE;
    }

    fillOf(position) {
        if (position.nominal) {
            return "url(#bf-planogram-hatch)";
        }
        return position.outside ? "#FBEAE7" : "#EAF7FD";
    }

    titleOf(position) {
        const parts = [position.product];
        parts.push(_t("%s facing(s)", position.facings));
        parts.push(_t("%s unit(s) of capacity", position.capacity));
        if (position.nominal) {
            parts.push(_t("no width on the product: drawn at a nominal width"));
        }
        if (position.outside) {
            parts.push(_t("past the end of the shelf"));
        }
        if (position.too_tall) {
            parts.push(_t("taller than the gap above it"));
        }
        return parts.join(" · ");
    }

    /** A label is drawn only where it fits: a 4 cm facing holds no word, and a word drawn
     * over the next box is worse than no word. Whether it fits, and what is left of it
     * once cut, are both decided by the server: the screen used to add the ellipsis and
     * the paper did not, so the same box read differently on each. */
    fits(position) {
        return this.state.labels && position.label_fits;
    }

    shortOf(position) {
        return position.label;
    }

    zoom(step) {
        this.state.zoom = Math.min(Math.max(this.state.zoom + step, 0.4), 3);
    }

    toggleLabels() {
        this.state.labels = !this.state.labels;
    }

    openPosition(position) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "bf.planogram.position",
            res_id: position.id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("view_widgets").add("bf_planogram_elevation", {
    component: PlanogramElevation,
});
