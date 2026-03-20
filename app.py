import streamlit as st
import os
import base64
import json
import io
import asyncio
import httpx
import gspread
from google.oauth2.service_account import Credentials
from fpdf import FPDF
import numpy as np
from PIL import Image
from datetime import datetime
from streamlit_option_menu import option_menu
from streamlit_drawable_canvas import st_canvas

st.set_page_config(
    page_title="Lavie SST",
    page_icon="assets/icon.png",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ─────────────────────────────────────────────
# 1. CONFIGURAÇÕES E SEGURANÇA (Via secrets.toml)
# ─────────────────────────────────────────────
URL_PLANILHA = st.secrets["URL_PLANILHA"]
URL_WEBAPP_GAS = st.secrets["URL_WEBAPP_GAS"]
ID_PASTA_DRIVE = st.secrets["ID_PASTA_DRIVE"]

ADMIN_USER = st.secrets["ADMIN_USER"]
ADMIN_PASS = st.secrets["ADMIN_PASS"]

ESCOPOS_GOOGLE = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

def get_credentials():
    """Busca credenciais de forma segura (Apenas Secrets)"""
    return Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=ESCOPOS_GOOGLE)

def conectar_planilha(nome_aba):
    cliente = gspread.authorize(get_credentials())
    return cliente.open_by_url(URL_PLANILHA).worksheet(nome_aba)

# ─────────────────────────────────────────────
# 4. CACHING DE ALTA PERFORMANCE
# ─────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def get_dados_planilha(nome_aba):
    """Puxa dados do Google Sheets e guarda na memória (cache) por 5 min."""
    planilha = conectar_planilha(nome_aba)
    return planilha.get_all_values()

# ─────────────────────────────────────────────
# 3. MANIPULAÇÃO EM MEMÓRIA RAM E ASYNC/HTTPX
# ─────────────────────────────────────────────
async def _upload_para_drive_via_gas_async(file_bytes, nome_arquivo):
    """Função assíncrona interna para enviar arquivo para o Drive."""
    try:
        file_b64 = base64.b64encode(file_bytes).decode("utf-8")
        payload = { "folderId": ID_PASTA_DRIVE, "fileName": nome_arquivo, "mimeType": "image/jpeg", "fileData": file_b64 }
        
        async with httpx.AsyncClient() as client:
            resposta = await client.post(URL_WEBAPP_GAS, content=json.dumps(payload), follow_redirects=True, timeout=60.0)
            
        if "Erro" not in resposta.text: return resposta.text 
        else: return "Erro de Upload"
    except Exception as e: return "Erro de Conexão"

def upload_para_drive_via_gas(file_bytes, nome_arquivo):
    """Ponte síncrona do Streamlit para executar a função async."""
    return asyncio.run(_upload_para_drive_via_gas_async(file_bytes, nome_arquivo))

def canvas_to_base64(canvas_result):
    """Converte o canvas diretamente em Base64 na memória RAM."""
    if canvas_result is not None and canvas_result.image_data is not None:
        img_data = canvas_result.image_data
        if np.sum(img_data) > 0:
            img = Image.fromarray(img_data.astype('uint8'), 'RGBA')
            buffered = io.BytesIO()
            img.save(buffered, format="PNG")
            return base64.b64encode(buffered.getvalue()).decode("utf-8")
    return ""

def base64_to_temp_img(b64_string, prefix="sig"):
    """Cria um arquivo temporário físico apenas porque a biblioteca FPDF exige caminho local."""
    if not b64_string or len(b64_string) < 100: return None
    try:
        img_data = base64.b64decode(b64_string)
        caminho = f"temp_{prefix}_{datetime.now().strftime('%H%M%S%f')}.png"
        with open(caminho, "wb") as f: f.write(img_data)
        return caminho
    except: return None

async def _baixar_foto_drive_async(url):
    """Função assíncrona interna para baixar foto do drive."""
    if not url or "http" not in url: return None
    try:
        file_id = url.split("/d/")[1].split("/")[0] if "/d/" in url else url.split("id=")[1].split("&")[0]
        
        async with httpx.AsyncClient() as client:
            res = await client.get(f"https://drive.google.com/uc?export=download&id={file_id}", follow_redirects=True, timeout=60.0)
            
        if res.status_code == 200:
            caminho = f"temp_drive_foto_{file_id}.jpg"
            with open(caminho, "wb") as f: f.write(res.content)
            return caminho
    except: pass
    return None

def baixar_foto_drive(url):
    """Ponte síncrona do Streamlit para executar a função async."""
    return asyncio.run(_baixar_foto_drive_async(url))

def sincronizar_funcionarios_nuvem():
    st.session_state['db_funcionarios'] = {}
    st.cache_data.clear() # Limpa o cache velho para puxar tudo novo
    abas = ["EPI", "CESTA", "ARMARIO", "FARDAMENTO", "OS", "INTEGRACAO", "TREINAMENTO"]
    for aba in abas:
        try:
            registros = get_dados_planilha(aba)
            for linha in registros[1:]:
                obra, nome, funcao = "-", "-", "-"
                if aba in ["EPI", "CESTA", "ARMARIO", "FARDAMENTO", "INTEGRACAO"] and len(linha) >= 4:
                    obra, nome, funcao = linha[1].strip(), linha[2].strip(), linha[3].strip()
                elif aba == "OS" and len(linha) >= 5:
                    obra, nome, funcao = linha[2].strip(), linha[3].strip(), linha[4].strip()
                elif aba == "TREINAMENTO" and len(linha) >= 8:
                    obra, nome, funcao = "-", linha[6].strip(), linha[7].strip()
                
                if nome and nome != "-" and nome not in st.session_state['db_funcionarios']:
                    st.session_state['db_funcionarios'][nome] = {'status': 'Ativo', 'funcao': funcao, 'obra': obra}
        except Exception: continue

# ─────────────────────────────────────────────
# 2. SEPARAÇÃO E DRY: MOTORES DE GERAÇÃO DE PDF
# ─────────────────────────────────────────────
def desenhar_cabecalho_pdf(pdf, titulo):
    pdf.add_page()
    try: pdf.image("assets/logo.png", x=85, y=10, w=40); pdf.ln(35)
    except: pdf.ln(15)
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, titulo, ln=True, align='C')
    pdf.ln(10)

def injetar_dados_colaborador_pdf(pdf, obra, nome, funcao, data):
    """Função DRY: Centraliza a injeção dos dados padrões no PDF"""
    pdf.set_font("Arial", '', 11)
    pdf.cell(0, 8, "EMPRESA: Lavie Construcoes e Empreendimentos", ln=True)
    pdf.cell(0, 8, f"OBRA: {obra}", ln=True)
    pdf.cell(0, 8, f"NOME DO COLABORADOR: {nome}", ln=True)
    pdf.cell(0, 8, f"FUNCAO: {funcao}", ln=True)
    pdf.cell(0, 8, f"DATA DE EMISSAO: {data}", ln=True)
    pdf.ln(10)

def injetar_assinatura_simples(pdf, caminho_img, titulo):
    pdf.ln(10)
    y_current = pdf.get_y()
    if caminho_img and os.path.exists(caminho_img):
        pdf.image(caminho_img, x=85, y=y_current - 5, w=40, h=15)
    else:
        pdf.set_xy(85, y_current + 5)
        pdf.set_font("Arial", 'I', 10)
        pdf.cell(40, 10, "(Assinado Fisicamente)", align='C')
        pdf.set_xy(10, y_current)
        
    pdf.ln(15)
    pdf.set_font("Arial", '', 10)
    pdf.cell(0, 8, "______________________________________________________", ln=True, align='C')
    pdf.cell(0, 8, titulo, ln=True, align='C')

def injetar_assinatura_dupla(pdf, caminho_img1, titulo1, caminho_img2, titulo2):
    pdf.ln(10)
    y_current = pdf.get_y()
    if caminho_img1 and os.path.exists(caminho_img1): pdf.image(caminho_img1, x=35, y=y_current - 5, w=40, h=15)
    if caminho_img2 and os.path.exists(caminho_img2): pdf.image(caminho_img2, x=135, y=y_current - 5, w=40, h=15)
    pdf.ln(15)
    pdf.set_font("Arial", '', 10)
    pdf.cell(95, 8, "___________________________", ln=False, align='C')
    pdf.cell(95, 8, "___________________________", ln=True, align='C')
    pdf.cell(95, 8, titulo1, ln=False, align='C')
    pdf.cell(95, 8, titulo2, ln=True, align='C')

