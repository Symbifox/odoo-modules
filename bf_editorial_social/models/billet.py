# -*- coding: utf-8 -*-
"""Un billet social, et la garantie qu'il ne part qu'une fois.

C'est le risque numéro un de ce genre de module : un travail périodique qui
reprend une file après une coupure réseau republie, si rien ne l'en empêche.
Trois verrous, et il en faut trois :

1. Une **clé d'idempotence** unique par canal, posée à la création.
2. Une **réservation en transaction séparée** : la file passe le billet à
   « en cours d'envoi » et valide AVANT le moindre appel sortant. Un second
   passage concurrent ne le voit donc plus comme à envoyer.
3. L'**identifiant distant** écrit dès la réponse, et un billet qui en porte
   un n'est jamais renvoyé, quel que soit son état.
"""

import logging
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SocialPost(models.Model):
    _name = "bf.social.post"
    _description = "Billet social"
    _inherit = ["mail.thread"]
    _order = "scheduled_datetime desc, id desc"

    name = fields.Char(string="Aperçu", compute="_compute_name", store=True)
    entry_id = fields.Many2one(
        "bf.editorial.entry", string="Entrée éditoriale", required=True,
        ondelete="cascade", index=True,
    )
    channel_id = fields.Many2one(
        "bf.social.channel", string="Canal", required=True,
        ondelete="restrict", index=True,
    )
    blurb_id = fields.Many2one("bf.editorial.blurb", string="Blurb")
    company_id = fields.Many2one(
        "res.company", related="channel_id.company_id", store=True, index=True,
    )
    lang_id = fields.Many2one("res.lang", related="channel_id.lang_id", store=True)

    body = fields.Text(string="Texte", required=True)
    body_length = fields.Integer(string="Caractères", compute="_compute_body_length")
    over_limit = fields.Boolean(string="Trop long", compute="_compute_body_length")
    link_url = fields.Char(string="Lien diffusé", readonly=True)
    tracker_id = fields.Many2one("link.tracker", string="Lien suivi", readonly=True)

    kind = fields.Selection(
        [("new", "Nouveauté"), ("recycle", "Fonds recyclé"), ("adhoc", "Ad hoc")],
        string="Nature", default="new", required=True,
    )
    scheduled_datetime = fields.Datetime(string="Diffusion prévue", tracking=True)
    published_datetime = fields.Datetime(string="Diffusé le", readonly=True, copy=False)

    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("scheduled", "Planifié"),
            ("sending", "En cours d'envoi"),
            ("sent", "Diffusé"),
            ("failed", "Échec"),
            ("cancelled", "Annulé"),
        ],
        string="État", default="draft", required=True, tracking=True, copy=False,
    )
    remote_id = fields.Char(string="Identifiant distant", readonly=True, copy=False)
    remote_url = fields.Char(string="URL publique", readonly=True, copy=False)
    error_message = fields.Text(string="Dernière erreur", readonly=True, copy=False)
    attempt_count = fields.Integer(string="Tentatives", default=0, readonly=True, copy=False)

    idempotency_key = fields.Char(
        string="Clé d'idempotence", required=True, readonly=True, copy=False,
        default=lambda self: str(uuid.uuid4()),
        help="Posée à la création et jamais modifiée. C'est elle qui empêche"
             " un second envoi du même billet sur le même canal.",
    )
    metric_ids = fields.One2many("bf.social.metric", "post_id", string="Mesures")

    _sql_constraints = [
        ("idempotency_unique",
         "UNIQUE(channel_id, idempotency_key)",
         "Ce billet existe déjà sur ce canal."),
        ("remote_unique",
         "UNIQUE(channel_id, remote_id)",
         "Ce billet distant est déjà rattaché sur ce canal."),
    ]

    @api.depends("body")
    def _compute_name(self):
        for p in self:
            t = (p.body or "").strip().replace("\n", " ")
            p.name = (t[:57] + "…") if len(t) > 58 else (t or _("(vide)"))

    @api.depends("body", "channel_id.body_limit")
    def _compute_body_length(self):
        for p in self:
            p.body_length = len(p.body or "")
            lim = p.channel_id.body_limit or 0
            p.over_limit = bool(lim and p.body_length > lim)

    # --- garde avant envoi ------------------------------------------------
    def _blocking_reasons(self):
        """Pourquoi ce billet ne peut pas partir. Liste vide = feu vert."""
        self.ensure_one()
        raisons = []
        if self.remote_id:
            raisons.append(_("Déjà diffusé (identifiant distant présent)."))
        if self.state in ("sent", "cancelled"):
            raisons.append(_("État « %s ».", dict(self._fields["state"].selection)[self.state]))
        if self.over_limit:
            raisons.append(_(
                "Texte trop long : %(n)s caractères pour une limite de %(l)s.",
                n=self.body_length, l=self.channel_id.body_limit,
            ))
        if not self.body or not self.body.strip():
            raisons.append(_("Texte vide."))
        # Un billet qui annonce un article et n'en porte pas le lien ne mène
        # nulle part : le lecteur voit une accroche et n'a rien à cliquer.
        # Vécu le 2026-08-29 : le premier billet parti sur Bluesky n'avait pas
        # de lien, parce que `action_queue` ne renseignait jamais `link_url`
        # alors que le connecteur savait déjà en faire une carte.
        # Un billet « ad hoc » ne parle pas forcément d'un article : il échappe
        # à cette exigence.
        if self.kind in ("new", "recycle") and not self.link_url:
            raisons.append(_(
                "Aucun lien vers l'article. Remettre le blurb en file pour"
                " qu'il en résolve un, ou renseigner « Lien diffusé »."))
        # La garde de l'article s'applique, mais PAS de la même façon selon
        # qu'on annonce une nouveauté ou qu'on repointe vers du déjà public.
        #
        # Pour une nouveauté, la garde de publication vaut telle quelle : on
        # n'annonce pas un texte que le module refuse encore de publier.
        #
        # Pour un article du fonds, elle serait absurde. Un billet public
        # depuis un an est déjà lu ; le bloquer sur de la dette de style
        # rendrait le recyclage inutilisable tant que tout le corpus n'est pas
        # remis à niveau. Ce qui compte alors est qu'il soit encore JUSTE :
        # version à jour, sources vivantes, langue diffusée bien publiée.
        entree = self.entry_id
        if self.kind == "new" and entree and not entree.preflight_ok:
            raisons.append(_("L'article n'a pas passé sa garde de pré-vol."))
        elif self.kind == "recycle" and entree:
            if not entree.published_date:
                raisons.append(_("Cet article n'a jamais été publié : rien à recycler."))
            if entree.version_drift:
                raisons.append(_(
                    "Le produit décrit a changé depuis le fact-check (%(a)s vers"
                    " %(b)s) : à corriger avant de le remettre en avant.",
                    a=entree.source_version, b=entree.current_version))
            if entree.dead_source_count:
                raisons.append(_(
                    "%s source(s) ne répondent plus.", entree.dead_source_count))
            if self.lang_id and not entree.version_ids.filtered(
                lambda v: v.lang_id == self.lang_id and v.state == "published"
            ):
                raisons.append(_(
                    "Le créneau « %s » n'est pas publié : le lien mènerait à"
                    " une version absente ou incomplète.", self.lang_id.name))
        if self.channel_id.credentials_state == "ko":
            raisons.append(_("Les identifiants du canal ont été refusés."))
        return raisons

    # --- envoi ------------------------------------------------------------
    def action_send_now(self):
        for p in self:
            raisons = p._blocking_reasons()
            if raisons:
                raise UserError(_(
                    "Diffusion refusée :\n\n%s", "\n".join("• " + r for r in raisons)))
            p._claim_and_send()
        return True

    def _claim_and_send(self):
        """Réserver le billet, puis l'envoyer.

        La réservation est validée AVANT l'appel sortant : c'est ce qui rend
        un second passage concurrent inoffensif.
        """
        self.ensure_one()
        if self.remote_id:
            return False
        self.write({"state": "sending", "attempt_count": self.attempt_count + 1})
        self.env.cr.commit()  # réservation rendue visible aux autres transactions

        try:
            res = self.channel_id._connector()._publish(self)
        except Exception as exc:            # noqa: BLE001 — on veut TOUT tracer
            _logger.exception("bf_editorial_social : échec de diffusion %s", self.id)
            self.write({"state": "failed", "error_message": str(exc)[:2000]})
            self.message_post(body=_("Échec de diffusion : %s", str(exc)[:400]))
            return False

        self.write({
            "state": "sent",
            "remote_id": res.get("remote_id"),
            "remote_url": res.get("url"),
            "published_datetime": fields.Datetime.now(),
            "error_message": False,
        })
        self.message_post(body=_("Diffusé sur %s.", self.channel_id.name))
        return True

    @api.model
    def _cron_send_scheduled(self):
        """Diffuser ce qui est dû. Un billet par transaction."""
        maintenant = fields.Datetime.now()
        dus = self.search([
            ("state", "=", "scheduled"),
            ("scheduled_datetime", "<=", maintenant),
            ("remote_id", "=", False),
        ])
        for p in dus:
            raisons = p._blocking_reasons()
            if raisons:
                p.write({"state": "failed",
                         "error_message": "\n".join(raisons)})
                p.message_post(body=_(
                    "Diffusion différée refusée :\n%s", "\n".join("• " + r for r in raisons)))
                continue
            p._claim_and_send()
        return True

    @api.model
    def _cron_fetch_metrics(self):
        """Rapatrier les mesures des billets diffusés."""
        Metric = self.env["bf.social.metric"]
        for p in self.search([("state", "=", "sent"), ("remote_id", "!=", False)]):
            try:
                mesures = p.channel_id._connector()._fetch_metrics(p)
            except Exception as exc:        # noqa: BLE001
                _logger.warning("mesures indisponibles pour %s : %s", p.id, exc)
                continue
            if mesures:
                Metric._record(p, mesures)
        return True
