"""Journal d'humeur de Healthy Fox.

Un journal de plus dans la saisie
quotidienne. Cinq crans, des activités à cocher, un texte facultatif. Un rappel
à l'heure choisie, sans série de jours. Historique, export et PDF pour le
médecin toujours libres. Rien ne quitte l'instance.

**Privé à chaque personne, administrateurs compris.** Règles GLOBALES (sans
groupe) sur ``create_uid`` : une règle de groupe se contourne en s'ajoutant un
groupe, pas une règle globale. Seul le superutilisateur (crons, coquille, base)
y échappe.

**Gen n'y a aucun accès** sans consentement ou conversation lancée depuis une
fiche du journal : les modèles déclarent la portée ``bf_health.mood`` par un
simple attribut de classe (``_gen_scope``), lu par le verrou générique de
``bf_claude_chat`` s'il est installé. Healthy Fox ne dépend pas de Gen.

🔴 Aucun SQL brut sur l'humeur : tout passe par l'ORM sous les droits de
l'appelant, sinon le verrou de Gen (une règle d'accès) ne s'appliquerait pas.
"""
import logging
import math
from datetime import datetime, time, timedelta

import pytz
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.misc import format_date, formatLang
from odoo.tools.translate import LazyTranslate

from .gen_portees import (
    LIBELLE_HUMEUR, LIBELLE_SAISIE, LIBELLE_SANTE, PORTEE_HUMEUR, PORTEE_SAISIE, PORTEE_SANTE,
)
from .parent_guard import garder_parents

_lt = LazyTranslate(__name__)
_logger = logging.getLogger(__name__)

#: La portée du verrou de Gen, partagée par tous les modèles de l'humeur.
PORTEE_GEN = PORTEE_HUMEUR
LIBELLE_GEN = LIBELLE_HUMEUR

NIVEAUX = [
    ("1", "Très mal"),
    ("2", "Mal"),
    ("3", "Correct"),
    ("4", "Bien"),
    ("5", "Très bien"),
]
VISAGES = {"1": "😣", "2": "🙁", "3": "😐", "4": "🙂", "5": "😄"}

#: Les activités semées pour chaque personne à sa première visite. Les noms
#: passent par `_()` dans la langue de la personne au moment du semis.
ACTIVITES_DE_DEPART = [
    (_lt("Sport"), "🏃"),
    (_lt("Plein air"), "🌳"),
    (_lt("Famille"), "👪"),
    (_lt("Amis"), "💬"),
    (_lt("Travail"), "💼"),
    (_lt("Repos"), "🛋️"),
    (_lt("Lecture"), "📖"),
    (_lt("Écrans"), "📱"),
    (_lt("Création"), "🎨"),
    (_lt("Méditation"), "🧘"),
]

#: En deçà, on ne parle pas de lien : trop peu de jours appariés.
MIN_JOURS_PEARSON = 7
#: En deçà, on ne compare pas deux groupes de jours.
MIN_JOURS_GROUPE = 3
#: Le seuil de sommeil de la comparaison simple.
SEUIL_SOMMEIL_H = 7.0


class HealthMoodActivity(models.Model):
    _name = "health.mood.activity"
    _description = "Activité du journal d'humeur"
    _order = "sequence, name, id"
    _gen_scope = PORTEE_GEN
    _gen_scope_label = LIBELLE_GEN

    name = fields.Char(string="Activité", required=True)
    icon = fields.Char(string="Icône", size=16, help="Un émoji.")
    sequence = fields.Integer(string="Séquence", default=10)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("bf_mood_activity_unique", "unique(create_uid, name)",
         "Cette activité existe déjà."),
    ]

    @api.model
    def _bf_semer_pour_moi(self):
        """Les activités de départ de la personne courante, une seule fois."""
        if self.with_context(active_test=False).search_count([("create_uid", "=", self.env.uid)]):
            return self.browse()
        valeurs = [{"name": str(nom), "icon": icone, "sequence": 10 * (i + 1)}
                   for i, (nom, icone) in enumerate(ACTIVITES_DE_DEPART)]
        return self.create(valeurs)


