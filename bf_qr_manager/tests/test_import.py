import base64

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from .commun import monter


@tagged("post_install", "-at_install")
class TestImport(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        monter(cls)
        cls.autre = cls.env["res.partner"].create({"name": "Salle C-301 (essai)"})

    def _import(self, csv_texte, user=None, **valeurs):
        valeurs.update({"batch_id": self.lot.id, "fichier_nom": "associations.csv",
                        "fichier": base64.b64encode(csv_texte.encode())})
        assistant = self.env["bf.qr.import"].with_user(user or self.concierge).create(valeurs)
        assistant.action_analyser()
        return assistant

    def test_numero_plage_reference_et_code(self):
        t = self.etiquettes
        csv = ("Étiquette;Type;Fiche;Adresse;Libellé;Posée sur;Public\n"
               "1;Contact;%s;;;Porte;\n"
               "2-4;res.partner;Salle C-301 (essai);;;;\n"
               "TST-0005;;;https://symbifox.com;Site;;oui\n"
               "%s;;;https://symbifox.com/guide;;;\n") % (self.partenaire.id, t[5].code)
        assistant = self._import(csv)
        self.assertEqual(assistant.count_erreur, 0, assistant.ligne_ids.mapped("message"))
        self.assertEqual(assistant.count_etiquettes, 6)
        # Rien n'est écrit avant d'appliquer.
        self.assertTrue(all(t.mapped("qr_vierge")))
        assistant.action_appliquer()
        self.assertEqual(t[0].res_id, self.partenaire.id)
        self.assertEqual(t[0].place, "Porte")
        self.assertEqual(set(t[1:4].mapped("res_id")), {self.autre.id})
        self.assertEqual(t[4].name, "Site")
        self.assertTrue(t[4].qr_public)
        self.assertEqual(t[5]._params()["url"], "https://symbifox.com/guide")
        self.assertTrue(all(t[6:].mapped("qr_vierge")))

    def test_une_erreur_bloque_tout(self):
        csv = ("etiquette,type,fiche,adresse\n"
               "1,res.partner,%s,\n"
               "2,Devise,1,\n"
               "3,res.partner,Personne qui n'existe pas,\n"
               "4,res.partner,%s,https://symbifox.com\n"
               "1,res.partner,%s,\n"
               "999,res.partner,%s,\n"
               "5,,,javascript:alert(1)\n") % ((self.partenaire.id,) * 4)
        assistant = self._import(csv)
        self.assertEqual(assistant.count_ok, 1)
        self.assertEqual(assistant.count_erreur, 6)
        with self.assertRaises(UserError):
            assistant.action_appliquer()
        self.assertTrue(all(self.etiquettes.mapped("qr_vierge")))
        # Les lignes prêtes passent seulement si on le demande.
        assistant.ignorer_erreurs = True
        assistant.action_appliquer()
        self.assertFalse(self.etiquettes[0].qr_vierge)
        self.assertTrue(all(self.etiquettes[1:].mapped("qr_vierge")))

    def test_l_essai_a_blanc_ne_laisse_aucune_trace(self):
        avant = len(self.etiquettes[0].message_ids)
        self._import("etiquette,type,fiche\n1,res.partner,%s\n" % self.partenaire.id)
        self.etiquettes.invalidate_recordset()
        self.assertTrue(self.etiquettes[0].qr_vierge)
        self.assertEqual(len(self.etiquettes[0].message_ids), avant)

    def test_deja_associee_refusee(self):
        self.etiquettes[0].with_user(self.gestion)._associer(self.geste_open, cible=self.partenaire)
        assistant = self._import("etiquette,type,fiche\n1,res.partner,%s\n" % self.autre.id)
        self.assertIn("déjà associée", assistant.ligne_ids.message)

    def test_l_interne_ne_peut_pas(self):
        assistant = self._import("etiquette,type,fiche\n1,res.partner,%s\n" % self.partenaire.id,
                                 user=self.interne)
        self.assertEqual(assistant.count_ok, 0)

    def test_nom_ambigu_refuse(self):
        self.env["res.partner"].create({"name": "Salle C-301 (essai)"})
        assistant = self._import("etiquette,type,fiche\n1,res.partner,Salle C-301 (essai)\n")
        self.assertIn("plusieurs fiches", assistant.ligne_ids.message)

    def test_xlsx(self):
        import io
        import openpyxl
        classeur = openpyxl.Workbook()
        classeur.active.append(["etiquette", "type", "fiche"])
        classeur.active.append([7, "res.partner", self.partenaire.id])
        tampon = io.BytesIO()
        classeur.save(tampon)
        assistant = self.env["bf.qr.import"].with_user(self.concierge).create({
            "batch_id": self.lot.id, "fichier_nom": "a.xlsx",
            "fichier": base64.b64encode(tampon.getvalue())})
        assistant.action_analyser()
        self.assertEqual(assistant.count_ok, 1, assistant.ligne_ids.mapped("message"))
