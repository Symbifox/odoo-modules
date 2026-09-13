"""« Faire le ménage » : traiter ce qui dort depuis longtemps.

Mesuré sur une base réelle le 2026-09-13 : **1 420 des 1 528 lignes en boîte ont
plus de 90 jours**, et aucune n'a plus d'un an. Ce n'est plus une boîte de
réception, c'est un dépôt.

⚠️ L'assistant COMPTE avant de faire, et le compte se recalcule quand on change
le nombre de jours. Un bouton qui traite 1 420 lignes sans montrer combien est
un bouton qu'on n'ose pas appuyer, donc un bouton qui ne sert pas.

⚠️ Il ne touche ni au serveur IMAP ni à l'état « lu ». « Traité » veut dire
« hors de ma boîte » et rien d'autre, ici comme ailleurs dans le module, et
c'est réversible d'un « Remettre en boîte ».
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

PLAFOND = 5000


class BfEmailCleanup(models.TransientModel):
    _name = "bf.email.cleanup"
    _description = "Traiter les courriels qui dorment"

    days = fields.Integer(
        string="Plus vieux que (jours)",
        default=90,
        required=True,
    )
    keep_questions = fields.Boolean(
        string="Épargner ceux qui posent une question",
        default=True,
        help="Le signal `is_question` vient de Dabbish & Kraut 2006 : une "
             "question a environ quatre fois plus de chances d'attendre une "
             "réponse. C'est le seul filet, et il est coché par défaut.",
    )
    keep_to_me = fields.Boolean(
        string="Épargner ceux qui me sont adressés directement",
        default=False,
        help="`is_to_me` : je suis dans « À », pas seulement en copie.",
    )
    match_count = fields.Integer(
        string="Courriels visés",
        compute="_compute_match_count",
    )
    sample = fields.Text(
        string="Aperçu",
        compute="_compute_match_count",
    )

    @api.depends("days", "keep_questions", "keep_to_me")
    def _compute_match_count(self):
        for wiz in self:
            lignes = wiz._targets(limit=10)
            wiz.match_count = wiz.env["bf.email"].search_count(wiz._domain())
            wiz.sample = "\n".join(
                "%s  %s  %s" % (
                    fields.Datetime.to_string(l.date)[:10] if l.date else "?",
                    (l.email_from or "")[:40],
                    (l.subject or "(sans objet)")[:50],
                )
                for l in lignes
            ) or _("Rien ne correspond.")

    def _domain(self):
        self.ensure_one()
        jours = max(int(self.days or 0), 1)
        limite = fields.Datetime.subtract(fields.Datetime.now(), days=jours)
        domaine = self.env["bf.email"]._inbox_domain() + [
            ("user_id", "=", self.env.uid),
            ("date", "<", limite),
        ]
        if self.keep_questions:
            domaine.append(("is_question", "=", False))
        if self.keep_to_me:
            domaine.append(("is_to_me", "=", False))
        return domaine

    def _targets(self, limit=None):
        self.ensure_one()
        return self.env["bf.email"].search(
            self._domain(), limit=limit or PLAFOND, order="date asc")

    def action_clean(self):
        self.ensure_one()
        lignes = self._targets()
        if not lignes:
            raise UserError(_("Rien ne correspond : la boîte est déjà à jour."))
        nombre = len(lignes)
        # ⚠️ `action_archive` du module, pas celui d'Odoo : c'est lui qui pose
        # `is_handled` et déclenche la recopie IMAP. `active` reste vrai, la
        # ligne n'est pas jetée.
        lignes.action_archive()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Ménage fait"),
                "message": _("%s courriel(s) sortis de la boîte. "
                             "« Remettre en boîte » les ramène.", nombre),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
