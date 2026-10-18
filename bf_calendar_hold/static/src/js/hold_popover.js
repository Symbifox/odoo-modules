/** @odoo-module **/

/**
 * La bulle d'une plage retenue.
 *
 * « Choisir cette plage » y est, parce que c'est là qu'on la regarde : ouvrir
 * le formulaire pour cliquer un bouton est le détour que la bulle évite.
 *
 * Et ce que la bulle offre à une rencontre n'a pas de sens sur une plage :
 * les boutons de statut (une plage est tentative par nature) et « Annuler »
 * de bf_calendar_invite, qui propose de prévenir des invités qu'une plage n'a
 * pas. Pour elle, « Supprimer » revient : supprimer une plage, c'est la
 * libérer.
 */

import { patch } from "@web/core/utils/patch";
import { AttendeeCalendarCommonPopover } from "@calendar/views/attendee_calendar/common/attendee_calendar_common_popover";

patch(AttendeeCalendarCommonPopover.prototype, {
    get bfHoldFor() {
        return this.props.record.rawRecord.bf_hold_for_id;
    },

    get bfDisplayStatusChoice() {
        return this.bfHoldFor ? false : super.bfDisplayStatusChoice;
    },

    get bfDisplayCancel() {
        return this.bfHoldFor ? false : super.bfDisplayCancel;
    },

    /**
     * « Aucune préparation » de bf_meeting : une plage n'a pas d'ordre du jour
     * à préparer. Sans dépendance sur bf_meeting : si son patch passe après
     * celui-ci, le bouton reste, ce qui ne casse rien.
     */
    get bfDisplaySkipAgenda() {
        return this.bfHoldFor ? false : super.bfDisplaySkipAgenda;
    },

    get isEventDeletable() {
        if (this.bfHoldFor) {
            return this.isEventEditable && !this.isEventArchivable;
        }
        return super.isEventDeletable;
    },

    async bfChooseHold() {
        await this.orm.call("calendar.event", "action_bf_hold_choose", [
            [this.props.record.id],
        ]);
        this.props.close();
        await this.props.model.load();
    },
});
