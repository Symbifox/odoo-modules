from odoo import _, api, fields, models


class BudgetLine(models.Model):
    _inherit = "bf.budget.line"

    training_category_ids = fields.Many2many(
        "bf.training.category", "bf_budget_line_training_category_rel",
        "line_id", "category_id", string="Catégories de formation",
        help="Les catégories du registre dont le coût nourrit cette ligne. "
             "Vide, la ligne ignore le registre.")
    training_record_count = fields.Integer(
        string="Formations comptées", compute="_compute_training_amounts")
    training_incomplete_count = fields.Integer(
        string="Formations incomplètes", compute="_compute_training_amounts")
    training_actual = fields.Monetary(
        string="Réalisé de formation", compute="_compute_training_amounts",
        currency_field="currency_id")
    training_uncosted_count = fields.Integer(
        string="Obligations non chiffrables", compute="_compute_training_engagements",
        help="Obligations à venir dont le coût ne peut pas être établi : activité "
             "sans durée, ou personne sans taux horaire. Elles ne sont pas dans "
             "l'engagé, et ce compteur est la seule chose qui le dit.")
    training_committed = fields.Monetary(
        string="Engagé de formation", compute="_compute_training_engagements",
        currency_field="currency_id")

    # ------------------------------------------------------------------
    # La garde du socle, étendue et non desserrée
    # ------------------------------------------------------------------
    @api.constrains("source", "analytic_account_ids", "training_category_ids")
    def _check_source_requirements(self):
        """Une catégorie de formation est un axe, au même titre qu'un analytique.

        🔴 Le socle refuse une ligne de coût interne sans compte analytique, et
        il a raison : sans axe, la ligne ne peut RIEN sélectionner, lit zéro pour
        toujours, et a l'air parfaitement normale. C'est le même faux vert que
        partout ailleurs dans la maison.

        Ce que ce pont change n'est pas l'invariant — « une ligne doit pouvoir
        désigner ce qui lui appartient » — mais la liste des axes qui le
        satisfont. Quand la garde a été écrite, l'analytique était le seul.
        Une catégorie de formation en est un autre, et elle sélectionne aussi
        précisément.

        ⚠️ On ne surcharge donc PAS pour laisser passer une ligne sans axe : une
        ligne sans analytique ET sans catégorie lève toujours.
        """
        for ligne in self:
            if ligne.source == "internal_cost" and ligne.training_category_ids:
                continue
            super(BudgetLine, ligne)._check_source_requirements()

    # ------------------------------------------------------------------
    # Le domaine
    # ------------------------------------------------------------------
    def _training_domain(self):
        """Les réalisations que cette ligne regarde.

        ⚠️ La fenêtre vient du BUDGET, pas de la ligne : `date_start` et
        `date_end` de la ligne sont facultatifs et servent à découper un poste
        à l'intérieur de l'exercice. Retomber sur l'exercice quand ils manquent
        est ce qui rend une ligne sans dates comparable aux autres.
        """
        self.ensure_one()
        debut = self.date_start or self.budget_id.date_start
        fin = self.date_end or self.budget_id.date_end
        domaine = [
            ("activity_id.category_id", "in", self.training_category_ids.ids),
            ("state", "=", "confirmed"),
        ]
        if debut:
            domaine.append(("date_done", ">=", debut))
        if fin:
            domaine.append(("date_done", "<=", fin))
        if self.company_id:
            domaine.append(("company_id", "=", self.company_id.id))
        return domaine

    @api.depends("training_category_ids", "date_start", "date_end",
                 "budget_id.date_start", "budget_id.date_end", "company_id")
    def _compute_training_amounts(self):
        Realisation = self.env["bf.training.record"].sudo()
        for ligne in self:
            ligne.training_actual = 0.0
            ligne.training_record_count = 0
            ligne.training_incomplete_count = 0
            if not ligne.training_category_ids:
                continue
            realisations = Realisation.search(ligne._training_domain())
            # 🔴 Le partage est ici, et c'est tout le module : ce qui est
            # incomplet ne descend PAS dans le réalisé. Il remonte plus bas
            # dans les heures non valorisées, là où le budget le signale déjà.
            completes = realisations.filtered("is_complete")
            ligne.training_actual = sum(completes.mapped("total_cost"))
            ligne.training_record_count = len(completes)
            ligne.training_incomplete_count = len(realisations) - len(completes)

    # ------------------------------------------------------------------
    # Les trois points d'extension du socle budgétaire
    # ------------------------------------------------------------------
    def _read_actual(self):
        """Ajouter le coût des formations au réalisé de la ligne.

        ⚠️ On ajoute PAR-DESSUS le socle au lieu de le remplacer : une ligne peut
        très bien porter des comptes analytiques ET des catégories de formation,
        et écraser ferait disparaître la moitié sans un mot.
        """
        montant = super()._read_actual()
        if not self.training_category_ids:
            return montant
        # 🔴 PAS de `_sign()` ici, et c'est contre-intuitif. `_sign()` redresse
        # les montants ANALYTIQUES, stockés négatifs pour une charge : il rend
        # -1 sur une ligne de coût interne en charges. Le coût d'une formation,
        # lui, est déjà stocké positif au registre. L'y appliquer rendait -400 $
        # pour 400 $ dépensés — un réalisé négatif sur une charge, que le
        # tableau aurait affiché comme une économie.
        return montant + self.training_actual

    def _compute_unvalued_hours(self):
        """Les heures des formations incomplètes sont des heures non valorisées.

        Le socle le dit déjà pour les feuilles de temps sans taux horaire :
        « la ligne lit zéro et a l'air parfaitement normale ». Une formation à
        laquelle il manque ses heures ou son coût horaire pose exactement le
        même problème, et mérite exactement le même signalement.
        """
        super()._compute_unvalued_hours()
        Realisation = self.env["bf.training.record"].sudo()
        for ligne in self:
            if not ligne.training_category_ids:
                continue
            incompletes = Realisation.search(
                ligne._training_domain()).filtered(lambda r: not r.is_complete)
            if incompletes:
                ligne.unvalued_hours += sum(incompletes.mapped("hours"))
                ligne.has_unvalued_time = True

    def _engagements_de_formation(self):
        """(montant chiffrable, nombre de non chiffrables).

        🔴 Rendre les DEUX est tout le point. Sauter une obligation qu'on ne sait
        pas chiffrer ne change aucun chiffre — `0 × 40` valait déjà zéro — donc
        une garde qui se contente de sauter est un commentaire, pas un
        comportement, et rien ne peut l'éprouver. Ce qui la rend réelle est de
        COMPTER ce qu'on laisse dehors : un engagement sous-évalué est pire qu'un
        engagement absent, parce qu'il a l'air renseigné, et la seule chose qui
        distingue les deux est ce compteur.
        """
        self.ensure_one()
        if not self.training_category_ids:
            return 0.0, 0
        Obligation = self.env["bf.training.obligation"].sudo()
        fin = self.date_end or self.budget_id.date_end
        # ⚠️ Le champ s'appelle `due_date`, pas `date_due`. Un nom inventé
        # passe la lecture, passe le linter, et lève au premier appel.
        domaine = [
            ("state", "in", ("pending", "expiring", "overdue")),
            "|",
            ("requirement_id.category_ids", "in", self.training_category_ids.ids),
            ("activity_id.category_id", "in", self.training_category_ids.ids),
        ]
        if fin:
            domaine += ["|", ("due_date", "<=", fin), ("due_date", "=", False)]
        montant = 0.0
        non_chiffrables = 0
        for obligation in Obligation.search(domaine):
            # Une exigence en heures porte les siennes ; une exigence sur une
            # activité précise emprunte la durée prévue de cette activité.
            heures = obligation.hours_required or obligation.activity_id.duration_hours
            taux = obligation.employee_id.hourly_cost
            if not heures or not taux:
                non_chiffrables += 1
                continue
            montant += heures * taux
        return montant, non_chiffrables

    @api.depends("training_category_ids", "date_end", "budget_id.date_end")
    def _compute_training_engagements(self):
        for ligne in self:
            montant, non_chiffrables = ligne._engagements_de_formation()
            ligne.training_committed = montant
            ligne.training_uncosted_count = non_chiffrables

    def _get_extra_commitments(self):
        """Ce qui est dû et pas encore suivi est un engagement, pas une surprise.

        ⚠️ On ajoute PAR-DESSUS le socle : il a ses propres engagements (bons de
        commande, notes de frais, abonnements à venir) et les écraser ferait
        disparaître la moitié du tableau sans un mot.
        """
        engage = super()._get_extra_commitments()
        montant, _non_chiffrables = self._engagements_de_formation()
        return engage + montant
