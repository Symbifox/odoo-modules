"""Par quelle porte un locataire arrive à son bail.

🔴 **Pas par la fraction, et c'est tout le point de ce fichier.**

Le portail de la copropriété résout son auditoire par `unit.occupant_id`, le
locataire porté à la fraction pour le registre de l'art. 1070. Réutiliser cette
porte ici produirait trois erreurs d'un coup :

1. **Les co-locataires disparaîtraient.** Un bail a `tenant_ids`, plusieurs
   personnes. La fraction n'en porte qu'une seule, celle que le syndicat a
   inscrite. Le deuxième signataire ne verrait pas le bail qu'il a signé.
2. **Les baux sans fraction disparaîtraient.** Une chambre ou un terrain de
   maison mobile ne correspond à aucune fraction inscrite : `unit_id` est vide,
   et c'est prévu.
3. 🔴 **Et le pire, en sens inverse : quelqu'un verrait le bail d'autrui.** La
   personne portée comme occupante d'une fraction sans être partie au bail y
   accéderait — un conjoint séparé resté au registre, un occupant inscrit à la
   hâte, un ancien locataire jamais retiré.

L'appartenance se lit donc au BAIL. C'est le bail qui dit qui est locataire, pas
le registre du syndicat, qui poursuit un autre but.

⚠️ Même famille qu'un défaut déjà corrigé ailleurs : la garde vérifiait
la copropriété et pas la fraction, et un occupant annonçait son visiteur à la
porte du voisin. Vérifier la bonne appartenance, pas une appartenance voisine.
"""
from odoo import _, api, fields, models

from odoo.addons.bf_property_core.models.mail_template import bf_mail_layout


