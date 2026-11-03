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

    bf_email_rule_problem = fields.Text(
        string="Règle inopérante",
        compute="_compute_bf_email_rule_problem",
        help="Mesuré en direct, et non relu de l'aperçu : une règle peut être "
             "devenue inopérante après le dernier balayage, et l'aperçu d'avant "
             "resterait rassurant. Tant que cette ligne s'affiche, le module "
             "est installé et actif mais ne proposera aucune destruction.",
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

    _BF_EMAIL_FACADE = (
        "bf_email_unfiled_years",
        "bf_email_filed_years",
        "bf_email_propose_active",
        "bf_email_rule_problem",
        "bf_email_expired_count",
        "bf_email_held_summary",
        "bf_email_preview_taken_at",
        "bf_email_reconcile_floor",
    )

    @api.model
    def default_get(self, fields_list):
        """Alimente les champs de façade, que le formulaire ne calcule jamais.

        🔴 La page Paramètres est bâtie par `onchange`, et la couche web sème à
        `False` tout champ de la vue que `default_get` ne rend pas. Le
        `compute` d'un champ de façade ne tourne donc JAMAIS à l'affichage :
        mesuré, `onchange` rendait 0 là où le calendrier portait
        1 095 et 1 825 jours, pendant que `create({})` rendait bien 3 et 5.
        Le formulaire affichait donc 0, et l'enregistrement, par l'`inverse`,
        écrivait ce 0 sur la règle et éteignait le cron. Un seul enregistrement
        des Paramètres, depuis n'importe quel onglet d'un autre module,
        suffisait à vider la politique de conservation sans un mot. C'est ce
        qui est arrivé sur des bases réelles, tout l'écran de l'aperçu
        affichant par ailleurs des zéros rassurants pour la même raison.

        Le champ voisin `bf_email_closure_quiet_days` n'a jamais eu ce défaut :
        il porte `config_parameter`, donc `default_get` le rend.
        """
        res = super().default_get(fields_list)
        vise = [nom for nom in self._BF_EMAIL_FACADE if nom in fields_list]
        if not vise:
            return res
        BfEmail = self.env["bf.email"].sudo()
        unfiled = self._bf_email_regle("retention_bf_email_unfiled", muet=True)
        filed = self._bf_email_regle("retention_bf_email_filed", muet=True)
        cron = self._bf_email_cron()
        apercu = BfEmail._privacy_read_preview()
        valeurs = {
            "bf_email_unfiled_years": unfiled.active_retention_years if unfiled else 0,
            "bf_email_filed_years": filed.active_retention_years if filed else 0,
            "bf_email_propose_active": bool(cron and cron.sudo().active),
            "bf_email_rule_problem": "\n".join(BfEmail._privacy_rule_problems()),
            # ⚠️ Le plancher VIVANT, jamais celui de l'aperçu. L'aperçu est une
            # photo : avec un `bf_email.reconcile_days` relevé depuis, il
            # afficherait un seuil que le refus n'applique pas, et l'alerte
            # renvoie justement l'administrateur à ce chiffre. Les comptes
            # viennent de l'aperçu, le seuil non.
            "bf_email_reconcile_floor": BfEmail._privacy_reconcile_floor_days(),
            "bf_email_expired_count": (apercu or {}).get("expired") or 0,
            "bf_email_held_summary": self._bf_email_resume_retenues(apercu),
            "bf_email_preview_taken_at": (apercu or {}).get("taken_at") or "",
        }
        for nom in vise:
            res[nom] = valeurs[nom]
        return res

    def _bf_email_regle(self, cle, muet=False):
        regle = self.env.ref(f"bf_email_privacy.{cle}", raise_if_not_found=False)
        if not regle:
            if muet:
                return self.env["privacy.retention.calendar"].sudo()
            raise UserError(_(
                "La règle de conservation « %s » est introuvable. Réinstallez "
                "le module « Gestion des courriels, pont Vie privée ».", cle,
            ))
        return regle.sudo()

    # Conservé sous son ancien nom : d'autres méthodes de cet écran l'appellent.
    def _bf_email_rule(self, key):
        return self._bf_email_regle(key)

    @api.model
    def _bf_email_resume_retenues(self, apercu):
        if not apercu:
            return _("Aperçu jamais calculé. Utilisez « Simuler un passage » "
                     "ci-dessus.")
        try:
            retenues = apercu.get("held") or {}
            lignes = "\n".join(
                f"• {motif} : {nombre}"
                for motif, nombre in sorted(
                    retenues.items(), key=lambda kv: (-kv[1], kv[0])
                )
            )
        except (AttributeError, TypeError):
            # ⚠️ Ce résumé est désormais calculé dans `default_get`, donc sur le
            # chemin d'ouverture de la page Paramètres de TOUS les modules. Un
            # aperçu mémorisé malformé y lèverait et rendrait la page
            # inouvrable. On retombe sur « jamais calculé ».
            _logger.warning(
                "bf_email_privacy: aperçu mémorisé illisible, il sera "
                "reconstruit au prochain balayage"
            )
            return _("Aperçu illisible. Utilisez « Simuler un passage » "
                     "ci-dessus pour le reconstruire.")
        return lignes or _("Rien de conservé faute d'ancrage.")

    @api.depends_context("company")
    def _compute_bf_email_retention(self):
        unfiled = self._bf_email_regle("retention_bf_email_unfiled", muet=True)
        filed = self._bf_email_regle("retention_bf_email_filed", muet=True)
        for record in self:
            record.bf_email_unfiled_years = (
                unfiled.active_retention_years if unfiled else 0
            )
            record.bf_email_filed_years = (
                filed.active_retention_years if filed else 0
            )

    def _compute_bf_email_rule_problem(self):
        texte = "\n".join(self.env["bf.email"].sudo()._privacy_rule_problems())
        for record in self:
            record.bf_email_rule_problem = texte

    def _bf_email_verifier_duree(self, annees, regle):
        """Refuse une durée qui rendrait la règle inopérante.

        🔴 Sous le plancher de réconciliation IMAP, une durée ne RALENTIT pas
        la destruction : elle l'ARRÊTE. Le balayage s'interrompt plutôt que de
        détruire ce que l'import de la nuit re-créerait, et le module reste
        installé, actif, et muet. Pour suspendre les propositions, il y a une
        bascule faite pour ça, qui ne travestit pas la politique inscrite au
        calendrier.

        ⚠️ Le contrôle ne vise que les règles dont le sort final est la
        destruction. Une règle réglée sur l'anonymisation ou la conservation
        permanente est une politique légitime dont la durée ne pilote aucun
        balayage, et son 0 ne veut pas dire la même chose.
        """
        if regle.final_disposition != "destroy":
            return
        plancher = self.env["bf.email"].sudo()._privacy_reconcile_floor_days()
        total = (annees + regle.semi_active_retention_years) * 365
        if total < plancher:
            raise UserError(_(
                "La durée de « %(nom)s » doit couvrir au moins le plancher de "
                "réconciliation IMAP, soit %(plancher)s jours : en dessous, le "
                "balayage s'arrête et ce régime ne propose plus rien. Reçu : "
                "%(annees)s année(s), soit %(total)s jours. Pour suspendre les "
                "propositions, décochez « Proposition automatique ».",
                nom=regle.name, plancher=plancher, annees=annees, total=total,
            ))

    def _bf_email_poser_duree(self, cle, annees):
        """Écrit la durée sur la règle, et seulement si elle change.

        ⚠️ Ne rien écrire quand la valeur reçue est déjà celle de la règle :
        la page Paramètres est un seul formulaire portant tous les onglets de
        tous les modules, donc cet `inverse` tourne à chaque enregistrement,
        même venu d'un onglet qui n'a rien à voir.
        """
        # ⚠️ `muet=True` : depuis qu'Odoo sème les défauts de TOUS les champs
        # manquants, cet `inverse` tourne à chaque `res.config.settings.create()`
        # de n'importe quel module. Lever ici sur une règle supprimée rendrait
        # la page Paramètres de tout le monde inutilisable, et il n'y a de
        # toute façon rien à écrire. L'écran, lui, dit « règle absente ».
        regle = self._bf_email_regle(cle, muet=True)
        if not regle or regle.active_retention_years == annees:
            return
        self._bf_email_verifier_duree(annees, regle)
        regle.write({"active_retention_years": annees})
        _logger.warning(
            "bf_email_privacy: durée de conservation de %s portée de %s à %s "
            "an(s) par l'écran des Paramètres",
            regle.code, regle.active_retention_years, annees,
        )

    def _inverse_bf_email_unfiled_years(self):
        for record in self:
            record._bf_email_poser_duree(
                "retention_bf_email_unfiled", record.bf_email_unfiled_years
            )

    def _inverse_bf_email_filed_years(self):
        for record in self:
            record._bf_email_poser_duree(
                "retention_bf_email_filed", record.bf_email_filed_years
            )

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
        """Bascule le cron, et seulement si l'état demandé diffère.

        ⚠️ Même raison que pour les durées : un enregistrement venu d'un autre
        onglet ne doit pas pouvoir éteindre un cron de rétention Loi 25.
        """
        cron = self._bf_email_cron()
        if not cron:
            return
        for record in self:
            voulu = bool(record.bf_email_propose_active)
            if bool(cron.sudo().active) == voulu:
                continue
            cron.sudo().write({"active": voulu})
            _logger.warning(
                "bf_email_privacy: proposition automatique %s par les Paramètres",
                "ALLUMÉE" if voulu else "ÉTEINTE",
            )

    def _compute_bf_email_preview(self):
        """Relit le dernier balayage mémorisé, il n'en relance pas un.

        Le chiffre affiché est celui que le balayage a réellement produit, pas
        une seconde lecture qui pourrait en diverger : c'est
        `_privacy_scan_expired` lui-même qui l'écrit, à chaque passage du cron
        comme à chaque simulation. Et il est daté, pour qu'on sache de quand il
        parle.

        ⚠️ Même source que `default_get`, à dessein : l'un alimente le
        formulaire, l'autre les lectures par l'ORM, et deux calculs séparés
        finiraient par dire deux choses.
        """
        BfEmail = self.env["bf.email"].sudo()
        apercu = BfEmail._privacy_read_preview()
        resume = self._bf_email_resume_retenues(apercu)
        # ⚠️ Même raison que dans `default_get` : le seuil affiché est le seuil
        # appliqué, pas celui qu'une photo ancienne a mémorisé.
        plancher = BfEmail._privacy_reconcile_floor_days()
        for record in self:
            record.bf_email_expired_count = (apercu or {}).get("expired") or 0
            record.bf_email_held_summary = resume
            record.bf_email_preview_taken_at = (apercu or {}).get("taken_at") or ""
            record.bf_email_reconcile_floor = plancher

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
