# -*- coding: utf-8 -*-
"""Le merci de la personne fêtée : par sa clé seulement, une fois, aux
signataires et jamais à elle-même."""

import json
from datetime import datetime, timedelta

from odoo.tests import HttpCase, TransactionCase, tagged


def _tableau_livre(env, nom="Bonne fête Iris", livrer=True):
    """Un tableau livré, avec sa clé de merci.

    ⚠️ `livrer=False` dans les tests HTTP : `_livrer()` rend le PDF, et
    wkhtmltopdf va chercher les feuilles de style sur le serveur de test,
    dont la requête attend le curseur que le test tient. Blocage mutuel,
    « Request timed out », et une passe qui ne finit jamais. Le cas HTTP pose
    donc l'état livré à la main ; la livraison réelle est éprouvée par les
    tests de transaction.
    """
    fetee = env["res.partner"].create({
        "name": "Iris Bellefeuille", "email": "iris.merci@example.test"})
    organisateur = env["res.users"].create({
        "name": "Org merci", "login": "cel_merci_org_%s@example.test" % nom[-4:],
        "email": "cel_merci_org@example.test",
        "groups_id": [(6, 0, [env.ref("base.group_user").id,
                              env.ref("bf_celebrations.group_organizer").id])],
    })
    board = env["bf.celebration.board"].create({
        "name": nom, "recipient_partner_id": fetee.id,
        "organizer_id": organisateur.id,
        "delivery_date": datetime.now() - timedelta(hours=1),
        "state": "open",
        # Deux personnes invitées par courriel, dont la personne fêtée
        # elle-même (un service la contient forcément).
        "invited_keys": json.dumps(["anouk@example.test",
                                    "iris.merci@example.test"]),
    })
    signataire = env["res.users"].create({
        "name": "Bastien", "login": "cel_merci_b_%s@example.test" % nom[-4:],
        "email": "bastien@example.test",
        "groups_id": [(6, 0, [env.ref("base.group_user").id])]})
    env["bf.celebration.post"].sudo().create([
        {"board_id": board.id, "author_name": "Anouk",
         "body": "<p>Bonne fête</p>"},
        {"board_id": board.id, "author_name": "Bastien",
         "body": "<p>Bravo</p>", "author_user_id": signataire.id,
         "author_partner_id": signataire.partner_id.id},
    ])
    if livrer:
        board._livrer()
    else:
        import secrets
        from odoo import fields
        board.sudo().write({
            "state": "delivered", "delivered_date": fields.Datetime.now(),
            "thanks_token": secrets.token_urlsafe(18)})
    return board


