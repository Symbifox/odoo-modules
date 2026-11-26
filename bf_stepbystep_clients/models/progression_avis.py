# -*- coding: utf-8 -*-
"""L'avis au responsable du mandat : le client a agi sur sa page.

Trois gestes du client avisent le responsable du projet (`user_id`) : une pièce
déposée, le questionnaire rempli (pont `bf_stepbystep_survey`), et une suite
d'erreurs de code d'accès. Trois canaux, réglés par société dans les
paramètres du module, cumulables : une activité à faire, une notification
Odoo, un courriel.

🔴 Le geste du client n'écrit qu'UNE ligne neuve : l'avis en file
(`bf.progression.notice`). Une tâche planifiée, réveillée sur-le-champ, l'envoie
dans SA transaction. Écrire l'activité, abonner le responsable ou mettre le
courriel en file dans la transaction du client touchait des lignes partagées : un
conflit avec le responsable qui fermait l'activité au même moment faisait
rejouer la requête, et sur le chemin S3 le rejeu ne peut pas refaire un
assemblage déjà consommé — le dépôt échouait.
Une insertion ne se dispute aucune ligne.

🔴 Rien de ce que le client a écrit ne sort de la base. Le courriel, la
notification et l'activité disent QU'IL s'est passé quelque chose sur quel
mandat, et mènent à la fiche ; le nom du fichier, que le client choisit, ne
paraît dans aucun avis (la note d'une activité faite est recopiée au fil, d'où
elle peut partir par courriel aux abonnés). La pièce n'est jamais jointe : les documents de certains mandats ne
circulent pas par courriel. L'adresse IP du visiteur n'est recopiée nulle part :
elle reste sur la trace du dépôt ou du code, qui la gardent déjà.

🔴 Le client n'est jamais abonné ni destinataire. Le dépôt reste une note
CONSIGNÉE (voir `_creer_depot`) ; l'avis s'adresse au seul responsable interne.
"""
import logging

from markupsafe import Markup
from odoo import _, api, fields, models
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY

from .project_project import GROUPE_PUBLICATION

_logger = logging.getLogger(__name__)

TYPE_ACTIVITE = "bf_stepbystep_clients.mail_activity_type_progression_client"
GABARIT_AVIS = "bf_stepbystep_clients.mail_template_progression_notice"
TACHE_AVIS = "bf_stepbystep_clients.ir_cron_progression_notices"
CANAUX = ("activity", "inbox", "email")
ESSAIS_AVIS = 3
# Une suite d'erreurs de code se lit sur une journée : deux erreurs d'il y a trois
# mois et une aujourd'hui ne sont pas « trois à la suite ».
HEURES_DE_LA_SUITE = 24
JOURS_DE_GARDE = {"done": 30, "failed": 90}


class AvisNonEnvoye(Exception):
    """Aucun canal n'est parti : le message nomme chaque canal et son erreur."""


class ResCompany(models.Model):
    _inherit = "res.company"

    progression_notify_activity = fields.Boolean(
        string="Progress page: activity for the mandate manager", default=True)
    progression_notify_inbox = fields.Boolean(
        string="Progress page: Odoo notification", default=False)
    progression_notify_email = fields.Boolean(
        string="Progress page: e-mail to the mandate manager", default=True)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    progression_notify_activity = fields.Boolean(
        related="company_id.progression_notify_activity", readonly=False)
    progression_notify_inbox = fields.Boolean(
        related="company_id.progression_notify_inbox", readonly=False)
    progression_notify_email = fields.Boolean(
        related="company_id.progression_notify_email", readonly=False)