class HealthMoodEntry(models.Model):
    _name = "health.mood.entry"
    _description = "Humeur"
    _inherit = ["bf.health.parent.guard"]
    _order = "date desc, id desc"
    _bf_champs_parents = ("activity_ids",)
    _gen_scope = PORTEE_GEN
    _gen_scope_label = LIBELLE_GEN

    date = fields.Date(
        string="Date", required=True, index=True, default=fields.Date.context_today)
    level = fields.Selection(NIVEAUX, string="Humeur", required=True)
    score = fields.Integer(
        string="Humeur (1 à 5)", compute="_compute_score", store=True, aggregator="avg")
    activity_ids = fields.Many2many(
        "health.mood.activity", "health_mood_entry_activity_rel",
        "entry_id", "activity_id", string="Activités")
    note = fields.Text(string="Note")
    client_uuid = fields.Char(
        string="Identifiant de la saisie au téléphone", index=True, copy=False, readonly=True,
        help="Tiré par la page du téléphone : une saisie renvoyée après une coupure "
             "retrouve la sienne au lieu d'en créer une seconde.")

    _sql_constraints = [
        ("bf_mood_client_uuid_unique", "unique(create_uid, client_uuid)",
         "Cette saisie est déjà enregistrée."),
    ]

    @api.depends("level")
    def _compute_score(self):
        for rec in self:
            rec.score = int(rec.level) if rec.level else 0

    @api.depends("date")
    def _compute_display_name(self):
        """Nom NEUTRE : ni cran ni note. Il voyage dans les titres de
        conversation de Gen, les activités et la liste des abonnés."""
        for rec in self:
            rec.display_name = _("Humeur du %s", format_date(self.env, rec.date)) if rec.date \
                else _("Humeur")

    @api.constrains("activity_ids")
    def _check_activites_lisibles(self):
        """On ne coche qu'une activité qu'on peut lire : sinon son nom se relit
        par le Many2many."""
        self._bf_verifier_parents()

    @api.model_create_multi
    def create(self, vals_list):
        entrees = super().create(vals_list)
        # Une saisie efface le rappel du jour : jamais d'activité qui traîne.
        if not self.env.su:
            self.env["health.mood.settings"]._bf_effacer_rappels(self.env.user)
        return entrees


