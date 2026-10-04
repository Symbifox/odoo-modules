"""Un Message-ID forgé ne rattache plus une ligne au message d'autrui.

L'ingestion IMAP (le cron, sans requête HTTP) liait la ligne au ``mail.message``
qui portait le même Message-ID, trouvé en superutilisateur, quels que soient
les droits du propriétaire de la boîte sur ce message. Le
rattachement exige maintenant que le PROPRIÉTAIRE de la boîte puisse lire le
message ; sinon la ligne naît comme un courriel neuf, sans erreur.

La passerelle ne classe plus une réponse dans la fiche d'autrui.
Le dossier d'une rangée se déduit des Message-ID qu'elle cite ; la fiche
trouvée doit être accessible en écriture au propriétaire de la rangée. Le
contrôle tournait sous sudo (la passerelle lit la rangée en sudo) et passait
toujours.
Données inventées ; aucune connexion IMAP (octets passés à `_ingest_rfc822`).
"""
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

CORPS_PRIVE = "CORPS_PRIVE-MID-essai"


def brut(message_id, a="b.mid@banc.invalid", sujet="leurre", corps="leurre"):
    return ("From: quelqu.un@exterieur.invalid\r\nTo: %s\r\nSubject: %s\r\n"
            "Message-ID: %s\r\nDate: Tue, 22 Sep 2026 22:00:00 +0000\r\n\r\n%s\r\n"
            % (a, sujet, message_id, corps)).encode()


