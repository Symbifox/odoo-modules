"""La cible démarchée : inscrire le refus, refuser le retour, proposer la destruction.

Deux règles, deux ancrages :

* **DEM-001, la cible sans refus** : trois ans après le dernier contact UTILE
  (personne rejointe, réponse, intérêt, refus poli, rappel demandé, ou toute
  interaction entrante), à défaut après la création de la fiche. Des appels
  restés sans réponse ne prolongent pas la conservation : sinon une cible
  relancée chaque mois ne mourrait jamais. Décision du 2026-10-18.
* **DEM-002, la fiche d'une personne qui a refusé** : proposée sans délai. La
  preuve du refus ne vit plus dans la fiche mais dans la liste d'opposition,
  inscrite au moment même du refus (`bf.outreach.optout`). Le délai réel est
  celui de l'approbation de la campagne.

Le pont **propose**, il ne détruit pas : même règle que les autres ponts de
rétention de la suite.
"""
import logging
from collections import defaultdict
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .outreach_optout import check_can_lift

_logger = logging.getLogger(__name__)

# Issues d'une interaction qui prouvent qu'on a joint la personne.
USEFUL_OUTCOMES = ("reached", "replied", "interested", "not_interested", "callback")

# États d'une campagne de destruction encore vivante.
_LIVE_CAMPAIGN_STATES = ["draft", "scanning", "review", "approved", "executing"]

# Une ligne ignorée À LA MAIN par le responsable retient la cible un an : il a
# décidé de la garder. Le libellé est celui de `action_skip` du socle. Les lignes
# ignorées par l'exécuteur (plus dues) et les échecs ne retiennent rien : la cible
# est reproposée dès qu'elle est de nouveau échue.
_MANUAL_SKIP_MESSAGE = "Ignoré manuellement"
_MANUAL_SKIP_HOLD_DAYS = 365

_CONTACT_FIELDS = ("email", "phone", "mobile")


