# -*- coding: utf-8 -*-
"""La page de liens suit le nom d'usage et montre les pronoms.

Ce qui est éprouvé sort de la forme de `bf_linkpage` : le titre et l'adresse
d'une page sont SUIVIS au fil de la page (un changement y laisserait l'ancien
nom), le titre est figé à la création (la passe ne rafraîchit que les liens),
un slug inconnu rend un 404, et la page publique est rendue en sudo.
"""

from odoo.exceptions import ValidationError
from odoo.tests import Form, HttpCase, TransactionCase, tagged

from ..wizard.bf_preferred_name_rename import carries_full_name, propose_slug

LEGAL = "bf_preferred_name.group_legal_name"


def _usager(env, nom, login, *groupes):
    groups = [env.ref("base.group_user").id, env.ref("base.group_partner_manager").id]
    groups += [env.ref(g).id for g in groupes]
    return env["res.users"].create({
        "name": nom, "login": login, "email": login,
        "groups_id": [(6, 0, groups)],
    })


@tagged("post_install", "-at_install", "bf_preferred_name")
class TestPageDeLiens(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=False))
        cls.proprio = _usager(cls.env, "Alex Ancien", "alex.ancien@essai.test",
                              "bf_linkpage.group_bf_linkpage_user")
        cls.collegue = _usager(cls.env, "Camille Collègue", "camille.collegue@essai.test",
                               "bf_linkpage.group_bf_linkpage_user")
        cls.registraire = _usager(cls.env, "Rosalie Registre", "rosalie.registre@essai.test",
                                  "bf_linkpage.group_bf_linkpage_manager", LEGAL)
        # Un contact, pas la fiche d'un usager : renommer la fiche d'un AUTRE
        # usager est refusé à un interne (les modules voisins écrivent alors
        # `res.users`). La page est à l'usager par `user_id`, ce qui lui donne
        # le droit de la modifier.
        cls.personne = cls.env["res.partner"].create({
            "name": "Alex Ancien", "email": "alex.contact@essai.test"})
        cls.page = cls.env["bf.linkpage"].create({
            "name": "Alex Ancien",
            "slug": "alex-ancien",
            "kind": "owner",
            "partner_id": cls.personne.id,
            "user_id": cls.proprio.id,
            "state": "published",
        })
        # Un `create` neutralise le suivi jusqu'au prochain pré-commit.
        cls.env.flush_all()
        cls.env.cr.precommit.run()

    def _vider_suivi(self):
        self.env.flush_all()
        self.env.cr.precommit.run()

    def _traces(self, page, *noms):
        champs = self.env["ir.model.fields"].search([
            ("model", "=", "bf.linkpage"), ("name", "in", noms)])
        return self.env["mail.tracking.value"].sudo().search([
            ("field_id", "in", champs.ids),
            ("mail_message_id.model", "=", "bf.linkpage"),
            ("mail_message_id.res_id", "=", page.id),
        ])

    def _assistant(self, user, partner, **values):
        return self.env["bf.preferred.name.rename"].with_user(user).with_context(
            active_model="res.partner", active_id=partner.id,
        ).create(dict(values))

    # -- Le titre suit le nom ------------------------------------------------

    def test_le_titre_suit_le_nom_sans_trace(self):
        # Une écriture plus tôt dans la transaction arme le relevé du suivi de
        # la page : c'est lui qu'un simple `mail_notrack` laisserait passer.
        self.page.write({"advisor_user_id": self.collegue.id})
        # Par n'importe quel chemin : ici, l'écriture directe de la fiche.
        self.personne.with_user(self.collegue).write({"name": "Alex Nouveau"})
        self._vider_suivi()
        self.assertEqual(self.page.name, "Alex Nouveau")
        self.assertFalse(self._traces(self.page, "name"))
        self.assertEqual(self.page.slug, "alex-ancien", "l'adresse ne change pas d'office")

    def test_le_titre_suit_aussi_par_le_geste_et_sur_une_page_archivee(self):
        self.page.active = False
        self._vider_suivi()
        self._assistant(self.proprio, self.personne, new_name="Alex Geste").action_apply()
        self._vider_suivi()
        self.assertEqual(self.page.name, "Alex Geste")
        self.assertFalse(self._traces(self.page, "name"))

    def test_un_titre_ecrit_a_la_main_reste(self):
        self.page.name = "Alex, conseillère en voyage"
        self._vider_suivi()
        self.personne.write({"name": "Alex Nouveau"})
        self.assertEqual(self.page.name, "Alex, conseillère en voyage")

    def test_une_page_d_autrui_ne_suit_pas(self):
        autre = self.collegue.partner_id
        page = self.env["bf.linkpage"].create({
            "name": "Alex Ancien", "slug": "alex-ancien-homonyme", "kind": "owner",
            "partner_id": autre.id, "state": "published",
        })
        self.personne.write({"name": "Alex Nouveau"})
        self.assertEqual(page.name, "Alex Ancien", "un homonyme ne suit pas")

    # -- L'adresse, au choix -------------------------------------------------

    def test_l_adresse_ne_change_que_sur_demande(self):
        sans = self._assistant(self.proprio, self.personne, new_name="Alex Sans")
        self.assertEqual(sans.address_count, 1)
        self.assertFalse(sans.change_linkpage_address, "jamais cochée d'office")
        self.assertFalse(sans.address_ids)

        self._assistant(self.proprio, self.personne, new_name="Alex Nouveau",
                        change_linkpage_address=True).action_apply()
        self._vider_suivi()
        self.assertEqual(self.page.slug, "alex-nouveau")
        self.assertFalse(self._traces(self.page, "slug", "name"),
                         "l'ancienne adresse ne reste pas au fil de la page")
        ancienne = self.env["bf.linkpage"]._resolve_slug("alex-ancien")
        self.assertFalse(ancienne, "l'ancienne adresse ne mène plus nulle part")

    def test_une_adresse_prise_reste_libre_de_doublon(self):
        self.env["bf.linkpage"].create({
            "name": "Alex Nouveau", "slug": "alex-nouveau", "kind": "owner",
            "partner_id": self.collegue.partner_id.id, "state": "published",
        })
        self._assistant(self.proprio, self.personne, new_name="Alex Nouveau",
                        change_linkpage_address=True).action_apply()
        self.assertEqual(self.page.slug, "alex-nouveau-2")

    def test_l_adresse_d_autrui_ne_change_pas(self):
        # La collègue peut renommer la fiche (gestion des contacts), pas la page.
        assistant = self._assistant(self.collegue, self.personne, new_name="Alex Nouveau",
                                    change_linkpage_address=True)
        self.assertEqual(assistant.address_count, 0, "la case ne paraît pas")
        self.assertFalse(assistant.address_ids)
        assistant.action_apply()
        self.assertEqual(self.personne.name, "Alex Nouveau")
        self.assertEqual(self.page.name, "Alex Nouveau", "le titre suit quand même")
        self.assertEqual(self.page.slug, "alex-ancien", "une case cochée par RPC n'y change rien")

    def test_l_adresse_change_sans_trace_apres_une_ecriture_suivie(self):
        # Une écriture suivie de la page plus tôt dans la
        # transaction relevait l'ancienne adresse.
        self.page.with_user(self.registraire).write({"advisor_user_id": self.collegue.id})
        self._assistant(self.registraire, self.personne, new_name="Alex Nouveau",
                        change_linkpage_address=True).action_apply()
        self._vider_suivi()
        self.assertEqual(self.page.slug, "alex-nouveau")
        self.assertFalse(self._traces(self.page, "slug", "name"))
        self.assertTrue(self._traces(self.page, "advisor_user_id"), "témoin : le suivi tourne")

    def test_la_page_publique_prend_l_adresse_propre(self):
        # Une page archivée plus ancienne ne
        # doit pas prendre l'adresse propre à la page publique.
        # Son titre la place AVANT la page publique dans l'ordre naturel des
        # pages (`_order = "name"`) : sans le tri, elle prendrait l'adresse.
        archivee = self.env["bf.linkpage"].create({
            "name": "Aaa ancienne page", "slug": "ancien-alex", "kind": "owner",
            "partner_id": self.personne.id, "user_id": self.proprio.id,
            "state": "published", "active": False,
        })
        ordre = self.env["bf.linkpage"]._bf_owner_pages(self.personne)
        self.assertEqual(ordre[0], archivee, "témoin : l'ordre naturel met l'archivée en tête")
        self._assistant(self.registraire, self.personne, new_name="Alex Nouveau",
                        change_linkpage_address=True).action_apply()
        self.assertEqual(self.page.slug, "alex-nouveau")
        self.assertEqual(archivee.with_context(active_test=False).slug, "alex-nouveau-2")

    def test_le_gestionnaire_des_pages_change_l_adresse(self):
        self._assistant(self.registraire, self.personne, new_name="Alex Nouveau",
                        change_linkpage_address=True).action_apply()
        self.assertEqual(self.page.slug, "alex-nouveau")

    # -- L'historique --------------------------------------------------------

    def test_effacer_l_historique_couvre_la_page(self):
        # Des traces anciennes : titre et adresse changés à la main, suivis.
        self.page.with_user(self.registraire).write({"name": "Alex Vieux Titre",
                                                     "slug": "alex-vieux"})
        self._vider_suivi()
        self.assertTrue(self._traces(self.page, "name"))
        self.assertTrue(self._traces(self.page, "slug"))
        self._assistant(self.registraire, self.personne, new_name="Alex Nouveau",
                        erase_history=True).action_apply()
        self._vider_suivi()
        self.assertFalse(self._traces(self.page, "name", "slug"))

    def test_effacer_l_historique_couvre_l_usager_et_l_assignation(self):
        # La trace de l'usager et l'avis d'assignation
        # gardent le nom en texte.
        page = self.env["bf.linkpage"].create({
            "name": "Alex Ancien", "slug": "alex-ancien-usager", "kind": "owner",
            "partner_id": self.personne.id, "state": "published",
        })
        self._vider_suivi()
        page.with_user(self.registraire).write({"user_id": self.proprio.id})
        self._vider_suivi()
        self.assertTrue(self._traces(page, "user_id"), "témoin : la trace existe")
        avis = lambda: self.env["mail.message"].sudo().search([
            ("model", "=", "bf.linkpage"), ("res_id", "=", page.id),
            ("message_type", "=", "user_notification")])
        self.assertTrue(avis(), "témoin : l'avis d'assignation existe")
        # Témoins négatifs : une note et la trace d'un autre champ restent.
        page.message_post(body="Note à garder", message_type="comment",
                          subtype_xmlid="mail.mt_note")
        page.with_user(self.registraire).write({"advisor_user_id": self.collegue.id})
        self._vider_suivi()
        self._assistant(self.registraire, self.personne, new_name="Alex Nouveau",
                        erase_history=True).action_apply()
        self._vider_suivi()
        self.assertFalse(self._traces(page, "user_id", "partner_id", "name", "slug"))
        self.assertFalse(avis())
        self.assertTrue(self._traces(page, "advisor_user_id"), "les autres traces restent")
        notes = page.message_ids.filtered(lambda m: "Note à garder" in (m.body or ""))
        self.assertTrue(notes, "les notes restent")
        self.assertEqual(notes.record_name, "Alex Nouveau", "le titre recopié suit")

    def test_la_fusion_fait_suivre_le_titre(self):
        # La fusion déplace la page par SQL.
        ancien = self.env["res.partner"].create({"name": "Zed Ancien", "email": "zed@essai.test"})
        nouveau = self.env["res.partner"].create({"name": "Zed Nouveau", "email": "zed@essai.test"})
        page = self.env["bf.linkpage"].create({
            "name": "Zed Ancien", "slug": "zed-ancien", "kind": "owner",
            "partner_id": ancien.id, "state": "published",
        })
        main = self.env["bf.linkpage"].create({
            "name": "Zed, à la main", "slug": "zed-main", "kind": "owner",
            "partner_id": ancien.id, "state": "published",
        })
        self._vider_suivi()
        self.env["base.partner.merge.automatic.wizard"]._merge(
            [ancien.id, nouveau.id], dst_partner=nouveau, extra_checks=False)
        self._vider_suivi()
        self.assertEqual(page.partner_id, nouveau)
        self.assertEqual(page.name, "Zed Nouveau")
        self.assertFalse(self._traces(page, "name"))
        self.assertEqual(main.name, "Zed, à la main", "un titre écrit à la main reste")
        # Et il suit encore le renommage suivant.
        nouveau.write({"name": "Zed Encore"})
        self.assertEqual(page.name, "Zed Encore")

    def test_hors_du_groupe_l_historique_de_la_page_reste(self):
        self.page.with_user(self.registraire).write({"name": "Alex Vieux Titre"})
        self._vider_suivi()
        self._assistant(self.proprio, self.personne, new_name="Alex Nouveau",
                        erase_history=True).action_apply()
        self._vider_suivi()
        self.assertTrue(self._traces(self.page, "name"))

    # -- Les pronoms ---------------------------------------------------------

    def test_pronoms_sur_la_page(self):
        moi = self.proprio.partner_id
        page = self.env["bf.linkpage"].create({
            "name": moi.name, "slug": "alex-sa-page", "kind": "owner",
            "partner_id": moi.id, "user_id": self.proprio.id, "state": "published",
        })
        self.assertEqual(page._bf_public_pronouns(), "", "rien de donné, rien de dit")
        moi.bf_pronouns = "iel"
        self.assertTrue(page.bf_show_pronouns, "la case est cochée d'office")
        self.assertEqual(page._bf_public_pronouns(), "iel")
        page.bf_show_pronouns = False
        self.assertEqual(page._bf_public_pronouns(), "")

    def test_pas_de_pronoms_sur_la_page_d_un_autre_usager(self):
        # Un interne peut faire une page au nom d'une
        # fiche qui n'est pas la sienne ; la case n'y est pas entre les mains
        # de la personne.
        self.personne.bf_pronouns = "iel"
        self.assertNotEqual(self.page.user_id.partner_id, self.personne)
        self.assertEqual(self.page._bf_public_pronouns(), "")
        # Sa propre page, si : la fiche de l'usager.
        self.proprio.partner_id.bf_pronouns = "elle"
        sienne = self.env["bf.linkpage"].create({
            "name": "Alex Ancien", "slug": "alex-sienne", "kind": "owner",
            "partner_id": self.proprio.partner_id.id, "user_id": self.proprio.id,
            "state": "published",
        })
        self.assertEqual(sienne._bf_public_pronouns(), "elle")

    def test_pas_de_pronoms_sur_la_page_d_un_contact_sans_compte(self):
        # Le portail n'a pas accès aux pages, la
        # personne ne pourrait jamais décocher la case.
        self.personne.bf_pronouns = "iel"
        page = self.env["bf.linkpage"].create({
            "name": "Alex Ancien", "slug": "alex-sans-compte", "kind": "owner",
            "partner_id": self.personne.id, "state": "published",
        })
        self.assertEqual(page._bf_public_pronouns(), "")

    def test_pas_de_pronoms_pour_une_societe_ni_une_page_d_organisation(self):
        societe = self.env["res.partner"].create({"name": "Société Essai", "is_company": True})
        societe.bf_pronouns = "iel"
        for kind, slug in (("org", "societe-essai"), ("owner", "societe-essai-page")):
            page = self.env["bf.linkpage"].create({
                "name": "Société Essai", "slug": slug, "kind": kind,
                "partner_id": societe.id, "state": "published",
            })
            self.assertEqual(page._bf_public_pronouns(), "", kind)


