"""Les gabarits LIVRÉS, et les PROPRIÉTÉS de leur HTML.

Les six gabarits livrés étaient déjà rendus par un essai :
`tests/test_signature.py`, `SignatureGalleryCase.test_every_shipped_template_renders`
les passe tous par l'aperçu de la galerie et exige le nom de la personne dans
chacun. Le trou n'était pas là ; il était un cran plus bas. **Rien ne mesurait
les propriétés du HTML rendu** : ni la taille du texte, ni le contraste, ni un
dégradé laissé sans repli, ni un logo servi en SVG. Rendre sans rien mesurer,
c'est ce qui a laissé une rangée de liens trois mois en production à 10 px dans
un gris à 2,85:1 de contraste, jusqu'à ce qu'un usager le signale.

Ce fichier rend donc chaque gabarit livré avec de vraies données et éprouve ce
qu'un client de messagerie en fait — il n'y a pas de navigateur au bout d'un
courriel, il y a le moteur de Word.

⚠️ Ce qu'aucun essai d'ici ne garde : la GÉOMÉTRIE. Les 520 px de la carte, les
460 px de la rangée en Arial et les 533 px qu'elle ferait avec un émoji par
bouton ont été mesurés **à la main** — au gabarit de police, puis au rendu — et
non en essai : il faudrait les polices et un moteur de rendu. Un libellé
rallongé ou une marge interne élargie peut donc faire déborder la rangée sans
qu'un seul essai rougisse. Ça se remesure à la main, et aucune garde d'ici n'en
tient lieu.
"""

import html
import json
import pathlib
import re

from odoo.tests import TransactionCase, tagged

# Les trois gabarits qui rendent la rangée de liens rapides.
AVEC_LIENS = ("bf_signature_card", "bf_signature_banner", "bf_signature_two_columns")

# Tous ceux que le module livre.
LIVRES = AVEC_LIENS + (
    "bf_signature_plain", "bf_signature_compact", "bf_signature_minimal",
)

# Les gabarits qui rendent un logo, et celui qui porte le filet dégradé. Ils
# sont nommés parce que deux gardes plus bas comptent ce qu'elles ont
# réellement éprouvé : une garde qui n'a plus rien à mesurer doit échouer, pas
# passer au vert.
AVEC_LOGO = ("bf_signature_card", "bf_signature_two_columns")
AVEC_DEGRADE = ("bf_signature_card",)

# Les gabarits qui rendent les pronoms à côté du nom. `minimal` n'en rend pas.
AVEC_PRONOMS = AVEC_LIENS + ("bf_signature_plain", "bf_signature_compact")

# Contraste minimal exigé par WCAG 2.1 niveau AA pour du texte normal. Les
# pronoms étaient à 2,85:1 (#999999 sur blanc) — le chiffre même qui condamnait
# la rangée de liens, laissé deux lignes plus haut sur la même carte.
CONTRASTE_AA = 4.5

# Plancher de taille du texte d'un bouton. 9 pt est l'unité de Word ; en
# dessous, le libellé passe sous le corps du message (Calibri 11 pt par défaut
# sous Outlook) et se lit comme une erreur d'affichage.
PLANCHER_PT = 9.0


def _en_points(valeur, unite):
    """Une taille déclarée, ramenée en points (1 px = 0,75 pt à 96 ppp)."""
    return float(valeur) * (0.75 if unite == "px" else 1.0)


def _luminance(couleur):
    """Luminance relative d'une couleur `#rgb` ou `#rrggbb`, selon WCAG 2.1."""
    brut = couleur.strip().lstrip("#")
    if len(brut) == 3:
        brut = "".join(c * 2 for c in brut)
    canaux = []
    for debut in (0, 2, 4):
        voie = int(brut[debut:debut + 2], 16) / 255.0
        canaux.append(
            voie / 12.92 if voie <= 0.04045
            else ((voie + 0.055) / 1.055) ** 2.4)
    rouge, vert, bleu = canaux
    return 0.2126 * rouge + 0.7152 * vert + 0.0722 * bleu


def _contraste(avant_plan, arriere_plan="#FFFFFF"):
    """Rapport de contraste entre deux couleurs, selon WCAG 2.1."""
    clair, sombre = sorted(
        (_luminance(avant_plan), _luminance(arriere_plan)), reverse=True)
    return (clair + 0.05) / (sombre + 0.05)


