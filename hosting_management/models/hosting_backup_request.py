# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Demander une sauvegarde maintenant, sans qu'Odoo n'exécute quoi que ce soit.

POURQUOI CE MODÈLE EXISTE PLUTÔT QU'UN APPEL DIRECT
Les scripts de `ops/backup/` tournent en `livv` sur l'hôte : ils lisent les mots de passe
des conteneurs, dumpent toutes les bases, touchent les clés privées du proxy inverse et
écrivent dans S3. Aujourd'hui l'hôte parle à Odoo, jamais l'inverse, et aucun conteneur
Odoo ne voit `/var/run/docker.sock`. Un webhook sur l'hôte ferait de n'importe quelle
faille Odoo une exécution de commande sur la machine.

🔴 Odoo n'exécute rien et ne transmet AUCUNE commande. Il écrit une demande, qui ne porte
qu'un identifiant de service. Un agent minuté sur l'hôte vient la chercher et résout
LUI-MÊME, dans une table en dur, quel script lancer et sur quelle instance. Même doctrine
que `bf_devops` : Odoo porte les décisions, l'exécution reste aux scripts.

Ce que la demande ne porte jamais : un nom de script, un chemin, un argument, une commande.
Si un jour l'un de ces champs apparaît ici, la doctrine est rompue.

CE QUI REFUSE UNE DEMANDE, ET POURQUOI
- Hors du groupe gestionnaire d'hébergement : déclencher une sauvegarde met un locataire en
  mode maintenance et charge le disque. Ce n'est pas une lecture.
- Service qui n'est pas attendu dans la chaîne maison : rien à déclencher, et une demande
  sur un service sauvegardé par son fournisseur resterait éternellement en attente.
