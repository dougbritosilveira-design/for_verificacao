Coloque o arquivo do logo da Hydro nesta pasta com um dos nomes abaixo:

- `hydro_logo.png` (recomendado)
- `hydro_logo.jpg`
- `hydro_logo.jpeg`
- `hydro_logo_vertical_black.png`

No servidor (ex.: PythonAnywhere), o caminho esperado fica assim:

- `/home/<usuario>/for_verificacao/static/branding/hydro_logo.png`

Opcionalmente, use a variavel de ambiente `HYDRO_LOGO_PATH` para apontar um caminho absoluto.
Como alternativa final, use `HYDRO_LOGO_BASE64` com o conteudo base64 da imagem.
