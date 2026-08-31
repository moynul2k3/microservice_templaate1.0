from collections import Counter
from collections.abc import Iterable

from tortoise import Tortoise
from tortoise.models import Model

CRUD_ACTIONS = ("create", "read", "update", "delete")
LEGACY_ACTIONS = ("view", "add")


def _iter_models() -> list[type[Model]]:
    models: list[type[Model]] = []
    for app_models in Tortoise.apps.values():
        for model in app_models.values():
            if not isinstance(model, type) or not issubclass(model, Model):
                continue
            if model._meta.abstract:
                continue
            models.append(model)
    return models


def _model_key(model: type[Model]) -> str:
    return f"{model._meta.app.lower()}.{model.__name__.lower()}"


def _resolve_permission_model(permission_model: type[Model] | None) -> type[Model]:
    if permission_model is not None:
        return permission_model

    for model in _iter_models():
        field_names = set(model._meta.fields_map)
        if model.__name__ == "Permission" and {"name", "codename"}.issubset(field_names):
            return model

    raise LookupError(
        "Could not find a registered Permission model with 'name' and 'codename' fields."
    )


def _normalize_actions(actions: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for action in actions:
        action_name = action.strip().lower()
        if not action_name or action_name in seen:
            continue
        seen.add(action_name)
        normalized.append(action_name)
    if not normalized:
        raise ValueError("At least one permission action is required.")
    return tuple(normalized)


def _model_slug(model: type[Model], duplicate_names: Counter[str]) -> str:
    model_name = model.__name__.lower()
    if duplicate_names[model_name] == 1:
        return model_name
    return f"{model._meta.app.lower()}_{model_name}"


def _permission_specs(
    models: Iterable[type[Model]],
    actions: Iterable[str],
) -> list[tuple[str, str]]:
    model_list = list(models)
    duplicate_names = Counter(model.__name__.lower() for model in model_list)
    specs: list[tuple[str, str]] = []

    for model in sorted(model_list, key=lambda item: (item._meta.app.lower(), item.__name__.lower())):
        slug = _model_slug(model, duplicate_names)
        label = model.__name__.replace("_", " ")
        for action in actions:
            codename = f"{action}_{slug}"
            name = f"Can {action} {label}"
            specs.append((codename, name))

    return specs


async def sync_permissions(
    permission_model: type[Model] | None = None,
    *,
    actions: Iterable[str] = CRUD_ACTIONS,
    exclude_models: Iterable[str] = (),
    exclude_apps: Iterable[str] = (),
    prune_stale: bool = True,
) -> list[Model]:
    permission_model = _resolve_permission_model(permission_model)
    normalized_actions = _normalize_actions(actions)
    excluded_models = {name.strip().lower() for name in exclude_models}
    excluded_apps = {name.strip().lower() for name in exclude_apps}

    models = [
        model
        for model in _iter_models()
        if model._meta.app.lower() not in excluded_apps
        and model.__name__.lower() not in excluded_models
        and _model_key(model) not in excluded_models
    ]
    specs = _permission_specs(models, normalized_actions)
    valid_codenames = {codename for codename, _ in specs}

    existing_permissions = {
        permission.codename: permission
        for permission in await permission_model.filter(codename__in=sorted(valid_codenames))
    }
    synced_permissions: list[Model] = []

    for codename, name in specs:
        permission = existing_permissions.get(codename)
        if permission is None:
            permission = await permission_model.create(codename=codename, name=name)
        elif getattr(permission, "name", None) != name:
            permission.name = name
            await permission.save(update_fields=["name"])
        synced_permissions.append(permission)

    if prune_stale:
        managed_prefixes = tuple({*normalized_actions, *LEGACY_ACTIONS})
        stale_managed_ids = [
            permission.id
            for permission in await permission_model.all()
            if permission.codename.startswith(managed_prefixes)
            and permission.codename not in valid_codenames
        ]
        if stale_managed_ids:
            await permission_model.filter(id__in=stale_managed_ids).delete()

    return await permission_model.filter(codename__in=sorted(valid_codenames)).order_by("codename")
