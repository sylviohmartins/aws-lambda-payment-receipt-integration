#!/usr/bin/env python3
"""Deploy manual de AWS Lambda via AWS CLI.

Arquivo reconstruído a partir do script exibido em vídeo e mantido aqui
TEMPORARIAMENTE como referência operacional.

Fluxo original preservado:
1. detecta repo/app/env/profile/function/alias/runtime pelo terraform.tfvars;
2. instala dependências e monta o .zip;
3. opcionalmente faz upload para S3;
4. publica nova versão com update-function-code --publish;
5. aguarda a função ficar Active/Successful;
6. atualiza o alias.

Adição temporária deste repositório:
- antes do publish, descobre environment_variables e lambda_layer_map em infra/;
- resolve referências simples a var.* com o terraform.tfvars do ambiente;
- preserva env vars/layers existentes não gerenciados por infra;
- executa update-function-configuration em $LATEST;
- valida a configuração aplicada;
- só então publica a nova versão.

Os blocos adicionados estão delimitados por TEMPORARY INFRA SYNC e devem ser
removidos quando solicitado ou quando a pipeline oficial voltar a ser o único
mecanismo de deploy.
"""

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


def log(msg: str) -> None:
    print(f"[deploy-lambda] {msg}")


def run(
    cmd: list[str],
    check: bool = True,
    capture: bool = True,
    dry_run: bool = False,
) -> str:
    """Executa um comando externo e retorna stdout."""
    if dry_run:
        log("[DRY-RUN] " + " ".join(cmd))
        return ""

    log("$ " + " ".join(cmd))

    if capture:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            log(f"ERRO: {err}")
            if check:
                sys.exit(result.returncode)
            return ""
        return result.stdout.strip()

    result = subprocess.run(cmd, capture_output=False, text=True, check=False)
    if result.returncode != 0 and check:
        log(f"Comando falhou com código {result.returncode}")
        sys.exit(result.returncode)
    return ""


def parse_requirements(req_path: Path) -> list[str]:
    specs: list[str] = []
    if not req_path.exists():
        return specs

    for line in req_path.read_text(encoding="utf-8").splitlines():
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
    """Parser simples usado apenas para autodetecção de settings escalares."""
    values: dict[str, str] = {}
    if not tfvars_path.exists():
        return values

    for raw in tfvars_path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip(",").strip()
        if value.startswith(("{", "[")):
            continue
        values[key] = value.strip('"').strip("'")

    return values


def find_tfvars(
    repo_dir: Path,
    env: str,
    explicit_tfvars: Path | None = None,
) -> Path | None:
    if explicit_tfvars and explicit_tfvars.exists():
        return explicit_tfvars

    candidates = [
        repo_dir / "infra" / "inventories" / env / "terraform.tfvars",
        repo_dir / "infra" / env / "terraform.tfvars",
        repo_dir / "infra" / f"{env}.tfvars",
        repo_dir / "infra" / "terraform.tfvars",
    ]

    for path in candidates:
        if path.exists():
            return path

    for path in repo_dir.rglob("terraform.tfvars"):
        if env.lower() in str(path).lower():
            return path

    return None


def detect_env_from_profile(profile: str | None) -> str | None:
    if not profile:
        return None
    profile_lower = profile.lower()
    if "hom" in profile_lower:
        return "hom"
    if "dev" in profile_lower:
        return "dev"
    return None


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
    if repo_dir_arg:
        repo_dir = repo_dir_arg.resolve()
    elif app_dir and app_dir.name == "app":
        repo_dir = app_dir.parent.resolve()
    else:
        log("Informe --repo-dir ou --app-dir apontando para <repo>/app")
        sys.exit(1)

    app_dir_resolved = app_dir.resolve() if app_dir else (repo_dir / "app").resolve()
    if not app_dir_resolved.is_dir():
        log(f"Diretório app/ não encontrado: {app_dir_resolved}")
        sys.exit(1)

    env = env_arg or detect_env_from_profile(profile_arg)
    if not env:
        log("Informe --env (dev/hom) para localizar o terraform.tfvars correto")
        sys.exit(1)

    profile = profile_arg or os.environ.get("AWS_PROFILE") or env
    alias = alias_arg or env

    tfvars_path = find_tfvars(repo_dir, env, explicit_tfvars=tfvars_arg)
    tfvars: dict[str, str] = {}
    if tfvars_path:
        log(f"Lendo configurações de {tfvars_path}")
        tfvars = parse_tfvars(tfvars_path)
    else:
        log("Aviso: terraform.tfvars não encontrado")

    function_name = function_name_arg or tfvars.get("function_name")
    if tfvars.get("deployment_alias") and not alias_arg:
        alias = tfvars["deployment_alias"]

    if not function_name:
        log("Não foi possível detectar --function-name")
        sys.exit(1)

    python_version = python_version_arg or DEFAULT_PYTHON
    if tfvars.get("runtime") and not python_version_arg:
        match = re.match(r"python(\d+\.\d+)", tfvars["runtime"])
        if match:
            python_version = match.group(1)

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
    req_path: Path,
    python_version: str,
    platform: str,
    architecture: str,
    dry_run: bool = False,
) -> None:
    specs = parse_requirements(req_path)
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
        check=True,
        capture=False,
        dry_run=dry_run,
    )