@tagged("post_install", "-at_install", "bf_celebrations")
class TestMerci(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ⚠️ Les tests qui livrent rendent un PDF, et wkhtmltopdf va chercher
        # les feuilles de style à `report.url` ou `web.base.url`. Sur un banc
        # où cette adresse est le serveur de test lui-même, la requête attend
        # le curseur que le test tient : blocage mutuel, « Request timed out »,
        # et une passe qui ne finit jamais. Une adresse fermée fait échouer la
        # récupération tout de suite ; le PDF sort quand même, sans styles.
        cls.env["ir.config_parameter"].sudo().set_param(
            "report.url", "http://127.0.0.1:9")

    def test_la_cle_nait_a_la_livraison_et_ne_va_qu_a_elle(self):
        board = _tableau_livre(self.env)
        cle = board.sudo().thanks_token
        self.assertTrue(cle and len(cle) > 15)
        self.assertIn("?cle=%s" % cle, board.recipient_url)
        self.assertNotIn("cle=", board.board_url,
                         "Le lien public ne porte pas la clé.")
        courriel = self.env["mail.mail"].sudo().search(
            [("model", "=", "bf.celebration.board"),
             ("res_id", "=", board.id)], order="id desc", limit=1)
        self.assertIn(cle, courriel.body_html,
                      "Le courriel de livraison porte la clé.")

    def test_le_merci_part_aux_signataires_pas_a_elle(self):
        board = _tableau_livre(self.env)
        avant = self.env["mail.mail"].sudo().search_count(
            [("model", "=", "bf.celebration.board"),
             ("res_id", "=", board.id)])
        self.assertTrue(board._remercier("<p>Merci à vous.</p>"))
        courriels = self.env["mail.mail"].sudo().search(
            [("model", "=", "bf.celebration.board"),
             ("res_id", "=", board.id),
             ("subject", "ilike", "remercie")])
        destinataires = sorted(c.email_to for c in courriels)
        self.assertEqual(set(destinataires), {
            "anouk@example.test", "bastien@example.test",
            '"Org merci" <cel_merci_org@example.test>'})
        self.assertEqual(len(destinataires), 3, "Une fois chacun.")
        self.assertNotIn("iris.merci@example.test", " ".join(destinataires))
        self.assertTrue(board.thanks_date)
        self.assertIn("Merci à vous.", board.thanks_html)
        # Le chatter porte le merci.
        self.assertTrue(any("Merci de" in (m.body or "")
                            for m in board.message_ids))
        self.assertGreater(self.env["mail.mail"].sudo().search_count(
            [("model", "=", "bf.celebration.board"),
             ("res_id", "=", board.id)]), avant)

    def test_un_seul_merci(self):
        board = _tableau_livre(self.env)
        self.assertTrue(board._remercier("<p>Merci.</p>"))
        self.assertFalse(board._remercier("<p>Encore ?</p>"))
        self.assertIn("Merci.", board.thanks_html)
        self.assertNotIn("Encore", board.thanks_html)

    def test_pas_de_merci_avant_la_livraison(self):
        board = self.env["bf.celebration.board"].create({
            "name": "Ouverte", "recipient_email": "x@example.test",
            "delivery_date": datetime.now() + timedelta(days=1),
            "state": "open"})
        self.assertFalse(board._remercier("<p>Trop tôt</p>"))
        self.assertFalse(board.sudo().thanks_token)


@tagged("post_install", "-at_install", "bf_celebrations")
class TestMerciPublic(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ⚠️ Les tests qui livrent rendent un PDF, et wkhtmltopdf va chercher
        # les feuilles de style à `report.url` ou `web.base.url`. Sur un banc
        # où cette adresse est le serveur de test lui-même, la requête attend
        # le curseur que le test tient : blocage mutuel, « Request timed out »,
        # et une passe qui ne finit jamais. Une adresse fermée fait échouer la
        # récupération tout de suite ; le PDF sort quand même, sans styles.
        cls.env["ir.config_parameter"].sudo().set_param(
            "report.url", "http://127.0.0.1:9")
        cls.board = _tableau_livre(cls.env, nom="Bonne fête Iris HTTP",
                                   livrer=False)
        cls.jeton = cls.board.sudo().access_token
        cls.cle = cls.board.sudo().thanks_token

    def test_sans_la_cle_pas_de_formulaire(self):
        page = self.url_open("/celebration/%s/tableau" % self.jeton).text
        self.assertNotIn("Dire merci", page)
        reponse = self.url_open(
            "/celebration/%s/merci" % self.jeton,
            data={"cle": "pasla", "body": "Je me fais passer pour elle"},
            timeout=30)
        self.assertIn("ne permet pas de remercier", reponse.text)
        self.assertFalse(self.board.sudo().thanks_html)

    def test_avec_la_cle_le_merci_parait_pour_tout_le_monde(self):
        page = self.url_open(
            "/celebration/%s/tableau?cle=%s" % (self.jeton, self.cle)).text
        self.assertIn("Dire merci", page)
        reponse = self.url_open(
            "/celebration/%s/merci" % self.jeton,
            data={"cle": self.cle, "body": "Merci du fond du cœur.\n<b>x</b>"},
            timeout=30)
        self.assertIn("merci=1", reponse.url)
        merci = self.board.sudo().thanks_html
        self.assertIn("Merci du fond du cœur.", merci)
        self.assertNotIn("<b>", merci, "Du texte, jamais du HTML fourni.")
        # Visible sans la clé, sur la carte et sur la page de signature fermée.
        for chemin in ("/celebration/%s/tableau", "/celebration/%s"):
            page = self.url_open(chemin % self.jeton).text
            self.assertIn("vous remercie", page, chemin)
            self.assertIn("Merci du fond du cœur.", page, chemin)
            self.assertNotIn("Dire merci", page, "Un seul merci.")
