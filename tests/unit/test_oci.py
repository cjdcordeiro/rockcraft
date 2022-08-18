# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright 2021-2022 Canonical Ltd.
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

import datetime
import hashlib
import json
import os
import tarfile
from pathlib import Path
from unittest.mock import ANY, call, mock_open, patch

import pytest

import tests
from rockcraft import oci


@pytest.fixture
def mock_run(mocker):
    yield mocker.patch("rockcraft.oci._process_run")


@pytest.fixture
def mock_compress_layer(mocker):
    yield mocker.patch("rockcraft.oci._compress_layer")


@pytest.fixture
def mock_rmtree(mocker):
    yield mocker.patch("shutil.rmtree")


@pytest.fixture
def mock_mkdir(mocker):
    yield mocker.patch("pathlib.Path.mkdir")


@pytest.fixture
def mock_mkdtemp(mocker):
    yield mocker.patch("tempfile.mkdtemp")


@pytest.fixture
def mock_inject_variant(mocker):
    yield mocker.patch("rockcraft.oci._inject_architecture_variant")


@pytest.fixture
def mock_read_bytes(mocker):
    yield mocker.patch("pathlib.Path.read_bytes")


@pytest.fixture
def mock_write_bytes(mocker):
    yield mocker.patch("pathlib.Path.write_bytes")


