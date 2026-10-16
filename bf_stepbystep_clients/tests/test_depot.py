"""Le dépôt de pièces au portail Progression.

Ce qui compte : la pièce arrive au dossier (pièce jointe de la tâche de l'étape,
note interne, trace avec empreinte) ; on ne dépose qu'avec le jeton, sur une
pièce d'une étape faite ou en cours de CE projet ; les fichiers refusés le sont
pour une raison que la page sait dire.
"""
import hashlib
import re

from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from ..models.progression_document import DepotRefuse
from .test_portail import _Mandat


@tagged("post_install", "-at_install")
class TestDepotAuPortail(HttpCase, _Mandat):

    def setUp(self):
        super().setUp()
        self.projet, self.colonnes = self._mandat(self.env)
        Document = self.env["bf.progression.document"]
        self.certificat = Document.create({
            "name": "Certificat de localisation", "stage_id": self.colonnes[1].id,
            "instructions": "Moins de 10 ans."})
        self.acte = Document.create({
            "name": "Procuration signée", "stage_id": self.colonnes[3].id})
        self.interne = Document.create({
            "name": "Pièce d'une colonne interne", "stage_id": self.colonnes[2].id})
        self.projet.action_progression_portal_publish()
        self.jeton = self.projet.progression_portal_token
        self.page = "/progression/%s/%s" % (self.projet.id, self.jeton)
        # Le jeton CSRF se prend une fois, sur la vraie page publiée : les essais
        # qui postent avec un faux jeton de portail, ou après la dépublication,
        # doivent atteindre notre garde, pas le refus CSRF d'Odoo (400).
        self.csrf = re.search(r'name="csrf_token" value="([^"]+)"',
                              self.url_open(self.page).text).group(1)

    def _deposer(self, document, nom="certificat.pdf", contenu=b"%PDF-1.4 essai", jeton=None,
                 autres_fichiers=None):
        fichiers = {"file": (nom, contenu)} if nom else {}
        fichiers.update(autres_fichiers or {})
        return self.url_open(
            "/progression/%s/%s/deposit/%s" % (self.projet.id, jeton or self.jeton, document.id),
            data={"csrf_token": self.csrf},
            files=fichiers or None,
            allow_redirects=False)

    def test_la_piece_arrive_au_dossier(self):
        page = self.url_open(self.page).text
        self.assertIn("Certificat de localisation", page)
        self.assertIn("Moins de 10 ans.", page)
        # Une pièce d'une étape à venir ou d'une colonne interne ne se montre pas.
        self.assertNotIn("Procuration signée", page)
        self.assertNotIn("colonne interne", page)

        reponse = self._deposer(self.certificat)
        self.assertEqual(reponse.status_code, 303)
        self.assertIn("message=received", reponse.headers["Location"])

        depot = self.projet.progression_deposit_ids
        self.assertEqual(len(depot), 1)
        self.assertEqual(depot.document_id, self.certificat)
        self.assertEqual(depot.checksum, hashlib.sha256(b"%PDF-1.4 essai").hexdigest())
        # La pièce va à la tâche OUVERTE de la colonne, pas à celle qui est faite.
        self.assertEqual(depot.task_id.name, "Revue interne du dossier fiscal 2026")
        self.assertEqual(depot.attachment_id.res_model, "project.task")
        self.assertEqual(depot.attachment_id.res_id, depot.task_id.id)
        self.assertEqual(depot.attachment_id.raw, b"%PDF-1.4 essai")
        note = depot.task_id.message_ids.filtered(
            lambda m: "Certificat de localisation" in (m.body or ""))
        self.assertTrue(note)
        self.assertTrue(note.subtype_id.internal, "une note interne, pas un courriel")
        self.assertEqual(note.author_id, self.projet.partner_id)

        # Une deuxième version s'ajoute, sans effacer la première.
        self._deposer(self.certificat, nom="certificat-v2.pdf", contenu=b"%PDF-1.4 v2")
        self.assertEqual(len(self.projet.progression_deposit_ids), 2)
        self.assertIn("Received", self.url_open(self.page).text)

    def test_les_refus(self):
        cas = {
            "type": dict(nom="virus.exe", contenu=b"MZ"),
            "empty": dict(nom="vide.pdf", contenu=b""),
            "missing": dict(nom=None),
        }
        for code, arguments in cas.items():
            with self.subTest(code):
                reponse = self._deposer(self.certificat, **arguments)
                self.assertEqual(reponse.status_code, 303)
                self.assertIn("message=%s" % code, reponse.headers["Location"])
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_stepbystep_clients.portal_max_upload_mb", 1)
        reponse = self._deposer(self.certificat, contenu=b"x" * (1024 * 1024 + 1))
        self.assertIn("message=too_large", reponse.headers["Location"])
        self.assertFalse(self.projet.progression_deposit_ids)

    def test_on_ne_depose_que_ce_qui_est_attendu_maintenant(self):
        for document in (self.acte, self.interne):
            with self.subTest(document.name):
                self.assertEqual(self._deposer(document).status_code, 404)
        autre, colonnes = self._mandat(self.env)
        etranger = self.env["bf.progression.document"].create({
            "name": "Pièce d'un autre mandat", "stage_id": colonnes[1].id})
        self.assertEqual(self._deposer(etranger).status_code, 404)
        self.assertFalse(self.projet.progression_deposit_ids)

    def test_sans_jeton_ou_portail_ferme_rien_ne_passe(self):
        self.assertEqual(self._deposer(self.certificat, jeton="x" * 43).status_code, 404)
        self.projet.action_progression_portal_unpublish()
        self.assertEqual(self._deposer(self.certificat).status_code, 404)
        self.assertFalse(self.projet.progression_deposit_ids)

    def test_la_cadence_est_bornee(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_stepbystep_clients.portal_deposits_per_hour", 2)
        Depot = self.env["bf.progression.deposit"].sudo()
        Depot._verser(self.projet, self.certificat, "a.pdf", b"a")
        Depot._verser(self.projet, self.certificat, "b.pdf", b"b")
        with self.assertRaises(UserError):
            Depot._verser(self.projet, self.certificat, "c.pdf", b"c")

    def test_le_nom_du_fichier_est_assaini(self):
        depot = self.env["bf.progression.deposit"].sudo()._verser(
            self.projet, self.certificat, "../../etc/rapport.pdf", b"%PDF")
        self.assertEqual(depot.filename, "rapport.pdf")
        self.assertEqual(depot.attachment_id.name, "rapport.pdf")

    def test_le_nom_garde_ni_controle_ni_mise_en_forme(self):
        """CR, LF, NUL et U+202E (qui retourne l'affichage) ne passent pas ; un nom
        qui n'a plus rien après filtrage est refusé comme un mauvais type."""
        Depot = self.env["bf.progression.deposit"].sudo()
        depot = Depot._verser(self.projet, self.certificat, "rap\u202eport\r\n\x00.pdf", b"%PDF")
        self.assertEqual(depot.filename, "rapport.pdf")
        for nom in ("\u202e\u200b", "\u202e.pdf", "\r\n"):
            with self.subTest(repr(nom)), self.assertRaises(DepotRefuse) as refus:
                Depot._verser(self.projet, self.certificat, nom, b"%PDF")
            self.assertEqual(refus.exception.code, "type")

    def test_le_client_n_est_pas_abonne_par_son_depot(self):
        """🔴 Le dépôt anonyme ne fait pas du client un abonné de la tâche : la
        réponse suivante de l'équipe ne lui part pas avec un lien vers la tâche."""
        client = self.projet.partner_id
        client.email = "famille.essai@example.com"
        self.assertEqual(self._deposer(self.certificat).status_code, 303)
        depot = self.projet.progression_deposit_ids
        tache = depot.task_id
        self.assertTrue(tache)
        self.assertNotIn(client, tache.message_partner_ids)
        note = tache.message_ids.filtered(lambda m: "Certificat de localisation" in (m.body or ""))
        self.assertEqual(note.author_id, client, "l'auteur est le client, pas l'usager public")
        self.assertEqual(note.attachment_ids, depot.attachment_id)
        message = tache.message_post(
            body="Bien reçu, merci.", message_type="comment", subtype_xmlid="mail.mt_comment")
        self.assertFalse(self.env["mail.notification"].search([
            ("mail_message_id", "=", message.id), ("res_partner_id", "=", client.id)]))
        self.assertFalse(self.env["mail.mail"].search([
            ("mail_message_id", "=", message.id), ("recipient_ids", "in", client.ids)]))

    def test_sans_tache_ouverte_la_piece_va_au_projet(self):
        """Toutes les tâches de la colonne sont fermées : la pièce va à la fiche
        projet, pas au chatter d'une tâche close."""
        self.env["project.task"].search([
            ("project_id", "=", self.projet.id), ("stage_id", "=", self.colonnes[1].id),
        ]).write({"state": "1_done"})
        self.assertEqual(self._deposer(self.certificat).status_code, 303)
        depot = self.projet.progression_deposit_ids
        self.assertFalse(depot.task_id)
        self.assertEqual(depot.attachment_id.res_model, "project.project")
        self.assertEqual(depot.attachment_id.res_id, self.projet.id)
        self.assertTrue(self.projet.message_ids.filtered(
            lambda m: "Certificat de localisation" in (m.body or "")))

    def test_la_note_suit_la_langue_du_client(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"]._load_module_terms(["bf_stepbystep_clients"], ["fr_CA"])
        self.projet.partner_id.lang = "fr_CA"
        self._deposer(self.certificat)
        tache = self.projet.progression_deposit_ids.task_id
        note = tache.message_ids.filtered(lambda m: "Certificat de localisation" in (m.body or ""))
        self.assertIn("Pièce déposée par le client au portail", note.body)

    def test_un_corps_trop_gros_est_refuse_avant_lecture(self):
        """Le corps entier compte : un fichier minuscule accompagné de 2 Mo d'autre
        chose est refusé quand le plafond est de 1 Mo."""
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_stepbystep_clients.portal_max_upload_mb", 1)
        reponse = self._deposer(self.certificat, autres_fichiers={
            "bourrage": ("bourrage.bin", b"x" * (2 * 1024 * 1024))})
        self.assertIn("message=too_large", reponse.headers["Location"])
        self.assertFalse(self.projet.progression_deposit_ids)

    def test_archives_fermees(self):
        """Une pièce archivée n'est plus listée ni déposable ; un projet archivé ne
        répond plus du tout."""
        self.certificat.active = False
        self.assertNotIn("Certificat de localisation", self.url_open(self.page).text)
        self.assertEqual(self._deposer(self.certificat).status_code, 404)
        self.certificat.active = True
        self.projet.active = False
        self.assertEqual(self.url_open(self.page, allow_redirects=False).status_code, 404)
        self.assertEqual(self._deposer(self.certificat).status_code, 404)
        self.assertFalse(self.projet.progression_deposit_ids)


@tagged("post_install", "-at_install")
class TestReglesDesDepots(TransactionCase, _Mandat):
    """Les dépôts se lisent comme le projet qui les porte."""

    def setUp(self):
        super().setUp()
        self.projet, colonnes = self._mandat(self.env)
        self.projet.privacy_visibility = "followers"
        self.projet.company_id = self.env.company
        piece = self.env["bf.progression.document"].create({
            "name": "Pièce d'identité", "stage_id": colonnes[1].id})
        self.depot = self.env["bf.progression.deposit"].sudo()._verser(
            self.projet, piece, "identite.pdf", b"%PDF")
        societe = self.env.company
        self.autre_societe = self.env["res.company"].create({"name": "Autre cabinet"})
        self.collegue = new_test_user(
            self.env, "progression_collegue", groups="project.group_project_user",
            company_id=societe.id, company_ids=[(6, 0, [societe.id])])
        self.etranger = new_test_user(
            self.env, "progression_etranger", groups="project.group_project_user",
            company_id=self.autre_societe.id, company_ids=[(6, 0, [self.autre_societe.id])])
        self.chef = new_test_user(
            self.env, "progression_chef_regles", groups="project.group_project_manager",
            company_id=societe.id, company_ids=[(6, 0, [societe.id])])

    def _ne_voit_pas(self, usager):
        Depot = self.env["bf.progression.deposit"].with_user(usager)
        self.assertFalse(Depot.search([("id", "=", self.depot.id)]))
        with self.assertRaises(AccessError):
            self.depot.with_user(usager).read(["filename"])

    def test_projet_prive_d_une_autre_societe(self):
        self._ne_voit_pas(self.etranger)

    def test_projet_prive_non_suivi(self):
        self._ne_voit_pas(self.collegue)
        self.projet.message_subscribe(partner_ids=self.collegue.partner_id.ids)
        self.assertEqual(
            self.env["bf.progression.deposit"].with_user(self.collegue).search(
                [("id", "=", self.depot.id)]), self.depot)

    def test_projet_ouvert_d_une_autre_societe(self):
        self.projet.privacy_visibility = "employees"
        self._ne_voit_pas(self.etranger)

    def test_l_administrateur_de_projets_voit_tout_dans_sa_societe(self):
        self.assertEqual(
            self.env["bf.progression.deposit"].with_user(self.chef).search(
                [("id", "=", self.depot.id)]), self.depot)
        self.assertEqual(self.depot.with_user(self.chef).filename, "identite.pdf")
