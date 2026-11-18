from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

#: An avatar is handed to a person, it is not chosen: a child must never be
#: handed an adult's face. The parts below are barred for a student under
#: MAJORITE, in the draw AND in the composer.
#:
#: None as a value bars the whole slot. The open_peeps hair list was settled by
#: looking at the 48 variants one by one on 2026-11-17: `shaved1` to
#: `shaved3` are buzz cuts with a full hairline, which is an ordinary teenage
#: haircut, so they stay. `bear` is already out of every seeded draw
#: (render.SEED_EXCLUDE), and so is the `old` expression.
#:
#: Notionists is line art with no colour palette at all, and none of its 64
#: `hair` variants is bald or receding (same survey): its beard slot is the
#: only thing to bar.
PIECES_D_ADULTE = {
    "open_peeps": {
        "facialHair": None,
        "head": {"grayBun", "grayMedium", "grayShort", "noHair1", "noHair2", "noHair3"},
    },
    "notionists": {
        "beard": None,
    },
}

#: The age of majority. ⚠️ NOT `privacy_consent.is_minor_child`, which means
#: "under the age of consent" — 14 in Québec. Reading that field here would
#: leave every 14 to 17 year old with a beard.
MAJORITE = 18


class BfAvatar(models.AbstractModel):
    _inherit = "bf.avatar"

    @api.model
    def _partner_of(self, record):
        """The contact behind a record whose avatar is being drawn, if any."""
        if not record:
            return self.env["res.partner"]
        if record._name == "res.partner":
            return record
        champ = record._fields.get("partner_id")
        if champ is not None and champ.comodel_name == "res.partner":
            return record.sudo().partner_id
        return self.env["res.partner"]

    @api.model
    def _is_minor_student(self, record):
        """True for a student under 18.

        🔴 In sudo, and that is the point: `student_birthdate` is reserved to
        `base.group_user`, while a contact's avatar is computed for whoever is
        looking, portal visitors included. Without sudo the read raises on the
        very pages this module is meant to protect.
        """
        partner = self._partner_of(record)
        if not partner or len(partner) != 1:
            return False
        # sudo() keeps the prefetch set, so a list of contacts still reads in
        # one query rather than one per line.
        partner = partner.sudo()
        if not partner.is_student or not partner.student_birthdate:
            return False
        return relativedelta(fields.Date.context_today(self), partner.student_birthdate).years < MAJORITE

    @api.model
    def _seed_exclusions(self, record, style):
        exclusions = dict(super()._seed_exclusions(record, style))
        if style not in PIECES_D_ADULTE or not self._is_minor_student(record):
            return exclusions
        for slot, variants in PIECES_D_ADULTE[style].items():
            deja = exclusions.get(slot, ())
            if deja is None or variants is None:
                exclusions[slot] = None
            else:
                exclusions[slot] = set(deja) | set(variants)
        return exclusions
