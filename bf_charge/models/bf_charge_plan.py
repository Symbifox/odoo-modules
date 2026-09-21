# -*- coding: utf-8 -*-
"""Le plan de charge : ce qu'on sait poser, et surtout ce qu'on ne sait pas poser.

Règle de conception : un calendrier qui n'affiche que ce qu'il sait placer ment
par omission. Sur une base réelle, la part qu'il savait placer était de quelques
pour cent. Le plan affiche donc toujours les deux nombres, et le second est
souvent le message.
"""
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

VERDICTS = [
    ("vert", "Vert"),
    ("jaune", "Jaune"),
    ("rouge", "Rouge"),
    ("inconnu", "Non déclaré"),
]


class BfChargePlan(models.Model):
    _name = "bf.charge.plan"
    _description = "Plan de charge"
    _order = "date_from desc, id desc"

    name = fields.Char(string="Nom", required=True, default="Plan de charge")
    company_id = fields.Many2one("res.company", string="Société", required=True,
                                 default=lambda self: self.env.company)
    user_id = fields.Many2one("res.users", string="Personne", required=True,
                              default=lambda self: self.env.user)
    contract_id = fields.Many2one("bf.charge.contract", string="Contrat de capacité",
                                  ondelete="set null")
    date_from = fields.Date(string="À partir du", required=True,
                            default=fields.Date.context_today)
    week_count = fields.Integer(string="Semaines d'horizon", required=True, default=8)
    build_date = fields.Datetime(string="Calculé le", readonly=True)

    week_ids = fields.One2many("bf.charge.plan.week", "plan_id", string="Semaines",
                               readonly=True)
    signal_ids = fields.One2many("bf.charge.plan.signal", "plan_id", string="Signaux",
                                 readonly=True)
    line_ids = fields.One2many("bf.charge.plan.line", "plan_id", string="Lignes",
                               readonly=True)
    # ⚠️ Un libellé par champ : Odoo avertit quand deux champs du même modèle
    # partagent le leur, et deux « Lignes » se confondent à la lecture.
    line_count = fields.Integer(string="Nombre de lignes", compute="_compute_line_count")

    capacity_per_week = fields.Float(string="Capacité déclarée par semaine", readonly=True)
    hours_placed = fields.Float(string="Heures posées", readonly=True)
    hours_unplaceable = fields.Float(string="Heures non plaçables", readonly=True)
    tasks_unplaceable = fields.Integer(string="Tâches non plaçables", readonly=True)
    hours_late = fields.Float(string="Heures en retard", readonly=True)
    hours_beyond = fields.Float(string="Heures au delà de l'horizon", readonly=True)
    hours_dormant = fields.Float(string="Heures dormantes", readonly=True)
    hours_template = fields.Float(string="Heures de gabarits", readonly=True)
    hours_rejected = fields.Float(string="Heures écartées (unité de vente)", readonly=True)
    tasks_without_charge = fields.Integer(string="Tâches sans charge", readonly=True)
    backlog_weeks = fields.Float(
        string="Carnet en semaines de capacité", readonly=True,
        help="Heures posées et non plaçables, divisées par la capacité nette déclarée.",
    )
    summary = fields.Text(string="Ce que le plan dit", readonly=True)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def action_build(self):
        for plan in self:
            plan._build()
        return True

    def _build(self):
        self.ensure_one()
        if self.week_count < 1:
            raise UserError(_("L'horizon doit valoir au moins une semaine."))
        self.week_ids.unlink()
        self.signal_ids.unlink()
        self.line_ids.unlink()
        lignes = []

        contrat = self.contract_id or self.env["bf.charge.contract"]._get_active(
            user=self.user_id, company=self.company_id)
        capacite = contrat.net_hours_per_week if contrat else 0.0

        lundi = self.date_from - timedelta(days=self.date_from.weekday())
        semaines = [lundi + timedelta(weeks=i) for i in range(self.week_count)]
        fin_horizon = semaines[-1] + timedelta(days=6)
        charge_par_semaine = dict.fromkeys(semaines, 0.0)
        taches_par_semaine = {jour: set() for jour in semaines}

        taches = self.env["project.task"].search([
            ("state", "not in", ["1_done", "1_canceled"]),
            ("company_id", "in", [False, self.company_id.id]),
        ])
        natures = {p.id: p.charge_kind for p in taches.project_id}

        totaux = dict.fromkeys(
            ["placees", "non_placables", "retard", "au_dela", "dormantes",
             "gabarits", "ecartees"], 0.0)
        compte = {"non_placables": 0, "sans_charge": 0}

        for tache in taches:
            nature = natures.get(tache.project_id.id, "dormant") if tache.project_id else "dormant"
            heures = tache.charge_hours or 0.0
            socle = self._socle_de_ligne(tache, nature)

            if tache.charge_source == "vente_ecartee":
                ecartees = tache.allocated_hours or 0.0
                totaux["ecartees"] += ecartees
                lignes.append(dict(socle, bucket="ecartee", hours=ecartees, share=1.0))

            if nature == "gabarit":
                totaux["gabarits"] += heures
                lignes.append(dict(socle, bucket="gabarit", hours=heures, share=1.0))
                continue
            if nature == "dormant":
                totaux["dormantes"] += heures
                lignes.append(dict(socle, bucket="dormant", hours=heures, share=1.0))
                continue

            if not heures:
                compte["sans_charge"] += 1
                lignes.append(dict(socle, bucket="sans_charge", hours=0.0, share=1.0))
                continue
            if not tache.charge_placeable:
                totaux["non_placables"] += heures
                compte["non_placables"] += 1
                lignes.append(dict(socle, bucket="non_placable", hours=heures, share=1.0))
                continue

            debut = tache.charge_date_start
            fin = tache.charge_date_end or debut
            if fin < lundi:
                totaux["retard"] += heures
                charge_par_semaine[semaines[0]] += heures
                taches_par_semaine[semaines[0]].add(tache.id)
                totaux["placees"] += heures
                lignes.append(dict(socle, bucket="retard", hours=heures, share=1.0,
                                   week_start=semaines[0]))
                continue
            if debut > fin_horizon:
                totaux["au_dela"] += heures
                lignes.append(dict(socle, bucket="au_dela", hours=heures, share=1.0))
                continue

            parts = self._repartir(debut, fin, semaines)
            if not parts:
                totaux["au_dela"] += heures
                lignes.append(dict(socle, bucket="au_dela", hours=heures, share=1.0))
                continue
            for jour, poids in parts.items():
                charge_par_semaine[jour] += heures * poids
                taches_par_semaine[jour].add(tache.id)
                lignes.append(dict(socle, bucket="pose", hours=heures * poids,
                                   share=poids, week_start=jour))
            totaux["placees"] += heures

        if lignes:
            self.env["bf.charge.plan.line"].create(lignes)

        Semaine = self.env["bf.charge.plan.week"]
        for index, jour in enumerate(semaines, start=1):
            Semaine.create({
                "plan_id": self.id,
                "sequence": index,
                "date_start": jour,
                "date_end": jour + timedelta(days=6),
                "hours": charge_par_semaine[jour],
                "task_count": len(taches_par_semaine[jour]),
                "capacity": capacite,
            })

        carnet = totaux["placees"] + totaux["non_placables"]
        self.write({
            "build_date": fields.Datetime.now(),
            "contract_id": contrat.id if contrat else False,
            "capacity_per_week": capacite,
            "hours_placed": totaux["placees"],
            "hours_unplaceable": totaux["non_placables"],
            "tasks_unplaceable": compte["non_placables"],
            "hours_late": totaux["retard"],
            "hours_beyond": totaux["au_dela"],
            "hours_dormant": totaux["dormantes"],
            "hours_template": totaux["gabarits"],
            "hours_rejected": totaux["ecartees"],
            "tasks_without_charge": compte["sans_charge"],
            "backlog_weeks": (carnet / capacite) if capacite else 0.0,
            "summary": self._redige_resume(totaux, compte, capacite, carnet, contrat),
        })
        self._build_signals(contrat)

    def action_open_lines(self):
        """Ouvre les lignes du plan sur le graphique, pas sur la liste.

        La question posée est « à quoi ressemble ma charge », pas « quelles sont
        mes 800 lignes ». Le graphique empile les sorts de charge par semaine :
        ce qui est posé et ce qui ne l'est pas se lisent côte à côte.
        """
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("bf_charge.action_bf_charge_plan_line")
        action["domain"] = [("plan_id", "=", self.id)]
        action["context"] = {"search_default_g_semaine": 1}
        action["display_name"] = _("Charge de « %(nom)s »") % {"nom": self.name}
        return action

    def _compute_line_count(self):
        for plan in self:
            plan.line_count = len(plan.line_ids)

    def _socle_de_ligne(self, tache, nature):
        """Ce qu'une ligne porte quel que soit le sort de sa charge."""
        self.ensure_one()
        projet = tache.project_id
        # 🔴 `partner_id` est lu sur le PROJET, pas sur la tâche : une tâche peut
        # porter un contact qui n'est pas le client du mandat, et c'est le mandat
        # qui décide si l'heure est du travail client.
        partenaire = projet.partner_id if projet else False
        return {
            "plan_id": self.id,
            "task_id": tache.id,
            "task_name": tache.name,
            "project_id": projet.id if projet else False,
            "partner_id": partenaire.id if partenaire else False,
            "is_client": bool(partenaire),
            "project_kind": nature,
            "source": tache.charge_source,
            "date_start": tache.charge_date_start or False,
            "date_end": tache.charge_date_end or False,
        }

    @staticmethod
    def _repartir(debut, fin, semaines):
        """Répartit une tâche sur les semaines de l'horizon, au prorata des jours.

        Rend un dictionnaire {lundi: poids}, dont les poids somment à 1 sur la
        partie de la tâche qui tombe dans l'horizon. Une tâche qui déborde voit
        son poids renormalisé : on ne place jamais plus que 100 % d'une tâche.
        """
        jours = {}
        total = 0
        for lundi in semaines:
            dimanche = lundi + timedelta(days=6)
            recouvre = (min(fin, dimanche) - max(debut, lundi)).days + 1
            if recouvre > 0:
                jours[lundi] = recouvre
                total += recouvre
        if not total:
            return {}
        return {lundi: n / total for lundi, n in jours.items()}

    def _redige_resume(self, totaux, compte, capacite, carnet, contrat):
        lignes = []
        if not contrat:
            lignes.append(_(
                "Aucun contrat de capacité en vigueur. Le plan pose les heures mais "
                "ne peut dire si elles tiennent : déclarer les heures par semaine "
                "est le préalable, pas une option."))
        elif not capacite:
            lignes.append(_(
                "Le contrat en vigueur ne porte pas d'heures par semaine, donc "
                "aucun signal de saturation ne peut se calculer."))
        lignes.append(_(
            "%(placees).0f h posées sur l'horizon, %(non_placables).0f h que le plan "
            "ne sait pas placer (%(nb)s tâches sans aucune date)."
        ) % {"placees": totaux["placees"], "non_placables": totaux["non_placables"],
             "nb": compte["non_placables"]})
        if totaux["placees"] + totaux["non_placables"]:
            part = 100.0 * totaux["non_placables"] / (totaux["placees"] + totaux["non_placables"])
            lignes.append(_("Le non plaçable pèse %(part).0f %% du carnet.") % {"part": part})
        if compte["sans_charge"]:
            lignes.append(_(
                "%(nb)s tâches vivantes ne portent aucune charge : elles ne sont "
                "comptées nulle part, et c'est un fait, pas un zéro."
            ) % {"nb": compte["sans_charge"]})
        if totaux["ecartees"]:
            lignes.append(_(
                "%(h).0f h écartées parce qu'elles viennent d'une unité de vente "
                "qui n'est pas l'heure."
            ) % {"h": totaux["ecartees"]})
        if totaux["gabarits"] or totaux["dormantes"]:
            lignes.append(_(
                "Hors plan : %(g).0f h de gabarits et %(d).0f h de projets dormants."
            ) % {"g": totaux["gabarits"], "d": totaux["dormantes"]})
        if capacite:
            lignes.append(_(
                "Au rythme déclaré de %(cap).1f h par semaine, le carnet vaut "
                "%(sem).1f semaines."
            ) % {"cap": capacite, "sem": carnet / capacite})
        return "\n".join(lignes)

    # ------------------------------------------------------------------
    # Signaux
    # ------------------------------------------------------------------
    def _build_signals(self, contrat):
        self.ensure_one()
        Signal = self.env["bf.charge.plan.signal"]
        for vals in self._collect_signals(contrat):
            vals["plan_id"] = self.id
            Signal.create(vals)

    def _collect_signals(self, contrat):
        self.ensure_one()
        today = fields.Date.context_today(self)
        depuis = today - timedelta(days=30)
        signaux = []

        # 🔴 `sudo()` + borne de société explicite : voir le contrat de capacité.
        lignes = self.env["account.analytic.line"].sudo().search([
            ("date", ">=", depuis), ("project_id", "!=", False),
            ("user_id", "=", self.user_id.id),
            ("company_id", "in", [False, self.company_id.id]),
        ])
        clients = set()
        heures_client = heures_interne = 0.0
        for ligne in lignes:
            partenaire = ligne.project_id.partner_id
            if partenaire:
                clients.add(partenaire.commercial_partner_id.id or partenaire.id)
                heures_client += ligne.unit_amount or 0.0
            else:
                heures_interne += ligne.unit_amount or 0.0

        plafond = contrat.max_concurrent_clients if contrat else 0
        signaux.append({
            "code": "clients",
            "name": _("Clients simultanés"),
            "measure": _("%(n)s clients touchés sur 30 jours") % {"n": len(clients)},
            "target": _("plafond déclaré : %(p)s") % {"p": plafond or _("aucun")},
            "verdict": self._verdict_seuil(len(clients), plafond),
        })

        total = heures_client + heures_interne
        part = (100.0 * heures_client / total) if total else 0.0
        vise = contrat.client_share_target if contrat else 0.0
        signaux.append({
            "code": "part_client",
            "name": _("Part client des heures livrées"),
            "measure": _("%(p).0f %% sur 30 jours (%(c).0f h client, %(i).0f h interne)")
            % {"p": part, "c": heures_client, "i": heures_interne},
            "target": _("part visée : %(v).0f %%") % {"v": vise} if vise else _("non déclarée"),
            "verdict": self._verdict_part(part, vise),
        })

        signaux.append(self._signal_banques())

        Task = self.env["project.task"]
        arrivees = Task.search_count([("create_date", ">=", fields.Datetime.to_datetime(depuis))])
        clotures = Task.search_count([
            ("state", "in", ["1_done", "1_canceled"]),
            ("date_last_stage_update", ">=", fields.Datetime.to_datetime(depuis)),
        ])
        signaux.append({
            "code": "flux",
            "name": _("Arrivées contre clôtures"),
            "measure": _("%(a)s ouvertes et %(c)s fermées sur 30 jours")
            % {"a": arrivees, "c": clotures},
            "target": _("le carnet ne doit pas grossir deux mois de suite"),
            "verdict": "vert" if clotures >= arrivees else "jaune",
        })

        capacite = self.capacity_per_week or (contrat.net_hours_per_week if contrat else 0.0)
        carnet = (self.hours_placed or 0.0) + (self.hours_unplaceable or 0.0)
        if capacite:
            semaines = carnet / capacite
            verdict = "vert" if semaines <= 8 else ("jaune" if semaines <= 16 else "rouge")
            mesure = _("%(s).1f semaines de capacité déclarée") % {"s": semaines}
        else:
            verdict = "inconnu"
            mesure = _("indéterminé : aucune capacité déclarée")
        signaux.append({
            "code": "carnet",
            "name": _("Carnet en semaines de capacité"),
            "measure": mesure,
            "target": _("%(h).0f h au carnet") % {"h": carnet},
            "verdict": verdict,
        })
        return signaux

    def _signal_banques(self):
        """Banques d'heures sous zéro. Muet si `bf_hour_bank` n'est pas installé."""
        self.ensure_one()
        modele = self.env["ir.model"].sudo().search([("model", "=", "hour.bank.client")], limit=1)
        if not modele:
            return {
                "code": "banques",
                "name": _("Banques d'heures"),
                "measure": _("module de banque d'heures absent"),
                "target": _("aucun solde à surveiller"),
                "verdict": "inconnu",
            }
        # ⚠️ Même règle : un sudo ne doit jamais élargir la portée en silence.
        banques = self.env["hour.bank.client"].sudo().search(
            [("company_id", "in", [False, self.company_id.id])])
        sous_zero = [b for b in banques if (b.current_balance or 0.0) < 0]
        noms = ", ".join("%s %.2f h" % (b.name, b.current_balance) for b in sous_zero)
        return {
            "code": "banques",
            "name": _("Banques d'heures sous zéro"),
            "measure": _("%(n)s sur %(t)s") % {"n": len(sous_zero), "t": len(banques)},
            "target": noms or _("aucune"),
            "verdict": "vert" if not sous_zero else ("jaune" if len(sous_zero) == 1 else "rouge"),
        }

    @staticmethod
    def _verdict_seuil(valeur, plafond):
        if not plafond:
            return "inconnu"
        if valeur > plafond:
            return "rouge"
        if valeur == plafond:
            return "jaune"
        return "vert"

    @staticmethod
    def _verdict_part(part, vise):
        if not vise:
            return "inconnu"
        if part >= vise:
            return "vert"
        if part >= vise * 0.75:
            return "jaune"
        return "rouge"


