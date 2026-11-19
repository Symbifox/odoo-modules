# -*- coding: utf-8 -*-
"""Ce que la page promet, et surtout ce qu'elle refuse.

⚠️ Les tests s'adaptent aux modules présents : ce module ne dépend d'aucun des
trois, donc un banc qui n'en porte qu'un doit quand même les jouer. Ils sautent
seulement quand il n'y en a aucun, ce qui est le seul cas où ils ne prouveraient
rien.
"""
import hashlib
import secrets
from unittest.mock import patch

from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("bf_devices", "post_install", "-at_install")
class TestRegistreAppareils(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Registre = cls.env["bf.device.registry"]
        cls.moi = new_test_user(cls.env, login="appareils_moi")
        cls.autre = new_test_user(cls.env, login="appareils_autre")
        cls.sources = cls.Registre._sources_presentes()

    def _poser(self, usager, nom="Banc"):
        """Un appareil par source présente, pour [usager].

        🔴 Le champ du jeton n'a pas le même nom partout, et le supposer faisait
        tomber SEPT essais de ce fichier dès qu'un pont vers un registre à
        empreinte était installé : ces registres-là ne portent QUE `token_hash`,
        jamais `device_token`, et la création levait
        « Invalid field 'device_token' ». Relevé à l'essai.

        Le montage demande donc au modèle ce qu'il sait porter, au lieu de le
        deviner : c'est aussi ce qui le gardera debout au prochain registre.
        """
        poses = []
        for source in self.sources:
            Modele = self.env[source["model"]].sudo()
            valeurs = {"user_id": usager.id, "name": nom}
            jeton = secrets.token_urlsafe(32)
            if "device_token" in Modele._fields:
                valeurs["device_token"] = jeton
            if "token_hash" in Modele._fields:
                valeurs["token_hash"] = hashlib.sha256(
                    jeton.encode()).hexdigest()
            valeurs.update(self._exigences(Modele, valeurs))
            poses.append(Modele.create(valeurs))
        return poses

    #: Ce qu'un registre exige en plus, et que ce fichier ne peut pas deviner.
    #: Nommé par modèle : une valeur plausible pour un essai, pas un défaut.
    EXIGENCES = {
        ("bf.desktop.device", "app"): "stickynotes",
    }

    def _exigences(self, Modele, deja):
        """Les champs OBLIGATOIRES du modèle que le montage n'a pas encore posés.

        🔴 Même leçon que pour le jeton, un cran plus haut : la quatrième
        source a ajouté `bf.desktop.device`, qui exige `app` (quelle surface de bureau), et les
        sept essais de ce fichier sont tombés d'un coup sur
        « null value in column "app" ». Deviner les champs d'un registre ne
        marche pas ; les découvrir, si.

        Le manque se signale ICI, avec le nom du champ, plutôt qu'au fond d'une
        contrainte Postgres : le prochain registre cassera donc vite et clair.
        """
        valeurs = {}
        for nom, champ in Modele._fields.items():
            if not champ.required or nom in deja or champ.default is not None:
                continue
            if champ.type not in ("char", "text", "selection"):
                continue
            cle = (Modele._name, nom)
            self.assertIn(
                cle, self.EXIGENCES,
                "le registre %s exige « %s » : ajouter sa valeur d'essai à "
                "EXIGENCES" % (Modele._name, nom))
            valeurs[nom] = self.EXIGENCES[cle]
        return valeurs

    def setUp(self):
        super().setUp()
        if not self.sources:
            self.skipTest("aucun module d'appariement sur ce banc")

    # ── Lire ─────────────────────────────────────────────────────────────

    def test_je_vois_mes_appareils_un_par_module(self):
        self._poser(self.moi, "Mon téléphone")
        lignes = self.Registre.with_user(self.moi)._mes_appareils()
        self.assertEqual(len(lignes), len(self.sources))
        self.assertEqual({l["nom"] for l in lignes}, {"Mon téléphone"})

    def test_l_appareil_d_une_autre_personne_n_apparait_jamais(self):
        self._poser(self.autre, "Téléphone d'à côté")
        lignes = self.Registre.with_user(self.moi)._mes_appareils()
        self.assertFalse(lignes)

    def test_un_appareil_retire_ne_s_affiche_plus(self):
        poses = self._poser(self.moi)
        poses[0].sudo().active = False
        lignes = self.Registre.with_user(self.moi)._mes_appareils()
        self.assertEqual(len(lignes), len(self.sources) - 1)

    def test_une_source_absente_est_ignoree_sans_bruit(self):
        """La découverte porte sur `env`, pas sur une liste figée."""
        fantome = ({"model": "ce.modele.n.existe.pas", "libelle": "Fantôme",
                    "efface": ()},)
        with patch.object(type(self.Registre), "SOURCES",
                          fantome + tuple(self.sources)):
            presentes = self.Registre._sources_presentes()
        self.assertNotIn("ce.modele.n.existe.pas",
                         [s["model"] for s in presentes])
        self.assertEqual(len(presentes), len(self.sources))

    # ── Retirer ──────────────────────────────────────────────────────────

    def test_retirer_desactive_et_efface_ce_que_le_logout_efface(self):
        poses = self._poser(self.moi)
        for appareil in poses:
            if "push_endpoint" in appareil._fields:
                appareil.sudo().push_endpoint = "https://ntfy.test/abc"

        for appareil in poses:
            cle = "%s,%s" % (appareil._name, appareil.id)
            self.assertTrue(self.Registre.with_user(self.moi)._revoquer(cle))

        par_modele = {s["model"]: s for s in self.sources}
        for appareil in poses:
            self.assertFalse(appareil.sudo().active)
            for champ in par_modele[appareil._name]["efface"]:
                if champ in appareil._fields:
                    self.assertFalse(appareil.sudo()[champ], champ)
            # 🔴 Ce que devient le JETON appartient à chaque module, et la
            # page n'a pas à le promettre. Les trois registres d'origine le
            # gardent (il n'ouvre plus rien puisque `_resolve` filtre sur
            # `active`) ; `bf.timer.device` et `bf.nfc.device`, eux, l'effacent
            # dans leur propre `write` dès qu'un appareil est désactivé. C'est
            # plus strict, pas plus faible, et l'affirmer identique pour tous
            # faisait tomber cet essai dès que l'un des deux ponts était
            # installé. Relevé à l'essai.
            #
            # Ce que la page promet, et ce qui s'éprouve ici : l'appareil est
            # inactif, et tout ce que `efface` nomme est bel et bien à faux.

    def test_je_ne_peux_pas_retirer_l_appareil_d_une_autre_personne(self):
        """🔴 La propriété qui tient toute la page : la clé vient de la
        requête, donc l'appartenance se vérifie sur l'enregistrement."""
        poses = self._poser(self.autre)
        for appareil in poses:
            cle = "%s,%s" % (appareil._name, appareil.id)
            self.assertFalse(self.Registre.with_user(self.moi)._revoquer(cle))
            self.assertTrue(appareil.sudo().active)

    def test_une_cle_malformee_ne_fait_rien(self):
        for cle in ("", None, "sans-virgule", "res.users,1",
                    "sms.archive.mobile.device,abc",
                    "sms.archive.mobile.device,999999999"):
            self.assertFalse(self.Registre.with_user(self.moi)._revoquer(cle),
                             "la clé %r a été acceptée" % (cle,))

    def test_tout_retirer_ne_touche_que_les_miens(self):
        self._poser(self.moi)
        chez_l_autre = self._poser(self.autre)

        retires = self.Registre.with_user(self.moi)._revoquer_tout()

        self.assertEqual(retires, len(self.sources))
        self.assertFalse(self.Registre.with_user(self.moi)._mes_appareils())
        for appareil in chez_l_autre:
            self.assertTrue(appareil.sudo().active)

    def test_tout_retirer_sans_appareil_ne_ment_pas(self):
        self.assertEqual(self.Registre.with_user(self.moi)._revoquer_tout(), 0)

    # ── Ce que le jeton ouvre vraiment ───────────────────────────────────

    def test_la_ligne_dit_les_autres_surfaces_du_meme_jeton(self):
        """🔴 L'enjeu relevé le 2026-09-03 : la page n'annonçait que le module
        émetteur, donc « retirer l'accès » se lisait comme « couper les
        messages » alors que le même jeton ouvre l'agenda, l'assistant et le
        reste."""
        self._poser(self.moi)
        lignes = self.Registre.with_user(self.moi)._mes_appareils()
        par_modele = {l["modele"]: l for l in lignes}

        installe = lambda nom: bool(self.env["ir.module.module"].sudo().search(  # noqa: E731
            [("name", "=", nom), ("state", "=", "installed")], limit=1))

        if "sms.archive.mobile.device" in par_modele and installe("bf_calendar_mobile"):
            self.assertIn(
                "Agenda et tâches",
                par_modele["sms.archive.mobile.device"]["capacites"],
                "Le jeton Messages ouvre l'agenda et la page ne le dit pas.")
        if "bf.email.mobile.device" in par_modele and installe("bf_calendar_mobile"):
            self.assertIn(
                "Agenda et tâches",
                par_modele["bf.email.mobile.device"]["capacites"])

    def test_le_telephone_n_est_offert_qu_au_jeton_messages(self):
        """`bf_softphone` importe le `_authed` de Messages, donc le jeton
        Courriel ne l'ouvre pas. Le promettre aux deux serait faux."""
        if not self.env["ir.module.module"].sudo().search(
                [("name", "=", "bf_softphone"), ("state", "=", "installed")], limit=1):
            self.skipTest("bf_softphone absent")
        par_source = self.Registre._capacites_par_source()
        self.assertIn("Téléphone", par_source.get("sms.archive.mobile.device", []))
        self.assertNotIn("Téléphone", par_source.get("bf.email.mobile.device", []))

    def test_une_capacite_non_installee_n_est_pas_promise(self):
        """La page est un inventaire, pas une brochure."""
        par_source = self.Registre._capacites_par_source()
        offerts = {lib for libs in par_source.values() for lib in libs}
        installes = set(self.env["ir.module.module"].sudo().search([
            ("name", "in", [c["module"] for c in self.Registre.CAPACITES]),
            ("state", "=", "installed"),
        ]).mapped("name"))
        for capacite in self.Registre.CAPACITES:
            if capacite["module"] not in installes:
                self.assertNotIn(
                    capacite["libelle"], offerts,
                    "%s est promis alors que %s n'est pas installé"
                    % (capacite["libelle"], capacite["module"]))
