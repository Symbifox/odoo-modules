/** @odoo-module **/

/**
 * Two things on the calendar popover that otherwise cost a trip through the
 * form: setting the meeting's status, and poking a guest who has not shown up.
 *
 * The status buttons deliberately copy the shape of core's Yes/No/Maybe
 * attendee group sitting next to them. They answer a different question — is
 * the meeting happening, rather than am I going — and putting them in a
 * different shape would suggest they are a different kind of control than they
 * are.
 */

import { patch } from "@web/core/utils/patch";
import { AttendeeCalendarCommonPopover } from "@calendar/views/attendee_calendar/common/attendee_calendar_common_popover";
import { AttendeeCalendarCommonRenderer } from "@calendar/views/attendee_calendar/common/attendee_calendar_common_renderer";

patch(AttendeeCalendarCommonPopover.prototype, {
    /**
     * The three statuses, read off the field itself.
     *
     * ⚠️ Not a hand-written list with `_t()` labels, which is what this was at
     * first and what put an untranslated "Confirmed" between "Tentative" and
     * "Annulé" in a French interface. JavaScript terms live in ONE dictionary
     * shared by every installed module, keyed by the English source string: a
     * word as ordinary as "Cancelled" is already claimed by somebody else, and
     * whoever loads last decides how it reads here.
     *
     * `props.model.fields` carries the selection Odoo has already translated
     * for this field, per field and with no collision possible. It also means
     * adding a status is a one-line change in Python.
     */
    get bfStatusChoices() {
        const field = this.props.model.fields.bf_event_status;
        return (field?.selection || []).map(([value, label]) => ({ value, label }));
    },

    get bfEventStatus() {
        return this.props.record.rawRecord.bf_event_status;
    },

    /**
     * Hidden unless the user may actually write. Core's own footer buttons key
     * on `user_can_edit`, and a button group that silently fails to save is
     * worse than no button group: the popover closes either way, so the failure
     * looks like a successful change until the page is reloaded.
     *
     * Also hidden on a recurring event. Writing the status there raises the
     * "this event / this and following / all events" question, and answering it
     * from a popover with no way to show the choice would silently pick one.
     */
    get bfDisplayStatusChoice() {
        return (
            this.isEventEditable &&
            !this.props.record.rawRecord.recurrency &&
            this.bfEventStatus !== undefined
        );
    },

    async bfChangeStatus(status) {
        const record = this.props.record;
        if (record.rawRecord.bf_event_status === status) {
            return this.props.close();
        }
        /**
         * 🔴 « Annulée » du groupe de statuts et « Annuler » du pied disaient
         * la même chose et ne faisaient pas la même chose : le premier écrivait
         * le mot, le second rendait le créneau et proposait de prévenir les
         * invités. Vu côte à côte dans la bulle, au banc navigateur, à deux
         * pouces l'un de l'autre.
         *
         * Le couplage statut/disponibilité vit maintenant dans `write()` côté
         * Python, donc les deux rendent le créneau. Ce détour-ci ajoute la
         * seconde moitié : l'avis d'annulation, qu'un simple champ ne peut pas
         * proposer.
         */
        if (status === "cancelled") {
            return this.bfOnClickCancel();
        }
        await this.orm.write(this.props.model.resModel, [record.id], {
            bf_event_status: status,
        });
        await this.props.model.load();
        this.props.close();
    },

    /**
     * Une rencontre qui n'a pas encore été annulée peut l'être d'ici.
     *
     * Masqué sur une série, pour la même raison que les boutons de statut
     * au-dessus : écrire sur une occurrence pose la question « cette
     * rencontre / celle-ci et les suivantes / toute la série », et une bulle
     * qui ne sait pas la montrer y répondrait toute seule — sur une série
     * entière, et par courriel.
     */
    get bfDisplayCancel() {
        return (
            this.isEventEditable &&
            !this.props.record.rawRecord.recurrency &&
            this.bfEventStatus !== undefined &&
            this.bfEventStatus !== "cancelled"
        );
    },

    /**
     * ⚠️ « Supprimer » ne survit que sur une rencontre DÉJÀ annulée.
     *
     * C'est tout le lot : supprimer était la seule action offerte, et elle
     * efface la rencontre sans que personne n'en soit averti — `unlink()` du
     * cœur ne prévient aucun participant, il ne fait que rafraîchir les
     * rappels. Le ménage reste possible, mais après coup : on annule d'abord,
     * ce qui prévient qui de droit et rend le créneau, et on supprime ensuite
     * si on veut vraiment que la trace disparaisse.
     *
     * Deux échappatoires, et aucune n'est un oubli :
     *
     * - `undefined` veut dire que `bf_event_status` n'est pas déclaré dans
     *   l'arch de CETTE vue calendrier, donc que la bulle ne peut rien savoir
     *   du statut. Retirer « Supprimer » sur cette foi-là laisserait une vue
     *   sans aucune action destructive, en silence.
     * - sur une série, « Annuler » n'est pas offert (voir ci-dessus) :
     *   retirer aussi « Supprimer » ne laisserait plus rien du tout.
     */
    get isEventDeletable() {
        if (!super.isEventDeletable) {
            return false;
        }
        if (this.bfEventStatus === undefined) {
            return true;
        }
        if (this.props.record.rawRecord.recurrency) {
            return true;
        }
        return this.bfEventStatus === "cancelled";
    },

    async bfOnClickCancel() {
        const action = await this.orm.call("calendar.event", "action_bf_cancel", [
            [this.props.record.id],
        ]);
        this.props.close();
        /**
         * ⚠️ `onClose` et pas un `await` : la boîte est un `target: "new"`,
         * `doAction` rend la main dès qu'elle est ouverte. Sans ce rappel, la
         * grille garde la vignette non barrée jusqu'au prochain rafraîchissement,
         * et l'annulation a l'air de n'avoir rien fait.
         */
        this.actionService.doAction(action, {
            onClose: () => this.props.model.load(),
        });
    },

    async bfOnClickPoke() {
        const action = await this.orm.call("calendar.event", "action_bf_poke", [
            [this.props.record.id],
        ]);
        this.props.close();
        this.actionService.doAction(action);
    },
});

