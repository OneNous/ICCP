#!/usr/bin/env bash
# Optional first-boot helper: directories + empty cloud.conf for Pi edge stack.
# Does not install packages or copy TLS secrets. Intended: sudo bash …

set -euo pipefail

ICCP_ETC="${ICCP_ETC:-/etc/iccp}"
AWS_IOT="${ICCP_ETC}/aws-iot"
SPOOL="${ICCP_SPOOL:-/var/lib/iccp/spool/commission}"

echo "Creating ${ICCP_ETC}, ${AWS_IOT}, ${SPOOL} …"
mkdir -p "${ICCP_ETC}" "${AWS_IOT}" "${SPOOL}"
chmod 0755 "${ICCP_ETC}" "${AWS_IOT}" "${SPOOL}"

if [[ ! -f "${ICCP_ETC}/cloud.conf" ]]; then
  umask 077
  printf '{}\n' >"${ICCP_ETC}/cloud.conf"
  chmod 0600 "${ICCP_ETC}/cloud.conf"
  echo "Wrote empty ${ICCP_ETC}/cloud.conf (0600)."
fi

echo "Done. Next:"
echo "  1) TLS: see deploy/aws-iot/README.md"
echo "  2) Register: set ICCP_CLOUD_API_URL, run iccp-cloud-register"
echo "  3) iccp-edge-doctor --strict"
echo "  4) BLE: touch ${ICCP_ETC}/ble_provision.enable; see deploy/iccp-ble-provision.service"