- Une demande déjà vivante pour le même service : deux passages concurrents sur la même
  instance se piétinent (c'est le défaut que le verrou de la vérification Odoo a fermé).
- Un délai de garde après la dernière demande : le bouton est à un clic, et une sauvegarde
  n'est pas gratuite.

L'EXPIRATION N'EST PAS UNE COQUETTERIE
Une demande que personne ne prend doit mourir. Sans cela, l'agent relancé des heures plus
tard exécuterait une demande que son auteur a oubliée, peut-être pendant la fenêtre
nocturne, sur une instance qu'il croyait au repos.
"""
import logging

from odoo import _, api, fields, models
from psycopg2.errors import UniqueViolation

from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)

# Seuls ces états comptent comme « une demande est vivante pour ce service ».
_ETATS_VIVANTS = ("pending", "claimed", "running")


class HostingBackupRequest(models.Model):
    _name = "hosting.backup.request"
    # `_name` ET `_inherit` : c'est la creation d'un modele qui emprunte le fil de
    # discussion, pas une extension de `mail.thread` (celle-la reinitialiserait
    # TOUS les modeles a chaque montee). Le fil sert a l'etat suivi et au compte
    # rendu que l'agent y depose.
    _inherit = ["mail.thread"]
    _description = "Backup Request"
    _order = "requested_date desc, id desc"
    _rec_name = "name"

    name = fields.Char(
        string="Reference", required=True, copy=False, readonly=True,
        default=lambda self: _("New"),
    )
    service_id = fields.Many2one(
        "hosting.service", string="Service", required=True, ondelete="cascade",
        index=True,
    )
    partner_id = fields.Many2one(
        related="service_id.partner_id", string="Client", store=True, readonly=True,
    )
    # Ce que l'agent lit pour savoir DE QUOI on parle. Jamais ce qu'il doit faire.
    docker_container = fields.Char(
        related="service_id.docker_container", string="Container", readonly=True,
    )
    software_id = fields.Many2one(
        related="service_id.software_id", string="Software", readonly=True,
    )
    state = fields.Selection(
        [
            ("pending", "Waiting for the agent"),
            ("claimed", "Taken by the agent"),
            ("running", "Running"),
            ("done", "Done"),
            ("failed", "Failed"),
            ("expired", "Expired"),
            ("cancelled", "Cancelled"),
        ],
        string="Status", default="pending", required=True, index=True, tracking=True,
    )
    requested_by_id = fields.Many2one(
        "res.users", string="Requested by", required=True, readonly=True,
        default=lambda self: self.env.user,
    )
    requested_date = fields.Datetime(
        string="Requested on", required=True, readonly=True,
        default=fields.Datetime.now,
    )
    claimed_date = fields.Datetime(string="Taken on", readonly=True)
    finished_date = fields.Datetime(string="Finished on", readonly=True)
    # Quelle machine a pris la demande. Un parc à plusieurs hôtes (une seconde machine
    # porte un service de secours) doit pouvoir dire qui a fait quoi.
    agent_host = fields.Char(string="Agent host", readonly=True)
    message = fields.Text(string="Agent message", readonly=True)
    run_id = fields.Many2one(
        "hosting.backup.run", string="Backup run", readonly=True, ondelete="set null",
    )

    _sql_constraints = [
        ("name_uniq", "unique(name)", "A backup request reference must be unique."),
    ]

    # 🔴 Le garde « une seule demande vivante par service » est un `search` fait
    # APRES le `create` : deux clics dans la meme seconde, ou deux `call_kw` en
    # vol, et chaque transaction ne voit aucune demande vivante. Deux demandes
    # naissent, l'agent les prend toutes deux et joue le script deux fois sur la
    # meme instance. Seule la base peut trancher, par un index unique PARTIEL :
    # une demande vivante au plus par service, les demandes closes ne genant pas.
    _INDEX_VIVANTE = "hosting_backup_request_une_vivante_par_service"

    def init(self):
        super().init()
        self.env.cr.execute(
            "SELECT indexname FROM pg_indexes WHERE indexname = %s",
            (self._INDEX_VIVANTE,),
        )
        if not self.env.cr.fetchone():
            self.env.cr.execute(
                f'CREATE UNIQUE INDEX "{self._INDEX_VIVANTE}" '
                "ON hosting_backup_request (service_id) "
                "WHERE state IN ('pending', 'claimed', 'running')"
            )

    # ── Paramètres ──────────────────────────────────────────────────────────
    def _cooldown_minutes(self):
        """Délai de garde entre deux demandes pour le même service."""
        valeur = self.env["ir.config_parameter"].sudo().get_param(
            "hosting.backup_request_cooldown_minutes", "30"
        )
        try:
            return max(0, int(valeur))
        except (TypeError, ValueError):
            _logger.warning(
                "hosting.backup_request_cooldown_minutes n'est pas un entier (%r), "
                "30 minutes retenues", valeur
            )
            return 30

    def _expiry_minutes(self):
        """Au-delà de ce délai, une demande que personne n'a prise expire."""
        return self._minutes_param("hosting.backup_request_expiry_minutes", 60)

    def _stuck_minutes(self):
        """Au-delà de ce délai, une demande PRISE mais jamais rendue est perdue.

        L'agent peut mourir entre le moment où il prend une demande et celui où il
        rend compte : conteneur tué, machine redémarrée, délai dépassé sans que le
        rapport parte. Sans ce second délai, la demande resterait « prise » pour
        toujours, et le garde « une seule demande vivante par service » empêcherait
        d'en refaire une. Le service serait donc bloqué par un fantôme.

        Généreux par défaut : une sauvegarde Nextcloud prend onze minutes, un dump
        Odoo avec vérification davantage. Deux heures ne coupent rien de vivant.
        """
        return self._minutes_param("hosting.backup_request_stuck_minutes", 120)

    def _minutes_param(self, cle, defaut):
        valeur = self.env["ir.config_parameter"].sudo().get_param(cle, str(defaut))
        try:
            return max(1, int(valeur))
        except (TypeError, ValueError):
            _logger.warning(
                "%s n'est pas un entier (%r), %s minutes retenues",
                cle, valeur, defaut,
            )
            return defaut

    # ── Garde-fous ──────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        # 🔴 Le contrôle de groupe est ici et non seulement dans la vue : un bouton
        # caché reste appelable par `/web/dataset/call_kw`, et créer une demande
        # déclenche du travail réel sur l'hôte.
        if not self.env.su and not self.env.user.has_group(
            "hosting_management.group_hosting_manager"
        ):
            raise AccessError(_(
                "Only the hosting manager can ask for a backup: the request makes a "
                "tenant go into maintenance mode on the host."
            ))
        for vals in vals_list:
            if vals.get("name", _("New")) in (_("New"), False, ""):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "hosting.backup.request"
                ) or _("New")
        try:
            with self.env.cr.savepoint():
                demandes = super().create(vals_list)
                # Le `flush` force l'ecriture, donc la verification de l'index
                # partiel, ICI plutot qu'a la validation : sans lui, la collision
                # remonterait beaucoup plus tard, loin de sa cause.
                demandes.flush_recordset()
        except UniqueViolation as e:
            if self._INDEX_VIVANTE in str(e):
                raise ValidationError(_(
                    "A backup request is already under way for this service. Two "
                    "passes on the same instance get in each other's way."
                )) from e
            raise
        for demande in demandes:
            demande._verifier_recevabilite()
        return demandes

    def _verifier_recevabilite(self):
        """Refuse ce qui ne peut pas aboutir, au moment de la création.

        Le refus est une exception et non un état : une demande irrecevable qui dormirait
        en base se lirait comme une sauvegarde en route.
        """
        self.ensure_one()
        service = self.service_id
        if service.state not in ("active", "to_disconnect"):
            raise ValidationError(_(
                "%s is not an active service: there is nothing to back up.",
                service.display_name,
            ))
        if service.backup_expectation != "pipeline":
            raise ValidationError(_(
                "%(service)s is not backed up by the in-house pipeline (%(expectation)s): "
                "nothing on the host would answer this request.",
                service=service.display_name,
                expectation=dict(
                    service._fields["backup_expectation"]._description_selection(self.env)
                ).get(service.backup_expectation, service.backup_expectation),
            ))
        if not service.docker_container:
            raise ValidationError(_(
                "%s has no container recorded: the agent would not know which instance "
                "to back up.", service.display_name,
            ))
        # 🔴 Sans serveur nommé, la demande est servie au PREMIER agent qui sonde,
        # y compris sur une machine où le conteneur n'existe pas : le script sort
        # alors en 0 sans instantané et Odoo écrit « échouée » sur une demande
        # saine que le vrai hôte ne verra jamais. Et un serveur dont le nom d'hôte
        # est vide n'est vu par AUCUN agent : la demande meurt au bout d'une heure
        # sur « personne n'est venu », sans que rien ne l'ait dit au clic. Les deux
        # se refusent ici, où le motif est lisible.
        if not service.server_id:
            raise ValidationError(_(
                "%s is not attached to a server: no agent could tell whether the "
                "container is on its machine.", service.display_name,
            ))
        if not (service.server_id.hostname or "").strip():
            raise ValidationError(_(
                "The server %s has no hostname: no agent can recognise itself as "
                "the one that should answer.", service.server_id.display_name,
            ))
        vivante = self.search([
            ("service_id", "=", service.id),
            ("state", "in", _ETATS_VIVANTS),
            ("id", "!=", self.id),
        ], limit=1)
        if vivante:
            raise ValidationError(_(
                "A backup request is already under way for %(service)s (%(ref)s). Two "
                "passes on the same instance get in each other's way.",
                service=service.display_name, ref=vivante.name,
            ))
        garde = self._cooldown_minutes()
        if garde:
            depuis = fields.Datetime.subtract(fields.Datetime.now(), minutes=garde)
            recente = self.search([
                ("service_id", "=", service.id),
                ("requested_date", ">=", depuis),
                ("id", "!=", self.id),
            ], limit=1)
            if recente:
                raise ValidationError(_(
                    "%(service)s was already asked for a backup less than %(minutes)s "
                    "minutes ago (%(ref)s). A backup is not free.",
                    service=service.display_name, minutes=garde, ref=recente.name,
                ))

    # Ce qui ne se réécrit jamais depuis une requête d'usager.
    #
    # Les quatre premiers sont le fait daté lui-même : le service demandé, qui l'a
    # demandé, quand, et sa référence.
    #
    # 🔴 Les trois suivants sont des champs `related`, et c'est le piège : `readonly`
    # est une consigne d'INTERFACE, pas une garde serveur. Odoo propage l'écriture
    # d'un `related` vers sa source (`_inverse_related`), donc un `write` par RPC sur
    # `docker_container` depuis une demande de sauvegarde changerait le conteneur du
    # SERVICE, et `partner_id`, stocké, ferait mentir la demande sur son client.
    # Même famille que les champs `related` stockés de bf_bookmarks.
    _CHAMPS_FIGES = frozenset({
        "service_id", "requested_by_id", "requested_date", "name",
        "partner_id", "docker_container", "software_id",
    })

    def write(self, vals):
        if not self.env.su and self._CHAMPS_FIGES & set(vals):
            raise AccessError(_(
                "A backup request does not get rewritten: create another one."
            ))
        return super().write(vals)

    def action_cancel(self):
        """Annuler une demande que l'agent n'a pas encore prise."""
        if not self.env.su and not self.env.user.has_group(
            "hosting_management.group_hosting_manager"
        ):
            raise AccessError(_("Only the hosting manager can cancel a backup request."))
        for demande in self:
            if demande.state != "pending":
                raise UserError(_(
                    "%(ref)s is no longer waiting (%(state)s): the host has it.",
                    ref=demande.name, state=demande.state,
                ))
        self.write({"state": "cancelled", "finished_date": fields.Datetime.now()})
        return True

    # ── Cron ────────────────────────────────────────────────────────────────
    @api.model
    def _cron_expire_requests(self):
        """Faire mourir les demandes que personne n'est venu chercher.

        Sans cela, un agent arrêté puis relancé le lendemain exécuterait des demandes
        oubliées, peut-être en pleine fenêtre nocturne.
        """
        maintenant = fields.Datetime.now()
        limite = fields.Datetime.subtract(maintenant, minutes=self._expiry_minutes())
        perimees = self.search([
            ("state", "=", "pending"), ("requested_date", "<", limite),
        ])
        if perimees:
            perimees.write({
                "state": "expired",
                "finished_date": maintenant,
                "message": _("No agent came for this request in time."),
            })

        # Les demandes que l'agent a PRISES et n'a jamais rendues. Sans elles, le
        # garde « une seule demande vivante par service » bloquerait ce service pour
        # toujours derrière un fantôme.
        limite_bloquee = fields.Datetime.subtract(
            maintenant, minutes=self._stuck_minutes()
        )
        # ⚠️ `claimed_date` NULL : en SQL, `NULL < X` ne vaut pas vrai, donc une
        # demande prise sans date echappait a cette branche et restait un fantome
        # permanent, exactement l'etat que la branche devait fermer. On retombe
        # alors sur la date de la demande, qui est toujours posee.
        bloquees = self.search([
            ("state", "in", ("claimed", "running")),
            "|",
            "&", ("claimed_date", "!=", False),
            ("claimed_date", "<", limite_bloquee),
            "&", ("claimed_date", "=", False),
            ("requested_date", "<", limite_bloquee),
        ])
        if bloquees:
            bloquees.write({
                "state": "failed",
                "finished_date": maintenant,
                "message": _(
                    "The agent took this request and never reported back. "
                    "Nothing says whether the backup ran: check the host's log."
                ),
            })
        total = len(perimees) + len(bloquees)
        if total:
            _logger.info(
                "Backup requests closed by the cron: %s expired, %s stuck",
                len(perimees), len(bloquees),
            )
        return total
