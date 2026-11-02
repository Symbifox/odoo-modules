"""Machinerie commune aux deux gisements : balayer, proposer, graver.

Le pont **propose**, il ne détruit pas. Le cron dépose les lignes échues dans
une `privacy.destruction.campaign` laissée en révision ; le responsable de la
vie privée approuve, puis exécute, et c'est la campagne qui écrit au registre.
C'est la décision prise pour `bf_email_privacy`, et il n'y a aucune raison que
la messagerie détruise plus librement que le courriel.
"""
import logging
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# États d'une campagne où une ligne « en attente » compte encore comme
# proposée : la re-proposer chaque nuit noierait la révision.
_LIVE_CAMPAIGN_STATES = ["draft", "scanning", "review", "approved", "executing"]


def privacy_destruction_scope_statement(env):
    """Ce que la destruction ne couvre pas.

    ⚠️ Ce texte entre dans une entrée de registre IMMUABLE : ce qui y est écrit
    y reste. Il énonce donc des faits vérifiables et des principes, jamais un
    chiffre qui vieillit ni une profondeur de sauvegarde déduite d'une
    politique dont l'effet n'a pas été constaté — deux formulations
    antérieures s'y sont trompées côté courriel, dans les deux sens.
    """
    return _(
        "Portée : la ligne quitte la base Odoo, et les pièces jointes MMS "
        "qu'elle porte partent avec elle ; leur bloc quitte le filestore au "
        "passage suivant du ramasse-miettes des pièces jointes.\n"
        "Ne sont PAS atteints par cette destruction : (1) l'appareil Android, "
        "dont l'archive n'est qu'une copie — le message et le journal d'appel "
        "restent sur le téléphone ; (2) les fichiers XML de sauvegarde, que "
        "l'import DÉPLACE vers « done/ » au lieu de les détruire, sur "
        "/mnt/sms-inbox comme sur le Nextcloud surveillé ; (3) VOIP.ms, qui "
        "tient son propre journal des messages de la ligne d'affaires ; "
        "(4) les sauvegardes, qui gardent l'état antérieur aussi longtemps que "
        "leur propre rotation le conserve, selon la politique de sauvegarde "
        "de l'exploitant, "
        "dont la profondeur RÉELLE se vérifie au dépôt plutôt que de se "
        "déduire de la politique.\n"
        "Ce qui empêche la ligne de revenir : son empreinte est gravée en "
        "pierre tombale (`sms.archive.privacy.tombstone`) et tout import "
        "ultérieur qui la recroise passe outre. Sans cela, reverser un vieux "
        "fichier de « done/ » la recréerait, la déduplication à l'import "
        "portant sur l'existence d'une ligne et non sur une trace de "
        "destruction.\n"
        "La méthode déclarée est « suppression », pas « effacement "
        "sécurisé » : on retire des lignes et des objets, sans garantie sur "
        "les blocs sous-jacents."
    )


