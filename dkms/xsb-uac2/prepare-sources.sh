#!/bin/bash
# Download f_uac2.c and its private headers for the target kernel version from
# the stable kernel tree, then replace the hard-coded streaming-interface
# strings with the configfs function_name. Fails loudly (so DKMS skips the
# module and the in-tree driver keeps working) if anything doesn't match.
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
KVER="${1:?kernel version}"
BASE="${KVER%%[+-]*}"          # 6.18.60+rpt-rpi-v7 -> 6.18.60
URL="https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/plain/drivers/usb/gadget/function"
for f in f_uac2.c u_audio.h u_uac2.h uac_common.h; do
    curl -fsSL --retry 3 -o "$f" "$URL/$f?h=v$BASE"
done
sed -i \
  -e 's/strings_fn\[STR_AS_OUT_ALT0\]\.s = "Playback Inactive";/strings_fn[STR_AS_OUT_ALT0].s = uac2_opts->function_name;/' \
  -e 's/strings_fn\[STR_AS_OUT_ALT1\]\.s = "Playback Active";/strings_fn[STR_AS_OUT_ALT1].s = uac2_opts->function_name;/' \
  -e 's/strings_fn\[STR_AS_IN_ALT0\]\.s = "Capture Inactive";/strings_fn[STR_AS_IN_ALT0].s = uac2_opts->function_name;/' \
  -e 's/strings_fn\[STR_AS_IN_ALT1\]\.s = "Capture Active";/strings_fn[STR_AS_IN_ALT1].s = uac2_opts->function_name;/' \
  f_uac2.c
n=$(grep -c 'STR_AS_\(OUT\|IN\)_ALT[01]\]\.s = uac2_opts->function_name;' f_uac2.c)
[ "$n" -eq 4 ] || { echo "prepare-sources: expected 4 patched lines, got $n" >&2; exit 1; }
echo "prepare-sources: f_uac2.c v$BASE patched ($n lines)"
