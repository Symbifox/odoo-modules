"""Le persona : dater la fin de la relation, proposer la destruction, détruire en entier.

Une règle, un ancrage :

* **PER-001** : trois ans après la fin de la relation d'affaires, alignés sur
  COR-001 (le persona est tiré des courriels ; il ne doit ni leur survivre ni
  mourir avant eux).
* **La fin de la relation, c'est le silence de la personne** : le dernier
  courriel reçu d'elle ou envoyé à elle. Pour un persona de société, de
  n'importe qui de la société. Pour le titulaire du compte (une société, ou un
  particulier facturé à son nom), sa dernière facture compte aussi. À défaut,
  la création du persona.
* **Jamais tant qu'une facture adressée au contact reste impayée** : la
  relation n'est pas finie, et la cote de payeur sert encore.

Un signal déclaratif (projet archivé, étape « Terminé ») n'a jamais tenu en
pratique : seul le silence discrimine.

Le pont **propose**, il ne détruit pas : même décision que pour les autres ponts.
"""
import logging
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# États d'une campagne de destruction encore vivante.
_LIVE_CAMPAIGN_STATES = ["draft", "scanning", "review", "approved", "executing"]

# Une ligne ignorée À LA MAIN par le responsable retient le persona un an : il a
# décidé de le garder. Le libellé est celui de `action_skip` du socle. Les lignes
# ignorées par l'exécuteur (plus dues) et les échecs ne retiennent rien.
_MANUAL_SKIP_MESSAGE = "Ignoré manuellement"
_MANUAL_SKIP_HOLD_DAYS = 365

# Ce qu'une personne de l'extérieur peut écrire : un courriel, ou un message du
# portail.
_INBOUND_TYPES = ("email", "comment")
# Ce que nous lui envoyons. « notification » compte : en pratique, de vrais
# envois en portent le type (ordre du jour, avis de sécurité). Les notes
# internes sont écartées par leur sous-type, pas par leur type.
_OUTBOUND_TYPES = ("comment", "email", "email_outgoing", "notification")


