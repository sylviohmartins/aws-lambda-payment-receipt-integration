#!/usr/bin/env python3
"""Generic AWS Lambda Python deployment helper driven by repository configuration.

The tool packages a Python Lambda, synchronizes supported runtime configuration
from the repository infrastructure, publishes a new Lambda version and moves an
alias to the published version.

Configuration discovery
-----------------------
* Repository/app: ``--repo-dir`` or ``--app-dir``.
* Environment: ``--env`` or inferred from ``--profile``/``AWS_PROFILE``.
* Terraform values: the selected ``terraform.tfvars`` plus variable defaults.
* AWS profile: explicit ``--profile`` -> ``AWS_PROFILE`` -> profile declared in
  tfvars -> automatic selection from ``aws configure list-profiles``.
* Function name, alias and Python runtime: command-line overrides first, then
  values found in the selected ``terraform.tfvars``.

Infrastructure synchronization
------------------------------
Before ``update-function-code --publish``, the tool reads ``infra/**/*.tf`` and
the selected tfvars and synchronizes the supported Lambda runtime settings:
``environment_variables`` and ``lambda_layer_map``. Existing environment
variables and layers not managed by those declarations are preserved.

This is intentionally not a replacement for Terraform. It only mirrors the
runtime settings required for a safe manual Lambda deployment when the normal
infrastructure pipeline is unavailable.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

DEFAULT_REGION = "sa-east-1"
DEFAULT_PYTHON = "3.12"
DEFAULT_PLATFORM = "manylinux2014_x86_64"
AWS_ZIP_LIMIT_MB = 50
AWS_ZIP_LIMIT_BYTES = AWS_ZIP_LIMIT_MB * 1024 * 1024
MAX_LAMBDA_LAYERS = 5

ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "dev": ("dev", "des", "development"),
    "hom": ("hom", "hml", "qa", "stg", "stage", "staging"),
    "prod": ("prod", "prd", "production"),
}
PROFILE_TFVARS_KEYS = (
    "aws_profile",
    "aws_cli_profile",
    "profile",
)

_VAR_EXPRESSION = re.compile(r"^\$\{var\.([A-Za-z0-9_]+)\}$")
_VAR_INTERPOLATION = re.compile(r"\$\{var\.([A-Za-z0-9_]+)\}")
_BARE_VAR_EXPRESSION = re.compile(r"^var\.([A-Za-z0-9_]+)$")
_UNRESOLVED_TERRAFORM = re.compile(
    r"(?:\$\{[^}]+\}|\b(?:local|module|data)\.[A-Za-z0-9_.-]+)"
)


def log(message: str) -> None:
    """Writes one deployment log line."""
    print(f"[deploy-lambda] {message}")


def run(
    command: list[str],
    check: bool = True,
    capture: bool = True,
    dry_run: bool = False,
) -> str:
    """Runs an external command and returns stdout when capture is enabled."""
    if dry_run:
        log("[DRY-RUN] " + " ".join(command))
        return ""

    log("$ " + " ".join(command))
    result = subprocess.run(
        command,
        capture_output=capture,
        text=True,
        check=False,
    )

    if result.returncode == 0:
        return result.stdout.strip() if capture else ""

    if capture:
        error = result.stderr.strip() or result.stdout.strip()
        if error:
            log(f"ERRO: {error}")
    else:
        log(f"Comando falhou com código {result.returncode}")

    if check:
        raise RuntimeError(
            f"Comando falhou ({result.returncode}): {' '.join(command)}"
        )
    return ""


def ensure_aws_cli() -> None:
    """Fails early with a useful message when AWS CLI is unavailable."""
    if shutil.which("aws"):
        return
    raise RuntimeError(
        "AWS CLI não encontrado no PATH. Instale/configure o AWS CLI antes do deploy."
    )


def parse_requirements(requirements_path: Path) -> list[str]:
    """Returns installable requirement specs, ignoring comments/options."""
    specs: list[str] = []
    if not requirements_path.exists():
        return specs

    for line in requirements_path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        line = re.sub(r"\s*;.*$", "", line).strip()
        if re.match(
            r"^[A-Za-z0-9_.-]+(?:\[[^\]]+\])?(?:(?:==|~=|>=|<=|!=|>|<).+)?$",
            line,
        ):
            specs.append(line)
    return specs


def parse_tfvars(tfvars_path: Path) -> dict[str, str]:
    """Parses scalar tfvars values used for lightweight auto-detection."""
    values: dict[str, str] = {}
    if not tfvars_path.exists():
        return values

    for raw in tfvars_path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip(",").strip()
        if value.startswith(("{", "[")):
            continue
        values[key.strip()] = value.strip('"').strip("'")
    return values


def canonical_environment(value: str | None) -> str | None:
    """Normalizes common environment aliases while preserving custom names."""
    if not value:
        return None
    lowered = value.strip().lower()
    for canonical, aliases in ENV_ALIASES.items():
        if lowered in aliases:
            return canonical
    return lowered


def detect_env_from_profile(profile: str | None) -> str | None:
    """Infers an environment from a local AWS profile name when possible."""
    if not profile:
        return None
    tokens = set(filter(None, re.split(r"[^a-z0-9]+", profile.lower())))
    for canonical, aliases in ENV_ALIASES.items():
        if tokens.intersection(aliases):
            return canonical
    return None


def find_tfvars(
    repo_dir: Path,
    env: str,
    explicit_tfvars: Path | None = None,
) -> Path | None:
    """Finds the most likely terraform.tfvars for the selected environment."""
    if explicit_tfvars:
        resolved = explicit_tfvars.resolve()
        if not resolved.exists():
            raise RuntimeError(f"tfvars informado não existe: {resolved}")
        return resolved

    candidates = [
        repo_dir / "infra" / "inventories" / env / "terraform.tfvars",
        repo_dir / "infra" / env / "terraform.tfvars",
        repo_dir / "infra" / f"{env}.tfvars",
        repo_dir / "infra" / "terraform.tfvars",
    ]
    for path in candidates:
        if path.exists():
            return path

    matches = [
        path
        for path in repo_dir.rglob("terraform.tfvars")
        if env.lower() in str(path).lower()
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        log(
            "Mais de um terraform.tfvars corresponde ao ambiente; "
            "use --tfvars para eliminar ambiguidade"
        )
        for path in matches:
            log(f"  - {path}")
    return None


def list_aws_profiles() -> list[str]:
    """Reads profile names configured on the current machine via AWS CLI."""
    ensure_aws_cli()
    result = subprocess.run(
        ["aws", "configure", "list-profiles"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            "Não foi possível listar os profiles locais com "
            f"'aws configure list-profiles': {error}"
        )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _profile_score(
    profile: str,
    env: str,
    repo_dir: Path,
    function_name: str | None,
) -> int:
    """Scores a local profile by environment and project affinity."""
    normalized = profile.lower()
    tokens = set(filter(None, re.split(r"[^a-z0-9]+", normalized)))
    aliases = set(ENV_ALIASES.get(env, (env,)))
    score = 0

    if normalized == env:
        score += 120
    if tokens.intersection(aliases):
        score += 80
    if any(
        normalized.endswith(f"-{alias}")
        or normalized.endswith(f"_{alias}")
        or normalized.startswith(f"{alias}-")
        or normalized.startswith(f"{alias}_")
        for alias in aliases
    ):
        score += 20

    hints = " ".join(
        value.lower()
        for value in (repo_dir.name, function_name or "")
        if value
    )
    for token in tokens - aliases:
        if len(token) >= 4 and token in hints:
            score += 25
    return score


def resolve_aws_profile(
    explicit_profile: str | None,
    env: str,
    repo_dir: Path,
    function_name: str | None,
    tfvars: dict[str, str],
) -> str:
    """Resolves a configured local AWS profile without assuming profile == env.

    Precedence:
    1. ``--profile``;
    2. ``AWS_PROFILE``;
    3. a profile name explicitly declared in tfvars;
    4. automatic match against ``aws configure list-profiles``.

    Automatic matching restores names such as ``sispag-hom``: profiles
    containing the environment token are ranked using repository/function-name
    affinity. Ambiguous matches fail safely and ask for ``--profile``.
    """
    configured_profiles = list_aws_profiles()
    if not configured_profiles:
        raise RuntimeError(
            "Nenhum AWS profile encontrado. Configure o AWS CLI/SSO antes do deploy."
        )

    requested = explicit_profile or os.environ.get("AWS_PROFILE")
    if not requested:
        requested = next(
            (tfvars[key] for key in PROFILE_TFVARS_KEYS if tfvars.get(key)),
            None,
        )

    if requested:
        if requested not in configured_profiles:
            available = ", ".join(configured_profiles)
            raise RuntimeError(
                f"AWS profile '{requested}' não está configurado nesta máquina. "
                f"Profiles disponíveis: {available}"
            )
        return requested

    ranked = sorted(
        (
            (_profile_score(profile, env, repo_dir, function_name), profile)
            for profile in configured_profiles
        ),
        reverse=True,
    )
    ranked = [(score, profile) for score, profile in ranked if score >= 80]

    if not ranked:
        available = ", ".join(configured_profiles)
        raise RuntimeError(
            f"Não foi possível associar automaticamente um AWS profile ao ambiente '{env}'. "
            f"Use --profile. Profiles disponíveis: {available}"
        )

    best_score = ranked[0][0]
    best = [profile for score, profile in ranked if score == best_score]
    if len(best) != 1:
        raise RuntimeError(
            "Mais de um AWS profile é compatível com o ambiente/projeto: "
            + ", ".join(best)
            + ". Informe --profile explicitamente."
        )

    selected = best[0]
    log(f"AWS profile autodetectado: {selected}")
    return selected


def auto_detect_settings(
    app_dir: Path | None,
    repo_dir_arg: Path | None,
    env_arg: str | None,
    profile_arg: str | None,
    function_name_arg: str | None,
    alias_arg: str | None,
    python_version_arg: str | None,
    tfvars_arg: Path | None,
) -> tuple[Path, Path, str, str, str, str, str, Path | None]:
    """Resolves all deployment settings with explicit arguments taking priority."""
    if repo_dir_arg:
        repo_dir = repo_dir_arg.resolve()
    elif app_dir:
        resolved_app = app_dir.resolve()
        repo_dir = resolved_app.parent if resolved_app.name == "app" else resolved_app
    else:
        raise RuntimeError("Informe --repo-dir ou --app-dir")

    app_dir_resolved = app_dir.resolve() if app_dir else (repo_dir / "app").resolve()
    if not app_dir_resolved.is_dir():
        raise RuntimeError(f"Diretório app/ não encontrado: {app_dir_resolved}")

    env = canonical_environment(env_arg)
    if not env:
        env = detect_env_from_profile(profile_arg or os.environ.get("AWS_PROFILE"))
    if not env and tfvars_arg:
        tfvars_preview = parse_tfvars(tfvars_arg)
        env = canonical_environment(
            tfvars_preview.get("environment")
            or tfvars_preview.get("env")
            or tfvars_preview.get("deployment_alias")
        )
    if not env:
        raise RuntimeError(
            "Não foi possível detectar o ambiente. Informe --env (ex.: dev, hom, prod)."
        )

    tfvars_path = find_tfvars(repo_dir, env, explicit_tfvars=tfvars_arg)
    tfvars: dict[str, str] = {}
    if tfvars_path:
        log(f"Lendo configurações de {tfvars_path}")
        tfvars = parse_tfvars(tfvars_path)
    else:
        log("Aviso: terraform.tfvars não encontrado; use overrides explícitos se necessário")

    function_name = function_name_arg or tfvars.get("function_name")
    if not function_name:
        raise RuntimeError(
            "Não foi possível detectar function_name. Use --function-name ou configure tfvars."
        )

    alias = alias_arg or tfvars.get("deployment_alias") or env
    python_version = python_version_arg or DEFAULT_PYTHON
    if tfvars.get("runtime") and not python_version_arg:
        match = re.match(r"python(\d+\.\d+)", tfvars["runtime"])
        if match:
            python_version = match.group(1)

    profile = resolve_aws_profile(
        profile_arg,
        env,
        repo_dir,
        function_name,
        tfvars,
    )

    return (
        repo_dir,
        app_dir_resolved,
        env,
        profile,
        function_name,
        alias,
        python_version,
        tfvars_path,
    )


def install_dependencies(
    deploy_dir: Path,
    requirements_path: Path,
    python_version: str,
    platform: str,
    architecture: str,
    dry_run: bool = False,
) -> None:
    """Installs Lambda-compatible binary dependencies into the staging dir."""
    specs = parse_requirements(requirements_path)
    if not specs:
        log("Nenhuma dependência encontrada em requirements.txt")
        return

    if architecture == "arm64":
        platform = "manylinux2014_aarch64"

    log(f"Instalando dependências: {', '.join(specs)}")
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--target",
            str(deploy_dir),
            "--platform",
            platform,
            "--implementation",
            "cp",
            "--python-version",
            python_version,
            "--only-binary=:all:",
            *specs,
        ],
        capture=False,
        dry_run=dry_run,
    )


def copy_app_files(app_dir: Path, deploy_dir: Path) -> None:
    """Copies application files while excluding local/dev artifacts."""
    excludes = {
        "__pycache__",
        ".pytest_cache",
        "tests",
        ".git",
        ".github",
        ".lupitaps",
        "Lib",
        "Scripts",
        "Include",
        "pyvenv.cfg",
    }

    for item in app_dir.iterdir():
        if item.name in excludes:
            continue
        destination = deploy_dir / item.name
        if item.is_dir():
            shutil.copytree(
                item,
                destination,
                ignore=shutil.ignore_patterns(*excludes, "*.pyc"),
            )
        elif item.is_file() and not item.name.endswith(".pyc"):
            shutil.copy2(item, destination)


def create_zip(deploy_dir: Path, zip_path: Path) -> None:
    """Creates the Lambda deployment ZIP from a prepared staging directory."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in deploy_dir.rglob("*"):
            if (
                file_path.is_file()
                and "__pycache__" not in file_path.parts
                and not file_path.name.endswith(".pyc")
            ):
                archive.write(file_path, file_path.relative_to(deploy_dir))


