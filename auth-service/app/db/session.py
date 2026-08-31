from importlib import import_module
from pkgutil import walk_packages

from tortoise import Tortoise, connections

from app.core.config import settings


def _tortoise_database_url() -> str:
    return settings.DATABASE_URL.replace("postgresql+psycopg2://", "postgres://", 1)


def _discover_model_modules(package_name: str = "app.models") -> list[str]:
    package = import_module(package_name)
    package_paths = getattr(package, "__path__", None)
    if not package_paths:
        return [package_name]

    modules = [package_name]
    for module_info in walk_packages(package_paths, prefix=f"{package_name}."):
        if module_info.name.rsplit(".", 1)[-1].startswith("_"):
            continue
        modules.append(module_info.name)
    return sorted(set(modules))


TORTOISE_ORM = {
    "connections": {"default": _tortoise_database_url()},
    "apps": {
        "models": {
            "models": _discover_model_modules(),
            "default_connection": "default",
        },
    },
}


async def _reconcile_existing_schema() -> None:
    connection = connections.get("default")
    await connection.execute_script(
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS is_2fa BOOLEAN NOT NULL DEFAULT FALSE;
        """
    )


async def init_db() -> None:
    await Tortoise.init(config=TORTOISE_ORM)
    await Tortoise.generate_schemas()
    await _reconcile_existing_schema()


async def close_db() -> None:
    await Tortoise.close_connections()
