# Part of bf_recruitment_privacy. Voir LICENSE.
"""Exécution réelle de la destruction, et refus de certifier autre chose.

🔴 **Le défaut du socle.** `privacy.destruction.campaign.line._execute_destruction()`
traite « Suppression » ainsi ::

    elif method == "delete":
        if hasattr(record, "active"):
            record.sudo().write({"active": False})
        else:
            record.sudo().unlink()

`hr.applicant` porte `active`. `hr.candidate` aussi. Une campagne aurait donc
ARCHIVÉ la candidature, CV intact, consultable en deux clics par qui sait cocher
« Archivé », pendant que `action_execute` écrivait au registre IMMUABLE une
entrée disant « Suppression ». Le registre refuse `write` et `unlink` : une
seule campagne aurait laissé une certification fausse et définitive.

🔴 **L'échec n'arrête pas la certification.** Dans `action_execute`, l'entrée de
registre est créée APRÈS l'appel, sans regarder l'état que celui-ci vient
d'écrire : une ligne passée à « échec » ou « ignoré » est réécrite à « fait »
avec son entrée. **Lever est le seul moyen d'empêcher une certification.**

🔴 **La personne n'est pas dans la candidature.** Odoo 18 a séparé
`hr.candidate` de `hr.applicant` : `partner_name`, `email_from`,
`partner_phone` et `linkedin_profile` sont des champs *related* portés par la
personne. Détruire la seule candidature ne détruit à peu près aucun
renseignement personnel. On emporte donc la personne avec sa dernière
candidature.

🔴 **La cascade SQL saute l'ORM.** `bf_interview.applicant_id` est
`ON DELETE CASCADE` en base : supprimer la candidature efface les séances et
les notations sans que `unlink()` soit appelé. Or c'est `unlink()` qui balaie
``ir_attachment WHERE res_model=… AND res_id IN …`` et les `mail.message`. Les
CV et les fils de discussion resteraient en base et au dépôt de fichiers,
orphelins et introuvables, pendant que le registre attesterait leur
destruction. On supprime donc les séances par l'ORM, d'abord.

⚠️ **Cette surcharge RELAIE à `super()`**, et ce n'est pas une politesse. Au
2026-08-30, cinq ponts surchargeaient déjà cette méthode : plusieurs ponts de vie privée, dont `bf_employee_experience_privacy` et
`bf_employee_experience_health_privacy`. Celui-ci fait le sixième. Ça ne se
compose que parce que chacun relaie pour les modèles qu'il ne possède pas. Un
pont qui oublierait de relayer ferait taire les gardes des autres en silence, et
aucun test qui ne charge qu'un pont ne le verrait.
"""

import logging

from odoo import _, models
from odoo.exceptions import UserError
from odoo.tools import SQL

_logger = logging.getLogger(__name__)

# Les modèles que ce pont ouvre à la classification, donc ceux dont il doit
# répondre au moment de la destruction.
_RECRUITMENT_MODELS = ("hr.applicant", "hr.candidate", "bf.interview")


