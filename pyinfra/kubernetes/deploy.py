from pyinfra.operations import apt, server

apt.packages(
    name="Ensure the vim apt package is installed",
    packages=["vim"],
    _sudo=True,
)
