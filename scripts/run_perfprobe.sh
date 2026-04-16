#!/usr/bin/env bash
set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly VENV_DIR="${PROJECT_ROOT}/.venv"
readonly REQUIREMENTS_FILE="${PROJECT_ROOT}/requirements.txt"
readonly TORCH_WHL_BASE_URL="https://download.pytorch.org/whl"
readonly TORCH_CPU_INDEX_URL="${TORCH_WHL_BASE_URL}/cpu"
readonly PYPI_INDEX_URL="https://pypi.org/simple"

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

fct_get_cuda_version_from_nvidia_smi() {
    if ! command -v nvidia-smi >/dev/null 2>&1; then
        return 1
    fi

    nvidia-smi 2>/dev/null | sed -n 's/.*CUDA Version: \([0-9]\+\.[0-9]\+\).*/\1/p' | head -n1
}

fct_get_nvidia_driver_version() {
    if ! command -v nvidia-smi >/dev/null 2>&1; then
        return 1
    fi

    nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -n1 | tr -d '[:space:]'
}

fct_version_to_int() {
    local version="$1"
    local major=""
    local minor=""

    if [[ ! "${version}" =~ ^([0-9]+)\.([0-9]+)$ ]]; then
        return 1
    fi

    major="${BASH_REMATCH[1]}"
    minor="${BASH_REMATCH[2]}"
    printf '%d\n' "$((10#${major} * 100 + 10#${minor}))"
}

fct_get_torch_cuda_channels() {
    local cuda_version="$1"
    local cuda_int=""

    if ! cuda_int="$(fct_version_to_int "${cuda_version}" 2>/dev/null)"; then
        printf '%s\n' "cu128 cu126 cu124 cu121 cu118"
        return
    fi

    if [[ "${cuda_int}" -ge 1208 ]]; then
        printf '%s\n' "cu128 cu126 cu124 cu121 cu118"
        return
    fi

    if [[ "${cuda_int}" -ge 1206 ]]; then
        printf '%s\n' "cu126 cu124 cu121 cu118"
        return
    fi

    if [[ "${cuda_int}" -ge 1204 ]]; then
        printf '%s\n' "cu124 cu121 cu118"
        return
    fi

    if [[ "${cuda_int}" -ge 1201 ]]; then
        printf '%s\n' "cu121 cu118"
        return
    fi

    if [[ "${cuda_int}" -ge 1108 ]]; then
        printf '%s\n' "cu118"
        return
    fi

    printf '%s\n' ""
}

fct_torch_cuda_ready() {
    "${VENV_DIR}/bin/python3" - <<'PY'
import sys

try:
    import torch
except Exception:
    sys.exit(1)

try:
    available = bool(torch.cuda.is_available()) and torch.cuda.device_count() > 0
except Exception:
    available = False

sys.exit(0 if available else 1)
PY
}

fct_install_torch_cpu_only() {
    printf 'Info: installing CPU-only PyTorch\n' >&2
    "${VENV_DIR}/bin/python3" -m pip install --upgrade --force-reinstall --index-url "${TORCH_CPU_INDEX_URL}" --extra-index-url "${PYPI_INDEX_URL}" "torch>=2.1"
}

fct_print_torch_cuda_failure_hint() {
    local driver_version="$1"
    local cuda_version="$2"

    printf '\nError: failed to install a CUDA-compatible PyTorch build for this host.\n' >&2
    printf 'Detected NVIDIA driver: %s\n' "${driver_version:-unknown}" >&2
    printf 'Detected max CUDA from nvidia-smi: %s\n' "${cuda_version:-unknown}" >&2
    printf 'PerfProbe will not fall back to CPU-only torch on NVIDIA hosts.\n' >&2
    printf 'Please update the driver, or run without GPU benchmark via:\n' >&2
    printf '  scripts/run_perfprobe.sh --run-only -- --no-gpu\n\n' >&2
}

fct_install_torch_for_driver() {
    local driver_version=""
    local cuda_version=""
    local channels_line=""
    local channel=""
    local -a channels=()

    if ! command -v nvidia-smi >/dev/null 2>&1; then
        printf 'Info: nvidia-smi not found, skip CUDA wheel selection\n' >&2
        fct_install_torch_cpu_only
        return
    fi

    driver_version="$(fct_get_nvidia_driver_version || true)"
    cuda_version="$(fct_get_cuda_version_from_nvidia_smi || true)"
    channels_line="$(fct_get_torch_cuda_channels "${cuda_version}")"

    if [[ -z "${driver_version}" ]]; then
        printf 'Error: failed to read NVIDIA driver version from nvidia-smi\n' >&2
        fct_print_torch_cuda_failure_hint "unknown" "${cuda_version:-unknown}"
        return 1
    fi

    if [[ -z "${channels_line}" ]]; then
        printf 'Error: detected NVIDIA driver=%s but CUDA capability is below 11.8\n' "${driver_version:-unknown}" >&2
        fct_print_torch_cuda_failure_hint "${driver_version}" "${cuda_version:-unknown}"
        return 1
    fi

    read -r -a channels <<<"${channels_line}"
    printf 'Info: detected NVIDIA driver=%s, max CUDA=%s\n' "${driver_version:-unknown}" "${cuda_version:-unknown}" >&2

    for channel in "${channels[@]}"; do
        printf 'Info: trying PyTorch wheel channel %s\n' "${channel}" >&2
        if ! "${VENV_DIR}/bin/python3" -m pip install --upgrade --force-reinstall --index-url "${TORCH_WHL_BASE_URL}/${channel}" --extra-index-url "${PYPI_INDEX_URL}" "torch>=2.1"; then
            printf 'Warning: install from %s failed, trying older CUDA channel\n' "${channel}" >&2
            continue
        fi

        if fct_torch_cuda_ready; then
            printf 'Info: selected CUDA-compatible PyTorch channel %s\n' "${channel}" >&2
            return
        fi

        printf 'Warning: torch from %s installed but CUDA init is unavailable, trying older channel\n' "${channel}" >&2
    done

    printf 'Error: no CUDA-compatible torch wheel found for this driver after trying channels: %s\n' "${channels_line}" >&2
    fct_print_torch_cuda_failure_hint "${driver_version}" "${cuda_version:-unknown}"
    return 1
}

fct_install_python_requirements_except_torch() {
    local filtered_requirements=""
    filtered_requirements="$(mktemp)"

    awk '
        BEGIN { IGNORECASE = 1 }
        /^[[:space:]]*torch([[:space:]]*[<>=!~].*)?[[:space:]]*$/ { next }
        { print }
    ' "${REQUIREMENTS_FILE}" >"${filtered_requirements}"

    "${VENV_DIR}/bin/python3" -m pip install -r "${filtered_requirements}"
    rm -f "${filtered_requirements}"
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
    fct_install_python_requirements_except_torch
    fct_install_torch_for_driver
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
