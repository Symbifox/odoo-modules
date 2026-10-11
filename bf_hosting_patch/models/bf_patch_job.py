# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""bf.patch.job — un ordre d'appliquer des mises à jour sur UN système.

L'ordre **descend en étant demandé**. Odoo ne pousse rien et n'a aucun chemin
vers les machines : il dépose l'ordre dans une file, et l'agent le ramasse à sa
prochaine interrogation. Une machine éteinte ne rate pas l'ordre, elle le prend
au réveil — à condition qu'il ne soit pas périmé.

⚠️ L'ordre vise un `bf.patch.system`, PAS une `hosting.endpoint`. Le chapitre 8
du plan disait `endpoint_id`, mais le chapitre 20 a déplacé l'état vers le
système : le parc est en double amorçage, et « applique les correctifs sur
le poste X » n'a aucun sens tant qu'on n'a pas dit de quel côté.

La portée « redémarrer seulement » ne touche aucun paquet : elle existe parce
que demander un redémarrage en le greffant sur une mise à jour qu'on ne veut pas
serait faire deux choses pour en obtenir une.

TROIS CONSENTEMENTS, et il faut les trois :

1. Un humain identifié crée l'ordre dans Odoo (`requested_by`).
2. Le serveur refuse de le remettre si le dernier relevé du système ne dit pas
   `apply_allowed`.
3. L'agent refuse de l'exécuter si `/etc/symbifox/apply-allowed` n'existe pas
   sur la machine, quelle que soit la réponse du serveur.

Le troisième est le seul qui compte vraiment : il est LOCAL, posé à la main, et
retirable sans passer par Odoo. Les deux premiers sont là pour qu'une erreur
d'exploitation soit rattrapée avant d'atteindre la machine ; le troisième est là
pour qu'un Odoo compromis ne transforme pas un portable en machine briquée.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Un ordre que personne n'a ramassé finit par mourir plutôt que d'attendre une
# machine décommissionnée.
EXPIRE_AFTER_DAYS = 7

# ⚠️ Et l'agent, lui, refuse d'exécuter un ordre plus vieux que ça. Les deux
# bornes sont volontairement différentes : le serveur cesse de PROPOSER après
# 7 jours, l'agent cesse d'ACCEPTER après 24 h. Une machine rallumée après trois
# semaines ne doit pas appliquer une décision prise dans un autre monde.
STALE_ORDER_HOURS = 24

JOB_STATES = [
    ("queued", "Queued"),
    ("claimed", "Claimed"),
    ("running", "In progress"),
    ("done", "Applied"),
    ("failed", "Failed"),
    ("expired", "Expired"),
]

# Un état terminal ne se rouvre pas : l'agent qui rapporte deux fois le même
# ordre ne doit pas pouvoir réécrire son issue.
FINAL_STATES = ("done", "failed", "expired")

# 🔴 Le code que l'agent rend quand il refuse faute de consentement LOCAL
# (`/etc/symbifox/apply-allowed` effacé sur la machine). C'est un contrat
# partagé avec l'agent : le changer des deux côtés ou pas du tout.
LOCAL_CONSENT_REFUSED = 77