class HealthMoodSettings(models.Model):
    _name = "health.mood.settings"
    _description = "Journal d'humeur : réglages"
    # `mail.thread` n'est là que pour l'activité du rappel (Odoo y abonne la
    # personne). La fiche n'a pas de fil à l'écran et rien n'y est posté.
    _inherit = ["bf.health.note.only", "mail.thread", "mail.activity.mixin"]
    _rec_name = "user_id"
    _gen_scope = PORTEE_GEN
    _gen_scope_label = LIBELLE_GEN

    user_id = fields.Many2one(
        "res.users", string="Personne", compute="_compute_user_id", store=True,
        readonly=True, index=True)
    reminder_enabled = fields.Boolean(string="Rappel quotidien", default=True)
    reminder_time = fields.Float(
        string="Heure du rappel", default=20.0,
        help="Heure locale, selon le fuseau de vos préférences.")
    reminder_tz = fields.Char(string="Fuseau", compute="_compute_reminder_tz")
    last_reminder_date = fields.Date(string="Dernier rappel", readonly=True, copy=False)
    # Corrélations : du profilage au sens de la Loi 25 (art. 8.1). DÉSACTIVÉES
    # par défaut ; la personne les active elle-même, informée.
    correlations_enabled = fields.Boolean(
        string="Calculer les corrélations", default=False,
        help="Compare votre humeur à votre sommeil, vos médicaments, vos symptômes et vos "
             "activités. Le calcul se fait dans votre instance, sur vos seules données, et "
             "seulement si vous l'activez. Vous pouvez le désactiver en tout temps.")
    # Gen : deux portes, fermées par défaut. L'instance (paramètre réservé à
    # l'administration système), puis la personne.
    gen_available = fields.Boolean(compute="_compute_gen")
    gen_instance_open = fields.Boolean(
        string="Gen ouvert à l'humeur sur cette instance", compute="_compute_gen")
    gen_instance_health_open = fields.Boolean(
        string="Gen ouvert à la santé sur cette instance", compute="_compute_gen")
    gen_consent = fields.Boolean(
        string="Gen peut lire mon journal d'humeur",
        compute="_compute_gen", inverse="_inverse_gen_consent",
        help="Sans cette permission, Gen ne lit votre journal que dans une conversation "
             "que vous lancez depuis une fiche du journal, et seulement cette fiche. "
             "Vous pouvez la retirer en tout temps.")
    gen_consent_health = fields.Boolean(
        string="Gen peut lire mes autres données Healthy Fox",
        compute="_compute_gen", inverse="_inverse_gen_consent_health",
        help="Médicaments, signes vitaux, analyses, examens, symptômes, consommation, "
             "entraînement, alimentation. Vous pouvez la retirer en tout temps.")
    entry_count = fields.Integer(string="Saisies", compute="_compute_resume")
    average_30 = fields.Float(string="Moyenne sur 30 jours", compute="_compute_resume", digits=(3, 1))
    correlation_html = fields.Html(
        string="Corrélations", compute="_compute_correlation_html", sanitize=False)

    _sql_constraints = [
        ("bf_mood_settings_unique", "unique(create_uid)",
         "Une seule fiche de réglages par personne."),
    ]

    # Vie privée : aucun abonné automatique,
    # aucun suivi qui ne soit une note interne.
    def _message_auto_subscribe_followers(self, updated_values, default_subtype_ids):
        return []

    def _track_subtype(self, init_values):
        return self.env.ref("mail.mt_note")

    def _compute_display_name(self):
        """Nom neutre : il s'affiche dans la liste des activités (le rappel)."""
        for rec in self:
            rec.display_name = _("Journal d'humeur")

    # ------------------------------------------------------------------
    @api.depends("create_uid")
    def _compute_user_id(self):
        for rec in self:
            rec.user_id = rec.create_uid

    def _compute_reminder_tz(self):
        for rec in self:
            rec.reminder_tz = (rec.user_id or self.env.user).tz or "UTC"

    def _compute_gen(self):
        present = "bf.gen.consent" in self.env
        Consent = self.env["bf.gen.consent"] if present else None
        for rec in self:
            personne = rec.user_id or self.env.user
            rec.gen_available = present
            rec.gen_instance_open = present and Consent._gen_instance_open(PORTEE_HUMEUR)
            rec.gen_instance_health_open = present and Consent._gen_instance_open(PORTEE_SANTE)
            rec.gen_consent = present and Consent._gen_has_consent(PORTEE_HUMEUR, personne)
            rec.gen_consent_health = present and Consent._gen_has_consent(PORTEE_SANTE, personne)

    def _bf_poser_consentement(self, portee, valeur):
        if "bf.gen.consent" not in self.env:
            return
        for rec in self:
            if rec.user_id and rec.user_id != self.env.user:
                raise AccessError(_("Seule la personne elle-même donne ou retire cette permission."))
            self.env["bf.gen.consent"]._gen_set_consent(portee, valeur)

    def _inverse_gen_consent(self):
        for rec in self:
            rec._bf_poser_consentement(PORTEE_HUMEUR, rec.gen_consent)

    def _inverse_gen_consent_health(self):
        for rec in self:
            rec._bf_poser_consentement(PORTEE_SANTE, rec.gen_consent_health)

    def _compute_resume(self):
        Entry = self.env["health.mood.entry"]
        depuis = fields.Date.context_today(self) - timedelta(days=29)
        for rec in self:
            uid = (rec.user_id or self.env.user).id
            rec.entry_count = Entry.search_count([("create_uid", "=", uid)])
            recentes = Entry.search([("create_uid", "=", uid), ("date", ">=", depuis)])
            rec.average_30 = (sum(recentes.mapped("score")) / len(recentes)) if recentes else 0.0

    @api.depends("correlations_enabled")
    def _compute_correlation_html(self):
        for rec in self:
            if not rec.correlations_enabled:
                rec.correlation_html = Markup("<p class=\"text-muted\">%s</p>") % _(
                    "Les corrélations sont désactivées. Activées, elles comparent votre humeur à "
                    "votre sommeil, vos médicaments, vos symptômes et vos activités, dans votre "
                    "instance et sur vos seules données. C'est vous qui décidez de les activer.")
                continue
            fin = fields.Date.context_today(rec)
            lignes = rec._bf_correlations(fin - timedelta(days=89), fin)
            rec.correlation_html = self._bf_html_correlations(lignes)

    @api.model
    def _bf_correlations_actives(self):
        """La personne courante a-t-elle activé les corrélations ?"""
        return bool(self.search_count([("create_uid", "=", self.env.uid),
                                       ("correlations_enabled", "=", True)]))

    # ------------------------------------------------------------------
    @api.model
    def _bf_mes_reglages(self):
        """La fiche de la personne courante, créée à sa première visite."""
        mienne = self.search([("create_uid", "=", self.env.uid)], limit=1)
        if not mienne:
            mienne = self.with_context(mail_create_nolog=True, mail_create_nosubscribe=True,
                                       tracking_disable=True).create({})
            self.env["health.mood.activity"]._bf_semer_pour_moi()
        return mienne

    @api.model
    def _bf_action_mes_reglages(self):
        reglages = self._bf_mes_reglages()
        return {
            "type": "ir.actions.act_window",
            "name": _("Mon journal d'humeur"),
            "res_model": self._name,
            "res_id": reglages.id,
            "view_mode": "form",
            "views": [(self.env.ref("bf_health.health_mood_settings_view_form").id, "form")],
            "target": "current",
        }

    def action_open_report_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Rapport pour le médecin"),
            "res_model": "health.mood.report.wizard",
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
        }

    def action_export_csv(self):
        return {"type": "ir.actions.act_url", "url": "/healthy-fox/humeur/export.csv",
                "target": "download"}

    def action_open_phone_page(self):
        return {"type": "ir.actions.act_url", "url": "/healthy-fox/humeur", "target": "new"}

    # ------------------------------------------------------------------
    # Rappel
    # ------------------------------------------------------------------
    @api.model
    def _bf_type_rappel(self):
        return self.env.ref("bf_health.mail_activity_type_mood_reminder", raise_if_not_found=False)

    @api.model
    def _bf_effacer_rappels(self, user, avant=None):
        """Efface les rappels ouverts de ``user`` (tous, ou ceux d'avant la date
        ``avant``). Jamais de rappel « en retard », jamais d'accumulation."""
        type_rappel = self._bf_type_rappel()
        if not type_rappel:
            return 0
        domaine = [("res_model", "=", self._name), ("activity_type_id", "=", type_rappel.id),
                   ("user_id", "=", user.id)]
        if avant:
            domaine.append(("date_deadline", "<", avant))
        rappels = self.env["mail.activity"].sudo().search(domaine)
        n = len(rappels)
        rappels.unlink()
        return n

    @api.model
    def _bf_maintenant_local(self, user, maintenant=None):
        maintenant = maintenant or fields.Datetime.now()
        try:
            fuseau = pytz.timezone(user.tz or "UTC")
        except pytz.UnknownTimeZoneError:
            fuseau = pytz.utc
        return pytz.utc.localize(maintenant).astimezone(fuseau)

    @api.model
    def _cron_mood_reminders(self, maintenant=None):
        """Aux 15 minutes, en superutilisateur : le rappel de chaque personne,
        à SON heure, dans SON fuseau.

        * posé une fois par jour local, si l'heure est passée et que rien n'est
          saisi ce jour-là ;
        * sans courriel : `mail_activity_quick_update` coupe l'avis « vous avez
          une activité », qu'Odoo envoie par courriel à qui le préfère ;
        * sans culpabilité : le rappel de la veille est effacé à chaque passe.
        """
        type_rappel = self._bf_type_rappel()
        if not type_rappel:
            return 0
        Entry = self.env["health.mood.entry"].sudo()
        poses = 0
        for reglages in self.sudo().search([]):
            personne = reglages.user_id
            if not personne or not personne.active:
                continue
            local = self._bf_maintenant_local(personne, maintenant)
            jour = local.date()
            self._bf_effacer_rappels(personne, avant=jour)
            if not reglages.reminder_enabled:
                self._bf_effacer_rappels(personne)
                continue
            if local.hour + local.minute / 60.0 < (reglages.reminder_time or 0.0):
                continue
            if reglages.last_reminder_date == jour:
                continue
            if Entry.search_count([("create_uid", "=", personne.id), ("date", "=", jour)]):
                continue
            if not personne.has_group("bf_health.group_health_user"):
                continue
            # Posé AU NOM de la personne et assigné à elle : ni le
            # compte système, ni un avis par courriel. Texte neutre : il peut
            # s'afficher sur un écran verrouillé ou partir dans une poussée.
            # `sudo()` : voir `au_nom_du_proprietaire` (lancé depuis l'écran).
            a_elle = reglages.with_user(personne).sudo().with_context(
                lang=personne.lang or "fr_CA", mail_activity_quick_update=True,
                mail_create_nosubscribe=True)
            a_elle.activity_schedule(
                "bf_health.mail_activity_type_mood_reminder",
                date_deadline=jour,
                summary=a_elle.env._("Healthy Fox : votre saisie du jour"),
                user_id=personne.id,
            )
            reglages.last_reminder_date = jour
            poses += 1
        return poses

    # ------------------------------------------------------------------
    # Corrélations
    # ------------------------------------------------------------------
    @api.model
    def _bf_jours(self, date_from, date_to):
        """Les séries par jour de la personne courante, lues par l'ORM sous ses
        droits. ``{"humeur": {jour: moyenne}, "activites": {jour: set(ids)},
        "sommeil": {jour: h}, "meds": {jour: (pris, prevus)}, "symptomes": {jour: max}}``."""
        uid = self.env.uid
        entrees = self.env["health.mood.entry"].search(
            [("create_uid", "=", uid), ("date", ">=", date_from), ("date", "<=", date_to)])
        par_jour = {}
        activites = {}
        for e in entrees:
            par_jour.setdefault(e.date, []).append(e.score)
            activites.setdefault(e.date, set()).update(e.activity_ids.ids)
        humeur = {j: sum(v) / len(v) for j, v in par_jour.items()}

        debut = datetime.combine(date_from, time.min)
        fin = datetime.combine(date_to, time.max)
        sommeil = {}
        for v in self.env["health.vital"].search(
                [("create_uid", "=", uid), ("vital_type", "=", "sleep_hours"),
                 ("date", ">=", debut), ("date", "<=", fin)]):
            sommeil[v.date.date()] = sommeil.get(v.date.date(), 0.0) + (v.value or 0.0)

        meds = {}
        for log in self.env["health.medication.log"].search(
                [("create_uid", "=", uid), ("date", ">=", date_from), ("date", "<=", date_to),
                 ("time_slot", "!=", "as_needed")]):
            pris, prevus = meds.get(log.date, (0, 0))
            meds[log.date] = (pris + (1 if log.taken else 0), prevus + 1)

        symptomes = {}
        for s in self.env["health.symptom.log"].search(
                [("create_uid", "=", uid), ("date", ">=", date_from), ("date", "<=", date_to)]):
            symptomes[s.date] = max(symptomes.get(s.date, 0), int(s.severity or 1))
        return {"humeur": humeur, "activites": activites, "sommeil": sommeil,
                "meds": meds, "symptomes": symptomes}

    @staticmethod
    def _bf_pearson(paires):
        n = len(paires)
        if n < 2:
            return None
        mx = sum(x for x, _y in paires) / n
        my = sum(y for _x, y in paires) / n
        sxy = sum((x - mx) * (y - my) for x, y in paires)
        sxx = sum((x - mx) ** 2 for x, _y in paires)
        syy = sum((y - my) ** 2 for _x, y in paires)
        if sxx <= 0 or syy <= 0:
            return None
        return sxy / math.sqrt(sxx * syy)

    def _bf_nombre(self, valeur, chiffres=1):
        return formatLang(self.env, valeur, digits=chiffres)

    def _bf_force(self, r):
        a = abs(r)
        if a < 0.2:
            return _("aucun lien net")
        if a < 0.4:
            return _("un lien faible")
        if a < 0.6:
            return _("un lien modéré")
        return _("un lien fort")

    def _bf_comparer(self, humeur, jours_oui, libelle_oui, libelle_non):
        """Moyenne d'humeur les jours « oui » contre les autres, ou ``None``."""
        oui = [humeur[j] for j in humeur if j in jours_oui]
        non = [humeur[j] for j in humeur if j not in jours_oui]
        if len(oui) < MIN_JOURS_GROUPE or len(non) < MIN_JOURS_GROUPE:
            return None
        m_oui, m_non = sum(oui) / len(oui), sum(non) / len(non)
        texte = _(
            "%(oui)s : humeur moyenne de %(m_oui)s sur 5 (%(n_oui)s jours). "
            "%(non)s : %(m_non)s (%(n_non)s jours).",
            oui=libelle_oui, m_oui=self._bf_nombre(m_oui), n_oui=len(oui),
            non=libelle_non, m_non=self._bf_nombre(m_non), n_non=len(non))
        return {"texte": texte, "ecart": m_oui - m_non, "n_oui": len(oui), "n_non": len(non)}

    @api.model
    def _bf_correlations(self, date_from, date_to):
        """Les corrélations de la personne courante sur la période.

        Une ligne par source : sommeil, médicaments, symptômes, puis une par
        activité qui a assez de jours. Chaque ligne dit son effectif ; sous les
        seuils, elle dit qu'il manque des jours plutôt que d'inventer un lien.
        """
        if not self._bf_correlations_actives():
            # Profilage (Loi 25, art. 8.1) : rien ne se calcule sans l'accord de
            # la personne, pas même pour le rapport.
            return []
        j = self._bf_jours(date_from, date_to)
        humeur = j["humeur"]
        lignes = []
        if len(humeur) < MIN_JOURS_PEARSON:
            lignes.append({"cle": "peu", "titre": _("Pas encore assez de jours"),
                           "texte": _("%(n)s jours saisis sur la période. Les liens se calculent "
                                      "à partir de %(min)s jours.",
                                      n=len(humeur), min=MIN_JOURS_PEARSON),
                           "suffisant": False})
            return lignes

        # Sommeil
        paires = [(j["sommeil"][d], humeur[d]) for d in humeur if d in j["sommeil"]]
        if len(paires) >= MIN_JOURS_PEARSON:
            r = self._bf_pearson(paires)
            textes = []
            if r is not None:
                sens = _("plus de sommeil va avec une meilleure humeur") if r > 0 \
                    else _("plus de sommeil va avec une humeur plus basse")
                textes.append(_("%(force)s (r = %(r)s, %(n)s jours) : %(sens)s.",
                                force=self._bf_force(r).capitalize(), r=self._bf_nombre(r, 2),
                                n=len(paires), sens=sens) if abs(r) >= 0.2 else
                              _("Aucun lien net (r = %(r)s, %(n)s jours).",
                                r=self._bf_nombre(r, 2), n=len(paires)))
            assez = {d for d, h in j["sommeil"].items() if h >= SEUIL_SOMMEIL_H}
            comp = self._bf_comparer({d: humeur[d] for d in humeur if d in j["sommeil"]}, assez,
                                     _("Nuits de 7 h ou plus"), _("Nuits plus courtes"))
            if comp:
                textes.append(comp["texte"])
            lignes.append({"cle": "sommeil", "titre": _("Sommeil"), "texte": " ".join(textes),
                           "r": r, "n": len(paires), "suffisant": True})
        else:
            lignes.append({"cle": "sommeil", "titre": _("Sommeil"), "suffisant": False,
                           "texte": _("%(n)s jours avec sommeil et humeur : il en faut %(min)s.",
                                      n=len(paires), min=MIN_JOURS_PEARSON)})

        # Médicaments
        jours_meds = {d for d in humeur if d in j["meds"]}
        sans_oubli = {d for d in jours_meds if j["meds"][d][0] >= j["meds"][d][1]}
        comp = self._bf_comparer({d: humeur[d] for d in jours_meds}, sans_oubli,
                                 _("Jours sans oubli de médicament"), _("Jours avec un oubli"))
        lignes.append({"cle": "meds", "titre": _("Médicaments"),
                       "texte": comp["texte"] if comp else _(
                           "Il faut au moins %(min)s jours de chaque sorte (sans oubli, avec "
                           "oubli) parmi les jours saisis.", min=MIN_JOURS_GROUPE),
                       "ecart": comp and comp["ecart"], "suffisant": bool(comp)})

        # Symptômes
        jours_sympt = set(j["symptomes"])
        comp = self._bf_comparer(humeur, jours_sympt, _("Jours avec un symptôme"),
                                 _("Jours sans symptôme"))
        textes = [comp["texte"]] if comp else []
        paires = [(j["symptomes"].get(d, 0), humeur[d]) for d in humeur]
        r = self._bf_pearson(paires) if jours_sympt else None
        if r is not None and abs(r) >= 0.2:
            textes.append(_("%(force)s avec la sévérité (r = %(r)s) : plus le symptôme est "
                            "sévère, %(sens)s.", force=self._bf_force(r).capitalize(),
                            r=self._bf_nombre(r, 2),
                            sens=_("plus l'humeur est basse") if r < 0 else _("plus l'humeur est haute")))
        lignes.append({"cle": "symptomes", "titre": _("Symptômes"),
                       "texte": " ".join(textes) or _(
                           "Il faut au moins %(min)s jours avec et %(min)s jours sans symptôme.",
                           min=MIN_JOURS_GROUPE),
                       "r": r, "suffisant": bool(textes)})

        # Activités
        Activity = self.env["health.mood.activity"].with_context(active_test=False)
        ids = set().union(*j["activites"].values()) if j["activites"] else set()
        for act in Activity.browse(sorted(ids)).exists():
            jours = {d for d, s in j["activites"].items() if act.id in s}
            comp = self._bf_comparer(humeur, jours, _("Avec %s", act.name), _("Sans"))
            if comp:
                lignes.append({"cle": "activite", "titre": "%s %s" % (act.icon or "", act.name),
                               "texte": comp["texte"], "ecart": comp["ecart"],
                               "activite_id": act.id, "suffisant": True})
        return lignes

    @api.model
    def _bf_html_correlations(self, lignes):
        morceaux = [Markup('<p class="text-muted small">%s</p>') % _(
            "Sur les 90 derniers jours. Une corrélation n'est pas une cause : "
            "c'est une piste à regarder, seul ou avec un soignant.")]
        for ligne in lignes:
            classe = "" if ligne.get("suffisant") else ' class="text-muted"'
            morceaux.append(Markup("<p%s><strong>%s</strong> · %s</p>") % (
                Markup(classe), ligne["titre"], ligne["texte"]))
        return Markup("").join(morceaux)


