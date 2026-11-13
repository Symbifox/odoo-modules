"""L'identifiant qui rapproche un droit de son destinataire au casier."""
from odoo import _, models
from odoo.exceptions import UserError


class BfPropertyAccessGrant(models.Model):
    _inherit = "bf.property.access.grant"

    def _expedibox_prefix(self):
        """Ce qui nomme CETTE base dans un identifiant de destinataire.

        🔴 Pas de valeur de repli : deux bases sans identifiant partageraient
        le même préfixe, se reconnaîtraient l'une l'autre au casier et
        s'effaceraient mutuellement. Mieux vaut refuser l'appel.
        """
        uuid = (self.env["ir.config_parameter"].sudo().get_param("database.uuid") or "")
        base = uuid.replace("-", "")[:8]
        if not base:
            raise UserError(_("Cette base n'a pas d'identifiant (database.uuid) : "
                              "le casier ne peut pas distinguer ses inscriptions "
                              "de celles d'une autre base."))
        return "sfx-%s-" % base

    def _expedibox_identifier(self):
        """Notre clé de rapprochement (``custom_identifier``, 50 caractères).

        🔴 Elle ne porte PAS l'identifiant du droit. Le socle pousse l'appel
        dans la transaction qui crée le droit : si cette transaction est
        rejetée après que le casier a répondu, le droit n'existe plus, son
        identifiant est brûlé (une séquence ne revient pas en arrière), et le
        destinataire resterait au casier sans qu'aucun droit ne puisse plus
        s'y reconnaître ni le retirer. La personne et le point, eux, existaient
        déjà avant la transaction : le droit recréé retrouve son destinataire,
        l'adopte, et aucun doublon n'est créé.

        Le préfixe nomme la base : une démo ou un banc branché par mégarde sur
        le casier d'un client ne reconnaîtrait aucun de ses destinataires.
        """
        self.ensure_one()
        # Un accès temporaire ne s'inscrit pas au casier (voir le connecteur) :
        # seul un résident a une clé.
        return ("%sp%s-pt%s" % (self._expedibox_prefix(),
                                self.partner_id.id, self.point_id.id))[:50]