def copy_app_files(app_dir: Path, deploy_dir: Path) -> None:
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
        dest = deploy_dir / item.name
        if item.is_dir():
            shutil.copytree(
                item,
                dest,
                ignore=shutil.ignore_patterns(*excludes, "*.pyc"),
            )
        elif item.is_file() and not item.name.endswith(".pyc"):
            shutil.copy2(item, dest)


def create_zip(deploy_dir: Path, zip_path: Path) -> None:
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
    start = time.time()

    while time.time() - start < timeout:
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
                "Configuration.{State:State,LastUpdateStatus:LastUpdateStatus}",
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

        time.sleep(3)

    log("Timeout aguardando a função ficar Active")
    return False


def aws_sso_login(profile: str, dry_run: bool = False) -> None:
    log(f"Executando aws sso login --profile {profile}")
    run(
        ["aws", "sso", "login", "--profile", profile],
        check=True,
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
        check=True,
        capture=False,
        dry_run=dry_run,
    )


# ============================================================================
# TEMPORARY INFRA SYNC — INÍCIO DO BLOCO ADICIONADO
# ============================================================================
# Este bloco NÃO pertence ao fluxo original do deploy tool.
# Foi adicionado temporariamente para que o deploy manual também aplique as
# configurações runtime já declaradas em infra/ antes de publicar a nova versão.
#
# Escopo deliberadamente limitado:
#   - environment_variables
#   - lambda_layer_map
#
# O script não hardcodeia URLs, IDs ou ARNs específicos: ele os descobre em
# infra/ e no terraform.tfvars selecionado pelo ambiente.
# ============================================================================

_VAR_EXPRESSION = re.compile(r"^\$\{var\.([A-Za-z0-9_]+)\}$")
_VAR_INTERPOLATION = re.compile(r"\$\{var\.([A-Za-z0-9_]+)\}")
_BARE_VAR_EXPRESSION = re.compile(r"^var\.([A-Za-z0-9_]+)$")


class _HclSubsetParser:
    """Fallback autocontido para o subconjunto HCL necessário ao deploy."""

    def __init__(self, text: str):
        self.tokens = self._tokenize(text)
        self.pos = 0

    @staticmethod
    def _tokenize(text: str) -> list[tuple[str, str]]:
        tokens: list[tuple[str, str]] = []
        i = 0
        punctuation = "{}[]=,:()"

        while i < len(text):
            ch = text[i]

            if ch.isspace():
                i += 1
                continue

            if ch == "#":
                while i < len(text) and text[i] != "\n":
                    i += 1
                continue

            if text.startswith("//", i):
                i += 2
                while i < len(text) and text[i] != "\n":
                    i += 1
                continue

            if text.startswith("/*", i):
                end = text.find("*/", i + 2)
                i = len(text) if end < 0 else end + 2
                continue

            if ch == '"':
                i += 1
                value_chars: list[str] = []
                while i < len(text):
                    current = text[i]
                    if current == "\\" and i + 1 < len(text):
                        nxt = text[i + 1]
                        escapes = {
                            "n": "\n",
                            "r": "\r",
                            "t": "\t",
                            '"': '"',
                            "\\": "\\",
                        }
                        value_chars.append(escapes.get(nxt, nxt))
                        i += 2
                        continue
                    if current == '"':
                        i += 1
                        break
                    value_chars.append(current)
                    i += 1
                tokens.append(("STRING", "".join(value_chars)))
                continue

            if ch in punctuation:
                tokens.append((ch, ch))
                i += 1
                continue

            start = i
            while (
                i < len(text)
                and not text[i].isspace()
                and text[i] not in punctuation
                and text[i] != "#"
                and not text.startswith("//", i)
                and not text.startswith("/*", i)
            ):
                i += 1

            value = text[start:i]
            if value:
                tokens.append(("ATOM", value))
            else:
                i += 1

        return tokens

    def _peek(self, offset: int = 0) -> tuple[str, str] | None:
        index = self.pos + offset
        return None if index >= len(self.tokens) else self.tokens[index]

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
        lower = value.lower()
        if lower == "true":
            return True
        if lower == "false":
            return False
        if lower == "null":
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

            name = self._take()[1]

            if self._accept("=") or self._accept(":"):
                result[name] = self._parse_value()
                self._accept(",")
                continue

            labels: list[str] = []
            while self._peek() and self._peek()[0] == "STRING":
                labels.append(self._take()[1])

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
    """Usa python-hcl2 se disponível e fallback autocontido caso contrário."""
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
    if isinstance(value, str):
        match = (
            _VAR_EXPRESSION.fullmatch(value)
            or _BARE_VAR_EXPRESSION.fullmatch(value)
        )
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

        return _VAR_INTERPOLATION.sub(replace, value)

    if isinstance(value, dict):
        return {
            key: _resolve_vars(item, variables)
            for key, item in value.items()
        }

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
    repo_dir: Path,
    tfvars_path: Path | None,
) -> tuple[dict[str, str], list[str]]:
    """Descobre environment_variables e lambda_layer_map do ambiente atual."""
    infra_dir = repo_dir / "infra"
    if not infra_dir.is_dir():
        log(f"Infra não encontrada em {infra_dir}; sync ignorado")
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
    marker = ":layer:"
    if marker not in arn:
        return arn
    prefix, suffix = arn.split(marker, 1)
    name = suffix.rsplit(":", 1)[0] if ":" in suffix else suffix
    return f"{prefix}{marker}{name}"


