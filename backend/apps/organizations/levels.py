"""The category structure an organization uses: how many levels, what each is called, and
which levels are "common".

A structure is a list of levels, 2 to 8 of them, each {"name": ..., "common": ...}.

- The levels that are not common form the tree, top to bottom. The last of them is the item
  itself (the thing with a barcode and a price); the ones above it are groups the item sits
  in. By default there are three: Category > Sub-category > Item.
- A common level is a separate, shared list (Colour, RAM, Storage). It is not part of the tree:
  any item can take a value from it, or none, and the list is the same for every group.

A superadmin sets the structure for a kind of organization (on its code) and can change it
for a single client.
"""
from django.core.exceptions import ValidationError

MIN_LEVELS = 2
MAX_LEVELS = 8
MAX_NAME = 40

DEFAULT_LEVELS = [
    {"name": "Category", "common": False},
    {"name": "Sub-category", "common": False},
    {"name": "Item", "common": False},
]

# Starting structures a superadmin can pick when editing, and what the starter codes get.
PRESETS = {
    "default": ("Default: Category > Sub-category > Item", DEFAULT_LEVELS),
    "mobiles": ("Mobile shop: Mobile > Brand > Model name, with Colour, RAM, Storage", [
        {"name": "Mobile", "common": False},
        {"name": "Brand", "common": False},
        {"name": "Model name", "common": False},
        {"name": "Colour", "common": True},
        {"name": "RAM", "common": True},
        {"name": "Storage", "common": True},
    ]),
    "clothes": ("Clothing store: Category > Brand > Item, with Size, Colour", [
        {"name": "Category", "common": False},
        {"name": "Brand", "common": False},
        {"name": "Item", "common": False},
        {"name": "Size", "common": True},
        {"name": "Colour", "common": True},
    ]),
}


def default_levels() -> list:
    """A fresh copy of the default structure (used as a model field default)."""
    return [dict(level) for level in DEFAULT_LEVELS]


def clean_levels(raw) -> list:
    """The structure as a clean list, or a ValidationError saying what is wrong."""
    if not isinstance(raw, list):
        raise ValidationError("The structure must be a list of levels.")
    levels, seen = [], set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValidationError("Each level needs a name.")
        name = " ".join(str(entry.get("name") or "").split())
        if not name:
            raise ValidationError("Every level needs a name.")
        if len(name) > MAX_NAME:
            raise ValidationError(f"“{name[:20]}…” is too long. Use {MAX_NAME} characters or fewer.")
        if name.lower() in seen:
            raise ValidationError(f"Two levels are called “{name}”. Give each level its own name.")
        seen.add(name.lower())
        levels.append({"name": name, "common": bool(entry.get("common"))})
    if not MIN_LEVELS <= len(levels) <= MAX_LEVELS:
        raise ValidationError(f"Use between {MIN_LEVELS} and {MAX_LEVELS} levels.")
    if sum(1 for level in levels if not level["common"]) < 2:
        raise ValidationError(
            "At least two levels must not be common: a group, and the item itself."
        )
    return levels


def org_levels(org) -> list:
    """The structure for this organization; the default when none is set or it is not valid."""
    raw = getattr(org, "levels", None)
    if not raw:
        return default_levels()
    try:
        return clean_levels(raw)
    except ValidationError:
        return default_levels()


def split_levels(levels: list):
    """(groups, item, commons): the group levels above the item, the item level, and the common levels,
    each as the level dicts, in order."""
    tree = [level for level in levels if not level["common"]]
    commons = [level for level in levels if level["common"]]
    return tree[:-1], tree[-1], commons
