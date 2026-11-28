from odoo import api, fields, models


class ProjectProject(models.Model):
    """Qui paie et qui reçoit, dits séparément sur le projet.

    `partner_id` est le seul client qu'un projet d'Odoo porte, et toute la
    facturation en descend : les lignes analytiques, les factures et les
    banques d'heures le suivent. Les cahiers de projet tenus à la main, eux,
    nomment deux parties : celle qui commande et paie, celle pour qui le
    travail est fait. Une heure de formation donnée à une entreprise se
    facture au centre qui la finance ; un volet payé par une association est
    reçu par l'un de ses membres.

    Donc `partner_id` garde son rôle (c'est le payeur, le donneur d'ordre)
    et ce module ajoute le bénéficiaire à côté. Le laisser vide dit que les
    deux sont la même partie, ce qui est le cas de la plupart des projets : le
    champ est là pour porter la différence, pas pour être saisi deux fois.
    """

    _inherit = "project.project"

    bf_beneficiary_id = fields.Many2one(
        "res.partner",
        string="Beneficiary",
        tracking=True,
        # `btree_not_null` et non `btree` : la colonne est vide sur la plupart
        # des projets (le bénéficiaire EST le donneur d'ordre), et un index qui
        # porterait ces NULL coûterait sans jamais servir à les chercher.
        index="btree_not_null",
        # Le MÊME domaine de société que `partner_id` (project/models/
        # project_project.py). Sans lui, l'écran proposait à un usager qui
        # voit deux sociétés, sur un projet de A, un partenaire réservé à B :
        # la page publique de A le nommait et le pont du portail lui envoyait
        # le code d'accès. ⚠️ Un domaine borne l'écran, pas le serveur (RPC et
        # import passent), exactement comme pour le donneur d'ordre. Pas de
        # `check_company` : `project.project` n'a pas `_check_company_auto` en
        # Odoo 18, il n'ajouterait que ce domaine.
        domain="['|', ('company_id', '=?', company_id), ('company_id', '=', False)]",
        help="The client the work is done for, when someone else orders and "
        "pays for it. Leave it empty when the ordering customer is also the "
        "beneficiary.",
    )

    bf_beneficiary_effective_id = fields.Many2one(
        "res.partner",
        string="Beneficiary or ordering customer",
        compute="_compute_bf_beneficiary_effective_id",
        help="The beneficiary when one is named, the ordering customer "
        "otherwise. This is the party a client-facing page must name.",
    )

    @api.depends(
        "bf_beneficiary_id.commercial_partner_id",
        "partner_id.commercial_partner_id",
    )
    def _compute_bf_beneficiary_effective_id(self):
        """La partie qu'une page tournée vers le client doit nommer.

        Calculée ici, à un seul endroit, pour que le portail et tout autre
        lecteur n'aient pas chacun à refaire le « sinon le donneur d'ordre ».

        Le partenaire COMMERCIAL, et non le contact : un projet rattaché à
        « Jean Fictif (Bêta inc.) » est lu par Bêta inc. Le portail lisait déjà
        `commercial_partner_id` pour le payeur ; le bénéficiaire reçoit le même
        traitement, sinon les deux ne se nomment pas de la même façon selon la
        fiche saisie.
        """
        for projet in self:
            partenaire = projet.bf_beneficiary_id or projet.partner_id
            projet.bf_beneficiary_effective_id = partenaire.commercial_partner_id
