# Manual do App (MVP)

## Nome
Verificacao de Balanca Dinamica (FOR 08.05.003)

## Objetivo
Executar o formulario de verificacao/ajuste de balanca dinamica via web, com:
- selecao de equipamento/local/OM
- preenchimento tecnico
- validacao com assinatura
- historico
- envio do formulario para SAP como anexo da OM (via API)

## Tecnologia
- Backend: `Django`
- Banco: `SQLite` (MVP)
- Integracao SAP: API (configuravel)

## Arquivos principais
- `manage.py`
- `verificacao_web/settings.py`
- `inspecoes/models.py`
- `inspecoes/forms.py`
- `inspecoes/views.py`
- `inspecoes/services.py`

## Instalacao e execucao
1. Abrir terminal na pasta do projeto
```powershell
cd "c:\Users\a824147\Documents\Verificacao"
```

2. Criar ambiente virtual
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

3. Instalar dependencias
```powershell
pip install -r requirements.txt
```

4. Criar banco e tabelas
```powershell
python manage.py makemigrations
python manage.py migrate
```

5. Criar usuario administrador
```powershell
python manage.py createsuperuser
```

6. Iniciar o sistema
```powershell
python manage.py runserver
```

7. Acessar
- App: `http://127.0.0.1:8000/historico/`
- Admin: `http://127.0.0.1:8000/admin/`

## Configuracao SAP (API)
Definir antes de iniciar o servidor:
```powershell
$env:SAP_API_BASE_URL="https://seu-endpoint-sap"
$env:SAP_API_ATTACH_ENDPOINT="/maintenance-orders/attachments"
$env:SAP_API_TOKEN="seu_token"
$env:SAP_VERIFY_SSL="true"
python manage.py runserver
```

## Perfis de uso
- `Administrador`: cadastra equipamentos
- `Executor`: cria e preenche formulario
- `Validador`: assina e aprova

## Fluxo de uso
1. **Cadastrar equipamento** (Admin)
- Acessar `/admin/`
- Entrar em `Equipment`
- Cadastrar:
  - `tag`
  - `description`
  - `location`
  - `active`

2. **Criar novo formulario**
- Menu `Novo formulario`
- Preencher:
  - Equipamento
  - Local
  - Nº OM
  - Data
  - Responsavel pela verificacao
- Clicar `Criar formulario`

3. **Preencher formulario tecnico**
- Informar medicoes (T1/T2/T3, M1/M2/M3 etc.)
- Adicionar observacoes
- Clicar:
  - `Salvar` (mantem no processo)
  - `Salvar e validar` (vai para assinatura)

4. **Validar e assinar**
- Informar nome do responsavel pela validacao
- Assinar no quadro (canvas)
- Marcar confirmacao
- Clicar `Validar e enviar para SAP`

5. **Resultado da validacao**
- Se SAP OK: status `Enviado SAP`
- Se SAP falhar: formulario fica `Aprovado` e SAP `Falhou`

6. **Consultar historico**
- Menu `Historico`
- Filtrar por:
  - Status
  - TAG
  - OM

## Status do formulario
- `Rascunho`
- `Pendente validacao`
- `Aprovado`
- `Enviado SAP`

## Status SAP
- `Nao iniciado`
- `Sucesso`
- `Falhou`

## Trocar senha do admin
```powershell
python manage.py changepassword SEU_USUARIO
```

## Problemas comuns
- `No module named django`
  - Instalar dependencias: `pip install -r requirements.txt`
  - Verificar se a venv esta ativa
- Falha no envio SAP
  - Conferir `SAP_API_BASE_URL`
  - Conferir `SAP_API_TOKEN`
  - Validar endpoint e payload em `inspecoes/services.py`
- Equipamento nao aparece na lista
  - Verificar se esta cadastrado no admin e com `active=True`

## Limitacoes do MVP
- Nem todos os campos da planilha original foram mapeados
- PDF gerado e simplificado (nao igual ao layout oficial)
- Sem autenticacao corporativa/SSO

## Proximas melhorias recomendadas
1. Mapear 100% do formulario Excel
2. PDF com layout oficial
3. Reenvio manual para SAP
4. Trilhas de auditoria detalhadas
5. Integracao com login corporativo
