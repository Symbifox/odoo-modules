# -*- coding: utf-8 -*-
"""Les tuiles de l'accueil ont un seul devoir : ne jamais casser la page.

Ces tests suivaient le module ``bf_dashboard``, absorbé par ``bf_home`` le
2026-08-30. Ils restent groupés ici plutôt que fondus dans
``test_home`` : ce qu'ils gardent est le contrat d'extension que cinq modules
satellites utilisent, et il se lit mieux au même endroit que ce qu'il protège.

Ces tests n'affirment pas ce que valent les chiffres — cela dépend des modules
que porte le locataire et de ce qui s'est passé hier. Ils affirment le contrat
sur lequel l'action client s'appuie : l'appel aboutit, la forme est stable, un
module absent laisse la tuile de côté au lieu de mentir, et un collecteur cassé
ne peut pas emporter les autres avec lui.
"""

from unittest.mock import patch

from odoo.addons.bf_home.models.bf_dashboard import REQUIREMENTS
from odoo.tests import TransactionCase, tagged

SECTIONS = (
    "revenue", "hosting", "devops", "knowledge", "privacy", "reconciliation",
    "invoices_to_validate", "bills_to_pay", "overdue_tasks",
    "overdue_activities",
)


@tagged("post_install", "-at_install")
class TestBfDashboard(TransactionCase):

    def test_no_collector_is_dormant_by_typo(self):
        """Une tuile muette doit dire « module absent », jamais « champ mal écrit ».

        Les deux sont indiscernables à l'exécution : c'est ainsi que quatre
        collecteurs de bf_home ont vécu silencieux, chacun demandant un champ
        qui n'existait pas. La garde répondait Faux, la tuile disparaissait, et
        rien nulle part ne le disait.
        """
        wrong = []
        for name, (model, needed) in sorted(REQUIREMENTS.items()):
            Model = self.env.get(model)
            if Model is None:
                continue          # le locataire ne le porte pas : silence légitime
            missing = [f for f in needed if f not in Model._fields]
            if missing:
                wrong.append("%s attend %s.%s" % (name, model, ", ".join(missing)))
        self.assertFalse(wrong, "collecteurs muets pour cause de champ inexistant :\n  "
                                + "\n  ".join(wrong))

    def test_declared_fields_are_searchable(self):
        """Déclarer un champ non interrogeable est le même échec, une couche plus bas.

        _has() répond Vrai pour un champ calculé, puis le domaine lève dans
        _safe() et la tuile disparaît exactement comme si le module était
        absent. « Non stocké » n'est pas le critère : un champ calculé doté
        d'une méthode de recherche va très bien dans un domaine. Ce qui ne doit
        jamais apparaître ici, c'est un champ qui n'est ni l'un ni l'autre.
        """
        unsearchable = []
        for name, (model, needed) in sorted(REQUIREMENTS.items()):
            Model = self.env.get(model)
            if Model is None:
                continue
            for f in needed:
                field = Model._fields.get(f)
                if field is not None and not field.store and not field.search:
                    unsearchable.append(
                        "%s déclare %s.%s : ni stocké, ni doté d'une méthode de recherche"
                        % (name, model, f))
        self.assertFalse(unsearchable, "\n  ".join(unsearchable))

    def test_payload_shape_is_stable(self):
        """Chaque section est présente, et vaut None ou un dictionnaire."""
        data = self.env["bf.dashboard"].get_dashboard_data()
        for key in SECTIONS:
            self.assertIn(key, data, "section absente de la charge utile : %s" % key)
            self.assertTrue(data[key] is None or isinstance(data[key], dict),
                            "%s devrait être None ou un dict, pas %r" % (key, data[key]))
        self.assertIsInstance(data["failed"], dict)
        self.assertFalse(data["failed"], "aucun collecteur ne devrait échouer ici")

    def test_inheritance_anchors_still_resolve(self):
        """Quatre modules étendent ce gabarit par xpath, et un xpath mort est MUET.

        bf_cx_dashboard s'accroche à une expression t-if littérale, et
        bf_subscription_dashboard à la classe du div de la carte Vie privée plus
        le t-on-click qu'il contient. Réécrire l'une ou l'autre ne lève rien :
        l'écran se contente de ne plus rendre la carte du module qui hérite. Ce
        test fige les deux ancres avec le gabarit.
        """
        from lxml import etree
        from odoo.modules.module import get_module_path

        path = "%s/static/src/xml/bf_dashboard.xml" % get_module_path("bf_home")
        tree = etree.parse(path)
        for owner, expr in (
            ("bf_cx_dashboard + bf_employee_experience_dashboard",
             "//t[@t-if='state.data.overdue_activities']"),
            # Deux modules partagent cette ancre, au caractère près. Une
            # seconde entrée identique n'ajouterait aucune protection, juste un
            # littéral de plus à garder synchronisé — ce qui est exactement le
            # contraire du service rendu ici. Ce qu'il faut tenir à jour, c'est
            # la liste des dépendants.
            ("bf_subscription_dashboard + bf_hour_bank_dashboard",
             "//div[@class='col-lg-4 mb-3'][.//*[@t-on-click='openPrivacyPending']]"),
        ):
            self.assertEqual(
                len(tree.xpath(expr)), 1,
                "l'ancre xpath de %s ne résout plus : %s" % (owner, expr))

    def test_absent_module_yields_no_tile_not_an_error(self):
        """Absent et cassé ne doivent pas se ressembler.

        Les deux rendent None — aucune tuile n'affiche de valeur inventée — mais
        seul l'échec est nommé dans ``failed``, et c'est ce que le gabarit lit
        pour décider entre « pas de tuile du tout » et « Données non
        disponibles ». Nommer un module simplement absent afficherait une panne
        sur un locataire où tout va bien.
        """
        Dash = self.env["bf.dashboard"]
        with patch.object(type(Dash), "_has", lambda self, model, *f: False):
            data = Dash.get_dashboard_data()
        for key in ("hosting", "knowledge", "privacy"):
            self.assertIsNone(data[key],
                              "%s devrait être None quand le module est absent" % key)
            self.assertNotIn(key, data["failed"],
                             "%s est absent, pas en panne" % key)

    def test_one_broken_collector_does_not_break_the_others(self):
        """Le test qui justifie le point de reprise dans _safe().

        Le collecteur mis en échec lève depuis l'intérieur d'une requête, ce qui
        laisse la transaction avortée. Sans le point de reprise, tous les
        collecteurs suivants échouent à leur tour — et la garde ne garde rien.
        « reconciliation » est interrogé APRÈS « privacy » et lit en SQL brut :
        c'est exactement la victime qu'on veut voir survivre.
        """
        Dash = self.env["bf.dashboard"]

        def boom(self):
            self.env.cr.execute("SELECT 1 FROM table_qui_nexiste_pas")

        with patch.object(type(Dash), "_get_privacy_summary", boom):
            data = Dash.get_dashboard_data()

        self.assertIsNone(data["privacy"])
        self.assertTrue(data["failed"].get("privacy"),
                        "le collecteur en échec doit être nommé dans failed")
        self.assertIsInstance(data["reconciliation"], dict,
                              "le collecteur suivant a été emporté : le point de "
                              "reprise de _safe() ne joue pas son rôle")
        self.assertIn("accounts", data["reconciliation"])
        self.assertEqual(list(data["failed"]), ["privacy"],
                         "un seul collecteur devait échouer")
        self.assertIsInstance(data["overdue_tasks"], dict)
        self.assertIn("count", data["overdue_tasks"])

    def test_the_screen_has_a_way_in(self):
        """Un tableau de bord qu'aucun menu n'ouvre est du calcul perdu.

        Jusqu'à la 18.0.1.2.0 ce module ne déclarait aucun menuitem : l'écran
        n'était atteignable que si son post_init_hook avait fait de son action
        l'accueil de l'usager. Sur les deux locataires qui portent les deux
        modules, l'accueil est celui de bf_home — donc quatre modules
        calculaient des cartes dans un écran que personne n'ouvrait. Rien à
        l'exécution ne le signalait : la charge utile était juste, le gabarit
        rendait, il manquait seulement la porte.
        """
        # ⚠️ Le test visait `bf_dashboard.bf_dashboard_action`, l'action du
        # module absorbé ici le 2026-08-30. Sur la production, l'identifiant
        # résout encore parce que l'ancien module y a été installé et que ses
        # lignes `ir.model.data` ont survécu à l'absorption. Sur une
        # installation NEUVE de `bf_home` seul, il n'existe pas : KeyError, un
        # reste de fusion qui ne se voyait nulle part. C'est l'action de
        # `bf_home` qui ouvre l'écran depuis.
        action = self.env.ref("bf_home.bf_home_action")
        menus = self.env["ir.ui.menu"].search([
            ("action", "=", "%s,%s" % (action.type, action.id)),
        ])
        self.assertTrue(menus, "aucun menu n'ouvre bf_home.bf_home_action")

    def test_no_post_init_hook_rewrites_personal_settings(self):
        """Le module ne doit plus toucher au réglage d'accueil de personne.

        Le hook retiré en 18.0.1.2.0 écrivait action_id sur TOUS les usagers
        internes à chaque installation, y compris ceux qui avaient déjà choisi
        un autre écran d'accueil.
        Un défaut se pose par ir.default, il ne s'impose pas par write().
        """
        import ast
        from odoo.modules.module import get_module_path

        path = "%s/__manifest__.py" % get_module_path("bf_home")
        manifest = ast.literal_eval(open(path, encoding="utf-8").read())
        self.assertNotIn("post_init_hook", manifest)
        self.assertNotIn("pre_init_hook", manifest)

    def test_diagnose_answers_for_every_requirement(self):
        """_diagnose() doit nommer chaque collecteur gardé, muet ou non."""
        out = self.env["bf.dashboard"]._diagnose()
        self.assertEqual(sorted(r[0] for r in out), sorted(REQUIREMENTS))
        for name, model, verdict in out:
            self.assertTrue(verdict, "%s (%s) sans verdict" % (name, model))


