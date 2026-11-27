"""Aucun gabarit livré ne doit donner prise au normaliseur de tableaux d'Odoo.

Le cœur Odoo 18.0.20261112 ajoute à l'éditeur HTML `populateTableSpans`
(`html_editor/static/src/main/table/table_plugin.js`). À chaque normalisation,
pour CHAQUE tableau du contenu édité :

1. il bâtit la matrice logique des cellules en tenant compte des `colspan` et
   `rowspan` ;
2. il RETIRE ces deux attributs de chaque cellule ;
3. il insère une cellule vide (`<td><div><br></div></td>`) partout où la
   matrice n'a rien, jusqu'à la largeur logique la plus grande.

La signature stockée n'en souffre pas, et le serveur ne voit rien : c'est le
composeur, dans le navigateur, qui casse la signature au moment où l'on écrit
le courriel. Constaté sur des courriels réels :
la rangée de boutons, étalée par `colspan="3"`, s'est retrouvée coincée dans
la colonne du logo.

L'invariant qui rend le normaliseur INOFFENSIF est simple, et c'est lui qu'on
garde : aucun `colspan`, aucun `rowspan`, et dans chaque tableau, des rangées
qui ont toutes le même nombre de cellules. Pour ne pas se contenter de
l'énoncer, `_normaliser` porte l'algorithme d'Odoo en Python, ligne pour ligne,
et l'essai exige qu'il ne change RIEN au rendu.

Les essais comptent ce qu'ils ont mesuré : une garde qui ne trouve plus de
tableau à éprouver doit échouer, pas passer au vert.
"""

from lxml import html as lxml_html

from odoo.tests import TransactionCase, tagged

LIVRES = (
    "bf_signature_card", "bf_signature_banner", "bf_signature_two_columns",
    "bf_signature_plain", "bf_signature_compact", "bf_signature_minimal",
)

# Ceux qui rendent au moins un tableau. `plain` n'en a jamais eu.
AVEC_TABLEAU = tuple(x for x in LIVRES if x != "bf_signature_plain")


def _rangees(table):
    """Les rangées PROPRES d'un tableau, comme `HTMLTableElement.rows`.

    Les rangées d'un tableau imbriqué n'en font pas partie : le normaliseur
    traite chaque tableau à part.
    """
    rangees = []
    for enfant in table:
        if not isinstance(enfant.tag, str):
            continue
        if enfant.tag == "tr":
            rangees.append(enfant)
        elif enfant.tag in ("thead", "tbody", "tfoot"):
            rangees.extend(e for e in enfant if e.tag == "tr")
    return rangees


def _cellules(rangee):
    return [e for e in rangee if isinstance(e.tag, str) and e.tag in ("td", "th")]


def _normaliser(racine):
    """`populateTableSpans` du cœur 18.0.20261112, porté tel quel.

    Rend le nombre de cellules insérées et d'attributs retirés : zéro et zéro
    pour un gabarit qui sort intact du composeur.
    """
    inserees = retires = 0
    for table in racine.iter("table"):
        rangees = _rangees(table)
        matrice = [dict() for _ in rangees]
        largeur = 0
        for r, rangee in enumerate(rangees):
            c = 0
            for cellule in _cellules(rangee):
                while c in matrice[r]:
                    c += 1
                rowspan = int(cellule.get("rowspan") or 1)
                colspan = int(cellule.get("colspan") or 1)
                for dr in range(rowspan):
                    if r + dr >= len(matrice):
                        matrice.append({})
                    for dc in range(colspan):
                        matrice[r + dr][c + dc] = cellule if (dr, dc) == (0, 0) else None
                for attribut in ("rowspan", "colspan"):
                    if attribut in cellule.attrib:
                        del cellule.attrib[attribut]
                        retires += 1
                c += colspan
                largeur = max(largeur, c)
        for r, rangee in enumerate(rangees):
            for c in range(largeur):
                # `is None`, jamais la valeur de vérité : un élément lxml
                # SANS ENFANT ÉLÉMENT est « faux », et toute cellule de
                # texte seul passerait pour une cellule à insérer. Absente
                # ou couverte par un span (None), le JS insère (`!entry`).
                if matrice[r].get(c) is None:
                    inserees += 1
    return inserees, retires


