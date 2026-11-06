# -*- coding: utf-8 -*-
"""The place of a meeting, taken from the address book.

Odoo neither fills nor suggests the place of a meeting. The address of the
company being visited was copied from the address book into the calendar
entry by hand, one customer at a time, and the confirmation ended up going
out through another calendar because the address was not in the Odoo
invitation. So: when a customer joins a meeting, their address becomes the
place, on one line, and the picker lists the other addresses on file for the
same company — the plant that is not the head office.

Three rules hold the whole thing together:

* **Nothing is imposed.** We only ever fill in a place that is EMPTY, and we
  only ever clear the place we put there ourselves, when the contact who
  supplied it is no longer invited. A place that was typed, picked from the
  list, or carried by a booking type is never replaced.
* **Nothing is invented.** Every address comes from the address book. No
  address is guessed from an email domain and none is fetched from a website.
  That was asked for by name, and it is a rule, not a side effect.
* **Nothing happens to an online meeting.** A meeting that carries a video
  call link is not somewhere you drive to — and one that becomes online later
  gives the address back.
"""

from odoo import Command, api, fields, models


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    bf_address_partner_id = fields.Many2one(
        "res.partner",
        string="Place from the address book",
        ondelete="set null",
        # Copied on purpose, like `location` itself: an occurrence of a
        # recurring visit, or a duplicated meeting, is born with the place
        # already written. Without the contact beside it, the place would be
        # one nobody could trace and nobody would ever clean up.
        copy=True,
        help="The contact whose address is used as the place of this meeting. "
             "Pick another one to use a different address on file — the head "
             "office, or one of the other plants. It is also how we know the "
             "place came from the address book: clear the place and we stop "
             "touching it.",
    )
    bf_address_candidate_ids = fields.Many2many(
        "res.partner",
        string="Addresses on file",
        compute="_compute_bf_address_candidate_ids",
        help="The addresses the guests of this meeting have on file: each "
             "guest, their company, and the other addresses filed under that "
             "company. What the picker offers.",
    )

    # ------------------------------------------------------------------ read

    @api.depends("partner_ids", "bf_address_partner_id")
    def _compute_bf_address_candidate_ids(self):
        for event in self:
            candidats = event.partner_ids._bf_address_candidats()
            source = event.bf_address_partner_id
            # The picker shows each contact WITH its address, which is what it
            # is for. So a contact a record rule keeps from this reader does
            # not go in it, not even the one already named on the meeting.
            if source and source.has_access("read"):
                candidats |= source
            event.bf_address_candidate_ids = candidats

    def _bf_address_est_le_notre(self):
        """Is the place, word for word, the address of the contact beside it?

        That is all « ours » means. It is deliberately not « is this still the
        address we would pick »: a guest arriving or leaving changes who we
        would pick, and a place we wrote would suddenly stop being ours —
        frozen in place, still naming a company nobody invited.
        """
        self.ensure_one()
        source = self.bf_address_partner_id.sudo().exists()
        return bool(source) and source.bf_address_correspond(self.location)

    def _bf_address_rafraichir(self):
        """Keep the place in step with the guests, without ever deciding.

        Two moves, in this order, and neither of them touches a place that
        somebody wrote:

        1. the place we filled in leaves with the contact who supplied it —
           an address left behind by a guest who is no longer invited is a
           place nobody checked, and it would still go out in the invitation,
           to the other customer now on the meeting;
        2. an EMPTY place, with no contact named beside it, gets filled in.
           Both conditions matter: an empty place next to a contact means
           somebody cleared it on purpose, and filling it back in would undo
           their decision.
        """
        self.ensure_one()
        source = self.bf_address_partner_id
        if source:
            # ⚠️ In `sudo` for this test only: it decides whether to CLEAR,
            # never what to offer. A record rule hiding the company from this
            # particular reader must not make the address look abandoned.
            # ⚠️ `active_test=False` : mesuré le 2026-11-03, archiver un
            # contact le fait DISPARAÎTRE de `partner_ids` (la lecture d'un
            # many2many filtre les inactifs). Sans ça, un ménage dans le
            # carnet — adhésion finie, doublon fusionné — effaçait le lieu
            # d'une visite encore au calendrier, alors que personne n'avait
            # retiré personne.
            invites = self.with_context(active_test=False).partner_ids.sudo()
            encore_la = source in invites._bf_address_candidats(archives=True)
            if not encore_la and self._bf_address_est_le_notre():
                self.location = False
                self.bf_address_partner_id = False
                source = self.env["res.partner"]
        if self.location or source:
            return
        contact, ligne = self.partner_ids._bf_address_candidats()._bf_address_choix_doffice()
        if ligne:
            self.location = ligne
            self.bf_address_partner_id = contact

    # -------------------------------------------------------------- on screen

    @api.onchange("partner_ids")
    def _onchange_bf_address_partner_ids(self):
        for event in self:
            if event.videocall_location:
                continue
            event._bf_address_rafraichir()

    @api.onchange("bf_address_partner_id")
    def _onchange_bf_address_partner_id(self):
        """Use the address that was picked.

        The one case the whole picker exists for: the invitation says the head
        office and the visit is at plant 3. Having picked, nobody wants the
        next guest added to the meeting to send them back to the head office —
        and nobody does, because we only ever fill in an empty place.

        Clearing the picker leaves the place alone: it is already written in
        invitations people have read, and emptying it would be a change nobody
        asked for.
        """
        for event in self:
            if not event.bf_address_partner_id:
                continue
            ligne = event.bf_address_partner_id.bf_address_one_line()
            if ligne:
                event.location = ligne

    # ----------------------------------------------------------- on creation

    @api.model_create_multi
    def create(self, vals_list):
        """Fill the place in before the record exists.

        It has to happen here, not only in the on-screen handler: a meeting
        born from a booking link carries its guests in `vals` and never passes
        through a form (a wave of visits, one booking link per company, the
        address already in it), and a meeting opened from a contact carries
        its guest in the context defaults, which an on-change never sees.
        """
        if not self.env.context.get("bf_address_skip"):
            # Les défauts ne dépendent que du contexte, le même pour tout le
            # lot : une lecture, pas une par fiche (un import ICS en crée des
            # centaines d'un coup).
            defauts = self.default_get(
                ["location", "videocall_location", "partner_ids"])
            internes = None
            for vals in vals_list:
                if internes is None and self._bf_address_a_remplir(vals, defauts):
                    internes = self.env["res.partner"]._bf_address_partenaires_internes()
                self._bf_address_remplir_vals(vals, defauts, internes)
        return super().create(vals_list)

    @api.model
    def _bf_address_a_remplir(self, vals, defauts):
        """Is this one set of values a meeting whose place we may fill in?

        Three ways of saying « leave the place alone », and they are not the
        same thing:

        * a place or a video call already in the values, or about to arrive
          through a default of the context (`super().create()` injects those
          long after this runs, so writing ours would silently cancel one);
        * a place given as an EMPTY STRING. That is a caller stating there is
          no place — what Nextcloud's importer writes for an event read from
          someone's phone, which is a reflection of another calendar, not a
          meeting we are composing. A place given as `False` is the other
          case: that is Odoo's booking module passing on a booking type with
          no fixed place, and filling that one in is the whole point.
        * an event mirrored from an external calendar (`x_sync_source`), or a
          caller that asked to be left alone (`bf_address_skip` in context).
        """
        if vals.get("location") or vals.get("videocall_location"):
            return False
        if vals.get("location") == "":
            return False
        if vals.get("x_sync_source") not in (None, False, "odoo"):
            return False
        if defauts.get("location") or defauts.get("videocall_location"):
            return False
        return True

    @api.model
    def _bf_address_remplir_vals(self, vals, defauts, internes=None):
        """Put the place, and where it came from, into one set of values."""
        if not self._bf_address_a_remplir(vals, defauts):
            return
        if vals.get("bf_address_partner_id"):
            contact = self.env["res.partner"].browse(vals["bf_address_partner_id"]).exists()
            ligne = contact.bf_address_one_line() if contact else ""
            if ligne:
                vals["location"] = ligne
            return
        commandes = (vals if "partner_ids" in vals else defauts).get("partner_ids")
        partenaires = self._bf_address_partenaires_des_commandes(commandes)
        if not partenaires:
            return
        contact, ligne = partenaires._bf_address_candidats(internes)._bf_address_choix_doffice()
        if not ligne:
            return
        vals["location"] = ligne
        vals["bf_address_partner_id"] = contact.id

    @api.model
    def _bf_address_partenaires_des_commandes(self, commandes):
        """The guests a set of x2many commands would leave on the meeting."""
        ids = []
        for commande in commandes or []:
            if isinstance(commande, int):
                ids.append(commande)
                continue
            code = commande[0]
            if code == Command.SET:
                ids = list(commande[2] or [])
            elif code == Command.LINK:
                ids.append(commande[1])
            elif code in (Command.UNLINK, Command.DELETE):
                ids = [i for i in ids if i != commande[1]]
            elif code == Command.CLEAR:
                ids = []
        vus = list(dict.fromkeys(ids))
        return self.env["res.partner"].browse(vus).exists()

    # -------------------------------------------------------------- on write

    def write(self, vals):
        """Two writes nobody asked for, and what we do about them.

        **A booking syncing itself.** `resource_booking._sync_meeting` rebuilds
        the whole set of values of its meeting on EVERY write to the booking —
        answers, guests, assignment — and always pushes `location`, read from
        the booking type. A type with no fixed place pushes an empty one. That
        is exactly the booking-link case: the address we put on the meeting
        when the link created it would be wiped a few seconds later, inside
        the same web request, with nothing in sight to say why. We let the write
        through and put our place back, and only then: the context key the
        booking itself sets (`syncing_booking_ids`) is what tells us this is a
        machine repeating itself, not someone clearing a field.

        **A meeting becoming online.** Click « Odoo meeting » on a meeting that
        has a street, and it keeps the street: a place nobody will drive to,
        printed on everything that reads `location`. If the place is ours, it
        goes.

        ⚠️ « Becoming » is read on the record, before and after, never on
        `vals`: the same booking sync pushes the video call link it already
        had, unchanged, on every write. Judged on `vals`, a hybrid visit —
        a link for the colleague at a distance, an address for the people
        driving there — lost the address somebody had picked by hand, in the
        same requests where the first branch had just put it back.
        """
        if self.env.context.get("bf_address_skip"):
            return super().write(vals)
        suit_la_serie = vals.get("recurrence_update") in ("all_events", "future_events")
        cibles = self | (self.recurrence_id.calendar_event_ids if suit_la_serie
                         else self.browse())
        avant_visio = ({event.id: bool(event.videocall_location) for event in cibles}
                       if "videocall_location" in vals else {})
        remettre = {}
        if (self.env.context.get("syncing_booking_ids")
                and "location" in vals and not vals["location"]):
            remettre = {
                event.id: event.location for event in self
                if event.location and event._bf_address_est_le_notre()
            }
        res = super().write(vals)
        for event in cibles.exists():
            if avant_visio.get(event.id) is False and event.videocall_location:
                remettre.pop(event.id, None)
                if event.location and event._bf_address_est_le_notre():
                    event.with_context(bf_address_skip=True).write(
                        {"location": False, "bf_address_partner_id": False})
            elif event.id in remettre and not event.location:
                event.with_context(bf_address_skip=True).write(
                    {"location": remettre[event.id]})
        return res