class ProgressionNotice(models.Model):
    """Un avis en file : le client a agi, le responsable n'est pas encore avisé."""

    _name = "bf.progression.notice"
    _description = "Notice of a client action on a progression page"
    _order = "id"
    _rec_name = "event"

    project_id = fields.Many2one(
        "project.project", string="Mandate", required=True, ondelete="cascade",
        index=True, readonly=True)
    company_id = fields.Many2one(related="project_id.company_id", store=True)
    event = fields.Char(string="Event", required=True, readonly=True)
    res_model = fields.Char(string="Record model", readonly=True)
    res_id = fields.Many2oneReference(
        string="Record", model_field="res_model", readonly=True)
    values = fields.Json(string="Values", readonly=True)
    state = fields.Selection(
        [("pending", "Pending"), ("done", "Sent"), ("failed", "Failed")],
        string="Status", default="pending", required=True, index=True, readonly=True)
    attempts = fields.Integer(string="Attempts", readonly=True, default=0)
    channels = fields.Char(string="Sent by (codes)", readonly=True)
    error = fields.Text(string="Last error", readonly=True)
    # Ce que la liste AFFICHE : `event` et `channels` gardent les codes internes.
    event_label = fields.Char(string="Event", compute="_compute_libelles")
    channels_label = fields.Char(string="Sent by", compute="_compute_libelles")

    def _progression_libelles_evenements(self):
        """Le libellé de chaque événement connu. `survey` vient du pont
        questionnaire : son code est un contrat, nommé ici pour que le pont n'ait
        pas à suivre. Un code inconnu s'affiche tel quel."""
        return {
            "deposit": _("Document deposited"),
            "survey": _("Questionnaire completed"),
            "otp_failures": _("Wrong access codes"),
        }

    def _progression_libelles_canaux(self):
        return {
            "activity": _("Activity"),
            "inbox": _("Odoo notification"),
            "email": _("email"),
        }

    @api.depends("event", "channels")
    @api.depends_context("lang")
    def _compute_libelles(self):
        evenements = self._progression_libelles_evenements()
        canaux = self._progression_libelles_canaux()
        for avis in self:
            avis.event_label = evenements.get(avis.event, avis.event)
            libelle = ", ".join(
                canaux.get(c, c) for c in (avis.channels or "").split(",") if c)
            avis.channels_label = libelle[:1].upper() + libelle[1:] if libelle else False

    @api.depends("event_label")
    def _compute_display_name(self):
        for avis in self:
            avis.display_name = avis.event_label or avis.event

    # ------------------------------------------------------------------
    # La tâche planifiée
    # ------------------------------------------------------------------

    @api.model
    def _cron_envoyer(self, limite=100):
        """Envoie les avis en file. Chacun sous son point de sauvegarde : un avis
        qui échoue reste en file (trois essais), les autres partent."""
        # Un avis qui a échoué attend cinq minutes avant l'essai suivant : chaque
        # geste réveille la tâche, et un conflit passager brûlait les trois
        # essais en quelques secondes.
        repos = fields.Datetime.subtract(fields.Datetime.now(), minutes=5)
        avis = self.sudo().search([
            ("state", "=", "pending"),
            "|", ("attempts", "=", 0), ("write_date", "<", repos),
        ], order="id", limit=limite)
        for un in avis:
            try:
                with self.env.cr.savepoint():
                    partis, erreurs = un._envoyer()
                    un.write({"state": "done", "channels": ",".join(partis),
                              "error": "\n".join(erreurs) or False})
            except Exception as erreur:
                _logger.exception(
                    "Portail Progression : avis %s non envoyé (projet %s)",
                    un.id, un.project_id.id)
                essais = un.attempts + 1
                un.write({
                    "attempts": essais,
                    "error": str(erreur)[:2000],
                    "state": "failed" if essais >= ESSAIS_AVIS else "pending",
                })
        self._purger()
        return len(avis)

    def action_retry(self):
        """Remet en file des avis en échec (après avoir réparé la cause).

        🔴 Sans `sudo` : l'écriture vérifie elle-même le droit, réservé aux
        administrateurs. Un gestionnaire de projets LIT les avis ; en `sudo`, il
        pouvait les remettre en file par un appel RPC.
        """
        echecs = self.filtered(lambda a: a.state == "failed")
        echecs.write({"state": "pending", "attempts": 0})
        if echecs:
            self.env.ref(TACHE_AVIS).sudo()._trigger()
        return True

    @api.model
    def _purger(self):
        for etat, jours in JOURS_DE_GARDE.items():
            avant = fields.Datetime.subtract(fields.Datetime.now(), days=jours)
            self.sudo().search(
                [("state", "=", etat), ("write_date", "<", avant)]).unlink()

    def _envoyer(self):
        """Avise le responsable par les canaux de la société. Rend `(canaux
        partis, erreurs)`. Un canal qui lève, ou qui n'a rien produit (un
        responsable sans adresse), n'est pas compté et se dit dans `error` ; il
        n'empêche pas les autres. Si aucun ne part alors qu'il y en avait à
        faire partir, l'avis reste en file."""
        self.ensure_one()
        projet = self.project_id.sudo()
        responsable = projet.user_id
        if not responsable or not responsable.active or responsable.share:
            _logger.info(
                "Portail Progression : geste « %s » sur le projet %s, aucun "
                "responsable interne à aviser", self.event, projet.id)
            return [], [_("No internal manager on the mandate.")]
        # La société du MANDAT, sinon celle du responsable : la même pour les
        # réglages et pour l'expéditeur du courriel.
        societe = projet.company_id or responsable.company_id
        canaux = [c for c in CANAUX if societe["progression_notify_%s" % c]]
        # Notification et courriel cochés ensemble : un usager « par courriel »
        # recevrait deux courriels du même geste.
        if "email" in canaux and responsable.notification_type == "email":
            canaux = [c for c in canaux if c != "inbox"]
        if not canaux:
            return [], []
        langue = responsable.lang or "en_US"
        projet = projet.with_context(lang=langue)
        cible = projet
        if self.res_model and self.res_id and self.res_model in self.env:
            cible = self.env[self.res_model].sudo().with_context(
                lang=langue).browse(self.res_id).exists() or projet
        textes = projet._progression_avis_textes(self.event, dict(self.values or {}))
        if not textes:
            return [], [_("Unknown event: %(event)s", event=self.event)]
        textes["quand"] = fields.Datetime.context_timestamp(
            cible.with_context(tz=responsable.tz or "UTC"), self.create_date)
        partis, erreurs, levees = [], [], []
        for canal in canaux:
            try:
                with self.env.cr.savepoint():
                    produit = getattr(projet, "_progression_aviser_par_%s" % canal)(
                        responsable, societe, cible, textes)
                if produit:
                    partis.append(canal)
                else:
                    erreurs.append(_("%(channel)s: nothing to send.", channel=canal))
            except Exception as erreur:
                _logger.exception(
                    "Portail Progression : avis %s par %s refusé (projet %s)",
                    self.id, canal, projet.id)
                erreurs.append("%s: %s: %s" % (canal, type(erreur).__name__, erreur))
                levees.append(erreur)
        if levees and not partis:
            raise AvisNonEnvoye("\n".join(erreurs))
        return partis, erreurs