def wait_for_function_active(
    function_name: str,
    profile: str,
    region: str,
    timeout: int = 120,
) -> bool:
    """Waits until Lambda reports Active/Successful or timeout is reached."""
    started = time.time()
    while time.time() - started < timeout:
        output = run(
            [
                "aws",
                "lambda",
                "get-function",
                "--function-name",
                function_name,
                "--profile",
                profile,
                "--region",
                region,
                "--query",
                "Configuration.{State:State,LastUpdateStatus:LastUpdateStatus,LastUpdateStatusReason:LastUpdateStatusReason}",
                "--output",
                "json",
            ],
            check=False,
        )
        if not output:
            time.sleep(3)
            continue

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            time.sleep(3)
            continue

        state = data.get("State")
        last_status = data.get("LastUpdateStatus")
        log(f"Status: {state} / {last_status}")
        if state == "Active" and last_status == "Successful":
            return True
        if last_status == "Failed":
            reason = data.get("LastUpdateStatusReason")
            if reason:
                log(f"Atualização da Lambda falhou: {reason}")
            return False
        time.sleep(3)

    log("Timeout aguardando a função ficar Active/Successful")
    return False


def aws_sso_login(profile: str, dry_run: bool = False) -> None:
    """Starts/refreshes an AWS SSO session for the selected local profile."""
    log(f"Executando aws sso login --profile {profile}")
    run(
        ["aws", "sso", "login", "--profile", profile],
        capture=False,
        dry_run=dry_run,
    )


