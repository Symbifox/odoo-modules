from odoo import api, models


class ProjectTask(models.Model):
    """Les sous-tâches se voient toujours, et l'œil qui les cachait disparaît.

    Le cœur d'Odoo 18 cache une sous-tâche rangée dans le même projet que son
    parent : son drapeau `display_in_project` vaut faux, et les sept actions de
    `project` (Tâches, Toutes les tâches, Tâches de projet, Tâches en
    dépassement, Partage de projet...) portent la feuille de domaine
    `('display_in_project', '=', True)`, comme les compteurs des cartes de
    projet (`project.project.__compute_task_count`). Le drapeau se change par
    deux boutons « œil » sur la fiche, visibles quand le champ technique
    `show_display_in_project` est vrai.

    Ici, le drapeau vaut toujours vrai et l'œil ne paraît plus : rien ne se
    cache, et aucune action, aucun compteur, aucune vue de portail n'a besoin
    d'être retouché — ils lisent tous le même drapeau.
    """

    _inherit = "project.task"

    # ⚠️ Le cœur déclare `display_in_project` SANS `string=` : Odoo en tire
    # « Display In Project », qui reste en anglais dans la liste des champs d'un
    # filtre personnalisé. Lui donner un libellé ici ne le traduit pas pour
    # autant : la description d'un champ s'exporte sous le module qui le DÉFINIT
    # (`project`), pas sous celui qui le redéfinit — mesuré le 2026-11-06, le
    # catalogue du module reste à deux entrées. On n'y touche donc pas.

    # --- Le drapeau du cœur, toujours vrai -------------------------------

    @api.depends("project_id", "parent_id")
    def _compute_display_in_project(self):
        self.display_in_project = True

    @api.depends("project_id", "parent_id")
    def _compute_show_display_in_project(self):
        # À faux, les deux champs `boolean_icon` de la fiche (fa-eye et
        # fa-eye-slash) restent invisibles : leur attribut `invisible` tient à
        # ce champ. Rien à retirer dans les vues, pas même dans celles du
        # partage de projet.
        self.show_display_in_project = False

    @api.model
    def default_get(self, fields_list):
        # Les vues du cœur ouvrent la création d'une sous-tâche avec
        # `default_display_in_project: False` au contexte. Un défaut l'emporte
        # sur le calcul (il arrive dans les valeurs de création), donc on le
        # retire ici : sans valeur donnée, le champ se calcule, et le calcul
        # rend toujours vrai.
        valeurs = super().default_get(fields_list)
        valeurs.pop("display_in_project", None)
        return valeurs

    @api.model_create_multi
    def create(self, vals_list):
        # Même raison pour une valeur ÉCRITE : le client renvoie ce que le
        # contexte lui avait donné. On RETIRE la clé plutôt que d'écrire vrai,
        # pour que les valeurs d'un usager de portail ne gagnent pas un champ
        # qu'il n'a pas le droit d'écrire (`display_in_project` est lisible mais
        # pas inscriptible au partage de projet —
        # `PROJECT_TASK_WRITABLE_FIELDS`).
        # ⚠️ Dès que la clé est LÀ, et pas seulement quand elle vaut `False` :
        # `0`, `None` et `""` ne sont pas `False` pour `is`, mais le cœur les
        # range à faux, et la fiche resterait cachée dans un produit où l'œil
        # n'existe plus : un appel XML-RPC ou une cellule vide d'un import
        # suffisait.
        return super().create([
            {c: v for c, v in vals.items() if c != "display_in_project"}
            if "display_in_project" in vals else vals
            for vals in vals_list
        ])

    def write(self, vals):
        # La bretelle : `project.project.copy` écrit faux sur les sous-tâches
        # copiées, et un import ou un appel XML-RPC pourrait faire de même. La
        # clé tombe — quelle que soit sa valeur, voir `create` — et la valeur en
        # base reste vraie.
        if "display_in_project" in vals:
            vals = {c: v for c, v in vals.items() if c != "display_in_project"}

        # Le projet d'un parent qui bouge emporte les enfants restés dans son
        # ancien projet. Sans cela, le déplacement laisserait les sous-tâches
        # derrière, et elles se verraient — c'est le cœur qui s'en chargeait,
        # mais il ne déplaçait que les sous-tâches cachées
        # (`_compute_project_id` ne bouge que si `not display_in_project`).
        suivis = {}
        if vals.get("project_id"):
            for tache in self:
                if tache.project_id.id != vals["project_id"]:
                    suivis[tache.id] = tache.project_id.id

        resultat = super().write(vals)

        for identifiant, ancien in suivis.items():
            # Une recherche, pas un `filtered` sur `child_ids` : la recherche
            # applique les règles d'accès sans lever, là où lire le projet d'un
            # enfant rangé dans un projet qu'on ne voit pas ferait échouer
            # l'écriture du parent.
            enfants = self.with_context(active_test=False).search([
                ("parent_id", "=", identifiant),
                ("project_id", "=", ancien),
            ])
            if enfants:
                enfants.write({"project_id": vals["project_id"]})
        return resultat

    # --- Le projet d'une sous-tâche --------------------------------------

    @api.depends("parent_id.project_id")
    def _compute_project_id(self):
        # Le cœur se servait du drapeau pour décider si une sous-tâche suit le
        # projet de son parent ; forcé à vrai, il ne suivrait plus jamais et
        # une sous-tâche créée sans projet resterait privée. La règle qui
        # remplace : sans projet, elle prend celui de son parent. Le cœur
        # retirait ici `display_in_project` de la file de calcul, ce qui
        # laissait la valeur par défaut (faux) s'écrire : on ne le fait pas.
        for tache in self:
            if tache.parent_id and not tache.project_id:
                tache.project_id = tache.parent_id.project_id

    # Pas de `_onchange_parent_id` ici : le calcul ci-dessus remplit déjà le
    # champ Projet dans le formulaire d'une sous-tâche neuve (mesuré par
    # mutation le 2026-11-05 : l'`onchange` retiré, la suite restait verte).
    # Celui du cœur devient inerte, puisqu'il sort d'emblée quand le drapeau
    # vaut vrai.
