# -*- coding: utf-8 -*-
import base64
import io

from odoo.tests import TransactionCase


def png_octets(largeur=40, hauteur=30, couleur=(200, 220, 240)):
    from PIL import Image
    tampon = io.BytesIO()
    Image.new("RGB", (largeur, hauteur), couleur).save(tampon, format="PNG")
    return tampon.getvalue()


class CasPlan(TransactionCase):
    """Un plan de 1000 × 800 cm, pas de 25, deux zones, trois éléments."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.gestion = Users.create({
            "name": "Gestion des plans", "login": "plan_gestion",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id,
                                  cls.env.ref("bf_floorplan.group_bf_floorplan_manager").id])],
        })
        cls.lecteur = Users.create({
            "name": "Lecteur de plans", "login": "plan_lecteur",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.Plan = cls.env["bf.floorplan"].with_user(cls.gestion)
        cls.Zone = cls.env["bf.floorplan.zone"].with_user(cls.gestion)
        cls.Element = cls.env["bf.floorplan.element"].with_user(cls.gestion)
        cls.Lien = cls.env["bf.floorplan.lien"].with_user(cls.gestion)
        cls.plan = cls.Plan.create({
            "name": "Banc", "batiment": "Atelier", "etage": "1",
            "largeur": 1000.0, "profondeur": 800.0, "pas": 25.0,
        })
        cls.z_ouvert = cls.Zone.create({
            "plan_id": cls.plan.id, "name": "Aire ouverte", "genre": "ouvert",
            "x": 0.0, "y": 0.0, "w": 600.0, "h": 800.0, "capacite": 4,
        })
        cls.z_tech = cls.Zone.create({
            "plan_id": cls.plan.id, "name": "Local technique", "genre": "technique",
            "x": 600.0, "y": 0.0, "w": 400.0, "h": 400.0,
        })
        cls.employe = cls.env["hr.employee"].create({"name": "Personne du banc"})
        cls.poste = cls.Element.create({
            "plan_id": cls.plan.id, "name": "P-01", "genre": "poste",
            "x": 100.0, "y": 100.0, "employee_id": cls.employe.id,
        })
        cls.commutateur = cls.Element.create({
            "plan_id": cls.plan.id, "name": "SW-01", "genre": "commutateur",
            "x": 700.0, "y": 100.0,
        })
        cls.borne = cls.Element.create({
            "plan_id": cls.plan.id, "name": "AP-01", "genre": "borne",
            "x": 300.0, "y": 500.0,
        })
        cls.lien = cls.Lien.create({
            "plan_id": cls.plan.id, "src_id": cls.commutateur.id,
            "dst_id": cls.borne.id, "genre": "reseau", "name": "port 3",
        })

    @classmethod
    def fond_b64(cls):
        return base64.b64encode(png_octets())