def upload_to_s3(
    zip_path: Path,
    bucket: str,
    key: str,
    profile: str,
    region: str,
    dry_run: bool = False,
) -> None:
    """Uploads a deployment package to S3 for large Lambda updates."""
    log(f"Fazendo upload do pacote para s3://{bucket}/{key}")
    run(
        [
            "aws",
            "s3",
            "cp",
            zip_path.as_posix(),
            f"s3://{bucket}/{key}",
            "--profile",
            profile,
            "--region",
            region,
        ],
        capture=False,
        dry_run=dry_run,
    )


class _HclSubsetParser:
    """Small fallback parser for the HCL subset required by this deploy tool."""

    def __init__(self, text: str):
        self.tokens = self._tokenize(text)
        self.pos = 0

    @staticmethod
    def _tokenize(text: str) -> list[tuple[str, str]]:
        tokens: list[tuple[str, str]] = []
        index = 0
        punctuation = "{}[]=,:()"

        while index < len(text):
            char = text[index]
            if char.isspace():
                index += 1
                continue
            if char == "#":
                while index < len(text) and text[index] != "\n":
                    index += 1
                continue
            if text.startswith("//", index):
                index += 2
                while index < len(text) and text[index] != "\n":
                    index += 1
                continue
            if text.startswith("/*", index):
                end = text.find("*/", index + 2)
                index = len(text) if end < 0 else end + 2
                continue
            if char == '"':
                index += 1
                value_chars: list[str] = []
                while index < len(text):
                    current = text[index]
                    if current == "\\" and index + 1 < len(text):
                        nxt = text[index + 1]
                        escapes = {
                            "n": "\n",
                            "r": "\r",
                            "t": "\t",
                            '"': '"',
                            "\\": "\\",
                        }
                        value_chars.append(escapes.get(nxt, nxt))
                        index += 2
                        continue
                    if current == '"':
                        index += 1
                        break
                    value_chars.append(current)
                    index += 1
                tokens.append(("STRING", "".join(value_chars)))
                continue
            if char in punctuation:
                tokens.append((char, char))
                index += 1
                continue

            start = index
            while (
                index < len(text)
                and not text[index].isspace()
                and text[index] not in punctuation
                and text[index] != "#"
                and not text.startswith("//", index)
                and not text.startswith("/*", index)
            ):
                index += 1
            value = text[start:index]
            if value:
                tokens.append(("ATOM", value))
            else:
                index += 1
        return tokens

    def _peek(self) -> tuple[str, str] | None:
        return None if self.pos >= len(self.tokens) else self.tokens[self.pos]

    def _take(self) -> tuple[str, str] | None:
        token = self._peek()
        if token is not None:
            self.pos += 1
        return token

    def _accept(self, kind: str) -> tuple[str, str] | None:
        token = self._peek()
        if token and token[0] == kind:
            self.pos += 1
            return token
        return None

    @staticmethod
    def _atom_value(value: str) -> Any:
        lowered = value.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        if lowered == "null":
            return None
        if re.fullmatch(r"-?\d+", value):
            return int(value)
        if re.fullmatch(r"-?(?:\d+\.\d*|\d*\.\d+)", value):
            return float(value)
        return value

    def _parse_value(self) -> Any:
        token = self._peek()
        if token is None:
            return None
        if token[0] == "{":
            self._take()
            return self._parse_body("}")
        if token[0] == "[":
            self._take()
            values: list[Any] = []
            while self._peek() and self._peek()[0] != "]":
                values.append(self._parse_value())
                self._accept(",")
            self._accept("]")
            return values
        if token[0] == "STRING":
            self._take()
            return token[1]
        if token[0] == "ATOM":
            self._take()
            atom = token[1]
            if self._peek() and self._peek()[0] == "(":
                parts = [atom]
                depth = 0
                while self._peek():
                    current = self._take()
                    if current is None:
                        break
                    parts.append(current[1])
                    if current[0] == "(":
                        depth += 1
                    elif current[0] == ")":
                        depth -= 1
                        if depth <= 0:
                            break
                return "".join(parts)
            return self._atom_value(atom)
        self._take()
        return token[1]

    def _parse_body(self, end_kind: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {}
        while self._peek():
            if end_kind and self._peek()[0] == end_kind:
                self._take()
                break
            token = self._peek()
            if token is None:
                break
            if token[0] not in {"ATOM", "STRING"}:
                self._take()
                continue

            name_token = self._take()
            if name_token is None:
                break
            name = name_token[1]
            if self._accept("=") or self._accept(":"):
                result[name] = self._parse_value()
                self._accept(",")
                continue

            labels: list[str] = []
            while self._peek() and self._peek()[0] == "STRING":
                label = self._take()
                if label:
                    labels.append(label[1])
            if self._accept("{"):
                body = self._parse_body("}")
                block_value: Any = body
                for label in reversed(labels):
                    block_value = {label: block_value}
                result.setdefault(name, [])
                existing = result[name]
                if not isinstance(existing, list):
                    existing = [existing]
                    result[name] = existing
                existing.append(block_value)
                continue

            while self._peek() and self._peek()[0] not in {",", "}"}:
                self._take()
            self._accept(",")
        return result

    def parse(self) -> dict[str, Any]:
        return self._parse_body()


def _load_hcl(path: Path) -> dict[str, Any]:
    """Loads HCL with python-hcl2 when present, otherwise uses the fallback."""
    try:
        hcl2 = importlib.import_module("hcl2")
    except ModuleNotFoundError:
        return _HclSubsetParser(path.read_text(encoding="utf-8")).parse()

    with path.open("r", encoding="utf-8") as file:
        data = hcl2.load(file)
    return data if isinstance(data, dict) else {}


def _find_values(node: Any, key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(node, dict):
        for current_key, value in node.items():
            if current_key == key:
                found.append(value)
            found.extend(_find_values(value, key))
    elif isinstance(node, list):
        for value in node:
            found.extend(_find_values(value, key))
    return found


def _terraform_variable_defaults(
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for document in documents:
        variable_blocks = document.get("variable", [])
        if isinstance(variable_blocks, dict):
            variable_blocks = [variable_blocks]
        for block in variable_blocks:
            if not isinstance(block, dict):
                continue
            for name, definition in block.items():
                if isinstance(definition, dict) and "default" in definition:
                    values[name] = definition["default"]
    return values


def _resolve_vars(value: Any, variables: dict[str, Any]) -> Any:
    """Resolves Terraform var.* references required by the supported settings."""
    if isinstance(value, str):
        match = _VAR_EXPRESSION.fullmatch(value) or _BARE_VAR_EXPRESSION.fullmatch(value)
        if match:
            name = match.group(1)
            if name not in variables:
                raise RuntimeError(f"Variável Terraform não encontrada: {name}")
            return _resolve_vars(variables[name], variables)

        def replace(match_obj: re.Match[str]) -> str:
            name = match_obj.group(1)
            if name not in variables:
                raise RuntimeError(f"Variável Terraform não encontrada: {name}")
            resolved = _resolve_vars(variables[name], variables)
            if isinstance(resolved, (dict, list)):
                raise RuntimeError(
                    f"Variável {name} não pode ser interpolada dentro de string"
                )
            return str(resolved)

        resolved_value = _VAR_INTERPOLATION.sub(replace, value)
        if _UNRESOLVED_TERRAFORM.search(resolved_value):
            raise RuntimeError(
                "Expressão Terraform não resolvida em configuração de runtime: "
                f"{resolved_value}. Use valor escalar/var.* ou execute a pipeline Terraform."
            )
        return resolved_value

    if isinstance(value, dict):
        return {key: _resolve_vars(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_vars(item, variables) for item in value]
    return value


def _collect_layer_arns(layer_map: Any) -> list[str]:
    arns: list[str] = []
    if isinstance(layer_map, dict):
        arn = layer_map.get("arn")
        if isinstance(arn, str) and arn.startswith("arn:"):
            arns.append(arn)
        for value in layer_map.values():
            arns.extend(_collect_layer_arns(value))
    elif isinstance(layer_map, list):
        for value in layer_map:
            arns.extend(_collect_layer_arns(value))
    return arns


def discover_infra_configuration(
    infra_dir: Path,
    tfvars_path: Path | None,
) -> tuple[dict[str, str], list[str]]:
    """Discovers environment_variables and lambda_layer_map for the deployment."""
    if not infra_dir.is_dir():
        log(f"Infra não encontrada em {infra_dir}; sincronização ignorada")
        return {}, []

    tf_documents: list[dict[str, Any]] = []
    for path in sorted(infra_dir.rglob("*.tf")):
        try:
            tf_documents.append(_load_hcl(path))
        except Exception as exc:
            raise RuntimeError(f"Falha ao interpretar HCL de {path}: {exc}") from exc

    variables = _terraform_variable_defaults(tf_documents)
    tfvars_document: dict[str, Any] = {}
    if tfvars_path and tfvars_path.exists():
        tfvars_document = _load_hcl(tfvars_path)
        variables.update(tfvars_document)

    documents = list(tf_documents)
    if tfvars_document:
        documents.append(tfvars_document)

    environment_variables: dict[str, str] = {}
    for document in documents:
        for candidate in _find_values(document, "environment_variables"):
            resolved = _resolve_vars(candidate, variables)
            if not isinstance(resolved, dict):
                continue
            for name, value in resolved.items():
                if value is None:
                    continue
                if isinstance(value, (dict, list)):
                    raise RuntimeError(
                        f"environment_variables.{name} possui valor não escalar"
                    )
                environment_variables[str(name)] = str(value)

    layer_arns: list[str] = []
    for document in documents:
        for candidate in _find_values(document, "lambda_layer_map"):
            resolved = _resolve_vars(candidate, variables)
            for arn in _collect_layer_arns(resolved):
                if arn not in layer_arns:
                    layer_arns.append(arn)
    return environment_variables, layer_arns


def _layer_identity(arn: str) -> str:
    """Returns a layer ARN identity without the version suffix."""
    marker = ":layer:"
    if marker not in arn:
        return arn
    prefix, suffix = arn.split(marker, 1)
    name = suffix.rsplit(":", 1)[0] if ":" in suffix else suffix
    return f"{prefix}{marker}{name}"


def sync_lambda_configuration_from_infra(
    infra_dir: Path,
    tfvars_path: Path | None,
    function_name: str,
    profile: str,
    region: str,
    dry_run: bool = False,
) -> None:
    """Merges supported infra runtime settings into Lambda $LATEST."""
    infra_env, infra_layers = discover_infra_configuration(infra_dir, tfvars_path)
    log(
        "Infra runtime descoberta: "
        f"{len(infra_env)} env var(s) e {len(infra_layers)} layer(s)"
    )
    if infra_env:
        log("Environment variables: " + ", ".join(sorted(infra_env)))
    for arn in infra_layers:
        log(f"Layer: {arn}")

    if not infra_env and not infra_layers:
        log("Nenhuma configuração runtime suportada encontrada em infra/")
        return
    if dry_run:
        log("[DRY-RUN] Configuração de infra não será aplicada")
        return

    current_raw = run(
        [
            "aws",
            "lambda",
            "get-function-configuration",
            "--function-name",
            function_name,
            "--profile",
            profile,
            "--region",
            region,
            "--output",
            "json",
        ]
    )
    try:
        current = json.loads(current_raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Resposta inválida de get-function-configuration") from exc

    current_env = dict(current.get("Environment", {}).get("Variables", {}) or {})
    current_env.update(infra_env)

    current_layers = [
        layer.get("Arn", "")
        for layer in current.get("Layers", []) or []
        if isinstance(layer, dict) and layer.get("Arn")
    ]
    managed_layer_ids = {_layer_identity(arn) for arn in infra_layers}
    preserved_layers = [
        arn
        for arn in current_layers
        if _layer_identity(arn) not in managed_layer_ids
    ]
    final_layers = [*preserved_layers, *infra_layers]
    if len(final_layers) > MAX_LAMBDA_LAYERS:
        raise RuntimeError(
            f"Configuração resultaria em {len(final_layers)} layers; "
            f"AWS Lambda permite no máximo {MAX_LAMBDA_LAYERS}."
        )

    update_command = [
        "aws",
        "lambda",
        "update-function-configuration",
        "--function-name",
        function_name,
        "--profile",
        profile,
        "--region",
        region,
    ]
    if infra_env:
        update_command.extend(
            [
                "--environment",
                json.dumps({"Variables": current_env}, separators=(",", ":")),
            ]
        )
    if infra_layers:
        update_command.extend(["--layers", *final_layers])

    log("Sincronizando configuração de infra/ em $LATEST antes do publish")
    run(update_command, capture=False)
    if not wait_for_function_active(function_name, profile, region):
        raise RuntimeError("Atualização da configuração da Lambda não concluiu com sucesso")

    verified_raw = run(
        [
            "aws",
            "lambda",
            "get-function-configuration",
            "--function-name",
            function_name,
            "--profile",
            profile,
            "--region",
            region,
            "--output",
            "json",
        ]
    )
    verified = json.loads(verified_raw)
    verified_env = dict(verified.get("Environment", {}).get("Variables", {}) or {})
    verified_layers = {
        layer.get("Arn")
        for layer in verified.get("Layers", []) or []
        if isinstance(layer, dict) and layer.get("Arn")
    }

    missing_env = [
        name for name, value in infra_env.items() if verified_env.get(name) != value
    ]
    missing_layers = [arn for arn in infra_layers if arn not in verified_layers]
    if missing_env or missing_layers:
        details: list[str] = []
        if missing_env:
            details.append("env=" + ",".join(missing_env))
        if missing_layers:
            details.append("layers=" + ",".join(missing_layers))
        raise RuntimeError(
            "Configuração runtime não aplicada integralmente: " + "; ".join(details)
        )
    log("Configuração runtime de infra/ sincronizada e validada")


def update_lambda(
    function_name: str,
    alias: str,
    profile: str,
    region: str,
    dry_run: bool = False,
    zip_path: Path | None = None,
    s3_bucket: str | None = None,
    s3_key: str | None = None,
) -> tuple[str, str]:
    """Updates code, publishes a version, waits and moves the alias."""
    update_command = [
        "aws",
        "lambda",
        "update-function-code",
        "--function-name",
        function_name,
        "--publish",
        "--profile",
        profile,
        "--region",
        region,
        "--output",
        "json",
    ]
    if s3_bucket and s3_key:
        update_command.extend(["--s3-bucket", s3_bucket, "--s3-key", s3_key])
    elif zip_path:
        update_command.extend(["--zip-file", f"fileb://{zip_path.as_posix()}"])
    else:
        raise RuntimeError("Informe pacote ZIP ou S3 para atualizar a Lambda")

    output = run(update_command, dry_run=dry_run)
    if dry_run:
        return "?", "dry-run"

    try:
        data = json.loads(output)
        version = data["Version"]
        function_arn = data["FunctionArn"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError("Não foi possível interpretar update-function-code") from exc

    log(f"Nova versão publicada: {version} ({function_arn})")
    if not wait_for_function_active(function_name, profile, region):
        raise RuntimeError("Lambda não ficou Active/Successful após publicação")

    run(
        [
            "aws",
            "lambda",
            "update-alias",
            "--function-name",
            function_name,
            "--name",
            alias,
            "--function-version",
            version,
            "--profile",
            profile,
            "--region",
            region,
        ],
        dry_run=dry_run,
    )
    return version, f"{function_arn}:{alias}"


def build_parser() -> argparse.ArgumentParser:
    """Builds a documented CLI suitable for interactive/manual deployment."""
    examples = """examples:
  # Auto-detect function/profile from repo + HOM tfvars
  python deploy_lambda.py --repo-dir ../../../my-lambda-repo --env hom

  # Override profile explicitly and refresh SSO session
  python deploy_lambda.py --repo-dir ../../../my-lambda-repo --env hom \\
      --profile sispag-hom --sso-login

  # Preview commands/config discovery without changing AWS
  python deploy_lambda.py --repo-dir ../../../my-lambda-repo --env hom --dry-run

  # List profiles configured on this machine
  python deploy_lambda.py --list-profiles

Notes:
  * Profile auto-detection reads `aws configure list-profiles`; it never assumes
    that the profile name equals the environment name.
  * infra sync preserves existing unmanaged env vars/layers and applies values
    declared in environment_variables/lambda_layer_map before publishing.
  * This tool does not run Terraform or create infrastructure resources.
"""
    parser = argparse.ArgumentParser(
        description=(
            "Package and deploy a Python AWS Lambda, synchronize supported runtime "
            "configuration from infra/, publish a version and move an alias."
        ),
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo-dir",
        type=Path,
        help="Repository root containing app/ and, optionally, infra/.",
    )
    parser.add_argument(
        "--app-dir",
        type=Path,
        help="Application directory. Defaults to <repo>/app when --repo-dir is used.",
    )
    parser.add_argument(
        "--infra-dir",
        type=Path,
        help="Infrastructure directory. Defaults to <repo>/infra.",
    )
    parser.add_argument(
        "--env",
        help="Environment name (for example dev, hom, prod). Used to locate tfvars/alias.",
    )
    parser.add_argument(
        "--profile",
        help=(
            "AWS CLI profile override. If omitted, uses AWS_PROFILE/tfvars or "
            "auto-detects a configured local profile such as sispag-hom."
        ),
    )
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="List AWS CLI profiles configured on this machine and exit.",
    )
    parser.add_argument("--function-name", help="Lambda function name override.")
    parser.add_argument("--alias", help="Lambda alias override; defaults to tfvars/env.")
    parser.add_argument(
        "--tfvars",
        type=Path,
        help="Explicit terraform.tfvars path; otherwise it is auto-discovered.",
    )
    parser.add_argument("--region", default=DEFAULT_REGION, help="AWS region.")
    parser.add_argument(
        "--python-version",
        help=f"Python ABI used for dependencies; default {DEFAULT_PYTHON} or tfvars runtime.",
    )
    parser.add_argument(
        "--architecture",
        default="x86_64",
        choices=["x86_64", "arm64"],
        help="Lambda architecture used for binary wheels (default: x86_64).",
    )
    parser.add_argument("--requirements", type=Path, help="requirements.txt override.")
    parser.add_argument("--zip-out", type=Path, help="Copy generated ZIP to this path.")
    parser.add_argument("--s3-bucket", help="S3 bucket for packages over direct upload size.")
    parser.add_argument("--s3-key", help="S3 key used together with --s3-bucket.")
    parser.add_argument(
        "--sso-login",
        action="store_true",
        help="Run `aws sso login` for the resolved profile before validating credentials.",
    )
    parser.add_argument(
        "--skip-infra-sync",
        "--no-infra-sync",
        dest="skip_infra_sync",
        action="store_true",
        help=(
            "Do not synchronize environment_variables/lambda_layer_map from infra/ "
            "before publishing. Useful only as an explicit escape hatch."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show deploy actions without changing AWS or creating the final package.",
    )
    parser.add_argument(
        "--keep-deploy-dir",
        action="store_true",
        help="Keep the temporary staging directory after execution for inspection.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.list_profiles:
            for profile in list_aws_profiles():
                print(profile)
            return

        if not args.repo_dir and not args.app_dir:
            parser.error("Informe --repo-dir ou --app-dir")

        (
            repo_dir,
            app_dir,
            env,
            profile,
            function_name,
            alias,
            python_version,
            tfvars_path,
        ) = auto_detect_settings(
            args.app_dir,
            args.repo_dir,
            args.env,
            args.profile,
            args.function_name,
            args.alias,
            args.python_version,
            args.tfvars,
        )

        infra_dir = args.infra_dir.resolve() if args.infra_dir else repo_dir / "infra"
        log(
            f"Repo: {repo_dir} | App: {app_dir} | Ambiente: {env} | "
            f"Profile: {profile} | Função: {function_name} | Alias: {alias}"
        )
        if tfvars_path:
            log(f"tfvars: {tfvars_path}")
        log(f"Infra: {infra_dir}")

        if bool(args.s3_bucket) != bool(args.s3_key):
            raise RuntimeError("--s3-bucket e --s3-key devem ser informados juntos")

        requirements_path = args.requirements or (app_dir / "requirements.txt")
        if args.sso_login:
            aws_sso_login(profile, dry_run=args.dry_run)

        if not args.dry_run:
            identity = run(
                [
                    "aws",
                    "sts",
                    "get-caller-identity",
                    "--profile",
                    profile,
                    "--region",
                    args.region,
                ],
                check=False,
            )
            if not identity:
                raise RuntimeError(
                    "Credenciais AWS não encontradas/expiradas. "
                    f"Execute `aws sso login --profile {profile}` ou use --sso-login."
                )
            try:
                arn = json.loads(identity).get("Arn", "<arn indisponível>")
                log(f"Autenticado na AWS: {arn}")
            except json.JSONDecodeError:
                log("Autenticado na AWS")

        temp_dir = Path(tempfile.mkdtemp(prefix="lambda-deploy-"))
        deploy_dir = temp_dir / "deploy"
        deploy_dir.mkdir()

        try:
            log(f"Copiando arquivos de {app_dir} para {deploy_dir}")
            if not args.dry_run:
                copy_app_files(app_dir, deploy_dir)

            if requirements_path.exists():
                install_dependencies(
                    deploy_dir,
                    requirements_path,
                    python_version,
                    DEFAULT_PLATFORM,
                    args.architecture,
                    dry_run=args.dry_run,
                )
            else:
                log(f"requirements.txt não encontrado em {requirements_path}")

            zip_path = temp_dir / "lambda-deploy.zip"
            log(f"Criando pacote {zip_path}")
            if not args.dry_run:
                create_zip(deploy_dir, zip_path)

            size = zip_path.stat().st_size if zip_path.exists() else 0
            log(f"Tamanho do .zip: {size} bytes ({size / 1024 / 1024:.2f} MB)")
            if size > AWS_ZIP_LIMIT_BYTES and not args.s3_bucket:
                raise RuntimeError(
                    f".zip maior que {AWS_ZIP_LIMIT_MB} MB; use --s3-bucket e --s3-key"
                )

            if args.zip_out and zip_path.exists():
                shutil.copy2(zip_path, args.zip_out)
                log(f".zip salvo em {args.zip_out}")

            if args.s3_bucket:
                upload_to_s3(
                    zip_path,
                    args.s3_bucket,
                    args.s3_key,
                    profile,
                    args.region,
                    dry_run=args.dry_run,
                )

            if not args.skip_infra_sync:
                sync_lambda_configuration_from_infra(
                    infra_dir=infra_dir,
                    tfvars_path=tfvars_path,
                    function_name=function_name,
                    profile=profile,
                    region=args.region,
                    dry_run=args.dry_run,
                )

            version, alias_arn = update_lambda(
                function_name,
                alias,
                profile,
                args.region,
                dry_run=args.dry_run,
                zip_path=zip_path if not args.s3_bucket else None,
                s3_bucket=args.s3_bucket,
                s3_key=args.s3_key,
            )

            log("=" * 60)
            if args.dry_run:
                log("DRY-RUN concluído. Nenhuma alteração foi feita.")
            else:
                log(f"Deploy concluído: {function_name}:{alias} -> versão {version}")
                log(f"Alias: {alias_arn}")
            log("=" * 60)
        finally:
            if args.keep_deploy_dir:
                log(f"Diretório temporário preservado em: {temp_dir}")
            else:
                shutil.rmtree(temp_dir, ignore_errors=True)
    except (RuntimeError, OSError, ValueError) as exc:
        log(f"ERRO: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
