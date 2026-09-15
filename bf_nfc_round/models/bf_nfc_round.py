"""La tournée, ses points, ses passages, et le guet qui alerte.

⚠️ L'horaire d'une tournée se lit dans le fuseau de la personne RESPONSABLE, pas
du serveur (UTC) ni de qui tape : « la ronde de 22 h » est 22 h là où elle a lieu.

⚠️ Une alerte par manquement, jamais une par passage du guet : le guet tourne tous
les quarts d'heure, et une activité qui se recrée à chaque passage devient du
bruit que tout le monde apprend à ignorer.
"""
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models

DUREE_SANS_HORAIRE = timedelta(hours=3)


class BfNfcRound(models.Model):
    _name = "bf.nfc.round"
    _description = "Tournée de pastilles"
    _inherit = ["mail.thread", "mail.activity.mixin", "bf.nfc.target.mixin"]
    _order = "name"

    name = fields.Char(string="Tournée", required=True, tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)
    responsible_id = fields.Many2one(
        "res.users", string="Responsable", required=True, default=lambda self: self.env.user,
        help="Reçoit les alertes, et donne le fuseau de l'horaire.")
    checkpoint_ids = fields.One2many("bf.nfc.round.checkpoint", "round_id", string="Points", copy=True)
    checkpoint_count = fields.Integer(compute="_compute_checkpoint_count")
    strict_order = fields.Boolean(string="Dans l'ordre", help="Un point tapé avant le précédent est marqué.")
    schedule = fields.Selection(
        [("none", "Sans horaire"), ("daily", "Chaque jour"), ("weekdays", "Du lundi au vendredi")],
        string="Horaire", default="none", required=True)
    start_hour = fields.Float(string="Commence à", default=22.0, help="Heure locale de la personne responsable.")
    duration_minutes = fields.Integer(string="Durée maximale (min)", default=60)
    last_missed_alert = fields.Date(string="Dernière alerte d'oubli", readonly=True, copy=False)
    run_ids = fields.One2many("bf.nfc.round.run", "round_id", string="Passages de tournée")

    def _nfc_domaine_pastilles(self):
        """Une tournée ne porte pas de pastille : ses POINTS en portent une chacun."""
        return [("res_model", "=", "bf.nfc.round.checkpoint"),
                ("res_id", "in", self.checkpoint_ids.ids)]

    def _nfc_comptes(self):
        """Les pastilles des POINTS, ramenées à leur tournée, en une requête."""
        points = {p.id: p.round_id.id for p in self.checkpoint_ids}
        groupes = self.env["bf.nfc.tag"].sudo()._read_group(
            [("res_model", "=", "bf.nfc.round.checkpoint"), ("res_id", "in", list(points))],
            ["res_id"], ["__count"])
        comptes = {}
        for res_id, nombre in groupes:
            tournee = points.get(res_id)
            comptes[tournee] = comptes.get(tournee, 0) + nombre
        return comptes

    def _nfc_fiches_a_etiqueter(self):
        return self.checkpoint_ids

    @api.depends("checkpoint_ids")
    def _compute_checkpoint_count(self):
        for tournee in self:
            tournee.checkpoint_count = len(tournee.checkpoint_ids)

    def _duree(self):
        self.ensure_one()
        if self.schedule == "none" and not self.duration_minutes:
            return DUREE_SANS_HORAIRE
        return timedelta(minutes=self.duration_minutes or 60)

    def _fuseau(self):
        return pytz.timezone(self.responsible_id.tz or self.env.user.tz or "UTC")

    def _fenetre_du_jour(self, maintenant):
        """(début, fin) de la fenêtre d'aujourd'hui en UTC naïf, ou None si pas d'horaire aujourd'hui."""
        self.ensure_one()
        if self.schedule == "none":
            return None
        fuseau = self._fuseau()
        local = pytz.utc.localize(maintenant).astimezone(fuseau)
        if self.schedule == "weekdays" and local.weekday() >= 5:
            return None
        heures = int(self.start_hour)
        minutes = int(round((self.start_hour - heures) * 60))
        debut_local = fuseau.localize(datetime.combine(local.date(), time(min(heures, 23), min(minutes, 59))))
        debut = debut_local.astimezone(pytz.utc).replace(tzinfo=None)
        return debut, debut + self._duree()

    def _alerter(self, resume, note):
        self.ensure_one()
        todo = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        self.activity_schedule(activity_type_id=todo.id if todo else False,
                               user_id=self.responsible_id.id, summary=resume, note=note)

    @api.model
    def _cron_guetter(self):
        maintenant = fields.Datetime.now()
        # 1. Les tournées commencées et pas finies dans leur durée.
        for passage in self.env["bf.nfc.round.run"].sudo().search([("state", "=", "running")]):
            tournee = passage.round_id
            if passage.date_start + tournee._duree() > maintenant:
                continue
            passage.state = "incomplete"
            manques = passage._points_manques()
            tournee._alerter(
                _("Tournée incomplète : %s", tournee.name),
                _("%(qui)s a commencé à %(debut)s et n'a pas tapé : %(points)s.",
                  qui=passage.user_id.name,
                  debut=fields.Datetime.context_timestamp(tournee, passage.date_start).strftime("%H:%M"),
                  points=", ".join(manques.mapped("name")) or "-"))
        # 2. Les tournées à horaire que personne n'a commencées dans leur fenêtre.
        for tournee in self.sudo().search([("schedule", "!=", "none")]):
            fenetre = tournee._fenetre_du_jour(maintenant)
            if not fenetre or maintenant < fenetre[1]:
                continue
            jour = pytz.utc.localize(fenetre[0]).astimezone(tournee._fuseau()).date()
            if tournee.last_missed_alert == jour:
                continue
            faite = self.env["bf.nfc.round.run"].sudo().search_count([
                ("round_id", "=", tournee.id),
                ("date_start", ">=", fenetre[0] - timedelta(minutes=30)),
                ("date_start", "<=", fenetre[1]),
            ])
            if faite:
                continue
            tournee.last_missed_alert = jour
            tournee._alerter(_("Tournée non faite : %s", tournee.name),
                             _("Personne n'a commencé la tournée prévue à %s.",
                               pytz.utc.localize(fenetre[0]).astimezone(tournee._fuseau()).strftime("%H:%M")))


