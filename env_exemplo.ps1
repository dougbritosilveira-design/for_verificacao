# Exemplo de variaveis de ambiente para execucao local/homologacao
# Preencha os valores e execute este script antes de rodar o Django.

# Django
$env:DJANGO_DEBUG = "true"
$env:DJANGO_SECRET_KEY = "trocar-por-uma-chave-forte"

# Se quiser restringir hosts em producao, ajuste em verificacao_web/settings.py
# Exemplo futuro: $env:DJANGO_ALLOWED_HOSTS = "verificacao.empresa.local,127.0.0.1"

# SAP API (anexo na OM)
$env:SAP_API_BASE_URL = "https://seu-endpoint-sap"
$env:SAP_API_ATTACH_ENDPOINT = "/maintenance-orders/attachments"
$env:SAP_API_TOKEN = "seu_token_bearer"
$env:SAP_VERIFY_SSL = "true"
# Opcional: caminho absoluto do logo para cabecalho do PDF
# Exemplo: C:\Users\a824147\Downloads\hydro-logo-vertical\Hydro logo vertical\hydro_logo_vertical_black.png
# Exemplo PythonAnywhere: /home/douglasa90/for_verificacao/static/branding/hydro_logo.png
$env:HYDRO_LOGO_PATH = ""
# Opcional: logo em base64 (data URI ou base64 puro) para usar se nao houver arquivo local
$env:HYDRO_LOGO_BASE64 = ""

# Prazo dos equipamentos
$env:EQUIPMENT_DUE_SOON_DAYS = "7"   # Quantos dias antes considerar "proximo do vencimento"

# E-mail / SMTP (para notificacoes de prazo)
$env:EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
$env:EMAIL_HOST = "smtp.seudominio.com"
$env:EMAIL_PORT = "587"
$env:EMAIL_HOST_USER = "usuario_smtp"
$env:EMAIL_HOST_PASSWORD = "senha_smtp"
$env:EMAIL_USE_TLS = "true"
$env:EMAIL_USE_SSL = "false"
$env:DEFAULT_FROM_EMAIL = "nao-responda@seudominio.com"

Write-Host "Variaveis carregadas no PowerShell atual." -ForegroundColor Green
Write-Host "Agora rode: python manage.py runserver" -ForegroundColor Cyan
