import oci
import os
import random
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
    identity_client = oci.identity.IdentityClient(config)
    print("OCI Authentication Successful. Initializing loop sequence...")
except Exception as e:
    print(f"Authentication Failed: {e}")
    exit(1)

# Execution parameters
compartment_id = os.getenv("OCI_TENANCY_ID")
subnet_id = os.getenv("OCI_SUBNET_ID")
image_id = os.getenv("OCI_IMAGE_ID")

# Strip accidental leading/trailing quote characters. This happens when a
# secret is pasted including the surrounding quotes from a .env-style line
# (e.g. OCI_PUBLIC_SSH_KEY="ssh-ed25519 ...") instead of just the raw value.
def _strip_wrapping_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value.strip()

public_ssh_key = _strip_wrapping_quotes(os.getenv("OCI_PUBLIC_SSH_KEY", ""))

# Instance sizing (override via env vars if you want, defaults to a small
# request first since small shapes succeed far more often than 4/24)
target_ocpus = float(os.getenv("OCI_OCPUS", "1"))
target_memory_gbs = float(os.getenv("OCI_MEMORY_GBS", "6"))

# SAFETY CHECK: Verify the key actually loaded from GitHub Secrets
if not public_ssh_key or public_ssh_key.strip() == "":
    print("CRITICAL ERROR: OCI_PUBLIC_SSH_KEY is empty or missing from your secrets!")
    exit(1)

if not subnet_id or not image_id:
    print("CRITICAL ERROR: OCI_SUBNET_ID or OCI_IMAGE_ID is missing!")
    exit(1)

# SAFETY CHECK: Skip launching if an instance with this display name already
# exists and isn't terminated. This prevents duplicate instances from being
# created by a later scheduled run after an earlier run already succeeded.
INSTANCE_DISPLAY_NAME = "FX-Backend-Server"
NON_TERMINAL_STATES = {"PROVISIONING", "RUNNING", "STARTING", "STOPPING", "STOPPED", "CREATING_IMAGE"}

try:
    existing = compute_client.list_instances(
        compartment_id=compartment_id,
        display_name=INSTANCE_DISPLAY_NAME,
    ).data
    active_existing = [inst for inst in existing if inst.lifecycle_state in NON_TERMINAL_STATES]
    if active_existing:
        print(f"Instance '{INSTANCE_DISPLAY_NAME}' already exists "
              f"(state: {active_existing[0].lifecycle_state}). Skipping launch.")
        exit(0)
except oci.exceptions.ServiceError as e:
    print(f"Warning: could not check for existing instances ({e}). Proceeding with launch attempt.")

# Dynamically fetch real Availability Domain names for this tenancy/region
# instead of hardcoding a guessed prefix (tenancy-specific, e.g. "uufj:...").
try:
    ad_response = identity_client.list_availability_domains(compartment_id=compartment_id)
    ads = [ad.name for ad in ad_response.data]
    if not ads:
        raise RuntimeError("No availability domains returned")
    print(f"Discovered Availability Domains: {ads}")
except Exception as e:
    print(f"Failed to list availability domains: {e}")
    exit(1)

total_attempts = 300              # fewer, more widely-spaced attempts
base_capacity_sleep = 180         # 3 min base — 45-65s was still tripping
                                   # OCI's LaunchInstance rate limit
capacity_jitter = 30              # +/- random seconds, still desynced
                                   # from other scripts but off a larger base
rate_limit_sleep = 300            # longer wait after 429 / TooManyRequests
generic_error_sleep = 60          # wait after any other unexpected error
max_backoff = 900                 # cap exponential backoff at 15 minutes

consecutive_rate_limits = 0

for i in range(1, total_attempts + 1):
    current_ad = ads[(i - 1) % len(ads)]
    print(f"[Attempt {i}/{total_attempts}] Requesting instance in {current_ad} "
          f"({target_ocpus} OCPU / {target_memory_gbs} GB)...")

    try:
        request = oci.core.models.LaunchInstanceDetails(
            display_name="FX-Backend-Server",
            compartment_id=compartment_id,
            availability_domain=current_ad,
            shape="VM.Standard.A1.Flex",
            shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                ocpus=target_ocpus,
                memory_in_gbs=target_memory_gbs,
            ),
            source_details=oci.core.models.InstanceSourceViaImageDetails(
                source_type="image",
                image_id=image_id,
                boot_volume_size_in_gbs=100,
            ),
            create_vnic_details=oci.core.models.CreateVnicDetails(
                subnet_id=subnet_id,
                assign_public_ip=True,
                assign_private_dns_record=True,
                display_name="forexalertsvnic",
            ),
            metadata={
                "ssh_authorized_keys": str(public_ssh_key).strip()
            },
        )

        response = compute_client.launch_instance(request)
        if response.status == 200:
            print("SUCCESS! Instance creation initialized.")
            exit(0)

    except oci.exceptions.ServiceError as e:
        status = getattr(e, "status", None)
        message = str(e)

        if status == 429 or "TooManyRequests" in message or "Too many requests" in message:
            consecutive_rate_limits += 1
            # exponential backoff, capped, so repeated rate limits don't
            # keep hammering the API at the same interval
            wait_time = min(rate_limit_sleep * (2 ** (consecutive_rate_limits - 1)), max_backoff)
            print(f"-> Rate limited by OCI API. Backing off {wait_time}s...")
            time.sleep(wait_time)
            continue

        consecutive_rate_limits = 0

        if "Out of host capacity" in message or status == 500:
            wait_time = base_capacity_sleep + random.randint(-capacity_jitter, capacity_jitter)
            wait_time = max(wait_time, 10)  # never sleep less than 10s
            print(f"-> Capacity unavailable in {current_ad}. Resting {wait_time}s...")
            time.sleep(wait_time)
        elif status == 400:
            print(f"-> Bad request (check subnet/image/shape config): {e.message}")
            time.sleep(generic_error_sleep)
        elif status == 401 or status == 404:
            print(f"-> Auth/resource error, check credentials and OCIDs: {e.message}")
            exit(1)  # no point retrying, config is wrong
        else:
            print(f"-> API Error ({status}): {e.message}")
            time.sleep(generic_error_sleep)

    except Exception as e:
        # Catch anything not covered by ServiceError (network blips, etc.)
        # so one unexpected error doesn't kill the whole run early.
        consecutive_rate_limits = 0
        print(f"-> Unexpected error: {e}")
        time.sleep(generic_error_sleep)

    if i < total_attempts:
        # small base delay between attempts even on success-adjacent paths
        time.sleep(2)

print("Exhausted all attempts without a successful launch.")
exit(1)