class BfSmsPrivacyRegime(models.AbstractModel):
    """Points communs des modèles soumis à une règle de conservation."""

    _name = "bf.sms.privacy.regime"
    _description = "Régime de conservation — messagerie et journaux d'appels"

    # À définir par le modèle concret.
    _privacy_date_field = None      # champ qui porte l'ancrage
    _privacy_hash_field = None      # champ qui porte l'empreinte de dédoublonnage
    _privacy_rule_xmlid = None      # règle par défaut du modèle
    _privacy_campaign_label = None  # préfixe du nom de campagne

    # ------------------------------------------------------------------
    # Les règles
    # ------------------------------------------------------------------

    @api.model
    def _privacy_rule(self):
        """Règle par défaut du modèle, ou `False` si elle a été supprimée."""
        return self.env.ref(self._privacy_rule_xmlid, raise_if_not_found=False)

    @api.model
    def _privacy_rules(self):
        """Toutes les règles que ce modèle peut porter.

        Sert à la garde de `action_scan` : une campagne ouverte sur l'une
        d'elles est alimentée par le cron, pas par les classifications.
        """
        rule = self._privacy_rule()
        return rule or self.env["privacy.retention.calendar"].browse()

    @api.model
    def _privacy_rule_days(self, rule):
        """Durée totale d'une règle, ou `False` si elle n'est pas exploitable.

        ⚠️ `total_retention_days` est calculé et stocké : une règle éditée à la
        main peut le laisser vide. Balayer avec une durée nulle proposerait
        TOUT à la destruction. On préfère ne rien proposer.
        """
        if not rule:
            return False
        days = rule.total_retention_days
        if not days or days <= 0:
            return False
        return days

    def _privacy_expiry_date(self):
        """Date d'échéance de CETTE ligne, telle qu'inscrite à la campagne."""
        self.ensure_one()
        anchor, rule, _motif = self._privacy_anchor()
        days = self._privacy_rule_days(rule)
        if not anchor or not days:
            return False
        return anchor + timedelta(days=days)

    def _privacy_anchor(self):
        """``(date d'ancrage, règle, motif de rétention)`` pour une ligne.

        Par défaut : la date propre de la ligne, sous la règle du modèle. Le
        modèle des appels surcharge pour tenir compte des comptes rendus.
        """
        self.ensure_one()
        raw = self[self._privacy_date_field]
        if not raw:
            return False, self._privacy_rule(), _("ligne sans date")
        return fields.Date.to_date(raw), self._privacy_rule(), False

    # ------------------------------------------------------------------
    # Balayage
    # ------------------------------------------------------------------

    @api.model
    def _privacy_scan_expired(self, limit=None):
        """``(seaux_par_règle, retenues_par_motif)`` — sans rien détruire.

        Le balayage est piloté par domaine : les lignes qui ne peuvent pas être
        échues ne sont jamais chargées.
        """
        held = defaultdict(int)
        buckets = defaultdict(lambda: self.browse())
        rule = self._privacy_rule()
        days = self._privacy_rule_days(rule)
        if not days:
            held[_("règle de conservation absente ou sans durée")] += (
                self.sudo().search_count([])
            )
            return dict(buckets), dict(held)

        cutoff = fields.Date.context_today(self) - timedelta(days=days)
        rows = self.sudo().search(
            [(self._privacy_date_field, "<", cutoff)],
            order=f"{self._privacy_date_field} asc",
            limit=limit or None,
        )
        if rows:
            buckets[rule.id] |= rows
        return dict(buckets), dict(held)

    # ------------------------------------------------------------------
    # Proposition
    # ------------------------------------------------------------------

    @api.model
    def _privacy_propose_expired(self, limit=None, dry_run=False):
        """Dépose les lignes échues dans une campagne. Ne détruit RIEN.

        Rend le nombre de lignes proposées (0 en simulation).
        """
        buckets, held = self._privacy_scan_expired()
        if held:
            _logger.info(
                "bf_sms_privacy: %s — %s ligne(s) retenue(s) : %s",
                self._name,
                sum(held.values()),
                "; ".join(f"{motif} : {n}" for motif, n in sorted(held.items())),
            )
        if not buckets:
            return 0

        proposed = 0
        remaining = limit
        for rule_id, rows in buckets.items():
            if remaining is not None:
                if remaining <= 0:
                    break
                rows = rows[:remaining]
            if dry_run:
                _logger.info(
                    "bf_sms_privacy (simulation) : %s ligne(s) de %s seraient "
                    "proposées sous la règle #%s",
                    len(rows), self._name, rule_id,
                )
                continue
            count = self._privacy_propose_lines(
                self.env["privacy.retention.calendar"].browse(rule_id), rows,
            )
            proposed += count
            if remaining is not None:
                remaining -= count
        return proposed

    @api.model
    def _privacy_propose_lines(self, rule, rows):
        """Dépose `rows` en lignes d'une campagne ouverte, sans rien détruire."""
        if not rows:
            return 0
        campaign = self._privacy_open_campaign(rule)
        Line = self.env["privacy.destruction.campaign.line"].sudo()

        # Ne pas re-proposer ce qui attend déjà : le cron tourne tous les jours
        # et l'approbation peut prendre des semaines.
        already = set(Line.search([
            ("campaign_id.state", "in", _LIVE_CAMPAIGN_STATES),
            ("res_model", "=", self._name),
            ("state", "=", "pending"),
        ]).mapped("res_id"))

        vals_list = []
        for row in rows:
            if row.id in already:
                continue
            vals_list.append({
                "campaign_id": campaign.id,
                "res_model": self._name,
                "res_id": row.id,
                # Figé ici : après la destruction, plus rien ne le produit, et
                # la ligne de campagne est ce qui en reste.
                "res_name": row.display_name or _("#%s", row.id),
                "retention_calendar_id": rule.id,
                "retention_expiry_date": row._privacy_expiry_date(),
                "destruction_method": rule.destruction_method or "delete",
                "state": "pending",
            })
        if not vals_list:
            return 0
        Line.create(vals_list)
        campaign.message_post(
            body=_(
                "%(count)s ligne(s) ajoutée(s) par le balayage de rétention de "
                "la messagerie. Rien n'est détruit tant que la campagne n'est "
                "pas approuvée puis exécutée.",
                count=len(vals_list),
            ),
            message_type="notification",
        )
        return len(vals_list)

    @api.model
    def _privacy_open_campaign(self, rule):
        """Campagne ouverte pour cette règle, créée au besoin.

        Une seule campagne vivante par règle : le cron tourne tous les jours et
        en créerait autrement une par nuit.
        """
        Campaign = self.env["privacy.destruction.campaign"].sudo()
        existing = Campaign.search([
            ("retention_calendar_id", "=", rule.id),
            ("state", "in", ["draft", "scanning", "review"]),
        ], order="id desc", limit=1)
        if existing:
            return existing
        campaign = Campaign.create({
            "name": _("%(label)s — %(rule)s",
                      label=self._privacy_campaign_label or self._name,
                      rule=rule.name),
            "retention_calendar_id": rule.id,
            "cutoff_date": fields.Date.context_today(self),
            "notes": _(
                "Campagne alimentée par le balayage de rétention de "
                "`bf_sms_privacy`. Les lignes sont déposées par le cron ; "
                "l'approbation et l'exécution restent manuelles.\n\n%s",
                privacy_destruction_scope_statement(self.env),
            ),
        })
        # `create` laisse la campagne en brouillon. On la passe en révision :
        # « Balayer » ne trouverait rien (le balayage natif lit les
        # classifications, pas nos lignes) et remettrait l'état à zéro.
        campaign.write({"state": "review"})
        campaign.message_post(
            body=_(
                "Campagne ouverte par le balayage de rétention de la "
                "messagerie. ⚠️ Ne pas utiliser « Balayer » : les lignes sont "
                "déposées par le cron, un balayage natif chercherait des "
                "classifications documentaires et n'en trouverait aucune."
            ),
            message_type="notification",
        )
        return campaign

    # ------------------------------------------------------------------
    # Pierres tombales
    # ------------------------------------------------------------------

    def _privacy_engrave_tombstones(self, campaign=None):
        """Grave l'empreinte des lignes AVANT qu'elles ne disparaissent."""
        hashes = [row[self._privacy_hash_field] for row in self]
        return self.env["sms.archive.privacy.tombstone"]._bf_engrave(
            self._name, hashes, campaign=campaign,
        )

    @api.model_create_multi
    def create(self, vals_list):
        """Refuse de recréer ce qui a été détruit.

        ⚠️ **Défaillance ouverte, et c'est délibéré.** Ce chemin est celui de
        TOUT message entrant, webhook VOIP.ms compris. Une exception ici
        ferait perdre des SMS en direct. Ressusciter une vieille ligne est un
        moindre mal que d'en perdre une nouvelle : au moindre pépin, on laisse
        passer et on journalise.

        ⚠️ Deux effets de bord connus, du côté de l'assistant d'import de
        `bf_sms_archive`, qui crée les messages un par un : son compteur
        « créés » compte la ligne refusée, et la création des parties MMS qui
        la suivait échoue sur un `message_id` vide — journalisée en
        avertissement par son propre `try/except`. Aucun des deux ne perd de
        donnée vivante.
        """
        try:
            blocked = self.env["sms.archive.privacy.tombstone"]._bf_blocked_hashes(
                self._name,
                [vals.get(self._privacy_hash_field) for vals in vals_list],
            )
        except Exception:  # noqa: BLE001 - voir la note « défaillance ouverte »
            _logger.exception(
                "bf_sms_privacy: lecture des pierres tombales impossible sur "
                "%s, création laissée passer", self._name,
            )
            return super().create(vals_list)

        if not blocked:
            return super().create(vals_list)

        kept = [
            vals for vals in vals_list
            if vals.get(self._privacy_hash_field) not in blocked
        ]
        _logger.info(
            "bf_sms_privacy: %s ligne(s) de %s non recréée(s) — empreinte "
            "gravée après destruction (import d'un fichier antérieur ?)",
            len(vals_list) - len(kept), self._name,
        )
        if not kept:
            return self.browse()
        return super().create(kept)
