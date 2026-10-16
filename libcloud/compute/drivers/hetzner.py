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
"""Compute driver for the Hetzner Cloud API."""

import json
from ipaddress import ip_network

from libcloud.utils.py3 import httplib
from libcloud.common.base import JsonResponse, ConnectionKey
from libcloud.common.types import LibcloudError, InvalidCredsError
from libcloud.compute.base import (
    Node,
    KeyPair,
    NodeSize,
    NodeImage,
    NodeDriver,
    NodeLocation,
    StorageVolume,
)
from libcloud.compute.types import (
    Provider,
    NodeState,
    StorageVolumeState,
    KeyPairDoesNotExistError,
)

__all__ = ["HetznerNodeDriver"]


class HetznerResponse(JsonResponse):
    def success(self):
        return 200 <= self.status < 300

    def parse_error(self):
        try:
            error = json.loads(self.body).get("error", {})
            message = "%s: %s" % (error.get("code", self.status), error.get("message", self.body))
        except (ValueError, AttributeError):
            message = self.body or self.error

        if self.status == httplib.UNAUTHORIZED:
            raise InvalidCredsError(message, driver=self.connection.driver)
        return message


class HetznerConnection(ConnectionKey):
    host = "api.hetzner.cloud"
    responseCls = HetznerResponse

    def add_default_headers(self, headers):
        headers["Authorization"] = "Bearer %s" % self.key
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        return headers

    def encode_data(self, data):
        return json.dumps(data)


