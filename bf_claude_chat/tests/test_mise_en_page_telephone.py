"""Ce que le navigateur a prouvé, et qu'une retouche distraite effacerait.

Ces trois invariants ne remplacent PAS la mesure au navigateur — un essai Python ne connaît
pas la largeur d'un écran. Ils pinnent ce que la mesure a coûté à trouver, et chacun
correspond à une panne vue pour vrai :

1. la classe du conteneur et le `t-if` du panneau doivent porter la MÊME condition,
   sinon l'écran peut annoncer une conversation que rien ne rend — liste cachée,
   conversation vide, et le retour injoignable ;
2. `display: none` SANS `!important` ne ferme rien : les panneaux portent `d-flex`, et
   l'utilitaire de Bootstrap est `display: flex !important`. Mesuré au navigateur : la règle
   s'appliquait, la liste tombait à 201 px, et les deux panneaux restaient à l'écran ;
3. le retour doit exister sur les DEUX surfaces et ne vivre que sous la borne, sans
   quoi le bureau gagne un bouton (ou le téléphone en perd le seul moyen de revenir).

⚠️ **Ce que ces essais ne peuvent pas voir, et il faut le dire.** Ils sont TOUS
statiques : ils lisent le texte du gabarit, du SCSS et du JS. Aucun ne voit un écran.
Un menu déroulant ouvert à x = -119, un lien qui couvre le centre d'une ligne, un
bouton invisible qui reçoit le toucher — la mesure au navigateur a trouvé
les quatre, et pas un de ces essais ne pouvait les trouver. Les mutants qu'ils tuent
sont des mutants du diff, pas de l'écran. **La preuve de l'écran est une mesure,**
faite dans un navigateur, et elle
ne vit pas ici. Ces essais ne servent qu'à une chose : qu'une retouche distraite ne
défasse pas en silence ce que la mesure a coûté à trouver.
"""

import pathlib
import re

from odoo.tests.common import TransactionCase, tagged

MODULE = pathlib.Path(__file__).resolve().parent.parent
SCSS = (MODULE / "static/src/scss/claude_chat.scss").read_text()
XML = (MODULE / "static/src/xml/claude_chat.xml").read_text()
BORNE = "max-width: 767.98px"


def scss_du_softphone():
    """Le SCSS de `bf_softphone`, ou None s'il n'est pas sur le chemin des modules.

    Deux des invariants ci-dessous tiennent un accord ENTRE DEUX MODULES : la voie que
    Gen réserve doit valoir la bulle que `bf_softphone` dessine, et la superposition du
    tiroir doit battre son `z-index`. Les lire chez le voisin est le seul moyen qu'un
    changement là-bas rende un essai rouge ICI. Absent, on saute : Gen ne dépend pas de
    lui, et la règle de Gen ne mord simplement pas.
    """
    from odoo.modules.module import get_module_path
    chemin = get_module_path("bf_softphone", display_warning=False)
    if not chemin:
        return None
    f = pathlib.Path(chemin) / "static/src/scss/softphone.scss"
    return f.read_text() if f.exists() else None


def px(valeur):
    """`3.5rem` → 56, `1.25rem` → 20, `280px` → 280. Le rem d'Odoo 18 vaut 16px."""
    valeur = valeur.strip()
    if valeur.endswith("rem"):
        return float(valeur[:-3]) * 16
    if valeur.endswith("px"):
        return float(valeur[:-2])
    raise AssertionError(f"unité non reconnue : {valeur}")


def sans_commentaires(css):
    """Le code seul. 🔴 Un mutant n'a tué AUCUN essai parce que le
    sélecteur qu'il retirait du code restait écrit dans le commentaire juste au-dessus,
    et `assertIn` s'en contentait : un essai berné par sa propre explication."""
    return "\n".join(l for l in css.splitlines() if not l.strip().startswith("//"))