class PrivacyDestructionCampaignLine(models.Model):
    _inherit = "privacy.destruction.campaign.line"

    def _execute_destruction(self):
        self.ensure_one()
        if self.res_model not in _RECRUITMENT_MODELS:
            return super()._execute_destruction()

        record = self.env[self.res_model].sudo().with_context(
            active_test=False
        ).browse(self.res_id)
        if not record.exists():
            raise UserError(_(
                "« %(name)s » n'existe plus : rien à détruire, et rien à "
                "inscrire au registre.",
                name=self.res_name or self.res_id,
            ))

        method = self.destruction_method
        if method not in ("delete", "secure_wipe"):
            raise UserError(_(
                "Méthode « %(method)s » non prise en charge sur %(model)s. Un "
                "dossier de candidature ne s'anonymise pas : le nom est dans le "
                "CV, dans le fil de discussion et dans les commentaires "
                "d'entrevue, qui sont écrits en toutes lettres. Retirer les "
                "champs d'identité laisserait tout le reste. Seule la "
                "suppression réelle est offerte, et la mesure est déjà gardée "
                "par l'agrégat anonymisé.",
                method=method or _("(vide)"), model=self.res_model,
            ))

        handler = {
            "hr.applicant": self._recruitment_destroy_applicant,
            "hr.candidate": self._recruitment_destroy_candidate,
            "bf.interview": self._recruitment_destroy_interview,
        }[self.res_model]
        handler(record)

        if self.env[self.res_model].sudo().with_context(
            active_test=False
        ).browse(self.res_id).exists():
            raise UserError(_(
                "%(model)s #%(id)s existe toujours après la suppression. Rien "
                "n'est inscrit au registre : une destruction qui n'a pas eu "
                "lieu ne s'atteste pas.",
                model=self.res_model, id=self.res_id,
            ))

        if self.classification_id:
            self.classification_id.write({"active": False})
        return None

    # ------------------------------------------------------------------
    # Les gardes
    # ------------------------------------------------------------------

    def _recruitment_check_aggregate(self, interviews):
        """Refuser de détruire des séances dont l'année n'est pas agrégée.

        C'est l'ordre qui compte : agréger, puis détruire. L'inverse fait perdre
        pour de bon ce que la grille avait appris, et une grille dont on ne sait
        plus si elle séparait quoi que ce soit ne se corrige plus.

        Seules comptent les séances qui portent une mesure : une séance annulée,
        ou tenue sans qu'aucune notation soit déposée, n'a rien à préserver.
        """
        Aggregate = self.env["bf.interview.aggregate"].sudo()
        measured = interviews.filtered(
            lambda i: i.state == "tenue" and i.submitted_count
        )
        for interview in measured:
            year = Aggregate._interview_year(interview)
            if not year:
                continue
            if Aggregate.has_coverage(
                interview.guide_id, interview.job_id, year, interview.company_id
            ):
                continue
            raise UserError(_(
                "Les entrevues de %(year)s sur la grille « %(guide)s » n'ont "
                "pas encore été agrégées. Les détruire maintenant ferait perdre "
                "ce que cette grille mesurait, et ça ne se reconstitue pas.\n\n"
                "Passez d'abord par « Agréger toutes les entrevues » dans "
                "Recrutement > Entrevues, ou attendez le cron nocturne. "
                "L'agrégat ne garde aucun nom, ni de candidat, ni d'évaluateur.",
                year=year, guide=interview.guide_id.display_name,
            ))

    def _recruitment_refuse_if_hired(self, candidate):
        """Une personne embauchée relève de RH-001, avec son dossier d'employé."""
        if candidate and candidate.employee_id:
            raise UserError(_(
                "« %(name)s » a été embauchée. Son dossier de candidature est "
                "passé sous RH-001 « Dossiers d'employés » et se détruit avec "
                "le dossier d'employé, pas ici. Deux régimes sur la même "
                "personne feraient perdre la trace de l'un des deux.",
                name=candidate.partner_name or candidate.display_name,
            ))

    # ------------------------------------------------------------------
    # Les destructions
    # ------------------------------------------------------------------

    @staticmethod
    def _recruitment_purge_attachments(record):
        """Les pièces jointes rattachées DIRECTEMENT à l'enregistrement.

        `unlink()` les emporte déjà. On les compte avant pour pouvoir le dire au
        journal, et pour que la suppression des séances par l'ORM ne repose pas
        sur une lecture de la documentation du noyau.
        """
        return record.env["ir.attachment"].sudo().search_count([
            ("res_model", "=", record._name), ("res_id", "=", record.id),
        ])

    def _recruitment_destroy_interview(self, interview):
        self._recruitment_check_aggregate(interview)
        count = self._recruitment_purge_attachments(interview)
        interview.check_access("unlink")
        interview.unlink()
        _logger.info(
            "bf_recruitment_privacy: bf.interview,%s supprimée par l'ORM "
            "(%s pièce(s) jointe(s)) par la campagne %s",
            self.res_id, count, self.campaign_id.name,
        )

    def _recruitment_destroy_applicant(self, applicant):
        candidate = applicant.candidate_id
        self._recruitment_refuse_if_hired(candidate)

        interviews = self.env["bf.interview"].sudo().with_context(
            active_test=False
        ).search([("applicant_id", "=", applicant.id)])
        self._recruitment_check_aggregate(interviews)

        # 🔴 Les séances D'ABORD, et par l'ORM. La contrainte de base est
        # `ON DELETE CASCADE` : supprimer la candidature les effacerait au
        # niveau SQL, sans que leurs pièces jointes ni leurs messages ne soient
        # jamais balayés.
        interview_count = len(interviews)
        if interviews:
            interviews.unlink()

        attachments = self._recruitment_purge_attachments(applicant)
        applicant.check_access("unlink")
        applicant.unlink()
        _logger.info(
            "bf_recruitment_privacy: hr.applicant,%s supprimée pour de bon "
            "(%s séance(s), %s pièce(s) jointe(s)) par la campagne %s",
            self.res_id, interview_count, attachments, self.campaign_id.name,
        )

        # 🔴 Et la personne, si c'était sa dernière candidature. Sans ça, le nom,
        # le courriel, le téléphone, le profil LinkedIn et le CV déposé sur la
        # personne survivent tous à la « destruction » de la candidature.
        self._recruitment_destroy_orphan_candidate(candidate)

    def _recruitment_destroy_orphan_candidate(self, candidate):
        if not candidate:
            return
        candidate = candidate.sudo().with_context(active_test=False)
        if not candidate.exists():
            return
        remaining = self.env["hr.applicant"].sudo().with_context(
            active_test=False
        ).search_count([("candidate_id", "=", candidate.id)])
        if remaining:
            _logger.info(
                "bf_recruitment_privacy: hr.candidate,%s gardée, il lui reste "
                "%s candidature(s)", candidate.id, remaining,
            )
            return
        attachments = self._recruitment_purge_attachments(candidate)
        name = candidate.partner_name or candidate.display_name
        partner = candidate.partner_id
        candidate.unlink()
        _logger.info(
            "bf_recruitment_privacy: hr.candidate,%s (%s) supprimée avec sa "
            "dernière candidature (%s pièce(s) jointe(s))",
            candidate.id, name, attachments,
        )
        self._recruitment_close_partner(partner)

    # ------------------------------------------------------------------
    # Le contact que le flux de recrutement a créé
    # ------------------------------------------------------------------

    def _recruitment_partner_references(self, partner):
        """Ce qui pointe encore vers ce contact, lu dans le CATALOGUE de la base.

        🔴 On interroge Postgres, pas le registre de l'ORM. Ce pont a déjà payé
        une fois le prix de leur divergence : `bf_interview.applicant_id` est
        `ON DELETE CASCADE` en base, ce qu'aucun champ du modèle ne dit. Un
        « personne ne le référence » calculé sur les champs déclarés se
        tromperait de la même façon, et il se tromperait en ATTESTANT.

        ⚠️ La ligne du contact lui-même est exclue : `res_partner` porte des
        clés qui pointent vers `res_partner`, dont `commercial_partner_id`, qui
        vaut son propre identifiant pour un contact sans société. Sans cette
        exclusion, aucun contact ne serait jamais orphelin.
        """
        self.env.flush_all()
        self.env.cr.execute(
            """
            SELECT cl.relname, a.attname
              FROM pg_constraint c
              JOIN pg_class cl ON cl.oid = c.conrelid
              JOIN pg_attribute a
                ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
             WHERE c.contype = 'f'
               AND c.confrelid = 'res_partner'::regclass
            """
        )
        porteurs = []
        for table, column in self.env.cr.fetchall():
            requete = SQL(
                "SELECT 1 FROM %s WHERE %s = %s",
                SQL.identifier(table), SQL.identifier(column), partner.id,
            )
            if table == "res_partner":
                requete = SQL("%s AND id != %s", requete, partner.id)
            self.env.cr.execute(SQL("%s LIMIT 1", requete))
            if self.env.cr.fetchone():
                porteurs.append("%s.%s" % (table, column))
        return porteurs

    def _recruitment_release_classifications(self, partner):
        """Une classification dont le dossier n'existe plus lâche le contact.

        🔴 Mesuré au banc le 2026-09-03, et c'est ce qui rendait la mesure
        inerte : `privacy.document.classification.subject_partner_id` pointe
        vers la personne, et la classification SURVIT à la destruction (elle est
        seulement désactivée). Le module se retenait donc lui-même : le contact
        était systématiquement « référencé », donc systématiquement archivé, et
        jamais détruit. Le garde aurait eu l'air de fonctionner.

        ⚠️ On ne touche qu'aux classifications dont l'enregistrement A DISPARU,
        et seulement sur les modèles de ce pont. Une classification vivante
        garde son sujet.

        ⚠️ L'attestation ne perd rien : `privacy.destruction.register` porte
        `res_name` et `subject_count`, du texte et un nombre, et AUCUNE clé
        vers `res.partner`. C'est le registre qui atteste, pas ce pointeur.
        """
        Classification = self.env["privacy.document.classification"].sudo()
        candidates = Classification.with_context(active_test=False).search([
            ("subject_partner_id", "=", partner.id),
            ("res_model", "in", list(_RECRUITMENT_MODELS)),
        ])
        orphelines = Classification
        for classification in candidates:
            cible = self.env[classification.res_model].sudo().with_context(
                active_test=False
            ).browse(classification.res_id)
            if not cible.exists():
                orphelines |= classification
        if orphelines:
            orphelines.write({"subject_partner_id": False})
            _logger.info(
                "bf_recruitment_privacy: %s classification(s) de dossiers "
                "détruits lâchent le contact %s",
                len(orphelines), partner.id,
            )
        return orphelines

    def _recruitment_close_partner(self, partner):
        """Le contact que le flux a créé ne survit pas à la personne en silence.

        ⚠️ Règle retenue : on le DÉTRUIT s'il n'est référencé
        nulle part, on l'ARCHIVE sinon. Un contact qui sert ailleurs (un client,
        un fournisseur, l'employé d'un autre dossier) n'est pas à nous.

        🔴 Sans ce geste, une destruction certifiée au registre laissait le nom
        et le courriel de la personne ACTIFS dans le carnet d'adresses, sans
        plus rien pour les rattacher à quoi que ce soit. Mesuré le 2026-08-31.
        """
        if not partner:
            return None
        partner = partner.sudo().with_context(active_test=False)
        if not partner.exists():
            return None

        # 🔴 Relâcher d'abord les classifications de dossiers DÉTRUITS.
        # Sans ça le module se retient lui-même, et n'aurait JAMAIS détruit un
        # seul contact : voir `_recruitment_release_classifications`.
        self._recruitment_release_classifications(partner)

        porteurs = self._recruitment_partner_references(partner)
        etiquette = partner.display_name
        if porteurs:
            partner.write({"active": False})
            _logger.info(
                "bf_recruitment_privacy: res.partner,%s (%s) ARCHIVÉ et non "
                "détruit, %s référence(s) le retiennent : %s",
                partner.id, etiquette, len(porteurs), ", ".join(porteurs[:5]),
            )
            return "archive"

        # ⚠️ Le catalogue ne connaît pas les gardes écrits en Python. Une
        # `@api.ondelete` du socle ou d'un autre module peut refuser après coup ;
        # on retombe alors sur l'archivage plutôt que d'emporter la campagne.
        try:
            with self.env.cr.savepoint():
                partner.unlink()
        except Exception as exc:
            partner.write({"active": False})
            _logger.info(
                "bf_recruitment_privacy: res.partner,%s (%s) ARCHIVÉ, sa "
                "suppression a été refusée : %s",
                partner.id, etiquette, str(exc)[:160].replace("\n", " "),
            )
            return "archive"
        _logger.info(
            "bf_recruitment_privacy: res.partner,%s (%s) supprimé, plus rien "
            "ne le référençait", partner.id, etiquette,
        )
        return "unlink"

    def _recruitment_destroy_candidate(self, candidate):
        """Détruire la personne directement : ses candidatures partent avec elle.

        ⚠️ `hr_applicant.candidate_id` est `ON DELETE RESTRICT` : la base refuse
        de supprimer une personne qui porte encore une candidature. Il faut donc
        passer par les candidatures, une à une, ce qui fait aussi jouer les
        gardes de l'agrégat et emporte les séances par l'ORM.
        """
        self._recruitment_refuse_if_hired(candidate)
        applicants = self.env["hr.applicant"].sudo().with_context(
            active_test=False
        ).search([("candidate_id", "=", candidate.id)])
        for applicant in applicants:
            interviews = self.env["bf.interview"].sudo().with_context(
                active_test=False
            ).search([("applicant_id", "=", applicant.id)])
            self._recruitment_check_aggregate(interviews)
            if interviews:
                interviews.unlink()
            applicant.unlink()

        attachments = self._recruitment_purge_attachments(candidate)
        partner = candidate.partner_id
        candidate.check_access("unlink")
        candidate.unlink()
        _logger.info(
            "bf_recruitment_privacy: hr.candidate,%s supprimée pour de bon "
            "(%s candidature(s), %s pièce(s) jointe(s)) par la campagne %s",
            self.res_id, len(applicants), attachments, self.campaign_id.name,
        )
        self._recruitment_close_partner(partner)
