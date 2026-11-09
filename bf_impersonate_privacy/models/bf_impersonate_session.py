"""La conservation du journal des incarnations, et la trace de sa purge.

Deux durées, parce que les deux moitiés du journal n'ont pas la même valeur de
preuve : le détail des gestes vit un an (INC-001), la séance elle-même trois
ans (INC-002). La purge efface pour vrai et inscrit chaque lot au registre de
destruction, par société.

⚠️ Elle ne touche jamais une séance OUVERTE, même échue : c'est le cron de
``bf_impersonate`` qui la ferme, et une séance sans fin n'a pas d'ancrage.

⚠️ Elle s'arrête d'elle-même si la règle est archivée, si sa disposition finale
n'est plus la destruction, ou si quelqu'un lui met une approbation : dans ces
trois cas, détruire sans rien dire serait pire que ne pas détruire.
"""
import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

CAL_DETAIL = "bf_impersonate_privacy.retention_impersonation_detail"
CAL_SESSION = "bf_impersonate_privacy.retention_impersonation_session"

# Fuseau de la date écrite au registre : la Loi 25 est québécoise, et l'usager
# du cron est en UTC pendant que la société principale de BF est à Auckland.
TZ_REGISTRE = "America/Toronto"


class BfImpersonateSession(models.Model):
    _inherit = "bf.impersonate.session"

    # ------------------------------------------------------------------
    # La purge

    @api.model
    def _cron_privacy_purge_journal(self):
        """Le détail d'abord, la séance ensuite ; rend ce que chacun a emporté."""
        lignes = self._bf_privacy_purge_detail()
        seances = self._bf_privacy_purge_sessions()
        if lignes or seances:
            _logger.info(
                "bf_impersonate_privacy : %s ligne(s) et %s séance(s) détruites",
                lignes, seances)
        return lignes, seances

    @api.model
    def _bf_privacy_calendar(self, xmlid):
        """La règle, ou rien du tout si elle ne prescrit plus de détruire."""
        calendar = self.env.ref(xmlid, raise_if_not_found=False)
        if not calendar or not calendar.active:
            return None
        if calendar.final_disposition != "destroy":
            return None
        if calendar.requires_approval:
            # Quelqu'un a décidé qu'un humain doit approuver : la purge
            # automatique n'a plus le droit de passer, et se taire la ferait
            # disparaître sans que personne ne le sache.
            _logger.warning(
                "bf_impersonate_privacy : %s exige une approbation, purge non exécutée",
                calendar.code)
            return None
        return calendar

    @api.model
    def _bf_privacy_cutoff(self, calendar):
        """La date avant laquelle une séance close est échue."""
        jours = calendar.total_retention_days or 0
        if jours:
            return fields.Datetime.subtract(fields.Datetime.now(), days=jours)
        annees = (calendar.active_retention_years or 0) + (
            calendar.semi_active_retention_years or 0)
        return fields.Datetime.now() - relativedelta(years=annees)

    @api.model
    def _bf_privacy_purge_detail(self):
        """INC-001 : les lignes des séances closes depuis plus d'un an.

        Ancré sur la FIN de la séance et non sur la date de la ligne : une
        séance se lit d'un bloc, et un détail qui part avant son en-tête
        laisserait un résumé qui ne résume plus rien.
        """
        calendar = self._bf_privacy_calendar(CAL_DETAIL)
        if not calendar:
            return 0
        limite = self._bf_privacy_cutoff(calendar)
        sessions = self.sudo().search([
            ("date_end", "!=", False), ("date_end", "<", limite),
            ("line_ids", "!=", False),
        ])
        if not sessions:
            return 0
        total = 0
        for company, lot in sessions._bf_privacy_par_societe().items():
            lignes = lot.line_ids
            nombre = len(lignes)
            if not nombre:
                continue
            # Compter AVANT d'effacer : après, rien ne distingue plus ces lignes.
            personnes = len(lot.target_user_id)
            lignes.sudo().unlink()
            total += nombre
            self._bf_privacy_register(
                # « Enregistrement entier » : c'est la LIGNE qui disparaît en
                # entier, et l'en-tête de la séance qui survit. « Partie de
                # l'enregistrement » affirmerait qu'une ligne subsiste amputée,
                # ce qui serait faux au registre.
                calendar=calendar, company=company, scope="full",
                subject_count=personnes,
                res_model="bf.impersonate.session.line",
                res_field=False,
                name=_("Journal des incarnations — détail détruit le %s",
                       self._bf_privacy_today()),
                description=_(
                    "%(lignes)s ligne(s) de journal détruites, portant sur "
                    "%(seances)s séance(s) d'incarnation closes depuis plus de "
                    "%(jours)s jours. L'en-tête de chaque séance subsiste (qui a "
                    "vu Symbifox en tant que qui, pourquoi, quand, combien de "
                    "temps, comment la séance a fini) : il relève d'INC-002 et "
                    "se conserve trois ans.",
                    lignes=nombre, seances=len(lot),
                    jours=calendar.total_retention_days or "",
                ),
                pi_categories=_(
                    "Modèles et identifiants des fiches touchées ou consultées, "
                    "nom que ces fiches portaient au moment du geste, champs "
                    "écrits, relevé des écritures, compteur des consultations."
                ),
            )
        return total

    @api.model
    def _bf_privacy_purge_sessions(self):
        """INC-002 : les séances closes depuis plus de trois ans, en entier."""
        calendar = self._bf_privacy_calendar(CAL_SESSION)
        if not calendar:
            return 0
        limite = self._bf_privacy_cutoff(calendar)
        sessions = self.sudo().search([
            ("date_end", "!=", False), ("date_end", "<", limite),
        ])
        if not sessions:
            return 0
        total = 0
        for company, lot in sessions._bf_privacy_par_societe().items():
            nombre = len(lot)
            personnes = len(lot.target_user_id)
            lot.sudo().unlink()
            total += nombre
            self._bf_privacy_register(
                calendar=calendar, company=company, scope="full",
                subject_count=personnes,
                res_model="bf.impersonate.session",
                res_field=False,
                name=_("Journal des incarnations — séances détruites le %s",
                       self._bf_privacy_today()),
                description=_(
                    "%(seances)s séance(s) d'incarnation détruites en entier, "
                    "closes depuis plus de %(jours)s jours : qui a ouvert la "
                    "séance, en tant que qui, le motif, le mode, le début, la "
                    "fin et sa cause, la durée, l'adresse IP, les lignes de "
                    "journal restantes et les avis envoyés à la personne.",
                    seances=nombre, jours=calendar.total_retention_days or "",
                ),
                pi_categories=_(
                    "Identité de la personne vue et de celle qui regardait, "
                    "motif de la séance, horodatage et durée, adresse IP."
                ),
            )
        return total

    def _bf_privacy_par_societe(self):
        """``{société: séances}``, d'après la société de qui a incarné.

        PAS ``env.company``, qui est celle de l'usager du cron : le registre
        chaîne ses empreintes par société, et une entrée portée à la mauvaise
        société entrerait dans la mauvaise chaîne.
        """
        principale = self.env.ref("base.main_company", raise_if_not_found=False)
        lots = {}
        for session in self.sudo():
            company = session.user_id.company_id or principale or self.env.company
            lots[company] = lots.get(company, self.browse()) | session
        return lots

    @api.model
    def _bf_privacy_today(self):
        return fields.Date.to_string(
            fields.Date.context_today(self.with_context(tz=TZ_REGISTRE)))

    @api.model
    def _bf_privacy_register(self, calendar, company, scope, subject_count,
                             res_model, res_field, name, description, pi_categories):
        """Une entrée de lot au registre immuable.

        Pas de ``res_id`` : l'entrée vise un lot. Nommer les séances une par une
        recopierait au registre, sans borne cette fois, ce qu'on vient d'y
        détruire.
        """
        vals = {
            "destruction_date": fields.Datetime.now(),
            "destroyed_by_id": self.env.user.id,
            # Exigé par le registre. Les règles INC-001 et INC-002 ne demandent
            # aucune approbation (et la purge s'arrête si on leur en met une) :
            # celui qui exécute est celui qui répond.
            "approved_by_id": self.env.user.id,
            "res_model": res_model,
            "res_name": name,
            "document_description": description,
            "pi_categories": pi_categories,
            "subject_count": subject_count,
            # `delete` et non `secure_wipe` : la valeur d'avant survit dans les
            # blocs de la table, le journal d'écriture de Postgres et les
            # sauvegardes.
            "destruction_method": "delete",
            "destruction_scope": scope,
            "legal_basis": calendar.legal_basis or _(
                "Art. 23 LPRPSP : le renseignement personnel est détruit lorsque "
                "la fin pour laquelle il a été recueilli est accomplie."),
            "company_id": company.id,
            "retention_calendar_id": calendar.id,
        }
        if res_field:
            vals["res_field"] = res_field
        return self.env["privacy.destruction.register"].sudo().create(vals)
