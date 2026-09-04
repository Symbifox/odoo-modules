# Part of bf_recruitment_privacy. Voir LICENSE.
"""Classer la candidature au moment où son horloge de conservation démarre.

Une règle de conservation qui ne s'applique à rien ne conserve rien. La campagne
de destruction balaie `privacy.document.classification` : sans classification,
elle ne trouve aucune candidature, et la durée déclarée reste une intention.

Personne ne classera jamais des candidatures à la main, une par une. Le pont le
fait donc au seul moment qui a un sens : la clôture. C'est là que la finalité
est accomplie, et c'est de là que court la durée.
"""

import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

_RULE_APPLICATION = "bf_recruitment_privacy.retention_recruitment_application"
_RULE_HR_FILE = "privacy_consent.retention_calendar_hr_files"


class HrApplicant(models.Model):
    _inherit = "hr.applicant"

    def _privacy_retention_rule(self):
        """La règle applicable : RH-001 si embauche, RH-REC-1 sinon.

        ⚠️ Les deux portent la même durée, et c'est le régime retenu :
        un seul régime pour tout le dossier de recrutement. La bascule est donc
        un changement de rattachement, pas un changement d'échéance. Elle compte
        quand même : c'est elle qui dit sous quel titre le dossier est gardé, et
        c'est ce titre qui se défend devant la Commission.
        """
        self.ensure_one()
        xmlid = _RULE_HR_FILE if self.employee_id else _RULE_APPLICATION
        return self.env.ref(xmlid, raise_if_not_found=False)

    def _privacy_classification_date(self):
        """La date d'où court la conservation : la clôture, à défaut la création.

        Pas la date de classification. Une candidature reprise cinq ans plus
        tard partirait sinon pour cinq ans de plus, ce que l'art. 23 LPRPSP ne
        permet pas.

        🔴 **`date_closed` n'est PAS la clôture, c'est la date d'EMBAUCHE.** Le
        coeur la nomme « Hire Date » et ne la pose que lorsque la candidature
        atteint une étape marquée `hired_stage`. Un refus, lui, pose
        `refuse_date`, que cette méthode ignorait. Toute candidature refusée
        voyait donc son horloge partir de sa CRÉATION, et un dossier ouvert en
        janvier et refusé en septembre se serait détruit huit mois trop tôt.

        🔴 Et le contournement était pire que le trou : écrire `date_closed` à
        la main pour donner une date à la classification fait compter la
        personne comme EMBAUCHÉE. `hr.job.no_of_hired_employee` compte les
        candidatures qui portent une `date_closed`, quelles qu'elles soient.
        Mesuré sur la démo le 2026-08-31 : le poste de QA affichait deux
        embauches pour une seule, et le coût par embauche s'en trouvait divisé
        par deux.

        L'ordre ci-dessous est celui des faits : l'embauche d'abord si elle a
        eu lieu, le refus ensuite, la création en dernier recours, pour une
        candidature ni refusée ni embauchée, qu'on classe quand même.
        """
        self.ensure_one()
        moment = self.date_closed or self.refuse_date or self.create_date
        return fields.Date.to_date(moment) if moment else fields.Date.context_today(self)

    def _privacy_sync_classification(self):
        """Créer ou remettre d'aplomb la classification de la candidature.

        Idempotent : appelée à chaque clôture et à chaque embauche, elle écrit
        au même enregistrement. La contrainte d'unicité du noyau porte sur
        (modèle, id, catégorie, société), donc une seule ligne par candidature.
        """
        Classification = self.env["privacy.document.classification"].sudo()
        for applicant in self:
            rule = applicant._privacy_retention_rule()
            if not rule:
                # La donnée de référence a été supprimée à la main. On ne
                # fabrique pas une classification sans règle : elle n'aurait
                # aucune échéance et n'entrerait dans aucune campagne.
                _logger.warning(
                    "bf_recruitment_privacy: aucune règle de conservation "
                    "trouvée pour hr.applicant,%s", applicant.id,
                )
                continue
            existing = Classification.with_context(active_test=False).search([
                ("res_model", "=", "hr.applicant"),
                ("res_id", "=", applicant.id),
                ("pi_category", "=", "identification"),
            ], limit=1)
            vals = {
                "retention_calendar_id": rule.id,
                "document_date": applicant._privacy_classification_date(),
                "active": True,
            }
            if existing:
                existing.write(vals)
                continue
            Classification.create(dict(vals, **{
                "res_model": "hr.applicant",
                "res_id": applicant.id,
                "pi_category": "identification",
                "sensitivity_level": "confidential",
                "contains_direct_identifiers": True,
                "contains_indirect_identifiers": True,
                "subject_partner_id": applicant.partner_id.id or False,
                "company_id": applicant.company_id.id or self.env.company.id,
                "notes": _(
                    "Classée automatiquement à la clôture de la candidature. Le "
                    "dossier comprend le CV et les pièces déposées, le fil de "
                    "discussion, et les commentaires d'entrevue, qui sont des "
                    "appréciations écrites sur une personne nommée."
                ),
            }))

    def write(self, vals):
        res = super().write(vals)
        # La clôture, le refus et l'embauche démarrent ou déplacent l'horloge.
        # `employee_id` est un champ related porté par `hr.candidate` : on le
        # relit après coup plutôt que de deviner ce que `vals` contenait.
        if {"date_closed", "refuse_reason_id", "employee_id", "active"} & set(vals):
            closed = self.filtered(lambda a: a.date_closed or a.refuse_reason_id or a.employee_id)
            if closed:
                closed._privacy_sync_classification()
        return res

    def action_privacy_classify(self):
        """Bouton : classer maintenant, sans attendre la clôture."""
        self._privacy_sync_classification()
        return True

