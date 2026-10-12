// Le formateur des voix d'assemblée (`formatVotes`, widget `bf_property_votes`).
// Les voix gardent quatre décimales en base ; l'écran n'imprime que celles qui
// portent quelque chose : « 585 », « 12.3456 », jamais « 585.0000 ». Ces essais
// gardent l'arrondi à quatre décimales, le signe, les milliers, la langue à
// virgule et le champ vide.
import { beforeEach, describe, expect, test } from "@odoo/hoot";
import { patchWithCleanup } from "@web/../tests/web_test_helpers";
import { localization } from "@web/core/l10n/localization";
import { registry } from "@web/core/registry";

import { formatVotes, VotesField } from "@bf_property_governance/js/votes_field";

describe.current.tags("headless");

beforeEach(() => {
    patchWithCleanup(localization, {
        decimalPoint: ".",
        thousandsSep: ",",
        grouping: [3, 0],
    });
});

test("une voix ronde s'imprime sans décimales", () => {
    expect(formatVotes(585)).toBe("585");
    expect(formatVotes(0)).toBe("0");
});

test("seules les décimales qui portent quelque chose, jusqu'à quatre", () => {
    expect(formatVotes(12.3456)).toBe("12.3456");
    expect(formatVotes(97.5)).toBe("97.5");
    expect(formatVotes(85.25)).toBe("85.25");
    expect(formatVotes(1 / 3)).toBe("0.3333");
    expect(formatVotes(2 / 3)).toBe("0.6667");
    expect(formatVotes(0.00004)).toBe("0");
});

test("une valeur négative garde son signe", () => {
    expect(formatVotes(-12.5)).toBe("-12.5");
    expect(formatVotes(-585)).toBe("-585");
    expect(formatVotes(-0.00004)).toBe("0");
});

test("les milliers sont groupés", () => {
    expect(formatVotes(1000)).toBe("1,000");
    expect(formatVotes(1234.5)).toBe("1,234.5");
    expect(formatVotes(-1234567.25)).toBe("-1,234,567.25");
});

test("une langue à virgule", () => {
    patchWithCleanup(localization, { decimalPoint: ",", thousandsSep: " " });
    expect(formatVotes(585)).toBe("585");
    expect(formatVotes(12.3456)).toBe("12,3456");
    expect(formatVotes(97.5)).toBe("97,5");
    expect(formatVotes(1234.5)).toBe("1 234,5");
});

test("un champ vide reste vide", () => {
    expect(formatVotes(false)).toBe("");
});

test("la précision d'un champ passée par la vue l'emporte", () => {
    expect(formatVotes(12.3456, { digits: [16, 2] })).toBe("12.35");
    expect(formatVotes(12.5, { digits: [16, 2] })).toBe("12.5");
});

test("le widget et le formateur sont inscrits sous le même nom", () => {
    // Le total en pied de liste prend le formateur par le nom du widget.
    expect(registry.category("formatters").get("bf_property_votes")).toBe(formatVotes);
    expect(registry.category("fields").get("bf_property_votes").component).toBe(VotesField);
});
