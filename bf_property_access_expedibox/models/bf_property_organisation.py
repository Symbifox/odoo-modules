"""Ce que le casier connecté ajoute à l'EFVP du contrôle d'accès."""
from odoo import _, models


class BfPropertyOrganisation(models.Model):
    _inherit = "bf.property.organisation"

    def _bf_access_efvp_extras(self):
        """Communication à un tiers (art. 17, 18.3) : le casier reçoit des
        renseignements, dont un moyen de joindre le résident."""
        extras = super()._bf_access_efvp_extras()
        points = self.env["bf.property.access.point"].sudo().with_context(
            active_test=False).search([
                ("organisation_id", "in", self.ids),
                ("connector_id.kind", "=", "expedibox"),
            ])
        if not points:
            return extras
        contact = set(points.connector_id.mapped("expedibox_contact"))
        sent = (_("le courriel et le numéro de mobile") if "both" in contact
                else _("le courriel, ou le numéro de mobile à défaut de courriel"))
        extras.append({
            "title": _("Boîte à colis connectée (Expedibox) : %s")
                     % ", ".join(points.mapped("name")),
            "items": [
                _("Ce que le logiciel transmet au casier, à l'arrivée du résident : "
                  "prénom, nom, logement, %s, et un identifiant de rapprochement "
                  "propre à ce logiciel. Aucun numéro fixe, aucune date de bail, "
                  "aucun code.") % sent,
                _("Pourquoi un moyen de joindre la personne : le casier l'avise "
                  "lui-même qu'un colis l'attend. Sans courriel ni mobile, "
                  "Expedibox refuse l'inscription et l'ordre revient au "
                  "responsable du point."),
                _("Ce que le casier montre : son écran sert au livreur à choisir "
                  "le destinataire, donc le nom du résident y paraît, et le "
                  "réglage d'affichage appartient au casier, pas au logiciel. "
                  "À vérifier chez le fournisseur et à nommer ici."),
                _("Seuls les résidents sont inscrits : un accès temporaire "
                  "(visiteur, entrepreneur, personnel) n'est jamais transmis au "
                  "casier par le logiciel."),
                _("Au départ confirmé, le logiciel retire le destinataire du "
                  "casier. Il ne touche jamais un destinataire qu'il n'a pas "
                  "inscrit lui-même (rapprochement par identifiant), et il "
                  "signale au responsable ceux qu'aucun droit ne réclame plus "
                  "au lieu de les supprimer de lui-même."),
                _("Une correction au dossier (nom, logement) est portée au "
                  "casier par le passage de nuit (art. 28 : la correction suit "
                  "le renseignement communiqué)."),
                _("À encadrer hors du logiciel : entente écrite avec le "
                  "fournisseur du casier (art. 18.3), lieu d'hébergement de ses "
                  "serveurs et évaluation préalable s'il est hors Québec "
                  "(art. 17), durée de conservation chez lui après le retrait."),
            ],
        })
        return extras
