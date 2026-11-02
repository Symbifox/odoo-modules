import logging

from odoo import _, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Les modèles que ce pont ouvre à la classification. Ce sont ceux dont il
# doit aussi répondre au moment de la destruction.
_MEETING_MODELS = ("meeting.record", "meeting.agenda")


class PrivacyDestructionCampaignLine(models.Model):
    """Fait dire vrai à la campagne de destruction sur les rencontres.

    🔴 **Le problème corrigé ici.** La méthode générique de `privacy_consent`
    traite « Suppression » ainsi ::

        elif method == "delete":
            if hasattr(record, "active"):
                record.sudo().write({"active": False})
            else:
                record.sudo().unlink()

    `meeting.record` porte un champ `active`. Une campagne l'aurait donc
    ARCHIVÉ — verbatim intact, consultable en deux clics par qui sait cocher
    « Archivé » — pendant que `action_execute` écrivait au registre IMMUABLE
    une entrée disant « Suppression ». Le registre ne se corrige pas : il
    refuse `write` et `unlink`. Une seule campagne aurait donc laissé une
    certification fausse et définitive.

    🔴 **Et l'échec n'arrête pas la certification.** Dans `action_execute`,
    l'entrée de registre est créée APRÈS l'appel, sans regarder l'état que
    celui-ci vient d'écrire : une ligne passée à « échec » ou « ignoré » est
    réécrite à « fait » avec son entrée. Le seul moyen d'empêcher une
    certification est donc de LEVER. C'est ce que fait cette surcharge quand
    elle ne peut pas garantir l'opération.
    """

    _inherit = "privacy.destruction.campaign.line"

    def _execute_destruction(self):
        self.ensure_one()
        if self.res_model not in _MEETING_MODELS:
            return super()._execute_destruction()

        record = self.env[self.res_model].browse(self.res_id)
        if not record.exists():
            # Rien à détruire, et rien à certifier : on lève plutôt que de
            # laisser `action_execute` écrire une entrée pour un
            # enregistrement déjà parti.
            raise UserError(
                _("« %(name)s » n'existe plus : rien à détruire, et rien à "
                  "inscrire au registre.", name=self.res_name or self.res_id)
            )

        method = self.destruction_method
        raw_rule = self.env.ref(
            "bf_meeting_privacy.retention_meeting_raw_material",
            raise_if_not_found=False,
        )
        # ⚠️ Comparaison par id, pas par recordset. `env.ref` rend `False` — pas
        # un recordset vide — quand la donnée a été supprimée à la main, et
        # `recordset == False` ne dit pas ce qu'on croit. Une ligne SANS règle
        # serait alors passée pour une ligne RENC-002.
        raw_only = bool(raw_rule) and self.retention_calendar_id.id == raw_rule.id

        if raw_only:
            # 🔴 Règle RENC-002 : la campagne ne l'exécute PAS, et c'est
            # délibéré. Elle n'efface que la transcription et laisse le compte
            # rendu debout ; or `action_execute` écrirait derrière une entrée
            # générique — « Destruction par campagne. Document : <compte
            # rendu> », méthode « Suppression » — que personne ne pourrait
            # relire autrement que comme la destruction du compte rendu. Une
            # entrée de registre ne se corrige pas.
            #
            # Le chemin sanctionné écrit sa propre entrée, qui nomme ce qui est
            # parti ET ce qui reste : le bouton « Purger la transcription
            # brute » sur le compte rendu, ou le cron du pont.
            raise UserError(
                _("La règle RENC-002 n'efface que la transcription brute et "
                  "laisse le compte rendu en place. La campagne ne sait pas "
                  "consigner cette nuance : elle inscrirait au registre "
                  "immuable la destruction du compte rendu entier.\n\n"
                  "Passez par « Purger la transcription brute » sur le compte "
                  "rendu, ou par le cron « Rencontres — purge de la matière "
                  "première ». Les deux écrivent une entrée qui dit exactement "
                  "ce qui a été effacé.")
            )

        if method != "delete":
            raise UserError(
                _("Méthode « %(method)s » non prise en charge sur %(model)s. "
                  "Une rencontre ne s'anonymise pas : les propos rapportés "
                  "nomment leurs auteurs d'un bout à l'autre du verbatim, et "
                  "un effacement sécurisé n'a pas de sens sur une ligne de "
                  "table. Seule la suppression réelle est offerte.",
                  method=method or _("(vide)"), model=self.res_model)
            )

        # Suppression RÉELLE. `unlink` emporte en cascade les présences, les
        # décisions et les sujets. ⚠️ Les ordres du jour et les tâches qui
        # pointaient vers ce compte rendu ne sont pas supprimés : leur lien
        # est un Many2one sans `ondelete`, donc mis à NULL en silence. Ils
        # perdent leur rattachement, ils ne perdent pas leur contenu.
        record.check_access("unlink")
        record.sudo().unlink()
        _logger.info(
            "bf_meeting_privacy: %s,%s supprimé pour de bon par la campagne %s",
            self.res_model, self.res_id, self.campaign_id.name,
        )
        if self.classification_id:
            self.classification_id.write({"active": False})
        return None
