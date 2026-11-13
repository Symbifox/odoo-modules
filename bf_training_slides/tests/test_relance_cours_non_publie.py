"""La relance du registre ne mène plus à un cours non publié.

Le correctif refusait d'abord le clic
sur un cours non publié du côté sensibilisation, mais le **socle** porte le même
lien dès que ce pont est installé (`_compute_training_url` envoie droit au
cours), et sa garde ne regardait que l'adresse courriel.

La règle du cœur de l'eLearning
(`website_slides.rule_slide_channel_visibility_signed_in_user`) exige
`website_published` pour TOUT compte portail, membre inscrit ou pas : une
relance vers un cours non publié envoie la personne sur une page qu'elle ne
peut pas ouvrir.

⚠️ Ce module part aussi chez un locataire **sans** la sensibilisation
(jlp-demo) : là, le bouton « Relancer » du socle est le seul chemin, et c'est
exactement celui que ces essais jouent.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_training_slides")
class TestRelanceCoursNonPublie(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.write({"email": "registre.essai@exemple.test"})
        Cours = cls.env["slide.channel"]
        cls.prepa = Cours.create({"name": "Cours en préparation essai",
                                  "channel_type": "training", "enroll": "invite",
                                  "visibility": "connected"})
        cls.prepa.is_published = False
        cls.publie = Cours.create({"name": "Cours publié essai",
                                   "channel_type": "training", "enroll": "invite",
                                   "visibility": "connected"})
        cls.publie.is_published = True
        cls.categorie = cls.env["bf.training.category"].create(
            {"name": "Catégorie essai", "code": "QA_T27371_SLIDES"})
        cls.personne = cls.env["res.partner"].create(
            {"name": "Apprenante essai", "email": "apprenante.essai@exemple.test"})

    def _assignation(self, cours):
        activite = self.env["bf.training.activity"].create({
            "name": "Formation essai", "category_id": self.categorie.id,
            "mode": "elearning", "slide_channel_id": cours.id})
        return self.env["bf.training.assignment"].create({
            "partner_id": self.personne.id, "activity_id": activite.id,
            "due_date": fields.Date.context_today(self.env.user) + timedelta(days=3)})

    def _dernier_mail(self):
        return self.env["mail.mail"].search([], order="id desc", limit=1).id or 0

    def _courriels(self, assignation, depuis):
        return self.env["mail.mail"].search([
            ("id", ">", depuis), ("model", "=", "bf.training.assignment"),
            ("res_id", "=", assignation.id)])

    # -- le bouton du socle refuse -------------------------------------------
    def test_un_cours_non_publie_refuse_le_clic(self):
        assignation = self._assignation(self.prepa)
        with self.assertRaises(UserError) as leve:
            assignation.action_remind()
        self.assertIn("publié", str(leve.exception))

    def test_un_cours_publie_ne_refuse_rien(self):
        assignation = self._assignation(self.publie)
        depuis = self._dernier_mail()
        assignation.action_remind()
        self.assertEqual(len(self._courriels(assignation, depuis)), 1)
        self.assertEqual(assignation.reminder_count, 1)

    def test_un_cours_non_publie_ne_compte_aucune_relance(self):
        """⚠️ Pas d'`assertRaises` : son point de sauvegarde défait ce que le
        bloc a écrit, et l'assertion lirait l'état d'AVANT sans rien prouver."""
        assignation = self._assignation(self.prepa)
        depuis = self._dernier_mail()
        leve = None
        try:
            assignation.action_remind()
        except UserError as exc:
            leve = exc
        self.env.invalidate_all()
        self.assertIsNotNone(leve, "le clic devait être refusé")
        self.assertFalse(self._courriels(assignation, depuis))
        self.assertEqual(assignation.reminder_count, 0)
        self.assertFalse(assignation.last_reminder_date)

    # -- la tâche planifiée écarte, sans mentir ------------------------------
    def test_le_cron_saute_le_cours_non_publie_et_relance_l_autre(self):
        muette = self._assignation(self.prepa)
        bonne = self._assignation(self.publie)
        depuis = self._dernier_mail()
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
        self.assertFalse(muette.last_reminder_date)

    # -- sans gabarit, il n'y a pas de lien, donc rien à refuser -------------
    def test_sans_gabarit_le_rappel_se_pose_au_fil_meme_sans_publication(self):
        self.env.ref("bf_training.mail_template_training_reminder").unlink()
        assignation = self._assignation(self.prepa)
        depuis = self._dernier_mail()
        assignation.action_remind()
        self.assertFalse(self._courriels(assignation, depuis))
        self.assertIn("Formation essai",
                      "\n".join(assignation.message_ids.mapped(lambda m: m.body or "")))
        self.assertEqual(assignation.reminder_count, 1)

    # -- la garde du socle reste en place ------------------------------------
    def test_la_garde_de_l_adresse_du_socle_tient_toujours(self):
        sans = self.env["res.partner"].create({"name": "Sans adresse essai"})
        activite = self.env["bf.training.activity"].create({
            "name": "Formation essai bis", "category_id": self.categorie.id,
            "mode": "elearning", "slide_channel_id": self.publie.id})
        assignation = self.env["bf.training.assignment"].create({
            "partner_id": sans.id, "activity_id": activite.id,
            "due_date": fields.Date.context_today(self.env.user) + timedelta(days=3)})
        with self.assertRaises(UserError) as leve:
            assignation.action_remind()
        self.assertIn("Sans adresse essai", str(leve.exception))
