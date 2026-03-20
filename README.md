# 🛡️ Lavie SST - Documentação Técnica e Guia de Manutenção

Este documento é voltado para **desenvolvedores e analistas de TI** que precisarão dar manutenção, expandir ou entender o funcionamento lógico do aplicativo **Lavie SST**. 

O sistema foi construído em **Python (Streamlit)** e opera como um aplicativo web 100% server-side. Ele é fortemente integrado ao ecossistema Google Workspace (Google Sheets e Google Drive) para atuar como banco de dados NoSQL e repositório de binários de forma gratuita e escalável.

---

## 🏗️ Arquitetura do Sistema

### 1. Banco de Dados (Google Sheets)
O aplicativo não usa um banco de dados relacional (SQL) ou serviço alugado. O banco principal de transações é uma planilha do Google gerida através da biblioteca `gspread`.
- **Autenticação:** Ocorre silenciosamente server-side utilizando uma `Service Account` da Google Cloud Platform (GCP). Sem telas de "Login com Google" para o usuário.
- **Estrutura Esperada (Tabelas):** O script exige que a planilha possua, obrigatoriamente, as seguintes abas (Worksheets) criadas com seus devidos cabeçalhos na linha 1:
  `EPI`, `CESTA`, `ARMARIO`, `FARDAMENTO`, `OS`, `INTEGRACAO`, `TREINAMENTO`
- **Cache Local na RAM (Alta Performance):** A função de leitura `get_dados_planilha(nome_aba)` utiliza o decorador `@st.cache_data`. Isso evita chamadas API desnecessárias ao Google, segurando os dados na RAM do Streamlit por 5 minutos (`ttl=300`). Ao realizar um cadastro novo no app (`processar_cadastro_padrao`), o comando `st.cache_data.clear()` é acionado globalmente para forçar uma nova sincronização limpa.

### 2. Upload de Arquivos Mídia (Google Drive) e WebApp GAS
Para contornar o limite de upload rígido de APIs do Google Drive e simplificar links, o sistema utiliza o **Google Apps Script (GAS)** como *middleware* de upload.
- Quando o usuário anexa uma foto no celular: A imagem sofre *encode* para string em Base64 usando `io.BytesIO`.
- Dispara-se uma chamada HTTP Post (`httpx`) assíncrona, não-bloqueante via módulo genérico `_upload_para_drive_via_gas_async`.
- A API hospedada no *Apps Scripts* capta o Base64, reconstrói o arquivo JPG físico, joga dentro da `ID_PASTA_DRIVE` pré-estabelecida e a devolve como um link de visualização válido.

### 3. Motor FPDF de Relatórios e Assinatura Eletrônica em Tela
O coração dos relatórios técnicos de SST está amarrado nas classes nativas da biblioteca `fpdf2`. Todo layout, parágrafos, retângulos bordados de EPI, textos em Itálico das portarias ministeriais estão programados em linhas absolutas (`X`, `Y` em milímetros).
- **Tratamento de Assinaturas Mobile:** Usando o componente externo `streamlit-drawable-canvas`, o aplicativo capta os gestos gerando uma Array do NumPy. Ele só salva no banco (Sheet) convertendo o `np.array` numa gigante string Base64 transparente png invisível. 
- Quando um gestor pedir para abrir o Histórico do Empregado ou imprimir um EPI (PDF), o Python puxa o código oculto da planilha, recria um arquivo na temp da OS `write(temp_sig_xxx.png)` e o embute no FPDF.

---

## ⚙️ Variáveis Globais, Secrets.toml de Ambiente

Se você precisar refazer deploy desse aplicativo ou iniciar local num PC novo, deverá criar e posicionar a pasta e o arquivo de credenciais na raiz da aplicação:
`/.streamlit/secrets.toml`

**A estrutura rígida desse arquivo Dicionário/TOML (vital ao app.py) consiste em:**

```toml
# Variaveis Administrativas / Permissões
ADMIN_USER = "seu_usuario"
ADMIN_PASS = "sua_senha"

# URLs Base e Integradores
URL_PLANILHA = "https://docs.google.com/spreadsheets/d/SEU_ID/edit"
URL_WEBAPP_GAS = "https://script.google.com/macros/s/SEU_ID/exec"
ID_PASTA_DRIVE = "seu_id_no_drive"

# Chave privada padrão GCP para API Gspread (Mantenha Intacto)
[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "..."
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."
universe_domain = "googleapis.com"
```

---

## 🛠️ Modificação de Telas e Estilo Front-End (CSS)

Caso haja a necessidade de alterar as cores temas de Laranja (#E37026) para alguma outra marca da corporação, todo o layout visual moderno, *Dark Glassmorphism* está centralizado na injeção global CSS no topo do código:
**Procure Pela Função:** `def inject_custom_css():`
Evite utilizar marcações inline pelo Streamlit, sempre substitua usando os identificadores de *Data-Teste* como `[data-testid="stSidebar"]` ou os seletores globais como `[data-baseweb="input"]` para as caixas de texto. Mudanças devem ocorrer nesse bloco multiline e isso vai propagar no site inteiro de uma vez só.

---

## 🪲 Troubleshooting (Dicas para Correção e Bugs Comuns)

1. **"Erro ao importar Pandas / Pyarrow Module Not Found"**
   Embora desnecessário no início, evite bibliotecas pesadas de Dataframe se não for essencial, a memória e a lerdeza do `app.py` vêm ao encher o código inicial com tratamentos longos.
2. **"Assinatura do Funcionário sumiu ou o arquivo não vira PDF"**
   Modificações nos atributos de tamanho físico (Width e Height) nas funções de declaração do Canvas (Ex: `canvas_ass = st_canvas(...)`) impactam não apenas a interface Web, mass como as dimensões de resolução caem. Se você mudar nas telas, as funções no final do doc responsáveis pelo design Milimétrico da lib `fpdf` (`def injetar_assinatura_simples()`) sofrerão deslocamento do eixo (y_current). Certifique-se de manter proporcional.
3. **Limite de Execuções API do Google**
   Ao gerar consultas demais ou se 10 usuários em smartphones logarem juntos, as cotas do Free-Tier da GCP explodem. Verifique sempre se o comando `sincronizar_funcionarios_nuvem` não está ativando loop de RERUN (`st.rerun()`) infinitas vezes perdendo referências do `st.session_state`.
