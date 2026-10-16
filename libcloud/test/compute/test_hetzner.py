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

import sys
import json
import unittest

from libcloud.test import MockHttp, LibcloudTestCase
from libcloud.utils.py3 import httplib, parse_qs, urlparse
from libcloud.common.types import InvalidCredsError
from libcloud.compute.types import NodeState, StorageVolumeState, KeyPairDoesNotExistError
from libcloud.common.exceptions import BaseHTTPError
from libcloud.test.file_fixtures import ComputeFileFixtures
from libcloud.compute.drivers.hetzner import HetznerNodeDriver


class HetznerTest(LibcloudTestCase):
    def setUp(self):
        HetznerNodeDriver.connectionCls.conn_class = HetznerMockHttp
        HetznerMockHttp.type = None
        self.driver = HetznerNodeDriver("token")

    def test_list_nodes(self):
        nodes = self.driver.list_nodes()
        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        self.assertEqual(node.id, "42")
        self.assertEqual(node.name, "test-server")
        self.assertEqual(node.state, NodeState.RUNNING)
        self.assertEqual(node.public_ips, ["192.0.2.1", "2001:db8::1"])
        self.assertEqual(node.private_ips, ["10.0.0.2", "10.0.0.3"])
        self.assertEqual(node.size.ram, 4096)
        self.assertEqual(node.image.name, "ubuntu-24.04")

    def test_list_nodes_private_only(self):
        HetznerMockHttp.type = "PRIVATE_ONLY"
        node = self.driver.list_nodes()[0]
        self.assertEqual(node.public_ips, [])
        self.assertEqual(node.private_ips, ["10.0.0.2", "10.0.0.3"])
        self.assertIsNone(node.image)

    def test_list_nodes_ipv6_only(self):
        HetznerMockHttp.type = "IPV6_ONLY"
        node = self.driver.list_nodes()[0]
        self.assertEqual(node.public_ips, ["2001:db8::1"])
        self.assertEqual(node.private_ips, [])

    def test_list_nodes_paginated(self):
        HetznerMockHttp.type = "PAGINATED"
        nodes = self.driver.list_nodes()
        self.assertEqual([node.id for node in nodes], ["42", "43"])

    def test_list_images(self):
        images = self.driver.list_images()
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].id, "1")
        self.assertEqual(images[0].name, "ubuntu-24.04")
        self.assertEqual(images[0].extra["architecture"], "x86")

    def test_list_snapshot_images(self):
        HetznerMockHttp.type = "SNAPSHOT"
        image = self.driver.list_images()[0]
        self.assertEqual(image.name, "My snapshot")
        self.assertEqual(image.extra["type"], "snapshot")

    def test_get_image(self):
        image = self.driver.get_image("1")
        self.assertEqual(image.id, "1")
        self.assertEqual(image.name, "ubuntu-24.04")

    def test_list_locations(self):
        locations = self.driver.list_locations()
        self.assertEqual(len(locations), 1)
        self.assertEqual(locations[0].id, "1")
        self.assertEqual(locations[0].name, "fsn1")
        self.assertEqual(locations[0].country, "DE")

    def test_list_sizes(self):
        sizes = self.driver.list_sizes()
        self.assertEqual(len(sizes), 1)
        size = sizes[0]
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

    def test_create_node(self):
        size = self.driver.list_sizes()[0]
        image = self.driver.list_images()[0]
        node = self.driver.create_node("test-server", size, image)
        self.assertEqual(node.id, "42")
        self.assertEqual(node.state, NodeState.PENDING)
        self.assertEqual(node.extra["password"], "example-password")

    def test_create_node_options(self):
        size = self.driver.list_sizes()[0]
        image = self.driver.list_images()[0]
        location = self.driver.list_locations()[0]
        key = self.driver.list_key_pairs()[0]
        HetznerMockHttp.type = "OPTIONS"
        node = self.driver.create_node(
            "test-server",
            size,
            image,
            location,
            ex_ssh_keys=[key, "other-key", 12],
            ex_user_data="#cloud-config",
            ex_labels={"environment": "test"},
            ex_networks=[4711],
            ex_public_net={"enable_ipv4": False, "enable_ipv6": True},
        )
        self.assertEqual(node.id, "42")
        self.assertNotIn("password", node.extra)

    def test_reboot_node(self):
        node = self.driver.list_nodes()[0]
        self.assertTrue(self.driver.reboot_node(node))

    def test_start_node(self):
        node = self.driver.list_nodes()[0]
        self.assertTrue(self.driver.start_node(node))

    def test_stop_node(self):
        node = self.driver.list_nodes()[0]
        self.assertTrue(self.driver.stop_node(node))

    def test_destroy_node(self):
        node = self.driver.list_nodes()[0]
        HetznerMockHttp.type = "DELETE"
        self.assertTrue(self.driver.destroy_node(node))

    def test_ex_get_node(self):
        node = self.driver.ex_get_node("42")
        self.assertEqual(node.id, "42")
        self.assertEqual(node.name, "test-server")

    def test_list_volumes(self):
        volumes = self.driver.list_volumes()
        self.assertEqual(len(volumes), 1)
        volume = volumes[0]
        self.assertEqual(volume.id, "4711")
        self.assertEqual(volume.name, "data")
        self.assertEqual(volume.size, 10)
        self.assertEqual(volume.state, StorageVolumeState.AVAILABLE)
        self.assertEqual(volume.extra["linux_device"], "/dev/disk/by-id/scsi-0HC_Volume_4711")
        self.assertIsNone(volume.extra["server"])

    def test_list_volumes_attached(self):
        HetznerMockHttp.type = "ATTACHED"
        volume = self.driver.list_volumes()[0]
        self.assertEqual(volume.state, StorageVolumeState.INUSE)
        self.assertEqual(volume.extra["server"], 42)

    def test_list_volumes_empty(self):
        HetznerMockHttp.type = "EMPTY"
        self.assertEqual(self.driver.list_volumes(), [])

    def test_create_volume(self):
        location = self.driver.list_locations()[0]
        volume = self.driver.create_volume(10, "data", location)
        self.assertEqual(volume.id, "4711")
        self.assertEqual(volume.size, 10)
        self.assertEqual(volume.state, StorageVolumeState.CREATING)

    def test_create_volume_requires_location(self):
        self.assertRaises(ValueError, self.driver.create_volume, 10, "data")

    def test_create_volume_rejects_snapshot(self):
        self.assertRaises(
            NotImplementedError, self.driver.create_volume, 10, "data", snapshot=object()
        )

    def test_attach_volume(self):
        volume = self.driver.list_volumes()[0]
        node = self.driver.list_nodes()[0]
        self.assertTrue(self.driver.attach_volume(node, volume))

    def test_attach_volume_rejects_device(self):
        volume = self.driver.list_volumes()[0]
        node = self.driver.list_nodes()[0]
        self.assertRaises(ValueError, self.driver.attach_volume, node, volume, device="/dev/sdb")

    def test_detach_volume(self):
        HetznerMockHttp.type = "ATTACHED"
        volume = self.driver.list_volumes()[0]
        HetznerMockHttp.type = None
        self.assertTrue(self.driver.detach_volume(volume))

    def test_destroy_volume(self):
        volume = self.driver.list_volumes()[0]
        self.assertTrue(self.driver.destroy_volume(volume))

    def test_list_key_pairs(self):
        keys = self.driver.list_key_pairs()
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0].name, "test-key")
        self.assertEqual(keys[0].extra["id"], 1)
        self.assertTrue(keys[0].public_key.startswith("ssh-ed25519 "))

    def test_get_key_pair(self):
        key = self.driver.get_key_pair("test-key")
        self.assertEqual(key.name, "test-key")

    def test_get_missing_key_pair(self):
        self.assertRaises(KeyPairDoesNotExistError, self.driver.get_key_pair, "missing")

    def test_import_key_pair(self):
        public_key = self.driver.list_key_pairs()[0].public_key
        key = self.driver.import_key_pair_from_string("test-key", public_key)
        self.assertEqual(key.name, "test-key")
        self.assertEqual(key.public_key, public_key)

    def test_delete_key_pair(self):
        key = self.driver.list_key_pairs()[0]
        self.assertTrue(self.driver.delete_key_pair(key))

    def test_authentication(self):
        HetznerMockHttp.type = "UNAUTHORIZED"
        self.assertRaises(InvalidCredsError, self.driver.list_nodes)

    def test_api_error(self):
        HetznerMockHttp.type = "ERROR"
        with self.assertRaisesRegex(BaseHTTPError, "invalid_input: Invalid field"):
            self.driver.list_nodes()


