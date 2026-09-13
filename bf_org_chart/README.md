# Org charts: the drawing engine (`bf_org_chart`)

This module draws nobody's org chart. It provides the **drawing**; satellite
modules provide the **data**.

## The contract

A model that wants to draw itself inherits `bf.org.chart.source` and returns a
`Carte`: a list of **boxes** and a list of **edges**. It computes no
coordinates, and it knows nothing about SVG or PDF.

```python
class ResPartner(models.Model):
    _name = "res.partner"
    _inherit = ["res.partner", "bf.org.chart.source"]

    def _org_chart_genres(self):
        return super()._org_chart_genres() + [
            {"code": "people", "libelle": "People", "sequence": 10}]

    def _org_chart_carte(self, code):
        carte = modele.Carte(titre=self.display_name)
        carte.boites.append(modele.Boite(cle="p1", titre="Jeanne", sous_titre="CEO"))
        carte.aretes.append(modele.Arete(de="p1", vers="p2", etiquette="60 %"))
        return carte
```

`_org_chart_genres` **adds** to `super()`'s result rather than replacing it.
Several satellites can equip the same model, and one that overwrote the list
would make another's button disappear without saying so.

## One geometry, two renderings

`disposition.disposer(carte)` returns a `Plan`: coordinates, label wrapping,
edge routing. The on-screen SVG and the PDF both draw **that plan**, computing
nothing of their own. That is what guarantees the printed page is the one you
were looking at.

Two layouts, chosen by the shape of the data rather than by the caller:

| Shape | Layout | What you get |
|---|---|---|
| every box has at most one parent | **tree** | parent centred over its children |
| some box has several parents | **layered** | levels, barycentre ordering, reserved lanes |

An edge that skips a level gets a **relay**: a reserved column at the level it
crosses. Without it the line would pass behind a box and its label would vanish
underneath. Labels are drawn **after** the boxes for the same reason: a hidden
label on an ownership chart is a hidden percentage.

A loop in the data does not stop the drawing. The edge that closes it is set
aside and `Plan.avertissements` says so, in plain words, on the page.

## Two routes

| Route | What it returns |
|---|---|
| `/bf/organigramme/<model>/<id>/<kind>` | the page: the SVG and a PDF button |
| `/bf/organigramme/<model>/<id>/<kind>/pdf` | the vector PDF |

Both check **two** things before rendering anything: that the model signed the
contract, and that the user may read the record. A route that accepts a model
name in its path is otherwise an arbitrary read of the database.

## What it does not do

* It stores nothing. The drawing is recomputed on every open.
* It does not edit. To drag a card around, use Odoo 18's own `hierarchy` view,
  which `bf_org_chart_people` installs alongside.
* It refuses past 400 boxes rather than taking an Odoo worker hostage.

## Requirements

Odoo 18. `reportlab`, already an Odoo dependency, does the PDF. Lexend ships
with the module under the SIL Open Font License 1.1.
