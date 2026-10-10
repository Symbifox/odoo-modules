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

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SocialPost(models.Model):
    _name = "bf.social.post"
    _description = "Social post"
    _inherit = ["mail.thread"]
    _order = "scheduled_datetime desc, id desc"

    name = fields.Char(string="Preview", compute="_compute_name", store=True)
    entry_id = fields.Many2one(
        "bf.editorial.entry", string="Editorial entry", required=True,
        ondelete="cascade", index=True,
    )
    channel_id = fields.Many2one(
        "bf.social.channel", string="Channel", required=True,
        ondelete="restrict", index=True,
    )
    blurb_id = fields.Many2one("bf.editorial.blurb", string="Blurb")
    company_id = fields.Many2one(
        "res.company", related="channel_id.company_id", store=True, index=True,
    )
    lang_id = fields.Many2one("res.lang", related="channel_id.lang_id", store=True)

    body = fields.Text(string="Text", required=True)
    body_length = fields.Integer(string="Characters", compute="_compute_body_length")
    over_limit = fields.Boolean(string="Too long", compute="_compute_body_length")
    link_url = fields.Char(string="Shared link", readonly=True)
    tracker_id = fields.Many2one("link.tracker", string="Tracked link", readonly=True)

    kind = fields.Selection(
        [("new", "New article"), ("recycle", "Recycled backlist"), ("adhoc", "Ad hoc")],
        string="Kind", default="new", required=True,
    )
    scheduled_datetime = fields.Datetime(string="Scheduled for", tracking=True)
    published_datetime = fields.Datetime(string="Published on", readonly=True, copy=False)

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("scheduled", "Scheduled"),
            ("sending", "Sending"),
            ("sent", "Published"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        string="Status", default="draft", required=True, tracking=True, copy=False,
    )
    remote_id = fields.Char(string="Remote ID", readonly=True, copy=False)
    remote_url = fields.Char(string="Public URL", readonly=True, copy=False)
    error_message = fields.Text(string="Last error", readonly=True, copy=False)
    attempt_count = fields.Integer(string="Attempts", default=0, readonly=True, copy=False)

    idempotency_key = fields.Char(
        string="Idempotency key", required=True, readonly=True, copy=False,
        default=lambda self: str(uuid.uuid4()),
        help="Set at creation and never changed. It prevents the same "
             "post from being sent twice on the same channel.",
    )
    metric_ids = fields.One2many("bf.social.metric", "post_id", string="Metrics")

    _sql_constraints = [
        ("idempotency_unique",
         "UNIQUE(channel_id, idempotency_key)",
         "This post already exists on this channel."),
        ("remote_unique",
         "UNIQUE(channel_id, remote_id)",
         "This remote post is already linked on this channel."),
    ]

    @api.depends("body")
    def _compute_name(self):
        for p in self:
            t = (p.body or "").strip().replace("\n", " ")
            p.name = (t[:57] + "…") if len(t) > 58 else (t or _("(empty)"))

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
            raisons.append(_("Already published (remote ID present)."))
        if self.state in ("sent", "cancelled"):
            raisons.append(_("Status \"%s\".", dict(
                self._fields["state"]._description_selection(self.env))[self.state]))
        if self.over_limit:
            raisons.append(_(
                "Text too long: %(n)s characters for a limit of %(l)s.",
                n=self.body_length, l=self.channel_id.body_limit,
            ))
        if not self.body or not self.body.strip():
            raisons.append(_("Empty text."))
        # Un billet qui annonce un article et n'en porte pas le lien ne mène
        # nulle part : le lecteur voit une accroche et n'a rien à cliquer.
        # Vécu le 2026-08-29 : le premier billet parti sur Bluesky n'avait pas
        # de lien, parce que `action_queue` ne renseignait jamais `link_url`
        # alors que le connecteur savait déjà en faire une carte.
        # Un billet « ad hoc » ne parle pas forcément d'un article : il échappe
        # à cette exigence.
        if self.kind in ("new", "recycle") and not self.link_url:
            raisons.append(_(
                "No link to the article. Queue the blurb again so it "
                "resolves one, or fill in \"Shared link\"."))
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
            raisons.append(_("The article did not pass its preflight guard."))
        elif self.kind == "recycle" and entree:
            if not entree.published_date:
                raisons.append(_("This article was never published: "
                                 "nothing to recycle."))
            if entree.version_drift:
                raisons.append(_(
                    "The product described has changed since the "
                    "fact-check (%(a)s to %(b)s): fix it before promoting "
                    "it again.",
                    a=entree.source_version, b=entree.current_version))
            if entree.dead_source_count:
                raisons.append(_(
                    "%s source(s) no longer respond.", entree.dead_source_count))
            if self.lang_id and not entree.version_ids.filtered(
                lambda v: v.lang_id == self.lang_id and v.state == "published"
            ):
                raisons.append(_(
                    "The \"%s\" slot is not published: the link would "
                    "lead to a missing or incomplete version.", self.lang_id.name))
        if self.channel_id.credentials_state == "ko":
            raisons.append(_("The channel's credentials were rejected."))
        return raisons

    # --- envoi ------------------------------------------------------------
    def action_send_now(self):
        for p in self:
            raisons = p._blocking_reasons()
            if raisons:
                raise UserError(_(
                    "Publishing refused:\n\n%s", "\n".join("• " + r for r in raisons)))
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
            self.message_post(body=_("Publishing failed: %s", str(exc)[:400]))
            return False

        self.write({
            "state": "sent",
            "remote_id": res.get("remote_id"),
            "remote_url": res.get("url"),
            "published_datetime": fields.Datetime.now(),
            "error_message": False,
        })
        self.message_post(body=_("Published on %s.", self.channel_id.name))
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
            # Le travail planifié n'a la langue de personne : ce qu'il écrit au
            # billet (motif de refus, échec, confirmation) se lit par l'équipe.
            # Sans lecteur retenu, l'anglais source : `_()` retomberait sinon sur la
            # langue d'OdooBot, et la note mêlerait deux langues.
            p = p.with_context(lang=p._langue_lecteur() or "en_US")
            raisons = p._blocking_reasons()
            if raisons:
                p.write({"state": "failed",
                         "error_message": "\n".join(raisons)})
                # `p.env._` et non `_` : `_` lit la langue de `self`, le modèle
                # du cron, pas celle épinglée sur `p`.
                p.message_post(body=p.env._(
                    "Scheduled publishing refused:\n%s", "\n".join("• " + r for r in raisons)))
                continue
            p._claim_and_send()
        return True

    def _langue_lecteur(self):
        """La langue de qui a créé le billet, sinon celle de la société.

        Jamais celle d'OdooBot ni d'un compte partagé. None : aucune ne convient,
        l'appelant écrit alors dans la langue source (l'anglais).
        """
        self.ensure_one()
        installees = {code for code, _nom in self.env["res.lang"].get_installed()}
        createur = self.create_uid
        langues = []
        if createur and createur.active and not createur.share and createur.id != SUPERUSER_ID:
            langues.append(createur.lang)
        langues.append((self.company_id or self.env.company).partner_id.lang)
        return next((lang for lang in langues if lang in installees), None)

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
