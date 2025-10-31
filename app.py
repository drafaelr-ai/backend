# Substitua a rota /obras (GET) existente por esta:

@app.route('/obras', methods=['GET', 'OPTIONS'])
@jwt_required() 
def get_obras():
    if request.method == 'OPTIONS':
        return make_response(jsonify({"message": "OPTIONS request allowed"}), 200)
    print("--- [LOG] Rota /obras (GET) acessada (com KPIs) ---")
    try:
        user = get_current_user() 
        if not user: return jsonify({"erro": "Usuário não encontrado"}), 404

        # 1. Subquery para totais de Lançamentos
        lancamentos_sum = db.session.query(
            Lancamento.obra_id,
            func.sum(Lancamento.valor).label('total_geral'),
            func.sum(case((Lancamento.status == 'Pago', Lancamento.valor), else_=0)).label('total_pago')
        ).group_by(Lancamento.obra_id).subquery()

        # 2. Subquery para totais de Pagamentos de Serviços
        pagamentos_sum = db.session.query(
            Servico.obra_id,
            func.sum(PagamentoServico.valor).label('total_geral'),
            func.sum(case((PagamentoServico.status == 'Pago', PagamentoServico.valor), else_=0)).label('total_pago')
        ).join(Servico).group_by(Servico.obra_id).subquery()

        # 3. Query Principal
        # Junta Obra com as subqueries de totais
        obras_query = db.session.query(
            Obra,
            func.coalesce(lancamentos_sum.c.total_geral, 0).label('lanc_geral'),
            func.coalesce(lancamentos_sum.c.total_pago, 0).label('lanc_pago'),
            func.coalesce(pagamentos_sum.c.total_geral, 0).label('pag_geral'),
            func.coalesce(pagamentos_sum.c.total_pago, 0).label('pag_pago')
        ).outerjoin(
            lancamentos_sum, Obra.id == lancamentos_sum.c.obra_id
        ).outerjoin(
            pagamentos_sum, Obra.id == pagamentos_sum.c.obra_id
        )

        # 4. Filtra permissões
        if user.role == 'administrador':
            obras_com_totais = obras_query.order_by(Obra.nome).all()
        else:
            # Filtra apenas as obras que o usuário tem permissão
            obras_com_totais = obras_query.join(
                user_obra_association, Obra.id == user_obra_association.c.obra_id
            ).filter(
                user_obra_association.c.user_id == user.id
            ).order_by(Obra.nome).all()

        # 5. Formata a saída
        resultados = []
        for obra, lanc_geral, lanc_pago, pag_geral, pag_pago in obras_com_totais:
            total_geral = float(lanc_geral) + float(pag_geral)
            total_pago = float(lanc_pago) + float(pag_pago)
            total_a_pagar = total_geral - total_pago
            
            resultados.append({
                "id": obra.id,
                "nome": obra.nome,
                "cliente": obra.cliente,
                "total_geral": total_geral, # <-- NOVO
                "total_a_pagar": total_a_pagar # <-- NOVO
            })
        
        return jsonify(resultados)

    except Exception as e:
        error_details = traceback.format_exc()
        print(f"--- [ERRO] /obras (GET): {str(e)}\n{error_details} ---")
        return jsonify({"erro": str(e), "details": error_details}), 500