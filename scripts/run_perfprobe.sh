#!/usr/bin/env bash
set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly VENV_DIR="${PROJECT_ROOT}/.venv"
readonly REQUIREMENTS_FILE="${PROJECT_ROOT}/requirements.txt"

fct_usage() {
    cat <<'EOF'
Usage:
  scripts/run_perfprobe.sh [--run-only] [-- <perfprobe args...>]

Options:
  --run-only   Skip dependency installation and run benchmark directly.
  -h, --help   Show this help message.

Examples:
  scripts/run_perfprobe.sh
  scripts/run_perfprobe.sh --run-only -- --no-gpu --disk-groups 16
EOF
}

fct_run_with_privilege() {
    if [[ "${EUID}" -eq 0 ]]; then
        "$@"
        return
    fi

    if command -v sudo >/dev/null 2>&1; then
        sudo "$@"
        return
    fi

    printf 'Warning: privilege elevation unavailable, skip: %s\n' "$*" >&2
    return 1
}

fct_install_system_deps() {
    if command -v apt-get >/dev/null 2>&1; then
        fct_run_with_privilege apt-get update || true
        fct_run_with_privilege apt-get install -y dmidecode ethtool fio pciutils util-linux || true
        fct_run_with_privilege apt-get install -y python3-venv || true
        return
    fi

    if command -v dnf >/dev/null 2>&1; then
        fct_run_with_privilege dnf install -y dmidecode ethtool fio pciutils util-linux || true
        return
    fi

    if command -v yum >/dev/null 2>&1; then
        fct_run_with_privilege yum install -y dmidecode ethtool fio pciutils util-linux || true
        return
    fi

    printf 'Warning: unsupported package manager, skipped system dependency install\n' >&2
}

fct_get_python_minor_version() {
    python3 -V 2>&1 | awk '{print $2}' | cut -d. -f1,2
}

fct_print_venv_recovery_hint() {
    local py_minor="$1"

    printf '\nError: failed to create Python virtual environment at %s\n' "${VENV_DIR}" >&2

    if command -v apt-get >/dev/null 2>&1; then
        printf 'Debian/Ubuntu fix (install at least one):\n' >&2
        printf '  sudo apt-get install -y python3-venv\n' >&2
        if [[ -n "${py_minor}" ]]; then
            printf '  sudo apt-get install -y python%s-venv\n' "${py_minor}" >&2
        fi
        printf 'Then rerun: scripts/run_perfprobe.sh\n\n' >&2
        return
    fi

    printf 'Install your distro Python venv package, then rerun: scripts/run_perfprobe.sh\n\n' >&2
}

fct_create_venv() {
    local venv_output=""
    local py_minor=""

    if venv_output="$(python3 -m venv "${VENV_DIR}" 2>&1)"; then
        return
    fi

    printf '%s\n' "${venv_output}" >&2

    if ! printf '%s' "${venv_output}" | grep -qi 'ensurepip is not available'; then
        fct_print_venv_recovery_hint ""
        return 1
    fi

    py_minor="$(fct_get_python_minor_version || true)"

    if command -v apt-get >/dev/null 2>&1; then
        if [[ -n "${py_minor}" ]]; then
            fct_run_with_privilege apt-get install -y "python${py_minor}-venv" || true
        fi
        fct_run_with_privilege apt-get install -y python3-venv || true

        if python3 -m venv "${VENV_DIR}"; then
            return
        fi
    fi

    fct_print_venv_recovery_hint "${py_minor}"
    return 1
}

fct_prepare_python_env() {
    if [[ ! -d "${VENV_DIR}" ]]; then
        fct_create_venv
    fi

    "${VENV_DIR}/bin/python3" -m pip install --upgrade pip wheel
    "${VENV_DIR}/bin/python3" -m pip install -r "${REQUIREMENTS_FILE}"
}

fct_main() {
    local run_only="0"
    local -a passthrough=()
    local -a cli_args=()

    while [[ "$#" -gt 0 ]]; do
        case "$1" in
            --run-only)
                run_only="1"
                shift
                ;;
            -h|--help)
                fct_usage
                exit 0
                ;;
            --)
                shift
                passthrough=("$@")
                break
                ;;
            *)
                passthrough+=("$1")
                shift
                ;;
        esac
    done

    if [[ "${run_only}" == "0" ]]; then
        fct_install_system_deps
        fct_prepare_python_env
    else
        cli_args+=("--run-only")
    fi

    if [[ -x "${VENV_DIR}/bin/python3" ]]; then
        "${VENV_DIR}/bin/python3" "${PROJECT_ROOT}/perfprobe.py" "${cli_args[@]}" "${passthrough[@]}"
        return
    fi

    python3 "${PROJECT_ROOT}/perfprobe.py" "${cli_args[@]}" "${passthrough[@]}"
}

fct_main "$@"
