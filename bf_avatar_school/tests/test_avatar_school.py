from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.bf_avatar.lib import render

from ..models.bf_avatar import MAJORITE, PIECES_D_ADULTE


@tagged("post_install", "-at_install")
class TestAvatarEcole(TransactionCase):
    """Ce qu'un enfant ne doit jamais se voir attribuer."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.avatars = cls.env["bf.avatar"]
        cls.params = cls.env["ir.config_parameter"].sudo()
        cls.params.set_param("bf_avatar.style", "open_peeps")

    def _eleve(self, nom="Noémie Tremblay-Fictive", ans=9, student=True):
        return self.env["res.partner"].create({
            "name": nom,
            "is_student": student,
            "student_birthdate": fields.Date.subtract(fields.Date.today(), years=ans),
        })

    def _config(self, record, style="open_peeps"):
        return self.avatars._clean_for(record, style, None)

    # --- Le tirage -------------------------------------------------------------

    def test_un_eleve_mineur_n_a_ni_barbe_ni_calvitie(self):
        eleve = self._eleve()
        config = self._config(eleve)
        self.assertIsNone(config["facialHair"], "un enfant de 9 ans a reçu une pilosité du visage")
        self.assertNotIn(config["head"], PIECES_D_ADULTE["open_peeps"]["head"])

    def test_le_meme_eleve_devenu_majeur_retrouve_le_tirage_ordinaire(self):
        eleve = self._eleve()
        eleve.student_birthdate = fields.Date.subtract(fields.Date.today(), years=40)
        temoin = render.clean_config("open_peeps", None, self.avatars._seed_of(eleve))
        self.assertEqual(self._config(eleve), temoin, "un élève majeur ne doit rien perdre du tirage")

    def test_la_plupart_des_enfants_gardent_leur_avatar(self):
        """Le tirage n'est pas refait : seul l'enfant mal servi change.

        Une pièce d'adulte sort dans un tirage sur quatre environ (pilosité 15 %,
        coiffure d'adulte 14 %). Les autres enfants doivent garder exactement
        l'avatar qu'ils avaient : c'est leur visage, et il n'avait rien de fautif.
        """
        inchanges = 0
        for i in range(40):
            eleve = self._eleve(nom="Enfant Témoin%02d" % i)
            graine = self.avatars._seed_of(eleve)
            if self._config(eleve) == render.clean_config("open_peeps", None, graine):
                inchanges += 1
        self.assertGreater(inchanges, 20, "trop d'enfants voient leur avatar refait")
        self.assertLess(inchanges, 40, "aucune pièce d'adulte n'a été retirée sur 40 enfants")

    def test_seules_les_pieces_interdites_changent(self):
        """🔴 Le garde-fou de la suite du tirage.

        Sauter le tirage d'un emplacement interdit décalerait tous les suivants :
        l'enfant recevrait un avatar ENTIÈREMENT différent de celui qu'il avait,
        au lieu du même sans la barbe. Mesuré sur une personne, à deux âges.
        """
        eleve = self._eleve()
        mineur = self._config(eleve)
        eleve.student_birthdate = fields.Date.subtract(fields.Date.today(), years=40)
        majeur = self._config(eleve)
        differents = {slot for slot in mineur if mineur[slot] != majeur[slot]}
        self.assertLessEqual(
            differents, {"facialHair", "head"},
            "l'interdiction d'une pièce a décalé le reste du tirage : %s" % sorted(differents))
        self.assertEqual(mineur["colors"], majeur["colors"])

    def test_un_adulte_qui_n_est_pas_eleve_ne_change_pas(self):
        adulte = self.env["res.partner"].create({"name": "Sylvain Beauregard-Fictif"})
        temoin = render.clean_config("open_peeps", None, self.avatars._seed_of(adulte))
        self.assertEqual(self._config(adulte), temoin)

    def test_un_eleve_sans_date_de_naissance_passe_pour_majeur(self):
        inconnu = self.env["res.partner"].create({"name": "Camille Lemieux-Fictive", "is_student": True})
        temoin = render.clean_config("open_peeps", None, self.avatars._seed_of(inconnu))
        self.assertEqual(self._config(inconnu), temoin)
        self.assertFalse(self.avatars._is_minor_student(inconnu))

    # --- L'âge qu'on lit -------------------------------------------------------

    def test_l_age_lu_est_la_majorite_pas_l_age_du_consentement(self):
        """⚠️ `privacy_consent.is_minor_child` dit « sous 14 ans » au Québec.

        Le lire ici laisserait les 14 à 17 ans avec une barbe. L'élève de cet
        essai a 16 ans : au-dessus de l'âge du consentement, sous la majorité.
        """
        ado = self._eleve(nom="Thomas Gauvreau-Fictif", ans=16)
        self.assertTrue(self.avatars._is_minor_student(ado))
        self.assertIsNone(self._config(ado)["facialHair"])
        majeur = self._eleve(nom="Élodie Charest-Fictive", ans=MAJORITE)
        self.assertFalse(self.avatars._is_minor_student(majeur))

    def test_la_veille_de_ses_dix_huit_ans_il_est_encore_mineur(self):
        demain = fields.Date.add(fields.Date.today(), days=1)
        presque = self.env["res.partner"].create({
            "name": "Jade Pelletier-Fictive", "is_student": True,
            "student_birthdate": demain - relativedelta(years=MAJORITE)})
        self.assertTrue(self.avatars._is_minor_student(presque))
        pile = self.env["res.partner"].create({
            "name": "Jade Pelletier-Fictive", "is_student": True,
            "student_birthdate": fields.Date.subtract(fields.Date.today(), years=MAJORITE)})
        self.assertFalse(self.avatars._is_minor_student(pile))

    # --- Les deux styles -------------------------------------------------------

    def test_notionists_perd_sa_barbe_et_garde_ses_coiffures(self):
        self.params.set_param("bf_avatar.style", "notionists")
        eleve = self._eleve(nom="AntoineRivard-Fictif", ans=11)
        config = self._config(eleve, "notionists")
        self.assertIsNone(config["beard"])
        # Aucune des 64 coiffures de notionists n'est chauve ni grise : le relevé
        # du 2026-11-17 n'en retire aucune, et l'enfant garde la sienne.
        temoin = render.clean_config("notionists", None, self.avatars._seed_of(eleve))
        self.assertEqual(config["hair"], temoin["hair"])

    # --- Ce qu'on ne peut pas choisir non plus ---------------------------------

    def test_une_piece_interdite_stockee_est_refusee(self):
        eleve = self._eleve(nom="Marc-Olivier Thibault-Fictif", ans=12)
        force = self.avatars._clean_for(
            eleve, "open_peeps", {"head": "noHair2", "facialHair": "full3"})
        self.assertNotEqual(force["head"], "noHair2")
        self.assertIsNone(force["facialHair"])

    def test_le_compositeur_n_offre_pas_les_pieces_interdites(self):
        usager = new_test_user(self.env, login="ecole_mineur", name="Léa Dufresne-Fictive")
        usager.partner_id.write({
            "is_student": True,
            "student_birthdate": fields.Date.subtract(fields.Date.today(), years=15)})
        slots, _colours = self.avatars._slot_payload(usager, "open_peeps")
        par_nom = {s["name"]: [v["key"] for v in s["variants"]] for s in slots}
        self.assertNotIn("facialHair", par_nom, "l'emplacement interdit est resté au compositeur")
        self.assertFalse(set(par_nom["head"]) & PIECES_D_ADULTE["open_peeps"]["head"])

    # --- Le droit de lecture ---------------------------------------------------

    def test_un_visiteur_du_portail_peut_lire_l_avatar_d_un_eleve(self):
        """🔴 `student_birthdate` est réservé à `base.group_user`.

        L'avatar d'un contact se calcule pour QUI REGARDE, visiteur du portail
        compris : sans le sudo, la lecture lèverait sur les pages mêmes que ce
        module protège.
        """
        eleve = self._eleve(nom="Rosalie Brodeur-Fictive", ans=8)
        portail = new_test_user(
            self.env, login="ecole_portail", name="Parent Fictif", groups="base.group_portal")
        avatars = self.env["bf.avatar"].with_user(portail)
        self.assertTrue(avatars._is_minor_student(eleve.with_user(portail)))

    # --- Les autres porteurs d'avatar ------------------------------------------

    def test_l_usager_d_un_eleve_est_protege_par_son_contact(self):
        usager = new_test_user(self.env, login="ecole_eleve", name="Samuel Nadeau-Fictif")
        usager.partner_id.write({
            "is_student": True,
            "student_birthdate": fields.Date.subtract(fields.Date.today(), years=10)})
        self.assertTrue(self.avatars._is_minor_student(usager))
        # ⚠️ Le tirage part TOUJOURS du contact : `_seed_of` lit
        # `_avatar_name_field`, que `res.users` n'a pas. `res_users.py` passe
        # `person.partner_id` partout, et c'est le contrat.
        self.assertIsNone(self._config(usager.partner_id)["facialHair"])