@tests.linux_only
class TestImage:
    """OCI image manipulation."""

    def test_attributes(self):
        image = oci.Image("a:b", Path("/c"))
        assert image.image_name == "a:b"
        assert image.path == Path("/c")

    def test_from_docker_registry(self, mock_run, new_dir):
        image, source_image = oci.Image.from_docker_registry(
            "a:b", image_dir=Path("images/dir"), arch="amd64", variant=None
        )
        assert Path("images/dir").is_dir()
        assert image.image_name == "a:b"
        assert source_image == "docker://a:b"
        assert image.path == Path("images/dir")
        assert mock_run.mock_calls == [
            call(
                [
                    "skopeo",
                    "--insecure-policy",
                    "--override-arch",
                    "amd64",
                    "copy",
                    "docker://a:b",
                    "oci:images/dir/a:b",
                ]
            )
        ]
        mock_run.reset_mock()
        _ = oci.Image.from_docker_registry(
            "a:b", image_dir=Path("images/dir"), arch="arm64", variant="v8"
        )
        assert mock_run.mock_calls == [
            call(
                [
                    "skopeo",
                    "--insecure-policy",
                    "--override-arch",
                    "arm64",
                    "--override-variant",
                    "v8",
                    "copy",
                    "docker://a:b",
                    "oci:images/dir/a:b",
                ]
            )
        ]

    def test_new_oci_image(self, mock_inject_variant, mock_run):
        image_dir = Path("images/dir")
        image, source_image = oci.Image.new_oci_image(
            "bare:latest", image_dir=image_dir, arch="amd64"
        )
        assert image_dir.is_dir()
        assert image.image_name == "bare:latest"
        assert source_image == f"oci:{str(image_dir)}/bare:latest"
        assert image.path == Path("images/dir")
        assert mock_run.mock_calls == [
            call(["umoci", "init", "--layout", f"{image_dir}/bare"]),
            call(["umoci", "new", "--image", f"{image_dir}/bare:latest"]),
            call(
                [
                    "umoci",
                    "config",
                    "--image",
                    f"{image_dir}/bare:latest",
                    "--architecture",
                    "amd64",
                    "--no-history",
                ]
            ),
        ]
        mock_inject_variant.assert_not_called()
        _ = oci.Image.new_oci_image(
            "bare:latest", image_dir=image_dir, arch="foo", variant="bar"
        )
        mock_inject_variant.assert_called_once_with(image_dir / "bare", "bar")

    def test_copy_to(self, mock_run):
        image = oci.Image("a:b", Path("/c"))
        new_image = image.copy_to("d:e", image_dir=Path("/f"))
        assert new_image.image_name == "d:e"
        assert new_image.path == Path("/f")
        assert mock_run.mock_calls == [
            call(
                [
                    "skopeo",
                    "--insecure-policy",
                    "copy",
                    "oci:/c/a:b",
                    "oci:/f/d:e",
                ]
            )
        ]

    def test_extract_to(self, mock_run, new_dir):
        image = oci.Image("a:b", Path("/c"))
        bundle_path = image.extract_to(Path("bundle/dir"))
        assert Path("bundle/dir").is_dir()
        assert bundle_path == Path("bundle/dir/a-b/rootfs")
        assert mock_run.mock_calls == [
            call(["umoci", "unpack", "--image", "/c/a:b", "bundle/dir/a-b"])
        ]

    def test_extract_to_existing_dir(self, mock_run, new_dir):
        image = oci.Image("a:b", Path("c"))
        Path("bundle/dir/a-b").mkdir(parents=True)
        Path("bundle/dir/a-b/foo.txt").touch()

        bundle_path = image.extract_to(Path("bundle/dir"))
        assert Path("bundle/dir/a-b/foo.txt").exists() is False
        assert bundle_path == Path("bundle/dir/a-b/rootfs")

    def test_add_layer(self, mocker, mock_run, new_dir):
        image = oci.Image("a:b", new_dir / "c")
        Path("c").mkdir()
        Path("layer_dir").mkdir()
        Path("layer_dir/foo.txt").touch()
        pid = os.getpid()

        spy_add = mocker.spy(tarfile.TarFile, "add")

        image.add_layer("tag", Path("layer_dir"))
        assert spy_add.mock_calls == [call(ANY, "./foo.txt")]
        expected_cmd = [
            "umoci",
            "raw",
            "add-layer",
            "--image",
            str(new_dir / "c/a:b"),
            str(new_dir / f"c/.temp_layer.{pid}.tar"),
            "--tag",
            "tag",
        ]
        assert mock_run.mock_calls == [
            call(expected_cmd + ["--history.created_by", " ".join(expected_cmd)])
        ]

    def test_to_docker_daemon(self, mock_run):
        image = oci.Image("a:b", Path("/c"))
        image.to_docker_daemon("tag")
        assert mock_run.mock_calls == [
            call(
                [
                    "skopeo",
                    "--insecure-policy",
                    "copy",
                    "oci:/c/a:tag",
                    "docker-daemon:a:tag",
                ]
            )
        ]

    def test_to_oci_archive(self, mock_run):
        image = oci.Image("a:b", Path("/c"))
        image.to_oci_archive("tag", filename="foobar")
        assert mock_run.mock_calls == [
            call(
                [
                    "skopeo",
                    "--insecure-policy",
                    "copy",
                    "oci:/c/a:tag",
                    "oci-archive:foobar:tag",
                ]
            )
        ]

    def test_digest(self, mocker):
        source_image = "docker://ubuntu:22.04"
        image = oci.Image("a:b", Path("/c"))
        mock_output = mocker.patch(
            "subprocess.check_output", return_value="000102030405060708090a0b0c0d0e0f"
        )

        digest = image.digest(source_image)
        assert mock_output.mock_calls == [
            call(
                ["skopeo", "inspect", "--format", "{{.Digest}}", "-n", source_image],
                text=True,
            )
        ]
        assert digest == bytes([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15])

    def test_set_entrypoint(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        image = oci.Image("a:b", Path("/c"))

        image.set_entrypoint(["arg1", "arg2"])

        assert mock_run.mock_calls == [
            call(
                [
                    "umoci",
                    "config",
                    "--image",
                    "/c/a:b",
                    "--clear=config.entrypoint",
                    "--config.entrypoint",
                    "arg1",
                    "--config.entrypoint",
                    "arg2",
                ],
                capture_output=True,
                check=True,
                universal_newlines=True,
            )
        ]

    def test_set_cmd(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        image = oci.Image("a:b", Path("/c"))

        image.set_cmd(["arg1", "arg2"])

        assert mock_run.mock_calls == [
            call(
                [
                    "umoci",
                    "config",
                    "--image",
                    "/c/a:b",
                    "--clear=config.cmd",
                    "--config.cmd",
                    "arg1",
                    "--config.cmd",
                    "arg2",
                ],
                capture_output=True,
                check=True,
                universal_newlines=True,
            )
        ]

    def test_set_env(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        image = oci.Image("a:b", Path("/c"))

        image.set_env([{"NAME1": "VALUE1"}, {"NAME2": "VALUE2"}])

        assert mock_run.mock_calls == [
            call(
                [
                    "umoci",
                    "config",
                    "--image",
                    "/c/a:b",
                    "--clear=config.env",
                    "--config.env",
                    "NAME1=VALUE1",
                    "--config.env",
                    "NAME2=VALUE2",
                ],
                capture_output=True,
                check=True,
                universal_newlines=True,
            )
        ]

    def test_set_control_data(
        self, mock_compress_layer, mock_rmtree, mock_mkdir, mock_mkdtemp, mocker
    ):
        mock_run = mocker.patch("subprocess.run")
        image = oci.Image("a:b", Path("/c"))

        mock_control_data_path = "layer_dir"
        mock_mkdtemp.return_value = mock_control_data_path

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        metadata = {"name": "rock-name", "version": 1, "created": now}

        expected = (
            f"created: '{now}'" + "{n}" "name: rock-name{n}" "version: 1{n}"
        ).format(n=os.linesep)

        mocked_data = {"writes": ""}

        def mock_write(s):
            mocked_data["writes"] += s

        m = mock_open()
        with patch("pathlib.Path.open", m):
            m.return_value.write = mock_write
            image.set_control_data(metadata)

        assert mocked_data["writes"] == expected
        mock_mkdtemp.assert_called_once()
        mock_mkdir.assert_called_once()
        mock_compress_layer.assert_called_once_with(
            Path(mock_control_data_path),
            Path(f"/c/.temp_layer.control_data.{os.getpid()}.tar"),
        )
        expected_cmd = [
            "umoci",
            "raw",
            "add-layer",
            "--image",
            str("/c/a:b"),
            str(f"/c/.temp_layer.control_data.{os.getpid()}.tar"),
        ]
        assert mock_run.mock_calls == [
            call(
                expected_cmd + ["--history.created_by", " ".join(expected_cmd)],
                capture_output=True,
                check=True,
                universal_newlines=True,
            )
        ]
        mock_rmtree.assert_called_once_with(Path(mock_control_data_path))

    def test_set_annotations(self, mocker):
        mock_run = mocker.patch("subprocess.run")
        image = oci.Image("a:b", Path("/c"))

        image.set_annotations({"NAME1": "VALUE1", "NAME2": "VALUE2"})

        assert mock_run.mock_calls == [
            call(
                [
                    "umoci",
                    "config",
                    "--image",
                    "/c/a:b",
                    "--clear=config.labels",
                    "--config.label",
                    "NAME1=VALUE1",
                    "--config.label",
                    "NAME2=VALUE2",
                ],
                capture_output=True,
                check=True,
                universal_newlines=True,
            ),
            call(
                [
                    "umoci",
                    "config",
                    "--image",
                    "/c/a:b",
                    "--clear=manifest.annotations",
                    "--manifest.annotation",
                    "NAME1=VALUE1",
                    "--manifest.annotation",
                    "NAME2=VALUE2",
                ],
                capture_output=True,
                check=True,
                universal_newlines=True,
            ),
        ]

    def test_inject_architecture_variant(self, mock_read_bytes, mock_write_bytes):
        test_index = {"manifests": [{"digest": "sha256:foomanifest"}]}
        test_manifest = {"config": {"digest": "sha256:fooconfig"}}
        test_config = {}
        mock_read_bytes.side_effect = [
            json.dumps(test_index),
            json.dumps(test_manifest),
            json.dumps(test_config),
        ]
        test_variant = "v0"

        new_test_config = {**test_config, **{"variant": test_variant}}
        new_test_config_bytes = json.dumps(new_test_config).encode("utf-8")

        new_image_config_digest = hashlib.sha256(new_test_config_bytes).hexdigest()
        new_test_manifest = {
            **test_manifest,
            **{
                "config": {
                    "digest": f"sha256:{new_image_config_digest}",
                    "size": len(new_test_config_bytes),
                }
            },
        }
        new_test_manifest_bytes = json.dumps(new_test_manifest).encode("utf-8")
        new_test_manifest_digest = hashlib.sha256(new_test_manifest_bytes).hexdigest()

        new_test_index = {
            **test_index,
            **{
                "manifests": [
                    {
                        "digest": f"sha256:{new_test_manifest_digest}",
                        "size": len(new_test_manifest_bytes),
                    }
                ]
            },
        }

        oci._inject_architecture_variant(Path("img"), test_variant)

        assert mock_read_bytes.call_count == 3
        assert mock_write_bytes.mock_calls == [
            call(new_test_config_bytes),
            call(new_test_manifest_bytes),
            call(json.dumps(new_test_index).encode("utf-8")),
        ]

    def test_compress_layer(self, mocker, new_dir):
        Path("c").mkdir()
        Path("layer_dir").mkdir()
        Path("layer_dir/bar.txt").touch()

        spy_add = mocker.spy(tarfile.TarFile, "add")

        oci._compress_layer(  # pylint: disable=protected-access
            Path("layer_dir"), Path("./bar.tar")
        )
        assert spy_add.call_count == 2
