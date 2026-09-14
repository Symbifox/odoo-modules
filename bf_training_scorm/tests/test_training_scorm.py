import base64
import io
import zipfile

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

MANIFESTE_12 = b"""<?xml version="1.0"?>
<manifest identifier="ESSAI-12" xmlns="http://www.imsproject.org/xsd/imscp_rootv1p1p2"
          xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_rootv1p2">
  <metadata><schema>ADL SCORM</schema><schemaversion>1.2</schemaversion></metadata>
  <organizations default="O1"><organization identifier="O1"><title>Essai</title>
    <item identifier="I1" identifierref="R1"><title>Module</title></item>
  </organization></organizations>
  <resources>
    <resource identifier="R1" type="webcontent" adlcp:scormtype="sco" href="index.html">
      <file href="index.html"/>
    </resource>
  </resources>
</manifest>"""

MANIFESTE_2004 = b"""<?xml version="1.0"?>
<manifest identifier="ESSAI-2004" xmlns="http://www.imsglobal.org/xsd/imscp_v1p1"
          xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_v1p3"
          xmlns:imsss="http://www.imsglobal.org/xsd/imsss">
  <metadata><schema>ADL SCORM</schema><schemaversion>2004 4th Edition</schemaversion></metadata>
  <organizations default="O1"><organization identifier="O1"><title>Essai</title>
    <item identifier="I1" identifierref="R1"><title>Module</title>
      <imsss:sequencing><imsss:controlMode choice="true"/></imsss:sequencing>
    </item>
  </organization></organizations>
  <resources>
    <resource identifier="R1" type="webcontent" adlcp:scormType="sco" href="demarrer.html">
      <file href="demarrer.html"/>
    </resource>
  </resources>
</manifest>"""


def _zip(fichiers):
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as zf:
        for nom, contenu in fichiers.items():
            zf.writestr(nom, contenu)
    return base64.b64encode(tampon.getvalue())


