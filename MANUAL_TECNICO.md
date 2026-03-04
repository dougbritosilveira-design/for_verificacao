# Manual Tecnico

## Visao geral
Aplicacao web em Django para execucao do formulario FOR 08.05.003 com fluxo de aprovacao (1 assinatura) e envio de PDF para SAP via API.

## Stack
- Python 3.x
- Django 5.x
- SQLite (MVP)
- Requests (integracao SAP)
- ReportLab (geracao de PDF)

## Estrutura principal do projeto
- `manage.py`
- `requirements.txt`
- `verificacao_web/settings.py`
- `verificacao_web/urls.py`
- `inspecoes/models.py`
- `inspecoes/forms.py`
- `inspecoes/views.py`
- `inspecoes/services.py`
- `templates/inspecoes/*.html`

## Instalacao (ambiente local)
```powershell
cd "c:\Users\a824147\Documents\Verificacao"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## Configuracoes (settings)
Arquivo: `verificacao_web/settings.py`

Configuracoes relevantes:
- `DATABASES` (SQLite no MVP)
- `LANGUAGE_CODE = 'pt-br'`
- `TIME_ZONE = 'America/Belem'`
- `SAP_API_BASE_URL`
- `SAP_API_ATTACH_ENDPOINT`
- `SAP_API_TOKEN`
- `SAP_VERIFY_SSL`

## Variaveis de ambiente SAP
Definir antes de subir o servidor:
```powershell
$env:SAP_API_BASE_URL="https://seu-endpoint-sap"
$env:SAP_API_ATTACH_ENDPOINT="/maintenance-orders/attachments"
$env:SAP_API_TOKEN="seu_token_bearer"
$env:SAP_VERIFY_SSL="true"
python manage.py runserver
```

## Fluxo tecnico da aplicacao
1. `selection_view` (`inspecoes/views.py`)
- Cria `FormSubmission` com dados iniciais (equipamento/local/OM)
- Status inicial: `draft`

2. `form_edit_view` (`inspecoes/views.py`)
- Salva campos tecnicos do formulario
- Ao salvar, evolui para `pending_validation` (quando sai de rascunho)

3. `form_validate_view` (`inspecoes/views.py`)
- Coleta nome do validador + assinatura (base64 PNG)
- Marca `validated_at`
- Seta status `approved`
- Chama `process_sap_submission()`

4. `process_sap_submission` (`inspecoes/services.py`)
- Gera PDF (`generate_submission_pdf_bytes`)
- Envia para SAP (`upload_pdf_to_sap`)
- Atualiza `sap_status`, `sap_attachment_id`, `sap_response_message`
- Em caso de sucesso, status final `sent_to_sap`

## Modelos (dados)
Arquivo: `inspecoes/models.py`

### `Equipment`
- `tag`
- `description`
- `location`
- `active`

### `FormSubmission`
- Dados iniciais: equipamento, local, OM, data, executor
- Campos tecnicos (MVP): T1/T2/T3, M1/M2/M3, pulsos, tara, ABW, etc.
- Validacao: nome do validador, assinatura, data/hora
- SAP: status, id do anexo, mensagem de retorno
- Campos calculados (property): `tm`, `md`, `il_before`, `il_after`, `loading_q`

## Integracao SAP (API)
Arquivo: `inspecoes/services.py`

### Funcao principal
- `upload_pdf_to_sap(submission, pdf_bytes)`

### Comportamento atual (MVP)
- Faz `POST` multipart com:
  - arquivo PDF (`file`)
  - campos de formulario (`maintenance_order`, `equipment_tag`, `form_code`, etc.)
- Autenticacao por Bearer token
- Considera sucesso HTTP `2xx`
- Tenta ler `attachment_id` ou `id` da resposta JSON

### Ajustes necessarios para producao
- Confirmar endpoint real da API SAP
- Confirmar nome exato dos campos esperados
- Confirmar formato de autenticacao (Bearer, OAuth, Basic, etc.)
- Tratar erros de timeout/rede com retry (opcional)
- Persistir logs de integracao com mais detalhes

## PDF
Arquivo: `inspecoes/services.py`
- Funcao: `generate_submission_pdf_bytes()`
- Gera PDF simplificado com `reportlab`
- Recomendacao: implementar layout oficial do formulario para auditoria

## Admin Django
- URL: `/admin/`
- Entidades expostas:
  - `Equipment`
  - `FormSubmission`

Uso principal do admin:
- cadastro de equipamentos
- consulta de formularios e status de integracao SAP

## Manutencao e evolucao recomendada
1. Mapear 100% dos campos da planilha Excel
2. Validacoes de negocio (faixas, obrigatoriedades por etapa)
3. Reenvio manual para SAP (acao na tela de detalhe)
4. Trilhas de auditoria (quem alterou, quando, o que mudou)
5. Autenticacao corporativa (AD/SSO)
6. Banco PostgreSQL para producao
7. Processamento assinc. para envio SAP (fila) se volume crescer

## Comandos uteis
Trocar senha do admin:
```powershell
python manage.py changepassword SEU_USUARIO
```

Criar novo usuario (shell Django, opcional):
```powershell
python manage.py shell
```
```python
from django.contrib.auth import get_user_model
User = get_user_model()
User.objects.create_superuser('admin2', 'email@empresa.com', 'SenhaForte123')
```

## Troubleshooting
### Erro: `No module named django`
- Ative a venv
- Rode `pip install -r requirements.txt`

### Falha no envio SAP
- Verifique variaveis de ambiente
- Valide token e endpoint
- Inspecione `sap_response_message` no historico/detalhe

### Assinatura nao valida
- O campo hidden precisa receber `data:image/png;base64,...`
- Verifique JavaScript da tela `templates/inspecoes/validation.html`