class BfOutreachTarget(models.Model):
    _inherit = "bf.outreach.target"

    # ------------------------------------------------------------------
    # Inscrire le refus, refuser le retour
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        """Refuse de recréer une personne inscrite à la liste d'opposition.

        Création directe : on lève, en nommant la cible, pour que l'usager
        retire la ligne de sa liste. Sous `bf_outreach_optout_filter` (l'assistant
        d'import) : on écarte et on compte.

        ⚠️ Le contrôle se fait APRÈS `super()`, sur les coordonnées que le module
        a lui-même posées : refaire la normalisation à partir des `vals`
        risquerait d'en diverger. Les lignes écartées n'ont jamais été validées,
        elles meurent dans la même transaction.
        """
        records = super().create(vals_list)
        refused = records.filtered("do_not_contact")
        for target in refused:
            target._bf_engrave_refusal()
        blocked = (records - refused).filtered(lambda t: t._bf_is_opted_out())
        if not blocked:
            return records
        if not self.env.context.get("bf_outreach_optout_filter"):
            raise UserError(_(
                "« %(name)s » a demandé de ne plus être démarché : son courriel ou "
                "son numéro figure à la liste d'opposition. Retirez cette ligne.",
                name=blocked[:1].name,
            ))
        _logger.info("bf_outreach_privacy: %s cible(s) écartée(s) à la création, "
                     "inscrite(s) à la liste d'opposition", len(blocked))
        counter = self.env.context.get("bf_outreach_optout_skipped")
        if isinstance(counter, list):
            counter.append(len(blocked))
        blocked.sudo().unlink()
        return records - blocked

    def write(self, vals):
        lifting = "do_not_contact" in vals and not vals["do_not_contact"]
        lifted = self.browse()
        if lifting:
            lifted = self.filtered("do_not_contact")
            if lifted:
                check_can_lift(self.env)
        res = super().write(vals)
        if vals.get("do_not_contact"):
            for target in self:
                target._bf_engrave_refusal()
        for target in lifted:
            target._bf_lift_refusal()
        if any(f in vals for f in _CONTACT_FIELDS):
            for target in self:
                if target.do_not_contact:
                    # Une nouvelle coordonnée d'une personne qui a refusé
                    # s'inscrit aussi.
                    target._bf_engrave_refusal()
                elif target._bf_is_opted_out():
                    raise UserError(_(
                        "Ce courriel ou ce numéro figure à la liste d'opposition : "
                        "la personne a demandé de ne plus être démarchée."
                    ))
        return res

    def _bf_is_opted_out(self):
        self.ensure_one()
        Optout = self.env["bf.outreach.optout"]
        keys = list(Optout._bf_target_keys(self))
        if self.partner_id and not self.email and not self.phone and not self.mobile:
            # Coordonnées portées par le contact seul (création par RPC).
            keys += list(Optout._bf_partner_keys(self.partner_id))
        return bool(Optout._bf_blocked(keys))

    def _is_solicitable(self):
        """Ajoute la liste d'opposition au contrôle de dernière minute du module."""
        return super()._is_solicitable() and not self._bf_is_opted_out()

    def _bf_engrave_refusal(self):
        """Inscrit le refus de cette cible.

        ⚠️ Les fiches sœurs (la même personne dans une autre campagne) ne sont
        PAS marquées « ne plus contacter » : un tel gel se propageait de
        coordonnée en coordonnée (un standard partagé suffisait à geler toute
        une entreprise) et faisait passer sous DEM-002 des personnes qui
        n'avaient rien refusé. Elles sont écartées au moment de solliciter, par
        `_is_solicitable`, que le module consulte avant chaque activité et
        chaque envoi.
        """
        self.ensure_one()
        return self.env["bf.outreach.optout"]._bf_engrave(
            self.env["bf.outreach.optout"]._bf_target_keys(self), "target", self,
            refused_on=self.do_not_contact_date,
            user=self.do_not_contact_user_id,
            reason=self.do_not_contact_reason,
            company=self.company_id,
        )

    def _bf_lift_refusal(self):
        self.ensure_one()
        removed, transferred, held = self.env["bf.outreach.optout"]._bf_lift(self)
        parts = []
        if removed:
            parts.append(_("%s inscription(s) retirée(s) de la liste d'opposition.", removed))
        if transferred:
            parts.append(_("%s inscription(s) conservée(s) : un autre refus porte la même "
                           "coordonnée.", transferred))
        if held:
            parts.append(_("⚠️ %s coordonnée(s) de cette fiche restent inscrites par une autre "
                           "fiche : le contact lié s'il a refusé (le lever sur le contact), "
                           "sinon un refus antérieur, que seul le responsable de la vie "
                           "privée peut retirer. La fiche reste écartée tant qu'elles y sont.",
                           held))
        if parts:
            self.message_post(body=Markup("<p>%s</p>") % " ".join(parts))

    def unlink(self):
        """Toute suppression passe par ici : campagne, gestionnaire, demande d'effacement.

        🔴 Les interactions partent par l'ORM, pas par la cascade SQL de
        `target_id` : c'est leur `unlink` qui balaie leurs pièces jointes.
        Le refus s'inscrit AVANT, et la suppression lève si elle effacerait la
        seule trace d'un refus.
        """
        Optout = self.env["bf.outreach.optout"]
        for target in self.filtered("do_not_contact"):
            target._bf_engrave_refusal()
            keys = Optout._bf_target_keys(target)
            partner_holds = bool(target.partner_id and target.partner_id.outreach_opt_out)
            if not partner_holds and not (keys and Optout._bf_blocked(list(keys)) == set(keys)):
                raise UserError(_(
                    "La cible #%(id)s a refusé d'être démarchée, et ce refus n'est "
                    "retenu ni par la liste d'opposition ni par un contact. La "
                    "supprimer effacerait la seule preuve du refus. Ajoutez-lui un "
                    "courriel ou un numéro, ou liez-la au contact qui a refusé.",
                    id=target.id,
                ))
        self._bf_unlink_federation_copies()
        touches = self.sudo().with_context(active_test=False).touch_ids
        if touches:
            touches.unlink()
        return super().unlink()

    def _bf_unlink_federation_copies(self):
        """La copie locale du suivi fédéré porte nom, coordonnées et résumés : elle part aussi.

        Le miroir chez le mandant, lui, n'est retiré qu'à son rafraîchissement.
        """
        if "federation.outreach.report.line" not in self.env:
            return
        Line = self.env["federation.outreach.report.line"].sudo().with_context(active_test=False)
        refs = [f"bf.outreach.campaign,{cid}" for cid in self.campaign_id.ids]
        if not refs:
            return
        lines = Line.search([
            ("report_id.campaign_ref", "in", refs),
            ("key", "in", [str(i) for i in self.ids]),
        ])
        lines.unlink()

    # ------------------------------------------------------------------
    # Règles et balayage
    # ------------------------------------------------------------------

    @api.model
    def _privacy_rule_active(self):
        """DEM-001 — cible sans refus."""
        return self.env.ref("bf_outreach_privacy.retention_outreach_target",
                            raise_if_not_found=False)

    @api.model
    def _privacy_rule_refused(self):
        """DEM-002 — fiche d'une personne qui a refusé."""
        return self.env.ref("bf_outreach_privacy.retention_outreach_refused",
                            raise_if_not_found=False)

    @api.model
    def _privacy_rules(self):
        rules = self.env["privacy.retention.calendar"].browse()
        for rule in (self._privacy_rule_active(), self._privacy_rule_refused()):
            if rule:
                rules |= rule
        return rules

    def _privacy_last_useful_dates(self):
        """``{id de cible: date du dernier contact utile}``, cibles sans contact absentes."""
        if not self:
            return {}
        groups = self.env["bf.outreach.touch"].sudo()._read_group(
            [("target_id", "in", self.ids),
             "|", ("outcome", "in", USEFUL_OUTCOMES), ("direction", "=", "in")],
            ["target_id"], ["date:max"],
        )
        return {target.id: fields.Date.to_date(last) for target, last in groups if last}

    def _privacy_anchor(self):
        """Date d'ancrage DEM-001 : le dernier contact utile, sinon la création."""
        self.ensure_one()
        created = fields.Date.to_date(self.create_date)
        useful = self._privacy_last_useful_dates().get(self.id)
        return max(created, useful) if useful else created

    def _privacy_still_due(self, rule):
        """La cible relève-t-elle ENCORE de `rule` ? Relu à l'exécution : une
        campagne peut attendre des semaines, la cible a pu répondre ou être levée."""
        self.ensure_one()
        if self.do_not_contact:
            # Un refus est dû sous toute règle : une cible proposée sous DEM-001
            # qui refuse pendant l'attente ne doit pas être ignorée (son refus
            # crée une interaction entrante récente). `unlink` garde la preuve.
            return True
        if rule == self._privacy_rule_refused():
            return False
        if rule == self._privacy_rule_active():
            days = rule.total_retention_days
            if not days or days <= 0:
                return False
            return self._privacy_anchor() + timedelta(days=days) <= fields.Date.context_today(self)
        return True

    def _privacy_refusal_is_held(self):
        """Le refus survivra-t-il à la fiche ? Liste d'opposition complète, ou contact."""
        self.ensure_one()
        Optout = self.env["bf.outreach.optout"]
        keys = Optout._bf_target_keys(self)
        if self.partner_id and self.partner_id.outreach_opt_out:
            return True
        return bool(keys) and Optout._bf_blocked(list(keys)) == set(keys)

    @api.model
    def _privacy_held_ids(self):
        """Cibles retenues : ligne en attente dans une campagne vivante, ou
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
    def _privacy_scan_expired(self, limit=None, dry_run=False):
        """``(seaux_par_règle, retenues_par_motif)`` — sans rien détruire."""
        held = defaultdict(int)
        buckets = defaultdict(lambda: self.browse())
        Target = self.sudo().with_context(active_test=False)
        today = fields.Date.context_today(self)
        already = self._privacy_held_ids()

        # DEM-001 — les cibles sans refus.
        rule = self._privacy_rule_active()
        days = rule.total_retention_days if rule else 0
        if not days or days <= 0:
            # Balayer avec une durée nulle proposerait TOUT à la destruction.
            held[_("règle DEM-001 absente ou sans durée")] += Target.search_count(
                [("do_not_contact", "=", False)])
        else:
            cutoff = today - timedelta(days=days)
            # Personne ne peut être échu si sa fiche est plus jeune que la règle.
            rows = Target.search(
                [("do_not_contact", "=", False), ("create_date", "<", cutoff),
                 ("id", "not in", list(already))],
                order="create_date asc",
            )
            last_useful = rows._privacy_last_useful_dates()
            for target in rows:
                created = fields.Date.to_date(target.create_date)
                anchor = max(created, last_useful.get(target.id) or created)
                if anchor < cutoff:
                    buckets[rule.id] |= target

        # DEM-002 — les refus.
        refused_rule = self._privacy_rule_refused()
        refused = Target.search([("do_not_contact", "=", True), ("id", "not in", list(already))],
                                order="do_not_contact_date asc")
        if refused and not refused_rule:
            held[_("règle DEM-002 absente")] += len(refused)
        elif refused:
            for target in refused:
                if not dry_run:
                    # Défensif et idempotent : un refus noté avant l'installation,
                    # ou par un chemin qui aurait contourné `write`, s'inscrit ici.
                    target._bf_engrave_refusal()
                if not target._privacy_refusal_is_held():
                    # Détruire la fiche effacerait la seule trace du refus.
                    held[_("refus sans coordonnée ni contact pour le retenir")] += 1
                    continue
                buckets[refused_rule.id] |= target

        if limit:
            for rule_id in list(buckets):
                buckets[rule_id] = buckets[rule_id][:limit]
        return dict(buckets), dict(held)

    def _privacy_expiry_date(self, rule):
        self.ensure_one()
        if rule == self._privacy_rule_refused():
            return fields.Date.to_date(self.do_not_contact_date or self.write_date)
        return self._privacy_anchor() + timedelta(days=rule.total_retention_days)

    # ------------------------------------------------------------------
    # Proposition
    # ------------------------------------------------------------------

    @api.model
    def _cron_privacy_propose_destruction(self, limit=None, dry_run=False):
        """Le cron du pont : dépose les cibles échues dans une campagne. Ne détruit RIEN."""
        if limit is None:
            try:
                limit = int(self.env["ir.config_parameter"].sudo().get_param(
                    "bf_outreach_privacy.batch_size", "500"))
            except (TypeError, ValueError):
                limit = 500
        buckets, held = self._privacy_scan_expired(limit=limit, dry_run=dry_run)
        if held:
            _logger.info(
                "bf_outreach_privacy: %s cible(s) retenue(s) : %s", sum(held.values()),
                "; ".join(f"{motif} : {n}" for motif, n in sorted(held.items())),
            )
        proposed = 0
        for rule_id, rows in buckets.items():
            if dry_run:
                _logger.info("bf_outreach_privacy (simulation) : %s cible(s) seraient "
                             "proposées sous la règle #%s", len(rows), rule_id)
                continue
            proposed += self._privacy_propose_lines(
                self.env["privacy.retention.calendar"].browse(rule_id), rows)
        _logger.info("bf_outreach_privacy: %s cible(s) proposée(s) à la destruction%s",
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
            # Figé ici, et sans nom : ce libellé finit dans le registre
            # IMMUABLE, et pour un particulier le nom de la cible est celui de
            # la personne.
            "res_name": _("Cible de démarchage #%(id)s (campagne #%(campaign)s)",
                          id=row.id, campaign=row.campaign_id.id),
            "retention_calendar_id": rule.id,
            "retention_expiry_date": row._privacy_expiry_date(rule),
            "destruction_method": rule.destruction_method or "delete",
            "state": "pending",
        } for row in rows])
        campaign.message_post(
            body=_("%(count)s cible(s) ajoutée(s) par le balayage de rétention du "
                   "démarchage. Rien n'est détruit tant que la campagne n'est pas "
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
        from .privacy_destruction_register import outreach_destruction_scope_statement

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
                "`bf_outreach_privacy`. Les lignes sont déposées par le cron ; "
                "l'approbation et l'exécution restent manuelles. Chaque ligne est "
                "revérifiée à l'exécution : une cible qui a répondu ou dont le refus "
                "a été levé entre-temps est ignorée.\n\n%s",
                outreach_destruction_scope_statement(self.env),
            ),
        })
        # « Balayer » ne trouverait rien (le balayage natif lit les
        # classifications) et remettrait l'état à zéro.
        campaign.write({"state": "review"})
        campaign.message_post(
            body=_("Campagne ouverte par le balayage de rétention du démarchage. "
                   "⚠️ Ne pas utiliser « Balayer » : les lignes sont déposées par "
                   "le cron."),
            message_type="notification",
        )
        return campaign


class BfOutreachCampaign(models.Model):
    _inherit = "bf.outreach.campaign"

    def unlink(self):
        """Les cibles partent par l'ORM : la cascade SQL sauterait leur `unlink`."""
        self.sudo().with_context(active_test=False).target_ids.unlink()
        return super().unlink()