class HetznerNodeDriver(NodeDriver):
    """Manage Hetzner Cloud servers using a project API token as ``key``."""

    connectionCls = HetznerConnection
    type = Provider.HETZNER
    name = "Hetzner Cloud"
    website = "https://www.hetzner.com/cloud"
    features = {"create_node": ["generates_password"]}

    NODE_STATE_MAP = {
        "running": NodeState.RUNNING,
        "initializing": NodeState.PENDING,
        "starting": NodeState.STARTING,
        "stopping": NodeState.STOPPING,
        "off": NodeState.STOPPED,
        "deleting": NodeState.PENDING,
        "migrating": NodeState.PENDING,
        "rebuilding": NodeState.PENDING,
        "unknown": NodeState.UNKNOWN,
    }

    def list_nodes(self):
        return [self._to_node(data) for data in self._list_resources("servers")]

    def list_images(self, location=None):
        """List system, snapshot, and backup images (available in all locations)."""
        return [self._to_image(data) for data in self._list_resources("images")]

    def get_image(self, image_id):
        data = self.connection.request("/v1/images/%s" % image_id).object
        return self._to_image(data["image"])

    def list_locations(self):
        return [self._to_location(data) for data in self._list_resources("locations")]

    def list_sizes(self, location=None):
        """
        List server types, optionally restricted to a location.

        With a location, ``price`` is the net hourly price in the project
        owner's currency. Without one, ``price`` is ``None``; all location
        prices are retained in ``extra['prices']``.
        """
        sizes = []
        for data in self._list_resources("server_types"):
            if location is not None and not any(
                item["name"] == location.name for item in data.get("locations", [])
            ):
                continue
            sizes.append(self._to_size(data, location))
        return sizes

    def create_node(
        self,
        name,
        size,
        image,
        location=None,
        auth=None,
        ex_ssh_keys=None,
        ex_user_data=None,
        ex_labels=None,
        ex_networks=None,
        ex_public_net=None,
    ):
        """
        Create and start a server.

        :param name: Server hostname, unique within the project.
        :type name: ``str``
        :param size: Server type to create.
        :type size: :class:`~libcloud.compute.base.NodeSize`
        :param image: Image to boot from.
        :type image: :class:`~libcloud.compute.base.NodeImage`
        :param location: Location to create the server in, or ``None`` to let
            Hetzner select a location.
        :type location: :class:`~libcloud.compute.base.NodeLocation` or ``None``
        :param auth: Accepted for deployment compatibility; does not configure
            server credentials.
        :type auth: :class:`~libcloud.compute.base.NodeAuthPassword` or
            :class:`~libcloud.compute.base.NodeAuthSSHKey` or ``None``
        :param ex_ssh_keys: Existing SSH key names, IDs, or ``KeyPair`` objects.
        :type ex_ssh_keys: ``list``
        :param ex_user_data: Cloud-init configuration (at most 32 KiB).
        :type ex_user_data: ``str``
        :param ex_labels: Labels to associate with the server.
        :type ex_labels: ``dict``
        :param ex_networks: Private network IDs to attach at creation.
        :type ex_networks: ``list`` of ``int``
        :param ex_public_net: Public network options, such as
            ``{'enable_ipv4': False, 'enable_ipv6': True}``.
        :type ex_public_net: ``dict``
        :return: The newly created server, which may still be initializing.
        :rtype: :class:`~libcloud.compute.base.Node`

        Import public keys with ``import_key_pair_from_string`` and pass them
        in ``ex_ssh_keys``. The ``auth`` argument is accepted for compatibility
        with ``deploy_node`` and is not used to configure server credentials.
        If Hetzner generates a root password, it is in ``node.extra['password']``.
        """
        data = {"name": name, "server_type": size.id, "image": image.id}
        if location is not None:
            data["location"] = location.id
        if ex_ssh_keys is not None:
            data["ssh_keys"] = [
                key.extra["id"] if isinstance(key, KeyPair) else key for key in ex_ssh_keys
            ]
        for key, value in (
            ("user_data", ex_user_data),
            ("labels", ex_labels),
            ("networks", ex_networks),
            ("public_net", ex_public_net),
        ):
            if value is not None:
                data[key] = value

        result = self.connection.request("/v1/servers", data=data, method="POST").object
        node = self._to_node(result["server"])
        if result.get("root_password") is not None:
            node.extra["password"] = result["root_password"]
        return node

    def reboot_node(self, node):
        """Request a graceful reboot. Returns when the action is accepted."""
        return self._node_action(node, "reboot")

    def start_node(self, node):
        """Power on a server. Returns when the action is accepted."""
        return self._node_action(node, "poweron")

    def stop_node(self, node):
        """Request a graceful shutdown. Returns when the action is accepted."""
        return self._node_action(node, "shutdown")

    def destroy_node(self, node):
        response = self.connection.request("/v1/servers/%s" % node.id, method="DELETE")
        return response.status == httplib.OK

    def list_volumes(self):
        """List all volumes in the project, including attached volumes."""
        return [self._to_volume(data) for data in self._list_resources("volumes")]

    def create_volume(self, size, name, location=None, snapshot=None):
        """
        Create a volume of ``size`` GB in the supplied location.

        ``location`` is required. Creating volumes from snapshots is unsupported.
        The returned volume may still be in the ``CREATING`` state.

        :param size: Size in GB (10 to 10240).
        :type size: ``int``
        :param name: Volume name, unique within the project.
        :type name: ``str``
        :param location: Location to create the volume in (required).
        :type location: :class:`~libcloud.compute.base.NodeLocation`
        :param snapshot: Unsupported; must be ``None``.
        :type snapshot: ``None``
        :return: The newly created volume.
        :rtype: :class:`~libcloud.compute.base.StorageVolume`
        """
        if snapshot is not None:
            raise NotImplementedError("Hetzner Cloud does not support volume snapshots")
        if location is None:
            raise ValueError("location is required to create a Hetzner Cloud volume")
        data = {"size": size, "name": name, "location": location.name}
        result = self.connection.request("/v1/volumes", data=data, method="POST").object
        return self._to_volume(result["volume"])

    def attach_volume(self, node, volume, device=None):
        """
        Attach a volume to a server in the same location.

        Hetzner assigns the device path, available in ``volume.extra['linux_device']``.
        ``device`` must be ``None``. Returns when the action is accepted.

        :param node: Server to attach the volume to.
        :type node: :class:`~libcloud.compute.base.Node`
        :param volume: Volume to attach.
        :type volume: :class:`~libcloud.compute.base.StorageVolume`
        :param device: Unsupported; must be ``None``.
        :type device: ``None``
        :return: Whether the attachment action was accepted.
        :rtype: ``bool``
        """
        if device is not None:
            raise ValueError("Hetzner Cloud assigns the volume device path automatically")
        response = self.connection.request(
            "/v1/volumes/%s/actions/attach" % volume.id,
            data={"server": int(node.id), "automount": False},
            method="POST",
        )
        return response.status == httplib.CREATED

    def detach_volume(self, volume):
        """
        Detach a volume. Returns when the action is accepted.

        :param volume: Volume to detach.
        :type volume: :class:`~libcloud.compute.base.StorageVolume`
        :return: Whether the detachment action was accepted.
        :rtype: ``bool``
        """
        response = self.connection.request(
            "/v1/volumes/%s/actions/detach" % volume.id, method="POST"
        )
        return response.status == httplib.CREATED

    def destroy_volume(self, volume):
        """
        Delete an unattached volume without delete protection.

        :param volume: Volume to delete.
        :type volume: :class:`~libcloud.compute.base.StorageVolume`
        :return: Whether the volume was deleted.
        :rtype: ``bool``
        """
        response = self.connection.request("/v1/volumes/%s" % volume.id, method="DELETE")
        return response.status == httplib.NO_CONTENT

    def list_key_pairs(self):
        return [self._to_key_pair(data) for data in self._list_resources("ssh_keys")]

    def get_key_pair(self, name):
        for data in self._list_resources("ssh_keys", params={"name": name}):
            if data["name"] == name:
                return self._to_key_pair(data)
        raise KeyPairDoesNotExistError(name=name, driver=self)

    def import_key_pair_from_string(self, name, key_material):
        data = {"name": name, "public_key": key_material}
        result = self.connection.request("/v1/ssh_keys", data=data, method="POST").object
        return self._to_key_pair(result["ssh_key"])

    def delete_key_pair(self, key_pair):
        response = self.connection.request(
            "/v1/ssh_keys/%s" % key_pair.extra["id"], method="DELETE"
        )
        return response.status == httplib.NO_CONTENT

    def ex_get_node(self, node_id):
        """
        Retrieve a server by ID.

        :param node_id: Server ID.
        :type node_id: ``str``
        :return: The requested server.
        :rtype: :class:`~libcloud.compute.base.Node`
        """
        data = self.connection.request("/v1/servers/%s" % node_id).object
        return self._to_node(data["server"])

    def _node_action(self, node, action):
        response = self.connection.request(
            "/v1/servers/%s/actions/%s" % (node.id, action), method="POST"
        )
        return response.status == httplib.CREATED

    def _list_resources(self, resource, params=None):
        params = dict(params or {})
        params.update(page=1, per_page=50)
        result = []
        while True:
            data = self.connection.request("/v1/%s" % resource, params=params).object
            result.extend(data[resource])
            next_page = data.get("meta", {}).get("pagination", {}).get("next_page")
            if next_page is None:
                return result
            if next_page <= params["page"]:
                raise LibcloudError("Invalid pagination: next page did not advance", driver=self)
            params["page"] = next_page

    def _to_node(self, data):
        public_ips = []
        public_net = data.get("public_net") or {}
        ipv4 = public_net.get("ipv4")
        ipv6 = public_net.get("ipv6")
        if ipv4 and ipv4.get("ip"):
            public_ips.append(ipv4["ip"])
        if ipv6 and ipv6.get("ip"):
            # Hetzner assigns a /64; the default server address is its first host.
            network = ip_network(ipv6["ip"], strict=False)
            public_ips.append(str(network.network_address + 1))
        private_ips = []
        for network in data.get("private_net") or []:
            if network.get("ip"):
                private_ips.append(network["ip"])
            private_ips.extend(network.get("alias_ips") or [])

        return Node(
            id=data["id"],
            name=data["name"],
            state=self.NODE_STATE_MAP.get(data["status"], NodeState.UNKNOWN),
            public_ips=public_ips,
            private_ips=private_ips,
            driver=self,
            size=self._to_size(data["server_type"]) if data.get("server_type") else None,
            image=self._to_image(data["image"]) if data.get("image") else None,
            extra=self._extra(data, ("id", "name", "status")),
        )

    def _to_size(self, data, location=None):
        price = None
        if location is not None:
            for item in data.get("prices", []):
                if item["location"] == location.name:
                    price = float(item["price_hourly"]["net"])
                    break
        return NodeSize(
            id=data["id"],
            name=data["name"],
            ram=int(data["memory"] * 1024),
            disk=data["disk"],
            bandwidth=None,
            price=price,
            driver=self,
            extra=self._extra(data, ("id", "name")),
        )

    def _to_image(self, data):
        return NodeImage(
            id=data["id"],
            name=data.get("name") or data["description"],
            driver=self,
            extra=self._extra(data, ("id", "name")),
        )

    def _to_location(self, data):
        return NodeLocation(
            id=data["id"],
            name=data["name"],
            country=data["country"],
            driver=self,
            extra=self._extra(data, ("id", "name", "country")),
        )

    def _to_volume(self, data):
        state = {
            "creating": StorageVolumeState.CREATING,
            "available": StorageVolumeState.AVAILABLE,
        }.get(data["status"], StorageVolumeState.UNKNOWN)
        if state == StorageVolumeState.AVAILABLE and data["server"] is not None:
            state = StorageVolumeState.INUSE
        return StorageVolume(
            id=str(data["id"]),
            name=data["name"],
            size=data["size"],
            driver=self,
            state=state,
            extra=self._extra(data, ("id", "name", "size")),
        )

    def _to_key_pair(self, data):
        return KeyPair(
            name=data["name"],
            public_key=data["public_key"],
            fingerprint=data["fingerprint"],
            driver=self,
            extra=self._extra(data, ("name", "public_key", "fingerprint")),
        )

    @staticmethod
    def _extra(data, exclude):
        return {key: value for key, value in data.items() if key not in exclude}
