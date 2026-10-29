from odoo import models

# Les champs du socle que la carte porte désormais : `contacts_nextcloud_sync`
# ne marque à repousser que les fiches dont SES champs changent.
_PRONOUN_FIELDS = {"bf_pronouns", "bf_grammatical_gender"}


def _to_push(partner):
    """Une fiche que la synchro POUSSE vers Nextcloud. Dans les autres sens,
    une carte marquée est relue de Nextcloud, qui écrase la fiche et la fait
    changer de source : les pronoms n'y arrivent jamais."""
    config = partner.x_nc_contacts_config_id
    return bool(partner.x_nc_contact_uid and config and config.active
                and config.sync_direction == "odoo_to_nc"
                and partner.x_contact_sync_source != "nextcloud")


class ResPartner(models.Model):
    _inherit = "res.partner"

    def write(self, vals):
        result = super().write(vals)
        if _PRONOUN_FIELDS & set(vals) and not self.env.context.get("skip_nc_contact_sync"):
            # Mêmes conditions que le module : une fiche reliée à Nextcloud,
            # dont Odoo est la source.
            dirty = self.filtered(_to_push)
            if dirty:
                dirty.with_context(skip_nc_contact_sync=True).write({"x_carddav_etag": False})
        return result
