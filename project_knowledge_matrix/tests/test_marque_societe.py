"""La marque d'une matrice suit SA société, pas celle de la session.

Le rapport d'une matrice d'une société secondaire partait par le bouton
« Envoyer le rapport » avec les couleurs, le logo et le nom de la société
principale : le bouton, l'envoi planifié et le PDF lisaient `env.company`, la
première société de la session. Le PDF mêlait même le logo de la matrice et la
palette de la session.

La règle : le champ Société de la matrice fait foi, à défaut celui du
projet, à défaut la session. La marque seulement : l'adresse d'envoi et l'adresse
de réponse ne changent pas.
"""

import base64
import io
from unittest.mock import patch

from PIL import Image

from odoo import Command
from odoo.tests import TransactionCase, tagged

SESSION = {"primary": "#0A64A0", "dark": "#0B1F33"}
MATRICE = {"primary": "#E17A4B", "dark": "#2A1F1D"}
ENVOI_COUPE = "odoo.addons.mail.models.mail_mail.MailMail.send"


def _logo(couleur):
    tampon = io.BytesIO()
    Image.new("RGB", (8, 8), couleur).save(tampon, format="PNG")
    return base64.b64encode(tampon.getvalue())


def _peindre(societe, couleurs):
    """Pose la palette dans les champs que ce locataire connaît (`_pkm_brand` les lit)."""
    valeurs = {}
    for champ, cle in (("report_brand_primary", "primary"), ("primary_color", "primary"),
                       ("report_brand_dark", "dark"), ("secondary_color", "dark")):
        if champ in societe._fields:
            valeurs[champ] = couleurs[cle]
    societe.write(valeurs)


