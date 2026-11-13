"""Une relance qui ne part pas ne se compte pas.

Le défaut d'origine a été trouvé dans `bf_security_awareness` (l'invitation à un
cours non publié), mais le socle portait la même famille, en pire :
`_relancer()` écrivait `reminder_count` et `last_reminder_date` avant le moindre
envoi, appelait `send_mail` sans `try`, et ne regardait même pas si la personne
avait une adresse courriel. Donc :

- une personne sans adresse voyait « Relances : 3 » sans avoir rien reçu, et le
  gabarit portant `auto_delete`, même le `mail.mail` en échec était effacé : il
  ne restait aucune trace nulle part ;
- une seule exception emportait la journée entière de la tâche planifiée, et
  les assignations suivantes du lot n'étaient jamais relancées.

⚠️ `assertRaises` d'Odoo défait ce que le bloc a écrit : les essais d'état et
les essais de levée sont séparés ici.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.mail.models.mail_template import MailTemplate


@tagged("post_install", "-at_install", "bf_training")
class TestRelanceMuette(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.write({"email": "registre.essai@exemple.test"})
        cls.categorie = cls.env["bf.training.category"].create(
            {"name": "Catégorie essai", "code": "QA_T27371"})
        cls.activite = cls.env["bf.training.activity"].create({
            "name": "Secourisme en milieu de travail",
            "category_id": cls.categorie.id, "mode": "elearning"})
        cls.avec = cls.env["res.partner"].create({
            "name": "Alice Avec", "email": "alice.avec.essai@exemple.test"})
        cls.sans = cls.env["res.partner"].create({"name": "Bruno Sans"})

    # -- outils ---------------------------------------------------------------
    def _assignation(self, partenaire, jours=3):
        return self.env["bf.training.assignment"].create({
            "partner_id": partenaire.id, "activity_id": self.activite.id,
            "due_date": (fields.Date.context_today(self.env.user)
                         + timedelta(days=jours))})

    def _courriels(self, assignation, depuis):
        return self.env["mail.mail"].search([
            ("id", ">", depuis),
            ("model", "=", "bf.training.assignment"),
            ("res_id", "=", assignation.id),
        ])

    def _dernier_mail(self):
        return self.env["mail.mail"].search([], order="id desc", limit=1).id or 0

    def _fil(self, assignation):
        return "\n".join(assignation.message_ids.mapped(lambda m: m.body or ""))

    # -- le bouton refuse -----------------------------------------------------
    def test_sans_adresse_le_bouton_refuse_et_nomme_la_personne(self):
        assignation = self._assignation(self.sans)
        with self.assertRaises(UserError) as leve:
            assignation.action_remind()
        self.assertIn("Bruno Sans", str(leve.exception))

    def test_le_bouton_ne_refuse_pas_une_relance_qui_peut_partir(self):
        assignation = self._assignation(self.avec)
        depuis = self._dernier_mail()
        assignation.action_remind()
        self.assertEqual(len(self._courriels(assignation, depuis)), 1)
        self.assertEqual(assignation.reminder_count, 1)
        self.assertTrue(assignation.last_reminder_date)

    def test_un_refus_n_emporte_pas_les_relances_du_meme_lot(self):
        """🔴 Le refus se lève AVANT le premier envoi, jamais en chemin.

        ⚠️ Pas d'`assertRaises` : il enveloppe le bloc d'un point de sauvegarde
        et le DÉFAIT quand l'exception tombe, donc une relance partie avant la
        levée serait annulée et l'essai passerait sans rien prouver.
        """
        bonne = self._assignation(self.avec)
        mauvaise = self._assignation(self.sans)
        depuis = self._dernier_mail()
        leve = None
        try:
            (bonne | mauvaise).action_remind()
        except UserError as exc:
            leve = exc
        self.env.invalidate_all()
        self.assertIsNotNone(leve, "le lot devait être refusé")
        self.assertFalse(
            self._courriels(bonne, depuis),
            "aucune relance du lot ne doit être partie avant le refus")
        self.assertEqual(
            bonne.reminder_count, 0,
            "rien ne doit avoir été compté avant le refus")

    # -- la tâche planifiée écarte, sans mentir -------------------------------
    def test_sans_adresse_rien_ne_part_et_rien_ne_se_compte(self):
        assignation = self._assignation(self.sans)
        depuis = self._dernier_mail()
        assignation._relancer()
        self.assertFalse(self._courriels(assignation, depuis))
        self.assertEqual(assignation.reminder_count, 0)
        self.assertFalse(assignation.last_reminder_date)

    def test_le_cron_relance_les_autres_et_saute_celle_sans_adresse(self):
        bonne = self._assignation(self.avec)
        muette = self._assignation(self.sans)
        depuis = self._dernier_mail()
        # ⚠️ Borné aux deux assignations de l'essai : le cron balaie toute la
        # base, et sur une base chargée il relancerait des dizaines
        # d'enregistrements étrangers — lent, et du bruit dans le journal.
        A = self.env["bf.training.assignment"]
        vrai_search = type(A).search

        def search(modele, domaine, **kw):
            if modele._name == "bf.training.assignment":
                domaine = list(domaine) + [("id", "in", (bonne | muette).ids)]
            return vrai_search(modele, domaine, **kw)

        with patch.object(type(A), "search", search):
            A._cron_reminders()
        self.assertEqual(len(self._courriels(bonne, depuis)), 1)
        self.assertEqual(bonne.reminder_count, 1)
        self.assertFalse(self._courriels(muette, depuis))
        self.assertEqual(muette.reminder_count, 0)

    # -- une exception ne mange plus le lot -----------------------------------
    def test_un_echec_d_envoi_ne_mange_pas_le_reste_du_lot(self):
        """🔴 `send_mail` partait sans `try` : une exception annulait tout."""
        casse = self._assignation(self.avec)
        suivante = self._assignation(self.avec)
        depuis = self._dernier_mail()
        vrai = MailTemplate.send_mail

        def send_mail(self_gabarit, res_id, **kw):
            if res_id == casse.id:
                raise ValueError("envoi cassé pour la mesure")
            return vrai(self_gabarit, res_id, **kw)

        with patch.object(MailTemplate, "send_mail", send_mail):
            (casse | suivante)._relancer()
        self.assertEqual(
            len(self._courriels(suivante, depuis)), 1,
            "la suivante du lot doit être relancée quand même")
        self.assertEqual(suivante.reminder_count, 1)
        self.assertEqual(casse.reminder_count, 0, "l'échec ne se compte pas")
        self.assertFalse(casse.last_reminder_date)
        self.assertIn("cassé", self._fil(casse))

    def test_une_erreur_de_base_laisse_le_fil_et_le_lot_intacts(self):
        """🔴 Une erreur venue de la BASE abandonne la
        transaction, et tout ordre suivant échoue en « current transaction is
        aborted ». Sans point de sauvegarde, le `message_post` du gestionnaire
        lèverait depuis l'intérieur du `except` — donc non rattrapé — et la
        tâche planifiée serait de nouveau emportée en entier.

        Rien n'est simulé ici : l'envoi patché exécute vraiment un `SELECT 1/0`,
        donc la transaction est vraiment abandonnée. Que la note porte le
        message de la base EST la preuve que le point de sauvegarde a rendu le
        curseur utilisable avant de l'écrire.
        """
        casse = self._assignation(self.avec)
        suivante = self._assignation(self.avec)
        depuis = self._dernier_mail()
        vrai = MailTemplate.send_mail

        def send_mail(gabarit, res_id, **kw):
            if res_id == casse.id:
                gabarit.env.cr.execute("SELECT 1/0")
            return vrai(gabarit, res_id, **kw)

        with patch.object(MailTemplate, "send_mail", send_mail):
            (casse | suivante)._relancer()
        self.assertIn("division by zero", self._fil(casse).lower())
        self.assertEqual(casse.reminder_count, 0, "l'échec ne se compte pas")
        self.assertFalse(casse.last_reminder_date)
        self.assertEqual(
            len(self._courriels(suivante, depuis)), 1,
            "la suivante du lot est relancée quand même")
        self.assertEqual(suivante.reminder_count, 1)

    # -- le repli sans gabarit reste intact -----------------------------------
    def test_sans_gabarit_le_rappel_se_pose_au_fil_et_compte(self):
        self.env.ref("bf_training.mail_template_training_reminder").unlink()
        assignation = self._assignation(self.avec)
        depuis = self._dernier_mail()
        assignation.action_remind()
        self.assertFalse(self._courriels(assignation, depuis))
        self.assertIn("Secourisme", self._fil(assignation))
        self.assertEqual(
            assignation.reminder_count, 1,
            "le rappel posé au fil compte : la personne l'a bien reçu")