class ContactPersona(models.Model):
    _inherit = "contact.persona"

    # ------------------------------------------------------------------
    # Destruction complète
    # ------------------------------------------------------------------

    def unlink(self):
        """Toute suppression passe par ici : campagne, gestionnaire, contact supprimé.

        Les indicateurs et les règles de copie partent par l'ORM, pas par la
        cascade SQL de `persona_id` : c'est l'`unlink` d'un modèle qui balaie
        ses pièces jointes, et la cascade le saute. Le fil du persona (où vit
        l'historique suivi de la qualité de payeur) part avec `mail.thread`.
        """
        children = self.sudo().with_context(active_test=False)
        children.kpi_ids.unlink()
        children.cc_rule_ids.unlink()
        return super().unlink()

    # ------------------------------------------------------------------
    # Règle et ancrage
    # ------------------------------------------------------------------

    @api.model
    def _privacy_rule(self):
        """PER-001 — le persona d'un contact. Archivée, elle ne pilote plus rien."""
        rule = self.env.ref("bf_persona_privacy.retention_persona", raise_if_not_found=False)
        return rule if rule and rule.active else self.env["privacy.retention.calendar"].browse()

    @api.model
    def _privacy_rules(self):
        """La règle, active ou non : une campagne déjà ouverte sur elle reste la nôtre."""
        rule = self.env.ref("bf_persona_privacy.retention_persona", raise_if_not_found=False)
        return rule if rule else self.env["privacy.retention.calendar"].browse()

    def _privacy_people(self):
        """``(contacts, adresses)`` qui désignent la personne du persona.

        La fiche, et pour une société toutes ses gens ; plus les autres fiches
        qui portent la même adresse, archivées comprises : en pratique, un
        doublon écrit souvent encore quand la fiche du persona se tait. Les
        adresses servent aux messages sans auteur reconnu.
        """
        self.ensure_one()
        Partner = self.env["res.partner"].sudo().with_context(active_test=False)
        partner = self.partner_id
        people = Partner.search([("id", "child_of", partner.id)]) if partner.is_company else partner
        emails = {e for e in people.mapped("email_normalized") if e}
        if emails:
            people |= Partner.search([("email_normalized", "in", list(emails))])
        people -= Partner.browse(self._privacy_our_partner_ids())
        return people, sorted(emails)

    @api.model
    def _privacy_our_partner_ids(self):
        """Nos usagers internes, et les fiches de nos sociétés (un envoi peut
        partir sous le nom de l'entreprise)."""
        return list(set(self._internal_partner_ids())
                    | set(self.env["res.company"].sudo().search([]).partner_id.ids))

    @staticmethod
    def _privacy_address_domain(field, emails):
        """`field` porte l'une des adresses (exactement, ou entre chevrons)."""
        leaves = []
        for email in emails:
            leaves.append([(field, "=ilike", email)])
            leaves.append([(field, "ilike", f"<{email}>")])
        if not leaves:
            return [("id", "=", 0)]
        return ["|"] * (len(leaves) - 1) + [leaf for group in leaves for leaf in group]

    def _privacy_is_account_holder(self):
        """Une société, ou un particulier qui est son propre titulaire de compte."""
        self.ensure_one()
        partner = self.partner_id
        return partner.is_company or partner.commercial_partner_id == partner

    def _privacy_last_signs(self):
        """``{source: date}`` des derniers signes de vie, sources muettes absentes."""
        self.ensure_one()
        partner = self.partner_id
        if not partner:
            return {}
        people, emails = self._privacy_people()
        ours = self._privacy_our_partner_ids()
        Message = self.env["mail.message"].sudo().with_context(active_test=False)
        signs = {}

        def last(Model, domain):
            row = Model.search(domain, order="date desc", limit=1)
            return row.date.date() if row and row.date else False

        # Reçu : écrit par la personne, ou par son adresse quand l'auteur n'a pas
        # été reconnu.
        received = [
            last(Message, [("message_type", "in", _INBOUND_TYPES), ("author_id", "in", people.ids)]),
            last(Message, [("message_type", "=", "email"), ("author_id", "=", False)]
                 + self._privacy_address_domain("email_from", emails)) if emails else False,
        ]
        # Envoyé : par nous, à elle, qu'elle soit destinataire ou abonnée
        # (la notification par courriel est la trace de l'envoi réel).
        sent = [last(Message, [
            ("author_id", "in", ours),
            ("message_type", "in", _OUTBOUND_TYPES),
            "|", ("subtype_id", "=", False), ("subtype_id.internal", "=", False),
            "|", ("partner_ids", "in", people.ids),
            ("notification_ids", "any", [
                ("res_partner_id", "in", people.ids),
                ("notification_type", "=", "email"),
                ("notification_status", "not in", ("exception", "bounce", "canceled")),
            ]),
        ])]
        # L'inbox unifiée garde des courriels qui n'ont jamais atteint un fil.
        if "bf.email" in self.env:
            Email = self.env["bf.email"].sudo().with_context(active_test=False)
            received.append(last(Email, [("direction", "=", "in"), "|",
                                         ("partner_id", "in", people.ids),
                                         ("author_id", "in", people.ids)]))
            if emails and "bf.email.participant" in self.env:
                # Les adresses d'un courriel, une par ligne : comparaison exacte,
                # « nada.ada@ » ne prolonge pas « ada@ ».
                Participant = self.env["bf.email.participant"].sudo()
                for direction, roles, bucket in (("in", ("from",), received),
                                                 ("out", ("to", "cc"), sent)):
                    rows = Participant.search([("address", "in", emails), ("role", "in", roles)])
                    if rows:
                        bucket.append(last(Email, [("direction", "=", direction),
                                                   ("id", "in", rows.email_id.ids)]))
            sent.append(last(Email, [("direction", "=", "out"), ("partner_id", "in", people.ids)]))
        received = [d for d in received if d]
        sent = [d for d in sent if d]
        if received:
            signs["inbound"] = max(received)
        if sent:
            signs["outbound"] = max(sent)
        if self._privacy_is_account_holder():
            invoice = self.env["account.move"].sudo().with_context(active_test=False).search(
                [("commercial_partner_id", "=", partner.commercial_partner_id.id),
                 ("move_type", "in", ("out_invoice", "out_refund")),
                 ("state", "=", "posted"), ("invoice_date", "!=", False)],
                order="invoice_date desc", limit=1)
            if invoice:
                signs["invoice"] = invoice.invoice_date
        return signs

    def _privacy_anchor(self):
        """Date d'ancrage PER-001 : le dernier signe de vie, jamais avant la création."""
        self.ensure_one()
        created = fields.Date.to_date(self.create_date)
        signs = self._privacy_last_signs()
        return max([created] + list(signs.values()))

    def _privacy_has_unpaid_invoice(self):
        """Une facture adressée au contact (à la société ou à ses gens, à ses
        doublons) reste due."""
        self.ensure_one()
        if not self.partner_id:
            return False
        people, _emails = self._privacy_people()
        return bool(self.env["account.move"].sudo().with_context(active_test=False).search_count(
            [("partner_id", "in", people.ids),
             ("move_type", "=", "out_invoice"), ("state", "=", "posted"),
             ("payment_state", "in", ("not_paid", "partial"))], limit=1))

    def _privacy_still_due(self, rule):
        """Le persona relève-t-il ENCORE de `rule` ? Relu à l'exécution : une
        campagne peut attendre des semaines, la personne a pu réécrire."""
        self.ensure_one()
        if rule != self._privacy_rules():
            return True
        if not rule.active:
            # Archiver PER-001 suspend le régime : une campagne déjà approuvée ne
            # détruit plus rien sous elle.
            return False
        days = rule.total_retention_days
        if not days or days <= 0:
            return False
        if self._privacy_has_unpaid_invoice():
            return False
        return self._privacy_anchor() + timedelta(days=days) <= fields.Date.context_today(self)

    def _privacy_expiry_date(self, rule):
        self.ensure_one()
        return self._privacy_anchor() + timedelta(days=rule.total_retention_days)

    # ------------------------------------------------------------------
    # Balayage
    # ------------------------------------------------------------------

    @api.model
    def _privacy_held_ids(self):
        """Personas retenus : ligne en attente dans une campagne vivante, ou
        ignorée à la main depuis moins d'un an (voir `_MANUAL_SKIP_MESSAGE`)."""
        Line = self.env["privacy.destruction.campaign.line"].sudo()
        since = fields.Datetime.now() - timedelta(days=_MANUAL_SKIP_HOLD_DAYS)
        return set(Line.search([
            ("res_model", "=", self._name),
            "|",
            "&", ("state", "=", "pending"), ("campaign_id.state", "in", _LIVE_CAMPAIGN_STATES),
            "&", "&", ("state", "=", "skipped"), ("error_message", "=", _MANUAL_SKIP_MESSAGE),
            ("write_date", ">=", since),
        ]).mapped("res_id"))

    @api.model
    def _privacy_scan_expired(self, limit=None):
        """``(seaux_par_règle, retenus_par_motif)`` — sans rien détruire."""
        held = defaultdict(int)
        buckets = defaultdict(lambda: self.browse())
        Persona = self.sudo().with_context(active_test=False)
        rule = self._privacy_rule()
        days = rule.total_retention_days if rule else 0
        if not days or days <= 0:
            # Balayer avec une durée nulle proposerait TOUT à la destruction.
            held[_("règle PER-001 absente ou sans durée")] += Persona.search_count([])
            return dict(buckets), dict(held)
        cutoff = fields.Date.context_today(self) - timedelta(days=days)
        already = self._privacy_held_ids()
        # Personne ne peut être échu si son persona est plus jeune que la règle.
        rows = Persona.search(
            [("create_date", "<", cutoff), ("id", "not in", list(already))],
            order="create_date asc")
        ours = set(self._privacy_our_partner_ids())
        for persona in rows:
            if not persona.partner_id:
                held[_("persona sans contact")] += 1
                continue
            if persona.partner_id.id in ours:
                # Un usager interne ou une de nos sociétés : son silence ne se
                # mesure pas par ce qu'il nous écrit. Jamais proposé.
                held[_("fiche d'un usager ou d'une de nos sociétés")] += 1
                continue
            if persona._privacy_anchor() >= cutoff:
                continue
            if persona._privacy_has_unpaid_invoice():
                held[_("facture impayée")] += 1
                continue
            buckets[rule.id] |= persona
            if limit and len(buckets[rule.id]) >= limit:
                break
        return dict(buckets), dict(held)

    # ------------------------------------------------------------------
    # Proposition
    # ------------------------------------------------------------------

    @api.model
    def _cron_privacy_propose_destruction(self, limit=None, dry_run=False):
        """Le cron du pont : dépose les personas échus dans une campagne. Ne détruit RIEN."""
        if limit is None:
            try:
                limit = int(self.env["ir.config_parameter"].sudo().get_param(
                    "bf_persona_privacy.batch_size", "500"))
            except (TypeError, ValueError):
                limit = 500
        buckets, held = self._privacy_scan_expired(limit=limit)
        if held:
            _logger.info(
                "bf_persona_privacy: %s persona(s) retenu(s) : %s", sum(held.values()),
                "; ".join(f"{motif} : {n}" for motif, n in sorted(held.items())),
            )
        proposed = 0
        for rule_id, rows in buckets.items():
            if dry_run:
                _logger.info("bf_persona_privacy (simulation) : %s persona(s) seraient "
                             "proposés sous la règle #%s", len(rows), rule_id)
                continue
            proposed += self._privacy_propose_lines(
                self.env["privacy.retention.calendar"].browse(rule_id), rows)
        _logger.info("bf_persona_privacy: %s persona(s) proposé(s) à la destruction%s",
                     proposed, " (simulation)" if dry_run else "")
        return proposed

    @api.model
    def _privacy_propose_lines(self, rule, rows):
        if not rows:
            return 0
        Line = self.env["privacy.destruction.campaign.line"].sudo()
        already = self._privacy_held_ids()
        rows = rows.filtered(lambda r: r.id not in already)
        if not rows:
            return 0
        campaign = self._privacy_open_campaign(rule)
        Line.create([{
            "campaign_id": campaign.id,
            "res_model": self._name,
            "res_id": row.id,
            # Figé ici, et sans nom : ce libellé finit dans le registre IMMUABLE,
            # et le nom du persona est celui de la personne.
            "res_name": _("Persona #%s", row.id),
            "retention_calendar_id": rule.id,
            "retention_expiry_date": row._privacy_expiry_date(rule),
            "destruction_method": rule.destruction_method or "delete",
            "state": "pending",
        } for row in rows])
        campaign.message_post(
            body=_("%(count)s persona(s) ajouté(s) par le balayage de rétention des "
                   "personas. Rien n'est détruit tant que la campagne n'est pas "
                   "approuvée puis exécutée.", count=len(rows)),
            message_type="notification",
        )
        return len(rows)

    @api.model
    def _privacy_open_campaign(self, rule):
        """Une seule campagne en révision par règle, créée au besoin.

        Seulement « en révision » : une campagne en brouillon créée à la main
        sur la même règle ne s'approuve pas et y resterait coincée.
        """
        from .privacy_destruction_register import persona_destruction_scope_statement

        Campaign = self.env["privacy.destruction.campaign"].sudo()
        existing = Campaign.search([
            ("retention_calendar_id", "=", rule.id), ("state", "=", "review"),
        ], order="id desc", limit=1)
        if existing:
            return existing
        campaign = Campaign.create({
            "name": rule.name,
            "retention_calendar_id": rule.id,
            "cutoff_date": fields.Date.context_today(self),
            "notes": _(
                "Campagne alimentée par le balayage de rétention de "
                "`bf_persona_privacy`. Les lignes sont déposées par le cron ; "
                "l'approbation et l'exécution restent manuelles. Chaque ligne est "
                "revérifiée à l'exécution : un persona dont la personne a réécrit, "
                "ou qui a une facture impayée, est ignoré.\n\n%s",
                persona_destruction_scope_statement(self.env),
            ),
        })
        # « Balayer » ne trouverait rien (le balayage natif lit les
        # classifications) et remettrait l'état à zéro.
        campaign.write({"state": "review"})
        campaign.message_post(
            body=_("Campagne ouverte par le balayage de rétention des personas. "
                   "⚠️ Ne pas utiliser « Balayer » : les lignes sont déposées par "
                   "le cron."),
            message_type="notification",
        )
        return campaign
