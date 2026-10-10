import requests

from odoo import _, fields, models
from odoo.exceptions import UserError
from .gen_portees import LIBELLE_SANTE, PORTEE_SANTE


OFF_PRODUCT_URL = "https://world.openfoodfacts.org/api/v2/product/%s.json"
OFF_FIELDS = "product_name,brands,nutriments"
OFF_USER_AGENT = "HealthyFox/2.5 (+https://symbifox.com)"


class HealthFood(models.Model):
    _name = "health.food"
    # Verrou de Gen (voir models/gen_portees.py).
    _gen_scope = PORTEE_SANTE
    _gen_scope_label = LIBELLE_SANTE
    _description = "Aliment"
    _order = "name asc"

    name = fields.Char(string="Nom", required=True)
    brand = fields.Char(string="Marque")
    serving_size = fields.Float(string="Portion", digits=(10, 2), default=100.0)
    serving_unit = fields.Char(string="Unité de portion", default="g")
    calories = fields.Float(string="Calories (kcal)", digits=(10, 2))
    protein_g = fields.Float(string="Protéines (g)", digits=(10, 2))
    carbs_g = fields.Float(string="Glucides (g)", digits=(10, 2))
    fat_g = fields.Float(string="Lipides (g)", digits=(10, 2))
    fiber_g = fields.Float(string="Fibres (g)", digits=(10, 2))
    sodium_mg = fields.Float(string="Sodium (mg)", digits=(10, 2))
    barcode = fields.Char(string="Code-barres", index=True)

    def action_lookup_barcode(self):
        """Fill the food from Open Food Facts using its barcode (per 100 g/mL)."""
        self.ensure_one()
        if not self.barcode:
            raise UserError(_("Veuillez saisir un code-barres."))
        try:
            resp = requests.get(
                OFF_PRODUCT_URL % self.barcode.strip(),
                params={"fields": OFF_FIELDS},
                headers={"User-Agent": OFF_USER_AGENT},
                timeout=10,
            )
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException as exc:
            raise UserError(_("Impossible de joindre Open Food Facts : %s") % exc)
        except ValueError:
            raise UserError(_("Réponse invalide d'Open Food Facts."))

        if not payload or payload.get("status") != 1 or not payload.get("product"):
            raise UserError(
                _("Aucun produit trouvé pour le code-barres %s.") % self.barcode
            )

        product = payload["product"]
        nutriments = product.get("nutriments") or {}

        def num(key):
            try:
                return float(nutriments.get(key))
            except (TypeError, ValueError):
                return 0.0

        vals = {
            # Open Food Facts nutriments are expressed per 100 g/mL.
            "serving_size": 100.0,
            "calories": num("energy-kcal_100g"),
            "protein_g": num("proteins_100g"),
            "carbs_g": num("carbohydrates_100g"),
            "fat_g": num("fat_100g"),
            "fiber_g": num("fiber_100g"),
            # OFF reports sodium in g/100 g → convert to mg.
            "sodium_mg": num("sodium_100g") * 1000.0,
        }
        if product.get("product_name"):
            vals["name"] = product["product_name"]
        if product.get("brands"):
            vals["brand"] = product["brands"]
        self.write(vals)
        return True