@tagged("post_install", "-at_install")
class SansColspanCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({
            "name": "Société Cellules",
            "website": "https://exemple-cellules.test",
        })
        # TOUT ce qui ajoute une rangée est rempli : logo, slogan,
        # avertissement, liens. Une rangée absente ne peut rien casser, donc
        # ne prouverait rien.
        cls.company.write({
            "bf_signature_accent_color": "#29ABE1",
            "bf_signature_text_color": "#2D3031",
            "bf_signature_tollfree": "1-800-555-0100",
            "bf_signature_logo_url": "https://exemple-cellules.test/logo.png",
            "bf_signature_tagline": "Un slogan de société",
            "bf_signature_disclaimer": "<p>Ce courriel est confidentiel.</p>",
        })
        cls.user = cls.env["res.users"].with_context(
            no_reset_password=True).create({
                "name": "Personne Cellules",
                "login": "cellules.person@test.invalid",
                "email": "personne@exemple-cellules.test",
                "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])],
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            })
        cls.env["bf.signature.link"].create([{
            "name": "Prendre rendez-vous", "sequence": 10,
            "target_url": "https://exemple-cellules.test/agenda",
            "user_id": cls.user.id,
        }, {
            "name": "M'envoyer un fichier", "sequence": 20,
            "target_url": "https://exemple-cellules.test/depot",
            "company_id": cls.company.id,
        }])

    def _rendre(self, xmlid):
        template = self.env.ref(f"bf_multi_company_email.{xmlid}")
        rendu = self.user._bf_render_with(template, self.company) or ""
        self.assertTrue(rendu.strip(), f"{xmlid} : rendu vide")
        return rendu

    def _eprouver(self, rendu, etiquette):
        """Rend le nombre de tableaux éprouvés."""
        racine = lxml_html.fragment_fromstring(rendu, create_parent="div")
        tables = list(racine.iter("table"))
        for table in tables:
            longueurs = {len(_cellules(r)) for r in _rangees(table)}
            self.assertLessEqual(
                len(longueurs), 1,
                f"{etiquette} : un tableau a des rangées de longueurs "
                f"différentes {sorted(longueurs)} ; le composeur les bourrera "
                "de cellules vides")
        for cellule in racine.iter("td", "th"):
            for attribut in ("colspan", "rowspan"):
                self.assertNotIn(
                    attribut, cellule.attrib,
                    f"{etiquette} : {attribut} sur une cellule ; le composeur "
                    "le retirera")
        inserees, retires = _normaliser(racine)
        self.assertEqual(
            (inserees, retires), (0, 0),
            f"{etiquette} : le normaliseur d'Odoo insérerait {inserees} "
            f"cellule(s) et retirerait {retires} attribut(s)")
        return len(tables)

    def test_every_shipped_template_survives_the_editor(self):
        for xmlid in LIVRES:
            with self.subTest(gabarit=xmlid):
                tables = self._eprouver(self._rendre(xmlid), xmlid)
                if xmlid in AVEC_TABLEAU:
                    self.assertGreater(
                        tables, 0,
                        f"{xmlid} ne rend plus de tableau : la garde n'a rien "
                        "mesuré")

    def test_card_buttons_span_the_card_width(self):
        """La raison d'être de la carte en deux tableaux.

        Les boutons, le filet, le slogan et l'avertissement doivent vivre
        dans un tableau de la même largeur que la carte, et pas dans une
        cellule de la rangée du logo.
        """
        racine = lxml_html.fragment_fromstring(
            self._rendre("bf_signature_card"), create_parent="div")
        bouton = next(
            (a for a in racine.iter("a")
             if "exemple-cellules.test/agenda" in (a.get("href") or "")),
            None)
        self.assertIsNotNone(bouton, "le bouton n'est pas rendu")
        enveloppe = list(bouton.iterancestors("table"))[-1]
        self.assertEqual(
            enveloppe.get("width"), "520",
            "la carte n'est plus enveloppée dans un tableau de 520 px")
        rangees = _rangees(enveloppe)
        self.assertEqual(len(rangees), 2, "l'enveloppe n'a plus deux rangées")
        logo = next(racine.iter("img"))
        self.assertIn(rangees[0], list(logo.iterancestors("tr")),
                      "le logo n'est plus dans la première rangée")
        self.assertIn(
            rangees[1], list(bouton.iterancestors("tr")),
            "les boutons ne sont pas dans la seconde rangée de l'enveloppe : "
            "ils vivent dans une cellule de la rangée du logo")
        # Et chaque cellule entre le bouton et l'enveloppe est seule dans sa
        # rangée, sauf la rangée des boutons elle-même : aucune n'est la
        # colonne du logo ou du texte.
        for td in bouton.iterancestors("td"):
            tr = td.getparent()
            if tr is None or tr.getparent() is None:
                continue
            if any(c is not td and any(x.tag == "a" for x in c.iter("a"))
                   for c in _cellules(tr)):
                continue  # la rangée des boutons : des cellules sœurs à lien
            self.assertEqual(
                len(_cellules(tr)), 1,
                "un ancêtre du bouton partage sa rangée avec une autre colonne")

    def test_the_stored_signature_survives_the_editor(self):
        """Ce que le composeur lit vraiment : la colonne `signature`."""
        mesurees = 0
        for xmlid in AVEC_TABLEAU:
            with self.subTest(gabarit=xmlid):
                self.user.bf_signature_template_id = self.env.ref(
                    f"bf_multi_company_email.{xmlid}")
                self.user._bf_apply_company_signature(force=True)
                self.env.flush_all()
                self.env.cr.execute(
                    "SELECT signature FROM res_users WHERE id = %s",
                    (self.user.id,))
                stockee = self.env.cr.fetchone()[0] or ""
                self.assertTrue(stockee, f"{xmlid} : signature non stockée")
                self.assertGreater(
                    self._eprouver(stockee, f"{xmlid} (stockée)"), 0)
                # Ce que lisent les notifications ET le composeur quand
                # l'identité d'envoi n'a pas sa propre signature.
                rendue = self.user.with_context(
                    allowed_company_ids=[self.company.id]).bf_signature_rendered or ""
                self.assertTrue(rendue, f"{xmlid} : rendu par société vide")
                self.assertGreater(
                    self._eprouver(rendue, f"{xmlid} (par société)"), 0)
                mesurees += 1
        self.assertEqual(mesurees, len(AVEC_TABLEAU))

    def test_french_rendering_survives_the_editor(self):
        """La traduction de `body_html` est une copie ENTIÈRE du corps.

        Après un `-u`, la clé `fr_CA` gardait
        l'ancien corps à colspan, rechargé depuis `i18n/fr_CA.po`, et tout
        rendu lancé depuis une interface en français le ramenait. On charge
        donc le catalogue LIVRÉ, comme une montée, et on rend en français.
        """
        self.env["res.lang"]._activate_lang("fr_CA")
        # Partir de l'état que laisse la migration (la source seule) : l'essai
        # mesure le CATALOGUE livré, pas une copie qu'une base a pu garder
        # d'une version précédente. Un `msgstr` vide n'efface rien en base ;
        # c'est la migration qui retire la copie périmée, et son propre essai
        # le garde.
        ids = [self.env.ref(f"bf_multi_company_email.{x}").id for x in AVEC_TABLEAU]
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = jsonb_build_object("
            "'en_US', body_html->'en_US') WHERE id IN %s", (tuple(ids),))
        self.env.invalidate_all()
        self.env["ir.module.module"]._load_module_terms(
            ["bf_multi_company_email"], ["fr_CA"], overwrite=True)
        francais = self.user.with_context(lang="fr_CA")
        for xmlid in AVEC_TABLEAU:
            with self.subTest(gabarit=xmlid):
                template = self.env.ref(f"bf_multi_company_email.{xmlid}")
                corps = template.with_context(lang="fr_CA").body_html or ""
                self.assertNotIn(
                    "colspan=", corps,
                    f"{xmlid} : le corps rendu en français porte un colspan ; "
                    "le catalogue livré le réintroduit")
                # `_bf_render_with` rend dans la langue du contexte du
                # GABARIT : c'est lui qui porte `lang`, pas la personne.
                rendu = francais._bf_render_with(
                    template.with_context(lang="fr_CA"), self.company) or ""
                self.assertGreater(self._eprouver(rendu, f"{xmlid} (fr_CA)"), 0)

    def test_migration_rewrites_what_carries_a_colspan(self):
        """La passe de la 18.0.5.2.0, sur une signature à l'ancienne forme."""
        import importlib.util
        import os

        import odoo.addons.bf_multi_company_email as module

        chemin = os.path.join(os.path.dirname(module.__file__), "migrations",
                              "18.0.5.2.0", "end-migration.py")
        spec = importlib.util.spec_from_file_location("migration_sans_colspan", chemin)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        self.company.bf_signature_template_id = self.env.ref(
            "bf_multi_company_email.bf_signature_card")
        self.company.bf_signature_auto_apply = True
        ancienne = ('<table><tbody><tr><td>logo</td><td>|</td><td>texte</td></tr>'
                    '<tr><td colspan="3">boutons</td></tr></tbody></table>')
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE res_users SET signature = %s, bf_signature_rendered = "
            "jsonb_build_object(%s, %s::text) WHERE id = %s",
            (ancienne, str(self.company.id), ancienne, self.user.id))
        self.env.invalidate_all()
        # Une traduction périmée du corps, comme celle que laisse un `-u`.
        carte = self.env.ref("bf_multi_company_email.bf_signature_card")
        self.env.cr.execute(
            "UPDATE mail_template SET body_html = body_html || "
            "jsonb_build_object('fr_CA', %s::text) WHERE id = %s",
            (ancienne, carte.id))
        self.env.invalidate_all()

        # Une autre personne de la même société, signature saisie à la main,
        # SANS colspan : la passe ne doit pas la toucher (pas de `force`).
        voisine = self.env["res.users"].with_context(
            no_reset_password=True).create({
                "name": "Voisine Cellules",
                "login": "voisine.cellules@test.invalid",
                "company_id": self.company.id,
                "company_ids": [(6, 0, [self.company.id])],
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
            })
        propre = "<p>Ma signature à moi</p>"
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE res_users SET signature = %s, bf_signature_rendered = "
            "jsonb_build_object(%s, %s::text) WHERE id = %s",
            (propre, str(self.company.id), propre, voisine.id))
        self.env.invalidate_all()

        migration._passe(self.env.cr)
        self.env.invalidate_all()

        self.env.cr.execute(
            "SELECT signature FROM res_users WHERE id = %s", (voisine.id,))
        self.assertEqual(self.env.cr.fetchone()[0], propre,
                         "la passe a réécrit une signature sans colspan")

        self.env.cr.execute(
            "SELECT signature, bf_signature_rendered::text FROM res_users "
            "WHERE id = %s", (self.user.id,))
        signature, rendue = self.env.cr.fetchone()
        self.assertNotIn("colspan=", signature or "")
        self.assertNotIn("colspan=", rendue or "")
        self.assertIn("exemple-cellules.test/agenda", signature or "",
                      "la passe a vidé la signature au lieu de la refaire")
        self.env.cr.execute(
            "SELECT body_html ? 'fr_CA', body_html ? 'en_US' FROM mail_template "
            "WHERE id = %s", (carte.id,))
        self.assertEqual(self.env.cr.fetchone(), (False, True),
                         "la traduction périmée du corps n'a pas été retirée")