@tagged("post_install", "-at_install", "isolation_menage")
class TestIsolationMessageId(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        g = "base.group_user,project.group_project_user"
        cls.a = new_test_user(cls.env, login="mid_iso_a", groups=g, email="a.mid@banc.invalid")
        cls.b = new_test_user(cls.env, login="mid_iso_b", groups=g, email="b.mid@banc.invalid")
        cls.projet_a = cls.env["project.project"].create({
            "name": "Projet privé A (mid)", "privacy_visibility": "followers",
            "message_partner_ids": [(6, 0, cls.a.partner_id.ids)]})
        cls.tache_a = cls.env["project.task"].create({"name": "Tâche A (mid)",
                                                      "project_id": cls.projet_a.id})
        cls.projet_b = cls.env["project.project"].create({
            "name": "Projet de B (mid)", "privacy_visibility": "followers",
            "message_partner_ids": [(6, 0, cls.b.partner_id.ids)]})
        cls.tache_b = cls.env["project.task"].create({"name": "Tâche B (mid)",
                                                      "project_id": cls.projet_b.id})
        cls.compte_b = cls.env["bf.email.account"].sudo().create({
            "name": "B", "user_id": cls.b.id, "host": "imap.banc.invalid", "port": 993,
            "login": "b.mid@banc.invalid", "password": "x"})

    def _ligne_b(self, message_id):
        self.env.invalidate_all()
        return self.env["bf.email"].sudo().with_context(active_test=False).search([
            ("user_id", "=", self.b.id), ("message_id_header", "=", message_id)], limit=1)

    def test_message_id_forge_ne_rattache_pas_la_note_privee_de_a(self):
        msg = self.tache_a.with_user(self.a).message_post(
            body=CORPS_PRIVE, message_type="comment", subtype_xmlid="mail.mt_note")
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self.env["mail.message"].with_user(self.b).browse(msg.id).read(["body"])
        # Le chemin du cron : environnement du propriétaire, sans requête HTTP.
        self.env["bf.email"].with_user(self.b)._ingest_rfc822(
            brut(msg.message_id), 4242, "INBOX", self.compte_b)
        ligne = self._ligne_b(msg.message_id)
        self.assertTrue(ligne, "le courriel doit être rangé quand même (pas d'erreur, pas de perte)")
        self.assertFalse(ligne.mail_message_id, "ligne de B rattachée à la note privée de A")
        self.env.invalidate_all()
        corps = self.env["bf.email"].with_user(self.b).browse(ligne.id).read(["body_html"])[0]["body_html"]
        self.assertNotIn(CORPS_PRIVE, str(corps or ""))

    def test_rattachement_legitime_a_un_message_que_b_lit(self):
        msg = self.tache_b.with_user(self.b).message_post(
            body="Message que B voit", message_type="comment", subtype_xmlid="mail.mt_comment")
        self.env["bf.email"].with_user(self.b)._ingest_rfc822(
            brut(msg.message_id), 4243, "INBOX", self.compte_b)
        ligne = self._ligne_b(msg.message_id)
        self.assertEqual(ligne.mail_message_id, msg, "le rattachement légitime doit rester")

    def test_passerelle_odoo_route_toujours_une_reponse(self):
        """La passerelle standard (mail.thread.message_route) n'est pas touchée :
        un courriel neuf crée sa fiche, la réponse retombe sur la même fiche."""
        Thread = self.env["mail.thread"]
        rid = Thread.message_process("project.task", brut(
            "<neuf@exterieur.invalid>", a="projet@banc.invalid", sujet="Neuve (passerelle)",
            corps="Bonjour"))
        tache = self.env["project.task"].browse(rid)
        self.assertEqual(tache.name, "Neuve (passerelle)")
        reponse = ("From: quelqu.un@exterieur.invalid\r\nTo: projet@banc.invalid\r\n"
                   "Subject: Re: Neuve (passerelle)\r\nMessage-ID: <rep@exterieur.invalid>\r\n"
                   "In-Reply-To: <neuf@exterieur.invalid>\r\n"
                   "References: <neuf@exterieur.invalid>\r\n"
                   "Date: Tue, 22 Sep 2026 22:05:00 +0000\r\n\r\nSuite\r\n").encode()
        rid2 = Thread.message_process("project.task", reponse)
        self.assertEqual(rid2, tache.id)
        self.assertTrue(self.env["mail.message"].search([
            ("message_id", "=", "<rep@exterieur.invalid>"),
            ("model", "=", "project.task"), ("res_id", "=", tache.id)]))

    def _rangee_b(self, ancetre):
        """Une réponse arrivée dans la boîte de B, qui cite ``ancetre``."""
        return self.env["bf.email"].sudo().create({
            "subject": "Re: leurre", "email_from": "quelqu.un@exterieur.invalid",
            "email_to": "b.mid@banc.invalid", "direction": "in", "status": "new",
            "source": "imap", "account_id": self.compte_b.id, "user_id": self.b.id,
            "imap_in_inbox": True, "imap_folder": "INBOX",
            "date": "2026-10-03 07:00:00",
            "message_id_header": "<rangee-b-%s@exterieur.invalid>" % ancetre.id,
            "in_reply_to": ancetre.message_id,
        })

    def _route(self, rangee):
        # La passerelle tourne en superutilisateur : c'est là que le contrôle
        # d'accès ne voyait rien.
        return self.env["mail.thread"].sudo()._bf_email_redirect_route(
            ("bf.email", rangee.id, None, self.b.id, False))

    def test_passerelle_ne_classe_pas_dans_la_fiche_privee_d_autrui(self):
        msg = self.tache_a.with_user(self.a).message_post(
            body=CORPS_PRIVE, message_type="comment", subtype_xmlid="mail.mt_note")
        rangee = self._rangee_b(msg)
        self.assertFalse(rangee._filing_target(), "dossier déduit = fiche privée de A")
        self.assertEqual(self._route(rangee), ("bf.email", rangee.id, None, self.b.id, False),
                         "la réponse arrivée chez B doit rester dans la boîte de B")

    def test_passerelle_classe_toujours_dans_le_dossier_de_b(self):
        msg = self.tache_b.with_user(self.b).message_post(
            body="Envoi de B", message_type="comment", subtype_xmlid="mail.mt_comment")
        rangee = self._rangee_b(msg)
        self.assertEqual(self._route(rangee),
                         ("project.task", self.tache_b.id, None, self.b.id, False))

    # ── Relecture adverse : le lien explicite et le propriétaire ──

    def _par_rpc(self):
        """Les gardes ne jouent que pour un appel porté par une requête."""
        return patch("odoo.addons.bf_email_management.models.bf_email.request", new=object())

    def test_b_ne_lie_pas_sa_ligne_a_la_tache_privee_de_a(self):
        msg = self.tache_b.with_user(self.b).message_post(
            body="Envoi de B", message_type="comment", subtype_xmlid="mail.mt_comment")
        rangee = self._rangee_b(msg)
        with self._par_rpc(), self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                rangee.with_user(self.b).write(
                    {"res_model": "project.task", "res_id": self.tache_a.id})
        with self._par_rpc(), self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                self.env["bf.email"].with_user(self.b).create({
                    "subject": "forgée", "direction": "in", "user_id": self.b.id,
                    "res_model": "project.task", "res_id": self.tache_a.id})
        self.assertFalse(rangee.res_model)

    def test_b_lie_sa_ligne_a_sa_propre_tache(self):
        msg = self.tache_b.with_user(self.b).message_post(
            body="Envoi de B", message_type="comment", subtype_xmlid="mail.mt_comment")
        rangee = self._rangee_b(msg)
        with self._par_rpc():
            rangee.with_user(self.b).write(
                {"res_model": "project.task", "res_id": self.tache_b.id})
        self.assertEqual((rangee.res_model, rangee.res_id), ("project.task", self.tache_b.id))

    def test_b_ne_donne_pas_sa_ligne_a_a(self):
        msg = self.tache_b.with_user(self.b).message_post(
            body="Envoi de B", message_type="comment", subtype_xmlid="mail.mt_comment")
        rangee = self._rangee_b(msg)
        with self._par_rpc(), self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                rangee.with_user(self.b).write({"user_id": self.a.id})
        self.assertEqual(rangee.user_id, self.b)

    def test_lien_pose_en_sudo_ne_classe_pas_chez_autrui(self):
        """Un lien posé en superutilisateur (rattachement automatique, données
        d'avant la garde) passe aussi par le contrôle sous le propriétaire."""
        msg = self.tache_b.with_user(self.b).message_post(
            body="Envoi de B", message_type="comment", subtype_xmlid="mail.mt_comment")
        rangee = self._rangee_b(msg)
        rangee.sudo().write({"res_model": "project.task", "res_id": self.tache_a.id})
        self.assertNotEqual(self._route(rangee)[:2], ("project.task", self.tache_a.id))

    def test_confier_a_par_regle_tient_pendant_une_requete(self):
        self.env["bf.email.rule"].sudo().create({
            "name": "Pour A", "scope": "user", "user_id": self.b.id,
            "route_user_id": self.a.id, "match_type": "all",
            "condition_ids": [(0, 0, {"kind": "condition", "field_name": "subject",
                                      "operator": "contains", "value": "pour A"})],
        })
        with self._par_rpc():
            rangee = self.env["bf.email"].with_user(self.b).create({
                "subject": "Dossier pour A", "direction": "in", "user_id": self.b.id,
                "email_from": "quelqu.un@exterieur.invalid", "source": "imap",
                "account_id": self.compte_b.id, "date": "2026-10-03 07:00:00",
                "message_id_header": "<confie-garde@exterieur.invalid>"})
        self.assertEqual(rangee.sudo().user_id, self.a)
