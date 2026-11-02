import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

CALENDAR_XMLID = "bf_security_awareness_privacy.retention_security_awareness"
GRACE_PARAM = "bf_security_awareness_privacy.purge_grace_days"
DEFAULT_GRACE_DAYS = 30


class BfSecurityProfile(models.Model):
    """Conservation du profil de risque : ancrée sur le départ de la personne.

    Le profil sert à mesurer, puis à corriger, le comportement de quelqu'un qui
    fait partie de l'organisation. Le jour où cette personne part, la fin pour
    laquelle il a été recueilli est accomplie — c'est le déclencheur de
    l'article 23 LPRPSP, et pas la date de la campagne.
    """

    _inherit = "bf.security.profile"

    privacy_departure_date = fields.Date(
        string="Date de départ",
        compute="_compute_privacy_retention",
        # 🔴 Réservé au responsable de la vie privée depuis la 18.0.1.3.0.
        # `hr` réserve la date de départ aux RH ; la republier ici à
        # l'opérateur de sensibilisation élargissait une visibilité RH sans
        # le dire. Pas `hr.group_hr_user` : ce module ne dépend pas de `hr`,
        # et un xmlid non résolu masquerait le champ pour tout le monde.
        groups="privacy_consent.group_privacy_officer",
        help="Date de départ lue sur la fiche employé liée à cette personne. "
             "Vide lorsqu'aucune fiche n'est liée : la règle de conservation "
             "n'a alors aucun point d'ancrage.",
    )
    privacy_retention_due_date = fields.Date(
        string="Destruction due le",
        compute="_compute_privacy_retention",
        # Même réserve : échéance moins la durée de la règle et le délai de
        # grâce, tous deux connus, rend la date de départ.
        groups="privacy_consent.group_privacy_officer",
        help="Départ + durée de la règle de conservation + délai d'application.",
    )
    privacy_retention_status = fields.Selection(
        selection=[
            ("employed", "En poste"),
            ("pending", "Départ connu — échéance à venir"),
            ("due", "Échue — à détruire"),
            ("unknown", "Aucune date de départ résolvable"),
        ],
        string="Conservation",
        compute="_compute_privacy_retention",
        search="_search_privacy_retention_status",
    )

    # ------------------------------------------------------------------ #
    # Paramètres de la règle
    # ------------------------------------------------------------------ #
    @api.model
    def _privacy_retention_calendar(self):
        """La règle du calendrier de conservation, ou un recordset vide.

        C'est elle qui gouverne : pas de règle, pas de destruction.

        ⚠️ `sudo()` : la règle vit dans `privacy_consent`, dont les ACL sont
        réservées aux groupes vie privée. Un opérateur de la sensibilisation
        n'y a pas accès, et lire le statut de conservation d'un profil lui
        rendrait une AccessError sur un champ qu'on lui affiche.
        """
        calendar = self.env.ref(CALENDAR_XMLID, raise_if_not_found=False)
        return (calendar or self.env["privacy.retention.calendar"]).sudo()

    @api.model
    def _privacy_grace_days(self):
        """Délai d'application, en jours, avant que la passe agisse.

        Ce n'est PAS une conservation supplémentaire justifiée en droit : il
        couvre la saisie tardive d'un départ et la révision de la personne
        responsable. Un DPO qui le veut à zéro le met à zéro.
        """
        raw = self.env["ir.config_parameter"].sudo().get_param(GRACE_PARAM)
        try:
            return max(0, int(float(raw)))
        except (TypeError, ValueError):
            return DEFAULT_GRACE_DAYS

    # ------------------------------------------------------------------ #
    # Résolution du départ
    # ------------------------------------------------------------------ #
    @api.model
    def _privacy_departure_map(self, partners):
        """Rend {partner_id: (date_de_depart, encore_en_poste)}.

        Dépendance douce à `hr` : le module ne le déclare pas dans ses
        `depends`, il regarde si le modèle est là.

        🔴 `active_test=False` est le garde-fou qui décide de tout. La fiche
        d'une personne partie est ARCHIVÉE : une recherche ordinaire ne voit
        jamais personne de parti, la passe conclurait « aucun départ connu »
        pour tout le monde et ne détruirait rien, sans un mot au journal.
        Vérifié par mutation : le retirer fait tomber le contrôle.

        ⚠️ `sudo()` est défensif, et il vaut mieux savoir pourquoi. Mesuré
        plutôt que supposé : `hr.employee.departure_date` porte
        `groups="hr.group_hr_user"`, mais ce garde-fou ne mord qu'à la
        frontière `read()` — un `AccessError` y est levé — tandis que l'accès
        par ATTRIBUT (`emp.departure_date`) rend la vraie valeur à n'importe
        quel utilisateur interne. Le code ci-dessous n'utilise que l'attribut,
        donc le `sudo()` n'est pas ce qui fait marcher la lecture. Il couvre
        deux autres cas : un locataire dont les ACL ou les règles
        d'enregistrement de `hr.employee` sont resserrées (la recherche
        rendrait alors un jeu tronqué, donc des départs invisibles), et tout
        appel futur qui passerait par `read()`.
        """
        resolved = {pid: (False, False) for pid in partners.ids}
        if not partners or "hr.employee" not in self.env:
            return resolved
        employees = self.env["hr.employee"].sudo().with_context(
            active_test=False).search([
                "|",
                ("work_contact_id", "in", partners.ids),
                ("user_id.partner_id", "in", partners.ids),
            ])
        for emp in employees:
            linked = {emp.work_contact_id.id, emp.user_id.partner_id.id}
            for pid in linked:
                if pid not in resolved:
                    continue
                departure, employed = resolved[pid]
                # Une fiche active sans date de départ tranche : la personne
                # est en poste, même si une fiche antérieure porte un départ
                # (réembauche).
                if emp.active and not emp.departure_date:
                    resolved[pid] = (departure, True)
                    continue
                if emp.departure_date and (
                        not departure or emp.departure_date > departure):
                    departure = emp.departure_date
                resolved[pid] = (departure, employed)
        return resolved

    def _privacy_retention_info(self):
        """Rend {profile_id: {status, departure, due}} pour tout le recordset.

        Résolu en une seule recherche pour l'ensemble : la compute est appelée
        sur la liste complète des profils, et une requête par ligne s'y verrait.
        """
        calendar = self._privacy_retention_calendar()
        extra_days = calendar.total_retention_days if calendar else 0
        grace = self._privacy_grace_days()
        today = fields.Date.context_today(self)
        departures = self._privacy_departure_map(self.mapped("partner_id"))

        info = {}
        for prof in self:
            departure, employed = departures.get(prof.partner_id.id, (False, False))
            if employed:
                status, due = "employed", False
            elif not departure:
                status, due = "unknown", False
            else:
                due = departure + timedelta(days=extra_days + grace)
                status = "due" if due <= today else "pending"
            info[prof.id] = {
                "status": status, "departure": departure, "due": due}
        return info

    @api.depends("partner_id")
    def _compute_privacy_retention(self):
        # Champ NON stocké, volontairement : la date de départ vit sur
        # `hr.employee`, donc un champ stocké se figerait au dernier écrit sur
        # le profil et mentirait dès qu'un départ est saisi ensuite.
        info = self._privacy_retention_info()
        for prof in self:
            data = info.get(prof.id) or {}
            prof.privacy_departure_date = data.get("departure") or False
            prof.privacy_retention_due_date = data.get("due") or False
            prof.privacy_retention_status = data.get("status") or "unknown"

    def _search_privacy_retention_status(self, operator, value):
        """Rend le statut cherchable depuis les filtres du responsable.

        ⚠️ Un champ calculé NON stocké sans méthode de recherche n'est pas
        « ignoré en silence » ici : Odoo refuse le domaine. Et le stocker
        serait pire — la date de départ vit sur `hr.employee`, donc la valeur
        se figerait au dernier écrit sur le profil.
        """
        if operator not in ("=", "!=", "in", "not in"):
            raise NotImplementedError(
                "Opérateur « %s » non pris en charge sur « Conservation »."
                % operator)
        wanted = value if isinstance(value, (list, tuple)) else [value]
        negate = operator in ("!=", "not in")

        profiles = self.sudo().search([])
        info = profiles._privacy_retention_info()
        matched = [p.id for p in profiles
                   if (info[p.id]["status"] in wanted) != negate]
        return [("id", "in", matched)]

    # ------------------------------------------------------------------ #
    # Destruction
    # ------------------------------------------------------------------ #
    @api.model
    def _cron_privacy_purge_departed(self, limit=200):
        """Détruit le profil des personnes parties depuis assez longtemps.

        🔴 Le cron qui appelle cette méthode est livré ÉTEINT : tant que
        personne ne l'allume, rien n'est détruit.

        Rend le nombre de profils détruits.
        """
        calendar = self._privacy_retention_calendar()
        if not calendar:
            _logger.warning(
                "bf_security_awareness_privacy : règle de conservation %s "
                "introuvable, aucune destruction. Pas de règle, pas de purge.",
                CALENDAR_XMLID)
            return 0
        if not calendar.active:
            _logger.info(
                "bf_security_awareness_privacy : la règle %s est archivée, "
                "aucune destruction.", calendar.code)
            return 0
        # La règle gouverne pour vrai : si le responsable la passe à
        # « anonymisation » ou « conservation permanente », la passe se tait.
        if calendar.final_disposition != "destroy":
            _logger.info(
                "bf_security_awareness_privacy : sort final « %s » sur la "
                "règle %s, aucune destruction automatique.",
                calendar.final_disposition, calendar.code)
            return 0

        profiles = self.sudo().search([])
        info = profiles._privacy_retention_info()
        due = profiles.filtered(lambda p: info[p.id]["status"] == "due")
        unknown = profiles.filtered(lambda p: info[p.id]["status"] == "unknown")

        purged = 0
        for prof in due[:limit]:
            try:
                # Un profil qui casse ne doit pas emporter la tournée : le
                # point de reprise annule ce profil-là et seulement lui.
                with self.env.cr.savepoint():
                    prof._privacy_purge_departed(info[prof.id])
                purged += 1
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "bf_security_awareness_privacy : destruction du profil %s "
                    "(%s) en échec, la tournée continue.",
                    prof.id, prof.partner_id.display_name)

        if unknown:
            # ⚠️ Ne pas taire ce compte : ce sont les personnes pour qui la
            # règle ne s'applique à rien, faute d'ancrage. Un journal muet
            # laisserait croire que tout est couvert.
            _logger.info(
                "bf_security_awareness_privacy : %s profil(s) sans date de "
                "départ résolvable — rien n'a été détruit pour eux.",
                len(unknown))
        if purged:
            _logger.info(
                "bf_security_awareness_privacy : %s profil(s) de risque "
                "détruits et inscrits au registre.", purged)
        return purged

    def _privacy_purge_departed(self, info):
        """Détruit ce profil et inscrit la destruction au registre.

        ⚠️ L'inventaire est figé AVANT toute suppression, et l'entrée de
        registre est écrite dans la MÊME transaction que la destruction. Si
        l'écriture au registre casse, le point de reprise annule aussi la
        suppression : jamais de données détruites sans trace, jamais de trace
        certifiant une destruction qui n'a pas eu lieu.
        """
        self.ensure_one()
        partner = self.partner_id
        results = self.result_ids
        assignments = self.assignment_ids

        snapshot = {
            "res_id": self.id,
            "res_name": partner.display_name,
            "company_id": (self.company_id or self.env.company).id,
            "results": len(results),
            "assignments": len(assignments),
            "campaigns": sorted(set(results.mapped("campaign_id.name"))),
            "courses": sorted(set(assignments.mapped("channel_id.name"))),
            "departure": info.get("departure"),
        }

        # Les traces d'envoi de masse portent l'adresse ciblée et l'état de
        # remise : c'est du résidu direct de nos propres envois, il part avec.
        # Rien ne les rattache par clé étrangère, donc rien ne les emporterait.
        traces = self.env["mailing.trace"].sudo().search([
            ("model", "=", "bf.phishing.result"),
            ("res_id", "in", results.ids),
        ]) if results else self.env["mailing.trace"]

        trace_count = len(traces)
        # On garde une poignée sur le modèle : `self` n'existe plus après
        # `unlink()`, et l'inscription au registre se fait après coup.
        Profile = self.env["bf.security.profile"]

        traces.unlink()
        results.unlink()
        assignments.unlink()
        self.unlink()

        return Profile._privacy_register_destruction(snapshot, trace_count)

    @api.model
    def _privacy_register_destruction(self, snapshot, trace_count=0):
        """Écrit l'entrée immuable de l'art. 3.2 LPRPSP pour ce profil."""
        Register = self.env["privacy.destruction.register"].sudo()
        user = self.env.user

        lines = [
            _("Profil de risque de sécurité de « %(name)s », détruit au départ "
              "de la personne (%(departure)s).",
              name=snapshot["res_name"],
              departure=snapshot["departure"] or _("date inconnue")),
            _("Résultats de simulation d'hameçonnage supprimés : %s",
              snapshot["results"]),
            _("Assignations de formation supprimées : %s",
              snapshot["assignments"]),
            _("Traces d'envoi de masse supprimées : %s", trace_count),
        ]
        if snapshot["campaigns"]:
            lines.append(_("Campagnes concernées : %s",
                           ", ".join(snapshot["campaigns"])))
        if snapshot["courses"]:
            lines.append(_("Formations concernées : %s",
                           ", ".join(snapshot["courses"])))
        # Dire aussi ce qui SURVIT : une entrée qui n'énumère que le détruit
        # se lit comme si plus rien ne restait sur la personne.
        lines.append(_(
            "Non visés par cette destruction : les signalements de courriel "
            "(bf.reported.phish) et les retraits (bf.clawback.*), qui relèvent "
            "du traitement d'incident ; l'inscription au cours dans le LMS "
            "(slide.channel.partner), qui relève du dossier employé."))

        vals = {
            "destruction_date": fields.Datetime.now(),
            "destroyed_by_id": user.id,
            "approved_by_id": user.id,
            "res_model": "bf.security.profile",
            "res_id": snapshot["res_id"],
            "res_name": snapshot["res_name"],
            "document_description": "\n".join(lines),
            "pi_categories": _(
                "Évaluation du comportement en sécurité de l'information : "
                "ouvertures, clics, soumission d'un faux formulaire, "
                "signalements, score de risque. Éléments techniques associés : "
                "adresse courriel ciblée, adresse IP et agent utilisateur du "
                "clic, longueurs des champs saisis (jamais leur valeur)."),
            "subject_count": 1,
            # `delete` et non `secure_wipe` : on supprime des lignes dans
            # PostgreSQL. Les blocs sous-jacents et les sauvegardes existantes
            # ne sont pas touchés — déclarer un effacement sécurisé serait un
            # surclaim.
            "destruction_method": "delete",
            # 🔴 Portée COMPLÈTE, et il faut que le champ le dise. La
            # méthode fait `self.unlink()` : le profil part, avec ses
            # événements, ses affectations et ses traces d'envoi. Déclarer
            # « partiel » sous-déclarerait pour toujours ce qui a été détruit,
            # dans un registre qui ne se reprend pas, et le `res_id` de
            # l'entrée désignerait une ligne qui n'existe plus. La garde du
            # socle ne refuse une entrée que si l'enregistrement EXISTE
            # ENCORE ; ici il est parti, donc elle laisse passer.
            "destruction_scope": "full",
            "legal_basis": _(
                "Art. 23 LPRPSP : le renseignement personnel est détruit "
                "lorsque la fin pour laquelle il a été recueilli ou utilisé "
                "est accomplie. Cette fin est la mesure de sécurité de "
                "l'art. 10 LPRPSP appliquée aux personnes de l'organisation ; "
                "elle prend fin au départ de la personne."),
            "company_id": snapshot["company_id"],
        }
        calendar = self._privacy_retention_calendar()
        if calendar:
            vals["retention_calendar_id"] = calendar.id
        return Register.create(vals)
