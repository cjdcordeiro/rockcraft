# Copyright 2021 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# For further info, check https://github.com/canonical/kerncraft

"""Craft-parts setup, lifecycle and plugins."""

from typing import Any, Dict, List, Optional, Set

from craft_cli import emit
from craft_parts import errors, plugins
from craft_parts.plugins import validator

# This is the plugin name expected in the project YAML
_PLUGIN_NAME = "pebble"


class _PebblePluginProperties(plugins.PluginProperties, plugins.base.PluginModel):
    """Supported attributes for the 'pebble' plugin."""

    source: str

    @classmethod
    def unmarshal(cls, data: Dict[str, Any]):
        """Populate properties from the part specification.

        :param data: A dictionary containing part properties.

        :return: The populated plugin properties data object.

        :raise pydantic.ValidationError: If validation fails.
        """
        plugin_data = plugins.base.extract_plugin_properties(
            data, plugin_name=_PLUGIN_NAME, required=["source"]
        )
        return cls(**plugin_data)


class _PebblePluginEnvironmentValidator(validator.PluginEnvironmentValidator):
    """Check the execution environment for the 'pebble' plugin.

    :param part_name: The part whose build environment is being validated.
    :param env: A string containing the build step environment setup.
    """

    def validate_environment(self, *, part_dependencies: Optional[List[str]] = None):
        """Ensure the environment contains dependencies needed by the plugin.

        :param part_dependencies: A list of the parts this part depends on.
        :raises PluginEnvironmentValidationError: If the environment is invalid.
        """
        # plugins.go_plugin.GoPluginEnvironmentValidator.validate_environment(self)
        version = self.validate_dependency(
            dependency="go",
            plugin_name="pebble",
            part_dependencies=part_dependencies,
            argument="version",
        )
        if not version.startswith("go version") and (
            part_dependencies is None or "go-deps" not in part_dependencies
        ):
            raise errors.PluginEnvironmentValidationError(
                part_name=self._part_name,
                reason=f"invalid go compiler version {version!r}",
            )


class _PebblePlugin(plugins.Plugin):
    """The Pebble daemon manager."""

    properties_class = _PebblePluginProperties
    validator_class = _PebblePluginEnvironmentValidator

    def get_build_snaps(self) -> Set[str]:
        """Return a set of required snaps to install in the build environment."""
        return set()

    def get_build_packages(self) -> Set[str]:
        """Return a set of required packages to install in the build environment."""
        # return set()
        return {"gcc", "golang-go"}

    def get_build_environment(self) -> Dict[str, str]:
        """Return a dictionary with the environment to use in the build step."""
        return {
            "GOBIN": "/bin",
            "GOOS": "linux",
            "CGO_ENABLED": "0",
        }

    def get_build_commands(self) -> List[str]:
        """Return a list of commands to run during the build step."""
        emit.debug("Pebble build command ...")
        return [
            "go mod download",
            f"go install -p {self._part_info.parallel_build_count} ./...",
        ]


def get_registration_details() -> Dict[str, Any]:
    """Return the plugin details for registration."""
    return {_PLUGIN_NAME: _PebblePlugin}