@tagged("post_install", "-at_install", "bf_preferred_name")
class TestPagePublique(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Sa propre page : les pronoms ne paraissent que là où la personne
        # peut décocher la case.
        usager = _usager(cls.env, "Sam Exemple", "sam@essai.test",
                         "bf_linkpage.group_bf_linkpage_user")
        cls.personne = usager.partner_id
        cls.personne.bf_pronouns = "they/them"
        cls.page = cls.env["bf.linkpage"].create({
            "name": "Sam Exemple", "slug": "sam-exemple-essai", "kind": "owner",
            "partner_id": cls.personne.id, "user_id": usager.id, "state": "published",
        })

    def _page(self):
        # Voir test_public_page de bf_linkpage : vider, fixer la langue, ne pas
        # suivre les redirections.
        self.env.flush_all()
        return self.url_open("/l/sam-exemple-essai", allow_redirects=False,
                             headers={"Accept-Language": "fr-CA,fr;q=0.9"})

    def test_les_pronoms_sous_le_nom(self):
        response = self._page()
        self.assertEqual(response.status_code, 200)
        self.assertIn('bf-linkpage__pronouns">they/them</p>', response.text)

    def test_la_case_les_retire_de_la_page_seulement(self):
        self.page.bf_show_pronouns = False
        response = self._page()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("bf-linkpage__pronouns", response.text)
        self.assertNotIn("they/them", response.text)
        self.assertEqual(self.personne.bf_pronouns, "they/them", "la fiche les garde")

    def test_le_titre_suivi_paraît_sur_la_page(self):
        self.personne.name = "Sam Nouveau"
        response = self._page()
        self.assertIn("Sam Nouveau", response.text)
        self.assertNotIn("Sam Exemple", response.text)


@tagged("post_install", "-at_install", "bf_preferred_name")
class TestProposition(TransactionCase):
    """Ce que le geste propose : seulement les adresses qui portent le nom quitté."""

    def test_propositions(self):
        cas = [
            ("alexandre-fictif", "Alexandre Fictif", "Alexandra Fictif", "alexandra-fictif"),
            ("alexandre", "Alexandre Fictif", "Alexandra Fictif", "alexandra"),
            ("perso-dominique", "Dominique Marchetti-Exemple", "Sacha Fictif", "perso-sacha"),
            ("dominique-marchetti-exemple", "Dominique Marchetti-Exemple", "Sacha Fictif", "sacha-fictif"),
            ("cafe-alexandre-magali", "Alexandre Fictif", "Alexandra Fictif", "cafe-alexandra-magali"),
            ("rencontre-flexible", "Alexandre Fictif", "Alexandra Fictif", None),
            ("alex-ancien", "Alex Ancien", "Alex Nouveau", "alex-nouveau"),
            ("demo-ancien", "Alex Ancien", "Alex Ancien", None),
            ("marie-ancienne", "Marie Ancienne", "Marié Ancienne", None),
            # Les particules ne déclenchent pas.
            ("visite-de-la-maison", "Marie de la Fontaine", "Marie Fontaine", None),
            ("rendez-vous-a-domicile", "Marc A. Tremblay", "Marc Tremblay", None),
            ("seminaire-paris", "Paris Gagnon", "Sacha Gagnon", "seminaire-sacha"),
        ]
        for slug, ancien, nouveau, attendu in cas:
            self.assertEqual(propose_slug(slug, ancien, nouveau), attendu, slug)

    def test_un_mot_isole_nait_decoche(self):
        self.assertTrue(carries_full_name("dominique-marchetti-exemple", "Dominique Marchetti-Exemple"))
        self.assertTrue(carries_full_name("alexandre-fictif", "Alexandre Fictif"))
        self.assertFalse(carries_full_name("seminaire-paris", "Paris Gagnon"))
        self.assertFalse(carries_full_name("perso-dominique", "Dominique Marchetti-Exemple"))
        self.assertFalse(carries_full_name("visite-de-la-maison", "Marie de la Fontaine"))


@tagged("post_install", "-at_install", "bf_preferred_name")
class TestAdressesRetirees(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.personne = cls.env["res.partner"].create({"name": "Alex Ancien"})
        cls.autre = cls.env["res.partner"].create({"name": "Alex Autre"})
        cls.page = cls.env["bf.linkpage"].create({
            "name": "Alex Ancien", "slug": "alex-ancien", "kind": "owner",
            "partner_id": cls.personne.id, "state": "published",
        })

    def test_une_adresse_quittee_ne_se_reprend_pas(self):
        self.page.slug = "alex-nouveau"
        with self.assertRaises(ValidationError):
            self.env["bf.linkpage"].create({
                "name": "Alex Autre", "slug": "alex-ancien", "kind": "owner",
                "partner_id": self.autre.id, "state": "published",
            })
        autre = self.env["bf.linkpage"].create({
            "name": "Alex Autre", "slug": "alex-autre", "kind": "owner",
            "partner_id": self.autre.id, "state": "published",
        })
        with self.assertRaises(ValidationError):
            autre.slug = "alex-ancien"
        # Le générateur saute l'adresse retirée.
        self.assertNotEqual(self.env["bf.linkpage"]._generate_slug("Alex Ancien"), "alex-ancien")
        # La page qui l'a quittée peut y revenir.
        self.page.slug = "alex-ancien"
        self.assertEqual(self.page.slug, "alex-ancien")

    def test_une_page_supprimee_garde_son_adresse(self):
        # Un départ suivi d'un homonyme.
        self.page.unlink()
        with self.assertRaises(ValidationError):
            self.env["bf.linkpage"].create({
                "name": "Alex Autre", "slug": "alex-ancien", "kind": "owner",
                "partner_id": self.autre.id, "state": "published",
            })
        # La même personne la reprend (passe des employés qui recrée sa page).
        page = self.env["bf.linkpage"].create({
            "name": "Alex Ancien", "slug": "alex-ancien", "kind": "owner",
            "partner_id": self.personne.id, "state": "published",
        })
        self.assertEqual(page.slug, "alex-ancien")

    def test_la_reserve_survit_a_la_rotation_du_secret(self):
        self.page.slug = "alex-nouveau"
        self.env["ir.config_parameter"].sudo().set_param("database.secret", "un-autre-secret")
        self.assertNotEqual(self.env["bf.linkpage"]._generate_slug("Alex Ancien"), "alex-ancien")

    def test_une_valeur_vide_garde_une_adresse_lisible(self):
        page = self.env["bf.linkpage"].create({
            "name": "Page", "slug": "page", "kind": "owner",
            "partner_id": self.autre.id, "state": "published",
        })
        page.slug = "page-quittee"
        self.assertEqual(self.env["bf.linkpage"]._generate_slug(""), "page-2")

    def test_un_usager_ne_reprend_pas_l_adresse_d_un_autre(self):
        # En rattachant un instant sa page à la fiche de
        # l'autre (voie A), ou en créant une page au nom de l'autre (voie B).
        self.page.slug = "alex-nouveau"
        bob = _usager(self.env, "Bob Essai", "bob.essai@essai.test",
                      "bf_linkpage.group_bf_linkpage_user")
        sienne = self.env["bf.linkpage"].create({
            "name": "Bob Essai", "slug": "bob-essai", "kind": "owner",
            "partner_id": bob.partner_id.id, "user_id": bob.id, "state": "published",
        })
        with self.assertRaises(ValidationError):
            sienne.with_user(bob).write({"partner_id": self.personne.id, "slug": "alex-ancien"})
        with self.assertRaises(ValidationError):
            self.env["bf.linkpage"].with_user(bob).create({
                "name": "Bob", "slug": "alex-ancien", "kind": "owner",
                "partner_id": self.personne.id, "user_id": bob.id, "state": "published",
            })

    def test_reattribuer_la_page_ne_transmet_pas_l_adresse(self):
        # La page de la personne reprend son adresse quittée, puis on la
        # rattache à quelqu'un d'autre SANS toucher à l'adresse : la contrainte
        # doit aussi regarder la fiche (mutation « contrainte sur l'adresse
        # seule », survivante avant cet essai).
        self.page.slug = "alex-nouveau"
        self.page.slug = "alex-ancien"
        with self.assertRaises(ValidationError):
            self.page.partner_id = self.autre

    def test_supprimer_la_fiche_garde_l_adresse(self):
        # La page part en cascade SQL.
        self.personne.unlink()
        with self.assertRaises(ValidationError):
            self.env["bf.linkpage"].create({
                "name": "Alex Autre", "slug": "alex-ancien", "kind": "owner",
                "partner_id": self.autre.id, "state": "published",
            })

    def test_la_cle_existe_des_l_installation(self):
        self.assertTrue(self.env["ir.config_parameter"].sudo().get_param(
            "bf_preferred_name_linkpage.retired_slug_key"))

    def test_la_reserve_ne_garde_pas_l_adresse_en_clair(self):
        self.page.slug = "alex-nouveau"
        reserve = self.env["bf.linkpage.retired.slug"].sudo().search([("page_id", "=", self.page.id)])
        self.assertTrue(reserve)
        for record in reserve:
            self.assertNotIn("alex", record.slug_hash)
            self.assertNotIn("ancien", record.slug_hash)
            self.assertEqual(len(record.slug_hash), 64)

    def test_la_reserve_n_est_pas_lisible_d_un_interne(self):
        self.page.slug = "alex-nouveau"
        interne = _usager(self.env, "Ivo Interne", "ivo.interne@essai.test",
                          "bf_linkpage.group_bf_linkpage_manager")
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            self.env["bf.linkpage.retired.slug"].with_user(interne).search([])


@tagged("post_install", "-at_install", "bf_preferred_name")
class TestAutresAdresses(TransactionCase):
    """Le dépôt sécurisé et les types de rendez-vous qui portent le nom."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "secure.transfer.brand" not in cls.env or "resource.booking.type" not in cls.env:
            cls.skipTest(cls, "sans le dépôt sécurisé ou les rendez-vous")
        cls.usager = _usager(cls.env, "Dominique Essai", "dominique.essai@essai.test",
                             "bf_linkpage.group_bf_linkpage_user")
        cls.personne = cls.usager.partner_id
        cls.autre = _usager(cls.env, "Lou Autre", "lou.autre@essai.test")
        Brand = cls.env["secure.transfer.brand"]
        # `bf_securetransfer` donne d'office à chaque usager interne une marque
        # à son nom : c'est le cas courant.
        cls.marque = Brand.search([("owner_user_id", "=", cls.usager.id)], limit=1) \
            or Brand.create({"name": "Dépôt", "slug": "dominique-essai", "owner_user_id": cls.usager.id})
        assert cls.marque.slug == "dominique-essai", cls.marque.slug
        cls.marque_neutre = Brand.create({"name": "Dépôt neutre", "slug": "depot-neutre-essai",
                                          "owner_user_id": cls.usager.id})
        calendrier = cls.env.ref("resource.resource_calendar_std")
        def type_de(slug, users):
            ressources = cls.env["resource.resource"].create(
                [{"name": u.name, "user_id": u.id, "calendar_id": calendrier.id} for u in users])
            combinaison = cls.env["resource.booking.combination"].create(
                {"resource_ids": [(6, 0, ressources.ids)]})
            return cls.env["resource.booking.type"].create({
                "name": slug, "slug": slug, "resource_calendar_id": calendrier.id,
                "combination_rel_ids": [(0, 0, {"combination_id": combinaison.id})],
            })
        cls.type_perso = type_de("perso-dominique-zz", cls.usager)
        cls.type_partage = type_de("cafe-dominique-lou-zz", cls.usager | cls.autre)

    def _assistant(self, **values):
        return self.env["bf.preferred.name.rename"].with_context(
            active_model="res.partner", active_id=self.personne.id,
        ).create(dict(values))

    def test_les_adresses_qui_portent_le_nom(self):
        assistant = self._assistant(new_name="Sacha Essai", change_linkpage_address=True)
        lignes = {(l.kind, l.res_id): l for l in assistant.address_ids}
        self.assertEqual(lignes[("securetransfer", self.marque.id)].new_slug, "sacha-essai")
        self.assertTrue(lignes[("securetransfer", self.marque.id)].apply, "le nom entier")
        self.assertEqual(lignes[("appointment", self.type_perso.id)].new_slug, "perso-sacha-zz")
        self.assertNotIn(("securetransfer", self.marque_neutre.id), lignes, "ne porte pas le nom")
        self.assertNotIn(("appointment", self.type_partage.id), lignes, "type partagé")
        # Une ligne décochée ne change pas.
        lignes[("appointment", self.type_perso.id)].apply = False
        assistant.action_apply()
        self.assertEqual(self.marque.slug, "sacha-essai")
        self.assertEqual(self.type_perso.slug, "perso-dominique-zz")
        self.assertEqual(self.type_partage.slug, "cafe-dominique-lou-zz")

    def test_une_adresse_modifiee_a_la_main_est_rendue_valide(self):
        assistant = self._assistant(new_name="Sacha Essai", change_linkpage_address=True)
        ligne = assistant.address_ids.filtered(lambda l: l.res_id == self.type_perso.id)
        self.assertFalse(ligne.apply, "un prénom isolé naît décoché")
        ligne.apply = True
        ligne.new_slug = "Rencontre Sachà"
        assistant.action_apply()
        self.assertEqual(self.type_perso.slug, "rencontre-sacha")

    def test_par_le_client_web(self):
        # Les champs en lecture seule des
        # lignes ne partaient pas à l'enregistrement.
        formulaire = Form(self.env["bf.preferred.name.rename"].with_context(
            active_model="res.partner", active_id=self.personne.id))
        formulaire.new_name = "Sacha Essai"
        formulaire.change_linkpage_address = True
        self.assertTrue(formulaire.address_ids)
        assistant = formulaire.save()
        self.assertTrue(all(assistant.address_ids.mapped("res_id")))
        assistant.action_apply()
        self.assertEqual(self.marque.slug, "sacha-essai")

    def test_ce_qui_suit_le_nouveau_nom(self):
        # L'interrupteur des pages
        # de dépôt est allumé ici : éteint (la démo), rien n'est synchronisé, et
        # c'est voulu (essai `test_interrupteur_du_depot_eteint`).
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.autoprovision_user_pages", "True")
        page = self.env["bf.linkpage"].create({
            "name": "Dominique Essai", "slug": "dominique-essai-page", "kind": "owner",
            "partner_id": self.personne.id, "user_id": self.usager.id,
            "state": "published", "booking_slug": "perso-dominique-zz",
        })
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url").rstrip("/")
        lien = None
        if "link.tracker" in self.env:
            lien = self.env["link.tracker"].create({"url": base + "/to/dominique-essai?x=1",
                                                    "title": "Dominique Essai | Dépôt"})
            autre = self.env["link.tracker"].create({"url": base + "/to/dominique-essai-bis"})
            # Un lien vers un autre site ne bouge pas.
            tiers = self.env["link.tracker"].create({"url": "https://exemple-tiers.example/to/dominique-essai"})
        assistant = self._assistant(new_name="Sacha Essai", change_linkpage_address=True)
        assistant.address_ids.write({"apply": True})
        assistant.action_apply()
        self.assertIn("Sacha Essai", self.marque.name, "le titre de la page de dépôt suit")
        self.assertEqual(self.marque.fixed_recipient_name, "Sacha Essai")
        self.assertEqual(page.booking_slug, "perso-sacha-zz")
        if lien:
            self.assertEqual(lien.url, base + "/to/sacha-essai?x=1")
            self.assertEqual(lien.title, "Sacha Essai | Dépôt", "le titre suit")
            self.assertEqual(autre.url, base + "/to/dominique-essai-bis", "une autre adresse ne bouge pas")
            self.assertEqual(tiers.url, "https://exemple-tiers.example/to/dominique-essai")

    def test_un_lien_court_en_doublon_ne_bloque_pas_le_geste(self):
        if "link.tracker" not in self.env:
            self.skipTest("sans link_tracker")
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url").rstrip("/")
        vieux = self.env["link.tracker"].create({"url": base + "/to/dominique-essai"})
        self.env["link.tracker"].create({"url": base + "/to/sacha-essai"})
        assistant = self._assistant(new_name="Sacha Essai", change_linkpage_address=True)
        assistant.action_apply()
        self.assertEqual(self.personne.name, "Sacha Essai")
        self.assertEqual(self.marque.slug, "sacha-essai")
        self.assertEqual(vieux.url, base + "/to/dominique-essai", "laissé tel quel")

    def test_interrupteur_du_depot_eteint(self):
        # La synchronisation créerait une page de dépôt.
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_securetransfer.autoprovision_user_pages", "False")
        nom_avant = self.marque.name
        self._assistant(new_name="Sacha Essai").action_apply()
        self.assertEqual(self.marque.name, nom_avant, "rien n'est synchronisé")

    def test_types_archive_et_partage_avec_une_ressource_sans_compte(self):
        self.type_perso.active = False
        calendrier = self.env.ref("resource.resource_calendar_std")
        sans_compte = self.env["resource.resource"].create({"name": "Salle", "calendar_id": calendrier.id})
        ressource = self.env["resource.resource"].search([("user_id", "=", self.usager.id)], limit=1)
        combinaison = self.env["resource.booking.combination"].create(
            {"resource_ids": [(6, 0, (ressource | sans_compte).ids)]})
        avec_salle = self.env["resource.booking.type"].create({
            "name": "dominique-salle-zz", "slug": "dominique-salle-zz",
            "resource_calendar_id": calendrier.id,
            "combination_rel_ids": [(0, 0, {"combination_id": combinaison.id})],
        })
        assistant = self._assistant(new_name="Sacha Essai", change_linkpage_address=True)
        lignes = {(l.kind, l.res_id) for l in assistant.address_ids}
        self.assertIn(("appointment", self.type_perso.id), lignes, "archivé, mais à elle seule")
        self.assertNotIn(("appointment", avec_salle.id), lignes, "partagé avec une salle")

    def test_une_ligne_ajoutee_par_l_api_ne_change_rien(self):
        assistant = self._assistant(new_name="Sacha Essai", change_linkpage_address=True)
        assistant.address_ids = [(0, 0, {
            "kind": "appointment", "res_id": self.type_partage.id,
            "current_address": "/appointment/x", "new_slug": "detourne", "apply": True})]
        assistant.action_apply()
        self.assertEqual(self.type_partage.slug, "cafe-dominique-lou-zz")

    def test_sans_droit_sur_la_marque_pas_de_ligne(self):
        collegue = _usager(self.env, "Camille Sans Droit", "camille.sd@essai.test")
        assistant = self.env["bf.preferred.name.rename"].with_user(collegue).with_context(
            active_model="res.partner", active_id=self.personne.id,
        ).create({"new_name": "Sacha Essai", "change_linkpage_address": True})
        self.assertFalse(assistant.address_ids.filtered(lambda l: l.kind != "linkpage"))
