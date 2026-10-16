Hetzner Cloud Compute Driver
============================

The Hetzner Cloud driver manages cloud servers using the `Hetzner Cloud API`_.
It supports listing, creating, starting, stopping, rebooting, and deleting
servers, listing images, server types and locations, and importing, listing,
retrieving and deleting SSH public keys. It also supports listing, creating,
attaching, detaching, and deleting block storage volumes.

Connecting
----------

Create a project API token in the Hetzner Console under **Security → API Tokens**.
Use a read/write token for creating or changing resources. Pass the token as
the driver's ``key`` argument:

.. code-block:: python

    from libcloud.compute.types import Provider
    from libcloud.compute.providers import get_driver

    cls = get_driver(Provider.HETZNER)
    driver = cls("your-project-api-token")

Creating a server
-----------------

Import a public key or use an existing key name or ID from your project:

.. literalinclude:: /examples/compute/hetzner/hetzner_compute_simple.py
   :language: python

``ex_ssh_keys`` accepts key names, IDs, or ``KeyPair`` objects returned by the
driver. When no keys are supplied, Hetzner generates a root password, exposed
as ``node.extra['password']``. ``create_node`` accepts ``auth`` for compatibility
with ``deploy_node``, but does not use it to configure server credentials.
With ``deploy_node``, ``auth=NodeAuthPassword(...)`` supplies an existing SSH
password for deployment. It does not set that password on the server. For SSH
key authentication, use ``ex_ssh_keys`` to select the keys to inject and
``ssh_key`` to supply the corresponding private key file to ``deploy_node``.
Use SSH keys for automated deployment: Hetzner requires a generated root
password to be changed on first login.

``create_node`` also accepts ``ex_user_data`` for cloud-init configuration,
``ex_labels`` for labels, ``ex_networks`` for private network IDs, and
``ex_public_net`` for public network options. For example, use
``{'enable_ipv4': False, 'enable_ipv6': True}`` to request an IPv6-only server.

Creation and server actions are asynchronous. Use ``wait_until_running`` to
wait for a created server to run. It waits for a public IPv4 address by default.
For an IPv6-only server, allow IPv6 addresses:

.. code-block:: python

    running = driver.wait_until_running([node], force_ipv4=False)

For a private-only server, select its private addresses:

.. code-block:: python

    running = driver.wait_until_running([node], ssh_interface="private_ips")

``stop_node`` requests a graceful shutdown.

Sizes and pricing
-----------------

``list_sizes(location=location)`` restricts server types to those supported in
the chosen location and sets ``size.price`` to the net hourly price in the
project owner's currency. Temporary availability and deprecation information
are retained in ``size.extra['locations']``. Without a location, the price is
``None``. All per-location prices are retained in ``size.extra['prices']``.
Memory is converted from GB to Libcloud's MB units.

Volumes
-------

Use the standard Libcloud volume methods:

.. code-block:: python

    volumes = driver.list_volumes()
    volume = driver.create_volume(size=10, name="data", location=location)

Volume creation is asynchronous. Refresh ``list_volumes()`` until the new
volume's state is ``StorageVolumeState.AVAILABLE`` before attaching it:

.. code-block:: python

    volume.attach(node)

The server must be in the same location as the volume. Hetzner assigns the
device path, exposed in ``volume.extra['linux_device']``; specifying ``device``
is unsupported. Attachment does not automatically format or mount the volume.

``volume.detach()`` also returns when the action is accepted. Refresh
``list_volumes()`` to check attachment state: attached volumes have state
``StorageVolumeState.INUSE`` and their server ID in ``volume.extra['server']``.
After detachment finishes, the state is ``StorageVolumeState.AVAILABLE`` and
the server ID is ``None``. Unmount the filesystem before detaching, and detach
the volume before calling ``volume.destroy()``. Delete protection must be
disabled before deletion.

Volume snapshots and creation from snapshots are unsupported.

API Docs
--------

.. autoclass:: libcloud.compute.drivers.hetzner.HetznerNodeDriver
    :members:
    :undoc-members:

.. _`Hetzner Cloud API`: https://docs.hetzner.cloud/reference/cloud
