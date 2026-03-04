# Manual do Usuario

## Objetivo
Este manual explica como usar o app de Verificacao de Balanca Dinamica para criar, preencher, validar e consultar formularios.

## Acesso ao sistema
- App: `http://127.0.0.1:8000/historico/`
- Menu principal:
  - `Novo formulario`
  - `Historico`

## Fluxo de uso
1. Criar formulario
- Clique em `Novo formulario`
- Preencha:
  - Equipamento
  - Local
  - Nº OM
  - Data
  - Responsavel pela verificacao
- Clique em `Criar formulario`

2. Preencher formulario tecnico
- Informe as medicoes e campos tecnicos (ex.: T1, T2, T3, M1, M2, M3 etc.)
- Preencha observacoes, se necessario
- Clique em:
  - `Salvar` para continuar depois
  - `Salvar e validar` para ir para assinatura

3. Validar e assinar
- Informe o nome do responsavel pela validacao
- Assine no quadro de assinatura (mouse ou toque)
- Marque a confirmacao
- Clique em `Validar e enviar para SAP`

4. Ver resultado
- O sistema tentara enviar o PDF para o SAP automaticamente
- Possiveis resultados:
  - `Enviado SAP` (sucesso)
  - `Aprovado` + SAP `Falhou` (validado, mas envio falhou)

5. Consultar historico
- Clique em `Historico`
- Filtre por:
  - Status
  - TAG
  - OM
- Clique em `Ver` para detalhes
- Clique em `Editar` para ajustar dados
- Clique em `Validar` para assinatura/aprovacao

## Status do formulario
- `Rascunho`: formulario criado
- `Pendente validacao`: formulario preenchido e aguardando assinatura
- `Aprovado`: validado, mas ainda nao enviado ao SAP (ou falhou envio)
- `Enviado SAP`: processo completo

## Dicas de uso
- Verifique a OM e a TAG antes de validar
- Confira os valores calculados exibidos no resumo do formulario
- Se a assinatura nao aparecer, clique em `Limpar assinatura` e assine novamente

## Problemas comuns
- Equipamento nao aparece na lista:
  - Solicite cadastro no admin
- Falha no envio SAP:
  - O formulario continua validado; informe o time tecnico para revisar a integracao
- Campos nao salvam:
  - Verifique se ha mensagens de erro na tela
