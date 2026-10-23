"""Normalização dos eventos de infração recebidos pelo webhook e-Frotas.

O manual do SERPRO acrescenta ``tipoEvento`` e ``idRastreamento`` a cada
payload. Os campos de infração, porém, são os mesmos usados pela consulta de
infrações e podem chegar na raiz do JSON ou no objeto ``infracao``. Centralizar
essa adaptação aqui deixa a rota de webhook curta o suficiente para respeitar o
timeout de um segundo do fornecedor.
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from contextlib import contextmanager
import logging
import os
import re
import tempfile
from urllib.parse import quote

import requests


logger = logging.getLogger(__name__)


class EventoEFrotasInvalido(ValueError):
    """O evento não contém os dados mínimos de uma infração."""


class EFrotasConfiguracaoErro(RuntimeError):
    """A consulta retrospectiva foi acionada sem credenciais válidas."""


class EFrotasConsultaErro(RuntimeError):
    """O e-Frotas não aceitou ou não respondeu à consulta."""


def normalizar_placa(valor):
    return re.sub(r'[^A-Z0-9]', '', str(valor or '').upper())


def _texto(valor, tamanho=None):
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    return texto[:tamanho] if tamanho else texto


def _data(valor):
    if not valor:
        return None
    if isinstance(valor, date):
        return valor
    try:
        return datetime.fromisoformat(str(valor).replace('Z', '+00:00')).date()
    except ValueError:
        for formato in ('%Y-%m-%d', '%d/%m/%Y'):
            try:
                return datetime.strptime(str(valor)[:10], formato).date()
            except ValueError:
                continue
        return None


def _decimal(valor):
    if valor is None or valor == '':
        return None
    if isinstance(valor, Decimal):
        return valor
    texto = str(valor).strip().replace('R$', '').strip()
    if ',' in texto and '.' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    elif ',' in texto:
        texto = texto.replace(',', '.')
    try:
        return Decimal(texto)
    except (InvalidOperation, ValueError):
        return None


def _booleano(valor):
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, str):
        texto = valor.strip().lower()
        if texto in {'true', '1', 'sim'}:
            return True
        if texto in {'false', '0', 'nao', 'não'}:
            return False
    return None


def _obter(fontes, *nomes):
    for fonte in fontes:
        if not isinstance(fonte, dict):
            continue
        for nome in nomes:
            valor = fonte.get(nome)
            if valor is not None and valor != '':
                return valor
    return None


def _fontes_payload(payload):
    """Objetos conhecidos em callbacks e respostas de consulta do SERPRO."""
    if not isinstance(payload, dict):
        return ()
    filhos = (
        payload.get('infracao'), payload.get('dadosInfracao'),
        payload.get('dados'), payload.get('data'), payload.get('notificacao'),
        payload.get('dadosNotificacao'),
    )
    return tuple(fonte for fonte in (*filhos, payload) if isinstance(fonte, dict))


def interpretar_evento_infracao(payload):
    """Converte um POST e-Frotas em dados persistíveis pelo Obraly.

    Aceita o formato documentado pela consulta de infrações e a variante em que
    o fornecedor embute esses campos em ``infracao``/``dados``. Não depende de
    um número fixo de ``tipoEvento``: a lista pode evoluir no SERPRO; o que
    caracteriza uma infração são os identificadores oficiais e a placa.
    """
    if not isinstance(payload, dict):
        raise EventoEFrotasInvalido('O corpo deve ser um objeto JSON.')

    fontes = _fontes_payload(payload)

    placa = normalizar_placa(_obter(fontes, 'placa', 'placaVeiculo'))
    codigo_orgao = _texto(_obter(fontes, 'codigoOrgaoAutuador', 'codigoOrgao'), 30)
    numero_ait = _texto(_obter(fontes, 'numeroAutoInfracao', 'numeroAit', 'numeroAIT'), 80)
    codigo_infracao = _texto(_obter(fontes, 'codigoInfracao'), 30)
    data_infracao = _data(_obter(fontes, 'dataAutuacao', 'dataInfracao', 'dataHoraEvento'))
    # ``valorOriginal`` é o campo emitido pelos eventos de autuação e
    # penalidade do e-Frotas; as demais chaves cobrem a consulta detalhada e
    # integrações anteriores que já chegavam ao Obraly.
    valor = _decimal(_obter(
        fontes, 'valorOriginal', 'valorIntegralInfracao', 'valor', 'valorMulta'
    ))

    faltantes = []
    if not placa:
        faltantes.append('placa')
    if not codigo_orgao:
        faltantes.append('codigoOrgaoAutuador')
    if not numero_ait:
        faltantes.append('numeroAutoInfracao')
    if not codigo_infracao:
        faltantes.append('codigoInfracao')
    if not data_infracao:
        faltantes.append('dataAutuacao')
    if valor is None:
        faltantes.append('valorOriginal')
    if faltantes:
        raise EventoEFrotasInvalido(
            'Evento sem campos obrigatórios de infração: ' + ', '.join(faltantes)
        )

    pontos = _obter(fontes, 'pontos', 'quantidadePontos')
    try:
        pontos = int(pontos) if pontos is not None else None
    except (TypeError, ValueError):
        pontos = None

    return {
        # Em evento 10 o objeto ``infracao`` pode trazer tipoEvento=0. Os
        # metadados de entrega são sempre os da raiz, por isso têm prioridade.
        'id_rastreamento': _texto(
            payload.get('idRastreamento') or payload.get('id_rastreamento')
            or _obter(fontes, 'idRastreamento', 'id_rastreamento'), 100,
        ),
        'tipo_evento': _texto(
            payload.get('tipoEvento') or payload.get('tipo_evento')
            or _obter(fontes, 'tipoEvento', 'tipo_evento'), 50,
        ),
        'placa': placa,
        'codigo_orgao_autuador': codigo_orgao,
        'numero_auto_infracao': numero_ait,
        'codigo_infracao': codigo_infracao,
        'data_infracao': data_infracao,
        'valor': valor,
        'descricao': _texto(_obter(fontes, 'descricaoInfracao', 'descricao'), 300),
        'pontos': pontos,
        'gravidade': _texto(_obter(fontes, 'gravidade'), 30),
        'local_autuacao': _texto(_obter(fontes, 'localAutuacao'), 300),
        'municipio_autuacao': _texto(_obter(fontes, 'descricaoMunicipioAutuacao', 'municipioAutuacao'), 120),
        'uf_autuacao': _texto(_obter(fontes, 'siglaUfLocalAutuacao', 'ufOrgaoAutuador'), 2),
        'data_notificacao_autuacao': _data(_obter(fontes, 'dataEmissaoNotificacaoAutuacao')),
        'data_notificacao_penalidade': _data(_obter(fontes, 'dataEmissaoNotificacaoPenalidade')),
        'data_limite_defesa': _data(_obter(fontes, 'dataLimiteDefesaAutuacao')),
        'data_vencimento_penalidade': _data(_obter(
            fontes, 'dataVencimentoNotificacaoPenalidade', 'dataVencimentoPenalidade'
        )),
        'data_pagamento': _data(_obter(fontes, 'dataPagamento', 'dataRegistroPagamento')),
        # No retorno histórico, o SERPRO sinaliza a baixa por ocorrência. A
        # informação é oficial e não pode ser confundida com pagamento local.
        'cancelada_suspensa': _booleano(_obter(fontes, 'indicadorCanceladaSuspensa')),
    }


# ---------------------------------------------------------------- consulta histórica

def _configuracao_consulta():
    """Lê a configuração sem jamais devolver segredo para a API/UI."""
    base_url = (os.environ.get('EFROTAS_API_BASE_URL') or '').strip().rstrip('/')
    token = (os.environ.get('EFROTAS_API_TOKEN') or '').strip()
    cert_path = (os.environ.get('EFROTAS_CLIENT_CERT_PATH') or '').strip()
    key_path = (os.environ.get('EFROTAS_CLIENT_KEY_PATH') or '').strip()
    # O Fly entrega Secrets como variáveis de ambiente. Aceitar o PEM aqui
    # evita exigir volume ou imagem customizada só para uma dupla de arquivos
    # de mTLS. Os arquivos temporários são criados apenas durante a chamada e
    # removidos em seguida, sem nunca expor o conteúdo em logs ou respostas.
    cert_pem = (os.environ.get('EFROTAS_CLIENT_CERT_PEM') or '').strip()
    key_pem = (os.environ.get('EFROTAS_CLIENT_KEY_PEM') or '').strip()
    ca_bundle = (os.environ.get('EFROTAS_CA_BUNDLE') or '').strip()

    erros = []
    if not base_url:
        erros.append('Defina EFROTAS_API_BASE_URL na API.')
    if bool(cert_path) != bool(key_path):
        erros.append('Informe certificado e chave do e-Frotas juntos.')
    if bool(cert_pem) != bool(key_pem):
        erros.append('Informe certificado PEM e chave PEM do e-Frotas juntos.')
    if cert_pem and cert_path:
        erros.append('Use o certificado/chave por arquivo ou por Secret PEM, não os dois.')
    if cert_pem and key_pem:
        modo = 'certificado'
    elif cert_path and key_path:
        if not os.path.isfile(cert_path) or not os.path.isfile(key_path):
            erros.append('O certificado ou a chave do e-Frotas não foi encontrado no servidor.')
        modo = 'certificado'
    elif token:
        # O token é útil para homologação. Produção normalmente usa mTLS.
        modo = 'token'
        if 'hom-' not in base_url.lower() and 'homolog' not in base_url.lower():
            erros.append('Produção exige certificado e chave e-Frotas; token é somente homologação.')
    else:
        modo = None
        erros.append('Configure o certificado e a chave do e-Frotas (ou o token de homologação).')
    if ca_bundle and not os.path.isfile(ca_bundle):
        erros.append('O arquivo de CA configurado para o e-Frotas não foi encontrado.')

    return {
        'habilitada': not erros,
        'mensagem': ' '.join(erros) if erros else 'Consulta histórica pronta para uso.',
        'base_url': base_url,
        'token': token,
        'cert': (cert_path, key_path) if cert_path and key_path else None,
        'cert_pem': (cert_pem, key_pem) if cert_pem and key_pem else None,
        'verify': ca_bundle or True,
        'modo': modo,
    }


def status_consulta_efrotas():
    """Estado seguro para ser exibido no painel de Frota."""
    cfg = _configuracao_consulta()
    return {
        'habilitada': cfg['habilitada'],
        'mensagem': cfg['mensagem'],
        'modo': cfg['modo'],
    }


def _timeout_consulta():
    try:
        # A carga histórica é executada em um request controlado. Manter cada
        # chamada curta permite que um lote seja concluído antes do timeout do
        # servidor e que uma falha seja mostrada no painel para nova tentativa.
        return max(3, min(int(os.environ.get('EFROTAS_TIMEOUT_SECONDS', '15')), 20))
    except (TypeError, ValueError):
        return 15


@contextmanager
def _certificado_para_requisicao(cfg):
    """Fornece ``cert`` compatível com requests e remove PEM temporário."""
    if not cfg['cert_pem']:
        yield cfg['cert']
        return

    caminhos = []
    try:
        for conteudo, sufixo in zip(cfg['cert_pem'], ('.crt', '.key')):
            descritor, caminho = tempfile.mkstemp(prefix='efrotas-', suffix=sufixo)
            caminhos.append(caminho)
            with os.fdopen(descritor, 'w', encoding='utf-8') as arquivo:
                arquivo.write(conteudo)
                arquivo.write('\n')
        yield tuple(caminhos)
    finally:
        for caminho in caminhos:
            try:
                os.unlink(caminho)
            except FileNotFoundError:
                pass


def _requisitar_efrotas(caminho):
    cfg = _configuracao_consulta()
    if not cfg['habilitada']:
        raise EFrotasConfiguracaoErro(cfg['mensagem'])
    headers = {'Accept': 'application/json'}
    if cfg['token']:
        headers['Authorization'] = f"Bearer {cfg['token']}"
    try:
        with _certificado_para_requisicao(cfg) as certificado:
            resposta = requests.get(
                f"{cfg['base_url']}/{caminho.lstrip('/')}",
                headers=headers,
                cert=certificado,
                verify=cfg['verify'],
                timeout=_timeout_consulta(),
            )
    except requests.RequestException as exc:
        logger.warning('e-Frotas: falha de rede na consulta histórica: %s', exc)
        raise EFrotasConsultaErro('Não foi possível consultar o e-Frotas agora.') from exc
    if not resposta.ok:
        logger.warning('e-Frotas: consulta recusada com HTTP %s', resposta.status_code)
        raise EFrotasConsultaErro(
            'O e-Frotas recusou a consulta. Confira o certificado, as permissões e o período.'
        )
    try:
        return resposta.json()
    except ValueError as exc:
        raise EFrotasConsultaErro('O e-Frotas respondeu em um formato inválido.') from exc


def consultar_infracoes_por_placa(placa, data_inicio, data_fim):
    """Consulta as infrações pendentes já existentes para uma placa.

    O endpoint oficial não oferece um lote da frota inteira: cada veículo é
    consultado individualmente e o intervalo máximo é de um ano. O chamador
    pode dividir períodos maiores em janelas anuais, mantendo o ritmo das
    requisições sob controle.
    """
    placa_normalizada = normalizar_placa(placa)
    if not placa_normalizada:
        raise EventoEFrotasInvalido('Placa inválida para consulta histórica.')
    if not isinstance(data_inicio, date) or not isinstance(data_fim, date):
        raise ValueError('datas da consulta devem ser objetos date')
    if data_fim < data_inicio:
        raise ValueError('data final anterior à data inicial')
    if (data_fim - data_inicio).days > 366:
        raise ValueError('A consulta por placa aceita no máximo um ano por vez.')
    caminho = (
        'consultas/v1/infracoes/placa/' + quote(placa_normalizada, safe='') +
        '?dataInicial=' + quote(data_inicio.isoformat(), safe='') +
        '&dataFinal=' + quote(data_fim.isoformat(), safe='')
    )
    resposta = _requisitar_efrotas(caminho)
    # O contrato publicado pelo SERPRO é uma lista direta de InfracaoDTO.
    # Não tentamos "adivinhar" envelopes alternativos: isso poderia ocultar
    # uma resposta de erro aceita com HTTP 200 e dar a falsa impressão de que
    # não há infrações para importar.
    if not isinstance(resposta, list):
        raise EFrotasConsultaErro(
            'O e-Frotas respondeu em um formato incompatível para a consulta de infrações.'
        )
    return resposta


def _identificadores_infracao(payload):
    fontes = _fontes_payload(payload)
    return {
        'codigo_orgao_autuador': _texto(
            _obter(fontes, 'codigoOrgaoAutuador', 'codigoOrgao'), 30
        ),
        'numero_auto_infracao': _texto(
            _obter(fontes, 'numeroAutoInfracao', 'numeroAit', 'numeroAIT'), 80
        ),
        'codigo_infracao': _texto(_obter(fontes, 'codigoInfracao'), 30),
    }


def identificadores_infracao(payload):
    """Chave oficial reutilizável inclusive pelo evento de cancelamento 75."""
    return _identificadores_infracao(payload)
