"""Les écrans, chargés sous un compte RÉEL de chaque rôle.

Jamais `uid 1` : le superutilisateur n'a aucun groupe et traverse tout, donc
il ne voit pas un champ absent de l'ACL ni un bouton réservé. Une vue se casse
en production pour la personne qui l'ouvre, pas pour l'administrateur qui l'a
écrite.
"""

from odoo.tests.common import tagged

from .common import PulseCase

ECRANS = [
    ("bf.ex.pulse.campaign", "list"),
    ("bf.ex.pulse.campaign", "form"),
    ("bf.ex.pulse.score", "list"),
    ("bf.ex.pulse.score", "form"),
    ("bf.ex.pulse.score", "graph"),
    ("bf.ex.pulse.score", "search"),
    ("bf.ex.pulse.metric", "list"),
    ("bf.ex.pulse.metric", "form"),
    ("bf.ex.pulse.question", "list"),
    ("bf.ex.pulse.question", "form"),
]


@tagged("post_install", "-at_install")
class TestPulseVues(PulseCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        commun = {
            "company_ids": [(6, 0, [cls.company.id])],
            "company_id": cls.company.id,
        }
        cls.utilisateur_rh = cls.env["res.users"].create(dict(
            commun, name="Adjointe RH", login="pulse-vue-rh",
            groups_id=[(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("hr.group_hr_user").id,
            ])],
        ))
        cls.utilisateur_manager = cls.env["res.users"].create(dict(
            commun, name="Direction RH", login="pulse-vue-manager",
            groups_id=[(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("hr.group_hr_manager").id,
            ])],
        ))
        cls.utilisateur_simple = cls.env["res.users"].create(dict(
            commun, name="Employé sans rôle RH", login="pulse-vue-simple",
            groups_id=[(6, 0, [cls.env.ref("base.group_user").id])],
        ))

    def test_les_ecrans_se_chargent_pour_l_administration(self):
        for modele, mode in ECRANS:
            with self.subTest(modele=modele, mode=mode):
                self.env[modele].with_user(
                    self.utilisateur_rh).get_view(view_type=mode)

    def test_les_ecrans_se_chargent_pour_la_direction(self):
        for modele, mode in ECRANS:
            with self.subTest(modele=modele, mode=mode):
                self.env[modele].with_user(
                    self.utilisateur_manager).get_view(view_type=mode)

    def test_le_formulaire_de_vague_se_charge_avec_ses_donnees(self):
        """Un `get_view` vert ne prouve pas qu'on peut lire les lignes."""
        campaign = self._campaign()
        campaign.action_open()
        for invitation in campaign.invitation_ids[:5]:
            self._repondre(invitation, texte="Un mot.")
        campaign.action_close()
        vue = campaign.with_user(self.utilisateur_rh)
        vue.read([
            "name", "state", "invitation_count", "response_count",
            "response_rate", "pending_flush_count",
        ])
        vue.invitation_ids.read(["employee_id", "segment_key", "used"])
        scores = self.env["bf.ex.pulse.score"].with_user(
            self.utilisateur_rh).search([("campaign_id", "=", campaign.id)])
        self.assertTrue(scores)
        scores.read([
            "display_score", "respondent_count", "is_displayable",
            "verbatim_count", "verbatims_displayable", "verbatim_html",
        ])

    def test_le_personnel_sans_role_rh_ne_voit_rien_du_pulse(self):
        """La conception écarte aussi le gestionnaire direct."""
        from odoo.exceptions import AccessError
        for modele in ("bf.ex.pulse.campaign", "bf.ex.pulse.score",
                       "bf.ex.pulse.invitation", "bf.ex.pulse.answer",
                       "bf.ex.pulse.staging"):
            with self.subTest(modele=modele):
                with self.assertRaises(AccessError):
                    self.env[modele].with_user(
                        self.utilisateur_simple).search([])

    def test_l_administration_ne_peut_pas_retourner_le_drapeau(self):
        """Sinon on efface une réponse en déclarant que la personne n'a pas
        répondu, puis on lui renvoie un lien neuf."""
        from odoo.exceptions import AccessError
        campaign = self._campaign()
        campaign.action_open()
        invitation = campaign.invitation_ids[0]
        self._repondre(invitation)
        with self.assertRaises(AccessError):
            invitation.with_user(self.utilisateur_rh).write({"used": False})
        with self.assertRaises(AccessError):
            invitation.with_user(
                self.utilisateur_manager).write({"used": False})

    def test_le_menu_pulse_ne_s_affiche_pas_pour_qui_ne_peut_rien_en_faire(self):
        """Un menu qui mène à un refus est un défaut d'écran, pas de droits.

        Les menus du pulse ne portent aucun groupe, et la racine de la famille
        non plus : ils comptent sur le filtrage d'Odoo, qui retire un menu dont
        l'action vise un modèle que la personne ne peut pas lire. Cet essai
        vérifie que ce filtrage joue vraiment, au lieu de le supposer.
        """
        menus_rh = self.env["ir.ui.menu"].with_user(
            self.utilisateur_rh).search([("name", "in", ("Pulse", "Vagues", "Scores"))])
        self.assertTrue(
            menus_rh, "l'administration RH devrait voir le menu Pulse")

        visibles = self.env["ir.ui.menu"].with_user(
            self.utilisateur_simple)._visible_menu_ids()
        cibles = self.env["ir.ui.menu"].sudo().search([
            ("name", "in", ("Pulse", "Vagues", "Scores")),
        ])
        fuites = [m.complete_name for m in cibles if m.id in visibles]
        self.assertFalse(
            fuites,
            "un employé sans rôle RH voit un menu qui le mènera à un refus : %s"
            % fuites,
        )
