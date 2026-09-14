import json

from odoo import _, api, fields, models

#: Les états terminaux, par version. 🔴 « incomplete » et « not attempted »
#: n'en sont PAS : une tentative en cours n'est pas un échec, et la traiter
#: comme tel ferait refaire le cours à quelqu'un qui l'a simplement mis en pause.
TERMINAUX_REUSSIS = {
    # SCORM 1.2 : cmi.core.lesson_status
    "1.2": ("completed", "passed"),
    # SCORM 2004 : cmi.completion_status + cmi.success_status
    "2004": ("completed", "passed"),
}
TERMINAUX_ECHOUES = ("failed",)


class ScormAttempt(models.Model):
    _name = "bf.scorm.attempt"
    _description = "Tentative SCORM"
    _order = "write_date desc"

    package_id = fields.Many2one(
        "bf.scorm.package", string="Paquet", required=True,
        ondelete="cascade", index=True)
    partner_id = fields.Many2one(
        "res.partner", string="Apprenant", required=True, index=True)
    slide_id = fields.Many2one(
        related="package_id.slide_id", store=True, string="Contenu")
    cmi_data = fields.Text(
        string="Données CMI", default="{}",
        help="Le modèle de données CMI tel que le contenu l'a écrit, en JSON.")
    lesson_status = fields.Char(string="État rapporté", readonly=True)
    score_raw = fields.Float(string="Score", readonly=True)
    score_max = fields.Float(string="Score maximal", readonly=True)
    total_time = fields.Char(string="Temps passé", readonly=True)
    completed = fields.Boolean(string="Terminé", readonly=True, index=True)
    date_completed = fields.Datetime(
        string="Terminé le", readonly=True,
        help="Écrit au moment où le contenu a rapporté son état terminal. Ce "
             "n'est pas un calcul : une date qui dépend d'un événement se pose "
             "quand l'événement arrive.")

    _sql_constraints = [
        ("unique_par_apprenant", "unique (package_id, partner_id)",
         "Une seule tentative par paquet et par apprenant."),
    ]

    # ------------------------------------------------------------------
    # Le modèle de données CMI
    # ------------------------------------------------------------------
    def _donnees(self):
        self.ensure_one()
        try:
            return json.loads(self.cmi_data or "{}")
        except ValueError:
            return {}

    def _lire(self, cle):
        return self._donnees().get(cle, "")

    def _ecrire(self, paires):
        """Poser des valeurs CMI, puis en tirer l'état.

        ⚠️ On fusionne au lieu de remplacer : un contenu écrit ses éléments un
        par un, et remplacer le dictionnaire perdrait `suspend_data` dès que le
        contenu poserait autre chose.
        """
        self.ensure_one()
        donnees = self._donnees()
        donnees.update({k: v for k, v in paires.items() if k})
        self.cmi_data = json.dumps(donnees)
        self._appliquer_etat(donnees)
        return True

    def _appliquer_etat(self, donnees):
        """Tirer l'état lisible des clés CMI, selon la version du paquet."""
        self.ensure_one()
        version = self.package_id.version or "1.2"
        if version == "2004":
            # 🔴 En 2004 il y a DEUX axes, et ils ne disent pas la même chose :
            # `completion_status` dit si le contenu a été parcouru,
            # `success_status` s'il a été réussi. Un contenu peut être terminé
            # ET échoué. On garde la réussite quand elle est posée, la complétion
            # sinon — lire un seul des deux perdrait la moitié de l'information.
            succes = (donnees.get("cmi.success_status") or "").lower()
            completion = (donnees.get("cmi.completion_status") or "").lower()
            etat = succes if succes in ("passed", "failed") else completion
            brut = donnees.get("cmi.score.raw")
            maxi = donnees.get("cmi.score.max")
            temps = donnees.get("cmi.total_time")
        else:
            etat = (donnees.get("cmi.core.lesson_status") or "").lower()
            brut = donnees.get("cmi.core.score.raw")
            maxi = donnees.get("cmi.core.score.max")
            temps = donnees.get("cmi.core.total_time")

        valeurs = {"lesson_status": etat or False, "total_time": temps or False}
        for champ, valeur in (("score_raw", brut), ("score_max", maxi)):
            try:
                valeurs[champ] = float(valeur) if valeur not in (None, "") else 0.0
            except (TypeError, ValueError):
                valeurs[champ] = 0.0

        termine = etat in TERMINAUX_REUSSIS.get(version, ())
        if version == "2004" and etat == "failed":
            # Terminé mais échoué : on le consigne, sans marquer le contenu suivi.
            termine = False
        if termine and not self.completed:
            valeurs["completed"] = True
            valeurs["date_completed"] = fields.Datetime.now()
        self.write(valeurs)
        if termine:
            self._marquer_le_contenu_termine()

    def _marquer_le_contenu_termine(self):
        """Faire porter la complétion par le lecteur, pas par nous.

        🔴 On appelle `_action_mark_completed` du natif plutôt que d'écrire
        nous-mêmes au registre : c'est le pont eLearning qui sait écrire une
        réalisation datée, et dupliquer ce geste ici donnerait deux chemins qui
        divergeraient au premier correctif.
        """
        self.ensure_one()
        diapo = self.package_id.slide_id
        if not diapo:
            return
        diapo.sudo().with_user(
            self.partner_id.user_ids[:1] or self.env.user
        )._action_mark_completed()

    @api.model
    def _pour(self, paquet, partenaire):
        """La tentative de cette personne sur ce paquet, créée au besoin."""
        tentative = self.sudo().search([
            ("package_id", "=", paquet.id), ("partner_id", "=", partenaire.id)
        ], limit=1)
        if tentative:
            return tentative
        return self.sudo().create({
            "package_id": paquet.id, "partner_id": partenaire.id})
