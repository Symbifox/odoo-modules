import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Remplissage à zéro du numéro affiché. Trois chiffres : la forme des cahiers de
# projet d'où vient la demande (001, 002, 003…), et celle qui se trie bien dans
# une colonne de texte jusqu'à 999.
LARGEUR = 3
CHIFFRES = re.compile(r"^\d+$")

# Clé du verrou d'avis Postgres qui ordonne les attributions (voir `create`).
# Un entier fixe, propre à ce module : deux transactions qui attribuent un numéro
# s'attendent, celles qui font autre chose passent.
VERROU = 2746304


class ProjectProject(models.Model):
    _inherit = "project.project"

    # Choisir un projet par son numéro là où Odoo propose une liste (feuille de
    # temps, facture, tâche) : sans ça le numéro ne se lit qu'à l'écran des
    # projets, et la référence de facturation ne sert à rien au moment de
    # facturer. `project.project` n'en déclare pas : rien n'est perdu ici.
    _rec_names_search = ["name", "bf_project_number"]

    # 🔴 Déclarée, et non posée à la main par `create_unique_index` dans `init()`.
    # Trois raisons mesurées : `_as_validation_error` cherche le nom de la
    # contrainte dans `registry._sql_constraints` et, ne l'y trouvant pas, rend le
    # « duplicate key value violates… » brut de Postgres ; une contrainte déclarée
    # existe en `ir.model.constraint`, donc son message se traduit et s'inspecte ;
    # et `create_unique_index` n'a aucun `try/except`, donc une base qui porterait
    # déjà des doublons ferait mourir la pose au lieu de la journaliser.
    # Les NULL ne se gênent pas entre eux : un projet sans numéro reste possible.
    _sql_constraints = [
        (
            "bf_project_number_uniq",
            "unique(bf_project_number)",
            "Two projects cannot carry the same project number.",
        ),
    ]

    bf_project_number = fields.Char(
        string="Project Number",
        copy=False,  # dupliquer un projet (un modèle « à dupliquer ») donne un numéro NEUF
        tracking=True,
        help="Number assigned at creation, taking the smallest number still free. "
        "Editable by hand as long as the target number is not already taken.",
    )

    # ── le numéro, sa forme et son unicité ───────────────────────────────────

    @api.model
    def _bf_numero_normalise(self, valeur):
        """« 7 » → « 007 ». Rend False pour un numéro vide, lève pour le reste.

        Normaliser à l'écriture, et non à l'affichage, est ce qui garde
        l'unicité vraie : sans ça « 7 » et « 007 » sont deux lignes distinctes
        pour la contrainte, et deux projets portent le même numéro à l'écran.

        ⚠️ `" "` est vrai en Python et vide ici : une cellule d'importation qui
        ne porte qu'une espace (ou une espace insécable) doit se lire « pas de
        numéro », pas « numéro vide ». Cf. le garde-fou de `create`.
        """
        if not valeur:
            return False
        texte = str(valeur).strip()
        if not texte:
            return False
        if not CHIFFRES.match(texte):
            raise ValidationError(
                _("A project number takes digits only: %s is refused.", texte)
            )
        entier = int(texte)
        if entier <= 0:
            raise ValidationError(_("Project numbers start at 1: %s is refused.", texte))
        return f"{entier:0{LARGEUR}d}"

    @api.model
    def _bf_numeros_pris(self):
        """Tous les numéros déjà pris, en entiers, lus en SQL.

        En SQL et non par l'ORM : l'unicité est celle de la TABLE. Un numéro pris
        par un projet archivé, par un projet d'une autre société, ou par un projet
        privé que l'usager ne voit pas, reste pris — et aucune règle
        d'enregistrement ne doit pouvoir le cacher au compteur, sinon on propose
        un numéro que la contrainte refuse ensuite sous les yeux de l'usager.
        """
        self.env.cr.execute(
            "SELECT bf_project_number FROM project_project WHERE bf_project_number IS NOT NULL"
        )
        pris = set()
        for (texte,) in self.env.cr.fetchall():
            texte = (texte or "").strip()
            if CHIFFRES.match(texte):
                pris.add(int(texte))
        return pris

    @api.model
    def _bf_numero_libre(self, pris):
        """Le plus petit entier positif absent de `pris`, et le marque pris."""
        candidat = 1
        while candidat in pris:
            candidat += 1
        pris.add(candidat)
        return f"{candidat:0{LARGEUR}d}"

    @api.model
    def _bf_verrouiller_attribution(self):
        """Ordonne les attributions concurrentes, jusqu'à la fin de la transaction.

        🔴 Sans ce verrou, deux transactions qui créent un projet en même temps
        lisent toutes les deux les mêmes numéros pris (READ COMMITTED ne montre
        rien de non validé), calculent le même « plus petit libre », et la seconde
        meurt sur la contrainte. Odoo ne rejoue PAS ce cas : `service/model.py`
        ne reprend que les conflits de sérialisation et d'interblocage, jamais une
        violation d'unicité — la transaction entière est perdue, et avec elle tout
        le flux qui créait le projet (accueil d'un client, importation).
        """
        self.env.cr.execute("SELECT pg_advisory_xact_lock(%s)", (VERROU,))

    def _bf_verifie_libre(self, numero, sauf=None):
        """Refuse, en nommant le détenteur quand l'usager a le droit de le voir.

        🔴 Mesuré sur une base d'essai : un `@api.constrains` seul NE SUFFIT
        PAS. Odoo pose l'INSERT (ou l'UPDATE) avant d'évaluer ses contraintes
        Python, donc c'est Postgres qui refuse le premier, et son message est
        illisible pour qui renumérote un projet. Le contrôle tombe donc AVANT
        l'écriture ; la contrainte déclarée n'est plus qu'un filet.

        ⚠️ Le nom du détenteur ne sort que si l'usager peut lire ce projet. La
        recherche de l'unicité, elle, est globale : nommer un projet privé ou
        d'une autre société révélerait son existence à qui n'y a pas accès.
        """
        if not numero:
            return
        domaine = [("bf_project_number", "=", numero)]
        if sauf:
            domaine.append(("id", "not in", list(sauf)))
        Projet = self.env["project.project"].with_context(active_test=False)

        visible = Projet.search(domaine, limit=1)
        if visible:
            raise ValidationError(
                _(
                    "Project number %(numero)s is already taken by “%(projet)s”. "
                    "Free it first, or choose another number.",
                    numero=numero,
                    projet=visible.display_name,
                )
            )
        if Projet.sudo().search(domaine, limit=1):
            raise ValidationError(
                _(
                    "Project number %s is already taken by a project you do not "
                    "have access to. Choose another number.",
                    numero,
                )
            )

    @api.model_create_multi
    def create(self, vals_list):
        # Le dictionnaire appartient à l'appelant : une boucle qui réutilise un
        # gabarit verrait son numéro figé au premier projet, et tous les suivants
        # refusés. On travaille sur une copie.
        vals_list = [dict(vals) for vals in vals_list]
        self._bf_verrouiller_attribution()

        # Premier passage : les numéros imposés. Normalisés et vérifiés avant
        # toute écriture, et retenus — sans ça, le second passage pourrait servir
        # à un projet sans numéro celui qu'un autre de la même fournée impose.
        imposes = set()
        for vals in vals_list:
            if not vals.get("bf_project_number"):
                continue
            numero = self._bf_numero_normalise(vals["bf_project_number"])
            if not numero:
                # Une cellule qui ne porte qu'une espace : pas un numéro vide,
                # pas de numéro du tout. Sans ce garde-fou, deux lignes pareilles
                # dans une importation se refusaient l'une l'autre avec
                # « Le numéro de projet False est demandé deux fois ».
                vals["bf_project_number"] = False
                continue
            if numero in imposes:
                raise ValidationError(
                    _("Project number %s is requested twice in the same batch.", numero)
                )
            self._bf_verifie_libre(numero)
            imposes.add(numero)
            vals["bf_project_number"] = numero

        # Second passage : les autres, au plus petit numéro libre. Les numéros
        # déjà pris ne sont lus qu'une fois pour la fournée, et chaque numéro
        # servi s'y ajoute : sans ça, créer dix projets d'un coup leur donnerait
        # dix fois le même numéro.
        pris = None
        for vals in vals_list:
            if vals.get("bf_project_number"):
                continue
            if pris is None:
                pris = self._bf_numeros_pris() | {int(n) for n in imposes}
            vals["bf_project_number"] = self._bf_numero_libre(pris)
        return super().create(vals_list)

    def write(self, vals):
        if "bf_project_number" in vals:
            vals = dict(vals)
            numero = self._bf_numero_normalise(vals["bf_project_number"])
            vals["bf_project_number"] = numero
            if numero:
                if len(self) > 1:
                    raise ValidationError(
                        _(
                            "Project number %(numero)s cannot be given to "
                            "%(nombre)s projects at once: it designates only one.",
                            numero=numero,
                            nombre=len(self),
                        )
                    )
                self._bf_verrouiller_attribution()
                self._bf_verifie_libre(numero, sauf=self.ids)
        return super().write(vals)

    @api.constrains("bf_project_number")
    def _check_bf_project_number(self):
        """Filet, pas serrure : `create` et `write` refusent déjà avant d'écrire.

        Reste pour les chemins qui ne passeraient pas par eux. Son message ne se
        voit donc presque jamais ; la contrainte déclarée, elle, tient sous la
        concurrence que le verrou n'aurait pas ordonnée.
        """
        for projet in self:
            projet._bf_verifie_libre(projet.bf_project_number, sauf=projet.ids)
