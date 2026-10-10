/** @odoo-module **/

/**
 * « Plus d'options » garde l'ordre du jour choisi dans la création rapide, et
 * les deux bascules « sans préparation ».
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
        const data = this.model.root.data;
        const ajouts = {};
        if (data.meeting_agenda_id) {
            ajouts.default_meeting_agenda_id = data.meeting_agenda_id[0];
        }
        // Champs absents de la vue pour qui n'a pas les Rencontres : `undefined`.
        // Des clés à nous, lues seulement par `calendar.event.default_get` : un
        // `default_bf_skip_*` suivrait le contexte vers d'autres fiches.
        if (data.bf_skip_agenda) {
            ajouts.bf_creation_rapide_sans_odj = true;
        }
        if (data.bf_skip_dashboard) {
            ajouts.bf_creation_rapide_hors_tableau = true;
        }
        if (!Object.keys(ajouts).length) {
            return super.goToFullEvent(...arguments);
        }
        const goToFullEvent = this.props.goToFullEvent;
        const props = {
            ...this.props,
            goToFullEvent: (context) => goToFullEvent({ ...context, ...ajouts }),
        };
        return super.goToFullEvent.call(Object.create(this, { props: { value: props } }));
    },
});