@tagged("post_install", "-at_install", "bf_training_scorm")
class TestTrainingScorm(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partenaire = cls.env["res.partner"].create({"name": "Apprenant SCORM"})

    def _paquet(self, manifeste=MANIFESTE_12, extra=None, **kw):
        fichiers = {"imsmanifest.xml": manifeste, "index.html": b"<html>ok</html>"}
        fichiers.update(extra or {})
        valeurs = {"name": "Paquet d'essai", "archive": _zip(fichiers)}
        valeurs.update(kw)
        return self.env["bf.scorm.package"].create(valeurs)

    # ------------------------------------------------------------------
    # La lecture du manifeste
    # ------------------------------------------------------------------
    def test_un_manifeste_1_2_est_reconnu(self):
        paquet = self._paquet()
        self.assertEqual(paquet.version, "1.2")
        self.assertEqual(paquet.launch_href, "index.html")
        self.assertEqual(paquet.identifier, "ESSAI-12")
        self.assertEqual(paquet.sco_count, 1)

    def test_un_manifeste_2004_est_reconnu_malgre_son_libelle(self):
        """« 2004 4th Edition » n'est pas « 2004 ».

        🔴 Le `schemaversion` n'est pas normalisé au caractère près : on a relevé
        « 1.2 », « CAM 1.3 », « 2004 3rd Edition », « 2004 4th Edition ». Une
        comparaison par ÉGALITÉ classerait les trois derniers en SCORM 1.2, et
        le module lirait `cmi.core.lesson_status` sur un contenu qui écrit
        `cmi.completion_status` : la complétion ne remonterait jamais.
        """
        paquet = self._paquet(manifeste=MANIFESTE_2004,
                              extra={"demarrer.html": b"<html>ok</html>"})
        self.assertEqual(paquet.version, "2004")
        self.assertEqual(paquet.launch_href, "demarrer.html")

    def test_le_sequencement_declare_est_signale(self):
        paquet = self._paquet(manifeste=MANIFESTE_2004,
                              extra={"demarrer.html": b"<html>ok</html>"})
        self.assertTrue(
            paquet.uses_sequencing,
            "un paquet qui déclare du séquencement doit le dire, puisqu'on ne "
            "l'applique pas")

    def test_une_archive_sans_manifeste_est_refusee_au_depot(self):
        """Refusée au dépôt, pas cassée au premier clic d'un apprenant."""
        with self.assertRaises(UserError):
            self.env["bf.scorm.package"].create({
                "name": "Sans manifeste",
                "archive": _zip({"index.html": b"<html/>"}),
            })

    def test_un_fichier_qui_n_est_pas_un_zip_est_refuse(self):
        with self.assertRaises(UserError):
            self.env["bf.scorm.package"].create({
                "name": "Pas un zip",
                "archive": base64.b64encode(b"ceci n'est pas une archive"),
            })

    def test_un_manifeste_illisible_est_refuse(self):
        with self.assertRaises(UserError):
            self._paquet(manifeste=b"<manifest><pas-ferme>")

    def test_un_manifeste_sans_href_est_refuse(self):
        sans = b"""<?xml version="1.0"?>
        <manifest identifier="X"><resources>
        <resource identifier="R1" type="webcontent"/></resources></manifest>"""
        with self.assertRaises(UserError):
            self._paquet(manifeste=sans)

    # ------------------------------------------------------------------
    # 🔴 La garde de chemin
    # ------------------------------------------------------------------
    def test_un_zip_slip_est_refuse_meme_quand_l_entree_EXISTE(self):
        """🔴 L'essai qui prouve vraiment la garde, et que je n'avais pas.

        Une archive peut CONTENIR une entrée nommée « ../secret.txt ». C'est
        l'attaque « zip slip », et c'est le seul cas où la garde change quelque
        chose : sur une archive ordinaire, un chemin qui sort rend `None` de
        toute façon parce que `zf.read` lève `KeyError`. Mon premier essai ne
        distinguait donc pas la garde de son absence — une mutation qui
        supprimait tout le contrôle passait au vert.
        """
        paquet = self._paquet(extra={
            "../secret.txt": b"vole",
            "a/../../secret2.txt": b"vole aussi",
        })
        for chemin in ("../secret.txt", "a/../../secret2.txt"):
            self.assertIsNone(
                paquet._lire_fichier(chemin),
                f"« {chemin} » est DANS l'archive et doit rester refusé")

    def test_un_chemin_qui_sort_du_paquet_est_refuse(self):
        """Sous toutes ses formes, pas seulement la plus évidente."""
        paquet = self._paquet()
        for chemin in (
            "../etc/passwd",
            "a/../../etc/passwd",
            "./../../etc/passwd",
            "/etc/passwd",
            "..\\..\\windows\\system32",
            "a/./../../..",
        ):
            self.assertIsNone(
                paquet._lire_fichier(chemin),
                f"« {chemin} » doit être refusé")

    def test_un_fichier_du_paquet_se_lit(self):
        paquet = self._paquet()
        self.assertEqual(paquet._lire_fichier("index.html"), b"<html>ok</html>")

    def test_un_fichier_absent_rend_none_sans_lever(self):
        paquet = self._paquet()
        self.assertIsNone(paquet._lire_fichier("absent.html"))

    # ------------------------------------------------------------------
    # Le modèle de données CMI
    # ------------------------------------------------------------------
    def _tentative(self, paquet):
        return self.env["bf.scorm.attempt"]._pour(paquet, self.partenaire)

    def test_une_tentative_incomplete_ne_termine_rien(self):
        """« incomplete » n'est pas un échec, et pas une complétion non plus."""
        tentative = self._tentative(self._paquet())
        tentative._ecrire({"cmi.core.lesson_status": "incomplete"})
        self.assertFalse(tentative.completed)
        self.assertFalse(tentative.date_completed)

    def test_completed_en_1_2_termine(self):
        tentative = self._tentative(self._paquet())
        tentative._ecrire({"cmi.core.lesson_status": "completed",
                           "cmi.core.score.raw": "88"})
        self.assertTrue(tentative.completed)
        self.assertTrue(tentative.date_completed)
        self.assertEqual(tentative.score_raw, 88.0)

    def test_en_2004_un_contenu_termine_mais_echoue_ne_compte_pas(self):
        """🔴 2004 a DEUX axes, et ils ne disent pas la même chose.

        `completion_status` dit si le contenu a été parcouru, `success_status`
        s'il a été réussi. Un contenu peut être « completed » ET « failed ».
        Ne lire que la complétion ferait passer un échec pour une formation
        suivie.
        """
        paquet = self._paquet(manifeste=MANIFESTE_2004,
                              extra={"demarrer.html": b"<html/>"})
        tentative = self._tentative(paquet)
        tentative._ecrire({"cmi.completion_status": "completed",
                           "cmi.success_status": "failed"})
        self.assertEqual(tentative.lesson_status, "failed")
        self.assertFalse(tentative.completed)

    def test_en_2004_passed_termine(self):
        paquet = self._paquet(manifeste=MANIFESTE_2004,
                              extra={"demarrer.html": b"<html/>"})
        tentative = self._tentative(paquet)
        tentative._ecrire({"cmi.completion_status": "completed",
                           "cmi.success_status": "passed"})
        self.assertTrue(tentative.completed)

    def test_en_2004_la_completion_seule_suffit_quand_la_reussite_est_muette(self):
        """Un contenu sans notion de réussite ne doit pas rester éternellement ouvert."""
        paquet = self._paquet(manifeste=MANIFESTE_2004,
                              extra={"demarrer.html": b"<html/>"})
        tentative = self._tentative(paquet)
        tentative._ecrire({"cmi.completion_status": "completed"})
        self.assertTrue(tentative.completed)

    def test_ecrire_fusionne_et_ne_perd_pas_suspend_data(self):
        """Un contenu écrit ses éléments un par un."""
        tentative = self._tentative(self._paquet())
        tentative._ecrire({"cmi.suspend_data": "page=4"})
        tentative._ecrire({"cmi.core.lesson_status": "incomplete"})
        self.assertEqual(tentative._lire("cmi.suspend_data"), "page=4",
                         "une écriture suivante ne doit pas effacer la reprise")

    def test_une_seule_tentative_par_apprenant(self):
        paquet = self._paquet()
        premiere = self._tentative(paquet)
        seconde = self._tentative(paquet)
        self.assertEqual(premiere, seconde)

    def test_une_date_de_completion_ne_se_reecrit_pas(self):
        """Elle est posée QUAND l'événement arrive, et pas rejouée après.

        ⚠️ La date est RECULÉE à une valeur connue avant la seconde écriture.
        Sans ça, `Datetime.now()` rend la même valeur dans la même seconde et
        l'essai passe que la garde existe ou non : une mutation qui réécrivait
        la date à chaque appel s'est échappée exactement comme ça. Un essai sur
        une date doit faire bouger la date, pas espérer qu'elle bouge.
        """
        from odoo import fields as odoo_fields
        tentative = self._tentative(self._paquet())
        tentative._ecrire({"cmi.core.lesson_status": "completed"})
        self.assertTrue(tentative.date_completed)

        veille = odoo_fields.Datetime.to_datetime("2026-01-02 03:04:05")
        tentative.write({"date_completed": veille})

        tentative._ecrire({"cmi.core.score.raw": "99"})
        self.assertEqual(
            tentative.date_completed, veille,
            "une date posée à l'événement ne se rejoue pas aux écritures suivantes")

    # ------------------------------------------------------------------
    # Le contenu du cours
    # ------------------------------------------------------------------
    def test_un_contenu_scorm_sans_paquet_est_refuse(self):
        canal = self.env["slide.channel"].create({"name": "Cours d'essai"})
        with self.assertRaises(ValidationError):
            self.env["slide.slide"].create({
                "name": "Leçon vide",
                "channel_id": canal.id,
                "slide_category": "scorm",
            })

    def test_la_duree_d_un_scorm_n_est_pas_inventee(self):
        """Le natif estime la durée d'un document par son nombre de pages.

        Un paquet SCORM n'a pas de page, et une durée inventée entrerait au
        registre comme des heures de formation.
        """
        canal = self.env["slide.channel"].create({"name": "Cours d'essai"})
        paquet = self._paquet()
        diapo = self.env["slide.slide"].create({
            "name": "Leçon SCORM",
            "channel_id": canal.id,
            "slide_category": "scorm",
            "scorm_package_id": paquet.id,
        })
        self.assertEqual(diapo._get_completion_time(), 0.0)

    def test_le_canal_porte_son_compteur_de_scorm(self):
        """🔴 Ajouter une catégorie de contenu EXIGE son compteur, et rien ne le dit.

        `_compute_slides_statistics` du natif construit ses clés depuis les
        valeurs de `slide_category` puis fait `channel[cle] = ...`. Une catégorie
        ajoutée par `selection_add` sans son `nbr_<categorie>` lève
        `KeyError: 'nbr_scorm'` au premier flush qui touche un canal — pas à
        l'installation, pas aux vues, mais bien plus tard, sur une écriture sans
        rapport apparent avec ce module.
        """
        canal = self.env["slide.channel"].create({"name": "Cours d'essai"})
        paquet = self._paquet()
        self.env["slide.slide"].create({
            "name": "Leçon SCORM",
            "channel_id": canal.id,
            "slide_category": "scorm",
            "scorm_package_id": paquet.id,
            "is_published": True,
        })
        canal.invalidate_recordset()
        self.assertEqual(
            canal.nbr_scorm, 1,
            "le canal doit compter ses paquets SCORM comme il compte le reste")