def sync_lambda_configuration_from_infra(
    repo_dir: Path,
    tfvars_path: Path | None,
    function_name: str,
    profile: str,
    region: str,
    dry_run: bool = False,
) -> None:
    """Sincroniza configuração runtime descoberta em infra/ com $LATEST."""
    infra_env, infra_layers = discover_infra_configuration(
        repo_dir,
        tfvars_path,
    )

    log(
        "Infra runtime descoberta: "
        f"{len(infra_env)} env var(s) e {len(infra_layers)} layer(s)"
    )

    if infra_env:
        # Loga somente nomes; não expõe valores potencialmente sensíveis.
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
        ],
        check=True,
        capture=True,
    )

    try:
        current = json.loads(current_raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Resposta inválida de get-function-configuration"
        ) from exc

    current_env = dict(
        current.get("Environment", {}).get("Variables", {}) or {}
    )
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

    update_cmd = [
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
        update_cmd.extend(
            [
                "--environment",
                json.dumps(
                    {"Variables": current_env},
                    separators=(",", ":"),
                ),
            ]
        )

    if infra_layers:
        update_cmd.extend(["--layers", *final_layers])

    log("Sincronizando infra/ em $LATEST antes do publish")
    run(update_cmd, check=True, capture=False)

    if not wait_for_function_active(function_name, profile, region):
        log("ERRO: atualização de configuração não concluiu com sucesso")
        sys.exit(1)

    # Validação fechada após o update.
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
        ],
        check=True,
        capture=True,
    )
    verified = json.loads(verified_raw)

    verified_env = dict(
        verified.get("Environment", {}).get("Variables", {}) or {}
    )
    verified_layers = {
        layer.get("Arn")
        for layer in verified.get("Layers", []) or []
        if isinstance(layer, dict) and layer.get("Arn")
    }

    missing_env = [
        name
        for name, value in infra_env.items()
        if verified_env.get(name) != value
    ]
    missing_layers = [
        arn
        for arn in infra_layers
        if arn not in verified_layers
    ]

    if missing_env or missing_layers:
        parts: list[str] = []
        if missing_env:
            parts.append("env=" + ",".join(missing_env))
        if missing_layers:
            parts.append("layers=" + ",".join(missing_layers))
        raise RuntimeError(
            "Configuração runtime não aplicada integralmente: "
            + "; ".join(parts)
        )

    log("Configuração runtime de infra/ sincronizada e validada")