class BfNfcRoundCheckpoint(models.Model):
    _name = "bf.nfc.round.checkpoint"
    _description = "Point de tournée"
    _order = "round_id, sequence, id"

    round_id = fields.Many2one("bf.nfc.round", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Point", required=True)
    place = fields.Char(string="Où")

    def _compute_display_name(self):
        for point in self:
            point.display_name = "%s · %s" % (point.round_id.name, point.name)


class BfNfcRoundRun(models.Model):
    _name = "bf.nfc.round.run"
    _description = "Passage d'une tournée"
    _order = "date_start desc, id desc"

    round_id = fields.Many2one("bf.nfc.round", required=True, ondelete="cascade", index=True)
    user_id = fields.Many2one("res.users", string="Par", required=True, index=True)
    date_start = fields.Datetime(string="Commencée", required=True)
    date_end = fields.Datetime(string="Finie")
    state = fields.Selection(
        [("running", "En cours"), ("done", "Complète"), ("incomplete", "Incomplète")],
        default="running", required=True, index=True)
    passage_ids = fields.One2many("bf.nfc.round.passage", "run_id", string="Points tapés")
    progress = fields.Char(string="Avancement", compute="_compute_progress")

    @api.depends("passage_ids", "round_id.checkpoint_ids")
    def _compute_progress(self):
        for passage in self:
            passage.progress = "%s / %s" % (len(passage.passage_ids.checkpoint_id), len(passage.round_id.checkpoint_ids))

    def _points_manques(self):
        self.ensure_one()
        return self.round_id.checkpoint_ids - self.passage_ids.checkpoint_id


class BfNfcRoundPassage(models.Model):
    _name = "bf.nfc.round.passage"
    _description = "Point tapé pendant une tournée"
    _order = "tapped_at, id"

    run_id = fields.Many2one("bf.nfc.round.run", required=True, ondelete="cascade", index=True)
    checkpoint_id = fields.Many2one("bf.nfc.round.checkpoint", required=True, ondelete="cascade")
    user_id = fields.Many2one("res.users", string="Par", required=True)
    tapped_at = fields.Datetime(string="Tapé à", required=True)
    offline = fields.Boolean(string="Envoyé en différé")
    out_of_order = fields.Boolean(string="Hors ordre")
