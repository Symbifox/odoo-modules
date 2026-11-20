"""Le colis qui vient d'un casier : d'où il vient, et qui l'a retiré.

Un seul champ de plus, et il porte une garde : la référence de la réservation
chez Expedibox. C'est elle qui fait qu'un avis rejoué ne consigne pas deux fois
le même colis, et qu'un retrait retrouve le bon colis.
"""
from odoo import _, fields, models


class BfPropertyParcel(models.Model):
    _inherit = "bf.property.parcel"

    expedibox_reservation_ref = fields.Char(
        string="Réservation Expedibox",
        copy=False,
        readonly=True,
        index=True,
        help="La réservation du casier d'où ce colis vient. Un colis consigné "
             "à la main n'en a pas.",
    )

    _sql_constraints = [
        # Une réservation, un colis. La contrainte porte sur la société : deux
        # organisations d'une même société ne peuvent pas consigner le même
        # dépôt, et deux sociétés n'ont jamais le même casier.
        # ⚠️ Un colis saisi à la main a ce champ vide, et Postgres laisse
        # passer autant de NULL qu'on veut : la contrainte ne gêne personne.
        ("uniq_expedibox_reservation",
         "unique(company_id, expedibox_reservation_ref)",
         "Un colis est déjà consigné pour cette réservation Expedibox."),
    ]

    def _bf_expedibox_collected(self, when):
        """Consigne le retrait constaté par le casier. Rend ce qui s'est passé.

        🔴 Pas ``action_collected`` : lui date la remise de MAINTENANT, alors
        que le casier sait l'heure exacte du retrait, et le passage planifié
        peut tourner cinq minutes plus tard. Le journal doit dire l'heure du
        geste, pas l'heure où nous l'avons appris.

        Et jamais de levée : un colis déjà remis (par le concierge, avant que
        l'avis soit traité) n'est pas une erreur, c'est l'ordre normal des
        choses quand deux canaux constatent la même chose.
        """
        self.ensure_one()
        if self.state != "held":
            return _("Le colis %(parcel)s n'était plus en dépôt (%(state)s) : "
                     "rien n'a été changé.") % {
                "parcel": self.name,
                "state": dict(self._fields["state"]._description_selection(
                    self.env))[self.state],
            }
        self.sudo().write({"state": "collected", "date_closed": when})
        # ⚠️ `_message_log` et non `message_post` : ce dernier exige une
        # adresse à son auteur, et le passage planifié tourne au nom du compte
        # système, qui n'en a pas forcément (même leçon que bf_property_sms,
        # 2026-09-26).
        self._message_log(body=_(
            "Colis retiré du casier Expedibox par le destinataire."))
        return _("Colis %s remis (retrait constaté par le casier).") % self.name
