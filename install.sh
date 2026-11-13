#!/usr/bin/env bash
# Lokale ontwikkelinstallatie; geen productie- of Proxmox-installer.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
install_dependencies=1
if [[ $# -gt 0 ]]; then
    if [[ $# -eq 1 && "$1" == '--zonder-afhankelijkheden' ]]; then
        install_dependencies=0
    else
        printf 'Gebruik: ./install.sh [--zonder-afhankelijkheden]\n' >&2
        exit 2
    fi
fi
printf '%s\n' 'LicentieKluis: lokale ontwikkelinstallatie (niet voor productie).'
if [[ "$install_dependencies" == 1 ]]; then
    command -v python3 >/dev/null || { printf '%s\n' 'Python 3 ontbreekt.' >&2; exit 1; }
    command -v npm >/dev/null || { printf '%s\n' 'npm ontbreekt.' >&2; exit 1; }
    if [[ ! -x "$project_dir/.venv/bin/python" ]]; then
        python3 -m venv "$project_dir/.venv"
    fi
    "$project_dir/.venv/bin/python" -m pip install -r "$project_dir/requirements.txt"
    npm ci --prefix "$project_dir/frontend"
elif [[ ! -x "$project_dir/.venv/bin/python" ]]; then
    printf '%s\n' 'De virtuele omgeving ontbreekt. Voer eerst ./install.sh zonder opties uit.' >&2
    exit 1
fi
cd "$project_dir/backend"
"$project_dir/.venv/bin/python" cli.py init
"$project_dir/.venv/bin/python" cli.py bootstrap-admin
printf '%s\n' 'Klaar. Volg docs/eerste-beheerder.md voor de encryptiesleutel, het starten en TOTP.'
