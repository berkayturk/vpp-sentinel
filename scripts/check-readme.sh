#!/usr/bin/env bash
# Checks the README before it reaches the reader:
#   - no raw device identifiers, tokens or sanitiser placeholders;
#   - no unresolved <<KPI:...>> placeholders (numbers must come from docs/kpis.md);
#   - every `make <target>` it mentions in backticks exists;
#   - none of a small set of phrases that would describe the planted defect's cause. The
#     phrases are kept only as SHA-256 digests of lowercase 2-, 3- and 4-word windows, so this
#     script does not spell them out either.
set -euo pipefail
README="$(cd "$(dirname "${1:-README.md}")" && pwd)/$(basename "${1:-README.md}")"
cd "$(dirname "$0")/.."
fail=0

if grep -nE '[a-z-]+-(charger|battery|inverter)-[0-9]{3}\b' "$README"; then
  echo "README: raw device identifier"; fail=1
fi
if grep -nE 'ghp_|github_pat_|sk-ant-|<token>|<ip>|-#[0-9a-f]{6}\b' "$README"; then
  echo "README: token or sanitiser placeholder"; fail=1
fi
if grep -nE '<<KPI:[^>]*>>' "$README"; then
  echo "README: unresolved KPI placeholder"; fail=1
fi

targets=$(make -s help | awk '{print $1}')
mentioned=$(grep -oE '`make [a-z][a-z-]*' "$README" | awk '{print $2}' | sort -u || true)
for t in $mentioned; do
  if ! grep -qx "$t" <<<"$targets"; then
    echo "README: mentions 'make $t' but no such target"; fail=1
  fi
done

if ! python3 - "$README" <<'PY'
import hashlib, re, sys
text = open(sys.argv[1]).read().lower()
words = re.findall(r"[a-z0-9]+", text)
windows = {
    hashlib.sha256(" ".join(words[i:i + n]).encode()).hexdigest()
    for n in (2, 3, 4) for i in range(len(words) - n + 1)
}
DIGESTS = {
    "08a36f57fc788fd29b3a70b80881ff8b79fdcdbb365b65e45fe1c44863444f0d",
    "0eaf57ce4ec45d4c23eb164a42dabd40a8ff7f4101337a1e6c29e86d7bdcc865",
    "18f4490ff3a91b34534ffb14ff5006467ccfd92799ac26788cb505ab6925a0aa",
    "2b2178674961688f04de61c2c70053cecf5fe7611606b59e10188ea3e90d6245",
    "35fe5ac48559368c78dd1031c251bb80f1237d070790519882ec7ae96969a337",
    "50cc3aca473e36902dc0ae3d6cdcce8f2969f6dfe143952f6195e0a87dedfd62",
    "795728e655b8b36a3f2e3b61f6f326bf652c99b9daf80692a7d28b53250c0356",
    "829c38302a3161ab4b6ca99226e0565041e036720deeb7e9c56a088e876f56e6",
    "8bcf8bee88f03dc532f839e4040e6166b3b3344f7417e96c3da89666ac4f4d4a",
    "8e55b2a4e03c2712eaba29e4327dd377efbaa71e388af022a24568f86e01f5df",
    "8f86be34575b0c8a31544ec014cd60f6f1ab82e8b818606504404e112db3ae06",
    "a50db4772ec7c330a7f44de81b92561ce4ec0e09cc16a8d9e382fb3d406543d8",
    "c23cf7441e7b92bb982d0d0b6616b8fc9d04bd00ef8fa1b0561d49101a619319",
    "c603fb185a39c3b1f155ee54a4c887d2fbdb9aa88669660c5fa080eed5582194",
    "cb68218795cfc5ee302914c6fb8c9281689c974388d6f593bac2c8eb808ab4bd",
    "d8c39dafd1b850ac3427bb9982d00e9b9f2af9cef311bb4f85e1f73b13d63311",
    "ddc3f78eb91cedfa7ac04e85798bc699991ec82108c76e37f0706863e3357ee8",
    "f5bba894cff613d65590029490df361cfdeb38faf0e43a58c6ff2596edf54086",
    "fc3f16933d87df5c5c646754d3bb30be66e1ae1136518be7f0366f931227033a",
    "fd974c120c35ec4c5e25e81848c4740651166524e4c16e9eee16dd76fb40b138",
}
hits = len(DIGESTS & windows)
if hits:
    print(f"README: describes the planted defect ({hits} phrase(s))")
    sys.exit(1)
PY
then fail=1; fi

if [ "$fail" -ne 0 ]; then exit 1; fi
echo "README checks passed"
