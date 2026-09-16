import oci
import os
import time
from dotenv import load_dotenv

load_dotenv()

# Setup configuration from environment variables
# The private key sometimes arrives with literal "\n" characters instead of
# real newlines (e.g. when pasted from a wrapped/escaped .env value). This
# converts those back into actual line breaks so the SDK can parse the PEM.
# If the key already has real newlines (e.g. pasted raw into GitHub Secrets),
# this replace is a harmless no-op.
raw_private_key = os.getenv("OCI_PRIVATE_KEY", "").strip()
if len(raw_private_key) >= 2 and raw_private_key[0] == raw_private_key[-1] and raw_private_key[0] in ("'", '"'):
    raw_private_key = raw_private_key[1:-1]
private_key_content = raw_private_key.replace("\\n", "\n")

config = {
    "user": os.getenv("OCI_USER_ID"),
    "key_content": private_key_content,
    "fingerprint": os.getenv("OCI_FINGERPRINT"),
    "tenancy": os.getenv("OCI_TENANCY_ID"),
    "region": os.getenv("OCI_REGION"),
}

try:
    compute_client = oci.core.ComputeClient(config)
    print("OCI Authentication Successful. Initializing resize sequence...")
except Exception as e:
    print(f"Authentication Failed: {e}")
    exit(1)

compartment_id = os.getenv("OCI_TENANCY_ID")

# Instance sizing
# Unlike bot.py (which cycles through a list of candidate memory sizes while
# hunting for capacity), a resize targets one specific shape. The target
# OCPU count and memory size are read from their own dedicated env vars,
# separate from bot.py's OCI_OCPUS / OCI_MEMORY_GBS, so the spawn list and
# the resize target can be configured independently via GitHub Secrets.
#
# OCI_RESIZE_OCPUS: an integer, e.g. "2"
# OCI_RESIZE_MEMORY_GBS: an integer, e.g. "12"
def _parse_positive_int(raw: str, default: int, var_name: str) -> int:
    raw = raw.strip()
    if not raw:
        return default
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError
        return value
    except ValueError:
        print(f"WARNING: invalid {var_name} value '{raw}', falling back to default {default}")
        return default


OCPUS = _parse_positive_int(os.getenv("OCI_RESIZE_OCPUS", ""), default=1, var_name="OCI_RESIZE_OCPUS")
TARGET_MEMORY_GBS = _parse_positive_int(os.getenv("OCI_RESIZE_MEMORY_GBS", ""), default=6, var_name="OCI_RESIZE_MEMORY_GBS")

INSTANCE_DISPLAY_NAME = "FX-Backend-Server"

# Find the instance to resize
try:
    instances = compute_client.list_instances(
        compartment_id=compartment_id,
        display_name=INSTANCE_DISPLAY_NAME,
    ).data
    active = [inst for inst in instances if inst.lifecycle_state in ("RUNNING", "STOPPED")]
    if not active:
        print(f"CRITICAL ERROR: no RUNNING or STOPPED instance named '{INSTANCE_DISPLAY_NAME}' found.")
        exit(1)
    instance = active[0]
    instance_id = instance.id
    print(f"Found instance {instance_id} (state: {instance.lifecycle_state}).")
except oci.exceptions.ServiceError as e:
    print(f"Failed to list instances: {e}")
    exit(1)

print(f"Resizing to {OCPUS} OCPU / {TARGET_MEMORY_GBS} GB...")

try:
    update_details = oci.core.models.UpdateInstanceDetails(
        shape_config=oci.core.models.UpdateInstanceShapeConfigDetails(
            ocpus=OCPUS,
            memory_in_gbs=TARGET_MEMORY_GBS,
        )
    )
    response = compute_client.update_instance(instance_id, update_details)
    if response.status == 200:
        print("SUCCESS! Resize request accepted.")
    else:
        print(f"Unexpected response status: {response.status}")
        exit(1)
except oci.exceptions.ServiceError as e:
    status = getattr(e, "status", None)
    if status == 400:
        print(f"-> Bad request (check OCPU/memory values are valid for this shape): {e.message}")
    elif status in (401, 404):
        print(f"-> Auth/resource error, check credentials and instance ID: {e.message}")
    elif status == 409:
        print(f"-> Conflict: instance may be in a state that doesn't allow resizing right now: {e.message}")
    else:
        print(f"-> API Error ({status}): {e.message}")
    exit(1)

# Poll until the update completes
print("Waiting for instance to reach RUNNING/UPDATING to settle...")
for _ in range(30):
    time.sleep(10)
    inst = compute_client.get_instance(instance_id).data
    print(f"  state: {inst.lifecycle_state}")
    if inst.lifecycle_state in ("RUNNING", "STOPPED"):
        print(f"Resize complete. Final state: {inst.lifecycle_state}")
        exit(0)

print("Timed out waiting for resize to settle. Check the OCI console for current status.")
exit(1)