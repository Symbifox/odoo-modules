"""Un Message-ID forgé ne rattache plus une ligne au message d'autrui.

L'ingestion IMAP (le cron, sans requête HTTP) liait la ligne au ``mail.message``
qui portait le même Message-ID, trouvé en superutilisateur, quels que soient
les droits du propriétaire de la boîte sur ce message. Le
rattachement exige maintenant que le PROPRIÉTAIRE de la boîte puisse lire le
message ; sinon la ligne naît comme un courriel neuf, sans erreur.
Données inventées ; aucune connexion IMAP (octets passés à `_ingest_rfc822`).
"""
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