@tagged("post_install", "-at_install")
class TestTuileDevops(TransactionCase):
    """La tuile « Mises à jour et sécurité » — celle qui doit refuser de rassurer.

    Le contrat particulier de cette tuile-ci : elle affiche des zéros, et un
    zéro de sécurité ne veut rien dire tant qu'on ne sait pas quand quelqu'un
    a regardé pour la dernière fois.
    """

    def test_la_tuile_dit_quand_rien_n_a_ete_rapproche(self):
        if self.env.get("bf.devops.advisory") is None:
            self.skipTest("bf_devops absent sur cette base")
        self.env["bf.devops.advisory"].search([]).unlink()
        données = self.env["bf.dashboard"].get_dashboard_data()
        self.assertIsNotNone(données["devops"])
        self.assertEqual(données["devops"]["advisories_to_fix"], 0)
        self.assertFalse(
            données["devops"]["last_reconciliation"],
            "sans rapprochement, la date doit être fausse : c'est elle qui "
            "empêche de lire le zéro au-dessus comme une bonne nouvelle")

    def test_la_tuile_compte_comme_la_liste_qu_elle_ouvre(self):
        Avis = self.env.get("bf.devops.advisory")
        if Avis is None:
            self.skipTest("bf_devops absent sur cette base")
        Avis.search([]).unlink()
        from odoo import fields
        for i, gravite in enumerate(("critical", "high", "medium")):
            Avis.create({
                "name": f"Tuile {i}", "reference": f"GHSA-tuile-{i}",
                "component": f"paquet{i}", "component_label": f"paquet{i}",
                "source": "github", "severity": gravite,
                "parc_state": "concerne", "parc_count": 1,
                "parc_date": fields.Datetime.now(),
            })
        données = self.env["bf.dashboard"].get_dashboard_data()["devops"]
        action = self.env["bf.dashboard"].action_open_devops_advisories()
        self.assertEqual(
            données["advisories_to_fix"],
            Avis.search_count(action["domain"]),
            "la tuile ne compte pas comme la liste qu'elle ouvre")
        self.assertEqual(données["advisories_severe"], 2)

    def test_les_mises_a_jour_ne_filtrent_pas_sur_l_etat(self):
        """⚠️ Une fiche `draft` ou `cancelled` peut porter un conteneur vivant
        et routé. Filtrer sur `state` a déjà rendu de la vraie production
        invisible pendant des semaines."""
        Service = self.env.get("hosting.service")
        if Service is None:
            self.skipTest("hosting_management absent sur cette base")
        action = self.env["bf.dashboard"].action_view_pending_updates()
        champs = {terme[0] for terme in action["domain"] if isinstance(terme, tuple)}
        self.assertNotIn(
            "state", champs,
            "le domaine ne doit pas filtrer sur l'état de la fiche")
        données = self.env["bf.dashboard"].get_dashboard_data()["devops"]
        self.assertEqual(données["updates_pending"],
                         Service.sudo().search_count(action["domain"]))