class BfPatchJob(models.Model):
    _name = "bf.patch.job"
    _description = "Update job"
    _order = "create_date desc, id desc"

    name = fields.Char(
        string="Reference", compute="_compute_name", store=True, readonly=True,
    )
    system_id = fields.Many2one(
        comodel_name="bf.patch.system", string="System", required=True,
        ondelete="cascade", index=True,
    )
    # Stocké et non pas simplement lié : c'est ce qui permet de grouper la file
    # par machine sans que chaque ligne aille chercher son système.
    endpoint_id = fields.Many2one(
        related="system_id.endpoint_id", string="Machine",
        store=True, readonly=True, index=True,
    )
    partner_id = fields.Many2one(
        related="system_id.partner_id", string="Client", store=True,
        readonly=True,
    )
    requested_by = fields.Many2one(
        comodel_name="res.users", string="Requested by", readonly=True,
        default=lambda self: self.env.user, required=True,
    )

    scope = fields.Selection(
        selection=[
            ("security", "Security only"),
            ("all", "All updates"),
            ("named", "Named packages"),
            ("reboot", "Reboot only"),
        ],
        string="Scope", required=True, default="security",
    )
    package_names = fields.Char(
        string="Packages",
        help="Separated by spaces or commas. \"Named packages\" scope only.",
    )
    reboot_after = fields.Selection(
        selection=[
            ("never", "Never"),
            ("if_required", "If the machine requires it"),
            ("always", "Always"),
        ],
        string="Reboot afterwards", required=True, default="never",
    )
    window_start = fields.Datetime(
        string="Not before",
        help="The job is not handed to the agent before this date. Empty "
             "= right away.",
    )

    state = fields.Selection(
        selection=JOB_STATES, string="State", default="queued", required=True,
        index=True, readonly=True, copy=False,
    )
    claimed_at = fields.Datetime(string="Claimed on", readonly=True,
                                 copy=False)
    finished_at = fields.Datetime(string="Finished on", readonly=True, copy=False)
    exit_code = fields.Integer(string="Exit code", readonly=True,
                               copy=False)
    output = fields.Text(string="Output", readonly=True, copy=False)
    packages_changed = fields.Integer(string="Packages changed", readonly=True,
                                      copy=False)

    # ------------------------------------------------------------------
    @api.depends("system_id", "scope", "create_date")
    def _compute_name(self):
        # Le libellé dans la langue de qui crée l'ordre : `selection` donnerait celui de
        # la source pour tout le monde, et le nom est stocké.
        labels = dict(self._fields["scope"]._description_selection(self.env))
        for job in self:
            system = job.system_id.name or "?"
            job.name = f"{system} — {labels.get(job.scope, job.scope)}"

    @api.constrains("scope", "package_names")
    def _check_named_packages(self):
        for job in self:
            if job.scope == "named" and not (job.package_names or "").strip():
                raise ValidationError(
                    _("A \"named packages\" scope without any package "
                      "would apply nothing.")
                )

    @api.constrains("scope", "reboot_after")
    def _check_reboot_scope(self):
        """⚠️ Un ordre de redémarrage qui ne redémarre jamais ne fait RIEN.

        `reboot_after` vaut « jamais » par défaut, ce qui est le bon défaut pour
        une mise à jour et le pire pour un redémarrage : l'ordre partirait, la
        machine le prendrait, le rapporterait `done`, et rien ne se passerait.
        Un succès qui n'a rien fait est pire qu'un refus.
        """
        for job in self:
            if job.scope == "reboot" and job.reboot_after == "never":
                raise ValidationError(
                    _("A \"reboot only\" job must say when to reboot: "
                      "\"always\" or \"if the machine requires it\". With "
                      "\"never\", it would do nothing.")
                )

    def package_list(self):
        """Les paquets nommés, découpés. Jamais une chaîne à recoller ailleurs."""
        self.ensure_one()
        raw = (self.package_names or "").replace(",", " ")
        return [name for name in raw.split() if name]

    # ------------------------------------------------------------------
    # Création : c'est un geste d'administration, pas une écriture ordinaire
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            system = self.env["bf.patch.system"].browse(vals.get("system_id"))
            system.endpoint_id._check_patch_manager()
            # ⚠️ Posé dans `create()`, jamais en `default=` : un défaut ne
            # survit pas à une création par RPC qui passe le champ à faux.
            vals.setdefault("requested_by", self.env.uid)
        return super().create(vals_list)

    def action_cancel(self):
        """Retirer un ordre de la file tant que personne ne l'a ramassé."""
        for job in self:
            job.system_id.endpoint_id._check_patch_manager()
            if job.state not in ("queued", "claimed"):
                raise UserError(
                    _("The job \"%s\" is no longer queued: it has already "
                      "been sent.",
                      job.name)
                )
            job.write({"state": "expired",
                       "finished_at": fields.Datetime.now(),
                       "output": _("Cancelled in Odoo by %s.",
                                   self.env.user.name)})

    # ------------------------------------------------------------------
    # Ce que l'agent vient chercher
    # ------------------------------------------------------------------
    @api.model
    def _claim_for(self, system):
        """Rendre `(ordre, refus)` — l'ordre à exécuter, ou la raison du refus.

        🔴 La garde répond AVANT le travail et sort. Un garde qui journalise et
        laisse passer ne garde rien : c'est la faute que l'audit du chapitre 17
        a trouvée deux fois dans ce module.
        """
        if not system.patch_managed:
            return self.browse(), "système non suivi"
        # Le consentement de la MACHINE, tel qu'elle l'a déclaré à son dernier
        # relevé. L'agent le revérifiera sur son disque ; ici on évite juste de
        # remettre un ordre qu'il refusera de toute façon.
        if not system.apply_allowed:
            return self.browse(), "consentement local absent sur la machine"

        now = fields.Datetime.now()
        jobs = self.sudo().search(
            [
                ("system_id", "=", system.id),
                ("state", "=", "queued"),
                "|", ("window_start", "=", False),
                ("window_start", "<=", now),
            ],
            # Le plus ancien d'abord : la file est une file.
            order="create_date asc, id asc", limit=1,
        )
        if not jobs:
            return self.browse(), ""
        jobs.write({"state": "claimed", "claimed_at": now})
        return jobs, ""

    def _payload_for_agent(self):
        """Ce que l'agent reçoit : le strict nécessaire pour agir."""
        self.ensure_one()
        return {
            "id": self.id,
            "system": self.system_id.name,
            # ⚠️ L'agent revérifie que l'ordre lui est bien destiné. Le jeton le
            # garantit déjà côté serveur ; ce champ est là pour que la machine
            # n'ait pas à faire confiance au serveur sur ce point précis.
            "machine_id": self.system_id.machine_id,
            "scope": self.scope,
            "packages": self.package_list(),
            "reboot_after": self.reboot_after,
            "created": self.create_date,
            "max_age_hours": STALE_ORDER_HOURS,
        }

    def _record_result(self, state, exit_code=None, output=None,
                       packages_changed=None):
        """Consigner l'issue rapportée par l'agent.

        ⚠️ Un état terminal ne se rouvre pas. Un agent qui rejoue son rapport —
        parce que sa réponse s'est perdue et qu'il a réessayé — ne doit pas
        pouvoir transformer un `failed` en `done`.
        """
        self.ensure_one()
        if self.state in FINAL_STATES:
            return False
        values = {"state": state}
        if state in FINAL_STATES:
            values["finished_at"] = fields.Datetime.now()
        if exit_code is not None:
            values["exit_code"] = exit_code
        if output is not None:
            values["output"] = output[:65536]
        if packages_changed is not None:
            values["packages_changed"] = packages_changed
        self.sudo().write(values)

        # 🔴 Un refus local VAUT révocation. La machine vient de prouver que le
        # consentement n'y est plus ; sans cette ligne, la fiche continuerait
        # d'afficher « application autorisée » — et de laisser partir des
        # ordres — jusqu'au relevé du lendemain, soit jusqu'à 24 heures de faux
        # feu vert. Le champ se remet à vrai tout seul au premier relevé qui
        # redéclare le fichier.
        if state == "failed" and exit_code == LOCAL_CONSENT_REFUSED:
            self.system_id.sudo().write({"apply_allowed": False})
        return True

    # ------------------------------------------------------------------
    @api.model
    def _cron_expire_jobs(self):
        """Un ordre que personne n'a ramassé finit par mourir.

        Vise `queued` ET `claimed` : un agent qui prend un ordre puis meurt
        avant de rapporter laisserait sinon une ligne « pris en charge » pour
        toujours, et la file cesserait d'être lisible.
        """
        limit = fields.Datetime.subtract(fields.Datetime.now(),
                                         days=EXPIRE_AFTER_DAYS)
        stale = self.sudo().search([
            ("state", "in", ("queued", "claimed")),
            ("create_date", "<", limit),
        ])
        if not stale:
            return 0
        lu = self.with_context(lang=self.env["bf.patch.system"]._bf_reader_lang())
        stale.write({
            "state": "expired",
            "finished_at": fields.Datetime.now(),
            "output": lu.env._("Expired: no agent ran it within %s days.",
                               EXPIRE_AFTER_DAYS),
        })
        _logger.info("bf_hosting_patch : %d ordre(s) périmé(s)", len(stale))
        return len(stale)
