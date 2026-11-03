from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDigestPorteLeConstat(TransactionCase):
    """Un constat du lecteur doit se retrouver dans le courriel réellement créé.

    L'essai de rendu éprouve la section ; celui-ci éprouve le CHEMIN complet,
    `_send_digest_to` comprise, donc la décision d'envoyer et l'insertion dans
    l'enveloppe.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.lecteur = cls.env["res.users"].create({
            "name": "Lecteur des constats",
            "login": "constats.lecteur@example.test",
            "email": "constats.lecteur@example.test",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.digest = cls.env["daily.digest.config"].create({
            "name": "Digest d'essai constats",
            "user_ids": [(6, 0, [cls.lecteur.id])],
            "include_weather": False,
            "include_quote": False,
            "company_id": cls.env.company.id,
        })
        cls.detecteur = cls.env["bf.irregularity.detector"].create({
            "name": "Détecteur d'essai", "code": "essai_crochet_digest",
            "family": "late", "severity": "warning",
        })

    def _constat(self):
        return self.env["bf.irregularity"].create({
            "name": "Un dossier muet",
            "detector_id": self.detecteur.id,
            "severity": "warning",
            "key": "essai,1",
            "owner_id": self.lecteur.id,
            "state": "open",
        })

    def _envoyer(self):
        """Envoie le digest en retenant le courriel.

        ⚠️ `daily_todo_digest` crée son `mail.mail` avec `auto_delete: True` et
        l'envoie aussitôt. En mode essai, Odoo saute le SMTP et rapporte un
        succès, donc l'enregistrement est SUPPRIMÉ avant qu'un essai puisse le
        lire : chercher le courriel après coup ne trouve rien, et un essai qui
        affirme sur ce vide ne prouve rien. Les cinq essais de
        `daily_todo_digest` qui pilotent `_send_digest*` rustinent `send` pour
        cette raison.
        """
        Mail = type(self.env["mail.mail"])
        with patch.object(Mail, "send", lambda self, *a, **k: True):
            self.digest._send_digest_to(self.lecteur)
        return self.env["mail.mail"].search([("email_to", "=", self.lecteur.email)],
                                            order="id desc", limit=1)

    def test_le_corps_ne_nomme_rien_sans_constat(self):
        """Discrimination : le courriel part, mais sans notre section.

        Le cœur du digest envoie dès qu'une section est allumée, la nôtre
        comprise : ce qui doit disparaître sans constat, c'est la SECTION, pas le
        courriel. L'affirmation porte donc sur un courriel RÉEL, pas sur un
        recordset vide.
        """
        courriel = self._envoyer()
        self.assertTrue(courriel, "le digest doit tout de même partir")
        self.assertNotIn("Un dossier muet", courriel.body_html or "")

    def test_un_constat_parait_dans_le_courriel_envoye(self):
        self._constat()
        courriel = self._envoyer()
        self.assertTrue(courriel, "le digest doit partir et porter le constat")
        self.assertIn(self.lecteur.email, courriel.email_to or "")
        self.assertIn("Un dossier muet", courriel.body_html or "")
