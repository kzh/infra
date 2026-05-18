from dataclasses import dataclass

import pulumi_postgresql as pg

import pulumi


def _first_present(values: list[object]) -> object:
    primary, fallback = values
    return primary or fallback


class PostgresStack:
    def __init__(self, stack_ref: str):
        self.ref = pulumi.StackReference(stack_ref)

    @property
    def admin_host(self) -> pulumi.Output[str]:
        return pulumi.Output.all(
            self.ref.require_output("ts_hostname"),
            self.ref.require_output("host"),
        ).apply(_first_present)

    @property
    def rw_service_fqdn(self) -> pulumi.Output[str]:
        return self.ref.require_output("rw_service_fqdn")

    @property
    def port(self) -> pulumi.Output[int]:
        return self.ref.require_output("port")

    @property
    def username(self) -> pulumi.Output[str]:
        return self.ref.require_output("username")

    @property
    def password(self) -> pulumi.Output[str]:
        return self.ref.require_output("password")

    def admin_provider(
        self,
        resource_name: str,
        *,
        database: pulumi.Input[str] = "postgres",
        sslmode: pulumi.Input[str] = "disable",
        host: pulumi.Input[str] | None = None,
        port: pulumi.Input[int] = 5432,
    ) -> pg.Provider:
        return pg.Provider(
            resource_name,
            host=host if host is not None else self.ref.require_output("ts_hostname"),
            port=port,
            username=self.username,
            password=self.password,
            database=database,
            sslmode=sslmode,
        )


@dataclass(frozen=True)
class DatabaseOwner:
    role: pg.Role
    database: pg.Database


def create_database_owner(
    *,
    role_resource_name: str,
    database_resource_name: str,
    provider: pg.Provider,
    role_name: pulumi.Input[str],
    database_name: pulumi.Input[str],
    password: pulumi.Input[str],
) -> DatabaseOwner:
    role = pg.Role(
        role_resource_name,
        name=role_name,
        login=True,
        password=password,
        opts=pulumi.ResourceOptions(provider=provider),
    )

    database = pg.Database(
        database_resource_name,
        name=database_name,
        owner=role.name,
        opts=pulumi.ResourceOptions(
            provider=provider,
            depends_on=[role],
        ),
    )

    return DatabaseOwner(role=role, database=database)
