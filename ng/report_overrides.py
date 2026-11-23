"""Extra "Group By" options for the standard asset reports, without touching core files.

How it works: the three reports are looked up by Frappe through `frappe.get_attr(<module>.execute)` on every run
(desk, Excel export and prepared reports alike), so `apply()` swaps that attribute for a wrapper. The wrapper
passes every request that does not use the new option straight to the original `execute`, and builds the
"Item" and (ledger only) "Asset Category" views from the original's own output, so the core numbers stay the
single source. `apply()` is idempotent and runs from the `before_request` and `before_job` hooks.

The matching filter options live in `public/js/ng.js`.
"""

import importlib

import frappe
from frappe import _
from frappe.utils import flt

ITEM = "Item"
ASSET_CATEGORY = "Asset Category"
NO_GROUP = "--Select a group--"


def apply():
	"""Wrap the report `execute` functions once per process."""
	for module_path, wrap in WRAPPERS.items():
		module = importlib.import_module(module_path)
		if getattr(module.execute, "_ng_wrapped", False):
			continue
		wrapper = wrap(module.execute, module)
		wrapper._ng_wrapped = True
		module.execute = wrapper


# ---------------------------------------------------------------------------------------------
# helpers


def get_asset_info(asset_names):
	"""{asset name: {item_code, asset_category}} for the given assets."""
	names = list({name for name in asset_names if name})
	if not names:
		return {}
	rows = frappe.get_all(
		"Asset", filters={"name": ["in", names]}, fields=["name", "item_code", "asset_category"]
	)
	return {row.name: row for row in rows}


def item_column(label=None):
	return {
		"label": label or _("Item"),
		"fieldname": "item_code",
		"fieldtype": "Link",
		"options": "Item",
		"width": 216,
	}


def assets_column():
	return {"label": _("Assets"), "fieldname": "assets", "fieldtype": "Int", "width": 80}


def sort_key(row, fieldname):
	return (row.get(fieldname) is None, str(row.get(fieldname) or ""))


# ---------------------------------------------------------------------------------------------
# Fixed Asset Register: Group By > Item


def wrap_fixed_asset_register(original, module):
	sum_fields = (
		"net_purchase_amount",
		"opening_accumulated_depreciation",
		"depreciated_amount",
		"asset_value",
	)

	def execute(filters=None):
		filters = frappe._dict(filters or {})
		if filters.get("group_by") != ITEM:
			return original(filters)

		plain = frappe._dict(filters)
		plain["group_by"] = NO_GROUP
		_columns, rows, message, _chart = original(plain)

		info = get_asset_info(row.get("asset_id") for row in rows)
		groups = {}
		for row in rows:
			item = (info.get(row.get("asset_id")) or {}).get("item_code")
			group = groups.setdefault(item, {"item_code": item, "assets": 0, **dict.fromkeys(sum_fields, 0.0)})
			group["assets"] += 1
			for field in sum_fields:
				group[field] += flt(row.get(field))

		# the core's grouped layout (group column + the four totals), with Item as the group column
		columns = module.get_columns(frappe._dict(filters, group_by=ASSET_CATEGORY))
		columns[0] = item_column()
		columns.insert(1, assets_column())

		return columns, sorted(groups.values(), key=lambda r: sort_key(r, "item_code")), message, None

	return execute


# ---------------------------------------------------------------------------------------------
# Asset Depreciations and Balances: Group By > Item


def wrap_asset_depreciations_and_balances(original, module):
	def execute(filters=None):
		if not filters or filters.get("group_by") != ITEM:
			return original(filters)

		# per-asset rows from the core, then totalled per item
		inner = frappe._dict(filters)
		inner["group_by"] = "Asset"
		inner["asset"] = None
		columns, rows = original(inner)

		money = [c["fieldname"] for c in columns if c.get("fieldtype") == "Currency"]
		info = get_asset_info((row.get("asset") or row.get("name")) for row in rows)
		category = filters.get("asset_category")

		groups = {}
		for row in rows:
			asset = info.get(row.get("asset") or row.get("name")) or {}
			if category and asset.get("asset_category") != category:
				continue
			item = asset.get("item_code")
			group = groups.setdefault(item, {"item_code": item, "assets": 0, **dict.fromkeys(money, 0.0)})
			group["assets"] += 1
			for field in money:
				group[field] += flt(row.get(field))

		columns = [c for c in columns if c.get("fieldname") not in ("asset", "asset_name")]
		columns.insert(0, assets_column())
		columns.insert(0, item_column())

		return columns, sorted(groups.values(), key=lambda r: sort_key(r, "item_code"))

	return execute


# ---------------------------------------------------------------------------------------------
# Asset Depreciation Ledger: new Group By filter (Item, Asset Category)


def wrap_asset_depreciation_ledger(original, module):
	def execute(filters=None):
		group_by = (filters or {}).get("group_by")
		if group_by not in (ITEM, ASSET_CATEGORY):
			return original(filters)

		columns, rows = original(filters)
		if not rows:
			return columns, rows

		info = get_asset_info(row.get("asset") for row in rows)

		# One pass per asset: opening from its first entry in the period, closing from its last,
		# depreciation summed. Rows arrive ordered by asset, then posting date.
		per_asset = {}
		for row in rows:
			asset = per_asset.setdefault(
				row["asset"],
				{
					"purchase": flt(row.get("net_purchase_amount")),
					"opening": flt(row.get("opening_accumulated_depreciation")),
					"depreciation": 0.0,
					"accumulated": 0.0,
					"value": 0.0,
				},
			)
			asset["depreciation"] += flt(row.get("depreciation_amount"))
			asset["accumulated"] = flt(row.get("accumulated_depreciation_amount"))
			asset["value"] = flt(row.get("value_after_depreciation"))

		key_field = "item_code" if group_by == ITEM else "asset_category"
		groups = {}
		for name, asset in per_asset.items():
			key = (info.get(name) or {}).get(key_field)
			group = groups.setdefault(
				key,
				{
					key_field: key,
					"assets": 0,
					"net_purchase_amount": 0.0,
					"opening_accumulated_depreciation": 0.0,
					"depreciation_amount": 0.0,
					"accumulated_depreciation_amount": 0.0,
					"value_after_depreciation": 0.0,
				},
			)
			group["assets"] += 1
			group["net_purchase_amount"] += asset["purchase"]
			group["opening_accumulated_depreciation"] += asset["opening"]
			group["depreciation_amount"] += asset["depreciation"]
			group["accumulated_depreciation_amount"] += asset["accumulated"]
			group["value_after_depreciation"] += asset["value"]

		wanted = (
			"net_purchase_amount",
			"opening_accumulated_depreciation",
			"depreciation_amount",
			"accumulated_depreciation_amount",
			"value_after_depreciation",
		)
		by_name = {c["fieldname"]: c for c in columns}
		if group_by == ITEM:
			first = item_column()
		else:
			first = {**by_name["asset_category"], "width": 216}
		new_columns = [first, assets_column()] + [by_name[name] for name in wanted if name in by_name]

		return new_columns, sorted(groups.values(), key=lambda r: sort_key(r, key_field))

	return execute


WRAPPERS = {
	"erpnext.assets.report.fixed_asset_register.fixed_asset_register": wrap_fixed_asset_register,
	"erpnext.accounts.report.asset_depreciations_and_balances.asset_depreciations_and_balances": (
		wrap_asset_depreciations_and_balances
	),
	"erpnext.accounts.report.asset_depreciation_ledger.asset_depreciation_ledger": (
		wrap_asset_depreciation_ledger
	),
}
