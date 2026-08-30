# AWS Lambda Payment Receipt Integration

Implementação de referência para acrescentar autenticação STS e uma consulta HTTP autenticada a uma AWS Lambda existente, preservando o fluxo original e mantendo o diff pequeno.

## Origem

Este repositório **não é um fork do código-fonte original**. A solução foi produzida por **engenharia reversa de um projeto de referência**, a partir de evidências disponíveis do comportamento e da estrutura do código, e depois materializada como um patch independente.

Por esse motivo, o patch deve sempre ser validado com `git apply --check` no repositório real antes da aplicação.

## Fluxo

```text
credenciais
  -> Secrets Manager (caminho permanente)
  -> fallback temporário de PROD, somente enquanto o secret não existir
  -> STS client_credentials
  -> Bearer token
  -> GET /comprovantes/v3/comprovantes/{identificador}
  -> resposta JSON
  -> fluxo original continua
```

A consulta é `GET` e não possui body.

## Fallback temporário de produção

O caminho permanente continua sendo o Secrets Manager. Enquanto a infraestrutura ainda não estiver provisionada, existe um bloco **TEMPORÁRIO / PRODUÇÃO** em `src/config/credentials.py`.

Esse bloco:

- não tenta identificar ambiente;
- contém apenas placeholders para os ciphertexts de produção;
- descriptografa uma única vez no cold start quando `CLIENT_ID`/`CLIENT_SECRET` ainda não existem;
- está delimitado por comentários de início/fim para ser removido integralmente depois;
- é excluído da métrica de cobertura porque é código transitório e não faz parte da solução permanente.

Não publique credenciais, ciphertexts reais ou a chave neste repositório público.

### Gerar ciphertexts localmente

```bash
pip install -r requirements-temporary.txt
python - <<'PY'
from getpass import getpass
from cryptography.fernet import Fernet

key = Fernet.generate_key()
fernet = Fernet(key)
print("TEMP_CREDENTIALS_KEY=", key.decode())
for name in ("CLIENT_ID", "CLIENT_SECRET"):
    value = getpass(f"{name}: ").encode()
    print(f"{name}_CIPHERTEXT=", fernet.encrypt(value).decode())
PY
```

Substitua os placeholders somente na cópia local usada no deploy manual.

## Estrutura

```text
src/
├── client/
│   ├── comprovantes_api.py
│   ├── http_retry.py
│   └── sts.py
└── config/
    └── credentials.py

tests/
├── test_comprovantes_api.py
├── test_credentials.py
├── test_http_retry.py
└── test_sts.py
```

A estrutura foi mantida propositalmente pequena, sem factories, registries ou camadas adicionais desnecessárias.

## Aplicação no projeto real

Coloque estes dois arquivos na **raiz do projeto-alvo**, no mesmo nível de `lambda_function.py` e `src/`:

```text
apply_patch.sh
payment-receipt-integration.patch
```

Depois execute:

```bash
chmod +x apply_patch.sh
./apply_patch.sh
```

O script valida:

1. que está em um repositório Git;
2. que `lambda_function.py` e `src/` existem na raiz;
3. que não há alterações locais pendentes;
4. `git apply --check`;
5. aplicação do patch;
6. `git diff --check`;
7. validação de sintaxe Python em memória, sem gerar `__pycache__`.

Veja também `PATCH_APPLY_INSTRUCTIONS.md`.

## Testes e qualidade

O código permanente novo possui testes unitários com cobertura de **100% de statements e branches**. O bloco temporário de credenciais é explicitamente excluído da cobertura por ser removível e transitório.

Nenhum teste realiza chamadas reais à AWS, ao STS ou à API HTTP.
