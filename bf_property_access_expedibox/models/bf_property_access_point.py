"""Le point d'accès d'un casier porte l'identifiant du casier chez Expedibox."""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

LIVE_STATES = ("awaiting_consent", "to_issue", "issued")


class BfPropertyAccessPoint(models.Model):
    _inherit = "bf.property.access.point"

    expedibox_locker_id = fields.Char(
        string="Identifiant du casier Expedibox",
        copy=False,
        tracking=True,
        help="L'identifiant que le portail d'Expedibox donne au casier "
             "(« Installation details »). Le bouton « Casiers du jeton » du "
             "connecteur en donne la liste.",
    )
    connector_kind = fields.Selection(
        related="connector_id.kind", string="Type de connecteur",
        help="Lu par la vue : l'identifiant du casier ne se montre que pour "
             "un connecteur Expedibox.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("expedibox_locker_id"):
                # Recopié à la main depuis « qovN9wxk — Casier A » : une espace
                # au bout, et le casier ne reconnaîtrait plus aucune de nos
                # inscriptions (comparaison stricte de chaînes).
                vals["expedibox_locker_id"] = vals["expedibox_locker_id"].strip()
        return super().create(vals_list)

    def write(self, vals):
        if "expedibox_locker_id" in vals and vals["expedibox_locker_id"]:
            vals["expedibox_locker_id"] = vals["expedibox_locker_id"].strip()
        if "expedibox_locker_id" in vals:
            # 🔴 Pour un point Expedibox, l'identifiant du casier EST la portée
            # du point : c'est lui qui décide quelle serrure s'ouvre. Le socle
            # traite déjà « la portée a changé » pour ses quatre champs
            # (archivage, fraction, immeuble, type) en retirant puis en
            # réémettant ; sans ceci, un identifiant corrigé laissait les
            # résidents inscrits à l'ANCIEN casier, et personne au nouveau.
            for point in self:
                if point.connector_id.kind != "expedibox":
                    continue
                if (vals["expedibox_locker_id"] or "") == (point.expedibox_locker_id or ""):
                    continue
                live = point.grant_ids.filtered(lambda g: g.state in LIVE_STATES)
                # Les retraits partent MAINTENANT, donc vers l'ancien casier :
                # le champ n'a pas encore changé.
                live._bf_revoke("other")
        res = super().write(vals)
        if "expedibox_locker_id" in vals:
            self.filtered(
                lambda p: p.active and p.connector_id.kind == "expedibox"
            )._bf_ensure_grants()
        return res

    @api.constrains("connector_id", "point_type", "expedibox_locker_id")
    def _check_expedibox(self):
        for point in self:
            if point.connector_id.kind != "expedibox":
                continue
            # Expedibox ne fait que des casiers : un portier ou une serrure
            # branchés là n'enverraient rien, et les ordres partiraient quand même.
            if point.point_type != "parcel":
                raise ValidationError(_(
                    "Le connecteur « %(connector)s » est un connecteur de boîte "
                    "à colis : le point « %(point)s » doit être de type « Boîte "
                    "à colis ».")
                    % {"connector": point.connector_id.name, "point": point.name})
            if not point.expedibox_locker_id:
                raise ValidationError(_(
                    "Le point « %s » est relié à Expedibox : il lui faut "
                    "l'identifiant de son casier.") % point.name)
