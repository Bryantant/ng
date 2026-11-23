// NG: extra "Group By" options on the standard asset reports (server side: ng/report_overrides.py).
//
// Core reports register their settings with `frappe.query_reports[name] = {...}` when the report opens,
// so a Proxy on that registry adds the option as the settings arrive, without touching core files.
frappe.provide("frappe.query_reports");

(function () {
	const NO_GROUP = "--Select a group--";

	const find_filter = (settings, fieldname) => (settings.filters || []).find((f) => f.fieldname === fieldname);

	function add_option(settings, fieldname, option) {
		const filter = find_filter(settings, fieldname);
		if (!filter) return;
		const options = Array.isArray(filter.options) ? filter.options : String(filter.options || "").split("\n");
		if (!options.includes(option)) filter.options = [...options, option];
	}

	const patches = {
		"Fixed Asset Register": (settings) => add_option(settings, "group_by", "Item"),

		"Asset Depreciations and Balances": (settings) => {
			add_option(settings, "group_by", "Item");
			// the category filter is hidden unless grouping by category; keep it for Item as well
			const category = find_filter(settings, "asset_category");
			if (category) {
				category.depends_on = "eval: doc.group_by == 'Asset Category' || doc.group_by == 'Item'";
			}
		},

		// the ledger has no Group By in core: add one after Asset Category
		"Asset Depreciation Ledger": (settings) => {
			if (find_filter(settings, "group_by")) return;
			settings.filters = settings.filters || [];
			const at = settings.filters.findIndex((f) => f.fieldname === "asset_category");
			settings.filters.splice(at < 0 ? settings.filters.length : at + 1, 0, {
				fieldname: "group_by",
				label: __("Group By"),
				fieldtype: "Select",
				options: [NO_GROUP, "Item", "Asset Category"],
				default: NO_GROUP,
			});
		},
	};

	function patch_once(name, settings) {
		const patch = patches[name];
		if (!patch || !settings || typeof settings !== "object" || settings.__ng_patched) return;
		try {
			patch(settings);
		} catch (e) {
			console.error("NG: could not extend report filters for " + name, e);
		}
		Object.defineProperty(settings, "__ng_patched", { value: true, enumerable: false });
	}

	// reports already registered before this file ran
	Object.keys(patches).forEach((name) => patch_once(name, frappe.query_reports[name]));

	frappe.query_reports = new Proxy(frappe.query_reports, {
		set(target, name, value) {
			patch_once(name, value);
			target[name] = value;
			return true;
		},
	});
})();
