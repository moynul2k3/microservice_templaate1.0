from collections.abc import Iterable

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from app.core.security import get_password_hash, verify_password
from app.models.user import Group, Permission, User

DUMMY_GROUPS = {
    "dummy_admin": {"actions": ("create", "read", "update", "delete")},
    "dummy_staff": {"actions": ("read",)},
}

USERS_DATA = [
    {
        "email": "admin@gmail.com",
        "password": "admin",
        "is_superuser": True,
        "is_active": True,
        "groups": ("dummy_admin",),
        "user_permissions": (),
    },
    {
        "email": "staff@gmail.com",
        "password": "staff",
        "is_superuser": False,
        "is_active": True,
        "groups": ("dummy_staff",),
        "user_permissions": ("update_user",),
    },
]


async def _permission_map() -> dict[str, Permission]:
    permissions = await Permission.all()
    return {permission.codename: permission for permission in permissions}


def _select_group_permissions(
    permission_by_codename: dict[str, Permission],
    *,
    actions: Iterable[str],
) -> list[Permission]:
    action_prefixes = tuple(f"{action}_" for action in actions)
    return sorted(
        (
            permission
            for codename, permission in permission_by_codename.items()
            if codename.startswith(action_prefixes)
        ),
        key=lambda permission: permission.codename,
    )


async def _sync_group_permissions(
    group: Group,
    permissions: list[Permission],
) -> bool:
    current_ids = set(await group.permissions.all().values_list("id", flat=True))
    desired_ids = {permission.id for permission in permissions}
    if current_ids == desired_ids:
        return False

    await group.permissions.clear()
    if permissions:
        await group.permissions.add(*permissions)
    return True


async def _sync_user_groups(user: User, groups: list[Group]) -> bool:
    current_ids = set(await user.groups.all().values_list("id", flat=True))
    desired_ids = {group.id for group in groups}
    if current_ids == desired_ids:
        return False

    await user.groups.clear()
    if groups:
        await user.groups.add(*groups)
    return True


async def _sync_user_permissions(user: User, permissions: list[Permission]) -> bool:
    current_ids = set(await user.user_permissions.all().values_list("id", flat=True))
    desired_ids = {permission.id for permission in permissions}
    if current_ids == desired_ids:
        return False

    await user.user_permissions.clear()
    if permissions:
        await user.user_permissions.add(*permissions)
    return True


async def _ensure_groups(permission_by_codename: dict[str, Permission]) -> dict[str, Group]:
    groups_by_name: dict[str, Group] = {}

    for group_name, config in DUMMY_GROUPS.items():
        group, _ = await Group.get_or_create(name=group_name)
        permissions = _select_group_permissions(
            permission_by_codename,
            actions=config["actions"],
        )
        changed = await _sync_group_permissions(group, permissions)
        if changed:
            print(f"[dummy-user] synced group permissions: {group_name}")
        groups_by_name[group_name] = group

    return groups_by_name


async def create_dummy_user() -> None:
    created_count = 0
    updated_count = 0

    permission_by_codename = await _permission_map()
    groups_by_name = await _ensure_groups(permission_by_codename)

    for data in USERS_DATA:
        email = data["email"].lower()
        try:
            async with in_transaction() as conn:
                defaults = {
                    "email": email,
                    "password": get_password_hash(data["password"]),
                    "is_active": data.get("is_active", True),
                    "is_superuser": data.get("is_superuser", False),
                }

                user, created = await User.get_or_create(
                    email=email,
                    defaults=defaults,
                    using_db=conn,
                )

                if created:
                    created_count += 1
                    print(f"[dummy-user] created: {email}")

                update_fields: list[str] = []
                for field in ("is_active", "is_superuser"):
                    expected = defaults[field]
                    if getattr(user, field) != expected:
                        setattr(user, field, expected)
                        update_fields.append(field)

                password_matches = False
                if user.password:
                    try:
                        password_matches = verify_password(data["password"], user.password)
                    except Exception:
                        password_matches = False

                if not password_matches:
                    user.password = defaults["password"]
                    update_fields.append("password")

                if update_fields:
                    await user.save(using_db=conn, update_fields=sorted(set(update_fields)))
                    if not created:
                        updated_count += 1
                        print(f"[dummy-user] updated: {email}")

                user_groups = [groups_by_name[name] for name in data.get("groups", ()) if name in groups_by_name]
                user_permissions = [
                    permission_by_codename[codename]
                    for codename in data.get("user_permissions", ())
                    if codename in permission_by_codename
                ]

                membership_changed = False
                membership_changed |= await _sync_user_groups(user, user_groups)
                membership_changed |= await _sync_user_permissions(user, user_permissions)

                if membership_changed and not created and not update_fields:
                    updated_count += 1
                    print(f"[dummy-user] updated memberships: {email}")
                elif not created and not update_fields and not membership_changed:
                    print(f"[dummy-user] exists: {email}")
        except IntegrityError as error:
            print(f"[dummy-user] integrity error for {email}: {error}")
        except Exception as error:
            print(f"[dummy-user] unexpected error for {email}: {error}")

    print(f"[dummy-user] seeding completed (created={created_count}, updated={updated_count})")


create_test_users = create_dummy_user
