"""Le préventif naît du bien, pas de la fermeture du billet précédent.

Le module `maintenance` d'Odoo sait répéter un travail, mais il le répète **par
billet** : dans `write()`, quand l'étape devient terminée, un `copy()` crée le
suivant à l'intervalle indiqué. Mesuré, et c'est une
chaîne, pas un cédule — elle tient tant que quelqu'un ferme chaque billet, et
elle s'arrête net le jour où personne ne le fait. Sans rien signaler : il ne
reste alors aucun billet à regarder.

Une chaudière s'inspecte deux fois l'an, que le billet précédent ait été fermé
ou non. C'est une récurrence **par bien**, et c'est ce que ce modèle porte.

⚠️ **Deux moteurs de récurrence dans la même base, c'est un de trop.** Un
travail issu d'un cédule ne peut donc pas porter en plus la récurrence par
billet du module d'origine : à sa fermeture, les deux ouvriraient chacun le
suivant. Une garde le refuse, plutôt que de laisser le doublon se constater à
l'usage — deux billets identiques ressemblent à une erreur de saisie.

⚠️ **Un cédule ne réclame rien d'avant lui.** À la création, la première
échéance est la première occurrence à VENIR, jamais une occurrence passée :
poser un cédule annuel sur une chaudière installée en 2015 ne doit pas ouvrir
onze billets d'arriéré pour des inspections que personne n'a promis de faire.
La date de début sert d'ancrage — c'est elle qui dit si l'inspection tombe en
mars ou en septembre — pas de prétexte à un rattrapage rétroactif.

⚠️ **r. 8.01, art. 4** veut, à la mise à jour annuelle du carnet, ce qui n'a pas
été fait ET pourquoi. Un préventif qu'on saute doit donc porter sa raison :
c'est `action_bf_not_done`, sur le travail. Le pont vers le carnet
(`bf_property_operations_records`) la porte jusqu'au carnet. Sans cela, le
module produirait un carnet qui ment par omission — le défaut de classe corrigé
ailleurs dans la suite.

⚠️ **Ce module ne fait pas de devis.** Un travail cédulé n'est ni une commande,
ni une facture, ni un bon de travail fournisseur. Le plafond du module est
explicite : la génération, et le retour au carnet.
"""
import calendar

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.osv import expression

# Le vocabulaire est celui du module d'origine (`repeat_unit`), au mot près :
# une base qui porte deux façons de dire « tous les trois mois » en porte une
# de trop.
INTERVAL_UNITS = [
    ("day", "Jours"),
    ("week", "Semaines"),
    ("month", "Mois"),
    ("year", "Ans"),
]

# ⚠️ Borne de rattrapage, par cédule et par passe. Elle ne change pas ce qui
# est dû : la passe suivante reprend là où celle-ci s'arrête. Elle empêche
# seulement qu'un cron resté muet pendant un an sur un cédule quotidien fasse
# de la passe de reprise une transaction de trois cent soixante-cinq billets.
MAX_CATCH_UP = 24