class HetznerMockHttp(MockHttp):
    fixtures = ComputeFileFixtures("hetzner")

    def _v1_servers(self, method, url, body, headers):
        self.assertEqual(headers["Authorization"], "Bearer token")
        if method == "POST":
            self.assertEqual(
                json.loads(body), {"name": "test-server", "server_type": "1", "image": "1"}
            )
            body = self.fixtures.load("create_server.json")
            return (httplib.CREATED, body, {}, httplib.responses[httplib.CREATED])
        body = self.fixtures.load("list_servers.json")
        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _v1_servers_OPTIONS(self, method, url, body, headers):
        self.assertEqual(method, "POST")
        self.assertEqual(
            json.loads(body),
            {
                "name": "test-server",
                "server_type": "1",
                "image": "1",
                "location": "1",
                "ssh_keys": [1, "other-key", 12],
                "user_data": "#cloud-config",
                "labels": {"environment": "test"},
                "networks": [4711],
                "public_net": {"enable_ipv4": False, "enable_ipv6": True},
            },
        )
        data = json.loads(self.fixtures.load("create_server.json"))
        data["root_password"] = None
        return (httplib.CREATED, json.dumps(data), {}, httplib.responses[httplib.CREATED])

    def _v1_servers_PRIVATE_ONLY(self, method, url, body, headers):
        data = json.loads(self.fixtures.load("list_servers.json"))
        data["servers"][0].update(public_net={"ipv4": None, "ipv6": None}, image=None)
        return (httplib.OK, json.dumps(data), {}, httplib.responses[httplib.OK])

    def _v1_servers_IPV6_ONLY(self, method, url, body, headers):
        data = json.loads(self.fixtures.load("list_servers.json"))
        data["servers"][0]["public_net"]["ipv4"] = None
        data["servers"][0]["private_net"] = []
        return (httplib.OK, json.dumps(data), {}, httplib.responses[httplib.OK])

    def _v1_servers_PAGINATED(self, method, url, body, headers):
        data = json.loads(self.fixtures.load("list_servers.json"))
        page = int(parse_qs(urlparse.urlparse(url).query)["page"][0])
        if page == 1:
            data["meta"]["pagination"]["next_page"] = 2
        else:
            self.assertEqual(page, 2)
            data["servers"][0]["id"] = 43
            data["servers"][0]["name"] = "second-server"
            data["meta"]["pagination"].update(page=2, previous_page=1)
        return (httplib.OK, json.dumps(data), {}, httplib.responses[httplib.OK])

    def _v1_servers_42(self, method, url, body, headers):
        self.assertEqual(method, "GET")
        data = json.loads(self.fixtures.load("list_servers.json"))
        body = json.dumps({"server": data["servers"][0]})
        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _v1_servers_42_DELETE(self, method, url, body, headers):
        self.assertEqual(method, "DELETE")
        data = json.loads(self.fixtures.load("action.json"))
        data["action"]["command"] = "delete_server"
        body = json.dumps(data)
        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _v1_servers_42_actions_reboot(self, method, url, body, headers):
        self.assertEqual(method, "POST")
        body = self.fixtures.load("action.json")
        return (httplib.CREATED, body, {}, httplib.responses[httplib.CREATED])

    def _v1_servers_42_actions_poweron(self, method, url, body, headers):
        self.assertEqual(method, "POST")
        body = self.fixtures.load("action.json")
        return (httplib.CREATED, body, {}, httplib.responses[httplib.CREATED])

    def _v1_servers_42_actions_shutdown(self, method, url, body, headers):
        self.assertEqual(method, "POST")
        body = self.fixtures.load("action.json")
        return (httplib.CREATED, body, {}, httplib.responses[httplib.CREATED])

    def _v1_images(self, method, url, body, headers):
        body = self.fixtures.load("list_images.json")
        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _v1_images_SNAPSHOT(self, method, url, body, headers):
        data = json.loads(self.fixtures.load("list_images.json"))
        data["images"][0].update(name=None, description="My snapshot", type="snapshot")
        return (httplib.OK, json.dumps(data), {}, httplib.responses[httplib.OK])

    def _v1_images_1(self, method, url, body, headers):
        data = json.loads(self.fixtures.load("list_images.json"))
        body = json.dumps({"image": data["images"][0]})
        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _v1_locations(self, method, url, body, headers):
        body = self.fixtures.load("list_locations.json")
        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _v1_server_types(self, method, url, body, headers):
        body = self.fixtures.load("list_server_types.json")
        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _v1_volumes(self, method, url, body, headers):
        if method == "POST":
            self.assertEqual(json.loads(body), {"size": 10, "name": "data", "location": "fsn1"})
            body = self.fixtures.load("create_volume.json")
            return (httplib.CREATED, body, {}, httplib.responses[httplib.CREATED])
        body = self.fixtures.load("list_volumes.json")
        return (httplib.OK, body, {}, httplib.responses[httplib.OK])

    def _v1_volumes_ATTACHED(self, method, url, body, headers):
        data = json.loads(self.fixtures.load("list_volumes.json"))
        data["volumes"][0]["server"] = 42
        return (httplib.OK, json.dumps(data), {}, httplib.responses[httplib.OK])

    def _v1_volumes_EMPTY(self, method, url, body, headers):
        return (httplib.OK, '{"volumes": []}', {}, httplib.responses[httplib.OK])

    def _v1_volumes_4711(self, method, url, body, headers):
        self.assertEqual(method, "DELETE")
        return (httplib.NO_CONTENT, "", {}, httplib.responses[httplib.NO_CONTENT])

    def _v1_volumes_4711_actions_attach(self, method, url, body, headers):
        self.assertEqual(method, "POST")
        self.assertEqual(json.loads(body), {"server": 42, "automount": False})
        body = self.fixtures.load("attach_volume.json")
        return (httplib.CREATED, body, {}, httplib.responses[httplib.CREATED])

    def _v1_volumes_4711_actions_detach(self, method, url, body, headers):
        self.assertEqual(method, "POST")
        self.assertFalse(body)
        body = self.fixtures.load("detach_volume.json")
        return (httplib.CREATED, body, {}, httplib.responses[httplib.CREATED])

    def _v1_ssh_keys(self, method, url, body, headers):
        if method == "POST":
            body_data = json.loads(body)
            data = json.loads(self.fixtures.load("create_ssh_key.json"))
            self.assertEqual(
                body_data,
                {"name": data["ssh_key"]["name"], "public_key": data["ssh_key"]["public_key"]},
            )
            return (httplib.CREATED, json.dumps(data), {}, httplib.responses[httplib.CREATED])
        data = json.loads(self.fixtures.load("list_ssh_keys.json"))
        params = parse_qs(urlparse.urlparse(url).query)
        if "name" in params:
            data["ssh_keys"] = [key for key in data["ssh_keys"] if key["name"] == params["name"][0]]
        return (httplib.OK, json.dumps(data), {}, httplib.responses[httplib.OK])

    def _v1_ssh_keys_1(self, method, url, body, headers):
        self.assertEqual(method, "DELETE")
        return (httplib.NO_CONTENT, "", {}, httplib.responses[httplib.NO_CONTENT])

    def _v1_servers_UNAUTHORIZED(self, method, url, body, headers):
        body = '{"error": {"code": "unauthorized", "message": "Invalid token"}}'
        return (httplib.UNAUTHORIZED, body, {}, httplib.responses[httplib.UNAUTHORIZED])

    def _v1_servers_ERROR(self, method, url, body, headers):
        body = '{"error": {"code": "invalid_input", "message": "Invalid field"}}'
        return (httplib.BAD_REQUEST, body, {}, httplib.responses[httplib.BAD_REQUEST])


if __name__ == "__main__":
    sys.exit(unittest.main())
