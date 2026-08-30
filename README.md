# AWS Lambda Payment Receipt Integration

Patch de referência para adicionar à Lambda de update de retorno de tributos a autenticação STS e a consulta de comprovante, preservando o fluxo existente.

## Fluxo permanente

```text
ARN_SECRET
  -> Secrets Manager
  -> CLIENT_ID / CLIENT_SECRET
  -> STS client_credentials
  -> Bearer token
  -> GET /comprovantes/v3/comprovantes/{numero_autenticacao_comprovante}
  -> fluxo original continua
```

## Fallback temporário sem nova infraestrutura

Enquanto o Secrets Manager ainda não existir, o patch contém um bloco explicitamente marcado como **TEMPORÁRIO** em `src/config/credentials.py`. O caminho permanente de `AwsSecretManagerConfig.set_env_from_secret()` continua intacto.

O fallback usa `cryptography.fernet` para descriptografar ciphertexts separados por `dev`, `hml` e `prod` durante o cold start. Depois de carregar `CLIENT_ID`/`CLIENT_SECRET` em memória, warm invocations reaproveitam os valores.

**Importante:** ciphertext + chave no mesmo fonte não é proteção real. Para o deploy manual de hoje, o modo recomendado é:

1. manter somente os ciphertexts no código local;
2. informar `TEMP_CREDENTIALS_KEY` manualmente como environment variable da Lambda;
3. não commitar a chave nem as credenciais reais;
4. quando o Secret Manager estiver disponível, remover integralmente o bloco temporário e `requirements-temporary.txt`.

Isso não exige criar um recurso novo de infraestrutura.

### Gerar chave e ciphertexts localmente

Instale a dependência temporária e execute localmente, sem colar o resultado no GitHub público:

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

Preencha somente a cópia local do patch aplicada ao repositório corporativo. Não publique os valores neste repositório.

## Aplicação

O arquivo `payment-receipt-integration.patch` é o artefato consolidado. Na raiz do repositório original:

```bash
git apply --check payment-receipt-integration.patch
git apply payment-receipt-integration.patch
```

Depois, substitua os placeholders temporários localmente e empacote `cryptography` para o runtime Lambda antes do deploy manual.

## Validação

```bash
python -m compileall -q src tests
PYTHONPATH=. pytest -q --cov=src.client --cov=src.config --cov-branch --cov-report=term-missing --cov-fail-under=100
```

Resultado da versão publicada: **30 testes, 100% de statements e 100% de branches nos quatro arquivos novos de produção**.

Nenhum teste realiza chamadas reais à AWS, STS ou API de comprovantes.
