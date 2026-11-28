"""Le code d'accès de la page suit le bénéficiaire, avec le VRAI champ.

Le module porteur l'éprouve en simulant un pont (il remplace `portal_client`
par un patch) ; ici, c'est le pont lui-même et `bf_beneficiary_id` qui
décident. Les deux doivent dire la même chose : le code part à la partie que
la page nomme, jamais au payeur à sa place.
"""
from unittest.mock import patch

from odoo.tests import HttpCase, tagged

from odoo.addons.bf_stepbystep_beneficiary.controllers.portal import (
    ProgressionPortailBeneficiaire,
)
from odoo.addons.bf_stepbystep_clients.controllers.portal import ProgressionPortail
from odoo.addons.bf_stepbystep_clients.tests.test_portail import _Mandat

CODE = "123456"


@tagged("post_install", "-at_install")
class TestCodeDuBeneficiaire(HttpCase, _Mandat):

    def setUp(self):
        super().setUp()
        self.projet, _colonnes = self._mandat(self.env)
        self.projet.partner_id.email = "payeur@essai.invalid"
        self.projet.action_progression_portal_publish()
        self.projet.progression_otp_required = True
        # L'environnement d'essai éteint les crons, et la fonction refuse de
        # créer un code quand la file de courriel ne tourne pas : on rallume la
        # file, comme le fait l'essai du module porteur.
        self.env.ref("mail.ir_cron_mail_scheduler_action").sudo().active = True

    def _demander(self):
        """Charge la page fermée : un code part, et c'est CODE."""
        with patch("odoo.addons.bf_stepbystep_clients.models.progression_otp"
                   ".secrets.randbelow", return_value=int(CODE)):
            jeton = self.projet.progression_portal_token
            return self.url_open(
                f"/progression/{self.projet.id}/{jeton}", allow_redirects=False).text

    def _codes(self):
        return self.env["bf.progression.otp"].sudo().search(
            [("project_id", "=", self.projet.id)])

    def test_le_code_part_au_beneficiaire(self):
        self.projet.bf_beneficiary_id = self.env["res.partner"].create({
            "name": "Recevant du mandat", "is_company": True,
            "email": "lecteur@essai.invalid"})
        page = self._demander()
        self.assertIn("l***@essai.invalid", page)
        self.assertEqual(self._codes().email, "lecteur@essai.invalid")
        self.assertEqual(self._codes().mail_id.email_to, "lecteur@essai.invalid")

    def test_un_beneficiaire_sans_adresse_ne_retombe_pas_sur_le_payeur(self):
        """🔴 Le code ouvrirait au payeur la page d'un dossier qui n'est pas le
        sien, et le lecteur resterait dehors."""
        self.projet.bf_beneficiary_id = self.env["res.partner"].create({
            "name": "Recevant sans courriel", "is_company": True})
        page = self._demander()
        self.assertIn("no email address on file", page)
        self.assertFalse(self._codes())

    def test_sans_beneficiaire_le_pont_garde_la_valeur_du_porteur(self):
        """🔴 Sans bénéficiaire, le pont ne réécrit pas `portal_client`, même à
        l'identique : il effacerait ce qu'un autre pont y a mis. La partie
        nommée par le porteur est ici une sentinelle, que seul un pont qui
        réécrit sans condition remplace."""
        sentinelle = self.env["res.partner"].create(
            {"name": "Nommée par un autre pont"})

        # Le vrai `_progression_valeurs` lit `request`, absente hors d'une
        # requête HTTP : l'autre pont simulé rend donc le seul dict utile.
        def autre_pont(cls, projet):
            return {"portal_client": sentinelle}

        with patch.object(ProgressionPortail, "_progression_valeurs",
                          classmethod(autre_pont)):
            valeurs = ProgressionPortailBeneficiaire._progression_valeurs(
                self.projet)
            self.assertEqual(valeurs["portal_client"], sentinelle)
            # Témoin : avec un bénéficiaire, c'est bien le pont qui décide.
            recevant = self.env["res.partner"].create(
                {"name": "Recevant", "is_company": True})
            self.projet.bf_beneficiary_id = recevant
            valeurs = ProgressionPortailBeneficiaire._progression_valeurs(
                self.projet)
            self.assertEqual(valeurs["portal_client"], recevant)

    def test_sans_beneficiaire_le_code_part_encore_au_client_du_projet(self):
        """Témoin positif : le pont ne change rien aux mandats à une partie."""
        self.assertFalse(self.projet.bf_beneficiary_id)
        page = self._demander()
        self.assertIn("p***@essai.invalid", page)
        self.assertEqual(self._codes().email, "payeur@essai.invalid")

    # ------------------------------------------------------------------
    # Un bénéficiaire saisi comme contact
    # ------------------------------------------------------------------

    def _contact_sous(self, email_societe):
        societe = self.env["res.partner"].create({
            "name": "Bêta inc.", "is_company": True, "email": email_societe})
        return self.env["res.partner"].create({
            "name": "Jean Fictif", "parent_id": societe.id,
            "email": "jean@essai.invalid"})

    def test_le_contact_recoit_le_code_quand_sa_societe_n_a_pas_d_adresse(self):
        self.projet.bf_beneficiary_id = self._contact_sous(False)
        page = self._demander()
        self.assertIn("j***@essai.invalid", page)
        self.assertEqual(self._codes().email, "jean@essai.invalid")

    def test_un_contact_archive_ne_recoit_plus_le_code(self):
        """Archivé, il a quitté le client : pas de repli vers lui."""
        contact = self._contact_sous(False)
        self.projet.bf_beneficiary_id = contact
        contact.active = False
        page = self._demander()
        self.assertIn("no email address on file", page)
        self.assertFalse(self._codes())

    def test_le_beneficiaire_passe_devant_le_contact_payeur_de_sa_societe(self):
        """Payeur et bénéficiaire, deux contacts d'une même société sans
        adresse : le porteur envoyait le code au contact qui paie."""
        jean = self._contact_sous(False)
        marc = self.env["res.partner"].create({
            "name": "Marc Fictif", "parent_id": jean.parent_id.id,
            "email": "marc@essai.invalid"})
        self.projet.write({"partner_id": marc.id, "bf_beneficiary_id": jean.id})
        self._demander()
        self.assertEqual(self._codes().email, "jean@essai.invalid")

    def test_la_societe_qui_a_une_adresse_la_garde(self):
        """Témoin : le repli ne joue que sur une société SANS adresse ; avec
        une, c'est elle que la page nomme et qui reçoit, comme dans le porteur."""
        self.projet.bf_beneficiary_id = self._contact_sous("info@essai.invalid")
        self._demander()
        self.assertEqual(self._codes().email, "info@essai.invalid")


