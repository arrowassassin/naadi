/**
 * The admin console's and the review queue's shipped ui_copy defaults (ui-copy.admin.defaults.json). Imported for
 * its effect by the admin and review page chunks, so these strings stay out of the first load (segment 22).
 */
import { values } from "../ui-copy.admin.defaults.json";
import { registerUiCopyDefaults } from "./uiCopy";

registerUiCopyDefaults(values);