class BfRentalLease(models.Model):
    _inherit = "bf.rental.lease"

    def _bf_portal_signed_forms(self):
        """Les pièces que le portail peut servir : celles RATTACHÉES au bail.

        🔴 L'appartenance à la relation ne suffit pas. Odoo ne contrôle aucun
        droit sur la pièce qu'on glisse dans un many2many : un gestionnaire
        pouvait y mettre l'identifiant de n'importe quelle pièce de la base,
        s'inviter au portail sur ce bail et la télécharger en `sudo`. Le bail
        rattache désormais ses formulaires (`res_model`, `res_id`), et seuls
        ceux-là sortent.
        """
        self.ensure_one()
        return self.sudo().lease_attachment_ids.filtered(
            lambda a: a.res_model == self._name and a.res_id == self.id
            and not a.res_field
        )

    def message_post(self, **kwargs):
        """Le courriel part dans la mise en page de marque quand elle est là.

        Sans ceci, la réponse du bureau à un résident partait
        dans l'habillage d'Odoo, nue, pendant que les factures et les banques
        d'heures portaient celui de la société. Un envoi qui choisit lui-même
        sa mise en page la garde.
        """
        if not kwargs.get("email_layout_xmlid"):
            kwargs["email_layout_xmlid"] = bf_mail_layout(self.env)
        return super().message_post(**kwargs)

    def action_bf_invite_portal(self):
        """Invite les locataires au portail, ou leur renvoie l'accès."""
        self.ensure_one()
        invited, resent, skipped = self.tenant_ids._bf_property_grant_portal(
            "bf_rental_portal.mail_template_lessee_invitation", self.organisation_id)
        message = self.tenant_ids._bf_property_portal_summary(invited, resent, skipped)
        self.message_post(body=message)
        return self._bf_portal_banner(message)

    # ── Ce que le courriel du bureau dit, et où il mène ──

    def _message_compute_subject(self):
        """« Locations Exemple inc. : bail BAIL/2026/0001 ».

        🔴 Sans ceci, la référence seule arrivait comme sujet.
        ⚠️ Odoo 18 rend une CHAÎNE pour un enregistrement, jamais un dict.
        """
        self.ensure_one()
        return _("%(landlord)s : bail %(name)s") % {
            "landlord": self.sudo().organisation_id.name, "name": self.name}

    def _notify_get_recipients_groups(self, message, model_description, msg_vals=None):
        """Un bouton vers SON logement pour le locataire qui a un compte portail."""
        groups = super()._notify_get_recipients_groups(
            message, model_description, msg_vals=msg_vals)
        if not self:
            return groups
        url = "%s/my/rental" % self[:1].get_base_url()
        for name, _func, data in groups:
            if name == "portal":
                data["active"] = True
                data["has_button_access"] = True
                data["button_access"] = {"url": url, "title": _("Voir mon logement")}
        return groups

    def _bf_portal_banner(self, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": _("Accès au portail"), "message": message,
                       "sticky": False, "type": "info"},
        }

    @api.model
    def _portal_leases_for(self, partner):
        """Les baux dont cette personne est partie. Rien d'autre.

        ⚠️ Aucun filtre de date ici : un bail terminé reste lisible par son
        ancien locataire, qui peut en avoir besoin pour une démarche. Ce qui se
        ferme, c'est le droit d'agir dessus, pas celui de le relire.
        """
        if not partner:
            return self.browse()
        return self.search([("tenant_ids", "in", partner.ids)])

    # ── Ce que le bail dit de lui-même, sans ouvrir le registre ──────────
    #
    # 🔴 **Le portail n'obtient AUCUN droit sur `bf.property.organisation`,
    # `bf.property.building` ni `bf.property.unit`**, et ces trois champs
    # existent pour rendre ce refus tenable.
    #
    # Le premier jet affichait `lease.organisation_id.name` et
    # `lease.unit_id.name` au gabarit. La page est sortie en 504 : le groupe
    # Portail n'a aucune ACL sur ces modèles. Le réflexe est d'en ajouter trois
    # — c'est ce que fait le portail de la copropriété — et c'est ici le mauvais
    # geste, parce que ces modèles ne portent pas que des noms :
    #
    # * `bf.property.organisation` porte `overdue_amount`, `overdue_total`,
    #   `self_insurance_balance`, `contingency_shortfall` : la situation
    #   financière du syndicat ou du bailleur.
    # * `bf.property.unit` porte `owner_ids`, `owner_display`, `quote_part`,
    #   `overdue_amount`, `overdue_days` : l'identité du propriétaire et ce
    #   qu'il doit.
    #
    # Rien de cela n'appartient au locataire. Le registre de l'art. 1070 C.c.Q.
    # l'inscrit par son nom et son adresse ; il ne lui ouvre pas les pièces du
    # syndicat. Une ACL de lecture donnée « pour afficher un nom » donne le
    # modèle entier, et le gabarit n'est pas la garde.
    #
    # ⚠️ `store=True` n'est PAS un détail de performance. Un related NON stocké
    # lit à travers, à l'affichage, avec les droits du lecteur : la page
    # retomberait exactement sur la même erreur, et seulement pour les baux qui
    # portent une fraction. Stocké, le champ est une colonne du bail, écrite
    # sous les droits du locateur et lue sous ceux du bail.
    #
    # Ce que le locataire lit ici, c'est donc ce que SON bail dit : qui est le
    # locateur, où est le logement, lequel c'est. Le formulaire obligatoire le
    # lui demande déjà à sa première section.

    portal_landlord_name = fields.Char(
        string="Locateur (portail)",
        related="organisation_id.name", store=True, readonly=True,
    )
    portal_building_name = fields.Char(
        string="Immeuble (portail)",
        related="building_id.name", store=True, readonly=True,
    )
    portal_unit_name = fields.Char(
        string="Logement (portail)",
        related="unit_id.name", store=True, readonly=True,
        help="⚠️ Vide pour un bail de chambre ou de terrain de maison mobile, "
             "qui ne correspond à aucune fraction inscrite.",
    )
