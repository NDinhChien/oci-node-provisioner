#!/usr/bin/env bash
#
# Generates an SSH key pair (if one doesn't already exist) and prints/saves
# the public key formatted as OCI_PUBLIC_SSH_KEY="..." for your .env file.
#
# Usage:
#   ./generate-ssh-key-for-env.sh [key_name]
#
# Example:
#   ./generate-ssh-key-for-env.sh oci_singapore
#
# If key_name is omitted, defaults to "oci_a1_server".
# Keys are created in ~/.ssh/

set -euo pipefail

KEY_NAME="${1:-oci_a1_server}"
KEY_DIR="$HOME/.ssh"
PRIVATE_KEY_PATH="$KEY_DIR/$KEY_NAME"
PUBLIC_KEY_PATH="$KEY_DIR/${KEY_NAME}.pub"

mkdir -p "$KEY_DIR"
chmod 700 "$KEY_DIR"

if [[ -f "$PRIVATE_KEY_PATH" ]]; then
    echo "Key already exists at: $PRIVATE_KEY_PATH"
    echo "Reusing existing key pair instead of generating a new one."
else
    echo "Generating new ed25519 key pair at: $PRIVATE_KEY_PATH"
    ssh-keygen -t ed25519 -f "$PRIVATE_KEY_PATH" -C "oci-a1-server" -N ""
fi

chmod 600 "$PRIVATE_KEY_PATH"
chmod 644 "$PUBLIC_KEY_PATH"

PUB_CONTENT="$(cat "$PUBLIC_KEY_PATH")"
ENV_LINE="OCI_PUBLIC_SSH_KEY=\"${PUB_CONTENT}\""

echo ""
echo "--- Paste this line into your .env file ---"
echo ""
echo "$ENV_LINE"
echo ""
echo "--- End ---"
echo ""

OUT_FILE="./ssh_key_for_env.txt"
echo "$ENV_LINE" > "$OUT_FILE"
echo "Also saved to: $OUT_FILE"
echo ""
echo "Private key location (keep this safe, never share/commit it): $PRIVATE_KEY_PATH"
echo "Use it to SSH into your instance once it's running:"
echo "  ssh -i $PRIVATE_KEY_PATH ubuntu@<instance_public_ip>"
