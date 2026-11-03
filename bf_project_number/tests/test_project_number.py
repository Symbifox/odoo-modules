from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from ..hooks import post_init_numeroter_existants


# `post_install` : sur une grosse base, les essais `at_install` tournent sur un
# registre PARTIEL et les surcharges des autres modules du projet ne sont pas
# encore chargées — un défaut se cache là, ou s'invente.
@tagged("post_install", "-at_install")
class TestNumeroDeProjet(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Projet = self.env["project.project"]

    # ── oracle INDÉPENDANT du code sous essai ────────────────────────────────
    # 🔴 Les attentes se lisaient d'abord en appelant `_bf_numeros_pris()` et
    # `_bf_numero_libre()`, c'est-à-dire les fonctions mêmes qu'on vérifie :
    # retirer `active_test=False` de la première cassait le code ET l'attente de
    # la même façon, et l'essai restait vert. L'oracle lit donc la table en SQL.

    def _pris(self):
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT bf_project_number FROM project_project WHERE bf_project_number IS NOT NULL"
        )
        return {int(t) for (t,) in self.env.cr.fetchall() if t and t.strip().isdigit()}

    def _libres(self, combien=1):
        """Les `combien` plus petits numéros libres, lus en SQL."""
        pris, suite, candidat = self._pris(), [], 1
        for _ in range(combien):
            while candidat in pris:
                candidat += 1
            suite.append(f"{candidat:03d}")
            pris.add(candidat)
        return suite

    def _libre_eleve(self):
        """Un numéro franchement libre, au-dessus de tout ce qui est pris.

        🔴 Aucun essai n'écrit de numéro en dur : sur une base réelle, la
        reprise a déjà pris les premiers numéros, et un numéro précis appartient
        à un vrai projet. Quatre essais sont tombés là-dessus — et deux de
        leurs remplaçants utilisaient encore « 8001 » et « 9001 », qu'une
        base peut très bien porter.
        """
        return max(self._pris() | {0}) + 7

    # ── attribution ──────────────────────────────────────────────────────────

    def test_creation_attribue_le_plus_petit_libre(self):
        attendu = self._libres()[0]
        projet = self.Projet.create({"name": "Essai attribution"})
        self.assertEqual(projet.bf_project_number, attendu)

    def test_le_trou_est_comble_avant_la_suite(self):
        """Un numéro rendu libre est repris, au lieu d'empiler à la fin."""
        premier = self.Projet.create({"name": "Essai trou A"})
        libere = premier.bf_project_number
        suivant = self.Projet.create({"name": "Essai trou B"})
        self.assertNotEqual(suivant.bf_project_number, libere)

        premier.bf_project_number = str(self._libre_eleve())  # libère son numéro
        repris = self.Projet.create({"name": "Essai trou C"})
        self.assertEqual(
            repris.bf_project_number,
            libere,
            "le plus petit numéro libre doit être repris, pas le suivant de la file",
        )

    def test_un_numero_libere_par_un_effacement_est_repris(self):
        """Les mots du client sont « un numéro libéré » : l'effacement en est un."""
        projet = self.Projet.create({"name": "Essai effacé"})
        libere = projet.bf_project_number
        projet.unlink()
        suivant = self.Projet.create({"name": "Essai après effacement"})
        self.assertEqual(suivant.bf_project_number, libere)

    def test_fournee_donne_les_TROIS_PLUS_PETITS_libres(self):
        """Pas seulement « trois numéros distincts » : les trois plus petits.

        L'ancienne assertion passait pour 019/500/777, qui satisfont
        « distincts » et violent l'exigence.
        """
        attendus = self._libres(3)
        projets = self.Projet.create(
            [{"name": "Essai fournée 1"}, {"name": "Essai fournée 2"}, {"name": "Essai fournée 3"}]
        )
        self.assertEqual(projets.mapped("bf_project_number"), attendus)

    def test_numero_donne_a_la_creation_est_garde(self):
        numero = self._libre_eleve()
        projet = self.Projet.create({"name": "Essai imposé", "bf_project_number": str(numero)})
        self.assertEqual(projet.bf_project_number, f"{numero:03d}")

    def test_un_numero_impose_ne_vole_pas_celui_qu_on_attribue(self):
        """Fournée mixte : le numéro servi est le plus petit libre, pas l'imposé."""
        attendus = self._libres(2)
        projets = self.Projet.create(
            [
                {"name": "Essai mixte imposé", "bf_project_number": attendus[0]},
                {"name": "Essai mixte servi"},
            ]
        )
        self.assertEqual(projets[0].bf_project_number, attendus[0])
        self.assertEqual(projets[1].bf_project_number, attendus[1])

    def test_un_projet_archive_garde_son_numero_pris(self):
        projet = self.Projet.create({"name": "Essai archivé"})
        numero = projet.bf_project_number
        projet.active = False
        attendu = self._libres()[0]
        self.assertNotEqual(attendu, numero)
        autre = self.Projet.create({"name": "Essai après archivage"})
        self.assertEqual(autre.bf_project_number, attendu)

    def test_duplication_donne_un_numero_neuf(self):
        """Les modèles « à dupliquer » ne doivent pas partager leur numéro."""
        modele = self.Projet.create({"name": "Essai modèle à dupliquer"})
        attendu = self._libres()[0]
        copie = modele.copy()
        self.assertEqual(copie.bf_project_number, attendu)
        self.assertNotEqual(copie.bf_project_number, modele.bf_project_number)

    def test_le_dictionnaire_de_l_appelant_n_est_pas_modifie(self):
        """Une boucle qui réutilise un gabarit ne doit pas se figer sur le 1er numéro."""
        gabarit = {"name": "Essai gabarit"}
        self.Projet.create(gabarit)
        self.assertNotIn("bf_project_number", gabarit)
        self.Projet.create(gabarit)  # ne doit pas lever « déjà pris »

    # ── forme du numéro ──────────────────────────────────────────────────────

    def test_normalisation_a_trois_chiffres(self):
        projet = self.Projet.create({"name": "Essai forme"})
        numero = self._libre_eleve()
        projet.bf_project_number = " %s " % numero          # espaces autour
        self.assertEqual(projet.bf_project_number, f"{numero:03d}")

    def test_un_numero_non_numerique_est_refuse(self):
        projet = self.Projet.create({"name": "Essai lettres"})
        with self.assertRaises(ValidationError):
            projet.bf_project_number = "A12"

    def test_zero_est_refuse(self):
        projet = self.Projet.create({"name": "Essai zéro"})
        with self.assertRaises(ValidationError):
            projet.bf_project_number = "0"

    def test_le_numero_peut_rester_vide(self):
        """Plusieurs projets sans numéro : la contrainte ne doit pas s'en mêler."""
        a = self.Projet.create({"name": "Essai vide A"})
        b = self.Projet.create({"name": "Essai vide B"})
        a.bf_project_number = False
        b.bf_project_number = False
        self.env.flush_all()
        self.assertFalse(a.bf_project_number)
        self.assertFalse(b.bf_project_number)

    def test_des_cellules_vides_d_importation_ne_se_refusent_pas_entre_elles(self):
        """🔴 Une cellule qui ne porte qu'une espace est « pas de numéro ».

        Sans le garde-fou, deux lignes pareilles dans une importation Excel se
        refusaient l'une l'autre avec « Le numéro de projet False est demandé
        deux fois », et l'importation ENTIÈRE était perdue.
        """
        attendus = self._libres(2)
        projets = self.Projet.create(
            [
                {"name": "Essai import 1", "bf_project_number": " "},
                {"name": "Essai import 2", "bf_project_number": " "},
            ]
        )
        self.assertEqual(projets.mapped("bf_project_number"), attendus)

    # ── unicité ──────────────────────────────────────────────────────────────

    def test_un_numero_deja_pris_est_refuse_en_nommant_le_detenteur(self):
        tenant = self.Projet.create({"name": "Essai détenteur"})
        autre = self.Projet.create({"name": "Essai voleur"})
        with self.assertRaises(ValidationError) as leve:
            autre.bf_project_number = tenant.bf_project_number
        self.assertIn(
            "Essai détenteur",
            str(leve.exception),
            "le refus doit nommer le projet qui détient le numéro, sinon il est inactionnable",
        )

    def test_numero_impose_deja_pris_est_refuse_a_la_creation(self):
        """Le refus doit tomber avant l'écriture, pas sortir de Postgres."""
        tenant = self.Projet.create({"name": "Essai détenteur à la création"})
        with self.assertRaises(ValidationError) as leve:
            self.Projet.create(
                {"name": "Essai voleur à la création", "bf_project_number": tenant.bf_project_number}
            )
        self.assertIn("Essai détenteur à la création", str(leve.exception))

    def test_un_numero_pris_par_un_projet_archive_est_refuse(self):
        tenant = self.Projet.create({"name": "Essai détenteur archivé"})
        numero = tenant.bf_project_number
        tenant.active = False
        autre = self.Projet.create({"name": "Essai voleur 2"})
        with self.assertRaises(ValidationError):
            autre.bf_project_number = numero

    def test_un_numero_demande_deux_fois_dans_une_fournee_est_refuse(self):
        numero = str(self._libre_eleve())
        with self.assertRaises(ValidationError):
            self.Projet.create(
                [
                    {"name": "Essai fournée doublon 1", "bf_project_number": numero},
                    {"name": "Essai fournée doublon 2", "bf_project_number": numero},
                ]
            )

    def test_un_numero_ne_se_donne_pas_a_deux_projets_a_la_fois(self):
        deux = self.Projet.create([{"name": "Essai lot A"}, {"name": "Essai lot B"}])
        with self.assertRaises(ValidationError):
            deux.write({"bf_project_number": str(self._libre_eleve())})

    def test_la_normalisation_ne_laisse_pas_passer_un_doublon_deguise(self):
        """« 19 » et « 019 » sont le même numéro : sans normalisation, deux lignes."""
        tenant = self.Projet.create({"name": "Essai doublon déguisé"})
        numero = tenant.bf_project_number
        sans_zeros = str(int(numero))
        if numero == sans_zeros:
            self.skipTest("aucun numéro à moins de trois chiffres dans cette base")
        autre = self.Projet.create({"name": "Essai doublon déguisé 2"})
        with self.assertRaises(ValidationError):
            autre.bf_project_number = sans_zeros

    @mute_logger("odoo.sql_db")
    def test_la_contrainte_existe_VRAIMENT_en_base(self):
        """🔴 Le seul essai que retirer la contrainte ferait échouer.

        Tous les autres passent par `create`/`write`, qui refusent en Python en
        amont : supprimer la déclaration SQL les laissait tous verts, alors que
        c'est elle, et elle seule, qui tient sous deux transactions simultanées.
        On écrit donc directement dans la table, sous point de reprise.
        """
        a = self.Projet.create({"name": "Essai contrainte A"})
        b = self.Projet.create({"name": "Essai contrainte B"})
        self.env.flush_all()
        with self.assertRaises(IntegrityError), self.env.cr.savepoint():
            self.env.cr.execute(
                "UPDATE project_project SET bf_project_number = %s WHERE id = %s",
                (a.bf_project_number, b.id),
            )

    def test_le_refus_ne_nomme_pas_un_projet_hors_de_portee(self):
        """Nommer le détenteur ne doit pas révéler un projet qu'on ne peut pas voir."""
        secret = self.Projet.create(
            {"name": "Essai projet confidentiel", "privacy_visibility": "followers"}
        )
        numero = secret.bf_project_number
        usager = self.env["res.users"].create(
            {
                "name": "Essai usager projet",
                "login": "essai.usager.projet",
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref("project.group_project_user").id,
                        ],
                    )
                ],
            }
        )
        with self.assertRaises(ValidationError) as leve:
            self.Projet.with_user(usager)._bf_verifie_libre(numero)
        # Sans langue au contexte, `get_lang` se replie sur la PREMIÈRE langue
        # active de la base : sur une base française le message sort du
        # fr_CA.po (ce qui l'éprouve au passage), ailleurs c'est le msgid
        # anglais. L'attente accepte les deux rendus du même terme.
        texte = str(leve.exception)
        self.assertNotIn("confidentiel", texte)
        self.assertTrue(
            "do not have access" in texte or "n'avez pas accès" in texte,
            texte,
        )

    # ── reprise des projets existants ────────────────────────────────────────

    def test_la_reprise_comble_un_numero_vide(self):
        """Le seul chemin SQL brut du module, et rien ne l'essayait."""
        projet = self.Projet.create({"name": "Essai reprise"})
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE project_project SET bf_project_number = NULL WHERE id = %s", (projet.id,)
        )
        self.env.invalidate_all()
        attendu = self._libres()[0]
        post_init_numeroter_existants(self.env)
        self.assertEqual(projet.bf_project_number, attendu)

    def test_la_reprise_ne_touche_pas_aux_numeros_deja_posés(self):
        projet = self.Projet.create({"name": "Essai reprise intacte"})
        numero = projet.bf_project_number
        post_init_numeroter_existants(self.env)
        self.assertEqual(projet.bf_project_number, numero)

    # ── le numéro sert à choisir un projet ───────────────────────────────────

    def test_le_projet_se_trouve_par_son_numero(self):
        """Dans une feuille de temps ou une facture, on tape le numéro."""
        projet = self.Projet.create({"name": "Essai recherche par numéro"})
        trouves = self.Projet.name_search(projet.bf_project_number)
        self.assertIn(
            projet.id,
            [ident for ident, _libelle in trouves],
            "le numéro doit retrouver son projet dans une liste de choix",
        )

    def test_la_recherche_par_nom_marche_encore(self):
        projet = self.Projet.create({"name": "Essai recherche par nom zzqq"})
        trouves = self.Projet.name_search("zzqq")
        self.assertIn(projet.id, [ident for ident, _libelle in trouves])
