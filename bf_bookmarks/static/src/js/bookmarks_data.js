/** @odoo-module **/
/**
 * Ce que la barre et la page Signets partagent : lire l'arbre (le même que celui de
 * l'extension, donc les mêmes droits) avec mes favoris, mes récents et les icônes ;
 * le mettre à plat pour la recherche ; et les gestes (ouvrir, étoile, ranger).
 */

/** Seules les adresses web deviennent des liens (défense en profondeur : le
 *  serveur refuse déjà le reste, mais un `javascript:` dans un href s'exécute). */
export const ADRESSE_WEB = /^https?:\/\//i;
/** Une icône n'est acceptée que comme adresse d'icône du module, sur Odoo même (le
 *  serveur n'en fait pas d'autre) : jamais une adresse externe, qui dirait au site
 *  qu'on regarde ses signets. */
const ICONE_SURE = /^\/bf_bookmarks\/icone\/\d+\/[0-9a-f]{32}\.png$/;
export const GLISSE = "application/x-bf-signet";

export function normaliser(texte) {
    return (texte || "")
        .normalize("NFD")
        .replace(/\p{Diacritic}/gu, "")
        .toLowerCase();
}

function nettoyer(noeud) {
    if (noeud.kind === "bookmark") {
        if (!ADRESSE_WEB.test(noeud.url || "")) return null;
        return { ...noeud, icon: ICONE_SURE.test(noeud.icon || "") ? noeud.icon : null };
    }
    return { ...noeud, children: (noeud.children || []).map(nettoyer).filter(Boolean) };
}

/*
 * Le dernier état lu, gardé le temps de la page. La barre s'ouvre dessus
 * tout de suite, puis la lecture serveur, qui porte les droits, le rafraîchit en
 * revenant. Rien n'est écrit dans le navigateur : il s'efface au rechargement et à
 * la déconnexion.
 */
let connus = null;
let connusTexte = "";
let enVol = null;
let numeroDemande = 0;
let numeroRetenu = 0;

/** Le dernier état lu dans cette page, ou null. */
export function signetsConnus() {
    return connus;
}

/**
 * Lit l'arbre, mes favoris et mes récents. `partager` : se joindre à une lecture déjà
 * en route (le survol de l'icône, puis le clic) au lieu d'en lancer une autre. Un
 * geste qui vient d'écrire demande toujours une lecture neuve.
 */
export function chargerSignets(orm, { partager = false } = {}) {
    if (partager && enVol) {
        return enVol;
    }
    const numero = ++numeroDemande;
    const lecture = orm.call("bf.bookmark.folder", "widget_data", []).then((d) => {
        // Une réponse partie avant une autre déjà retenue ne la remplace pas.
        if (numero < numeroRetenu) {
            return connus;
        }
        numeroRetenu = numero;
        const texte = JSON.stringify(d);
        // Rien n'a changé : le même objet, donc pas de nouveau rendu.
        if (texte !== connusTexte) {
            connusTexte = texte;
            connus = {
                sections: d.sections.map(nettoyer),
                favoris: (d.favorites || []).map(nettoyer).filter(Boolean),
                recents: (d.recents || []).map(nettoyer).filter(Boolean),
            };
        }
        return connus;
    });
    enVol = lecture;
    const liberer = () => {
        if (enVol === lecture) {
            enVol = null;
        }
    };
    lecture.then(liberer, liberer);
    return lecture;
}

/** Tous les signets, avec leur chemin (« Entreprise / Outils »), pour la recherche. */
export function aplatir(sections) {
    const tous = [];
    const marcher = (n, chemin) => {
        for (const c of n.children || []) {
            if (c.kind === "bookmark") {
                tous.push({ ...c, chemin: chemin.join(" / "), cle: normaliser(`${c.title} ${c.url} ${chemin.join(" ")}`) });
            } else {
                marcher(c, [...chemin, c.title]);
            }
        }
    };
    for (const s of sections) marcher(s, [s.title]);
    return tous;
}

export function chercher(tous, requete, limite = 50) {
    const mots = normaliser(requete).split(/\s+/).filter(Boolean);
    if (!mots.length) return [];
    return tous.filter((s) => mots.every((m) => s.cle.includes(m))).slice(0, limite);
}

export function compter(noeud) {
    return (noeud.children || []).reduce(
        (n, c) => n + (c.kind === "bookmark" ? 1 : compter(c)), 0);
}

const idDe = (noeud) => parseInt(String(noeud.id).slice(1), 10);

/**
 * Les gestes, partagés par la barre et la page. `recharger` relit l'état après un
 * geste qui le change. Tout passe par le serveur, qui vérifie les droits.
 */
export function gestes(orm, recharger, { notification, action } = {}) {
    return {
        async copier(lien) {
            try {
                await navigator.clipboard.writeText(lien.url);
                notification?.add("Lien copié.", { type: "success" });
            } catch {
                notification?.add("Le navigateur a refusé la copie.", { type: "warning" });
            }
        },
        proposer(lien) {
            action?.doAction({
                type: "ir.actions.act_window",
                name: "Proposer à l'équipe",
                res_model: "bf.bookmark.proposal",
                views: [[false, "form"]],
                target: "new",
                context: {
                    default_name: lien.title,
                    default_url: lien.url,
                    default_bookmark_id: idDe(lien),
                },
            });
        },
        ouvrir(lien) {
            // Sans attendre : l'onglet s'ouvre tout de suite, le récent suit. La
            // relecture tient le dernier état à jour pour la prochaine ouverture du
            // menu, qui n'a pas à réordonner les récents sous le curseur.
            orm.call("bf.bookmark", "action_mark_used", [[idDe(lien)]])
                .then(() => chargerSignets(orm))
                .catch(() => {});
        },
        async favori(lien) {
            await orm.call("bf.bookmark", "action_toggle_favorite", [[idDe(lien)]]);
            await recharger();
        },
        async ordonnerFavoris(ids) {
            await orm.call("bf.bookmark", "set_favorite_order", [ids.map((i) => idDe({ id: i }))]);
            await recharger();
        },
        async deplacer(noeudId, dossierId, index) {
            const r = await orm.call("bf.bookmark.folder", "extension_apply",
                [[{ op: "move", id: noeudId, parent: dossierId, index }]]);
            await recharger();
            return r.errors?.length ? r.errors[0].message : null;
        },
    };
}

/** Où est un nœud : {parentId, index}, ou null. */
export function position(sections, id) {
    const marcher = (n) => {
        const i = (n.children || []).findIndex((c) => c.id === id);
        if (i >= 0) return { parentId: n.id, index: i };
        for (const c of n.children || []) {
            if (c.kind === "folder") {
                const r = marcher(c);
                if (r) return r;
            }
        }
        return null;
    };
    for (const s of sections) {
        const r = marcher(s);
        if (r) return r;
    }
    return null;
}

/**
 * Le rang à demander au serveur pour « déposer avant la cible ». Le serveur retire
 * d'abord l'élément puis l'insère au rang : dans le même dossier, venir d'avant la
 * cible décale d'un cran.
 */
export function rangDeDepot(sections, id, parentId, index) {
    const p = position(sections, id);
    return p && p.parentId === parentId && p.index < index ? index - 1 : index;
}

/** Le nouvel ordre des favoris quand `id` est déposé avant `cible` (ou à la fin). */
export function nouvelOrdre(favoris, id, cibleId) {
    const reste = favoris.map((f) => f.id).filter((f) => f !== id);
    const i = cibleId ? reste.indexOf(cibleId) : -1;
    reste.splice(i < 0 ? reste.length : i, 0, id);
    return reste;
}