class BfPropertyMaintenancePlan(models.Model):
    _name = "bf.property.maintenance.plan"
    _description = "Cédule d'entretien préventif"
    _order = "next_date, id"

    name = fields.Char(
        string="Travail d'entretien",
        required=True,
        help="Ce qui est à faire, dit comme on le dirait à la personne qui le "
             "fera : « Inspection de la chaudière », « Vérification des "
             "détecteurs de fumée ».",
    )
    equipment_id = fields.Many2one(
        "maintenance.equipment",
        string="Bien",
        required=True,
        index=True,
        # Le cédule n'a plus d'objet sans le bien : il ne survit pas à sa
        # suppression. Les travaux déjà ouverts, eux, survivent au cédule.
        ondelete="cascade",
        # Pas de `check_company` : la société du cédule est CELLE du bien, par
        # champ lié. Un écart est impossible par construction, et le contrôle
        # ne ferait que lever sur l'ordre de calcul à la création.
        help="Le bien dont l'entretien se répète. C'est lui qui donne "
             "l'immeuble, l'équipe et le technicien du travail généré.",
    )
    company_id = fields.Many2one(
        related="equipment_id.company_id", store=True, index=True, string="Société"
    )
    active = fields.Boolean(
        default=True,
        help="Un cédule archivé cesse d'ouvrir des travaux. Les travaux déjà "
             "ouverts restent.",
    )

    interval_count = fields.Integer(
        string="Tous les",
        required=True,
        default=1,
        help="r. 8.01, art. 2 al. 2, par. 2° : la fréquence à laquelle les "
             "travaux d'entretien requis doivent être effectués.",
    )
    interval_unit = fields.Selection(
        INTERVAL_UNITS, string="Unité", required=True, default="month"
    )
    frequency_display = fields.Char(
        string="Fréquence",
        compute="_compute_frequency_display",
        help="La fréquence telle qu'elle se lit, pour les écrans où l'on "
             "balaie des cédules sans les ouvrir.",
    )

    start_date = fields.Date(
        string="À partir du",
        required=True,
        default=fields.Date.context_today,
        help="L'ancrage du cédule : c'est cette date qui dit si l'inspection "
             "tombe en mars ou en septembre. Elle ne fait pas d'arriéré : la "
             "première échéance est la première occurrence à venir.",
    )
    end_date = fields.Date(
        string="Jusqu'au",
        help="Au-delà, le cédule n'ouvre plus rien. Laisser vide pour un "
             "entretien qui n'a pas de terme connu.",
    )
    next_date = fields.Date(
        string="Prochaine échéance",
        index=True,
        copy=False,
        help="La prochaine occurrence attendue. Elle avance d'elle-même à "
             "chaque travail ouvert, et se corrige à la main quand une "
             "campagne se décale.",
    )
    advance_days = fields.Integer(
        string="Ouvrir le travail d'avance (jours)",
        default=0,
        help="Le travail apparaît sur la liste de l'équipe ce nombre de jours "
             "avant son échéance. Zéro l'ouvre le jour même.",
    )

    duration = fields.Float(
        string="Durée prévue",
        help="Le temps que le travail prend, reporté sur chaque travail "
             "ouvert. C'est ce qui permettra de composer une liste de quart "
             "qui tienne dans un quart.",
    )
    instruction_text = fields.Html(
        string="Consigne",
        help="Ce qu'il y a à faire, dans le détail, recopié sur chaque travail "
             "ouvert. Une consigne qui vit uniquement dans la tête du "
             "concierge sortant se perd avec lui.",
    )

    request_ids = fields.One2many(
        "maintenance.request", "bf_plan_id", string="Travaux ouverts"
    )
    request_count = fields.Integer(
        string="Nombre de travaux", compute="_compute_request_count"
    )
    last_done_date = fields.Date(
        string="Dernier entretien fait",
        compute="_compute_last_done_date",
        help="La date du dernier travail de ce cédule effectivement terminé. "
             "C'est elle que le carnet d'entretien reprend, lorsque le pont "
             "vers le carnet est installé.",
    )

    _sql_constraints = [
        (
            "interval_positive",
            "CHECK(interval_count > 0)",
            "Une fréquence d'entretien se répète au moins tous les 1.",
        ),
        (
            "advance_days_positive",
            "CHECK(advance_days >= 0)",
            "On ouvre un travail d'avance, pas en retard : le nombre de jours "
            "d'avance ne peut pas être négatif.",
        ),
    ]

    # ── Ce qui se lit ──

    @api.depends("interval_count", "interval_unit")
    @api.depends_context("lang")
    def _compute_frequency_display(self):
        # ⚠️ L'unité se lit dans la sélection traduite, pas dans la constante
        # du module : elle ne parle que français (« Every 6 mois »).
        units = dict(self._fields["interval_unit"]._description_selection(self.env))
        for plan in self:
            unit = units.get(plan.interval_unit, "")
            plan.frequency_display = _(
                "Tous les %(count)s %(unit)s",
                count=plan.interval_count,
                unit=unit.lower(),
            )

    @api.depends("request_ids")
    def _compute_request_count(self):
        for plan in self:
            plan.request_count = len(plan.request_ids)

    @api.depends("request_ids.close_date", "request_ids.stage_id.done")
    def _compute_last_done_date(self):
        for plan in self:
            done = plan.request_ids.filtered(
                lambda work: work.stage_id.done and work.close_date
            )
            plan.last_done_date = max(done.mapped("close_date"), default=False)

    @api.depends("name", "equipment_id")
    def _compute_display_name(self):
        for plan in self:
            if plan.equipment_id:
                plan.display_name = f"{plan.equipment_id.display_name} / {plan.name}"
            else:
                plan.display_name = plan.name or ""

    # ── Les contrôles ──

    @api.constrains("start_date", "end_date")
    def _check_dates(self):
        for plan in self:
            if plan.end_date and plan.end_date < plan.start_date:
                raise ValidationError(
                    _(
                        "Le cédule « %(plan)s » se termine le %(end)s et "
                        "commence le %(start)s.",
                        plan=plan.name or "",
                        end=plan.end_date,
                        start=plan.start_date,
                    )
                )

    # ── Le calendrier ──

    def _interval(self):
        """Le pas du cédule, sous la forme que `relativedelta` attend."""
        self.ensure_one()
        return relativedelta(**{f"{self.interval_unit}s": self.interval_count})

    def _next_occurrence(self, current):
        """L'occurrence qui suit celle-ci.

        🔴 **Le jour du mois vient de l'ANCRAGE, pas de l'occurrence
        précédente.** Sans cela, un cédule mensuel posé un 31 glisse au 30 le
        mois suivant, puis reste au 30 pour toujours ; passé février, il
        tombe à 28 et n'en remonte jamais. La dérive est silencieuse — chaque
        pas, pris isolément, a l'air juste — et au bout de deux ans
        l'inspection annuelle ne tombe plus à sa date anniversaire.

        Un décalage posé à la main sur la prochaine échéance déplace donc CETTE
        occurrence, et celles d'après reviennent à l'ancrage. C'est ce qu'on
        veut d'un cédule : une campagne se décale, un calendrier ne dérive pas.
        """
        self.ensure_one()
        following = current + self._interval()
        if self.interval_unit in ("month", "year") and self.start_date:
            last_day = calendar.monthrange(following.year, following.month)[1]
            following = following.replace(day=min(self.start_date.day, last_day))
        return following

    def _first_occurrence(self, today=None):
        """La première échéance : l'ancrage, avancé jusqu'à aujourd'hui.

        ⚠️ Le pas s'applique en boucle plutôt qu'en division : un pas mensuel
        n'a pas de longueur fixe, et « combien de mois depuis mars 2015 »
        n'a pas la même réponse selon qu'on compte en jours ou en mois.
        """
        self.ensure_one()
        today = today or fields.Date.context_today(self)
        occurrence = self.start_date
        if not occurrence:
            return False
        # Le cédule ne réclame rien d'avant lui : on avance jusqu'à la
        # première occurrence qui n'est pas passée.
        guard = 0
        while occurrence < today:
            occurrence = self._next_occurrence(occurrence)
            guard += 1
            if guard > 5000:  # un pas d'un jour depuis 1900 : personne.
                break
        if self.end_date and occurrence > self.end_date:
            return False
        return occurrence

    @api.model_create_multi
    def create(self, vals_list):
        """🔴 `next_date` se pose ici, pas par un `default`.

        Un défaut ne voit que son propre champ : il ne connaît ni la date de
        début, ni la fréquence, ni le terme du cédule qu'on est en train de
        créer. La première échéance se déduit des trois.
        """
        plans = super().create(vals_list)
        for plan in plans:
            if not plan.next_date:
                plan.next_date = plan._first_occurrence()
        return plans

    # ── La génération ──

    def _prepare_request_vals(self, due_date):
        """Le travail à ouvrir pour une échéance.

        🔴 `maintenance_team_id` porte un `default=_get_default_team_id` qui
        rend « la première équipe venue ». Un champ calculé qui porte AUSSI un
        défaut ne calcule pas à la création : sans l'équipe posée ici
        explicitement, le préventif partirait chez une équipe plausible qui
        n'est pas la bonne, et rien ne le montrerait. Mesuré sur le pont
        du portail, même piège, même correctif.
        """
        self.ensure_one()
        equipment = self.equipment_id
        vals = {
            "name": self.name,
            "equipment_id": equipment.id,
            "company_id": equipment.company_id.id or self.env.company.id,
            "maintenance_type": "preventive",
            # ⚠️ Jamais `recurring_maintenance` : le cédule EST la récurrence.
            # Voir la garde `_check_bf_plan_is_the_only_engine`.
            "recurring_maintenance": False,
            "request_date": due_date,
            # ⚠️ Midi UTC, et non minuit. Une date-heure à minuit UTC s'affiche
            # la VEILLE dans tout fuseau des Amériques : le travail du 1er
            # septembre apparaîtrait daté du 31 août sur l'écran du concierge.
            # Le module ne prétend pas connaître l'heure de la tournée — c'est
            # le quart qui la décidera.
            "schedule_date": fields.Datetime.to_datetime(due_date).replace(hour=12),
            "duration": self.duration,
            "bf_plan_id": self.id,
        }
        if equipment.maintenance_team_id:
            vals["maintenance_team_id"] = equipment.maintenance_team_id.id
        if self.instruction_text:
            vals["instruction_type"] = "text"
            vals["instruction_text"] = self.instruction_text
        return vals

    def _generate_due(self, today=None):
        """Ouvrir les travaux à échoir, et avancer l'échéance.

        Chaque occurrence manquée donne SON travail : deux inspections sautées
        sont deux obligations distinctes, et l'art. 4 du règlement veut une
        raison pour chacune. Un seul billet de rattrapage en effacerait une.
        """
        today = today or fields.Date.context_today(self)
        works = self.env["maintenance.request"]
        for plan in self:
            if not plan.next_date or not plan.active:
                continue
            horizon = today + relativedelta(days=plan.advance_days)
            due = plan.next_date
            opened = 0
            # ⚠️ Les travaux se posent en UN seul `create`. Un rattrapage de
            # vingt-quatre occurrences en vingt-quatre créations séparées ne
            # rend pas un résultat différent, il le rend vingt-quatre fois.
            vals_list = []
            while due <= horizon and opened < MAX_CATCH_UP:
                if plan.end_date and due > plan.end_date:
                    due = False
                    break
                vals_list.append(plan._prepare_request_vals(due))
                due = plan._next_occurrence(due)
                opened += 1
            if vals_list:
                works |= works.create(vals_list)
            if due and plan.end_date and due > plan.end_date:
                # Le terme est passé : le cédule n'a plus d'échéance, et cela
                # se voit sur l'écran plutôt que de se déduire d'une date qui
                # ne viendra jamais.
                due = False
            plan.next_date = due
        return works

    def action_generate_now(self):
        """Ouvrir tout de suite ce qui est dû, sans attendre la passe de nuit."""
        works = self._generate_due()
        if not works:
            raise UserError(
                _(
                    "Rien à ouvrir : aucune échéance de ces cédules ne tombe "
                    "avant %(horizon)s.",
                    horizon=fields.Date.context_today(self),
                )
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Travaux ouverts"),
            "res_model": "maintenance.request",
            "view_mode": "list,form",
            "domain": [("id", "in", works.ids)],
        }

    def action_view_requests(self):
        """🔴 La garde d'accès EST la première ligne, et elle n'est pas
        décorative.

        Toute méthode sans souligné initial est appelable par RPC : la vue
        n'est pas une barrière. Sans ce contrôle, `ensure_one()` ne lit rien,
        le dictionnaire d'action se compose sans toucher la base, et la
        méthode répond poliment à quelqu'un qui n'a aucun droit sur le cédule.
        Rien ne sort — le modèle visé lui est fermé par ses droits d'accès —
        mais une méthode qui rend un écran sur un enregistrement que
        l'appelant ne peut pas lire n'a aucune raison de répondre.

        Un contrôle d'autorité ne mesure pas ce qu'il n'a pas regardé : ce
        modèle a besoin de son propre jeu d'essai.
        """
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Travaux du cédule"),
            "res_model": "maintenance.request",
            "view_mode": "list,form",
            "domain": [("bf_plan_id", "=", self.id)],
            "context": {"search_default_active": True},
        }

    @api.model
    def _cron_generate_requests(self):
        """La passe de nuit.

        ⚠️ Elle ne dépend d'AUCUNE fermeture de billet : c'est tout l'objet du
        cédule préventif. Un cédule dont personne ne ferme les travaux continue d'ouvrir
        les suivants, et l'arriéré devient le signal.
        """
        plans = self.search([("next_date", "!=", False)])
        return plans._generate_due()

    # ── Le préventif échu ──

    is_overdue = fields.Boolean(
        string="Préventif échu",
        compute="_compute_is_overdue",
        search="_search_is_overdue",
        help="L'échéance est passée et le travail n'a pas encore été ouvert. "
             "C'est l'écart entre ce que le cédule promet et ce que la passe "
             "de nuit a fait.",
    )

    @api.depends("next_date")
    def _compute_is_overdue(self):
        """⚠️ Calculé NON stocké : il dépend de l'heure, pas d'une écriture.

        Un stocké figerait l'état au dernier passage et demanderait un cron
        pour rien. Et un non stocké SANS `search=` verrait son critère ignoré
        en silence — le filtre paraîtrait marcher et ne filtrerait rien. Même
        piège, même parade que `is_overdue` de la demande d'un occupant.
        """
        today = fields.Date.context_today(self)
        for plan in self:
            plan.is_overdue = bool(plan.next_date and plan.next_date < today)

    def _search_is_overdue(self, operator, value):
        if operator not in ("=", "!="):
            raise ValueError(_("Opérateur non pris en charge : %s") % operator)
        today = fields.Date.context_today(self)
        # 🔴 `normalize_domain` AVANT la négation. `["!"] + [A, B]` n'inverse
        # que A : l'opérateur est unaire, et le domaine devient « pas A ET B »
        # — ici « sans échéance ET échéance passée », donc rien du tout. Le
        # filtre « pas échu » rendait zéro cédule, et seul un test qui éprouve
        # les DEUX sens pouvait le dire.
        overdue = expression.normalize_domain(
            [("next_date", "!=", False), ("next_date", "<", today)]
        )
        wants_overdue = (operator == "=") == bool(value)
        return overdue if wants_overdue else ["!"] + overdue