@tagged("post_install", "-at_install")
class TestPurgeAuChangement(HttpCase, _Mandat):
    """Changer la partie nommée oublie les codes et les appareils du mandat."""

    def setUp(self):
        super().setUp()
        self.projet, _colonnes = self._mandat(self.env)
        self.Otp = self.env["bf.progression.otp"].sudo()
        self.Appareils = self.env["bf.progression.device"].sudo()
        self.societe = self.env["res.partner"].create(
            {"name": "Recevante", "is_company": True, "email": "r@essai.invalid"})
        self.contact = self.env["res.partner"].create(
            {"name": "Contact A", "parent_id": self.societe.id,
             "email": "a@essai.invalid"})
        self.autre_contact = self.env["res.partner"].create(
            {"name": "Contact B", "parent_id": self.societe.id,
             "email": "b@essai.invalid"})

    def _semer(self):
        self.Appareils.create({"project_id": self.projet.id, "token_hash": "x" * 64})
        self.Otp.create({"project_id": self.projet.id, "email": "p@essai.invalid"})
        self.projet.sudo().write({"progression_otp_failed_streak": 3})

    def _restes(self):
        domaine = [("project_id", "=", self.projet.id)]
        return (self.Appareils.search_count(domaine), self.Otp.search_count(domaine))

    def test_nommer_un_beneficiaire_oublie_codes_et_appareils(self):
        self._semer()
        self.projet.bf_beneficiary_id = self.contact
        self.assertEqual(self._restes(), (0, 0))
        self.assertEqual(self.projet.sudo().progression_otp_failed_streak, 0)

    def test_retirer_le_beneficiaire_oublie_aussi(self):
        self.projet.bf_beneficiary_id = self.contact
        self._semer()
        self.projet.bf_beneficiary_id = False
        self.assertEqual(self._restes(), (0, 0))
        self.assertEqual(self.projet.sudo().progression_otp_failed_streak, 0)

    def test_un_autre_contact_de_la_meme_societe_ne_deconnecte_personne(self):
        """La page nomme la même société, et c'est elle qui reçoit le code
        (elle a son adresse) : personne ne doit refaire un code."""
        self.projet.bf_beneficiary_id = self.contact
        self._semer()
        self.projet.bf_beneficiary_id = self.autre_contact
        self.assertEqual(self._restes(), (1, 1))

    def test_sous_une_societe_sans_adresse_changer_de_contact_oublie(self):
        """Sans adresse sur la société, c'est le contact qui reçoit le code :
        en changer, c'est changer de lecteur."""
        self.societe.email = False
        self.projet.bf_beneficiary_id = self.contact
        self._semer()
        self.projet.bf_beneficiary_id = self.autre_contact
        self.assertEqual(self._restes(), (0, 0))

    def test_changer_de_donneur_d_ordre_sans_beneficiaire_oublie(self):
        """Sans bénéficiaire, c'est le donneur d'ordre que la page nomme."""
        self.assertFalse(self.projet.bf_beneficiary_id)
        self._semer()
        self.projet.partner_id = self.societe
        self.assertEqual(self._restes(), (0, 0))

    def test_changer_de_donneur_d_ordre_quand_le_beneficiaire_est_nomme_garde(self):
        """Témoin : la page nomme le bénéficiaire, qui ne change pas."""
        self.projet.bf_beneficiary_id = self.contact
        self._semer()
        self.projet.partner_id = self.env["res.partner"].create(
            {"name": "Autre payeur", "is_company": True, "email": "o@essai.invalid"})
        self.assertEqual(self._restes(), (1, 1))

    def test_ecrire_un_autre_champ_ne_touche_a_rien(self):
        """Témoin positif des essais précédents."""
        self._semer()
        self.projet.name = "Renommé"
        self.assertEqual(self._restes(), (1, 1))