@tagged("post_install", "-at_install")
class GabaritsLivresCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({
            "name": "Société Gabarits",
            "website": "https://exemple-gabarits.test",
        })
        cls.company.write({
            "bf_signature_accent_color": "#29ABE1",
            "bf_signature_text_color": "#2D3031",
            "bf_signature_tollfree": "1-800-555-0100",
            "bf_signature_logo_url": "https://exemple-gabarits.test/logo.png",
        })
        # Les pronoms sont un champ : sans eux, la garde de lisibilité
        # ci-dessous n'aurait rien à mesurer et passerait au vert pour rien.
        cls.user = cls.env["res.users"].with_context(
            no_reset_password=True).create({
                "name": "Personne Gabarits",
                "login": "gabarits.person@test.invalid",
                "email": "personne@exemple-gabarits.test",
                "company_id": cls.company.id,
                "company_ids": [(6, 0, [cls.company.id])],
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
            })
        # Écrit après la création : avec `bf_preferred_name_signature`
        # installé, le champ est un `related` vers le contact (readonly=False),
        # et le chemin d'écriture est le même des deux côtés.
        cls.user.bf_signature_pronouns = "iel"

        Link = cls.env["bf.signature.link"]
        # Des libellés de la longueur de ceux de la vraie vie : c'est la
        # longueur qui décide si trois boutons tiennent dans la carte.
        cls.liens = Link.create([{
            "name": "Rencontre instantanée", "icon": "🎥", "sequence": 10,
            "target_url": "https://exemple-gabarits.test/rencontre",
            "user_id": cls.user.id,
        }, {
            "name": "Prendre rendez-vous", "icon": "📅", "sequence": 20,
            "target_url": "https://exemple-gabarits.test/agenda",
            "user_id": cls.user.id,
        }, {
            "name": "M'envoyer un fichier", "icon": "📎", "sequence": 30,
            "target_url": "https://exemple-gabarits.test/depot",
            "company_id": cls.company.id,
        }])

    def _rendre(self, xmlid):
        template = self.env.ref(f"bf_multi_company_email.{xmlid}")
        return self.user._bf_render_with(template, self.company) or ""

    # ------------------------------------------------------------------
    # Les liens : présents, cliquables, et assez gros pour être lus
    # ------------------------------------------------------------------
    def test_link_templates_render_every_link_as_a_button(self):
        for xmlid in AVEC_LIENS:
            with self.subTest(gabarit=xmlid):
                rendu = self._rendre(xmlid)
                # `t-out` échappe : « M'envoyer un fichier » sort en
                # « M&#39;envoyer un fichier », ce qui est le bon HTML et
                # s'affiche correctement partout. On cherche donc le libellé
                # dans le TEXTE, pas dans la source — sinon tout libellé à
                # apostrophe fait échouer la garde pour rien. (Le champ
                # stocké, lui, reçoit l'apostrophe littérale :
                # `html_sanitize` normalise les entités. Deux formes pour la
                # même chaîne, d'où le dés-échappement des deux côtés.)
                lisible = html.unescape(rendu)
                for lien in self.liens:
                    self.assertIn(lien.name, lisible,
                                  "le libellé du lien n'est pas rendu")
                    self.assertIn(lien.url, lisible,
                                  "l'adresse du lien n'est pas rendue")
                # Le fond du bouton vit sur une cellule, pas sur le <a> : le
                # moteur de Word ignore display:inline-block.
                self.assertRegex(
                    rendu,
                    r"<td[^>]+background-color:\s*#2D3031[^>]*>\s*<a ",
                    "aucune cellule à fond ne porte directement un lien : "
                    "le bouton n'en est pas un",
                )

    def test_button_labels_are_never_smaller_than_nine_points(self):
        """Le défaut que cette version corrige, repris en garde.

        La rangée était à 10 px, soit 7,5 pt — plus petite que tout le reste du
        message. On regarde la taille déclarée sur chaque `<a>` qui porte
        l'adresse d'un lien rapide, et sur rien d'autre.
        """
        for xmlid in AVEC_LIENS:
            with self.subTest(gabarit=xmlid):
                rendu = self._rendre(xmlid)
                ancres = re.findall(r"<a\s[^>]*>", rendu)
                mesurees = 0
                for ancre in ancres:
                    if not any(lien.url in ancre for lien in self.liens):
                        continue
                    tailles = re.findall(
                        r"font-size:\s*([0-9.]+)\s*(px|pt)", ancre)
                    self.assertTrue(
                        tailles,
                        "un bouton sans taille déclarée hérite de celle de son "
                        f"tableau, et Outlook n'hérite pas pareil : {ancre}")
                    for valeur, unite in tailles:
                        self.assertGreaterEqual(
                            _en_points(valeur, unite), PLANCHER_PT,
                            f"bouton sous le plancher de {PLANCHER_PT} pt :"
                            f" {ancre}")
                    mesurees += 1
                self.assertEqual(
                    mesurees, len(self.liens),
                    "tous les liens n'ont pas été mesurés : la forme du bouton "
                    "a changé et cette garde ne voit plus rien")

    def test_pronouns_are_readable(self):
        """Le défaut que le lot des boutons faillit laisser derrière lui.

        Les pronoms étaient rendus en `#999` sur 10 à 11 px : **2,85:1**, le
        chiffre exact que ce même lot cite pour condamner la rangée de liens,
        deux lignes plus bas sur la même carte. Ils lisent maintenant la
        couleur atténuée de la société, et la garde porte sur ce qui compte —
        le contraste et la taille — pas sur une valeur en dur.
        """
        attendu = self.user.bf_signature_pronouns
        self.assertTrue(attendu, "aucun pronom à mesurer")
        for xmlid in AVEC_PRONOMS:
            with self.subTest(gabarit=xmlid):
                rendu = self._rendre(xmlid)
                self.assertIn(attendu, rendu, "les pronoms ne sont pas rendus")
                spans = [
                    span for span in re.findall(r"<span\s[^>]*>", rendu)
                    if "font-weight" in span and "color" in span
                ]
                mesures = 0
                for span in spans:
                    couleurs = re.findall(
                        r"color:\s*(#[0-9a-fA-F]{3,6})", span)
                    tailles = re.findall(
                        r"font-size:\s*([0-9.]+)\s*(px|pt)", span)
                    if not couleurs or not tailles:
                        continue
                    # Le seul `<span>` gras-et-coloré de ces gabarits est
                    # celui des pronoms ; les autres textes vivent dans des
                    # `<div>` ou des `<td>`.
                    for couleur in couleurs:
                        self.assertGreaterEqual(
                            _contraste(couleur), CONTRASTE_AA,
                            f"pronoms à {_contraste(couleur):.2f}:1 sur blanc,"
                            f" AA exige {CONTRASTE_AA}:1 — {span}")
                    for valeur, unite in tailles:
                        self.assertGreaterEqual(
                            _en_points(valeur, unite), PLANCHER_PT,
                            f"pronoms sous le plancher de {PLANCHER_PT} pt :"
                            f" {span}")
                    mesures += 1
                self.assertGreaterEqual(
                    mesures, 1,
                    "aucun span de pronoms mesuré : la forme a changé et "
                    "cette garde ne voit plus rien")

    def test_buttons_carry_no_emoji(self):
        """Trois boutons de 9 pt AVEC émoji font 533 px, la carte en fait 520.

        L'émoji reste dans la donnée (quelqu'un peut écrire son propre
        gabarit), mais aucun gabarit livré ne le rend. Si cette garde tombe,
        c'est que la rangée de boutons a recommencé à déborder de la carte.
        """
        for xmlid in AVEC_LIENS:
            with self.subTest(gabarit=xmlid):
                rendu = self._rendre(xmlid)
                for lien in self.liens:
                    self.assertNotIn(lien.icon, rendu)

    # ------------------------------------------------------------------
    # Ce qu'un client de messagerie ne sait pas faire
    # ------------------------------------------------------------------
    def test_no_gradient_without_a_solid_colour_under_it(self):
        """Word ne dessine aucun dégradé : sans repli, le filet disparaît.

        Le compteur final est là pour la même raison que celui du plancher de
        9 pt : sans lui, cette garde passe au vert le jour où le dégradé sort
        des gabarits ou change de forme de déclaration — c'est-à-dire
        exactement quand elle n'éprouve plus rien.
        """
        eprouves = 0
        for xmlid in LIVRES:
            with self.subTest(gabarit=xmlid):
                rendu = self._rendre(xmlid)
                for style in re.findall(r'style="([^"]*)"', rendu):
                    if "gradient" not in style:
                        continue
                    eprouves += 1
                    self.assertIn(
                        "background-color", style,
                        "dégradé sans couleur unie de repli : invisible sous "
                        f"Outlook — {style}")
        self.assertGreaterEqual(
            eprouves, len(AVEC_DEGRADE),
            "aucun dégradé éprouvé : le filet a quitté les gabarits, ou sa "
            "déclaration a changé et cette garde ne voit plus rien")

    def test_no_layout_the_word_engine_ignores(self):
        for xmlid in LIVRES:
            with self.subTest(gabarit=xmlid):
                rendu = self._rendre(xmlid)
                for interdit in ("display:flex", "display: flex",
                                 "display:grid", "display: grid",
                                 "display:inline-block", "display: inline-block",
                                 "position:absolute", "position: absolute"):
                    self.assertNotIn(interdit, rendu)
                # Gmail retire le <style> du corps d'un message.
                self.assertNotIn("<style", rendu)

    def test_no_svg_logo_anywhere(self):
        """Gmail et Outlook ne dessinent pas un SVG dans un <img>.

        Vécu deux fois : un premier correctif a remplacé l'adresse dans
        les signatures posées, pas dans le champ qui les engendre, où le
        SVG attendait, prêt à revenir au premier rendu.
        """
        for xmlid in LIVRES:
            with self.subTest(gabarit=xmlid):
                rendu = self._rendre(xmlid)
                srcs = re.findall(r'src="([^"]*)"', rendu)
                for src in srcs:
                    self.assertNotIn(".svg", src.lower())
                    self.assertNotIn(
                        "/web/image/website/", src,
                        "cette route sert le logo du site TEL QUEL, et c'est "
                        "un SVG : case vide chez la plupart des destinataires")
                # Même garde-compteur que le plancher de 9 pt, mais par
                # gabarit : ces deux-là rendent un logo, donc un `src` au
                # moins doit avoir été éprouvé. Sans ça, l'essai reste vert
                # quand le logo cesse d'être rendu — et c'est le logo qui
                # portait la régression.
                if xmlid in AVEC_LOGO:
                    self.assertTrue(
                        srcs,
                        f"« {xmlid} » rend un logo et aucun `src` n'a été "
                        "éprouvé : cette garde ne voit plus rien")

    # ------------------------------------------------------------------
    # Ce que l'outil LIT, pas ce qu'il rend
    # ------------------------------------------------------------------
    def test_the_stored_signature_keeps_the_button_background(self):
        """Le fond du bouton, relu dans la colonne, pas dans le rendu.

        `res.users.signature` est un `fields.Html` nu : Odoo l'assainit à
        l'écriture, et un bouton qui perdrait son fond en route serait un
        bouton de moins chez le destinataire — sans qu'aucun essai sur le
        rendu ne s'en aperçoive.

        Mesuré sur une base d'essai, rendu de la carte passé dans
        `html_sanitize` avec les options exactes de chaque champ puis relu en
        SQL : des trois `bgcolor`, il n'en reste **aucun** dans
        `res.users.signature` (liste blanche d'attributs), et les trois
        `mso-line-height-rule` y restent (`sanitize_style=False`). Ce qui
        porte réellement le bouton — `background-color`, `padding`,
        `align="center"`, `font-size: 9pt`, `font-weight: 700` — survit. C'est
        donc là-dessus que porte la garde, et elle se lit sur la colonne.
        """
        self.user.sudo().bf_signature_template_id = self.env.ref(
            "bf_multi_company_email.bf_signature_card")
        self.user.sudo()._bf_apply_company_signature(force=True)
        # La valeur en cache est déjà la valeur assainie, mais la règle de la
        # maison est de mesurer ce que l'outil lit : on vide vers la base et on
        # relit la colonne.
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT signature FROM res_users WHERE id = %s", (self.user.id,))
        stockee = self.env.cr.fetchone()[0] or ""

        self.assertTrue(stockee, "aucune signature stockée : rien à mesurer")
        self.assertRegex(
            stockee,
            r"<td[^>]+background-color:\s*#2D3031[^>]*>\s*<a ",
            "la cellule du bouton a perdu son fond dans le champ STOCKÉ : "
            "l'assainissement d'Odoo a emporté ce qui faisait le bouton",
        )
        lisible = html.unescape(stockee)
        for lien in self.liens:
            self.assertIn(lien.name, lisible)
            self.assertIn(lien.url, lisible)
        # Compté sur les ANCRES des boutons, pas sur la signature entière :
        # les pronoms sont eux aussi à 9 pt depuis cette version, et un compte
        # global ferait rougir cette garde à chaque fois qu'un autre élément
        # rejoint le plancher.
        ancres_boutons = [
            ancre for ancre in re.findall(r"<a\s[^>]*>", stockee)
            if any(lien.url in html.unescape(ancre) for lien in self.liens)
        ]
        self.assertEqual(
            len(ancres_boutons), len(self.liens),
            "les trois boutons ne sont pas tous dans la colonne")
        for ancre in ancres_boutons:
            self.assertRegex(
                ancre, r"font-size:\s*9pt",
                f"un bouton a perdu sa taille dans le champ STOCKÉ : {ancre}")
        self.assertNotIn(
            "#999999", stockee,
            "le gris à 2,85:1 de l'ancienne rangée est revenu")

    # ------------------------------------------------------------------
    # Le semis du locataire, quand il est là
    # ------------------------------------------------------------------
    def test_the_tenant_seed_never_seeds_an_svg_logo(self):
        chemin = (pathlib.Path(__file__).resolve().parent.parent
                  / "data" / "brand_seed.private.json")
        if not chemin.exists():
            self.skipTest("pas de semis : c'est le cas du module publié")
        semis = json.loads(chemin.read_text())
        for nom, societe in (semis.get("companies") or {}).items():
            url = (societe.get("fields") or {}).get("bf_signature_logo_url")
            if not url:
                continue
            self.assertNotIn("/web/image/website/", url,
                             f"le semis de « {nom} » reposerait le SVG")
            self.assertNotIn(".svg", url.lower())
