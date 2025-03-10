# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright 2023 Canonical Ltd.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Rockcraft Lifecycle service."""

import contextlib
from pathlib import Path
from typing import Any, cast

from craft_application import LifecycleService
from craft_archives import repo  # type: ignore[import-untyped]
from craft_cli import emit
from craft_parts import Features, LifecycleManager, Step, callbacks
from craft_parts.errors import CallbackRegistrationError
from craft_parts.infos import ProjectInfo, StepInfo
from overrides import override  # type: ignore[reportUnknownVariableType]

from rockcraft import layers
from rockcraft.models.project import Project

# Enable the craft-parts features that we use
Features(enable_overlay=True)


class RockcraftLifecycleService(LifecycleService):
    """Rockcraft-specific lifecycle service."""

    @override
    def setup(self) -> None:
        """Initialize the LifecycleManager with previously-set arguments."""
        # pylint: disable=import-outside-toplevel
        # This inner import is necessary to resolve a cyclic import
        from rockcraft.services import RockcraftServiceFactory

        # Configure extra args to the LifecycleManager
        project = cast(Project, self._project)
        project_vars = {"version": project.version}

        services = cast(RockcraftServiceFactory, self._services)
        image_service = services.image
        image_info = image_service.obtain_image()

        self._manager_kwargs.update(
            base_layer_dir=image_info.base_layer_dir,
            base_layer_hash=image_info.base_digest,
            base=project.base,
            package_repositories=project.package_repositories or [],
            project_name=project.name,
            project_vars=project_vars,
            rootfs_dir=image_info.base_layer_dir,
        )

        super().setup()

    @override
    def run(self, step_name: str | None, part_names: list[str] | None = None) -> None:
        """Run the lifecycle manager for the parts."""
        # Overridden to configure package repositories.
        project = cast(Project, self._project)
        package_repositories = project.package_repositories

        if package_repositories is not None:
            _install_package_repositories(package_repositories, self._lcm)
            with contextlib.suppress(CallbackRegistrationError):
                callbacks.register_configure_overlay(_install_overlay_repositories)

        try:
            callbacks.register_post_step(_post_prime_callback, step_list=[Step.PRIME])
            super().run(step_name, part_names)
        finally:
            callbacks.unregister_all()


def _install_package_repositories(
    package_repositories: list[dict[str, Any]] | None,
    lifecycle_manager: LifecycleManager,
) -> None:
    """Install package repositories in the environment."""
    if not package_repositories:
        emit.debug("No package repositories specified, none to install.")
        return

    refresh_required = repo.install(package_repositories, key_assets=Path("/dev/null"))
    if refresh_required:
        emit.progress("Refreshing repositories")
        lifecycle_manager.refresh_packages_list()

    emit.progress("Package repositories installed")


def _install_overlay_repositories(overlay_dir: Path, project_info: ProjectInfo) -> None:
    if project_info.base != "bare":
        package_repositories = project_info.package_repositories
        repo.install_in_root(
            project_repositories=package_repositories,
            root=overlay_dir,
            key_assets=Path("/dev/null"),
        )


def _post_prime_callback(step_info: StepInfo) -> bool:
    prime_dir = step_info.prime_dir
    base_layer_dir = step_info.rootfs_dir
    files: set[str]

    files = step_info.state.files if step_info.state else set()

    layers.prune_prime_files(prime_dir, files, base_layer_dir)
    return True
