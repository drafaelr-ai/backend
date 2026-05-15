import logging
from datetime import datetime, date

logger = logging.getLogger(__name__)


def extrair_dados_boleto_pdf_admin(pdf_base64):
    """Extrai código de barras, vencimento e valor do PDF do boleto"""
    import re, io, base64
    try:
        try:
            import pdfplumber
        except ImportError:
            return {'sucesso': False, 'multiplos': False, 'quantidade': 0, 'boletos': [], 'codigo_barras': None, 'data_vencimento': None, 'valor': None, 'beneficiario': None}

        pdf_bytes = base64.b64decode(pdf_base64)
        boletos_encontrados = []

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_num, page in enumerate(pdf.pages):
                text = page.extract_text() or ''
                boleto = {'codigo_barras': None, 'data_vencimento': None, 'valor': None, 'beneficiario': None}

                # Extrair código de barras (47-48 dígitos)
                codigos = re.findall(r'\d[\d\s\.]{44,50}\d', text)
                for codigo in codigos:
                    codigo_limpo = re.sub(r'[\s\.]', '', codigo)
                    if 44 <= len(codigo_limpo) <= 48 and codigo_limpo[0] in '123456789':
                        boleto['codigo_barras'] = codigo_limpo[:47] if len(codigo_limpo) >= 47 else codigo_limpo
                        break

                # Extrair valor
                valores = re.findall(r'R\$\s*([\d\.]+,\d{2})', text)
                for v_str in valores:
                    try:
                        valor = float(v_str.replace('.', '').replace(',', '.'))
                        if valor > 0:
                            boleto['valor'] = valor
                            break
                    except Exception:
                        pass

                # Extrair data de vencimento
                datas = re.findall(r'(\d{2}/\d{2}/\d{4})', text)
                hoje = date.today()
                datas_futuras = []
                datas_passadas = []
                for d_str in datas:
                    try:
                        d = datetime.strptime(d_str, '%d/%m/%Y').date()
                        if d >= hoje:
                            datas_futuras.append(d)
                        else:
                            datas_passadas.append(d)
                    except Exception:
                        pass
                if datas_futuras:
                    boleto['data_vencimento'] = min(datas_futuras).isoformat()
                elif datas_passadas:
                    boleto['data_vencimento'] = max(datas_passadas).isoformat()

                # Extrair beneficiário
                benef_match = re.search(r'Benefici[aá]rio[:\s]+([A-Z][A-Za-z\s]+(?:LTDA|S\.A\.|SA|ME|EPP)?)', text)
                if benef_match:
                    boleto['beneficiario'] = benef_match.group(1).strip()[:100]

                if boleto['codigo_barras'] or boleto['data_vencimento']:
                    # Evitar duplicatas
                    duplicado = any(
                        b['codigo_barras'] == boleto['codigo_barras']
                        for b in boletos_encontrados if boleto['codigo_barras']
                    )
                    if not duplicado:
                        boletos_encontrados.append(boleto)

        if len(boletos_encontrados) == 0:
            return {'sucesso': False, 'multiplos': False, 'quantidade': 0, 'boletos': [], 'codigo_barras': None, 'data_vencimento': None, 'valor': None, 'beneficiario': None}
        elif len(boletos_encontrados) == 1:
            b = boletos_encontrados[0]
            return {'sucesso': True, 'multiplos': False, 'quantidade': 1, 'boletos': boletos_encontrados, 'codigo_barras': b['codigo_barras'], 'data_vencimento': b['data_vencimento'], 'valor': b['valor'], 'beneficiario': b['beneficiario']}
        else:
            return {'sucesso': True, 'multiplos': True, 'quantidade': len(boletos_encontrados), 'boletos': boletos_encontrados, 'codigo_barras': None, 'data_vencimento': None, 'valor': None, 'beneficiario': None}

    except Exception as e:
        logger.exception(f"Erro ao extrair PDF: {e}")
        return {'sucesso': False, 'multiplos': False, 'quantidade': 0, 'boletos': [], 'codigo_barras': None, 'data_vencimento': None, 'valor': None, 'beneficiario': None}