@tagged("post_install", "-at_install")
class TestMarqueDeLaSociete(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.session = cls.env["res.company"].create({"name": "Société de la session"})
        cls.autre = cls.env["res.company"].create({"name": "Société de la matrice"})
        _peindre(cls.session, SESSION)
        _peindre(cls.autre, MATRICE)
        # Deux logos distincts : par défaut, toute société neuve reçoit le même.
        cls.session.logo = _logo(SESSION["dark"])
        cls.autre.logo = _logo(MATRICE["dark"])
        # La société de la session est en tête, comme la société principale après
        # une reconnexion.
        cls.env = cls.env(context=dict(
            cls.env.context, allowed_company_ids=[cls.session.id, cls.autre.id]))
        cls.destinataire = cls.env["res.partner"].create({
            "name": "Destinataire", "email": "destinataire@example.invalid"})
        cls.matrice = cls.env["project.knowledge.matrix"].create({
            "name": "Matrice de l'autre société", "company_id": cls.autre.id,
            "recipient_ids": [Command.set(cls.destinataire.ids)]})

    def _nouveaux_courriels(self, envoyer):
        avant = self.env["mail.mail"].sudo().search([]).ids
        with patch(ENVOI_COUPE, lambda s, *a, **k: None):
            envoyer()
        return self.env["mail.mail"].sudo().search([("id", "not in", avant)])

    def _par_le_bouton(self):
        assistant = self.env["knowledge.matrix.send.wizard"].create({"matrix_id": self.matrice.id})
        return self._nouveaux_courriels(assistant.action_send)

    def _porte_la_marque_de_la_matrice(self, texte, quoi):
        texte = str(texte)
        bas = texte.lower()
        self.assertIn(MATRICE["dark"].lower(), bas, "%s : palette de la matrice" % quoi)
        for couleur in SESSION.values():
            self.assertNotIn(couleur.lower(), bas, "%s : palette de la session" % quoi)
        self.assertIn("Société de la matrice", texte, "%s : nom de la matrice" % quoi)
        self.assertNotIn("Société de la session", texte, "%s : nom de la session" % quoi)

    def _porte_le_logo_de_la_matrice(self, pdf, quoi):
        self.assertIn(self.autre.logo.decode(), pdf, "%s : logo de la matrice" % quoi)
        self.assertNotIn(self.session.logo.decode(), pdf, "%s : logo de la session" % quoi)

    def _pdf_joint(self, courriel):
        # En mode essai, Odoo rend le « PDF » en HTML : la palette s'y lit en clair.
        self.assertEqual(len(courriel.attachment_ids), 1)
        return courriel.attachment_ids.raw.decode()

    def test_le_bouton_habille_le_courriel_aux_couleurs_de_la_matrice(self):
        courriel = self._par_le_bouton()
        self.assertEqual(len(courriel), 1)
        self._porte_la_marque_de_la_matrice(courriel.body_html, "courriel du bouton")
        self.assertIn("/brand/logo/%d/" % self.autre.id, courriel.body_html)
        self.assertNotIn("/brand/logo/%d/" % self.session.id, courriel.body_html)

    def test_le_pdf_du_bouton_porte_palette_logo_et_nom_de_la_matrice(self):
        """Le cas d'origine : logo et nom de la matrice, palette de la session."""
        pdf = self._pdf_joint(self._par_le_bouton())
        self._porte_la_marque_de_la_matrice(pdf, "PDF joint")
        self._porte_le_logo_de_la_matrice(pdf, "PDF joint")

    def test_l_envoi_planifie_suit_la_matrice_meme_sans_societe_au_projet(self):
        """La plupart des matrices ont un projet sans société : l'envoi planifié
        tombait alors sur la société de l'usager du cron."""
        projet = self.env["project.project"].create({"name": "Projet sans société", "company_id": False})
        self.matrice.project_id = projet
        self.assertEqual(self.matrice.company_id, self.autre, "le projet sans société n'aligne rien")
        courriel = self._nouveaux_courriels(self.matrice._send_report_to_recipients)
        self.assertEqual(len(courriel), 1)
        self._porte_la_marque_de_la_matrice(courriel.body_html, "courriel planifié")
        pdf = self._pdf_joint(courriel)
        self._porte_la_marque_de_la_matrice(pdf, "PDF planifié")
        self._porte_le_logo_de_la_matrice(pdf, "PDF planifié")

    def test_l_adresse_d_envoi_et_de_reponse_ne_changent_pas(self):
        """La marque seulement. Envoyer sous la société de la matrice ferait
        recalculer à Odoo le « Répondre à » sur l'alias de cette société."""
        domaine = self.env["mail.alias.domain"].create({"name": "matrice.example"})
        self.autre.alias_domain_id = domaine
        courriel = self._par_le_bouton()
        self.assertEqual(courriel.email_from, self.env.user.email_formatted)
        self.assertNotIn("matrice.example", courriel.reply_to or "")

    def test_la_regle_de_repli(self):
        projet = self.env["project.project"].create({"name": "Projet", "company_id": self.autre.id})
        sans = self.env["project.knowledge.matrix"].create({"name": "Sans société", "company_id": False})
        self.assertEqual(self.matrice._pkm_company(), self.autre, "le champ de la matrice")
        choisie = self.env["project.knowledge.matrix"].create({
            "name": "Société choisie", "company_id": self.session.id, "project_id": projet.id})
        self.assertEqual(choisie._pkm_company(), self.session, "le champ l'emporte sur le projet")
        self.assertEqual(sans._pkm_company(), self.session, "à défaut de tout, la session")
        sans.write({"project_id": projet.id, "company_id": False})
        self.assertFalse(sans.company_id)
        self.assertEqual(sans._pkm_company(), self.autre, "à défaut du champ, le projet")


@tagged("post_install", "-at_install")
class TestSocieteAligneeSurLeProjet(TransactionCase):
    """Une matrice créée sans société explicite prenait celle de la session, même
    quand son projet appartenait à une autre société."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.session = cls.env["res.company"].create({"name": "Session"})
        cls.autre = cls.env["res.company"].create({"name": "Autre"})
        cls.env = cls.env(context=dict(
            cls.env.context, allowed_company_ids=[cls.session.id, cls.autre.id]))
        cls.projet = cls.env["project.project"].create({"name": "Projet de l'autre", "company_id": cls.autre.id})
        cls.projet_libre = cls.env["project.project"].create({"name": "Projet libre", "company_id": False})
        cls.Matrice = cls.env["project.knowledge.matrix"]

    def test_creee_sur_un_projet_la_matrice_prend_sa_societe(self):
        self.assertEqual(self.Matrice.create({"name": "M", "project_id": self.projet.id}).company_id, self.autre)
        matrice = self.Matrice.with_context(default_project_id=self.projet.id).create({"name": "M"})
        self.assertEqual(matrice.company_id, self.autre, "projet venu du contexte")

    def test_une_societe_fournie_reste_la_sienne(self):
        matrice = self.Matrice.create({
            "name": "M", "project_id": self.projet.id, "company_id": self.session.id})
        self.assertEqual(matrice.company_id, self.session)

    def test_un_projet_sans_societe_laisse_la_session(self):
        self.assertEqual(
            self.Matrice.create({"name": "M", "project_id": self.projet_libre.id}).company_id, self.session)

    def test_changer_de_projet_aligne_la_societe(self):
        matrice = self.Matrice.create({"name": "M", "project_id": self.projet_libre.id})
        matrice.project_id = self.projet
        self.assertEqual(matrice.company_id, self.autre)
        matrice.write({"project_id": self.projet_libre.id})
        self.assertEqual(matrice.company_id, self.autre, "un projet sans société ne retire rien")
        matrice.write({"project_id": self.projet.id, "company_id": self.session.id})
        self.assertEqual(matrice.company_id, self.session, "fournie, elle reste")

    def test_reecrire_le_meme_projet_garde_la_societe_choisie(self):
        """Un import ou une modification en masse réécrit souvent le même projet."""
        choisie = self.Matrice.create({"name": "M", "project_id": self.projet.id, "company_id": self.session.id})
        alignee = self.Matrice.create({"name": "N", "project_id": self.projet_libre.id})
        (choisie | alignee).write({"project_id": self.projet.id})
        self.assertEqual(choisie.company_id, self.session, "même projet : la société choisie reste")
        self.assertEqual(alignee.company_id, self.autre, "projet changé : la société suit")

    def test_le_formulaire_aligne_la_societe_sur_le_projet(self):
        # Par le registre des onchange d'Odoo : un décorateur mal ciblé ne passerait pas.
        matrice = self.Matrice.new({"name": "M", "company_id": self.session.id})
        matrice.project_id = self.projet
        matrice._apply_onchange_methods("project_id", {"warnings": set()})
        self.assertEqual(matrice.company_id, self.autre)
        matrice.project_id = self.projet_libre
        matrice._apply_onchange_methods("project_id", {"warnings": set()})
        self.assertEqual(matrice.company_id, self.autre, "un projet sans société ne retire rien")
