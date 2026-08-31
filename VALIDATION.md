# Validação

## Escopo

A implementação foi construída por **engenharia reversa de um projeto de referência**, sem acesso ao repositório-fonte original completo. O objetivo é reproduzir somente o mecanismo necessário para autenticação STS, consulta HTTP e integração mínima com o fluxo existente.

## Comportamentos cobertos pelos testes

- Secrets Manager continua sendo o caminho principal de credenciais;
- fallback temporário permanece restrito a PROD e removível em bloco;
- STS usa `client_credentials` com HTTP Basic Auth;
- `TOKEN_URL`, `API_BASE_URL`, `X_APIGW_API_ID` e `X_ITAU_FLOW_ID` aceitam configuração por variável de ambiente;
- consulta de comprovante usa `GET` sem body;
- `CLIENT_ID` é reutilizado nos headers `x-itau-apikey` e `x-itau-apikey-internal`;
- `x-itau-correlationID` é um UUID novo por chamada;
- a consulta recebe `numero_comprovante` e retorna somente `data.identificacao.numero_autenticacao_comprovante`;
- resposta vazia, JSON inválido e ausência do campo esperado falham explicitamente;
- falhas HTTP transitórias usam retry com backoff e jitter;
- erros 4xx não transitórios não são repetidos;
- logs são interpolados antes do envio ao logger e incluem detalhes da exceção sem expor credenciais.

## Comandos de validação

```bash
python -m compileall -q .
PYTHONPATH=. pytest -q
```

Os testes usam mocks e não devem realizar chamadas reais à AWS, ao STS ou à API HTTP.

## Segurança

O repositório público contém somente placeholders para URLs/IDs de PROD e para o fallback temporário de credenciais. Não publique:

- `CLIENT_SECRET`;
- token STS;
- Authorization header;
- API keys reais;
- ciphertexts reais;
- chave Fernet temporária.

## Limitação de aplicação

Como o código-fonte original completo não fez parte da análise, qualquer integração com `lambda_function.py` do projeto real deve ser revisada no contexto da versão corporativa antes do deploy.