class ProjectProject(models.Model):
    _inherit = "project.project"

    # Le compteur des erreurs de code d'accès depuis le dernier code juste. Il
    # vit sur le mandat, et non sur le code : un code n'a que cinq essais, et
    # une suite d'erreurs se lit d'un code à l'autre.
    progression_otp_failed_streak = fields.Integer(
        string="Wrong access codes in a row", readonly=True, copy=False,
        default=0, groups=GROUPE_PUBLICATION)
    progression_otp_failed_at = fields.Datetime(
        string="Last wrong access code", readonly=True, copy=False,
        groups=GROUPE_PUBLICATION)

    # ------------------------------------------------------------------
    # Mettre en file — dans la transaction du client
    # ------------------------------------------------------------------

    def _progression_aviser(self, evenement, cible=None, **valeurs):
        """Met en file l'avis d'un geste du client. N'écrit qu'une ligne neuve.

        `valeurs` doit tenir en JSON (des identifiants, pas des enregistrements).
        Rend l'avis, ou un ensemble vide si la mise en file a échoué : le geste
        du client ne dépend pas de son avis. Seules les erreurs de concurrence
        que `retrying()` rejoue remontent : c'est le rejeu qui tient les plafonds
        du portail. Toute autre erreur est journalisée et avalée.
        """
        self.ensure_one()
        Avis = self.env["bf.progression.notice"].sudo()
        try:
            with self.env.cr.savepoint():
                avis = Avis.create({
                    "project_id": self.id,
                    "event": evenement,
                    "res_model": cible._name if cible else False,
                    "res_id": cible.id if cible else False,
                    "values": valeurs,
                })
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            # Les seules qu'Odoo REJOUE : une IntegrityError, il la convertit en
            # erreur de validation sans rejeu, et le dépôt S3 serait perdu.
            raise
        except Exception:
            _logger.exception(
                "Portail Progression : avis « %s » non mis en file (projet %s)",
                evenement, self.id)
            return Avis.browse()
        self._progression_reveiller_la_file()
        return avis

    def _progression_reveiller_la_file(self):
        """« Maintenant », pas au prochain passage. Une tâche éteinte se dit au
        journal : l'avis reste en file, visible, jusqu'à ce qu'on la rallume."""
        tache = self.env.ref(TACHE_AVIS, raise_if_not_found=False)
        if not tache or not tache.sudo().active:
            _logger.warning(
                "Portail Progression : la tâche planifiée des avis est éteinte ; "
                "l'avis du projet %s attend en file", self.id)
            return
        try:
            with self.env.cr.savepoint():
                tache.sudo()._trigger()
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            raise
        except Exception:
            _logger.warning(
                "Réveil des avis refusé pour le projet %s ; il partira au "
                "prochain passage", self.id)

    # ------------------------------------------------------------------
    # Ce que dit l'avis — dans la langue du RESPONSABLE
    # ------------------------------------------------------------------

    def _progression_avis_textes(self, evenement, valeurs):
        """Rend un dict `sujet`, `ligne` (l'activité), `lignes` (courriel et
        notification), ou None. Un module pont ajoute ses événements en
        surchargeant cette méthode.

        🔴 Ni la `ligne` (l'activité) ni les `lignes` (courriel, notification) ne
        citent ce que le client a écrit : la note d'une activité faite est
        recopiée au fil, et peut partir par courriel aux abonnés.
        """
        self.ensure_one()
        projet = self.sudo()
        if evenement == "deposit":
            depot = self.env["bf.progression.deposit"].sudo().with_context(
                lang=self.env.context.get("lang")).browse(
                valeurs.get("depot_id")).exists()
            # Relue dans la langue du responsable : la copie `document_name` est
            # dans celle du client qui a déposé.
            piece = (depot.document_id.name or depot.document_name) if depot else _("a document")
            taille = max(1, (depot.file_size or 0) // 1024) if depot else 0
            if not depot:
                ou = _("The deposit is no longer in Odoo.")
            elif depot.attachment_id:
                ou = _("The document is in Odoo. It is not attached to this e-mail.")
            else:
                ou = _("The document is in the secure storage, reachable from Odoo. "
                       "It is not attached to this e-mail.")
            return {
                "sujet": _("Document deposited: %(doc)s", doc=piece),
                # 🔴 Pas le nom du fichier, même ici : quand l'activité est marquée
                # faite, Odoo recopie sa note au fil de la tâche, et un abonné qui
                # suit les activités la reçoit par courriel. Le fichier porte son
                # nom sur la tâche, en pièce jointe.
                "ligne": _("The client deposited %(doc)s on the progress page "
                           "(deposit no. %(ref)s, %(size)s KB).",
                           doc=piece, ref=depot.id or "?", size=taille),
                "lignes": [
                    _("A document was deposited on the progress page of the "
                      "mandate %(project)s: %(doc)s (%(size)s KB).",
                      project=projet.display_name, doc=piece, size=taille),
                    ou,
                ],
            }
        if evenement == "otp_failures":
            echecs = valeurs.get("echecs") or 0
            return {
                "sujet": _("Access code: wrong attempts in a row"),
                "ligne": _("%(count)s wrong access codes in a row were entered on "
                           "the progress page.", count=echecs),
                "lignes": [
                    _("%(count)s wrong access codes in a row were entered on the "
                      "progress page of the mandate %(project)s.",
                      count=echecs, project=projet.display_name),
                    _("If the client did not report any trouble, someone else may "
                      "hold the link. On the Client portal tab of the project, "
                      "\"Forget the devices and codes\" or \"New address\" closes "
                      "the page to them."),
                ],
            }
        return None

    # ------------------------------------------------------------------
    # Les trois canaux — dans la transaction de la tâche planifiée
    # ------------------------------------------------------------------

    def _progression_aviser_par_activity(self, responsable, societe, cible, textes):
        """Une activité à faire aujourd'hui, au responsable, sur la fiche.

        Une activité ouverte du même sujet reçoit la ligne au lieu d'en faire
        naître une autre : dix fichiers d'une même pièce font une activité.
        `mail_activity_quick_update` : le canal « courriel » est à part, et
        l'avis d'assignation d'Odoo en aurait fait un second, en anglais.
        """
        type_activite = self.env.ref(TYPE_ACTIVITE)
        local = cible.with_context(tz=responsable.tz or "UTC")
        aujourdhui = fields.Date.context_today(local)
        # L'heure du GESTE, pas de l'envoi : un avis resté en file reste juste.
        quand = textes.get("quand") or fields.Datetime.context_timestamp(
            local, fields.Datetime.now())
        note = Markup("<p>%s — %s</p>") % (quand.strftime("%Y-%m-%d %H:%M"), textes["ligne"])
        ouverte = self.env["mail.activity"].sudo().search([
            ("res_model", "=", cible._name), ("res_id", "=", cible.id),
            ("activity_type_id", "=", type_activite.id),
            ("user_id", "=", responsable.id), ("summary", "=", textes["sujet"]),
        ], limit=1)
        if ouverte:
            ouverte.with_context(mail_activity_quick_update=True).write({
                "note": (ouverte.note or Markup()) + note,
                "date_deadline": aujourdhui,
            })
            return ouverte
        return cible.with_context(mail_activity_quick_update=True).activity_schedule(
            TYPE_ACTIVITE, date_deadline=aujourdhui, summary=textes["sujet"],
            note=note, user_id=responsable.id)

    def _progression_aviser_par_inbox(self, responsable, societe, cible, textes):
        """Une notification Odoo au seul responsable, sans abonner personne.

        🔴 Les `lignes`, pas la `ligne` de l'activité : un usager réglé « par
        courriel » reçoit cette notification PAR COURRIEL, et le nom du fichier
        du client serait sorti avec elle.
        """
        corps = Markup("").join(Markup("<p>%s</p>") % ligne for ligne in textes["lignes"])
        return cible.message_notify(
            partner_ids=responsable.partner_id.ids,
            author_id=societe.partner_id.id,
            subject=textes["sujet"],
            body=corps,
            email_layout_xmlid="mail.mail_notification_light",
        )

    def _progression_aviser_par_email(self, responsable, societe, cible, textes):
        """Un courriel à l'adresse du responsable, quelle que soit sa préférence.

        ⚠️ Pas `message_notify` : un usager réglé « dans Odoo » ne recevrait
        jamais un avis par notification sous forme de courriel.
        """
        adresse = responsable.partner_id.email_formatted or responsable.email
        if not adresse:
            _logger.info(
                "Portail Progression : le responsable %s du projet %s n'a pas "
                "d'adresse, pas de courriel", responsable.id, self.id)
            return self.env["mail.mail"]
        gabarit = self.env.ref(GABARIT_AVIS)
        adresse_fiche = "%s/odoo/%s/%s" % (
            cible.get_base_url().rstrip("/"), cible._name, cible.id)
        valeurs = {"email_to": adresse, "recipient_ids": [], "auto_delete": False}
        expediteur = societe.partner_id.email_formatted or societe.email
        if expediteur:
            valeurs["email_from"] = expediteur
        courriel_id = gabarit.sudo().with_context(
            bf_avis_lang=responsable.lang or "en_US",
            bf_avis_sujet=textes["sujet"],
            bf_avis_lignes=textes["lignes"],
            bf_avis_url=adresse_fiche,
            bf_avis_bouton=_("Open in Odoo"),
        ).send_mail(self.id, force_send=False, email_values=valeurs)
        courriel = self.env["mail.mail"].sudo().browse(courriel_id)
        # Détaché du dossier : l'avis est pour le responsable, pas pour le fil du
        # projet que d'autres suivent.
        courriel.mail_message_id.sudo().write({"model": False, "res_id": False})
        try:
            with self.env.cr.savepoint():
                self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()
        except Exception:
            _logger.warning(
                "Réveil de la file refusé pour l'avis du projet %s ; il partira "
                "au prochain passage", self.id)
        return courriel

    # ------------------------------------------------------------------
    # La suite d'erreurs de code
    # ------------------------------------------------------------------

    def _progression_otp_noter_un_echec(self):
        """Compte une erreur de code ; met un avis en file à chaque multiple du
        seuil. Rend la longueur de la suite.

        Par SQL, en une écriture : deux erreurs simultanées ne lisent pas la
        même valeur. Une suite dont la dernière erreur date de plus d'une
        journée repart à un.
        """
        self.ensure_one()
        self.env.cr.execute(
            "UPDATE project_project SET "
            " progression_otp_failed_streak = CASE"
            "   WHEN progression_otp_failed_at IS NULL"
            "     OR progression_otp_failed_at < (now() at time zone 'UTC') - %s * interval '1 hour'"
            "   THEN 1 ELSE COALESCE(progression_otp_failed_streak, 0) + 1 END,"
            " progression_otp_failed_at = (now() at time zone 'UTC')"
            " WHERE id = %s RETURNING progression_otp_failed_streak",
            (HEURES_DE_LA_SUITE, self.id))
        serie = self.env.cr.fetchone()[0]
        self.invalidate_recordset(
            ["progression_otp_failed_streak", "progression_otp_failed_at"])
        seuil = self.env["bf.progression.otp"]._parametre("portal_otp_alert_after", 3)
        if serie % seuil == 0:
            self._progression_aviser("otp_failures", echecs=serie)
        return serie

    def _progression_otp_remettre_la_suite(self):
        if not self:
            return
        self.env.cr.execute(
            "UPDATE project_project SET progression_otp_failed_streak = 0, "
            "progression_otp_failed_at = NULL "
            "WHERE id IN %s AND (progression_otp_failed_streak <> 0 "
            "OR progression_otp_failed_at IS NOT NULL)",
            (tuple(self.ids),))
        self.invalidate_recordset(
            ["progression_otp_failed_streak", "progression_otp_failed_at"])
