# AWS Lambda Payment Receipt Integration

Implementação de referência para acrescentar autenticação STS e consulta HTTP autenticada de comprovante a uma AWS Lambda existente, preservando o fluxo original e mantendo a alteração pequena.

## Origem

Este repositório **não é um fork do código-fonte original**. A solução foi produzida por **engenharia reversa de um projeto de referência** e materializada como integração independente.

## Fluxo

```text
credenciais
  -> Secrets Manager (caminho permanente)
  -> fallback temporário de PROD enquanto o secret não existir
  -> STS client_credentials
  -> Bearer token
  -> GET /comprovantes/v3/comprovantes/{numero_comprovante}
  -> data.identificacao.numero_autenticacao_comprovante
  -> fluxo original continua
```

A consulta é `GET` sem body e retorna para o chamador somente `numero_autenticacao_comprovante`.

## Configuração por ambiente

Valores que variam por ambiente seguem o padrão simples **variável de ambiente -> fallback de PROD**:

- `TOKEN_URL`
- `API_BASE_URL`
- `X_APIGW_API_ID`
- `X_ITAU_FLOW_ID`

No repositório público os fallbacks permanecem como placeholders. Na cópia de deploy, eles podem ser preenchidos com os valores de PROD até a infraestrutura passar a fornecer as variáveis.

`CLIENT_ID` e `CLIENT_SECRET` seguem o fluxo de credenciais. O `CLIENT_ID` também é reutilizado em `x-itau-apikey` e `x-itau-apikey-internal`. `x-itau-correlationID` é gerado por `uuid.uuid4()` a cada chamada.

## Observabilidade

Os logs das integrações usam mensagens já interpoladas, evitando placeholders literais como `%s` e `%.2f`. Em falhas são registrados, quando disponíveis:

- operação;
- tentativa/retry;
- HTTP status;
- tipo da exceção;
- mensagem da exceção;
- stack trace no erro definitivo.

Token, `CLIENT_SECRET`, Authorization e API keys não são logados.

## Retry

STS e consulta de comprovante repetem somente falhas transitórias:

- timeout/conexão;
- HTTP `429`, `500`, `502`, `503` e `504`.

A política usa exponential backoff com jitter e máximo padrão de 3 tentativas. Erros 4xx não transitórios não são repetidos.

## Fallback temporário de produção

O caminho permanente continua sendo o Secrets Manager. Enquanto a infraestrutura ainda não estiver provisionada, existe um bloco **TEMPORÁRIO / PRODUÇÃO** em `src/config/credentials.py`.

Esse bloco:

- não seleciona DEV/HML/PROD;
- contém apenas placeholders para os ciphertexts de produção e chave local temporária;
- descriptografa no cold start quando `CLIENT_ID`/`CLIENT_SECRET` ainda não existem;
- está delimitado para remoção integral posterior;
- não deve conter valores reais no repositório público.

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

Substitua os placeholders somente na cópia local usada no deploy manual e não versione os valores reais.

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

Nenhum teste deve realizar chamadas reais à AWS, ao STS ou à API HTTP.
