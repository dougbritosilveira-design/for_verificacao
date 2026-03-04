# Checklist de Implantacao (Producao)

## Objetivo
Checklist para colocar o app de Verificacao de Balanca Dinamica em ambiente de producao com seguranca e integracao SAP.

## 1. Infraestrutura
- [ ] Definir servidor de aplicacao (Windows Server/Linux)
- [ ] Definir versao do Python suportada
- [ ] Definir pasta de deploy do projeto
- [ ] Garantir acesso de rede ao endpoint da API SAP
- [ ] Garantir DNS/hostname da aplicacao (ex.: `verificacao.empresa.local`)
- [ ] Garantir certificado SSL/TLS (HTTPS)

## 2. Banco de dados
- [ ] Validar se producao usara `PostgreSQL` (recomendado) ou outro banco corporativo
- [ ] Criar banco, usuario e senha dedicados
- [ ] Configurar backup automatico do banco
- [ ] Definir politica de retencao de backup
- [ ] Testar restauracao de backup

## 3. Codigo e ambiente Python
- [ ] Publicar codigo em pasta/servidor de producao
- [ ] Criar ambiente virtual (`venv`)
- [ ] Instalar dependencias (`pip install -r requirements.txt`)
- [ ] Configurar arquivo/variaveis de ambiente de producao
- [ ] Ajustar `DJANGO_DEBUG=false`
- [ ] Configurar `DJANGO_SECRET_KEY` forte e exclusiva
- [ ] Configurar `ALLOWED_HOSTS` corretamente

## 4. Configuracao Django
- [ ] Rodar migracoes (`python manage.py migrate`)
- [ ] Criar usuario admin inicial
- [ ] Configurar arquivos estaticos (se aplicavel)
- [ ] Configurar pasta de midia/logs (se utilizada)
- [ ] Validar timezone/locale corretos

## 5. Integracao SAP (API)
- [ ] Confirmar endpoint oficial de anexo de OM
- [ ] Confirmar metodo HTTP e contrato (payload)
- [ ] Confirmar autenticacao (Bearer token / OAuth / outro)
- [ ] Configurar variaveis:
  - [ ] `SAP_API_BASE_URL`
  - [ ] `SAP_API_ATTACH_ENDPOINT`
  - [ ] `SAP_API_TOKEN`
  - [ ] `SAP_VERIFY_SSL`
- [ ] Validar certificado SSL da API SAP
- [ ] Testar envio de anexo em ambiente de homologacao SAP
- [ ] Confirmar retorno do `attachment_id` (ou campo equivalente)
- [ ] Definir procedimento de reenvio em caso de falha

## 6. Seguranca e acesso
- [ ] Definir perfis de usuario (admin, executor, validador)
- [ ] Revisar politica de senha
- [ ] Restringir acesso ao `/admin/` (VPN/rede interna/ACL)
- [ ] Habilitar HTTPS obrigatorio
- [ ] Definir logs de auditoria (quem validou, quando, OM, TAG)
- [ ] Validar armazenamento da assinatura (dados e retencao)

## 7. Servico de execucao (producao)
- [ ] Definir como o app sera executado:
  - [ ] IIS + wfastcgi (Windows) OU
  - [ ] Gunicorn/Uvicorn + Nginx (Linux)
- [ ] Configurar servico para iniciar automaticamente
- [ ] Configurar restart automatico em falha
- [ ] Configurar timeout e limites adequados
- [ ] Validar logs de aplicacao/servico

## 8. Testes antes de liberar
- [ ] Cadastrar equipamento de teste
- [ ] Criar formulario com OM de teste
- [ ] Preencher formulario tecnico
- [ ] Validar com assinatura
- [ ] Confirmar geracao de PDF
- [ ] Confirmar envio para SAP
- [ ] Confirmar anexo visivel na OM no SAP
- [ ] Testar fluxo de falha SAP (token invalido / endpoint indisponivel)
- [ ] Confirmar comportamento no historico (`Aprovado` + SAP `Falhou`)
- [ ] Testar filtros de historico (TAG/OM/Status)

## 9. Operacao e suporte
- [ ] Definir responsavel tecnico pelo app
- [ ] Definir responsavel funcional (area de manutencao/PCP)
- [ ] Definir canal para incidentes (Teams, e-mail, chamado)
- [ ] Criar rotina de monitoramento de falhas SAP
- [ ] Criar rotina de revisao de logs
- [ ] Planejar atualizacoes e janela de manutencao

## 10. Pos-implantacao (primeira semana)
- [ ] Acompanhar uso real com usuarios-chave
- [ ] Coletar feedback de usabilidade
- [ ] Corrigir campos/validacoes faltantes da planilha
- [ ] Ajustar layout do PDF para padrao oficial
- [ ] Registrar backlog de melhorias

## Comandos uteis (referencia)
```powershell
# Ambiente virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Dependencias
pip install -r requirements.txt

# Banco
python manage.py makemigrations
python manage.py migrate

# Admin
python manage.py createsuperuser
python manage.py changepassword SEU_USUARIO

# Execucao
python manage.py runserver
```

## Observacoes
- O MVP atual usa `SQLite`; para producao, prefira `PostgreSQL`.
- O PDF atual e simplificado; validar aderencia com auditoria/qualidade antes da liberacao final.
- A integracao SAP deve ser homologada com o contrato real da API antes do go-live.
