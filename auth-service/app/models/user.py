from tortoise import fields, models
from tortoise.expressions import Q

class Permission(models.Model):
    id = fields.IntField(pk=True, readonly=True, hidden=True)
    name = fields.CharField(max_length=100, unique=True, editable=False)
    codename = fields.CharField(max_length=100, unique=True, editable=False)

    def __str__(self):
        return self.codename


class Group(models.Model):
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=100, unique=True)

    permissions: fields.ManyToManyRelation["Permission"] = fields.ManyToManyField(
        "models.Permission",
        related_name="groups",
        through="group_permissions",
    )

    def __str__(self):
        return self.name


class User(models.Model):
    id = fields.IntField(primary_key=True)
    email = fields.CharField(max_length=255, unique=True, index=True)
    password = fields.CharField(max_length=255)
    is_2fa = fields.BooleanField(default=False)
    is_active = fields.BooleanField(default=True)
    is_superuser = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    last_login_at = fields.DatetimeField(null=True)

    groups: fields.ManyToManyRelation["Group"] = fields.ManyToManyField(
        "models.Group",
        related_name="users",
        through="user_groups",
    )
    user_permissions: fields.ManyToManyRelation["Permission"] = fields.ManyToManyField(
        "models.Permission",
        related_name="users",
        through="user_permissions",
    )

    class Meta:
        table = "users"

    def __str__(self) -> str:
        return self.email

    async def get_permission_codenames(self) -> set[str]:
        if self.is_superuser:
            return set(
                await Permission.all().distinct().values_list("codename", flat=True)
            )

        permission_rows = await Permission.filter(
            Q(users__id=self.id) | Q(groups__users__id=self.id)
        ).distinct().values_list("codename", flat=True)
        return set(permission_rows)

    async def has_permission(self, codename: str) -> bool:
        if self.is_superuser:
            return True
        return codename in await self.get_permission_codenames()
