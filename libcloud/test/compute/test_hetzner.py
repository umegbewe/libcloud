# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import unittest
from unittest.mock import patch

import requests_mock

from libcloud.test import LibcloudTestCase
from libcloud.common.types import LibcloudError, InvalidCredsError
from libcloud.compute.base import NodeAuthPassword
from libcloud.compute.types import (
    Provider,
    NodeState,
    StorageVolumeState,
    KeyPairDoesNotExistError,
)
from libcloud.common.exceptions import BaseHTTPError, RateLimitReachedError
from libcloud.compute.providers import get_driver
from libcloud.test.file_fixtures import ComputeFileFixtures
from libcloud.compute.drivers.hetzner import HetznerNodeDriver


class HetznerTest(LibcloudTestCase):
    fixtures = ComputeFileFixtures("hetzner")
    base_url = "https://api.hetzner.cloud/v1/"

    def setUp(self):
        super().setUp()
        self.mock = requests_mock.Mocker()
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.driver = HetznerNodeDriver("test-token")
        for resource in ("servers", "server_types", "images", "locations", "ssh_keys", "volumes"):
            self.mock.get(self.base_url + resource, json=self.load("list_" + resource))

    def load(self, name):
        return json.loads(self.fixtures.load(name + ".json"))

    def create_node(self, **kwargs):
        self.mock.post(self.base_url + "servers", json=self.load("create_server"), status_code=201)
        size = self.driver.list_sizes()[0]
        image = self.driver.list_images()[0]
        return self.driver.create_node("test-server", size, image, **kwargs)

    def test_provider_registration(self):
        self.assertIs(get_driver(Provider.HETZNER), HetznerNodeDriver)
        self.assertIs(get_driver("hetzner"), HetznerNodeDriver)

    def test_authentication_headers(self):
        self.driver.list_nodes()
        request = self.mock.last_request
        self.assertEqual(request.headers["Authorization"], "Bearer test-token")
        self.assertEqual(request.headers["Content-Type"], "application/json")
        self.assertEqual(request.headers["Accept"], "application/json")
        self.assertNotIn("test-token", request.url)

    def test_list_nodes(self):
        node = self.driver.list_nodes()[0]
        self.assertEqual(node.id, "42")
        self.assertEqual(node.name, "test-server")
        self.assertEqual(node.state, NodeState.RUNNING)
        self.assertEqual(node.public_ips, ["192.0.2.1", "2001:db8::1"])
        self.assertEqual(node.private_ips, ["10.0.0.2", "10.0.0.3"])
        self.assertEqual(node.size.ram, 4096)
        self.assertEqual(node.image.name, "ubuntu-24.04")
        self.assertEqual(node.extra["location"]["name"], "fsn1")
        self.assertIs(node.driver, self.driver)

    def test_private_only_node_with_deleted_image(self):
        data = self.load("list_servers")
        data["servers"][0]["public_net"] = {"ipv4": None, "ipv6": None}
        data["servers"][0]["image"] = None
        self.mock.get(self.base_url + "servers", json=data)
        node = self.driver.list_nodes()[0]
        self.assertEqual(node.public_ips, [])
        self.assertEqual(node.private_ips, ["10.0.0.2", "10.0.0.3"])
        self.assertIsNone(node.image)

    def test_ipv6_only_node(self):
        data = self.load("list_servers")
        data["servers"][0]["public_net"]["ipv4"] = None
        data["servers"][0]["private_net"] = []
        self.mock.get(self.base_url + "servers", json=data)
        node = self.driver.list_nodes()[0]
        self.assertEqual(node.public_ips, ["2001:db8::1"])
        self.assertEqual(node.private_ips, [])

    def test_wait_until_running_ipv6_only(self):
        data = self.load("list_servers")
        data["servers"][0]["public_net"]["ipv4"] = None
        self.mock.get(self.base_url + "servers", json=data)
        node = self.driver.list_nodes()[0]
        running = self.driver.wait_until_running([node], force_ipv4=False, timeout=1)
        self.assertEqual(running[0][0].id, node.id)
        self.assertEqual(running[0][1], ["2001:db8::1"])

    def test_wait_until_running_private_only(self):
        data = self.load("list_servers")
        data["servers"][0]["public_net"] = {"ipv4": None, "ipv6": None}
        self.mock.get(self.base_url + "servers", json=data)
        node = self.driver.list_nodes()[0]
        running = self.driver.wait_until_running([node], ssh_interface="private_ips", timeout=1)
        self.assertEqual(running[0][0].id, node.id)
        self.assertEqual(running[0][1], ["10.0.0.2", "10.0.0.3"])

    def test_node_states(self):
        states = {
            "initializing": NodeState.PENDING,
            "starting": NodeState.STARTING,
            "running": NodeState.RUNNING,
            "stopping": NodeState.STOPPING,
            "off": NodeState.STOPPED,
            "deleting": NodeState.PENDING,
            "migrating": NodeState.PENDING,
            "rebuilding": NodeState.PENDING,
            "unknown": NodeState.UNKNOWN,
            "new-status": NodeState.UNKNOWN,
        }
        for status, expected in states.items():
            with self.subTest(status=status):
                data = self.load("list_servers")
                data["servers"][0]["status"] = status
                self.mock.get(self.base_url + "servers", json=data)
                self.assertEqual(self.driver.list_nodes()[0].state, expected)

    def test_list_images(self):
        image = self.driver.list_images()[0]
        self.assertEqual(image.id, "1")
        self.assertEqual(image.name, "ubuntu-24.04")
        self.assertEqual(image.extra["architecture"], "x86")

    def test_snapshot_image_without_name(self):
        data = self.load("list_images")
        data["images"][0].update(name=None, description="My snapshot", type="snapshot")
        self.mock.get(self.base_url + "images", json=data)
        image = self.driver.list_images()[0]
        self.assertEqual(image.name, "My snapshot")
        self.assertEqual(image.extra["type"], "snapshot")

    def test_get_image(self):
        data = self.load("list_images")["images"][0]
        self.mock.get(self.base_url + "images/1", json={"image": data})
        self.assertEqual(self.driver.get_image("1").id, "1")

    def test_list_locations(self):
        location = self.driver.list_locations()[0]
        self.assertEqual(location.id, "1")
        self.assertEqual(location.name, "fsn1")
        self.assertEqual(location.country, "DE")
        self.assertEqual(location.extra["city"], "Falkenstein")

    def test_list_sizes(self):
        size = self.driver.list_sizes()[0]
        self.assertEqual(size.id, "1")
        self.assertEqual(size.name, "cpx22")
        self.assertEqual(size.ram, 4096)
        self.assertEqual(size.disk, 80)
        self.assertIsNone(size.price)
        self.assertEqual(size.extra["cores"], 2)

    def test_list_sizes_by_location(self):
        location = self.driver.list_locations()[0]
        size = self.driver.list_sizes(location)[0]
        self.assertEqual(size.price, 0.008)
        location.name = "unavailable-location"
        self.assertEqual(self.driver.list_sizes(location), [])

    def test_pagination_for_all_lists(self):
        for resource, method in (
            ("servers", self.driver.list_nodes),
            ("images", self.driver.list_images),
            ("server_types", self.driver.list_sizes),
            ("locations", self.driver.list_locations),
            ("ssh_keys", self.driver.list_key_pairs),
            ("volumes", self.driver.list_volumes),
        ):
            with self.subTest(resource=resource):
                first = self.load("list_" + resource)
                first["meta"]["pagination"]["next_page"] = 2
                second = self.load("list_" + resource)
                second[resource][0]["id"] = 99
                self.mock.get(self.base_url + resource, json=first)
                self.mock.get(self.base_url + resource + "?page=2&per_page=50", json=second)
                self.assertEqual(len(method()), 2)
                self.assertEqual(self.mock.last_request.qs["page"], ["2"])

    def test_empty_lists(self):
        self.mock.get(self.base_url + "servers", json={"servers": []})
        self.assertEqual(self.driver.list_nodes(), [])

    def test_nonadvancing_pagination(self):
        data = self.load("list_servers")
        data["meta"]["pagination"]["next_page"] = 1
        self.mock.get(self.base_url + "servers", json=data)
        with self.assertRaisesRegex(LibcloudError, "Invalid pagination"):
            self.driver.list_nodes()

    def test_create_node_generated_password(self):
        node = self.create_node()
        self.assertEqual(node.id, "42")
        self.assertEqual(node.extra["password"], "example-password")
        self.assertEqual(
            self.mock.last_request.json(),
            {"name": "test-server", "server_type": "1", "image": "1"},
        )

    def test_create_node_options(self):
        location = self.driver.list_locations()[0]
        key = self.driver.list_key_pairs()[0]
        self.create_node(
            location=location,
            ex_ssh_keys=[key, "other-key", 12],
            ex_user_data="#cloud-config\npackages: [nginx]",
            ex_labels={"environment": "test"},
            ex_networks=[4711],
            ex_public_net={"enable_ipv4": False, "enable_ipv6": True},
        )
        body = self.mock.last_request.json()
        self.assertEqual(body["location"], "1")
        self.assertEqual(body["ssh_keys"], [1, "other-key", 12])
        self.assertEqual(body["user_data"], "#cloud-config\npackages: [nginx]")
        self.assertEqual(body["labels"], {"environment": "test"})
        self.assertEqual(body["networks"], [4711])
        self.assertEqual(body["public_net"], {"enable_ipv4": False, "enable_ipv6": True})

    def test_create_node_with_no_password(self):
        data = self.load("create_server")
        data["root_password"] = None
        self.mock.post(self.base_url + "servers", json=data, status_code=201)
        node = self.driver.create_node(
            "test-server",
            self.driver.list_sizes()[0],
            self.driver.list_images()[0],
            ex_ssh_keys=["test-key"],
        )
        self.assertNotIn("password", node.extra)

    def test_create_node_does_not_send_auth(self):
        self.create_node(auth=NodeAuthPassword("existing-password"))
        self.assertEqual(
            self.mock.last_request.json(),
            {"name": "test-server", "server_type": "1", "image": "1"},
        )

    def test_deploy_node_authentication(self):
        for auth, ssh_key, password in (
            (NodeAuthPassword("existing-password"), None, "existing-password"),
            (None, None, "example-password"),
            (None, "/path/to/private-key", None),
        ):
            with self.subTest(auth=auth, ssh_key=ssh_key):
                data = self.load("create_server")
                if ssh_key:
                    data["root_password"] = None
                self.mock.post(self.base_url + "servers", json=data, status_code=201)
                size = self.driver.list_sizes()[0]
                image = self.driver.list_images()[0]
                with (
                    patch("libcloud.compute.ssh.have_paramiko", True),
                    patch.object(self.driver, "_connect_and_run_deployment_script") as connect,
                ):
                    node = self.driver.deploy_node(
                        deploy=object(),
                        name="test-server",
                        size=size,
                        image=image,
                        auth=auth,
                        ssh_key=ssh_key,
                        timeout=1,
                        ex_ssh_keys=["test-key"] if ssh_key else None,
                    )
                self.assertEqual(node.id, "42")
                connect.assert_called_once()
                self.assertEqual(connect.call_args.kwargs["ssh_password"], password)
                self.assertEqual(connect.call_args.kwargs["ssh_key_file"], ssh_key)
                creation = next(
                    request
                    for request in reversed(self.mock.request_history)
                    if request.method == "POST"
                )
                expected = {"name": "test-server", "server_type": "1", "image": "1"}
                if ssh_key:
                    expected["ssh_keys"] = ["test-key"]
                self.assertEqual(creation.json(), expected)

    def test_node_actions(self):
        node = self.driver.list_nodes()[0]
        for action, method in (
            ("reboot", self.driver.reboot_node),
            ("poweron", self.driver.start_node),
            ("shutdown", self.driver.stop_node),
        ):
            with self.subTest(action=action):
                self.mock.post(
                    self.base_url + "servers/42/actions/" + action,
                    json=self.load("action"),
                    status_code=201,
                )
                self.assertTrue(method(node))

    def test_destroy_node(self):
        self.mock.delete(self.base_url + "servers/42", json=self.load("action"))
        self.assertTrue(self.driver.list_nodes()[0].destroy())

    def test_ex_get_node(self):
        data = self.load("list_servers")["servers"][0]
        self.mock.get(self.base_url + "servers/42", json={"server": data})
        self.assertEqual(self.driver.ex_get_node("42").id, "42")

    def test_list_volumes(self):
        volume = self.driver.list_volumes()[0]
        self.assertEqual(volume.id, "4711")
        self.assertEqual(volume.name, "data")
        self.assertEqual(volume.size, 10)
        self.assertEqual(volume.state, StorageVolumeState.AVAILABLE)
        self.assertEqual(volume.extra["linux_device"], "/dev/disk/by-id/scsi-0HC_Volume_4711")
        self.assertEqual(volume.extra["location"]["name"], "fsn1")
        self.assertIsNone(volume.extra["server"])
        self.assertIs(volume.driver, self.driver)

    def test_volume_states(self):
        for status, server, expected in (
            ("available", None, StorageVolumeState.AVAILABLE),
            ("available", 42, StorageVolumeState.INUSE),
            ("creating", None, StorageVolumeState.CREATING),
            ("creating", 42, StorageVolumeState.CREATING),
            ("new-status", None, StorageVolumeState.UNKNOWN),
        ):
            with self.subTest(status=status, server=server):
                data = self.load("list_volumes")
                data["volumes"][0].update(status=status, server=server)
                self.mock.get(self.base_url + "volumes", json=data)
                self.assertEqual(self.driver.list_volumes()[0].state, expected)

    def test_empty_volumes(self):
        self.mock.get(self.base_url + "volumes", json={"volumes": []})
        self.assertEqual(self.driver.list_volumes(), [])

    def test_create_volume(self):
        location = self.driver.list_locations()[0]
        self.mock.post(self.base_url + "volumes", json=self.load("create_volume"), status_code=201)
        volume = self.driver.create_volume(10, "data", location)
        self.assertEqual(volume.id, "4711")
        self.assertEqual(volume.size, 10)
        self.assertEqual(volume.state, StorageVolumeState.CREATING)
        self.assertEqual(
            self.mock.last_request.json(), {"size": 10, "name": "data", "location": "fsn1"}
        )

    def test_create_volume_requires_location(self):
        with self.assertRaisesRegex(ValueError, "location is required"):
            self.driver.create_volume(10, "data")
        self.assertEqual(self.mock.call_count, 0)

    def test_create_volume_rejects_snapshot(self):
        with self.assertRaisesRegex(NotImplementedError, "volume snapshots"):
            self.driver.create_volume(10, "data", snapshot=object())
        self.assertEqual(self.mock.call_count, 0)

    def test_attach_volume(self):
        volume = self.driver.list_volumes()[0]
        node = self.driver.list_nodes()[0]
        self.mock.post(
            self.base_url + "volumes/4711/actions/attach",
            json=self.load("attach_volume"),
            status_code=201,
        )
        self.assertTrue(volume.attach(node))
        self.assertEqual(self.mock.last_request.json(), {"server": 42, "automount": False})

    def test_attach_volume_rejects_device(self):
        volume = self.driver.list_volumes()[0]
        node = self.driver.list_nodes()[0]
        requests_before = self.mock.call_count
        with self.assertRaisesRegex(ValueError, "device path automatically"):
            volume.attach(node, device="/dev/sdb")
        self.assertEqual(self.mock.call_count, requests_before)

    def test_attach_volume_error(self):
        volume = self.driver.list_volumes()[0]
        node = self.driver.list_nodes()[0]
        self.mock.post(
            self.base_url + "volumes/4711/actions/attach",
            status_code=400,
            json={"error": {"code": "invalid_input", "message": "Server in different location"}},
        )
        with self.assertRaisesRegex(BaseHTTPError, "Server in different location"):
            volume.attach(node)

    def test_detach_volume(self):
        data = self.load("list_volumes")
        data["volumes"][0]["server"] = 42
        self.mock.get(self.base_url + "volumes", json=data)
        volume = self.driver.list_volumes()[0]
        self.mock.post(
            self.base_url + "volumes/4711/actions/detach",
            json=self.load("detach_volume"),
            status_code=201,
        )
        self.assertTrue(volume.detach())
        self.assertFalse(self.mock.last_request.body)

    def test_destroy_volume(self):
        volume = self.driver.list_volumes()[0]
        self.mock.delete(self.base_url + "volumes/4711", status_code=204)
        self.assertTrue(volume.destroy())

    def test_destroy_protected_volume(self):
        volume = self.driver.list_volumes()[0]
        self.mock.delete(
            self.base_url + "volumes/4711",
            status_code=403,
            json={"error": {"code": "protected", "message": "Volume is protected"}},
        )
        with self.assertRaisesRegex(BaseHTTPError, "Volume is protected"):
            volume.destroy()

    def test_list_key_pairs(self):
        key = self.driver.list_key_pairs()[0]
        self.assertEqual(key.name, "test-key")
        self.assertEqual(key.extra["id"], 1)
        self.assertTrue(key.public_key.startswith("ssh-ed25519 "))
        self.assertIsNone(key.private_key)

    def test_get_key_pair(self):
        self.assertEqual(self.driver.get_key_pair("test-key").name, "test-key")
        self.assertEqual(self.mock.last_request.qs["name"], ["test-key"])

    def test_get_missing_key_pair(self):
        self.mock.get(self.base_url + "ssh_keys", json={"ssh_keys": []})
        with self.assertRaises(KeyPairDoesNotExistError):
            self.driver.get_key_pair("missing")

    def test_import_key_pair(self):
        data = self.load("create_ssh_key")
        self.mock.post(self.base_url + "ssh_keys", json=data, status_code=201)
        key = self.driver.import_key_pair_from_string("test-key", data["ssh_key"]["public_key"])
        self.assertEqual(key.extra["id"], 1)
        self.assertEqual(self.mock.last_request.json()["public_key"], key.public_key)

    def test_delete_key_pair(self):
        self.mock.delete(self.base_url + "ssh_keys/1", status_code=204)
        self.assertTrue(self.driver.delete_key_pair(self.driver.list_key_pairs()[0]))

    def test_invalid_credentials(self):
        self.mock.get(
            self.base_url + "servers",
            status_code=401,
            json={"error": {"code": "unauthorized", "message": "Invalid token"}},
        )
        with self.assertRaisesRegex(InvalidCredsError, "Invalid token"):
            self.driver.list_nodes()

    def test_api_error(self):
        self.mock.get(
            self.base_url + "servers",
            status_code=400,
            json={"error": {"code": "invalid_input", "message": "Invalid field"}},
        )
        with self.assertRaisesRegex(BaseHTTPError, "invalid_input: Invalid field"):
            self.driver.list_nodes()

    def test_read_only_token(self):
        node = self.driver.list_nodes()[0]
        self.mock.delete(
            self.base_url + "servers/42",
            status_code=403,
            json={"error": {"code": "forbidden", "message": "Insufficient permissions"}},
        )
        with self.assertRaisesRegex(BaseHTTPError, "Insufficient permissions") as error:
            self.driver.destroy_node(node)
        self.assertEqual(error.exception.code, 403)

    def test_rate_limit(self):
        self.mock.get(
            self.base_url + "servers",
            status_code=429,
            headers={"Retry-After": "10"},
            json={"error": {"code": "rate_limit_exceeded", "message": "Too many requests"}},
        )
        with self.assertRaises(RateLimitReachedError) as error:
            self.driver.list_nodes()
        self.assertEqual(error.exception.retry_after, 10)

    def test_plain_text_error(self):
        self.mock.get(self.base_url + "servers", status_code=502, text="Bad gateway")
        with self.assertRaisesRegex(BaseHTTPError, "Bad gateway"):
            self.driver.list_nodes()


if __name__ == "__main__":
    unittest.main()
