/** @odoo-module **/

/**
 * Les icônes de marque du coffre : d'où elles viennent, et ce qu'elles ne font
 * pas.
 *
 * 🔴 Le module refuse toujours d'aller chercher une favicon. La requête
 * révélerait au service, et à qui regarde le réseau, la liste des comptes qu'on
 * protège. Ce refus ne bouge pas : rien ici ne sort de l'instance.
 *
 * ⚠️ **La sélection est MÉCANIQUE, et c'est ce qui la rend publiable.** Le
 * catalogue est l'intersection de deux jeux publics : l'annuaire 2FA Directory
 * (les services qui gèrent le TOTP) et Simple Icons. Aucun coffre n'a été
 * regardé pour le composer. Une sélection tirée des émetteurs réels ferait de la
 * liste des services d'une organisation une donnée publique, exactement ce que
 * le refus de la favicon évite.
 *
 * ⛔ Microsoft, LinkedIn, Amazon Web Services et Fastmail sont ABSENTS de la
 * source, retirés à la demande de leurs propriétaires. Ce sont parmi les plus
 * répandus ; ils gardent la pastille, et on ne leur dessine pas de substitut.
 * Une marque approchée est une marque contrefaite.
 *
 * ⚠️ Cette liste se vérifie contre la source à chaque montée, elle ne se recopie
 * pas. Slack et Twilio y ont figuré, et ils sont revenus : les annoncer absents
 * pendant que le catalogue les embarque est une contradiction qu'on ne veut pas
 * avoir à expliquer sur un dépôt public.
 *
 * ⚠️ **Le catalogue n'est PAS dans le paquet d'actifs.** Il pèse quatre cents
 * kilo-octets, et le mettre dans `web.assets_backend` le ferait charger à
 * chaque personne qui ouvre Odoo, coffre ou pas. Il est donc servi en fichier
 * statique et récupéré une seule fois, quand le coffre s'ouvre. Le paquet de
 * tout le monde est plus LÉGER qu'avant ce changement, pas plus lourd.
 *
 * ⚠️ Correspondance par identifiant EXACT, jamais par préfixe. Deviner
 * « Apple Federal Credit Union » à partir de « Apple » poserait le logo d'une
 * marque sur le compte d'une autre, ce qui est pire que pas d'icône du tout.
 * Ce qui ne correspond pas garde la pastille calculée du nom.
 *
 * Provenance : Simple Icons (https://simple-icons.org), CC0 1.0, version notée
 * dans le catalogue lui-même. Les icônes qui portent une licence PROPRE dans
 * les données du projet sont écartées à la fabrication : le CC0 du projet ne
 * les couvre pas. Relevé icône par icône dans `static/src/data/icones-provenance.tsv`
 * et résumé dans `THIRD_PARTY.md`.
 */

const CATALOGUE_URL = "/bf_otp/static/src/data/icones.json";

/** Le catalogue chargé, ou un catalogue vide tant qu'il ne l'est pas. */
let _catalogue = { icones: {}, alias: {} };
let _promesse = null;

/**
 * Charge le catalogue, une fois.
 *
 * ⚠️ **Un échec ne casse rien et ne se signale pas à l'usager** : sans
 * catalogue, chaque jeton garde sa pastille, qui est un repli qui ne rate
 * jamais. Une bannière d'erreur pour une icône manquante serait du bruit.
 *
 * 🔴 La promesse est mise en cache, pas le résultat : deux ouvertures
 * rapprochées ne doivent pas lancer deux requêtes, et la seconde doit attendre
 * la première plutôt que repartir sur un catalogue vide.
 */
export function chargerIcones() {
    if (!_promesse) {
        _promesse = fetch(CATALOGUE_URL)
            .then((r) => (r.ok ? r.json() : null))
            .then((d) => {
                if (d && d.icones) {
                    _catalogue = { icones: d.icones, alias: d.alias || {} };
                }
                return _catalogue;
            })
            .catch(() => _catalogue);
    }
    return _promesse;
}

/** Combien d'icônes le catalogue porte. Sert aux contrôles, pas à l'écran. */
export function nombreDIcones() {
    return Object.keys(_catalogue.icones).length;
}

/** Réduit un nom d'émetteur à l'identifiant de Simple Icons. */
export function identifiant(nom) {
    return (nom || "")
        .toLowerCase()
        .replace(/\+/g, "plus")
        .replace(/\./g, "dot")
        .replace(/&/g, "and")
        .normalize("NFD")
        .replace(/[̀-ͯ]/g, "")
        .replace(/[^a-z0-9]/g, "");
}

/**
 * L'icône d'un émetteur, ou `null`.
 *
 * ⚠️ Rend `null` sans hésiter : la pastille est un repli qui ne rate jamais,
 * alors qu'une icône approximative se remarque et trompe.
 *
 * ⚠️ Un alias qui vaut `null` est un refus EXPLICITE, pas une absence : c'est
 * ainsi que « amazon » ou « microsoft » gardent la pastille même le jour où une
 * icône de ce nom réapparaîtrait dans la source.
 */
export function iconeDe(emetteur) {
    const id = identifiant(emetteur);
    if (!id) {
        return null;
    }
    const cible = id in _catalogue.alias ? _catalogue.alias[id] : id;
    return (cible && _catalogue.icones[cible]) || null;
}

/**
 * Noir ou blanc au-dessus d'une couleur de marque, décidé par luminance.
 *
 * 🔴 Sans ce calcul, une marque très claire (il y en a) rendrait le glyphe
 * blanc invisible sur son propre fond.
 */
export function contraste(hex) {
    const v = hex.replace("#", "");
    const r = parseInt(v.slice(0, 2), 16) / 255;
    const g = parseInt(v.slice(2, 4), 16) / 255;
    const b = parseInt(v.slice(4, 6), 16) / 255;
    const f = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    const L = 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    return L > 0.45 ? "#111" : "#fff";
}
