/** @odoo-module **/
/**
 * Les signets dans la palette de commandes d'Odoo (Ctrl+K). Ils sortent parmi les
 * résultats dès qu'on tape ; Entrée ouvre le signet dans un nouvel onglet.
 *
 * L'arbre est relu au plus une fois par minute : la palette appelle le fournisseur à
 * chaque frappe.
 */
import { registry } from "@web/core/registry";
import { aplatir, chargerSignets, chercher } from "./bookmarks_data";

registry.category("command_categories").add("bf_bookmarks", { name: "Signets" }, { sequence: 60 });

let cache = { quand: 0, tous: [] };

async function signets(orm) {
    if (Date.now() - cache.quand > 60_000) {
        const { sections } = await chargerSignets(orm, { partager: true });
        cache = { quand: Date.now(), tous: aplatir(sections) };
    }
    return cache.tous;
}

registry.category("command_provider").add("bf_bookmarks", {
    async provide(env, options) {
        const requete = (options.searchValue || "").trim();
        if (requete.length < 2) return [];
        let tous;
        try {
            tous = await signets(env.services.orm);
        } catch {
            return [];
        }
        return chercher(tous, requete, 8).map((lien) => ({
            category: "bf_bookmarks",
            // La palette n'affiche pas le nom des rubriques dans l'espace par défaut :
            // le préfixe dit ce que c'est (vu au parcours navigateur).
            name: `Signet : ${lien.title} — ${lien.chemin}`,
            action() {
                window.open(lien.url, "_blank", "noopener,noreferrer");
                const id = parseInt(String(lien.id).slice(1), 10);
                env.services.orm.call("bf.bookmark", "action_mark_used", [[id]]).catch(() => {});
            },
        }));
    },
});
