"""Ce que les écritures du téléphone envoient aux abonnés d'un dossier (S-M6).

Audit du 2026-09-08 : ``/route``, ``/reply`` et ``/compose`` postaient en
« Discussion ». Les abonnés du dossier visé, portail compris, recevaient le
courriel sans que le téléphone le montre ; et comme ``mail_composer_cc_bcc``
regroupe tous les destinataires en un seul envoi, le fournisseur à qui l'on
répondait voyait en plus l'adresse du client qui suit la tâche.

La règle posée :

- **classer** (``/route``) : une note interne, personne n'est notifié ;
- **répondre, composer** : le courriel part aux destinataires que la personne
  a SAISIS (À et Cc), et à personne d'autre ;
- **envoyer un brouillon** (``/draft/send``) : pareil, aux destinataires que
  le brouillon porte (18.0.11.36.3 : il notifiait encore les abonnés).

⚠️ Le poste a rejoint le téléphone sur le CLASSEMENT (, arbitrage du
2026-09-18) : « Lier à un dossier » pose lui aussi une note interne. Son
composeur, lui, continue de notifier les abonnés, ils sont désormais montrés
en pastilles retirables, et seul ce qui est retiré ne part pas. Les essais
« du poste » ci-dessous mesurent ce qui reste, et c'est ce qui prouve que le
banc voit bien une notification quand il y en a une : sans eux, un zéro
pourrait venir d'un abonné mal posé plutôt que de la garde.

On compte des ``mail.notification`` et des ``mail.mail`` (envois gardés en
file par ``mail_notify_force_send=False`` : un envoi immédiat, au banc, se
supprime aussitôt « parti » et ne se compterait plus).

⚠️ ``mail_defer_seconds=0`` partout : là où ``mail_post_defer`` est installé,
une notification sans envoi forcé attend trente secondes et n'existe pas encore
au moment de compter. Un zéro y passerait pour la garde.
"""
from odoo.tests import new_test_user, tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestMobileNotifications(MobileApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner.write({"groups_id": [
            (4, cls.env.ref("project.group_project_manager").id)]})
        cls.portal = new_test_user(
            cls.env, login="mobile.portail@test.invalid", name="Client Portail",
            email="client.portail@acme.test", groups="base.group_portal")
        cls.suiveur = cls.env["res.partner"].create({
            "name": "Suiveur Externe", "email": "suiveur@externe.test"})
        cls.projet = cls.env["project.project"].create({
            "name": "Dossier suivi", "privacy_visibility": "portal"})
        cls.tache = cls.env["project.task"].create({
            "name": "Tâche suivie par le client", "project_id": cls.projet.id})
        cls.tache.message_subscribe(
            partner_ids=(cls.portal.partner_id | cls.suiveur).ids)

    def setUp(self):
        super().setUp()
        self.assertIn(self.portal.partner_id, self.tache.message_partner_ids,
                      "le client portail doit suivre la tâche, sinon l'essai "
                      "ne prouve rien")

    # ------------------------------------------------------------ outils
    def _abonnes(self):
        return self.portal.partner_id | self.suiveur

    def _nouveaux_messages(self, avant):
        return self.env["mail.message"].sudo().search([
            ("model", "=", "project.task"), ("res_id", "=", self.tache.id),
            ("id", ">", avant),
        ])

    def _dernier_id(self):
        self.env.flush_all()
        self.env.cr.execute("SELECT COALESCE(MAX(id), 0) FROM mail_message")
        return self.env.cr.fetchone()[0]

    def _notifies(self, messages, partenaires):
        """(notifications, courriels en file) visant ``partenaires``."""
        self.env.flush_all()
        notifications = self.env["mail.notification"].sudo().search_count([
            ("mail_message_id", "in", messages.ids),
            ("res_partner_id", "in", partenaires.ids),
        ])
        courriels = self.env["mail.mail"].sudo().search_count([
            ("mail_message_id", "in", messages.ids),
            "|", ("recipient_ids", "in", partenaires.ids),
            "|", ("email_to", "ilike", partenaires[:1].email),
            ("email_cc", "ilike", partenaires[:1].email),
        ])
        return notifications, courriels

    def _courriel_classe_sur_la_tache(self, uid):
        return self.as_owner().create(dict(self._vals(
            subject="Soumission du fournisseur", sender="fournisseur@acme.test",
            direction="in", status="new", root=False,
            body="Voici notre prix.", uid=uid,
        ), res_model="project.task", res_id=self.tache.id))

    def _orphelin(self, uid):
        return self.as_owner().create(self._vals(
            subject="Courriel à classer", sender="fournisseur@acme.test",
            direction="in", status="new", root=False,
            body="À ranger dans la tâche.", uid=uid,
        ))

    # ------------------------------------------------------------ classer
    def test_classer_depuis_le_telephone_ne_notifie_personne(self):
        courriel = self._orphelin("301")
        avant = self._dernier_id()
        courriel.with_user(self.owner).with_context(
            mail_notify_force_send=False).mobile_route("project.task", self.tache.id)
        poste = self._nouveaux_messages(avant).filtered(
            lambda m: m.message_type == "email")
        self.assertEqual(len(poste), 1, "le courriel devait être importé")
        self.assertEqual(poste.subtype_id, self.env.ref("mail.mt_note"))
        for abonne in self._abonnes():
            with self.subTest(abonne=abonne.name):
                self.assertEqual(self._notifies(poste, abonne), (0, 0))

    def _classer_au_poste(self, sentinelle, **kwargs):
        """« Lier à un dossier », exactement comme l'assistant l'appelle.

        ⚠️ Le contexte va sur la FICHE VISÉE autant que sur le courriel : c'est
        la fiche qui poste, et son environnement vient de ``setUpClass``. Posé
        sur le seul courriel, le report s'appliquait et le témoin lisait zéro —
        ce qui se lisait comme une absence de notification.
        """
        courriel = self._orphelin(sentinelle)
        avant = self._dernier_id()
        cible = self.tache.with_context(
            mail_notify_force_send=False, mail_defer_seconds=0)
        courriel.with_user(self.owner).with_context(
            mail_notify_force_send=False, mail_defer_seconds=0)._import_into_chatter(
                cible, force_file=True, **kwargs)
        return self._nouveaux_messages(avant).filtered(
            lambda m: m.message_type == "email")

    def test_classer_depuis_le_poste_ne_notifie_personne(self):
        """ : classer au poste pose une note interne, comme au téléphone.

        Le geste range une trace, il n'écrit à personne. Avant l'arbitrage du
        2026-09-18, l'assistant importait en « Discussion » et les abonnés, 
        portail compris, recevaient le courriel entrant au complet.
        """
        poste = self._classer_au_poste("302")
        self.assertEqual(len(poste), 1, "le courriel devait être importé")
        self.assertEqual(poste.subtype_id, self.env.ref("mail.mt_note"))
        for abonne in (self.portal.partner_id, self.suiveur):
            with self.subTest(abonne=abonne.name):
                self.assertEqual(self._notifies(poste, abonne), (0, 0))

    def test_classer_en_discussion_notifie_encore(self):
        """Le contrôle qui rend le zéro ci-dessus significatif : le paramètre
        reste, et un appelant qui demande « Discussion » notifie bel et bien.
        Sans lui, un zéro pourrait venir d'un abonné mal posé."""
        poste = self._classer_au_poste("304", subtype_xmlid="mail.mt_comment")
        self.assertEqual(poste.subtype_id, self.env.ref("mail.mt_comment"))
        notifications, _courriels = self._notifies(poste, self.portal.partner_id)
        self.assertGreaterEqual(notifications, 1)

    # --------------------------------------------------- répondre, composer
    def test_repondre_depuis_le_telephone_va_au_destinataire_saisi_seulement(self):
        courriel = self._courriel_classe_sur_la_tache("303")
        self.assertEqual(courriel._composer_target(), ("project.task", self.tache.id))
        avant = self._dernier_id()
        courriel.with_user(self.owner).with_context(
            mail_notify_force_send=False).mobile_reply(
                mode="reply", body="Merci, c'est noté.",
                to=["fournisseur@acme.test"], cc=["collegue.cc@acme.test"])
        envoi = self._nouveaux_messages(avant).filtered(
            lambda m: m.message_type == "comment")
        self.assertEqual(len(envoi), 1)
        fournisseur = self.env["res.partner"].search(
            [("email", "=", "fournisseur@acme.test")], limit=1)
        collegue = self.env["res.partner"].search(
            [("email", "=", "collegue.cc@acme.test")], limit=1)
        self.assertEqual(self._notifies(envoi, fournisseur), (1, 1))
        self.assertEqual(self._notifies(envoi, collegue)[0], 1,
                         "le Cc saisi doit rester destinataire")
        for abonne in self._abonnes():
            with self.subTest(abonne=abonne.name):
                self.assertEqual(self._notifies(envoi, abonne), (0, 0))

    def test_composer_depuis_le_telephone_va_au_destinataire_saisi_seulement(self):
        avant = self._dernier_id()
        self.as_owner().with_context(mail_notify_force_send=False).mobile_compose(
            to=["fournisseur@acme.test"], subject="Question sur la tâche",
            body="Pouvez-vous confirmer ?", res_model="project.task",
            res_id=self.tache.id)
        envoi = self._nouveaux_messages(avant).filtered(
            lambda m: m.message_type == "comment")
        self.assertEqual(len(envoi), 1)
        fournisseur = self.env["res.partner"].search(
            [("email", "=", "fournisseur@acme.test")], limit=1)
        self.assertEqual(self._notifies(envoi, fournisseur), (1, 1))
        for abonne in self._abonnes():
            with self.subTest(abonne=abonne.name):
                self.assertEqual(self._notifies(envoi, abonne), (0, 0))

    def test_repondre_depuis_le_poste_notifie_les_abonnes(self):
        """Le composeur du poste, sans rien retirer : les abonnés reçoivent,
        comme avant, c'est l'arbitrage (« cochés, retirables »), et
        c'est le contrôle qui rend significatifs les zéros des essais de
        retrait dans ``test_poste_abonnes``."""
        fournisseur = self.env["res.partner"].create({
            "name": "Fournisseur", "email": "fournisseur.poste@acme.test"})
        avant = self._dernier_id()
        self.env["mail.compose.message"].with_user(self.owner).with_context(
            mail_notify_force_send=False, mail_defer_seconds=0).create({
                "model": "project.task",
                "res_ids": repr([self.tache.id]),
                "composition_mode": "comment",
                "subject": "Re: Soumission",
                "body": "<p>Merci.</p>",
                "partner_ids": [(6, 0, fournisseur.ids)],
            })._action_send_mail()
        envoi = self._nouveaux_messages(avant).filtered(
            lambda m: m.message_type == "comment")
        notifications, _courriels = self._notifies(envoi, self.portal.partner_id)
        self.assertGreaterEqual(notifications, 1)

    def test_la_garde_ne_deborde_pas_sur_un_autre_dossier(self):
        """Le contexte nomme la fiche visée : un message posté ailleurs dans le
        même contexte garde ses abonnés."""
        autre = self.env["project.task"].create({
            "name": "Autre tâche", "project_id": self.projet.id})
        autre.message_subscribe(partner_ids=self.suiveur.ids)
        avant = self._dernier_id()
        autre.with_context(
            bf_notify_explicit_only=("project.task", self.tache.id),
            mail_notify_force_send=False, mail_defer_seconds=0,
        ).message_post(body="Note de discussion", message_type="comment",
                       subtype_xmlid="mail.mt_comment")
        message = self.env["mail.message"].sudo().search([
            ("model", "=", "project.task"), ("res_id", "=", autre.id),
            ("id", ">", avant)])
        self.assertGreaterEqual(self._notifies(message, self.suiveur)[0], 1)

    # ------------------------------------------------- envoyer un brouillon
    def _brouillon_sur_la_tache(self, destinataire, sujet):
        """Un brouillon posé comme le poste le pose : sentinelle + drapeau."""
        return self.env["mail.scheduled.message"].with_user(self.owner).create({
            "model": "project.task",
            "res_id": self.tache.id,
            "subject": sujet,
            "body": "<p>Voici la version révisée.</p>",
            "author_id": self.owner.partner_id.id,
            "partner_ids": [(6, 0, destinataire.ids)],
            "scheduled_date": "2031-01-01 12:00:00",
            "bf_is_draft": True,
        })

    def _fournisseur(self, courriel):
        return self.env["res.partner"].create(
            {"name": "Fournisseur", "email": courriel})

    def test_envoyer_un_brouillon_depuis_le_telephone_va_au_destinataire_seulement(self):
        fournisseur = self._fournisseur("fournisseur.brouillon@acme.test")
        brouillon = self._brouillon_sur_la_tache(fournisseur, "Soumission révisée")
        avant = self._dernier_id()
        self.as_owner().with_context(
            mail_notify_force_send=False, mail_defer_seconds=0,
        ).mobile_draft_send(brouillon.id)
        envoi = self._nouveaux_messages(avant).filtered(
            lambda m: m.subject == "Soumission révisée")
        self.assertEqual(len(envoi), 1)
        self.assertEqual(self._notifies(envoi, fournisseur)[0], 1)
        for abonne in self._abonnes():
            with self.subTest(abonne=abonne.name):
                self.assertEqual(self._notifies(envoi, abonne), (0, 0))

    def test_envoyer_un_brouillon_depuis_le_poste_notifie_les_abonnes(self):
        """Contrôle : le même brouillon, envoyé par le poste (« Envoyer
        maintenant »), notifie le client portail qui suit la tâche. C'est ce
        qui rend significatif le zéro de l'essai précédent."""
        fournisseur = self._fournisseur("fournisseur.brouillon.poste@acme.test")
        brouillon = self._brouillon_sur_la_tache(fournisseur, "Soumission du poste")
        avant = self._dernier_id()
        brouillon.with_user(self.owner).with_context(
            mail_notify_force_send=False, mail_defer_seconds=0).post_message()
        envoi = self._nouveaux_messages(avant).filtered(
            lambda m: m.subject == "Soumission du poste")
        self.assertEqual(len(envoi), 1)
        self.assertGreaterEqual(
            self._notifies(envoi, self.portal.partner_id)[0], 1)

    def test_la_garde_survit_au_report_des_notifications(self):
        """🔴 Un envoi différé recalcule ses destinataires sans le contexte.

        Sans la portée passée en ``kwargs``, le cron notifie les abonnés trente
        secondes après un envoi qui les avait exclus.
        """
        fournisseur = self._fournisseur("fournisseur.report@acme.test")
        avant = self._dernier_id()
        self.tache.with_user(self.owner).with_context(
            bf_notify_explicit_only=("project.task", self.tache.id),
            mail_notify_force_send=False,
        ).message_post(body="Envoi différé", message_type="comment",
                       subtype_xmlid="mail.mt_comment",
                       partner_ids=fournisseur.ids)
        envoi = self._nouveaux_messages(avant)
        self.env.flush_all()
        programme = self.env["mail.message.schedule"].sudo().search(
            [("mail_message_id", "in", envoi.ids)])
        self.assertTrue(programme,
                        "rien n'est différé : l'essai ne prouverait rien")
        programme._send_notifications()
        self.env.flush_all()
        self.assertGreaterEqual(self._notifies(envoi, fournisseur)[0], 1)
        for abonne in self._abonnes():
            with self.subTest(abonne=abonne.name):
                self.assertEqual(self._notifies(envoi, abonne), (0, 0))
