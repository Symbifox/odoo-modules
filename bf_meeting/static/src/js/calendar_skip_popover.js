/** @odoo-module **/

/**
 * « Aucune préparation » sur la bulle d'une rencontre.
 *
 * Le suivi des rencontres marche par exception : tout est réputé mériter un
 * ordre du jour et un compte rendu, et on coche la dispense au cas par cas. Ce
 * cas par cas se fait aujourd'hui en ouvrant l'événement, en descendant dans le
 * formulaire et en cochant une case — pour un point d'équipe de quinze minutes
 * dont personne n'attend de document. Ici, c'est un clic depuis la grille.
 *
 * Bascule `bf_skip_agenda`, le même champ que la case du formulaire : les deux
 * commandes écrivent au même endroit, il n'y a pas deux façons d'être dispensé.
 * Les deux pastilles OdJ / CR passent alors à « non requis » et disparaissent
 * de la vignette, et la rencontre sort du tableau de bord.
 */

import { patch } from "@web/core/utils/patch";
import { AttendeeCalendarCommonPopover } from "@calendar/views/attendee_calendar/common/attendee_calendar_common_popover";

patch(AttendeeCalendarCommonPopover.prototype, {
    /**
     * ⚠️ `undefined` et non `false` : le champ n'arrive dans `rawRecord` que
     * s'il est déclaré dans l'arch de la vue calendrier. Distinguer les deux
     * évite d'afficher un bouton mort sur une vue qui ne le porte pas — et
     * c'est aussi ce qui garde ce patch inerte sur les autres vues calendrier
     * de la base, qui n'ont pas ce champ du tout.
     */
    get bfDisplaySkipAgenda() {
        return (
            this.isEventEditable &&
            this.props.record.rawRecord.bf_skip_agenda !== undefined
        );
    },

    get bfSkipAgenda() {
        return Boolean(this.props.record.rawRecord.bf_skip_agenda);
    },

    async bfToggleSkipAgenda() {
        const record = this.props.record;
        await this.orm.write(this.props.model.resModel, [record.id], {
            bf_skip_agenda: !this.bfSkipAgenda,
        });
        await this.props.model.load();
        this.props.close();
    },
});
