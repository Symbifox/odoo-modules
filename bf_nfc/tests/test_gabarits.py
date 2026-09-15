"""Les gabarits : chacun se pose tel qu'il est livré, dans le rôle qui s'en sert.

⚠️ Joué au post-install : dans une passe qui installe les satellites, cet essai voit
TOUS les gabarits livrés, tournées, équipements, salles et billets compris. Un
gabarit qui ne se pose pas avec son propre exemple est un gabarit cassé.
"""
import logging

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


_logger = logging.getLogger(__name__)


@tagged("post_install", "-at_install")
class TestGabarits(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groupes = ["base.group_user", "bf_nfc.group_nfc_manager"]
        # Poser un gabarit crée aussi les fiches qu'il porte (un billet vise un contact,
        # une salle réserve dans l'agenda) : la gestion doit pouvoir les créer.
        for xmlid in ("base.group_partner_manager", "project.group_project_manager"):
            if cls.env.ref(xmlid, raise_if_not_found=False):
                groupes.append(xmlid)
        cls.gestion = new_test_user(cls.env, login="gabarits-gestion", groups=",".join(groupes))
        cls.interne = new_test_user(cls.env, login="gabarits-interne", groups="base.group_user")
        cls.immeuble = cls.env["res.partner"].create({"name": "Immeuble du 12", "is_company": True})

    def _poser(self, gabarit, utilisateur=None, **valeurs):
        pose = self.env["bf.nfc.template.apply"].with_user(utilisateur or self.gestion).with_context(
            default_template_id=gabarit.id).create(valeurs)
        if pose.cible_requise and "cible" not in valeurs:
            pose.cible = self.immeuble
        if pose.demande_adresse and "adresse" not in valeurs:
            pose.adresse = "https://symbifox.com/procedure"
        action = pose.action_appliquer()
        return self.env["bf.nfc.tag"].search(action["domain"])

    def test_chaque_gabarit_livre_se_pose_avec_son_exemple(self):
        gabarits = self.env["bf.nfc.template"].search([])
        self.assertTrue(gabarits)
        # ⚠️ Journalisé pour qu'une passe puisse prouver ce qu'elle a parcouru : un essai
        # en boucle qui ne voit qu'un gabarit est vert pour de mauvaises raisons.
        _logger.info("Gabarits posés par l'essai : %s (%s)", len(gabarits),
                     ", ".join(gabarits.mapped("name")))
        for gabarit in gabarits:
            with self.subTest(gabarit=gabarit.name):
                lignes = [l for l in (gabarit.exemple or "").splitlines() if l.strip()]
                self.assertTrue(lignes, "« %s » n'a pas d'exemple" % gabarit.name)
                pastilles = self._poser(gabarit)
                self.assertEqual(len(pastilles), len(lignes))
                for tag in pastilles:
                    self.assertIsNone(tag._refus_eventuel("app"),
                                      "« %s » pose une pastille inutilisable" % gabarit.name)

    def test_la_recette_de_base_pose_l_adresse_sur_la_pastille(self):
        pastilles = self._poser(self.env.ref("bf_nfc.gabarit_procedure"),
                                lignes="Évacuation ; escalier", adresse="https://exemple.org/evacuation")
        self.assertEqual(pastilles._params(), {"url": "https://exemple.org/evacuation"})
        self.assertEqual(pastilles.place, "escalier")

    def test_les_lignes_vides_et_les_commentaires_sont_sautes(self):
        pastilles = self._poser(self.env.ref("bf_nfc.gabarit_procedure"),
                                lignes="\n# à revoir\nUne ; ici\n\nDeux\n")
        self.assertEqual(sorted(pastilles.mapped("name")), ["Deux", "Une"])

    def test_sans_ligne_ou_sans_adresse_rien_n_est_cree(self):
        with self.assertRaises(UserError):
            self._poser(self.env.ref("bf_nfc.gabarit_procedure"), lignes="# rien", adresse="https://x.org")
        with self.assertRaises(UserError):
            self._poser(self.env.ref("bf_nfc.gabarit_procedure"), lignes="Une", adresse="javascript:alert(1)")

    def test_un_interne_ne_pose_pas_de_gabarit(self):
        with self.assertRaises(AccessError):
            self._poser(self.env.ref("bf_nfc.gabarit_procedure"), utilisateur=self.interne, lignes="Une")
