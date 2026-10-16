import os

from libcloud.compute.types import Provider
from libcloud.compute.providers import get_driver

driver = get_driver(Provider.HETZNER)(os.environ["HETZNER_TOKEN"])
location = next(location for location in driver.list_locations() if location.name == "fsn1")
size = next(size for size in driver.list_sizes(location) if size.name == "cpx22")
image = next(
    image
    for image in driver.list_images()
    if image.name == "ubuntu-24.04" and image.extra["architecture"] == "x86"
)

node = driver.create_node(
    name="libcloud-example",
    size=size,
    image=image,
    location=location,
    ex_ssh_keys=["my-existing-ssh-key"],
)
print(driver.wait_until_running([node]))