def criar_pdf_epi(obra, nome, funcao, data, epi, qtd, ca, img_assinatura, img_foto=None):
    pdf = FPDF()
    desenhar_cabecalho_pdf(pdf, "TERMO DE ENTREGA DE EQUIPAMENTO DE PROTECAO INDIVIDUAL")
    injetar_dados_colaborador_pdf(pdf, obra, nome, funcao, data)
    
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(90, 8, "Equipamento (EPI)", border=1)
    pdf.cell(30, 8, "Quantidade", border=1, align='C')
    pdf.cell(50, 8, "N. do CA", border=1, align='C')
    pdf.ln()
    pdf.set_font("Arial", '', 10)
    pdf.cell(90, 8, str(epi), border=1)
    pdf.cell(30, 8, str(qtd), border=1, align='C')
    pdf.cell(50, 8, str(ca), border=1, align='C')
    pdf.ln(15)
    
    termo = "DECLARO PARA TODOS OS EFEITOS LEGAIS QUE RECEBI OS EPI's DISCRIMINADOS NESTA FICHA, FICANDO RESPONSAVEL PELO USO, GUARDA E CONSERVACAO DOS MESMOS, DEVENDO INDENIZA-LA A EMPRESA, CASO OCORRAM DANOS POR COMPROVADA NEGLIGENCIA OU EXTRAVIO."
    pdf.set_font("Arial", 'I', 9)
    pdf.multi_cell(0, 5, termo)
    injetar_assinatura_simples(pdf, img_assinatura, "Assinatura do Colaborador")
    
    if img_foto and os.path.exists(img_foto):
        pdf.add_page()
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(0, 10, "ANEXO FOTOGRAFICO", ln=True, align='C')
        pdf.ln(10)
        try: pdf.image(img_foto, x=20, w=170)
        except:
            pdf.set_font("Arial", '', 10)
            pdf.cell(0, 10, "(Erro ao processar imagem baixada da nuvem.)", ln=True, align='C')
            
    return pdf.output()

def criar_pdf_cesta(obra, nome, funcao, data, img_assinatura):
    pdf = FPDF()
    desenhar_cabecalho_pdf(pdf, "TERMO DE ENTREGA DE CESTA BASICA")
    injetar_dados_colaborador_pdf(pdf, obra, nome, funcao, data)
    
    termo = "Declaro que recebi 01 cesta basica completa de acordo com a recomendacao feita pela convencao coletiva de trabalho vigente. A cesta basica que recebi continha os seguintes produtos: 03 quilos de arroz, 03 quilos de feijao, 03 pacotes de flocao, 02 quilos de acucar, 02 quilos de farinha de mandioca, 02 pacotes de macarrao, 02 latas de oleo 900ml, 02 pacotes de cafe 250g, 02 pacotes de leite integral, 01 sardinha enlatada. Tambem declaro que recebi o cafe da manha de acordo com a recomendacao feita pela convencao coletiva de trabalho vigente, confirmando que recebo 02 paes, manteiga, ovos/queijo e cafe diariamente."
    pdf.set_font("Arial", 'I', 10)
    pdf.multi_cell(0, 6, termo)
    injetar_assinatura_simples(pdf, img_assinatura, "Assinatura do Colaborador")
    return pdf.output()

def criar_pdf_armario(obra, nome, funcao, data, img_assinatura):
    pdf = FPDF()
    desenhar_cabecalho_pdf(pdf, "TERMO DE ENTREGA DE ARMARIO E CADEADO")
    injetar_dados_colaborador_pdf(pdf, obra, nome, funcao, data)
    
    termo = "DECLARO PARA TODOS OS EFEITOS LEGAIS QUE RECEBI ARMARIO E CADEADO PARA CONSERVACAO DE ITENS E EQUIPAMENTOS PESSOAIS, FICANDO RESPONSAVEL PELO USO, GUARDA E CONSERVACAO DOS MESMOS, DEVENDO INDENIZA-LA A EMPRESA, CASO OCORRAM DANOS POR COMPROVADA NEGLIGENCIA OU MAL USO."
    pdf.set_font("Arial", 'I', 10)
    pdf.multi_cell(0, 6, termo)
    injetar_assinatura_simples(pdf, img_assinatura, "Assinatura do Colaborador")
    return pdf.output()

def criar_pdf_fardamento(obra, nome, funcao, item_fard, qtd, data, img_assinatura):
    pdf = FPDF()
    desenhar_cabecalho_pdf(pdf, "TERMO DE ENTREGA DE FARDAMENTO")
    injetar_dados_colaborador_pdf(pdf, obra, nome, funcao, data)
    
    pdf.set_font("Arial", 'B', 11)
    pdf.cell(0, 8, f"ITEM DE FARDAMENTO FORNECIDO: {item_fard}   |   QUANTIDADE: {qtd}", ln=True)
    pdf.ln(10)
    termo = "DECLARO TER RECEBIDO DA EMPRESA ACIMA CITADA, CONJUNTO DE UNIFORME, CONFORME NORMA REGULAMENTADORA NR 24 ITENS: 24.8 VESTIMENTA DE TRABALHO. ME COMPROMETENDO A UTILIZA-LOS SOMENTE PARA FINS LABORAIS DURANTE TODA A JORNADA DE TRABALHO E DEVOLVE-LO NO TERMINO DO CONTRATO DE TRABALHO, SOB PENA DE SER ENQUADRADO EM PUNICOES DISCIPLINARES."
    pdf.set_font("Arial", 'I', 10)
    pdf.multi_cell(0, 6, termo)
    injetar_assinatura_simples(pdf, img_assinatura, "Assinatura do Colaborador")
    return pdf.output()

def criar_pdf_os(obra, nome, funcao, data_inicio, texto_os, data_emissao, img_ass1, img_ass2):
    pdf = FPDF()
    desenhar_cabecalho_pdf(pdf, "ORDEM DE SERVICO - SST")
    injetar_dados_colaborador_pdf(pdf, obra, nome, funcao, data_emissao)
    
    pdf.set_font("Arial", '', 10)
    pdf.cell(0, 6, f"DATA DE INICIO NA OBRA: {data_inicio}", ln=True)
    pdf.ln(8)
    pdf.set_font("Arial", 'B', 11)
    pdf.cell(0, 8, "TEXTO DA ORDEM DE SERVICO (DIRETRIZES E RISCOS):", ln=True)
    pdf.set_font("Arial", '', 9)
    pdf.multi_cell(0, 5, texto_os)
    injetar_assinatura_dupla(pdf, img_ass1, "Assinatura do Funcionario", img_ass2, "Responsavel de Seguranca")
    return pdf.output()

def criar_pdf_integracao(obra, nome, funcao, data, texto_integracao, img_ass1, img_ass2):
    pdf = FPDF()
    desenhar_cabecalho_pdf(pdf, "TERMO DE INTEGRACAO DE SEGURANCA - NR 18")
    injetar_dados_colaborador_pdf(pdf, obra, nome, funcao, data)
    
    pdf.set_font("Arial", 'B', 11)
    pdf.cell(0, 8, "CONTEUDO MINIMO ABORDADO NA INTEGRACAO:", ln=True)
    pdf.set_font("Arial", '', 9)
    pdf.multi_cell(0, 5, texto_integracao)
    injetar_assinatura_dupla(pdf, img_ass1, "Assinatura do Funcionario", img_ass2, "Gestor de Obras")
    return pdf.output()

def criar_pdf_treinamento(descricao, instrutor, data_realizacao, local, carga, validade, nome_func, funcao_func, img_assinatura):
    pdf = FPDF()
    desenhar_cabecalho_pdf(pdf, "ATA DE TREINAMENTO / DDS")
    pdf.set_font("Arial", '', 11)
    pdf.cell(0, 7, f"TEMA / DESCRICAO: {descricao}", ln=True)
    pdf.cell(0, 7, f"INSTRUTOR RESPONSAVEL: {instrutor}", ln=True)
    pdf.cell(0, 7, f"DATA DE REALIZACAO: {data_realizacao}", ln=True)
    pdf.cell(0, 7, f"LOCAL: {local}", ln=True)
    pdf.cell(0, 7, f"CARGA HORARIA: {carga}  |  VALIDADE: {validade}", ln=True)
    pdf.ln(10)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 8, "LISTA DE PRESENCA - PARTICIPANTE", ln=True)
    pdf.set_font("Arial", '', 11)
    pdf.cell(0, 8, f"Nome: {nome_func}", ln=True)
    pdf.cell(0, 8, f"Funcao: {funcao_func}", ln=True)
    injetar_assinatura_simples(pdf, img_assinatura, "Assinatura do Participante")
    return pdf.output()

# ─────────────────────────────────────────────
# BANCO DE DADOS E LISTAS
# ─────────────────────────────────────────────
if 'db_funcionarios' not in st.session_state: st.session_state['db_funcionarios'] = {}
OBRAS = ["Selecione...", "Arc Space", "Burj Lavie", "The Well", "JCarlos"]
FUNCOES = ["Selecione...", "PEDREIRO", "AJUDANTE", "CARPINTEIRO", "BETONEIRO", "ARMADOR", "ELETRICISTA", "MESTRE DE OBRAS", "GUINCHEIRO", "ALMOXARIFE", "ENCANADOR"]
EPIS = ["Selecione...", "DE COURO", "DE BORRACHA", "DE SEGURANÇA", "ÓCULOS DE SEGURANÇA TRANSPARENTE", "ÓCULOS DE SEGURANÇA FUMÊ", "BALACLAVA DE SEGURANÇA", "TOUCA ÁRABE", "MÁSCARA PFF1", "MÁSCARA PFF2", "MÁSCARA PFF3", "PROTETOR AUDITIVO PLUG", "PROTETOR AUDITIVO CONCHA", "LUVA LATEX", "LUVA VULCANIZADA", "LUVA DE VAQUETA", "AVENTAL DE SEGURANÇA", "CINTO DE SEGURANÇA TIPO PARAQUEDISTA", "TALABARTE", "TRAVA QUEDAS", "COLETE REFLEXIVO", "LUVA ISOLANTE"]