/**
 * A cancelled meeting has to LOOK cancelled in the grid.
 *
 * Setting the status is only half the request: a cancelled event that renders
 * exactly like a confirmed one still has to be opened to be read, which is the
 * trip the whole feature exists to save.
 *
 * Done by adding a class rather than by extending the event template, because
 * the class has to reach the chip element itself — the template only fills its
 * inside — and because `eventClassNames` is the hook core provides for exactly
 * this (`o_event_hatched`, `o_past_event` are its own uses of it).
 */
patch(AttendeeCalendarCommonRenderer.prototype, {
    eventClassNames(info) {
        const classes = super.eventClassNames(info);
        const record = this.props.model.records[info.event.id];
        /**
         * Une classe par statut RÉELLEMENT posé, et rien quand il n'y en a pas.
         *
         * ⚠️ Pas de `else` qui traiterait l'absence comme une confirmation.
         * `bf_event_status` n'a jamais été rétro-rempli, délibérément (voir le
         * `create()` du modèle) : sur un calendrier existant, la plupart des
         * rencontres n'en portent aucun. Peindre l'absence en « confirmée » afficherait la
         * marque sur presque tout, donc n'informerait plus de rien — et
         * surtout, ça affirmerait une confirmation que personne n'a donnée,
         * ce que tout le reste du module refuse de faire.
         *
         * L'absence reste donc neutre. Les rencontres créées depuis naissent
         * « confirmée », donc l'écart se referme de lui-même.
         */
        const statut = record?.rawRecord?.bf_event_status;
        if (statut) {
            classes.push(`bf_event_${statut}`);
        }
        return classes;
    },
});