def bloc_de_la_borne():
    """Le contenu du `@media` de la borne, accolades comptées.

    La borne absente est un ÉCHEC du contrôle, pas une erreur de l'aide : un mutant
    qui déplace la borne doit rendre un essai rouge lisible, et non une ValueError.
    """
    assert BORNE in SCSS, f"la borne « {BORNE} » a disparu du SCSS"
    i = SCSS.index(BORNE)
    i = SCSS.index("{", i)
    profondeur, j = 0, i
    while j < len(SCSS):
        if SCSS[j] == "{":
            profondeur += 1
        elif SCSS[j] == "}":
            profondeur -= 1
            if profondeur == 0:
                return SCSS[i:j + 1]
        j += 1
    raise AssertionError("accolades du @media non refermées")


@tagged("post_install", "-at_install")
class TestMiseEnPageTelephone(TransactionCase):

    def test_la_classe_du_conteneur_suit_le_t_if_du_panneau(self):
        conteneur = re.search(
            r'<div t-att-class="\'bf-chat-container[^"]*?'
            r'\((?P<cond>[^?]+?)\s*\?\s*\' bf-gen-conv\'', XML)
        self.assertTrue(conteneur, "le conteneur ne pose plus `bf-gen-conv`")
        self.assertIn('<t t-if="state.activeSessionId">', XML,
                      "le `t-if` du panneau a changé de forme : la classe doit suivre")
        self.assertEqual(
            conteneur.group("cond").strip(), "state.activeSessionId",
            "la classe et le `t-if` du panneau ne portent plus la même condition : "
            "l'écran peut se vider en annonçant une conversation que rien ne rend")

    def test_le_corps_du_tiroir_suit_son_propre_t_if(self):
        corps = re.search(
            r"<div t-att-class=\"'d-flex bf-side-panel-body'\s*"
            r"\+\s*\((?P<cond>[^?]+?)\s*\?\s*' bf-gen-conv'", XML)
        self.assertTrue(corps, "le corps du tiroir ne pose plus `bf-gen-conv`")
        self.assertEqual(corps.group("cond").strip(), "showChat",
                         "le corps du tiroir doit suivre `showChat`, la condition même "
                         "du `t-if` qui décide d'afficher la conversation")
        self.assertIn('<t t-if="showChat">', XML)

    def test_les_quatre_regles_qui_cachent_portent_important(self):
        bloc = bloc_de_la_borne()
        caches = [l.strip() for l in bloc.splitlines()
                  if "display: none" in l and "bf-gen-conv" in l]
        self.assertEqual(len(caches), 4,
                         f"quatre règles attendues, {len(caches)} trouvée(s) : {caches}")
        for regle in caches:
            self.assertIn("!important", regle,
                          f"sans `!important`, `.d-flex` de Bootstrap gagne : {regle}")

    def test_le_retour_existe_sur_les_deux_surfaces_et_ne_sort_pas_du_telephone(self):
        retours = re.findall(r'class="[^"]*bf-gen-retour[^"]*"', XML)
        self.assertEqual(len(retours), 2,
                         "le retour doit exister sur les deux surfaces : plein écran "
                         f"et tiroir ({len(retours)} trouvé(s))")
        for classe in retours:
            self.assertIn("d-md-none", classe,
                          f"le retour doit disparaître au-dessus de la borne : {classe}")
        self.assertEqual(XML.count("this._clearConversation()"), 2,
                         "les deux retours doivent passer par le geste du mixin partagé, "
                         "qui vide aussi le total et l'avis « archivée »")

    def test_le_menu_des_compteurs_est_ancre_sur_la_barre_pas_sur_son_bouton(self):
        """C1, la régression : `right: 0` dans un `<span>` de 16 px
        mettait 57 % du menu hors écran dès que le volet partait à x = 0, sans barre de
        défilement. Il s'ancre sur la barre des compteurs."""
        bloc = bloc_de_la_borne()
        self.assertRegex(
            sans_commentaires(bloc),
            r"\.bf-gen-meters\s*>\s*span\.position-relative\s*\{[^}]*"
                  r"position:\s*static\s*!important",
            "le `<span>` du bouton doit redevenir non positionné — et `!important`, "
            "parce que `.position-relative` de Bootstrap en porte un")
        # 🔴 Cet essai exigeait `left/right/width` sur un
        # sélecteur `.bf-gen-meters-menu` (0,1,0) que la règle préexistante
        # `.bf-gen-meters .bf-gen-meters-menu` (0,2,0) battait. Il pinnait donc du CODE
        # MORT en lui prêtant l'effet du voisin : mesuré, `left` calculé à 148 px, et le
        # retirer ne changeait rien. C'est le SÉLECTEUR qui compte ici.
        code = sans_commentaires(bloc)
        self.assertIn(".bf-gen-meters .bf-gen-meters-menu", code,
                      "le menu doit être visé par DEUX classes, sinon la règle de la "
                      "barre gagne et l'étirement n'a jamais lieu")
        menu = code[code.index(".bf-gen-meters .bf-gen-meters-menu"):]
        menu = menu[:menu.index("}")]
        for prop in ("left: 0", "right: 0", "width: auto"):
            self.assertIn(prop, menu,
                          f"le menu doit s'étirer sur la barre : {prop} manque")

    def test_la_pastille_de_fiche_ne_vole_plus_le_geste_de_la_ligne(self):
        """C2 : la pastille couvrait 86 % de la ligne et son centre.
        Décorative sous la borne, inchangée au-dessus."""
        bloc = bloc_de_la_borne()
        self.assertRegex(sans_commentaires(bloc),
                         r"\.bf-gen-record-pill\s*\{[^}]*pointer-events:\s*none")
        dehors = SCSS[:SCSS.index(BORNE)]
        self.assertNotIn("pointer-events: none", dehors,
                         "au-dessus de la borne la pastille reste un lien")

    def test_les_actions_de_ligne_se_voient_sur_un_ecran_tactile(self):
        """C3 : `opacity: 0` révélé au `:hover` seulement, donc un
        bloc invisible de 52 px recevait le toucher — et Archiver écrit en base."""
        bloc = bloc_de_la_borne()
        code = sans_commentaires(bloc)
        for sel in (".bf-chat-sidebar .bf-chat-session-item .bf-chat-session-actions",
                    ".bf-side-panel-session-item .bf-side-panel-row-actions .btn"):
            i = code.find(sel)
            self.assertNotEqual(i, -1, f"règle absente pour {sel}")
            self.assertIn("opacity: 1", code[i:i + len(sel) + 40],
                          f"les actions doivent se voir sous la borne : {sel}")

    def test_le_partage_part_avec_la_conversation(self):
        """C4 : `shareOpen` restait vrai, donc le panneau flottait
        par-dessus la liste au tiroir, et se rouvrait seul au plein écran."""
        js = (MODULE / "static/src/js/gen_screen.js").read_text()
        corps = js[js.index("_clearConversation("):]
        corps = corps[:corps.index("\n    },")]
        self.assertIn("this.state.shareOpen = false", corps,
                      "`_clearConversation` doit aussi vider `shareOpen`")

    def test_le_retour_a_une_cible_touchable(self):
        """C5 : 19 × 31 px, sous les 24 × 24 de WCAG 2.5.8."""
        bloc = bloc_de_la_borne()
        code = sans_commentaires(bloc)
        i = code.index(".bf-gen-retour")
        regle = code[i:code.index("}", i)]
        self.assertIn("min-width: 44px", regle)
        self.assertIn("min-height: 44px", regle)
        # Et la cohérence : rendre les actions de ligne visibles sans les rendre
        # touchables n'était qu'une demi-mesure (14,8 px, dont Archiver, qui écrit).
        for sel in (".bf-chat-session-actions .btn", ".bf-side-panel-row-actions .btn",
                    ".bf-chat-panel-header .btn", ".bf-side-panel-header .btn"):
            self.assertIn(sel, code, f"plancher tactile manquant pour {sel}")
        i = code.index(".bf-side-panel-header .btn")
        self.assertIn("min-width: 32px", code[i:code.index("}", i)],
                      "plancher de 32 px attendu (WCAG 2.5.8 AA en demande 24)")
        # 🔴 Et le plancher doit ÉPARGNER le retour : écrit sans `:not`, son sélecteur
        # (0,2,0) bat `.bf-gen-retour` (0,1,0) et ramène le retour de 44 à 32 px.
        # Mesuré pour vrai avant d'être corrigé.
        self.assertEqual(
            code.count(".btn:not(.bf-gen-retour)"), 2,
            "les deux en-têtes doivent exclure le retour de leur plancher de 32 px, "
            "sinon il perd ses 44 px sans un mot")

    def test_les_deux_surfaces_savent_nommer_la_conversation_ouverte(self):
        """C6 : la liste cachée, rien ne nommait la conversation."""
        self.assertIn('class="fw-semibold text-truncate d-md-none bf-gen-titre-mobile"',
                      XML, "l'en-tête du plein écran doit porter le nom sous la borne")
        for fichier in ("claude_chat.js", "claude_systray.js"):
            js = (MODULE / "static/src/js" / fichier).read_text()
            self.assertIn("get activeSessionName()", js,
                          f"{fichier} doit savoir nommer la conversation ouverte")
            # Le littéral n'était pas marqué (« New Chat »
            # dans une interface française), et le nom tombait à "" quand la
            # conversation ouverte n'est pas dans la liste chargée.
            self.assertIn('_t("New Chat")', js, f"{fichier} : littéral non traduit")
            self.assertIn('_t("Conversation")', js,
                          f"{fichier} : pas de repli, le nom peut être vide")

    def test_la_borne_est_celle_du_lot_1_et_d_odoo(self):
        self.assertIn(BORNE, SCSS)
        self.assertNotIn("max-width: 768px", SCSS,
                         "768 px pile appartient encore à l'arrière-guichet d'Odoo "
                         "(il y rend le menu usager) : la borne s'arrête à 767,98")

    # ── La voie de la bulle du softphone ────────────────────────────────────
    # 🔴 Mesuré au navigateur : la bulle de `bf_softphone` recouvrait 82,6 % du bouton Envoyer de Gen
    # et le rendait INATTEIGNABLE à 390, 768, 1024 et 1250 px. Ces deux essais ne sont
    # PAS dans le bloc de la borne, et c'est le point : la panne vivait aussi sur une
    # fenêtre de portable de 1 250 px, où aucune règle de la borne ne s'applique.

    def test_la_voie_de_la_bulle_est_reservee_sans_borne_et_seulement_si_la_bulle_est_la(self):
        code = sans_commentaires(SCSS)
        regle = re.search(
            r"body:has\(\.o_bf_sp_bubble\)\s+\.bf-chat-input-area\s*\{(?P<corps>[^}]*)\}",
            code)
        self.assertTrue(regle,
                        "la voie de la bulle n'est plus réservée : le bouton Envoyer "
                        "(et « Arrêter ») redevient inatteignable sous ~1 300 px")
        corps = regle.group("corps")
        self.assertIn("!important", corps,
                      "`p-3` de Bootstrap est `padding: 1rem !important` : sans "
                      "`!important`, la règle est posée et ne fait RIEN")
        # Hors du `@media` : la panne n'est pas bornée au téléphone.
        self.assertNotIn(regle.group(0), bloc_de_la_borne(),
                         "la voie ne doit pas vivre sous la borne : le recouvrement a "
                         "été mesuré à 1 024 et 1 250 px aussi")
        # Et le tiroir, lui, ne réserve rien — il n'en a pas besoin (essai suivant).
        self.assertNotRegex(
            code, r"body:has\(\.o_bf_sp_bubble\)\s+\.bf-side-panel-input",
            "le tiroir n'a rien à réserver : sa superposition bat la bulle au z-index")

    def test_la_voie_vaut_exactement_la_bulle_que_le_softphone_dessine(self):
        """L'arithmétique du `clamp`, relue chez les deux modules.

        🔴 C'est la garde contre la DÉRIVE SILENCIEUSE. Les trois nombres du `clamp`
        sortent de constantes qui vivent ailleurs : la bulle chez `bf_softphone`, la
        barre latérale et la largeur du composeur chez Gen. Qu'on touche à l'une sans
        l'autre et la voie se réserve trop court — sans qu'un seul essai rougisse, et
        sans qu'un écran le montre au-dessus de la borne. On refait donc le calcul.
        """
        sp = scss_du_softphone()
        if not sp:
            self.skipTest("bf_softphone absent du chemin des modules")
        ecart = px(re.search(r"--bf-sp-gap:\s*([^;]+);", sp).group(1))
        taille = px(re.search(r"--bf-sp-size:\s*([^;]+);", sp).group(1))
        voie = ecart + taille                      # ce que la bulle prend au bord droit
        jeu = 16                                   # le jeu qu'on se donne, en dur ici
        code = sans_commentaires(SCSS)
        barre = px(re.search(r"\.bf-chat-sidebar\s*\{\s*width:\s*([^;]+);",
                             code).group(1))
        composeur = px(re.search(
            r"\.bf-chat-input-area\s*\{\s*max-width:\s*([^;]+);", code).group(1))
        # `p-3` d'Odoo/Bootstrap : 1rem de chaque côté du composeur.
        pivot = barre + composeur + 2 * 16
        clamp = re.search(
            r"body:has\(\.o_bf_sp_bubble\)\s+\.bf-chat-input-area\s*\{[^}]*"
            r"padding-right:\s*clamp\(\s*(?P<bas>[\d.]+)px\s*,\s*"
            r"calc\(\s*(?P<voie>[\d.]+)px\s*-\s*\(\s*100vw\s*-\s*"
            r"(?P<pivot>[\d.]+)px\s*\)\s*/\s*2\s*\)\s*,\s*"
            r"(?P<haut>[\d.]+)px\s*\)", code)
        self.assertTrue(clamp, "la formule de la voie a changé de forme : la relire")
        self.assertEqual(float(clamp.group("bas")), 16,
                         "le plancher doit rendre le `p-3` d'avant, au pixel, pour que "
                         "le bureau ne bouge pas")
        self.assertEqual(float(clamp.group("haut")), voie + jeu,
                         f"le plafond de la voie doit valoir la bulle de "
                         f"`bf_softphone` ({voie:g}px) plus le jeu ({jeu}px)")
        self.assertEqual(float(clamp.group("voie")), voie + jeu,
                         "le terme soustrait part de la même voie que le plafond")
        self.assertEqual(float(clamp.group("pivot")), pivot,
                         f"le pivot doit valoir la barre latérale ({barre:g}px) plus le "
                         f"composeur ({composeur:g}px) plus son `p-3` (32px) = {pivot:g}")

    def test_le_tiroir_ne_doit_sa_survie_qu_a_son_z_index(self):
        """Pourquoi le tiroir n'a RIEN à corriger, et ce qui le casserait.

        Mesuré : le bouton Envoyer du tiroir est recouvert à 39,1 % par la
        bulle aux CINQ largeurs — 1 440 px compris — et reste atteignable aux cinq, au
        centre comme aux quatre quarts. La seule raison est l'écart de `z-index` entre la
        superposition du tiroir et la bulle. Baisser l'un ou monter l'autre rendrait le
        tiroir aussi inatteignable que le plein écran l'était, et aucune autre garde ne
        le verrait.
        """
        sp = scss_du_softphone()
        if not sp:
            self.skipTest("bf_softphone absent du chemin des modules")
        bulle = int(re.search(
            r"\.o_bf_sp_bubble\s*\{[^}]*z-index:\s*(\d+)",
            sans_commentaires(sp), re.S).group(1))
        superposition = int(re.search(
            r"\.bf-panel-overlay\s*\{[^}]*z-index:\s*(\d+)",
            sans_commentaires(SCSS), re.S).group(1))
        self.assertGreater(
            superposition, bulle,
            f"la superposition du tiroir ({superposition}) doit rester au-dessus de la "
            f"bulle du softphone ({bulle}) : c'est la SEULE raison pour laquelle le "
            f"bouton Envoyer du tiroir, recouvert à 39,1 %, reçoit encore le doigt")
