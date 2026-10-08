/** @odoo-module **/

/**
 * « Plus d'options » garde l'ordre du jour choisi dans la création rapide.
 *
 * Le cœur ne passe au formulaire complet qu'une liste FIXE de champs
 * (`QUICK_CREATE_CALENDAR_EVENT_FIELDS`, non exportée) : titre, dates,
 * participants, visio, description. Le choix d'un OdJ y remplit bien ces
 * champs-là, mais le lien lui-même restait en route : la rencontre
 * enregistrée depuis le formulaire complet n'avait plus d'ordre du jour.
 *
 * On ne recopie pas la liste du cœur. On appelle sa méthode telle quelle, en
 * lui donnant un `props.goToFullEvent` qui ajoute le lien au contexte.
 */

import { patch } from "@web/core/utils/patch";
import { CalendarQuickCreateFormController } from "@calendar/views/calendar_form/calendar_quick_create";

patch(CalendarQuickCreateFormController.prototype, {
    goToFullEvent() {
        const agenda = this.model.root.data.meeting_agenda_id;
        if (!agenda) {
            return super.goToFullEvent(...arguments);
        }
        const goToFullEvent = this.props.goToFullEvent;
        const props = {
            ...this.props,
            goToFullEvent: (context) =>
                goToFullEvent({ ...context, default_meeting_agenda_id: agenda[0] }),
        };
        return super.goToFullEvent.call(Object.create(this, { props: { value: props } }));
    },
});
