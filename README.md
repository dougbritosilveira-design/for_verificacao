# Verificacao de Balanca Dinamica (MVP)

App web em Django para executar o formulario de verificacao/ajuste, validar com assinatura e enviar o PDF para o SAP como anexo na OM.

## Fluxo implementado (MVP)
- Selecao de equipamento / local / OM
- Preenchimento do formulario tecnico
- Validacao com uma assinatura (nome + assinatura em canvas)
- Historico de formularios
- Tentativa de envio para SAP via API (stub configuravel)

## Como executar
1. Criar ambiente virtual e instalar dependencias:
   - `python -m venv .venv`
   - `.venv\Scripts\activate`
   - `pip install -r requirements.txt`
2. Aplicar migracoes:
   - `python manage.py makemigrations`
   - `python manage.py migrate`
3. Criar usuario admin (opcional):
   - `python manage.py createsuperuser`
4. Rodar servidor:
   - `python manage.py runserver`

## Deploy online (Render + GitHub)
### 1) Subir no GitHub
1. Crie um repositorio vazio no GitHub.
2. No projeto local:
   - `git init`
   - `git add .`
   - `git commit -m "Preparar deploy"`
   - `git branch -M main`
   - `git remote add origin https://github.com/SEU_USUARIO/SEU_REPO.git`
   - `git push -u origin main`

### 2) Publicar no Render
1. Acesse Render e conecte sua conta GitHub.
2. Clique em `New +` > `Blueprint`.
3. Selecione o repositorio.
4. O Render vai ler `render.yaml` e criar:
   - 1 Web Service Django
   - 1 banco PostgreSQL
5. Clique em `Apply`.
6. Ao terminar o deploy, abra a URL do servico.

### 3) Criar usuario admin online
No Render, abra `Shell` do servico e rode:
- `python manage.py createsuperuser`

### 4) Ajustar URL final no ambiente
Depois do nome real do servico no Render:
1. Abra o Web Service > `Environment`.
2. Ajuste:
   - `DJANGO_ALLOWED_HOSTS`
   - `DJANGO_CSRF_TRUSTED_ORIGINS`
3. Salve e faca `Manual Deploy`.

## Configuracao SAP (API)
Configure variaveis de ambiente antes de subir o servidor:
- `SAP_API_BASE_URL`
- `SAP_API_ATTACH_ENDPOINT`
- `SAP_API_TOKEN` (Bearer)
- `SAP_VERIFY_SSL` (`true`/`false`)

O envio ocorre apos a validacao. Se falhar, o formulario fica aprovado com status SAP `failed`.

## Proximos ajustes recomendados
- Mapear 100% dos campos da planilha Excel
- Gerar PDF com layout identico ao formulario oficial
- Autenticacao corporativa (AD/SSO) para assinatura
- Reenvio manual para SAP e trilha de auditoria detalhada