# ============================================================================
# TEMPORARY INFRA SYNC — FIM DO BLOCO ADICIONADO
# ============================================================================


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
    """Atualiza código, publica nova versão e move o alias."""
    update_cmd = [
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
        update_cmd.extend(
            ["--s3-bucket", s3_bucket, "--s3-key", s3_key]
        )
    elif zip_path:
        update_cmd.extend(
            ["--zip-file", f"fileb://{zip_path.as_posix()}"]
        )
    else:
        log("ERRO: informe zip ou S3")
        sys.exit(1)

    output = run(update_cmd, check=True, dry_run=dry_run)

    if dry_run:
        return "?", "dry-run"

    try:
        data = json.loads(output)
        version = data["Version"]
        function_arn = data["FunctionArn"]
    except (json.JSONDecodeError, KeyError):
        log("Não foi possível parsear update-function-code")
        sys.exit(1)

    log(f"Nova versão publicada: {version} ({function_arn})")

    if not wait_for_function_active(function_name, profile, region):
        sys.exit(1)

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
        check=True,
        capture=True,
        dry_run=dry_run,
    )

    return (
        version,
        f"arn:aws:lambda:{region}:<account>:function:{function_name}:{alias}",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Deploy manual de Lambda Python via AWS CLI. "
            "Detecta settings pelo terraform.tfvars."
        )
    )

    parser.add_argument("--repo-dir", type=Path, default=None)
    parser.add_argument("--app-dir", type=Path, default=None)
    parser.add_argument("--env", choices=["dev", "hom"], default=None)
    parser.add_argument("--function-name", default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--alias", default=None)
    parser.add_argument("--tfvars", type=Path, default=None)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--python-version", default=None)
    parser.add_argument(
        "--architecture",
        default="x86_64",
        choices=["x86_64", "arm64"],
    )
    parser.add_argument("--requirements", type=Path, default=None)
    parser.add_argument("--zip-out", type=Path, default=None)
    parser.add_argument("--s3-bucket", default=None)
    parser.add_argument("--s3-key", default=None)
    parser.add_argument("--sso-login", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-deploy-dir", action="store_true")

    # TEMPORARY INFRA SYNC — opção adicionada apenas para debug/escape hatch.
    parser.add_argument(
        "--skip-infra-sync",
        action="store_true",
        help=(
            "TEMPORÁRIO: não sincroniza environment_variables/lambda_layer_map "
            "de infra/ antes do publish"
        ),
    )

    args = parser.parse_args()

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

    log(
        f"Repo: {repo_dir} | Ambiente: {env} | "
        f"Função: {function_name} | Alias: {alias}"
    )

    if bool(args.s3_bucket) != bool(args.s3_key):
        log("ERRO: --s3-bucket e --s3-key devem ser informados juntos")
        sys.exit(1)

    req_path = args.requirements or (app_dir / "requirements.txt")

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
            log(
                "Credenciais AWS não encontradas. "
                f"Execute aws sso login --profile {profile}"
            )
            sys.exit(1)
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
        copy_app_files(app_dir, deploy_dir)

        if req_path.exists():
            install_dependencies(
                deploy_dir,
                req_path,
                python_version,
                DEFAULT_PLATFORM,
                args.architecture,
                dry_run=args.dry_run,
            )
        else:
            log(f"requirements.txt não encontrado em {req_path}")

        zip_path = temp_dir / "lambda-deploy.zip"
        log(f"Criando pacote {zip_path}")
        if not args.dry_run:
            create_zip(deploy_dir, zip_path)

        size = zip_path.stat().st_size if zip_path.exists() else 0
        log(
            f"Tamanho do .zip: {size} bytes "
            f"({size / 1024 / 1024:.2f} MB)"
        )

        if size > AWS_ZIP_LIMIT_BYTES and not args.s3_bucket:
            log(
                f"ERRO: .zip maior que {AWS_ZIP_LIMIT_MB} MB. "
                "Use --s3-bucket e --s3-key"
            )
            sys.exit(1)

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

        # ====================================================================
        # TEMPORARY INFRA SYNC — CALL SITE ADICIONADO
        # A configuração precisa existir em $LATEST ANTES do --publish para que
        # a nova versão seja criada já com env vars/layers corretos.
        # ====================================================================
        if not args.skip_infra_sync:
            sync_lambda_configuration_from_infra(
                repo_dir=repo_dir,
                tfvars_path=tfvars_path,
                function_name=function_name,
                profile=profile,
                region=args.region,
                dry_run=args.dry_run,
            )
        # ======================= END TEMPORARY CALL SITE =====================

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
            log(
                f"Deploy concluído: {function_name}:{alias} "
                f"-> versão {version}"
            )
            log(f"Alias: {alias_arn}")
        log("=" * 60)

    finally:
        if args.keep_deploy_dir:
            log(f"Diretório temporário preservado em: {temp_dir}")
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
