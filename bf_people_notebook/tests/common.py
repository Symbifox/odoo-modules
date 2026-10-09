"""Le foyer d'essai : trois membres ordinaires, une administratrice, un compte portail.

🔴 Aucun essai d'isolation ne tourne en superutilisateur (uid 1 / sudo) : il
contourne les règles et prouverait seulement que le code marche. Carole est administratrice
FONCTIONNELLE (base.group_system) : elle aussi doit rester à la porte d'une fiche.
"""
import base64
import io

from PIL import Image

from odoo.tests import TransactionCase, new_test_user

SECRET = "Ondine"


def petite_photo(couleur="red"):
    tampon = io.BytesIO()
    Image.new("RGB", (4, 4), couleur).save(tampon, "PNG")
    return base64.b64encode(tampon.getvalue())


class FoyerCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=False))
        # Le profil de foyer donne à chaque membre la création de contacts
        # (base.group_partner_manager) : les membres d'essai l'ont aussi.
        membre = "base.group_user,base.group_partner_manager"
        cls.alex = new_test_user(cls.env, login="foyer-alex", name="Alex Essai", groups=membre)
        cls.sam = new_test_user(cls.env, login="foyer-sam", name="Sam Essai", groups=membre)
        cls.lee = new_test_user(cls.env, login="foyer-lee", name="Lee Essai",
                                groups=membre + ",base.group_no_one")
        cls.carole = new_test_user(cls.env, login="foyer-carole", name="Carole Essai",
                                   groups="base.group_user,base.group_system")
        cls.portail = new_test_user(cls.env, login="foyer-portail", name="Portail Essai",
                                    groups="base.group_portal")
        cls.Person = cls.env["bf.people.person"]
        cls.gaspesie = cls.env["bf.people.occasion"].with_user(cls.alex).create(
            {"name": "Gaspésie 2026"})
        cls.jazz = cls.env["bf.people.interest"].with_user(cls.alex).create(
            {"name": "Festival de jazz"})
        cls.fiche = cls.Person.with_user(cls.alex).create({
            "name": SECRET, "name_uncertain": True,
            "description": "casque de vélo rouge",
            "place": "Café du quai", "city": "Rimouski",
            "occasion_id": cls.gaspesie.id, "interest_ids": [(6, 0, cls.jazz.ids)],
            "likes": "le kayak de mer",
            "knows_about_me": "je prépare un marathon",
            "reconnect_detail": "on a parlé de la traversée du Bic",
            "image_1920": petite_photo(),
        })

    def as_(self, user):
        return self.Person.with_user(user)

    def lu_par(self, record, user):
        """Le cache garde ce qu'une autre personne a lu ou écrit, et un champ en cache
        se rend sans contrôle d'accès : on le vide avant de lire au nom de quelqu'un."""
        self.env.invalidate_all()
        return record.with_user(user)

    def partager_avec_sam(self):
        self.fiche.with_user(self.alex).write({"shared_user_ids": [(6, 0, self.sam.ids)]})
