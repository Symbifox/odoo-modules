"""Le canari : rien de ce que Lee peut lire ne porte un mot de la fiche d'Alex.

Pour chaque modèle que Lee peut lire :
ses champs texte, ses Many2one, ses Reference et son display_name. La fiche a
traversé tout ce qu'une vie normale lui fait : message au fil, pièce jointe, photo,
rappel, note liée (dont une note PARTAGÉE, que Lee lit), contact fait à partir d'elle
(sous un autre nom), recherche, partage avec Sam puis retrait.
"""
import base64

from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import FoyerCase, petite_photo

SECRETS = ("Zéphyrin", "luthier-canari", "Tadoussac-canari", "théremine-canari")


@tagged("post_install", "-at_install")
class TestCanari(FoyerCase):

    def test_canari_rien_de_lisible_par_lee_ne_porte_la_fiche_d_alex(self):
        Alex = self.as_(self.alex)
        occasion = self.env["bf.people.occasion"].with_user(self.alex).create({"name": "Tadoussac-canari"})
        fiche = Alex.create({
            "name": "Zéphyrin", "name_uncertain": True, "description": "luthier-canari",
            "occasion_id": occasion.id, "image_1920": petite_photo("green"),
            "interest_ids": [(0, 0, {"name": "théremine-canari"})],
            "likes": "luthier-canari", "knows_about_me": "luthier-canari",
        })
        fiche.message_post(body="Revu Zéphyrin, luthier-canari", message_type="comment",
                           subtype_xmlid="mail.mt_note")
        self.env["ir.attachment"].with_user(self.alex).create({
            "name": "Zéphyrin.txt", "datas": base64.b64encode(b"luthier-canari"),
            "res_model": "bf.people.person", "res_id": fiche.id})
        self.env["mail.activity"].with_user(self.alex).create({
            "res_model_id": self.env["ir.model"]._get_id("bf.people.person"),
            "res_id": fiche.id, "user_id": self.lee.id, "summary": "Écrire à Zéphyrin"})
        # Une note partagée liée à la fiche : Lee lit la note, pas le nom de la fiche.
        self.env["bf.note"].with_user(self.alex).create({
            "body": "<p>Rappel commun</p>", "is_shared": True,
            "link_ids": [(0, 0, {"res_model": "bf.people.person", "res_id": fiche.id})]})
        fiche.write({"shared_user_ids": [(6, 0, self.sam.ids)]})
        fiche.write({"shared_user_ids": [(5, 0, 0)]})
        self.env.flush_all()
        self.env.invalidate_all()

        fuites, lus = [], 0
        for nom in sorted(self.env.registry):
            Modele = self.env[nom].with_user(self.lee)
            if Modele._abstract or Modele._transient or not Modele._auto:
                continue
            if not Modele.has_access("read"):
                continue
            champs = [
                f for f, champ in Modele._fields.items()
                if champ.type in ("char", "text", "html", "many2one", "reference")
                and (not champ.groups or self.lee.has_groups(champ.groups))
                and f != "display_name"
            ]
            try:
                with self.env.cr.savepoint(), mute_logger("odoo.sql_db", "odoo.models"):
                    lignes = Modele.search_read([], champs + ["display_name"], limit=500)
            except Exception:  # noqa: BLE001 un modèle qui refuse est un modèle qui ne fuit pas
                continue
            lus += 1
            for ligne in lignes:
                for champ, valeur in ligne.items():
                    texte = valeur[1] if isinstance(valeur, (list, tuple)) and len(valeur) == 2 else valeur
                    if isinstance(texte, str) and any(s in texte for s in SECRETS):
                        fuites.append(f"{nom}({ligne['id']}).{champ}")
        self.assertGreater(lus, 20, "le canari n'a presque rien lu : il ne prouve rien")
        self.assertFalse(fuites, "la fiche d'Alex se lit par : %s" % ", ".join(fuites))
