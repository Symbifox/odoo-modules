"""Répartition d'une somme entre des fractions, au sou près.

Une répartition au prorata produit presque toujours des montants à décimales
infinies : 100 $ entre trois fractions égales, c'est 33,333… chacune. Arrondir
chaque part séparément donne 99,99 $ ou 100,02 $, et l'appel de fonds ne
totalise plus le budget. Sur douze appels et cinquante fractions, l'écart
devient une somme réelle que personne ne sait expliquer.

La méthode retenue est celle du plus fort reste : on donne à chacun sa part
entière en cents, puis on distribue les cents manquants aux fractions dont le
reste décimal est le plus grand. La somme des parts égale alors exactement le
montant réparti, par construction et non par chance.

L'ordre de départage compte : à reste égal, le cent va à la plus petite clé.
Sans cette règle, deux exécutions sur les mêmes données donneraient des
répartitions différentes, et un appel recalculé ne serait plus le même que
celui qui a été transmis aux copropriétaires.
"""
import math

CENTS = 2


def allocate(amount, weights, digits=CENTS):
    """Répartit `amount` entre des clés pondérées.

    `weights` est une liste de couples (clé, poids), la clé devant être
    ordonnable pour que le départage soit reproductible. Rend un dictionnaire
    clé -> montant dont la somme vaut exactement `amount`, arrondi à `digits`.

    Un poids total nul rend des parts nulles plutôt qu'une division par zéro :
    une partie commune à usage restreint dont aucune fraction bénéficiaire n'a
    de quote-part est une donnée incomplète, pas une erreur de calcul, et
    l'appelant le signale à sa façon.
    """
    if not weights:
        return {}
    scale = 10 ** digits
    total_weight = sum(weight for _, weight in weights)
    if total_weight <= 0:
        return {key: 0.0 for key, _ in weights}

    target = int(round(amount * scale))
    exact = [(key, amount * weight / total_weight * scale) for key, weight in weights]
    shares = {key: int(math.floor(value)) for key, value in exact}
    remainders = {key: value - math.floor(value) for key, value in exact}

    missing = target - sum(shares.values())
    # `missing` est borné par le nombre de clés : chaque troncature perd moins
    # d'un cent. Un montant négatif n'a pas de sens ici et est refusé en amont
    # par une contrainte SQL sur les lignes de budget.
    order = sorted(shares, key=lambda key: (-remainders[key], key))
    for index in range(missing):
        shares[order[index % len(order)]] += 1

    return {key: value / scale for key, value in shares.items()}