class HealthMoodReportWizard(models.TransientModel):
    _name = "health.mood.report.wizard"
    _description = "Rapport d'humeur pour le médecin"
    _gen_scope = PORTEE_SAISIE
    _gen_scope_label = LIBELLE_SAISIE

    date_from = fields.Date(
        string="Du", required=True,
        default=lambda self: fields.Date.context_today(self) - timedelta(days=89))
    date_to = fields.Date(string="Au", required=True, default=fields.Date.context_today)
    # Un défaut, pas un champ calculé : sur un enregistrement neuf, `onchange`
    # rend faux un calcul sans dépendance (mesuré au parcours du banc).
    correlations_enabled = fields.Boolean(
        readonly=True,
        default=lambda self: self.env["health.mood.settings"]._bf_correlations_actives())
    include_correlations = fields.Boolean(
        string="Inclure les corrélations",
        default=lambda self: self.env["health.mood.settings"]._bf_correlations_actives())
    include_notes = fields.Boolean(
        string="Inclure mes notes", default=False,
        help="Vos notes sont privées. Cochez seulement si vous voulez les montrer.")

    @api.constrains("date_from", "date_to")
    def _check_periode(self):
        for rec in self:
            if rec.date_from and rec.date_to and rec.date_from > rec.date_to:
                raise ValidationError(_("La période commence après sa fin."))

    def action_print(self):
        self.ensure_one()
        return self.env.ref("bf_health.action_report_mood_doctor").report_action(self)

    def _bf_donnees_rapport(self):
        """Tout ce que le PDF affiche, lu sous les droits de la personne."""
        self.ensure_one()
        Entry = self.env["health.mood.entry"]
        entrees = Entry.search([("create_uid", "=", self.env.uid),
                                ("date", ">=", self.date_from), ("date", "<=", self.date_to)],
                               order="date asc, id asc")
        par_jour = {}
        for e in entrees:
            par_jour.setdefault(e.date, []).append(e.score)
        moyennes = {d: sum(v) / len(v) for d, v in par_jour.items()}
        libelles = dict(self.env["health.mood.entry"]._fields["level"]._description_selection(self.env))
        repartition = []
        for code, _nom in NIVEAUX:
            n = len(entrees.filtered(lambda e, c=code: e.level == c))
            repartition.append({"code": code, "libelle": libelles.get(code, code),
                                "visage": VISAGES[code], "n": n,
                                "pct": round(100.0 * n / len(entrees)) if entrees else 0})
        semaines = {}
        for d, m in moyennes.items():
            lundi = d - timedelta(days=d.weekday())
            semaines.setdefault(lundi, []).append(m)
        Settings = self.env["health.mood.settings"]
        return {
            "personne": self.env.user.name,
            "du": format_date(self.env, self.date_from),
            "au": format_date(self.env, self.date_to),
            "genere": format_date(self.env, fields.Date.context_today(self)),
            "nb_saisies": len(entrees),
            "nb_jours": len(moyennes),
            "nb_jours_periode": (self.date_to - self.date_from).days + 1,
            "moyenne": Settings._bf_nombre(sum(moyennes.values()) / len(moyennes)) if moyennes else "",
            "repartition": repartition,
            "courbe": self._bf_svg_courbe(moyennes),
            "semaines": [{"du": format_date(self.env, lundi),
                          "moyenne": Settings._bf_nombre(sum(v) / len(v)), "n": len(v)}
                         for lundi, v in sorted(semaines.items())],
            "correlations": Settings._bf_correlations(self.date_from, self.date_to)
            if self.include_correlations else [],
            "notes": [{"date": format_date(self.env, e.date), "libelle": libelles.get(e.level, ""),
                       "note": e.note} for e in entrees if e.note] if self.include_notes else [],
        }

    def _bf_svg_courbe(self, moyennes):
        """La courbe quotidienne en SVG en ligne (wkhtmltopdf le rend). Rien que
        des nombres : aucun texte de la personne n'entre dans le dessin."""
        largeur, hauteur, marge = 680, 150, 24
        jours = (self.date_to - self.date_from).days or 1
        def x(d):
            return marge + (largeur - 2 * marge) * ((d - self.date_from).days / jours)
        def y(v):
            return hauteur - marge - (hauteur - 2 * marge) * ((v - 1) / 4.0)
        lignes = []
        for niveau in range(1, 6):
            lignes.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#d8dee4" stroke-width="1"/>'
                          '<text x="4" y="%.1f" font-size="10" fill="#5b6670">%d</text>'
                          % (marge, y(niveau), largeur - marge, y(niveau), y(niveau) + 3, niveau))
        points = ["%.1f,%.1f" % (x(d), y(v)) for d, v in sorted(moyennes.items())]
        if len(points) > 1:
            lignes.append('<polyline points="%s" fill="none" stroke="#1f7fae" stroke-width="2"/>'
                          % " ".join(points))
        for p in points:
            cx, cy = p.split(",")
            lignes.append('<circle cx="%s" cy="%s" r="2.5" fill="#1f7fae"/>' % (cx, cy))
        return Markup('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                      'viewBox="0 0 %d %d">%s</svg>') % (
            largeur, hauteur, largeur, hauteur, Markup("".join(lignes)))
