"""Écran d'administration de la rétention de l'inbox unifiée.

Les deux durées ne vivent pas dans `ir.config_parameter` mais sur les règles
du calendrier de conservation : c'est là qu'un vérificateur va les chercher,
avec leur base légale à côté, et c'est la valeur que le registre cite ensuite.
Cet écran en est une façade — il lit et écrit les mêmes enregistrements.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # ── Durées (façade sur privacy.retention.calendar) ───────────────────────
    bf_email_unfiled_years = fields.Integer(
        string="Courriel non versé à un dossier (années)",
        compute="_compute_bf_email_retention",
        inverse="_inverse_bf_email_unfiled_years",
        readonly=False,
        help="Conservation d'une ligne bf.email sans enregistrement lié, à "
             "compter de la réception. Écrit la règle COR-BFMAIL-1 du "
             "calendrier de conservation.",
    )
    bf_email_filed_years = fields.Integer(
        string="Courriel versé à un dossier (années)",
        compute="_compute_bf_email_retention",
        inverse="_inverse_bf_email_filed_years",
        readonly=False,
        help="Conservation d'une ligne bf.email liée à un dossier, à compter "
             "de la CLÔTURE du projet qui la gouverne. Écrit la règle "
             "COR-BFMAIL-2 du calendrier de conservation.",
    )

    bf_email_closure_quiet_days = fields.Integer(
        string="Silence avant clôture (jours)",
        config_parameter="bf_email_privacy.closure_quiet_days",
        default=180,
        help="Un dossier rangé (étape repliée ou archivé) n'est tenu pour clos "
             "qu'après ce silence : plus aucun message ni feuille de temps. "
             "Aucun signal déclaratif ne dit la clôture — archiver "
             "sert à désencombrer, l'étape « Terminé » couvre des dossiers "
             "encore actifs, et la « Date de fin » est une fin PRÉVUE.",
    )

    # ── Opération ────────────────────────────────────────────────────────────
    bf_email_propose_active = fields.Boolean(
        string="Proposition automatique",
        compute="_compute_bf_email_propose_active",
        inverse="_inverse_bf_email_propose_active",
        readonly=False,
        help="Bascule le cron quotidien « Courriels : proposer la destruction "
             "du contenu échu (Loi 25) ». Ce cron ne détruit rien : il alimente "
             "une campagne de destruction laissée en révision, qu'il faut "
             "approuver puis exécuter à la main.",
    )
    bf_email_batch_size = fields.Integer(
        string="Lignes par passage",
        config_parameter="bf_email_privacy.batch_size",
        default=500,
        help="Plafond de lignes proposées par passage du cron. La passe "
             "suivante reprend là où celle-ci s'arrête.",
    )

    # ── Aperçu (même lecture que le cron) ────────────────────────────────────
    bf_email_expired_count = fields.Integer(
        string="Échus",
        compute="_compute_bf_email_preview",
        help="Lignes que la prochaine passe détruirait, plafond compris.",
    )
    bf_email_held_summary = fields.Text(
        string="Conservés faute d'ancrage",
        compute="_compute_bf_email_preview",
        help="Ce que le cron refuse de détruire, et pourquoi. Un projet clos "
             "sans date de clôture se répare en posant sa date de fin.",
    )
    bf_email_preview_taken_at = fields.Char(
        string="Aperçu daté du",
        compute="_compute_bf_email_preview",
        help="Un aperçu est une PHOTO, pas une mesure en direct : le balayage "
             "coûte quelques secondes sur les données réelles et la page "
             "Paramètres calcule tous ses onglets à l'ouverture. Le cron le "
             "rafraîchit à chaque passage ; « Simuler un passage » aussi.",
    )
    bf_email_reconcile_floor = fields.Integer(
        string="Plancher de réconciliation IMAP (jours)",
        compute="_compute_bf_email_preview",
        help="Sous cette durée, une ligne détruite est re-créée par la "
             "réconciliation IMAP au passage suivant. Le cron s'arrête plutôt "
             "que d'alimenter la boucle.",
    )

    # ------------------------------------------------------------------

    def _bf_email_rule(self, key):
        rule = self.env.ref(f"bf_email_privacy.{key}", raise_if_not_found=False)
        if not rule:
            raise UserError(_(
                "La règle de conservation « %s » est introuvable. Réinstallez "
                "le module « Gestion des courriels — pont Vie privée ».", key,
            ))
        return rule

    @api.depends_context("company")
    def _compute_bf_email_retention(self):
        unfiled = self.env.ref(
            "bf_email_privacy.retention_bf_email_unfiled", raise_if_not_found=False
        )
        filed = self.env.ref(
            "bf_email_privacy.retention_bf_email_filed", raise_if_not_found=False
        )
        for record in self:
            record.bf_email_unfiled_years = unfiled.active_retention_years if unfiled else 0
            record.bf_email_filed_years = filed.active_retention_years if filed else 0

    def _inverse_bf_email_unfiled_years(self):
        for record in self:
            self._bf_email_rule("retention_bf_email_unfiled").sudo().write({
                "active_retention_years": record.bf_email_unfiled_years,
            })

    def _inverse_bf_email_filed_years(self):
        for record in self:
            self._bf_email_rule("retention_bf_email_filed").sudo().write({
                "active_retention_years": record.bf_email_filed_years,
            })

    def _bf_email_cron(self):
        return self.env.ref(
            "bf_email_privacy.cron_bf_email_destroy_expired",
            raise_if_not_found=False,
        )

    def _compute_bf_email_propose_active(self):
        cron = self._bf_email_cron()
        for record in self:
            record.bf_email_propose_active = bool(cron and cron.sudo().active)

    def _inverse_bf_email_propose_active(self):
        cron = self._bf_email_cron()
        if not cron:
            return
        for record in self:
            cron.sudo().write({"active": record.bf_email_propose_active})

    def _compute_bf_email_preview(self):
        """Relit le dernier balayage mémorisé — il n'en relance pas un.

        Le chiffre affiché est celui que le balayage a réellement produit, pas
        une seconde lecture qui pourrait en diverger : c'est `_privacy_scan_expired`
        lui-même qui l'écrit, à chaque passage du cron comme à chaque
        simulation. Et il est daté, pour qu'on sache de quand il parle.
        """
        BfEmail = self.env["bf.email"]
        preview = BfEmail._privacy_read_preview()
        never = _(
            "Aperçu jamais calculé. Utilisez « Simuler un passage » ci-dessus."
        )
        for record in self:
            if not preview:
                record.bf_email_expired_count = 0
                record.bf_email_held_summary = never
                record.bf_email_preview_taken_at = ""
                record.bf_email_reconcile_floor = (
                    BfEmail._privacy_reconcile_floor_days()
                )
                continue
            held = preview.get("held") or {}
            record.bf_email_expired_count = preview.get("expired") or 0
            record.bf_email_held_summary = "\n".join(
                f"• {motif} : {count}"
                for motif, count in sorted(
                    held.items(), key=lambda kv: (-kv[1], kv[0])
                )
            ) or _("Rien de conservé faute d'ancrage.")
            record.bf_email_preview_taken_at = preview.get("taken_at") or ""
            record.bf_email_reconcile_floor = preview.get("floor_days") or 0

    # ------------------------------------------------------------------

    def action_bf_email_dry_run(self):
        """Balaie, rafraîchit l'aperçu, ne propose ni ne détruit rien."""
        self.ensure_one()
        preview = self.env["bf.email"]._privacy_refresh_preview()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "sticky": False,
                "title": _("Simulation — rien n'a été proposé ni détruit"),
                "message": _(
                    "%(expired)s ligne(s) échue(s), %(held)s conservée(s) faute "
                    "d'ancrage. Le tableau ci-dessous reprend ces chiffres à la "
                    "prochaine ouverture des Paramètres — on ne recharge pas la "
                    "page ici, ce qui effacerait les durées non enregistrées.",
                    expired=preview["expired"],
                    held=sum(preview["held"].values()),
                ),
            },
        }

    def action_bf_email_open_campaigns(self):
        """Ouvre les campagnes de destruction de l'inbox unifiée."""
        self.ensure_one()
        rules = (
            self._bf_email_rule("retention_bf_email_unfiled")
            | self._bf_email_rule("retention_bf_email_filed")
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Campagnes — inbox unifiée"),
            "res_model": "privacy.destruction.campaign",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("retention_calendar_id", "in", rules.ids)],
        }

    def action_bf_email_open_calendar(self):
        """Ouvre les deux règles dans le calendrier de conservation."""
        self.ensure_one()
        rules = (
            self._bf_email_rule("retention_bf_email_unfiled")
            | self._bf_email_rule("retention_bf_email_filed")
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Règles de conservation — inbox unifiée"),
            "res_model": "privacy.retention.calendar",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("id", "in", rules.ids)],
        }
