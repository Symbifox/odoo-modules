import { cookie } from "@web/core/browser/cookie";
import { session } from "@web/session";

const CIDS_SEPARATOR = "-";

const allowed = session?.user_companies?.allowed_companies;
if (allowed && !cookie.get("cids")) {
    const ids = Object.keys(allowed).map(Number);
    if (ids.length > 1) {
        cookie.set("cids", ids.join(CIDS_SEPARATOR));
    }
}