class BfChargePlanWeek(models.Model):
    _name = "bf.charge.plan.week"
    _description = "Semaine du plan de charge"
    _order = "plan_id, sequence"

    plan_id = fields.Many2one("bf.charge.plan", string="Plan", required=True,
                              ondelete="cascade", index=True)
    sequence = fields.Integer(string="Rang", default=1)
    date_start = fields.Date(string="Lundi", required=True)
    date_end = fields.Date(string="Dimanche", required=True)
    hours = fields.Float(string="Heures posées")
    task_count = fields.Integer(string="Tâches")
    capacity = fields.Float(string="Capacité déclarée")
    load_percent = fields.Float(string="Charge (%)", compute="_compute_load", store=False)
    verdict = fields.Selection(VERDICTS, string="Verdict", compute="_compute_load", store=False)

    @api.depends("hours", "capacity")
    def _compute_load(self):
        for rec in self:
            if not rec.capacity:
                rec.load_percent = 0.0
                rec.verdict = "inconnu"
                continue
            rec.load_percent = 100.0 * rec.hours / rec.capacity
            if rec.load_percent > 100.0:
                rec.verdict = "rouge"
            elif rec.load_percent > 80.0:
                rec.verdict = "jaune"
            else:
                rec.verdict = "vert"


class BfChargePlanSignal(models.Model):
    _name = "bf.charge.plan.signal"
    _description = "Signal de saturation"
    _order = "plan_id, id"

    plan_id = fields.Many2one("bf.charge.plan", string="Plan", required=True,
                              ondelete="cascade", index=True)
    code = fields.Char(string="Code", required=True)
    name = fields.Char(string="Signal", required=True)
    measure = fields.Char(string="Mesure")
    target = fields.Char(string="Repère")
    verdict = fields.Selection(VERDICTS, string="Verdict", default="inconnu")