def get_base64_image(image_path):
    if not os.path.exists(image_path): return None
    with open(image_path, "rb") as img_file: return base64.b64encode(img_file.read()).decode()

def processar_cadastro_padrao(nome_aba, nova_linha, nome_func, funcao, obra):
    """Função DRY que centraliza o salvamento na nuvem"""
    planilha = conectar_planilha(nome_aba)
    planilha.append_row(nova_linha)
    st.cache_data.clear() # Invalida o cache para mostrar dados frescos
    if nome_func not in st.session_state['db_funcionarios']: 
        st.session_state['db_funcionarios'][nome_func] = {'status': 'Ativo', 'funcao': funcao, 'obra': obra}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CSS GLOBAL E AUXILIARES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def inject_custom_css():
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');
        html, body, [class*="css"] { font-family: 'Inter', sans-serif; background-color: #0a0a0a; }
        .stApp { background: radial-gradient(circle at center, #1a1a1a 0%, #050505 100%); color: #e4e4e7 !important; }
        [data-testid="stSidebar"], [data-testid="stSidebar"] > div:first-child { background-color: #050505 !important; border-right: 1px solid rgba(227, 112, 38, 0.2) !important; }
        [data-testid="stHeader"] { background-color: transparent !important; }
        .block-container { padding-top: 2rem !important; padding-bottom: 3rem !important; max-width: 1400px; }
        #MainMenu, footer, header { visibility: hidden; }
        button[kind="header"], [data-testid="StyledFullScreenButton"] { display: none !important; visibility: hidden !important; }
        [data-testid="collapsedControl"] { background-color: #050505 !important; border: 1px solid rgba(227, 112, 38, 0.2) !important; border-radius: 8px !important; top: 15px !important; left: 15px !important; z-index: 999999 !important; display: flex !important; visibility: visible !important; }
        [data-testid="collapsedControl"] svg { fill: #E37026 !important; color: #E37026 !important; }
        [data-testid="collapsedControl"]:hover { background-color: rgba(227, 112, 38, 0.15) !important; border-color: #E37026 !important; }
        ::-webkit-scrollbar { width: 5px; height: 5px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #E37026; border-radius: 99px; }
        ::-webkit-scrollbar-thumb:hover { background: #E37026; }
        * { scrollbar-width: thin; scrollbar-color: #E37026 transparent; }
        h1, h2, h3, h4 { color: #ffffff !important; font-weight: 600; letter-spacing: -0.5px; }
        [data-testid="stForm"], div.card { background: rgba(255, 255, 255, 0.02) !important; border: 1px solid rgba(227, 112, 38, 0.15) !important; border-radius: 12px !important; padding: 2rem !important; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4) !important; transition: border-color 0.3s ease, transform 0.3s ease; }
        [data-testid="stForm"]:hover { border-color: rgba(227, 112, 38, 0.35) !important; }
        [data-baseweb="input"], [data-baseweb="select"], [data-baseweb="textarea"], .stSelectbox > div > div, .stTextInput > div > div, .stTextArea > div > div, [data-testid="stTextInput"] > div > div, [data-testid="stTextArea"] > div > div { background-color: rgba(255, 255, 255, 0.05) !important; border: 1px solid rgba(255, 255, 255, 0.1) !important; border-radius: 10px !important; color: white !important; transition: border-color 0.3s ease, box-shadow 0.3s ease; }
        input, textarea, [data-baseweb="select"] div { background-color: transparent !important; color: #ffffff !important; font-size: 0.9rem !important; font-weight: 400 !important; }
        input::placeholder, textarea::placeholder { color: rgba(255, 255, 255, 0.3) !important; }
        [data-baseweb="input"]:focus-within, [data-baseweb="select"]:focus-within, [data-baseweb="textarea"]:focus-within, .stSelectbox > div > div:focus-within { border-color: #E37026 !important; box-shadow: 0 0 0 2px rgba(227, 112, 38, 0.15) !important; }
        ul[data-baseweb="menu"] { background-color: #111111 !important; border: 1px solid rgba(255, 255, 255, 0.1) !important; border-radius: 10px !important; box-shadow: 0 12px 40px rgba(0, 0, 0, 0.6) !important; }
        ul[data-baseweb="menu"] li { color: #aaa !important; font-size: 0.9rem !important; padding: 0.5rem 1rem !important; transition: all 0.2s ease; border-radius: 6px; }
        ul[data-baseweb="menu"] li:hover, ul[data-baseweb="menu"] li[aria-selected="true"] { background-color: rgba(227, 112, 38, 0.15) !important; color: #E37026 !important; font-weight: 600 !important; }
        .stButton > button, div[data-testid="stFormSubmitButton"] > button, [data-testid="stDownloadButton"] > button { background-color: #E37026 !important; color: white !important; border-radius: 12px !important; border: none !important; padding: 14px 24px !important; font-weight: 600 !important; font-size: 0.95rem !important; text-transform: uppercase !important; letter-spacing: 1px !important; width: 100% !important; min-height: 54px !important; box-shadow: 0 0 15px rgba(227, 112, 38, 0.4) !important; transition: all 0.3s ease !important; cursor: pointer; display: flex !important; align-items: center !important; justify-content: center !important; }
        .stButton > button:hover, div[data-testid="stFormSubmitButton"] > button:hover, [data-testid="stDownloadButton"] > button:hover { background-color: #f0853d !important; transform: translateY(-2px) !important; box-shadow: 0 0 25px rgba(227, 112, 38, 0.5) !important; }
        .stButton > button:active, div[data-testid="stFormSubmitButton"] > button:active, [data-testid="stDownloadButton"] > button:active { transform: translateY(0) !important; box-shadow: 0 0 10px rgba(227, 112, 38, 0.3) !important; }
        [data-testid="stTextInput"] button { background: transparent !important; width: auto !important; padding: 0.2rem 0.4rem !important; min-height: 0 !important; font-size: 0.7rem !important; box-shadow: none !important; color: rgba(255, 255, 255, 0.3) !important; margin: 0 !important; border-radius: 6px !important; }
        [data-testid="stTextInput"] button:hover { background: rgba(227, 112, 38, 0.1) !important; color: #E37026 !important; box-shadow: none !important; transform: none !important; }
        [data-testid="stSidebar"] .stButton > button { background: transparent !important; border: 1px solid rgba(255, 255, 255, 0.1) !important; color: #666 !important; font-weight: 500 !important; font-size: 0.85rem !important; box-shadow: none !important; width: 100% !important; min-height: 40px !important; letter-spacing: 0.5px !important; }
        [data-testid="stSidebar"] .stButton > button:hover { background: rgba(227, 112, 38, 0.1) !important; border-color: rgba(227, 112, 38, 0.3) !important; color: #E37026 !important; box-shadow: none !important; transform: none !important; }
        [data-testid="stFileUploadDropzone"], [data-testid="stFileUploadDropzone"] > div, [data-testid="stFileUploadDropzone"] > section, [data-testid="stFileUploadDropzone"] > button { background-color: rgba(255, 255, 255, 0.02) !important; background-image: none !important; border: none !important; border-radius: 10px !important; }
        [data-testid="stFileUploadDropzone"] { border: 1px dashed rgba(255, 255, 255, 0.2) !important; transition: all 0.3s ease; }
        [data-testid="stFileUploadDropzone"]:hover { border-color: rgba(227, 112, 38, 0.4) !important; background-color: rgba(227, 112, 38, 0.08) !important; }
        
        .badge-ativo { background: rgba(34, 197, 94, 0.1); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.2); padding: 4px 12px; border-radius: 20px; font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; display: inline-flex; align-items: center; gap: 6px; }
        .badge-ativo::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: #22c55e; box-shadow: 0 0 6px #22c55e; }
        .badge-inativo { background: rgba(239, 68, 68, 0.1); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.2); padding: 4px 12px; border-radius: 20px; font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; display: inline-flex; align-items: center; gap: 6px; }
        .badge-inativo::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: #ef4444; box-shadow: 0 0 6px #ef4444; }
        
        .custom-table-container { background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 10px; padding: 1rem; overflow-x: auto; margin-top: 1.5rem; }
        .custom-table { width: 100%; border-collapse: collapse; }
        .custom-table th { color: #E37026; padding: 12px; text-align: left; font-size: 0.75rem; text-transform: uppercase; border-bottom: 1px solid rgba(227, 112, 38, 0.3); }
        .custom-table td { padding: 12px; border-bottom: 1px solid rgba(255, 255, 255, 0.05); color: #d4d4d8; font-size: 0.85rem; }
        .custom-table tr:last-child td { border-bottom: none; }
        .custom-table tr:hover td { background-color: rgba(255, 255, 255, 0.03); }
        .doc-valido { color: #4ade80; font-weight: 600; }
        .doc-vencido { color: #f87171; font-weight: 600; }
        .doc-obsoleto { color: #fbbf24; font-weight: 600; }
        
        .legal-box { background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(227, 112, 38, 0.15); border-left: 3px solid #E37026; padding: 1.25rem; border-radius: 10px; margin: 1.5rem 0; }
        .legal-box-header { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.75rem; font-size: 0.75rem; color: #E37026; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; }
        .legal-box p { color: #888; font-size: 0.8rem; margin: 0; line-height: 1.8; }
        .dashboard-metric { background-color: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 0.5rem; padding: 1rem; text-align: center; margin-top: 1rem; }
        .dashboard-metric h3 { font-size: 2rem !important; color: #f97316 !important; margin: 0; border-bottom: none !important; }
        .dashboard-metric p { color: #a1a1aa; font-size: 0.75rem; margin: 0; font-weight: 600; text-transform: uppercase;}
        .section-divider { display: flex; align-items: center; gap: 1rem; margin: 1.5rem 0; }
        .section-divider::before, .section-divider::after { content: ''; flex: 1; height: 1px; background: rgba(255, 255, 255, 0.06); }
        .section-divider span { color: #666; font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 2px; white-space: nowrap; }
        .sidebar-logo-container { text-align: center; padding: 10px 0; margin-bottom: 15px; }
        .sidebar-logo-text { font-family: 'Inter', sans-serif; font-weight: 700; font-size: 1.5rem; color: white; letter-spacing: 2px; }
        .sidebar-logo-sub { font-size: 0.7rem; color: #E37026; text-transform: uppercase; letter-spacing: 3px; }
        .sidebar-footer { color: #333; font-size: 0.65rem; text-align: center; padding: 1rem 0; border-top: 1px solid rgba(227, 112, 38, 0.1); margin-top: 1rem; letter-spacing: 1px; }
        div[data-baseweb="notification"] { background-color: #111 !important; border: 1px solid rgba(34, 197, 94, 0.2) !important; border-left: 3px solid #22c55e !important; border-radius: 10px !important; }
        .stAlert > div { background: rgba(255, 255, 255, 0.02) !important; border: 1px solid rgba(255, 255, 255, 0.1) !important; color: #aaa !important; border-radius: 10px !important; }
        .login-container { background-color: transparent; background-image: linear-gradient(160deg, #1e1e1f 0%, #0a0a0c 100%); border: 1px solid rgba(255, 255, 255, 0.1); padding: 40px; border-radius: 12px; text-align: center; margin-bottom: 10px; }
        .login-container + div [data-testid="stForm"], div:has(> .login-container) ~ div [data-testid="stForm"] { background: transparent !important; border: none !important; box-shadow: none !important; padding: 0 !important; }
        </style>
    """, unsafe_allow_html=True)

def section_divider(label):
    st.markdown(f'<div class="section-divider"><span>{label}</span></div>', unsafe_allow_html=True)

def legal_box(texto):
    st.markdown(f'<div class="legal-box"><div class="legal-box-header">§ Declaração Legal</div><p>{texto}</p></div>', unsafe_allow_html=True)

def get_nomes_db(): return ["Selecione..."] + list(st.session_state.get('db_funcionarios', {}).keys())

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TELA DE LOGIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def render_login():
    inject_custom_css()
    _, col_login, _ = st.columns([1, 1, 1])
    with col_login:
        logo_file = "assets/logo.png" if os.path.exists("assets/logo.png") else "assets/logo.jpg"
        img_b64 = get_base64_image(logo_file)
        header_html = f'<img src="data:image/png;base64,{img_b64}" style="width: 100%; max-width: 500px; height: auto; display: block; margin: 0 auto 20px auto;">' if img_b64 else "<h2 style='color:#E37026;'>LAVIE</h2>"
        st.markdown(f"<div class='login-container'>{header_html}<h2 style='color:#E37026; font-size: 2.5rem; margin-top: 10px; margin-bottom: 0px; letter-spacing: 3px;'>SST</h2><p style='color:#E37026; font-size: 0.8rem; margin-top: 5px; letter-spacing: 2px;'>Segurança e Saúde do Trabalho</p></div>", unsafe_allow_html=True)
        with st.form("login_form", clear_on_submit=False):
            st.markdown("<p style='text-align: left; font-size: 14px; margin-bottom: 5px; color: #aaa;'>Usuário</p>", unsafe_allow_html=True)
            usuario = st.text_input("Usuário", label_visibility="collapsed", placeholder="Digite o usuário")
            st.markdown("<p style='text-align: left; font-size: 14px; margin-bottom: 5px; color: #aaa; margin-top:10px;'>Senha</p>", unsafe_allow_html=True)
            senha = st.text_input("Senha", type="password", label_visibility="collapsed", placeholder="••••••••")
            st.markdown("<div style='height: 1rem;'></div>", unsafe_allow_html=True)
            if st.form_submit_button("Acessar Painel", use_container_width=True):
                if usuario == ADMIN_USER and senha == ADMIN_PASS:
                    st.session_state['autenticado'] = True
                    with st.spinner("A conectar ao banco de dados na nuvem..."): sincronizar_funcionarios_nuvem()
                    st.rerun()
                else:
                    st.error("Credenciais inválidas.")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MÓDULOS DE CADASTRO (APENAS ENVIO DE DADOS)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def render_module_1():
    st.header("ENTREGA DE EPI", divider="orange")
    with st.form("form_epi"):
        c1, c2, c3 = st.columns([2, 3, 1])
        obra = c1.selectbox("OBRA", OBRAS)
        nome = c2.text_input("NOME FUNCIONÁRIO", key="nome_epi")
        c4, c5 = st.columns(2)
        funcao = c4.selectbox("FUNÇÃO", FUNCOES)
        data = c5.date_input("DATA", value=datetime.today())

        section_divider("Dados do EPI")
        c6, c7, c8 = st.columns([2, 1, 1])
        epi = c6.selectbox("EPI", EPIS)
        qtd = c7.text_input("QUANTIDADE")
        ca = c8.text_input("Nº DE CA")

        legal_box("DECLARO PARA TODOS OS EFEITOS LEGAIS QUE RECEBI OS EPI's DISCRIMINADOS NESTA FICHA, FICANDO RESPONSÁVEL PELO USO, GUARDA E CONSERVAÇÃO DOS MESMOS, DEVENDO INDENIZÁ-LA À EMPRESA, CASO OCORRAM DANOS POR COMPROVADA NEGLIGÊNCIA OU EXTRAVIO.")
        
        section_divider("Assinatura e Anexos")
        c9, c10 = st.columns([2, 1])
        with c9:
            st.markdown("<label>ASSINATURA DO FUNCIONÁRIO</label>", unsafe_allow_html=True)
            canvas_ass = st_canvas(fill_color="rgba(255, 255, 255, 0.3)", stroke_width=3, stroke_color="#000000", background_color="#F0F2F6", height=150, width=600, drawing_mode="freedraw", key="canvas_epi")
        with c10:
            foto = st.file_uploader("FOTO (ANEXO)", type=['png', 'jpg', 'jpeg'])

        if st.form_submit_button("SALVAR DADOS", use_container_width=True):
            if not nome or obra == "Selecione..." or funcao == "Selecione..." or epi == "Selecione...":
                st.error("Por favor, preencha todos os campos obrigatórios.")
            else:
                with st.spinner("A enviar informações para a nuvem..."):
                    try:
                        b64_ass = canvas_to_base64(canvas_ass)
                        data_str = data.strftime('%d/%m/%Y')
                        link_foto = "Sem anexo"
                        if foto is not None:
                            nome_arq_drive = f"Foto_EPI_{nome.replace(' ', '_')}_{datetime.now().strftime('%H%M%S')}.jpg"
                            link_foto = upload_para_drive_via_gas(foto.getvalue(), nome_arq_drive)

                        processar_cadastro_padrao("EPI", [data_str, obra, nome, funcao, epi, qtd, ca, b64_ass, link_foto], nome, funcao, obra)
                        st.success("✓ Dados cadastrados com sucesso no banco!")
                    except Exception as e: st.error(f"Erro: {e}")

def render_module_2():
    st.header("TERMO DE ENTREGA CESTA BÁSICA", divider="orange")
    with st.form("form_cesta"):
        c1, c2 = st.columns(2)
        obra = c1.selectbox("OBRA", OBRAS)
        nome = c2.text_input("NOME FUNCIONÁRIO", key="nome_cesta")
        c3, c4 = st.columns(2)
        funcao = c3.selectbox("FUNÇÃO", FUNCOES)
        data = c4.date_input("DATA", value=datetime.today())

        legal_box("Declaro que recebi 01 cesta basica completa de acordo com a recomendacao feita pela convencao coletiva de trabalho vigente. Também declaro que recebi o cafe da manha de acordo com a recomendacao feita pela convencao coletiva de trabalho vigente, confirmando que recebo 02 paes, manteiga, ovos/queijo e cafe diariamente.")
        
        st.markdown("<label>ASSINATURA DO FUNCIONÁRIO</label>", unsafe_allow_html=True)
        canvas_ass = st_canvas(fill_color="rgba(255, 255, 255, 0.3)", stroke_width=3, stroke_color="#000000", background_color="#F0F2F6", height=150, width=800, drawing_mode="freedraw", key="canvas_cesta")

        if st.form_submit_button("SALVAR DADOS", use_container_width=True):
            if not nome or obra == "Selecione..." or funcao == "Selecione...": st.error("Preencha os campos obrigatórios.")
            else:
                with st.spinner("A enviar informações para a nuvem..."):
                    try:
                        processar_cadastro_padrao("CESTA", [data.strftime('%d/%m/%Y'), obra, nome, funcao, canvas_to_base64(canvas_ass)], nome, funcao, obra)
                        st.success("✓ Dados cadastrados com sucesso!")
                    except Exception as e: st.error(f"Erro ao salvar: {e}")

def render_module_3():
    st.header("TERMO DE ENTREGA DE ARMARIO/CADEADO", divider="orange")
    with st.form("form_armario"):
        c1, c2 = st.columns(2)
        obra = c1.selectbox("OBRA", OBRAS)
        nome = c2.text_input("NOME FUNCIONÁRIO", key="nome_armario")
        c3, c4 = st.columns(2)
        funcao = c3.selectbox("FUNÇÃO", FUNCOES)
        data = c4.date_input("DATA", value=datetime.today())

        legal_box("DECLARO PARA TODOS OS EFEITOS LEGAIS QUE RECEBI ARMÁRIO E CADEADO PARA CONSERVAÇÃO DE ITENS E EQUIPAMENTOS PESSOAIS, FICANDO RESPONSÁVEL PELO USO, GUARDA E CONSERVAÇÃO DOS MESMOS, DEVENDO INDENIZÁ-LA À EMPRESA, CASO OCORRAM DANOS POR COMPROVADA NEGLIGÊNCIA OU MAL USO.")
        
        st.markdown("<label>ASSINATURA DO FUNCIONÁRIO</label>", unsafe_allow_html=True)
        canvas_ass = st_canvas(fill_color="rgba(255, 255, 255, 0.3)", stroke_width=3, stroke_color="#000000", background_color="#F0F2F6", height=150, width=800, drawing_mode="freedraw", key="canvas_armario")

        if st.form_submit_button("SALVAR DADOS", use_container_width=True):
            if not nome or obra == "Selecione..." or funcao == "Selecione...": st.error("Preencha os campos obrigatórios.")
            else:
                with st.spinner("A enviar informações para a nuvem..."):
                    try:
                        processar_cadastro_padrao("ARMARIO", [data.strftime('%d/%m/%Y'), obra, nome, funcao, canvas_to_base64(canvas_ass)], nome, funcao, obra)
                        st.success("✓ Dados cadastrados com sucesso!")
                    except Exception as e: st.error(f"Erro ao salvar: {e}")

def render_module_4():
    st.header("TERMO DE ENTREGA DE FARDAMENTO", divider="orange")
    with st.form("form_fardamento"):
        c1, c2 = st.columns(2)
        obra = c1.selectbox("OBRA", OBRAS)
        nome = c2.text_input("NOME FUNCIONÁRIO", key="nome_fard")
        funcao = st.selectbox("FUNÇÃO", FUNCOES)
        c3, c4 = st.columns([3, 1])
        item_fard = c3.text_input("ITEM DE FARDAMENTO")
        qtd = c4.text_input("QUANTIDADE")

        legal_box("DECLARO TER RECEBIDO DA EMPRESA ACIMA CITADA, CONJUNTO DE UNIFORME, CONFORME NORMA REGULAMENTADORA NR 24. ME COMPROMETENDO A UTILIZÁ-LOS SOMENTE PARA FINS LABORAIS DURANTE TODA A JORNADA DE TRABALHO E DEVOLVÊ-LO NO TÉRMINO DO CONTRATO, SOB PENA DE SER ENQUADRADO EM PUNIÇÕES DISCIPLINARES.")
        
        c5, c6 = st.columns([1, 2])
        with c5: data = st.date_input("DATA", value=datetime.today())
        with c6:
            st.markdown("<label>ASSINATURA DO FUNCIONÁRIO</label>", unsafe_allow_html=True)
            canvas_ass = st_canvas(fill_color="rgba(255, 255, 255, 0.3)", stroke_width=3, stroke_color="#000000", background_color="#F0F2F6", height=150, width=600, drawing_mode="freedraw", key="canvas_fardamento")

        if st.form_submit_button("SALVAR DADOS", use_container_width=True):
            if not nome or obra == "Selecione..." or funcao == "Selecione...": st.error("Preencha os campos obrigatórios.")
            else:
                with st.spinner("A enviar informações para a nuvem..."):
                    try:
                        processar_cadastro_padrao("FARDAMENTO", [data.strftime('%d/%m/%Y'), obra, nome, funcao, item_fard, qtd, canvas_to_base64(canvas_ass)], nome, funcao, obra)
                        st.success("✓ Dados cadastrados com sucesso!")
                    except Exception as e: st.error(f"Erro ao salvar: {e}")

def render_module_5():
    st.header("ORDEM DE SERVIÇO", divider="orange")
    with st.form("form_os"):
        c1, c2 = st.columns(2)
        funcao = c1.selectbox("FUNÇÃO", FUNCOES)
        obra = c2.selectbox("OBRA", OBRAS)
        c3, c4 = st.columns(2)
        nome = c3.text_input("NOME DO FUNCIONÁRIO", key="nome_os")
        data_inicio = c4.date_input("DATA DE INÍCIO EM OBRA", value=datetime.today())
        if funcao != "Selecione...": st.info(f"O sistema carregará automaticamente o texto da OS referente à função: {funcao}")
        texto_os = st.text_area("TEXTO DE OS:", height=150, placeholder="Digite ou cole aqui o texto da Ordem de Serviço...")

        section_divider("Assinaturas")
        data = st.date_input("DATA DE EMISSÃO", value=datetime.today(), key="data_os")
        c_ass1, c_ass2 = st.columns(2)
        with c_ass1:
            st.markdown("<label>ASSINATURA FUNCIONÁRIO</label>", unsafe_allow_html=True)
            canvas_ass1 = st_canvas(fill_color="rgba(255, 255, 255, 0.3)", stroke_width=3, stroke_color="#000000", background_color="#F0F2F6", height=150, width=450, drawing_mode="freedraw", key="canvas_os_func")
        with c_ass2:
            st.markdown("<label>ASSINATURA RESP. SEGURANÇA</label>", unsafe_allow_html=True)
            canvas_ass2 = st_canvas(fill_color="rgba(255, 255, 255, 0.3)", stroke_width=3, stroke_color="#000000", background_color="#F0F2F6", height=150, width=450, drawing_mode="freedraw", key="canvas_os_seg")

        if st.form_submit_button("SALVAR DADOS", use_container_width=True):
            if not nome or obra == "Selecione..." or funcao == "Selecione...": st.error("Preencha os campos obrigatórios.")
            else:
                with st.spinner("A enviar informações para a nuvem..."):
                    try:
                        processar_cadastro_padrao("OS", [data.strftime('%d/%m/%Y'), data_inicio.strftime('%d/%m/%Y'), obra, nome, funcao, texto_os, canvas_to_base64(canvas_ass1), canvas_to_base64(canvas_ass2)], nome, funcao, obra)
                        st.success("✓ Dados cadastrados com sucesso!")
                    except Exception as e: st.error(f"Erro ao salvar: {e}")

def render_module_6():
    st.header("INTEGRAÇÃO", divider="orange")
    with st.form("form_nr18"):
        c1, c2 = st.columns(2)
        obra = c1.selectbox("OBRA", OBRAS)
        nome = c2.text_input("NOME DO FUNCIONÁRIO", key="nome_nr18")
        c3, c4 = st.columns(2)
        funcao = c3.selectbox("FUNÇÃO", FUNCOES)
        data = c4.date_input("DATA", value=datetime.today())

        texto_integracao = st.text_area("TEXTO DE INTEGRAÇÃO", height=120, placeholder="Digite aqui o conteúdo da integração de segurança...")

        section_divider("Assinaturas")
        c5, c6 = st.columns(2)
        with c5:
            st.markdown("<label>ASSINATURA DO FUNCIONÁRIO</label>", unsafe_allow_html=True)
            canvas_ass1 = st_canvas(fill_color="rgba(255, 255, 255, 0.3)", stroke_width=3, stroke_color="#000000", background_color="#F0F2F6", height=150, width=450, drawing_mode="freedraw", key="canvas_nr18_func")
        with c6:
            st.markdown("<label>ASSINATURA DO GESTOR</label>", unsafe_allow_html=True)
            canvas_ass2 = st_canvas(fill_color="rgba(255, 255, 255, 0.3)", stroke_width=3, stroke_color="#000000", background_color="#F0F2F6", height=150, width=450, drawing_mode="freedraw", key="canvas_nr18_gestor")

        if st.form_submit_button("SALVAR DADOS", use_container_width=True):
            if not nome or obra == "Selecione..." or funcao == "Selecione...": st.error("Preencha os campos obrigatórios.")
            else:
                with st.spinner("A enviar informações para a nuvem..."):
                    try:
                        processar_cadastro_padrao("INTEGRACAO", [data.strftime('%d/%m/%Y'), obra, nome, funcao, texto_integracao, canvas_to_base64(canvas_ass1), canvas_to_base64(canvas_ass2)], nome, funcao, obra)
                        st.success("✓ Dados cadastrados com sucesso!")
                    except Exception as e: st.error(f"Erro ao salvar: {e}")

def render_module_7():
    st.header("TREINAMENTOS (RENOVAÇÃO VERIFICAR)", divider="orange")
    with st.form("form_dds"):
        c1, c2 = st.columns(2)
        descricao = c1.text_input("DESCRIÇÃO", placeholder="Ex: DDS sobre trabalho em altura")
        instrutor = c2.text_input("INSTRUTOR", placeholder="Nome do instrutor responsável")
        c3, c4, c5, c6 = st.columns(4)
        data_realizacao = c3.date_input("DATA DE REALIZAÇÃO", value=datetime.today())
        local = c4.text_input("LOCAL", placeholder="Ex: Canteiro A")
        carga = c5.text_input("CARGA HORÁRIA", placeholder="Ex: 2h")
        validade = c6.text_input("VALIDADE", placeholder="Ex: 12 meses")

        section_divider("Participante")
        c_f1, c_f2 = st.columns(2)
        nome_func = c_f1.text_input("NOME FUNCIONÁRIO", key="func_treinamento", placeholder="Nome do participante")
        funcao_func = c_f2.selectbox("FUNÇÃO", FUNCOES, key="funcao_treinamento")
        
        st.markdown("<label>ASSINATURA DO PARTICIPANTE</label>", unsafe_allow_html=True)
        canvas_ass = st_canvas(fill_color="rgba(255, 255, 255, 0.3)", stroke_width=3, stroke_color="#000000", background_color="#F0F2F6", height=150, width=800, drawing_mode="freedraw", key="canvas_treinamento")

        if st.form_submit_button("SALVAR DADOS", use_container_width=True):
            if not nome_func or funcao_func == "Selecione...": st.error("Preencha os dados do participante principal.")
            else:
                with st.spinner("A enviar informações para a nuvem..."):
                    try:
                        processar_cadastro_padrao("TREINAMENTO", [data_realizacao.strftime('%d/%m/%Y'), descricao, instrutor, local, carga, validade, nome_func, funcao_func, canvas_to_base64(canvas_ass)], nome_func, funcao_func, "-")
                        st.success("✓ Dados cadastrados com sucesso!")
                    except Exception as e: st.error(f"Erro ao salvar: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MÓDULOS DE ACOMPANHAMENTO E GERADOR DE PDF
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def render_acomp_obra():
    st.header("OBRA", divider="orange")
    col_sync1, col_sync2 = st.columns([3, 1])
    with col_sync2:
        if st.button("↻ Sincronizar Base", key="btn_sync_obra", type="primary", use_container_width=True):
            with st.spinner("A sincronizar..."): sincronizar_funcionarios_nuvem()
            st.rerun()

    with st.form("form_acomp_obra"):
        obra = st.selectbox("SELECIONE A OBRA PARA ACOMPANHAMENTO", OBRAS)
        submit = st.form_submit_button("BUSCAR DADOS DA OBRA", use_container_width=True)
        
    if submit and obra != "Selecione...":
        funcs = {k: v for k, v in st.session_state.get('db_funcionarios', {}).items() if v['obra'] == obra}
        ativos = len(funcs)
        c1, c2 = st.columns(2)
        c1.markdown(f'<div class="dashboard-metric"><h3>{ativos}</h3><p>Total de Colaboradores</p></div>', unsafe_allow_html=True)
        c2.markdown(f'<div class="dashboard-metric"><h3>{obra}</h3><p>Obra Selecionada</p></div>', unsafe_allow_html=True)
        
        rows = ""
        for nome, dados in funcs.items():
            badge = '<span class="badge-ativo">Ativo</span>'
            rows += f"<tr><td>{nome}</td><td>{dados['funcao']}</td><td>{badge}</td></tr>"
        if not rows: rows = "<tr><td colspan='3' style='text-align:center;'>Nenhum colaborador encontrado para esta obra.</td></tr>"
        st.markdown(f'<div class="custom-table-container"><table class="custom-table"><tr><th>Colaborador</th><th>Função Registrada</th><th>Status</th></tr>{rows}</table></div>', unsafe_allow_html=True)

def render_acomp_funcionario():
    st.header("FUNCIONARIO", divider="orange")
    col_sync1, col_sync2 = st.columns([3, 1])
    with col_sync2:
        if st.button("↻ Sincronizar Base", key="btn_sync_func", type="primary", use_container_width=True):
            with st.spinner("A sincronizar..."): sincronizar_funcionarios_nuvem()
            st.rerun()

    with st.form("form_acomp_func"):
        nome = st.selectbox("SELECIONE O FUNCIONÁRIO", get_nomes_db())
        submit = st.form_submit_button("BUSCAR DADOS REAIS", use_container_width=True)
        
    if submit and nome != "Selecione...":
        status = st.session_state['db_funcionarios'][nome]['status']
        css_class = "badge-ativo" if status == "Ativo" else "badge-inativo"
        st.markdown(f'<div style="margin-top: 1rem; margin-bottom: 2rem;"><span class="{css_class}">{status}</span></div>', unsafe_allow_html=True)
        st.markdown("<h4 style='color: #fff;'>Documentação Real Registrada na Nuvem</h4>", unsafe_allow_html=True)
        
        rows = ""
        mapa_abas = { "EPI": "Entrega de EPI", "CESTA": "Cesta Básica", "ARMARIO": "Armário/Cadeado", "FARDAMENTO": "Fardamento", "OS": "Ordem de Serviço", "INTEGRACAO": "Integração", "TREINAMENTO": "Treinamentos" }
        
        with st.spinner("Buscando o histórico real no Google Sheets..."):
            for sigla, nome_amigavel in mapa_abas.items():
                try:
                    registros = get_dados_planilha(sigla)
                    for linha in registros[1:]:
                        idx_nome = 2
                        if sigla == "OS": idx_nome = 3
                        elif sigla == "TREINAMENTO": idx_nome = 6
                        
                        if len(linha) > idx_nome and linha[idx_nome].strip() == nome:
                            data_doc = linha[0]
                            detalhe = "Registrado"
                            if sigla == "TREINAMENTO" and len(linha) > 5: detalhe = f"Validade: {linha[5]}"
                            elif sigla == "EPI" and len(linha) > 4: detalhe = f"EPI: {linha[4]}"
                            rows += f"<tr><td>{nome_amigavel}</td><td>{data_doc}</td><td class='doc-valido'>{detalhe}</td></tr>"
                except: continue
        
        if not rows: rows = "<tr><td colspan='3' style='text-align:center;'>Nenhum documento encontrado.</td></tr>"
        st.markdown(f'<div class="custom-table-container"><table class="custom-table"><tr><th>Tipo de Documento</th><th>Data de Emissão</th><th>Detalhes do Documento</th></tr>{rows}</table></div>', unsafe_allow_html=True)
        legal_box("Os dados acima são extraídos em tempo real do banco de dados na nuvem.")

def render_acomp_item():
    st.header("ITEM", divider="orange")
    col_sync1, col_sync2 = st.columns([3, 1])
    with col_sync2:
        if st.button("↻ Sincronizar Base", key="btn_sync_item", type="primary", use_container_width=True):
            with st.spinner("A sincronizar..."): sincronizar_funcionarios_nuvem()
            st.rerun()

    with st.form("form_acomp_item"):
        opcoes_itens = ["Selecione...", "Entrega de EPI", "Cesta Básica", "Armário/Cadeado", "Fardamento", "Ordem de Serviço", "Integração", "Treinamentos"]
        item = st.selectbox("SELECIONE O TIPO DE DOCUMENTO", opcoes_itens)
        submit = st.form_submit_button("BUSCAR HISTÓRICO GERAL", use_container_width=True)
        
    if submit and item != "Selecione...":
        st.markdown(f"<h4 style='margin-top: 2rem; color: #fff;'>Histórico: {item}</h4>", unsafe_allow_html=True)
        mapa_abas = {"Entrega de EPI": "EPI", "Cesta Básica": "CESTA", "Armário/Cadeado": "ARMARIO", "Fardamento": "FARDAMENTO", "Ordem de Serviço": "OS", "Integração": "INTEGRACAO", "Treinamentos": "TREINAMENTO"}
        aba = mapa_abas.get(item)
        rows = ""
        
        with st.spinner(f"A procurar histórico de {item} na nuvem..."):
            try:
                registros = get_dados_planilha(aba)
                for linha in registros[1:]:
                    data_doc, obra, nome = "-", "-", "-"
                    if aba in ["EPI", "CESTA", "ARMARIO", "FARDAMENTO", "INTEGRACAO"] and len(linha) >= 3:
                        data_doc, obra, nome = linha[0], linha[1], linha[2]
                    elif aba == "OS" and len(linha) >= 4:
                        data_doc, obra, nome = linha[0], linha[2], linha[3]
                    elif aba == "TREINAMENTO" and len(linha) >= 7:
                        data_doc, obra, nome = linha[0], "-", linha[6]
                    
                    if nome == "-": continue 
                    status = st.session_state['db_funcionarios'][nome].get('status', 'Ativo') if nome in st.session_state.get('db_funcionarios', {}) else "Ativo"
                    doc_status = '<span class="doc-valido">Ativo</span>' if status == 'Ativo' else '<span class="doc-obsoleto">Obsoleto (Inativo)</span>'
                    rows += f"<tr><td>{data_doc}</td><td>{nome}</td><td>{obra}</td><td>{doc_status}</td></tr>"
            except: st.error("Erro ao ligar ao Google Sheets.")
            
        if not rows: rows = "<tr><td colspan='4' style='text-align:center;'>Nenhum registro encontrado.</td></tr>"
        st.markdown(f'<div class="custom-table-container"><table class="custom-table"><tr><th>Data</th><th>Colaborador</th><th>Obra</th><th>Situação do Documento</th></tr>{rows}</table></div>', unsafe_allow_html=True)

def render_acomp_editar():
    st.header("EDITAR CADASTRO", divider="orange")
    col_sync1, col_sync2 = st.columns([3, 1])
    with col_sync2:
        if st.button("↻ Sincronizar Base", key="btn_sync_editar", type="primary", use_container_width=True):
            with st.spinner("A sincronizar..."): sincronizar_funcionarios_nuvem()
            st.rerun()
            
    opcoes_modulos = { "Selecione...": None, "Entrega de EPI": "EPI", "Cesta Básica": "CESTA", "Armário/Cadeado": "ARMARIO", "Fardamento": "FARDAMENTO", "Ordem de Serviço": "OS", "Integração": "INTEGRACAO", "Treinamentos": "TREINAMENTO" }
    modulo_selecionado = st.selectbox("1. SELECIONE O MÓDULO ONDE ESTÁ O ERRO", list(opcoes_modulos.keys()))
    
    if modulo_selecionado != "Selecione...":
        nome_aba = opcoes_modulos[modulo_selecionado]
        with st.spinner("A conectar com a planilha..."):
            try:
                registros = get_dados_planilha(nome_aba)
            except Exception as e:
                st.error(f"Erro ao conectar: {e}")
                return
                
        if len(registros) <= 1: return st.info("Ainda não existem registros cadastrados neste módulo.")
            
        cabecalho = registros[0]
        opcoes_registros = ["Selecione um registro..."]
        for i, linha in enumerate(registros[1:], start=2):
            identificador = " - ".join(linha[:3]) if len(linha) >= 3 else f"Registro da linha {i}"
            opcoes_registros.append(f"Linha {i} | {identificador}")
            
        registro_selecionado = st.selectbox("2. SELECIONE O REGISTRO", opcoes_registros)
        
        if registro_selecionado != "Selecione um registro...":
            linha_idx = int(registro_selecionado.split(" ")[1])
            dados_atuais = registros[linha_idx - 1]
            while len(dados_atuais) < len(cabecalho): dados_atuais.append("")
            
            with st.form("form_editar_registro"):
                st.markdown("### Alterar Dados Cadastrados")
                novos_dados = []
                for col_idx, col_nome in enumerate(cabecalho):
                    valor_atual = dados_atuais[col_idx]
                    if len(valor_atual) > 200 or "http" in valor_atual:
                        st.text_input(col_nome, value="(Assinatura ou Anexo Criptografado)", disabled=True, key=f"edit_block_{col_idx}")
                        novos_dados.append(valor_atual)
                    else:
                        novo_valor = st.text_input(col_nome, value=valor_atual, key=f"edit_free_{col_idx}")
                        novos_dados.append(novo_valor)
                
                st.markdown("<div style='height: 1rem;'></div>", unsafe_allow_html=True)
                c_btn1, c_btn2 = st.columns(2)
                with c_btn1: submit_atualizar = st.form_submit_button("ATUALIZAR REGISTRO", type="primary", use_container_width=True)
                with c_btn2: submit_excluir = st.form_submit_button("EXCLUIR REGISTRO", use_container_width=True)

                if submit_atualizar:
                    with st.spinner("A reescrever a linha no Google Sheets..."):
                        planilha = conectar_planilha(nome_aba)
                        planilha.update(f"A{linha_idx}", [novos_dados])
                        st.cache_data.clear()
                        st.markdown('<div style="background-color: rgba(227, 112, 38, 0.1); border-left: 4px solid #E37026; padding: 15px; color: #E37026; border-radius: 5px; margin-bottom: 1rem;"><strong>⚠️ Sucesso:</strong> Registro atualizado! Clique em <b>Sincronizar Base</b>.</div>', unsafe_allow_html=True)
                if submit_excluir:
                    with st.spinner("A excluir a linha do Google Sheets..."):
                        planilha = conectar_planilha(nome_aba)
                        planilha.delete_rows(linha_idx)
                        st.cache_data.clear()
                        st.markdown('<div style="background-color: rgba(227, 112, 38, 0.1); border-left: 4px solid #E37026; padding: 15px; color: #E37026; border-radius: 5px; margin-bottom: 1rem;"><strong>⚠️ Sucesso:</strong> Registro excluído! Clique em <b>Sincronizar Base</b>.</div>', unsafe_allow_html=True)

def render_acomp_gerar_pdf():
    st.header("GERAR PDF", divider="orange")
    st.markdown("<p style='color:#aaa; font-size:0.9rem;'>Reconstrua o documento PDF de qualquer cadastro passado utilizando a assinatura digital guardada na nuvem.</p>", unsafe_allow_html=True)
    
    opcoes_modulos = { "Selecione...": None, "Entrega de EPI": "EPI", "Cesta Básica": "CESTA", "Armário/Cadeado": "ARMARIO", "Fardamento": "FARDAMENTO", "Ordem de Serviço": "OS", "Integração": "INTEGRACAO", "Treinamentos": "TREINAMENTO" }
    modulo_selecionado = st.selectbox("1. SELECIONE O MÓDULO DO DOCUMENTO", list(opcoes_modulos.keys()))
    
    if modulo_selecionado != "Selecione...":
        nome_aba = opcoes_modulos[modulo_selecionado]
        with st.spinner("A conectar com a planilha..."):
            try:
                registros = get_dados_planilha(nome_aba)
            except Exception as e:
                st.error("Erro ao conectar ao banco.")
                return
                
        if len(registros) <= 1: return st.info("Nenhum registro encontrado neste módulo.")
            
        opcoes_registros = ["Selecione um registro para gerar o PDF..."]
        for i, linha in enumerate(registros[1:], start=2):
            identificador = " - ".join(linha[:3]) if len(linha) >= 3 else f"Registro {i}"
            opcoes_registros.append(f"Linha {i} | {identificador}")
            
        registro_selecionado = st.selectbox("2. SELECIONE O CADASTRO E CLIQUE EM GERAR", opcoes_registros)
        
        if registro_selecionado != "Selecione um registro para gerar o PDF...":
            linha_idx = int(registro_selecionado.split(" ")[1])
            linha = registros[linha_idx - 1]
            
            if st.button("RECONSTRUIR DOCUMENTO PDF", type="primary", use_container_width=True):
                with st.spinner("A reconstruir o PDF com os dados da nuvem..."):
                    pdf_bytes = None
                    nome_arquivo = f"Documento_{nome_aba}.pdf"
                    
                    try:
                        # EPI: [Data, Obra, Nome, Funcao, EPI, Qtd, CA, B64_Ass, LinkFoto]
                        if nome_aba == "EPI":
                            data_str, obra, nome, funcao = linha[0], linha[1], linha[2], linha[3]
                            epi, qtd, ca = (linha[4] if len(linha)>4 else ""), (linha[5] if len(linha)>5 else ""), (linha[6] if len(linha)>6 else "")
                            b64_ass = linha[7] if len(linha)>7 else ""
                            link_foto = linha[8] if len(linha)>8 else ""
                            
                            img_ass = base64_to_temp_img(b64_ass, "epi")
                            img_foto = baixar_foto_drive(link_foto)
                            
                            pdf_bytes = criar_pdf_epi(obra, nome, funcao, data_str, epi, qtd, ca, img_ass, img_foto)
                            nome_arquivo = f"EPI_{nome.replace(' ', '_')}.pdf"
                            if img_ass: os.remove(img_ass)
                            if img_foto: os.remove(img_foto)

                        # CESTA/ARMARIO: [Data, Obra, Nome, Funcao, B64_Ass]
                        elif nome_aba in ["CESTA", "ARMARIO"]:
                            data_str, obra, nome, funcao = linha[0], linha[1], linha[2], linha[3]
                            b64_ass = linha[4] if len(linha)>4 else ""
                            img_ass = base64_to_temp_img(b64_ass, nome_aba)
                            if nome_aba == "CESTA": pdf_bytes = criar_pdf_cesta(obra, nome, funcao, data_str, img_ass)
                            else: pdf_bytes = criar_pdf_armario(obra, nome, funcao, data_str, img_ass)
                            nome_arquivo = f"{nome_aba}_{nome.replace(' ', '_')}.pdf"
                            if img_ass: os.remove(img_ass)

                        # FARDAMENTO: [Data, Obra, Nome, Funcao, Item_fard, Qtd, B64_Ass]
                        elif nome_aba == "FARDAMENTO":
                            data_str, obra, nome, funcao = linha[0], linha[1], linha[2], linha[3]
                            item_fard, qtd = (linha[4] if len(linha)>4 else ""), (linha[5] if len(linha)>5 else "")
                            b64_ass = linha[6] if len(linha)>6 else ""
                            img_ass = base64_to_temp_img(b64_ass, "fard")
                            pdf_bytes = criar_pdf_fardamento(obra, nome, funcao, item_fard, qtd, data_str, img_ass)
                            nome_arquivo = f"Fardamento_{nome.replace(' ', '_')}.pdf"
                            if img_ass: os.remove(img_ass)

                        # OS: [Data, DataIn, Obra, Nome, Funcao, Texto, B64_1, B64_2]
                        elif nome_aba == "OS":
                            data_str, data_in, obra, nome, funcao = linha[0], linha[1], linha[2], linha[3], linha[4]
                            texto_os = linha[5] if len(linha)>5 else ""
                            b64_1, b64_2 = (linha[6] if len(linha)>6 else ""), (linha[7] if len(linha)>7 else "")
                            img_1, img_2 = base64_to_temp_img(b64_1, "os1"), base64_to_temp_img(b64_2, "os2")
                            pdf_bytes = criar_pdf_os(obra, nome, funcao, data_in, texto_os, data_str, img_1, img_2)
                            nome_arquivo = f"OS_{nome.replace(' ', '_')}.pdf"
                            if img_1: os.remove(img_1)
                            if img_2: os.remove(img_2)

                        # INTEGRACAO: [Data, Obra, Nome, Funcao, Texto, B64_1, B64_2]
                        elif nome_aba == "INTEGRACAO":
                            data_str, obra, nome, funcao = linha[0], linha[1], linha[2], linha[3]
                            texto = linha[4] if len(linha)>4 else ""
                            b64_1, b64_2 = (linha[5] if len(linha)>5 else ""), (linha[6] if len(linha)>6 else "")
                            img_1, img_2 = base64_to_temp_img(b64_1, "int1"), base64_to_temp_img(b64_2, "int2")
                            pdf_bytes = criar_pdf_integracao(obra, nome, funcao, data_str, texto, img_1, img_2)
                            nome_arquivo = f"Integracao_{nome.replace(' ', '_')}.pdf"
                            if img_1: os.remove(img_1)
                            if img_2: os.remove(img_2)

                        # TREINAMENTO: [Data, Descricao, Instrutor, Local, Carga, Validade, Nome, Funcao, B64_Ass]
                        elif nome_aba == "TREINAMENTO":
                            data_str, desc, instrutor, local = linha[0], linha[1], linha[2], linha[3]
                            carga, val, nome, funcao = linha[4], linha[5], linha[6], linha[7]
                            b64_ass = linha[8] if len(linha)>8 else ""
                            img_ass = base64_to_temp_img(b64_ass, "treina")
                            pdf_bytes = criar_pdf_treinamento(desc, instrutor, data_str, local, carga, val, nome, funcao, img_ass)
                            nome_arquivo = f"Treinamento_{nome.replace(' ', '_')}.pdf"
                            if img_ass: os.remove(img_ass)

                    except Exception as e:
                        st.error(f"Erro ao processar dados da nuvem para o PDF: {e}")
                    
                    if pdf_bytes:
                        st.session_state['pdf_gerado_sob_demanda'] = bytes(pdf_bytes)
                        st.session_state['nome_pdf_sob_demanda'] = nome_arquivo
                        st.rerun()

        if 'pdf_gerado_sob_demanda' in st.session_state:
            st.markdown("<div style='height: 1rem;'></div>", unsafe_allow_html=True)
            st.success("✓ Documento legal reconstruído com sucesso.")
            st.download_button("↓ BAIXAR DOCUMENTO (PDF)", data=st.session_state['pdf_gerado_sob_demanda'], file_name=st.session_state['nome_pdf_sob_demanda'], mime="application/pdf", use_container_width=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# APP PRINCIPAL E NAVEGAÇÃO
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def app():
    inject_custom_css()
    if 'autenticado' not in st.session_state: st.session_state['autenticado'] = False
    if not st.session_state['autenticado']: render_login()
    else:
        if 'prev_menu_cadastro' not in st.session_state: st.session_state.prev_menu_cadastro = 'ENTREGA DE EPI'
        if 'prev_menu_acomp' not in st.session_state: st.session_state.prev_menu_acomp = 'OBRA'
        if 'active_view' not in st.session_state: st.session_state.active_view = 'ENTREGA DE EPI'

        menu_styles = {
            "container": {"padding": "0!important", "background": "transparent"},
            "menu-title": {"color": "#aaa", "font-size": "0.8rem", "font-weight": "700", "letter-spacing": "1px", "padding": "0 10px"},
            "nav-link": { "color": "#aaa", "font-size": "0.9rem", "margin": "6px", "text-align": "left", "transition": "all 0.2s ease" },
            "nav-link-selected": { "background-color": "rgba(227, 112, 38, 0.15)", "color": "#E37026", "border-left": "3px solid #E37026" },
            "icon": {"font-size": "1.1rem"}
        }

        with st.sidebar:
            st.markdown("")
            try: st.image('assets/logo.png', use_container_width=True)
            except: pass
            st.markdown("")
            st.markdown('<div class="sidebar-logo-container"><div class="sidebar-logo-text">SST</div><div class="sidebar-logo-sub">Segurança e Saúde do Trabalho</div></div>', unsafe_allow_html=True)
            st.markdown("")

            menu_cadastro = option_menu(
                "CADASTRO",
                options=['ENTREGA DE EPI', 'TERMO DE ENTREGA CESTA BÁSICA', 'TERMO DE ENTREGA DE ARMARIO/CADEADO', 'ORDEM DE SERVIÇO', 'INTEGRAÇÃO', 'TREINAMENTOS (RENOVAÇÃO VERIFICAR)'],
                icons=['shield-check', 'box-seam', 'lock', 'card-checklist', 'people', 'journal-check'],
                styles=menu_styles, key="menu_cadastro_opt"
            )

            st.markdown("<div style='height: 1rem;'></div>", unsafe_allow_html=True)
            
            menu_acomp = option_menu(
                "ACOMPANHAMENTO", 
                options=['OBRA', 'FUNCIONARIO', 'ITEM', 'EDITAR CADASTRO', 'GERAR PDF'], 
                icons=['building', 'person-lines-fill', 'file-earmark-text', 'pencil-square', 'file-pdf'], 
                styles=menu_styles, 
                key="menu_acomp_opt"
            )
            
            st.markdown("<div style='flex: 1; min-height: 5vh;'></div>", unsafe_allow_html=True)
            
            if st.button("← Sair do Sistema"):
                st.session_state['autenticado'] = False
                st.cache_data.clear() # Limpa os dados se fizer logout
                st.rerun()
            st.markdown('<p class="sidebar-footer">Lavie Construções e Incorporações</p>', unsafe_allow_html=True)

        if menu_cadastro != st.session_state.prev_menu_cadastro or menu_acomp != st.session_state.prev_menu_acomp:
            st.session_state.active_view = menu_cadastro if menu_cadastro != st.session_state.prev_menu_cadastro else menu_acomp
            st.session_state.prev_menu_cadastro = menu_cadastro
            st.session_state.prev_menu_acomp = menu_acomp
            if 'pdf_gerado_sob_demanda' in st.session_state: del st.session_state['pdf_gerado_sob_demanda']

        view = st.session_state.active_view
        if view == 'ENTREGA DE EPI': render_module_1()
        elif view == 'TERMO DE ENTREGA CESTA BÁSICA': render_module_2()
        elif view == 'TERMO DE ENTREGA DE ARMARIO/CADEADO': render_module_3()
        elif view == 'ORDEM DE SERVIÇO': render_module_5()
        elif view == 'INTEGRAÇÃO': render_module_6()
        elif view == 'TREINAMENTOS (RENOVAÇÃO VERIFICAR)': render_module_7()
        elif view == 'OBRA': render_acomp_obra()
        elif view == 'FUNCIONARIO': render_acomp_funcionario()
        elif view == 'ITEM': render_acomp_item()
        elif view == 'EDITAR CADASTRO': render_acomp_editar()
        elif view == 'GERAR PDF': render_acomp_gerar_pdf()

if __name__ == '__main__':
    app()