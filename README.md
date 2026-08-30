# AWS Lambda Payment Receipt Integration

Implementação mínima para acrescentar à Lambda de **update de retorno de tributos** a consulta de comprovante usando o mesmo padrão de autenticação recuperado da Lambda de cancelamentos.

> Este repositório é um **patch de referência**, não o repositório-fonte completo da Lambda original. O código original foi reconstruído a partir de vídeo, portanto o arquivo `lambda_function.patch` mostra apenas a inserção necessária no handler existente.

## Fluxo

```text
ARN_SECRET
  -> Secrets Manager
  -> CLIENT_ID / CLIENT_SECRET
  -> STS (client_credentials + Basic Auth)
  -> access_token
  -> GET {BOLETOS_API_BASE_URL}/comprovantes/v3/comprovantes/{numero_autenticacao_comprovante}
  -> response JSON
  -> fluxo original continua
```

A consulta é **GET sem body**.

## Estrutura escolhida

A estrutura foi simplificada para evitar hierarquia de um único arquivo:

```text
src/
├── client/
│   ├── comprovantes_api.py
│   ├── http_retry.py
│   └── sts.py
└── config/
    └── credentials.py
```

`config/secretManager/credentials.py` foi reduzido para `config/credentials.py`. O STS e sua exception ficaram juntos em `client/sts.py`, eliminando um pacote `exception/` que existiria apenas para uma classe.

## Resiliência e logs

- Secrets Manager: timeout + retry `standard` do botocore, máximo de 3 tentativas.
- STS: até 3 tentativas para timeout/conexão e HTTP `429/500/502/503/504`, com exponential backoff + jitter.
- GET de comprovante: mesma política de retry transitório.
- HTTP 4xx não transitório não é repetido.
- Logs informam início/sucesso/falha e tentativas sem registrar token, client secret ou API keys.
- Todos os requests possuem timeout de 10 segundos, alinhado ao padrão reconstruído da Lambda de cancelamentos.

## Headers

A chamada usa:

- `Authorization: Bearer <token>`
- `x-apigw-api-id`
- `x-itau-apikey-internal`
- `x-itau-apikey`
- `x-itau-flowID`
- `x-itau-correlationID`
- `Accept: application/json`
- `Content-Type: application/json`

Valores não fornecidos permanecem placeholders/configuração por environment variable. O client bloqueia a chamada se os três headers de API ainda estiverem com `<FAKE_...>`, evitando uso acidental em ambiente real.

## Variáveis esperadas

```text
ARN_SECRET
TOKEN_URL
BOLETOS_API_BASE_URL
X_APIGW_API_ID
X_ITAU_APIKEY_INTERNAL
X_ITAU_APIKEY
X_ITAU_FLOW_ID
X_ITAU_CORRELATION_ID
```

`CLIENT_ID` e `CLIENT_SECRET` são carregados do Secrets Manager no bootstrap, seguindo o padrão da Lambda de cancelamentos.

Os valores de `flowID` e `correlationID` também permanecem como placeholders explícitos e devem ser configurados no ambiente antes do uso.

## Ponto de inserção

No vídeo da Lambda de tributos já havia comentário imediatamente antes de `execute_with_retries(Dynamodb().update_item, ...)` mencionando `numero_autenticacao_comprovante`. O patch utiliza exatamente esse ponto.

## Validação local

```bash
python -m compileall -q .
PYTHONPATH=. pytest -q
```

Nenhum teste realiza chamadas reais à AWS, STS ou API de comprovantes.
