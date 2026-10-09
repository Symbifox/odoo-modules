"""La fiche elle-même : un seul indice, le prénom incertain, la date approximative, et
les trois gestes (faire un contact, me rappeler de renouer, faire une fiche d'une note).
Joués dans le rôle visé, la création ET la mise à jour."""
from datetime import date, timedelta

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import new_test_user, tagged

from .common import SECRET, FoyerCase, petite_photo


@tagged("post_install", "-at_install")
class TestFiche(FoyerCase):

    # ------------------------------------------------------------ un seul indice
    def test_un_seul_indice_suffit(self):
        for valeurs in ({"name": "Marco"}, {"description": "la cycliste du traversier"},
                        {"image_1920": petite_photo("blue")}):
            fiche = self.as_(self.alex).create(valeurs)
            self.assertTrue(fiche.display_name)

    def test_sans_aucun_indice_la_fiche_est_refusee(self):
        with self.assertRaises(ValidationError):
            self.as_(self.alex).create({"place": "Un café"})
        with self.assertRaises(ValidationError):
            self.fiche.with_user(self.alex).write(
                {"name": False, "description": False, "image_1920": False})

    def test_le_nom_incertain_porte_un_point_d_interrogation(self):
        self.assertEqual(self.fiche.with_user(self.alex).display_name, f"{SECRET} ?")
        self.fiche.with_user(self.alex).write({"name_uncertain": False})
        self.assertEqual(self.fiche.with_user(self.alex).display_name, SECRET)

    def test_sans_nom_la_description_tient_lieu_de_nom(self):
        fiche = self.as_(self.alex).create({"description": "la cycliste du traversier"})
        self.assertEqual(fiche.display_name, "la cycliste du traversier")

    def test_la_date_peut_etre_approximative(self):
        fiche = self.as_(self.alex).with_context(lang="en_US").create(
            {"name": "Ana", "met_on": date(2026, 3, 14), "met_on_precision": "month"})
        self.assertEqual(fiche.met_on_display, "March 2026")
        fiche.write({"met_on_precision": "year"})
        self.assertEqual(fiche.met_on_display, "2026")
        fiche.write({"met_on": False})
        self.assertFalse(fiche.met_on_display)

    # ------------------------------------------------------------ faire un contact
    def test_faire_un_contact_ne_sort_que_le_nom_la_ville_le_pays_et_la_photo(self):
        canada = self.env.ref("base.ca")
        self.fiche.with_user(self.alex).write({"country_id": canada.id})
        action = self.fiche.with_user(self.alex).action_make_contact()
        contact = self.fiche.with_user(self.alex).partner_id
        self.assertEqual(action["res_id"], contact.id)
        self.assertEqual(contact.name, SECRET)
        self.assertEqual(contact.city, "Rimouski")
        self.assertEqual(contact.country_id, canada)
        self.assertTrue(contact.image_1920)
        self.assertFalse(contact.comment)
        self.assertFalse(contact.category_id)
        # Le contact est du foyer : Sam le voit. La fiche, elle, reste privée.
        self.assertEqual(self.lu_par(contact, self.sam).read(["name"])[0]["name"], SECRET)
        self.assertFalse(self.as_(self.sam).search([]))
        # Une seconde fois ouvre le même contact, sans en refaire un.
        self.assertEqual(self.fiche.with_user(self.alex).action_make_contact()["res_id"], contact.id)

    def test_faire_un_contact_sans_adresse_courriel(self):
        # Un membre du foyer sans adresse : message_post refusait (« configure the
        # sender's email address »), vu au navigateur.
        self.alex.partner_id.email = False
        self.fiche.with_user(self.alex).action_make_contact()
        self.assertTrue(self.fiche.with_user(self.alex).partner_id)

    def test_sans_le_droit_de_creer_des_contacts_on_est_refuse(self):
        dana = new_test_user(self.env, login="foyer-dana", groups="base.group_user")
        fiche = self.as_(dana).create({"name": "Lou"})
        with self.assertRaises(AccessError):
            fiche.action_make_contact()
        self.assertFalse(self.lu_par(fiche, dana).partner_id)

    def test_seule_la_proprietaire_fait_un_contact(self):
        self.partager_avec_sam()
        with self.assertRaises(AccessError):
            self.fiche.with_user(self.sam).action_make_contact()

    # ------------------------------------------------------------ renouer
    def test_me_rappeler_de_renouer_passe_par_l_assistant_d_activite(self):
        action = self.fiche.with_user(self.alex).action_remind_reconnect()
        type_renouer = self.env.ref("bf_people_notebook.mail_activity_type_reconnect")
        self.assertEqual(action["res_model"], "mail.activity.schedule")
        assistant = self.env["mail.activity.schedule"].with_user(self.alex).with_context(
            action["context"]).create({})
        self.assertEqual(assistant.activity_type_id, type_renouer)
        self.assertGreater(assistant.date_deadline, fields.Date.today() + timedelta(days=80))
        assistant.action_schedule_activities()
        activite = self.fiche.with_user(self.alex).activity_ids
        self.assertEqual(activite.activity_type_id, type_renouer)
        self.assertEqual(activite.user_id, self.alex)
        self.assertIn(self.fiche, self.as_(self.alex).search([("activity_ids", "!=", False)]))

    def test_aucun_rappel_sans_le_demander(self):
        self.assertFalse(self.fiche.sudo().activity_ids)

    def test_seule_la_proprietaire_planifie_un_rappel(self):
        self.partager_avec_sam()
        with self.assertRaises(AccessError):
            self.fiche.with_user(self.sam).action_remind_reconnect()

    # ------------------------------------------------------------ d'une note à une fiche
    def test_faire_une_fiche_d_une_note(self):
        note = self.env["bf.note"].with_user(self.alex).create(
            {"body": "<p>Marco, guide de kayak au Bic</p><p>aime le jazz</p>"})
        action = note.with_user(self.alex).action_make_person_card()
        fiche = self.as_(self.alex).with_context(action["context"]).create({"name": "Marco"})
        self.assertEqual(fiche.bf_note_count, 1)
        self.assertEqual(note.with_user(self.alex).res_name, "Marco")

    def test_une_fiche_enregistree_vide_prend_le_titre_de_la_note(self):
        note = self.env["bf.note"].with_user(self.alex).create(
            {"body": "<p>La cycliste du traversier</p>"})
        action = note.with_user(self.alex).action_make_person_card()
        fiche = self.as_(self.alex).with_context(action["context"]).create({})
        self.assertEqual(fiche.description, note.name)

    def test_la_note_d_autrui_ne_se_lie_pas(self):
        note_sam = self.env["bf.note"].with_user(self.sam).create({"body": "<p>Note de Sam</p>"})
        with self.assertRaises(AccessError):
            note_sam.with_user(self.alex).action_make_person_card()
        # Même en forçant le contexte, la note de Sam ne rejoint pas la fiche d'Alex.
        fiche = self.as_(self.alex).with_context(bf_people_from_note_id=note_sam.id).create(
            {"name": "Quelqu'un"})
        self.assertFalse(self.env["bf.note.link"].sudo().search(
            [("res_model", "=", "bf.people.person"), ("res_id", "=", fiche.id)]))

    def test_la_note_partagee_d_autrui_ne_se_lie_pas_non_plus(self):
        # Alex LIT une note partagée de Sam : la garde doit tenir sur l'auteur, pas
        # seulement sur la lecture : une note privée de Sam ne le prouverait pas.
        note_sam = self.env["bf.note"].with_user(self.sam).create(
            {"body": "<p>Note commune de Sam</p>", "is_shared": True})
        self.assertTrue(self.lu_par(note_sam, self.alex).read(["name"]), "précondition")
        with self.assertRaises(AccessError):
            note_sam.with_user(self.alex).action_make_person_card()
        # Ni le titre de la note de Sam ne sert d'indice…
        with self.assertRaises(ValidationError):
            self.as_(self.alex).with_context(bf_people_from_note_id=note_sam.id).create({})
        # …ni la note ne rejoint la fiche.
        fiche = self.as_(self.alex).with_context(bf_people_from_note_id=note_sam.id).create(
            {"name": "Quelqu'un"})
        self.assertFalse(self.env["bf.note.link"].sudo().search(
            [("res_model", "=", "bf.people.person"), ("res_id", "=", fiche.id)]))

    def test_vu_la_derniere_fois_vient_des_notes(self):
        self.fiche.with_user(self.alex).write({"met_on": date(2026, 1, 5)})
        self.assertEqual(self.fiche.with_user(self.alex).last_seen, date(2026, 1, 5))
        self.env["bf.note"].with_user(self.alex).create({
            "body": "<p>Recroisé au marché</p>",
            "link_ids": [(0, 0, {"res_model": "bf.people.person", "res_id": self.fiche.id})],
        })
        self.fiche.invalidate_recordset(["last_seen"])
        self.assertEqual(self.fiche.with_user(self.alex).last_seen, fields.Date.today())

    # ------------------------------------------------------------ créer ET mettre à jour
    def test_alex_cree_puis_modifie_sa_fiche(self):
        fiche = self.as_(self.alex).create({"name": "Ana"})
        fiche.write({"place": "Refuge", "interest_ids": [(0, 0, {"name": "Escalade"})],
                     "occasion_id": self.gaspesie.id, "circle": "travel"})
        fiche.write({"name": "Anna", "interest_ids": [(0, 0, {"name": "Cuisine"})]})
        self.assertEqual(sorted(fiche.interest_ids.mapped("name")), ["Cuisine", "Escalade"])
        self.assertEqual(fiche.interest_ids.user_id, self.alex)
        # Couleur 0 = étiquette masquée sur la carte kanban.
        self.assertTrue(all(fiche.interest_ids.mapped("color")))
        fiche.action_archive()
        self.assertFalse(self.as_(self.alex).search([("id", "=", fiche.id)]))
        fiche.action_unarchive()
        fiche.unlink()
